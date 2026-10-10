"""Per-document pipeline. Stage A: analysis. Stage B: XML edits. Stage C: finalise + save."""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path

from ..templates_store import models as m
from .change_report import DocReport, TaskResult
from .config import Settings, load_settings
from .context import Ctx, RunForm
from .docx_validate import InvalidDocx, validate_docx
from .logging_setup import JobLogger
from .package import DocxPackage
from .pagenum import contents_labels, pages_from_layout, refresh_page_numbers
from .pages import find_soffice, render_page_texts
from .steps import fields_steps, tables, text
from .xmlutil import w

RUNNERS = {
    "period_removal": text.period_removal,
    "placeholders": text.placeholders,
    "find_replace": text.find_replace,
    "toc_update": fields_steps.toc_update,
    "document_details": tables.document_details,
    "table_fix": tables.table_fix,
    "final_refresh": fields_steps.final_refresh,
}


STEP_LABELS = {
    "period_removal": "Remove 'Period:' label (page 1)",
    "placeholders": "Fill placeholders",
    "find_replace": "Custom find/replace",
    "toc_update": "Update table of contents",
    "document_details": "Update & format Document Details table",
    "table_fix": "Fix table splits across pages",
    "final_refresh": "Final global refresh",
}


def plan_for(template: m.Template) -> list[dict]:
    return [{"key": s.type, "label": STEP_LABELS[s.type]} for s in template.steps if s.enabled and s.type in RUNNERS]


def _apply_page_numbers(ctx, pkg, work, use_layout, settings, log) -> TaskResult:
    """Render the already-edited document and write the section page numbers into the contents."""
    res = TaskResult("page_numbers", "Contents page numbers")
    layout = None
    soffice = find_soffice(settings.soffice) if use_layout else None
    if soffice:
        tmp = work / "layout_probe.docx"
        pkg.save(tmp)
        texts = render_page_texts(tmp, soffice, min(settings.doc_timeout_s, 120))
        if texts:
            layout = pages_from_layout(pkg, texts, contents_labels(pkg))
    st = refresh_page_numbers(pkg, layout)
    res.counts.update(source=0, set=st.contents_cells + st.updated)
    if st.source == "layout":
        res.changes.append(f"Page numbers read from a rendered layout of the finished document ({len(layout)} section(s) placed)")
    elif st.source == "markers":
        res.changes.append("Page numbers estimated from Word's saved page markers (LibreOffice not available)")
        res.warnings.append("These numbers are approximate. In Word, select the contents and press F9 to make them exact.")
    else:
        if st.pagerefs:
            res.warnings.append("No layout information available for the page numbers. In Word, select the contents and press F9.")
        return res
    if st.contents_tables:
        res.changes.append(f"Contents table: {st.contents_cells} page number(s) set"
                           + (f", {st.contents_fields_made} turned into refreshable page-reference fields" if st.contents_fields_made else "")
                           + (f", {st.contents_cleaned} stray digit(s) removed" if st.contents_cleaned else ""))
    if st.updated:
        res.changes.append(f"{st.updated} other page reference(s) updated")
    if st.unresolved:
        res.warnings.append(f"{st.unresolved} contents row(s) could not be placed; check them in Word (select, F9).")
    if not (st.contents_cells or st.updated) and not st.pagerefs:
        res.changes.append("No contents or page references to update")
    return res


def output_name_for(src: Path, suffix: str = "") -> str:
    return f"{src.stem}{suffix}.docx"


def process_document(
    src: Path,
    out_dir: Path | None,
    template: m.Template,
    form: RunForm,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    use_layout: bool = True,
    job_id: str | None = None,
    progress=None,  # callable(step_key, state) for live UI; state: running|ok|warning|failed
) -> DocReport:
    settings = settings or load_settings()
    job_id = job_id or uuid.uuid4().hex[:8]
    log = JobLogger(logging.getLogger("crespoqa"), {"job": job_id})
    out_name = output_name_for(src, settings.output_suffix)
    report = DocReport(
        job_id=job_id, input_name=src.name, report_type=template.report_type, template=template.name,
        template_version=template.version, processed_by=form.consultant.strip(), dry_run=dry_run,
        output_name=None if dry_run else out_name,
    )
    started = time.monotonic()
    work = settings.temp_dir / uuid.uuid4().hex
    try:
        work.mkdir(parents=True, exist_ok=True)
        validate_docx(src, settings.max_file_bytes)
        copy = work / src.name
        shutil.copyfile(src, copy)  # the original is never opened for writing

        pkg = DocxPackage(copy)
        ctx = Ctx(pkg=pkg, form=form, output_name=out_name, log=log, dry_run=dry_run)

        pre = TaskResult("prechecks", "Pre-checks")
        has_revisions = pkg.track_revisions_on() or any(True for _ in pkg.document.iter(w("ins"), w("del")))
        if has_revisions:
            pre.warnings.append("Document contains tracked changes; they were left untouched and edits made by this tool are not tracked.")
        # Stage A: optional read-only layout render for cross-checks
        if use_layout:
            soffice = find_soffice(settings.soffice)
            if soffice:
                ctx.page_texts = render_page_texts(copy, soffice, min(settings.doc_timeout_s, 120))
                if ctx.page_texts is None:
                    pre.warnings.append("Layout render failed or timed out; page cross-checks skipped.")
            else:
                pre.changes.append("LibreOffice not available: page checks use Word's saved page markers only.")
        report.tasks.append(pre)

        steps = [s for s in template.steps if s.enabled and s.type in RUNNERS]
        for i, step in enumerate(steps, 1):
            if time.monotonic() - started > settings.doc_timeout_s:
                raise TimeoutError(f"Per-document timeout of {settings.doc_timeout_s}s exceeded")
            t0 = time.monotonic()
            if progress:
                progress(step.type, "running")
            try:
                result = RUNNERS[step.type](ctx, step.params)
            except Exception as exc:  # a failing step fails the document, with the step named
                log.error(f"[Task {i}/{len(steps)}] {step.type} FAILED ({type(exc).__name__})")
                if progress:
                    progress(step.type, "failed")
                raise
            report.tasks.append(result)
            if progress:
                progress(step.type, result.status)
            log.info(f"[Task {i}/{len(steps)}] {result.title} ... {result.status.upper()} {result.counts} ({time.monotonic() - t0:.2f}s)")

        if ctx.extras.get("want_page_numbers"):
            report.tasks.append(_apply_page_numbers(ctx, pkg, work, use_layout, settings, log))

        # Stage C: save
        if not dry_run:
            if out_dir is None:
                raise ValueError("out_dir required unless dry_run")
            out_dir.mkdir(parents=True, exist_ok=True)
            if (out_dir / out_name).resolve() == src.resolve():
                raise InvalidDocx("The output would overwrite the original file. Choose a different output folder.")
            try:
                pkg.save(out_dir / out_name)
            except PermissionError:
                raise InvalidDocx(f"Cannot write '{out_name}'. It is probably open in Word or another program: close it and run again.") from None
    except InvalidDocx as exc:
        report.error = str(exc)
        log.warning("Rejected upload: " + type(exc).__name__)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}" if not isinstance(exc, TimeoutError) else str(exc)
        log.error(f"Document failed ({type(exc).__name__})")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        report.duration_s = time.monotonic() - started
        report.finalize()
        log.info(f"Done: {report.status} in {report.duration_s:.1f}s")
    return report
