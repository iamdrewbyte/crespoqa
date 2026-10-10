"""Job queue: N worker threads (default 1), per-job UUID temp dirs, delete-after-download and TTL cleanup."""
from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.change_report import DocReport
from ..core.config import Settings
from ..core.context import RunForm
from ..core.pipeline import plan_for, process_document
from ..review import ReviewError, ReviewState, extract_for_review, run_review
from ..templates_store import models as m
from ..templates_store.ai_defaults import defaults_for

log = logging.getLogger("crespoqa.jobs")


@dataclass
class Job:
    id: str
    service: str
    form: RunForm
    dry_run: bool
    dir: Path
    inputs: list[tuple[str, Path]]  # (report_type, path)
    templates: dict = field(default_factory=dict)  # report_type -> Template (version pinned at submit)
    owner: str = ""
    ai_review: bool = False
    review: ReviewState | None = None
    review_docs: dict | None = None  # extracted text held in memory only until the review ends
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued | running | done
    plan: dict[str, list[dict]] = field(default_factory=dict)  # input name -> steps
    reports: list[DocReport] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    downloaded: set[str] = field(default_factory=set)
    files_deleted: bool = False
    saved: list = field(default_factory=list)      # [{name, ok, error}] when CRESPOQA_SAVE_DIR is set
    events: list[dict] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, **ev) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def snapshot(self) -> dict:
        return {
            "id": self.id, "status": self.status, "dry_run": self.dry_run, "service": self.service,
            "plan": self.plan, "outputs": [] if self.files_deleted else self.outputs,
            "files_deleted": self.files_deleted,
            "review_status": self.review.status if self.review else "off",
            "saved": self.saved,
            "reports": [
                {"index": i, "input_name": r.input_name, "report_type": r.report_type, "status": r.status,
                 "error": r.error, "template": r.template, "template_version": r.template_version,
                 "output_name": r.output_name, "tasks": [
                     {"key": t.key, "title": t.title, "status": t.status, "changes": t.changes, "warnings": t.warnings}
                     for t in r.tasks]}
                for i, r in enumerate(self.reports)],
        }


class JobManager:
    def __init__(self, settings: Settings, llm=None):
        self.settings = settings
        self.llm = llm  # review-only model client (None = AI review unavailable)
        self.rq: queue.Queue[str | None] = queue.Queue()
        self.root = settings.temp_dir / "jobs"
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.q: queue.Queue[str | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self.cleanup_startup()
        for i in range(self.settings.workers):
            t = threading.Thread(target=self._worker, name=f"qa-worker-{i}", daemon=True)
            t.start(); self._threads.append(t)
        if self.llm is not None:  # one review at a time: protects the local GPU
            t = threading.Thread(target=self._review_worker, name="qa-review", daemon=True)
            t.start(); self._threads.append(t)
        t = threading.Thread(target=self._sweeper, name="qa-sweeper", daemon=True)
        t.start(); self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for _ in range(self.settings.workers):
            self.q.put(None)
        self.rq.put(None)
        for t in self._threads:
            t.join(timeout=5)

    def cleanup_startup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - self.settings.ttl_minutes * 60
        for p in self.settings.temp_dir.iterdir():
            if p != self.root and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)

    # -- API -----------------------------------------------------------
    def create(self, service: str, form: RunForm, dry_run: bool, uploads: list[tuple[str, str, Path]],
               templates: dict, owner: str, ai_review: bool = False) -> Job:
        """uploads: (report_type, safe_name, path already inside the job dir)."""
        job_dir = uploads[0][2].parent.parent
        job = Job(id=job_dir.name, service=service, form=form, dry_run=dry_run, dir=job_dir,
                  inputs=[(rt, p) for rt, _n, p in uploads], templates=templates, owner=owner, ai_review=ai_review)
        for rt, p in job.inputs:
            job.plan[p.name] = plan_for(templates[rt])
        with self.lock:
            self.jobs[job.id] = job
        self.q.put(job.id)
        return job

    def new_job_dir(self) -> Path:
        d = self.root / uuid.uuid4().hex
        (d / "in").mkdir(parents=True)
        return d

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def delete_files(self, job: Job) -> None:
        shutil.rmtree(job.dir, ignore_errors=True)
        job.files_deleted = True
        log.info(f"[job {job.id[:8]}] files deleted")

    def mark_downloaded(self, job: Job, name: str) -> None:
        job.downloaded.add(name)
        if job.outputs and set(job.outputs) <= job.downloaded:
            self.delete_files(job)

    # -- workers -------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            job_id = self.q.get()
            if job_id is None:
                return
            job = self.get(job_id)
            if job is None or job.files_deleted:
                continue
            try:
                self._run(job)
            except Exception:
                log.exception(f"[job {job.id[:8]}] worker error")
            finally:
                job.status = "done"
                job.emit(type="job", state="done")

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.emit(type="job", state="running")
        out_dir = None if job.dry_run else job.dir / "out"
        for rtype, path in job.inputs:
            name = path.name
            template = job.templates[rtype]

            def progress(key: str, state: str, _n=name):
                job.emit(type="step", doc=_n, key=key, state=state)

            report = process_document(path, out_dir, template, job.form, settings=self.settings,
                                      dry_run=job.dry_run, job_id=job.id[:8], progress=progress)
            report.input_name = name
            job.reports.append(report)
            if report.output_name and not report.error:
                job.outputs.append(report.output_name)
            job.emit(type="doc", doc=name, status=report.status)
            if job.ai_review and not report.error:
                self._extract_for_review(job, rtype, template, path, out_dir, report.output_name)
        self._save_to_folder(job, out_dir)
        for _rt, path in job.inputs:  # originals are not needed once processed
            path.unlink(missing_ok=True)
        if job.review_docs and len(job.review_docs) == len(job.inputs):
            job.review = ReviewState(status="pending")
            self.rq.put(job.id)
        elif job.ai_review:
            job.review_docs = None
            job.review = ReviewState(status="error", error="Review skipped because a document failed to process.")

    def _save_to_folder(self, job: Job, out_dir) -> None:
        """Replace same-named files in CRESPOQA_SAVE_DIR (atomic swap). Never raises: results go to job.saved."""
        target = (self.settings.save_dir or "").strip()
        if not target or out_dir is None:
            return
        dest = Path(target).expanduser()
        for name in job.outputs:
            entry = {"name": name, "ok": False, "error": None, "folder": target}
            try:
                dest.mkdir(parents=True, exist_ok=True)
                tmp = dest / f".{name}.crespoqa.tmp"
                tmp.write_bytes((out_dir / name).read_bytes())
                os.replace(tmp, dest / name)
                entry["ok"] = True
            except PermissionError:
                entry["error"] = "The file is open in Word or another program. Close it and run again."
            except OSError as exc:
                entry["error"] = f"Could not write to the folder ({type(exc).__name__})."
            job.saved.append(entry)
            log.info(f"[job {job.id[:8]}] save to folder: {'ok' if entry['ok'] else 'failed'}")

    def _extract_for_review(self, job: Job, rtype: str, template, src: Path, out_dir, output_name) -> None:
        # Review the processed file when there is one (placeholders are filled by then); else the upload.
        path = (out_dir / output_name) if out_dir and output_name and (out_dir / output_name).exists() else src
        details = next((st.params for st in template.steps if isinstance(st, m.DocumentDetailsStep) and st.enabled), None)
        try:
            rules_step = next((st for st in template.steps if isinstance(st, m.AiRulesStep) and st.enabled), None)
            rules = rules_step.params.rules if rules_step else defaults_for(rtype)
            doc = extract_for_review(path, rtype, details, rules)
            job.review_docs = (job.review_docs or {}) | {rtype: doc}
        except Exception:
            log.exception(f"[job {job.id[:8]}] review extraction failed")

    def _review_worker(self) -> None:
        while not self._stop.is_set():
            job_id = self.rq.get()
            if job_id is None:
                return
            job = self.get(job_id)
            if job is None or not job.review_docs:
                continue
            job.review.status = "running"
            try:
                run_review(job.review_docs, job.form, self.llm, job.review,
                           chunk_chars=self.settings.ai_chunk_chars, max_chunks=self.settings.ai_max_chunks)
                job.review.status, job.review.progress = "done", ""
                log.info(f"[job {job.id[:8]}] review done: {len(job.review.findings)} finding(s)")
            except ReviewError as exc:
                job.review.status, job.review.error = "error", str(exc)
                log.warning(f"[job {job.id[:8]}] review failed")
            except Exception:
                job.review.status, job.review.error = "error", "The review failed unexpectedly."
                log.exception(f"[job {job.id[:8]}] review crashed")
            finally:
                job.review_docs = None                     # drop extracted report text

    def _sweeper(self) -> None:
        while not self._stop.wait(60):
            self.sweep()

    def sweep(self) -> None:
        cutoff = time.time() - self.settings.ttl_minutes * 60
        with self.lock:
            expired = [j for j in self.jobs.values() if j.created < cutoff and j.status != "running"]
            for j in expired:
                del self.jobs[j.id]
        for j in expired:
            shutil.rmtree(j.dir, ignore_errors=True)
            log.info(f"[job {j.id[:8]}] expired")
