"""FastAPI app: auth, templates, upload form, job queue, live progress, downloads."""
from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..core.config import Settings, load_settings
from ..core.context import RunForm
from ..core.docx_validate import InvalidDocx, validate_docx
from ..core.logging_setup import setup_logging
from ..templates_store import models as m
from ..templates_store.db import Store, StoreError, parse_steps
from ..templates_store.describe import describe
from ..templates_store.textfmt import HELP, TextError, from_text, to_text
from .auth import COOKIE, CSRF_HEADER, CSRF_VALUE, Sessions, check_login
from ..review import ChatClient, ReviewError
from .jobs import JobManager

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_FIELD = 200


def _safe_name(raw: str | None, fallback: str) -> str:
    name = Path((raw or "").replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    if not name.lower().endswith(".docx"):
        name = fallback
    return name[:120]


def template_uses(template: m.Template, field: str) -> bool:
    for s in template.steps:
        if not s.enabled:
            continue
        if isinstance(s, m.PlaceholdersStep) and any(mp.field == field for mp in s.params.mappings):
            return True
        if isinstance(s, m.DocumentDetailsStep) and (field in s.params.property_mappings.values()
                                                   or any(c.get("field") == field for c in s.params.cell_mappings)):
            return True
        if isinstance(s, m.PeriodRemovalStep) and field == "period" and s.params.period_label_action == "fill":
            return True
    return False


async def _save_upload(up: UploadFile, dest: Path, max_bytes: int) -> None:
    size = 0
    with dest.open("wb") as fh:
        while chunk := await up.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                fh.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, f"{up.filename or 'File'} is larger than the {max_bytes // (1024 * 1024)} MB limit.")
            fh.write(chunk)


class LoginBody(BaseModel):
    username: str
    password: str


class TemplateBody(BaseModel):
    name: str
    service: str = ""
    report_type: str = ""
    steps: list[dict] | None = None
    text: str | None = None          # Notepad-style form; takes precedence over steps


class DuplicateBody(BaseModel):
    service: str


class PreviewBody(BaseModel):
    steps: list[dict] | None = None
    text: str | None = None


def _steps_from(body) -> list:
    """Accept either the Notepad text or a raw step list; parse errors name the line."""
    if body.text is not None:
        try:
            return [s.model_dump(mode="json") for s in from_text(body.text)]
        except TextError as exc:
            raise StoreError(str(exc)) from None
    if body.steps is None:
        raise StoreError("The template has no steps.")
    return body.steps


def _tpl_json(meta: dict, t: m.Template) -> dict:
    return {**meta, "name": t.name, "service": t.service, "report_type": t.report_type, "version": t.version,
            "steps": [s.model_dump(mode="json") for s in t.steps], "description": describe(t),
            "text": to_text(t.steps)}


def build_llm(settings: Settings):
    """Review client from env settings, or None when AI review is off/misconfigured."""
    if not settings.ai_enabled:
        return None
    import logging
    if not (settings.ai_base_url and settings.ai_model):
        logging.getLogger("crespoqa").warning("AI_ENABLED is set but AI_BASE_URL or AI_MODEL is missing; AI review disabled.")
        return None
    try:
        return ChatClient(settings.ai_base_url, settings.ai_model, settings.ai_api_key, settings.ai_timeout_s, settings.ai_allow_remote)
    except ReviewError as exc:
        logging.getLogger("crespoqa").warning(f"AI review disabled: {exc}")
        return None


def _bootstrap_admin(store: Store, settings: Settings) -> None:
    """Containers have no shell on first start: CRESPOQA_ADMIN_USER/PASSWORD create the first editor (once)."""
    import logging
    import os
    from .auth import hash_password
    user, pw = os.environ.get("CRESPOQA_ADMIN_USER", "").strip(), os.environ.get("CRESPOQA_ADMIN_PASSWORD", "")
    if not (settings.auth_enabled and user and pw) or store.user_count() > 0:
        return
    if len(pw) < 10:
        logging.getLogger("crespoqa").warning("CRESPOQA_ADMIN_PASSWORD is shorter than 10 characters: no account created.")
        return
    store.add_user(user, hash_password(pw), "editor")
    logging.getLogger("crespoqa").info("Created the first editor account from CRESPOQA_ADMIN_USER.")


def create_app(settings: Settings | None = None, store: Store | None = None, llm=None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    store = store or Store(settings.db_path)
    sessions = Sessions(settings.session_hours)
    manager = JobManager(settings, llm if llm is not None else build_llm(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        import logging
        if not settings.auth_enabled and settings.host not in ("127.0.0.1", "localhost", "::1"):
            logging.getLogger("crespoqa").warning(
                "Login is OFF but the server listens on %s: anyone who can reach it can use and edit templates. "
                "Set CRESPOQA_AUTH=on or bind to 127.0.0.1.", settings.host)
        _bootstrap_admin(store, settings)
        if settings.auth_enabled and store.user_count() == 0:
            import logging
            logging.getLogger("crespoqa").warning("No user accounts yet. Create one: python -m crespoqa user add <name> --role editor")
        yield
        manager.stop()

    app = FastAPI(title="CrespoQA", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager, app.state.store, app.state.sessions = manager, store, sessions

    @app.exception_handler(StoreError)
    async def store_error(_req, exc: StoreError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": "Missing request header."}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        return resp

    # -- auth dependencies -------------------------------------------
    LOCAL_USER = {"username": "local", "role": "editor", "auth": False}

    def current_user(request: Request) -> dict:
        if not settings.auth_enabled:
            return LOCAL_USER
        u = sessions.get(request.cookies.get(COOKIE))
        row = store.get_user(u["username"]) if u else None
        if not row or row["disabled"]:
            raise HTTPException(401, "Please sign in.")
        return {"username": row["username"], "role": row["role"], "auth": True}

    def editor(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "editor":
            raise HTTPException(403, "Only editors can change templates.")
        return user

    def page(name: str, request: Request, need_editor: bool = False):
        if settings.auth_enabled and not sessions.get(request.cookies.get(COOKIE)):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse((WEB_DIR / name).read_text(encoding="utf-8"))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page("index.html", request)

    @app.get("/templates", response_class=HTMLResponse)
    def templates_page(request: Request):
        return page("templates.html", request)

    @app.get("/healthz")
    def healthz():
        """Unauthenticated liveness probe for systemd/nginx/monitoring. Reveals nothing."""
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        if not settings.auth_enabled:
            return RedirectResponse("/", status_code=303)
        return (WEB_DIR / "login.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    # -- session -----------------------------------------------------
    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        ip = request.client.host if request.client else "?"
        if sessions.locked(ip, body.username):
            raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
        user = check_login(store, body.username.strip(), body.password)
        if not user:
            sessions.fail(ip, body.username)
            raise HTTPException(401, "Wrong username or password.")
        sessions.ok(ip, body.username)
        resp = JSONResponse(user)
        resp.set_cookie(COOKIE, sessions.create(user), httponly=True, samesite="strict",
                        secure=settings.cookie_secure, max_age=settings.session_hours * 3600)
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        sessions.destroy(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        return user

    @app.get("/api/services")
    def services(user: dict = Depends(current_user)):
        return {"services": store.runnable_services(), "max_file_mb": settings.max_file_bytes // (1024 * 1024),
                "ai_review": manager.llm is not None}

    # -- templates ---------------------------------------------------
    @app.get("/api/templates")
    def tpl_list(user: dict = Depends(current_user)):
        return {"templates": store.list_templates()}

    @app.post("/api/templates/preview")
    def tpl_preview(body: PreviewBody, user: dict = Depends(current_user)):
        t = m.Template(name="preview", service="preview", report_type="technical", steps=parse_steps(_steps_from(body)))
        return {"description": describe(t)}

    @app.get("/api/templates-help")
    def tpl_help(user: dict = Depends(current_user)):
        return {"help": HELP}

    @app.get("/api/templates/{tid}")
    def tpl_get(tid: int, version: int | None = None, user: dict = Depends(current_user)):
        meta, t = store.get(tid, version)
        return _tpl_json(meta, t)

    @app.get("/api/templates/{tid}/versions")
    def tpl_versions(tid: int, user: dict = Depends(current_user)):
        return {"versions": store.versions(tid)}

    @app.post("/api/templates")
    def tpl_create(body: TemplateBody, user: dict = Depends(editor)):
        return {"id": store.create(body.name, body.service, body.report_type, _steps_from(body), user["username"])}

    @app.put("/api/templates/{tid}")
    def tpl_update(tid: int, body: TemplateBody, user: dict = Depends(editor)):
        return {"version": store.new_version(tid, body.name, _steps_from(body), user["username"])}

    @app.post("/api/templates/{tid}/duplicate")
    def tpl_dup(tid: int, body: DuplicateBody, user: dict = Depends(editor)):
        return {"id": store.duplicate(tid, body.service, user["username"])}

    @app.delete("/api/templates/{tid}")
    def tpl_delete(tid: int, user: dict = Depends(editor)):
        store.soft_delete(tid)
        return {"ok": True}

    # -- jobs --------------------------------------------------------
    @app.post("/api/jobs")
    async def create_job(
        user: dict = Depends(current_user),
        service: str = Form(...),
        technical: UploadFile = File(...),
        executive: UploadFile = File(...),
        consultant: str = Form(""),
        reviewer: str = Form(""),
        client: str = Form(""),
        report_date: str = Form(""),
        period: str = Form(""),
        dry_run: bool = Form(False),
        ai_review: bool = Form(False),
    ):
        try:
            templates = {"technical": store.get_active(service, "technical"), "executive": store.get_active(service, "executive")}
        except KeyError:
            raise HTTPException(400, "Unknown service, or it lacks an active Technical or Executive template.")
        if ai_review and manager.llm is None:
            raise HTTPException(400, "AI review is not enabled on this server.")
        vals = {k: v.strip() for k, v in dict(consultant=consultant, reviewer=reviewer, client=client,
                                              report_date=report_date, period=period).items()}
        if any(len(v) > MAX_FIELD for v in vals.values()):
            raise HTTPException(400, "A form field is too long.")
        for f in ("consultant", "period"):
            if any(template_uses(t, f) for t in templates.values()) and not vals[f]:
                raise HTTPException(400, f"{'Consultant full name' if f == 'consultant' else 'Engagement period'} is required for this service.")

        job_dir = manager.new_job_dir()
        uploads = []
        try:
            used: set[str] = set()
            for rtype, up in (("technical", technical), ("executive", executive)):
                name = _safe_name(up.filename, f"{rtype}_report.docx")
                if name.lower() in used:  # same file name for both uploads: keep outputs distinct
                    name = f"{Path(name).stem}_{rtype}.docx"
                used.add(name.lower())
                dest = job_dir / "in" / name
                await _save_upload(up, dest, settings.max_file_bytes)
                try:
                    validate_docx(dest, settings.max_file_bytes)
                except InvalidDocx as exc:
                    raise HTTPException(400, f"{rtype.title()} report: {exc}")
                uploads.append((rtype, name, dest))
        except HTTPException:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        job = manager.create(service, RunForm(**vals), dry_run, uploads, templates, user["username"], ai_review)
        return {"job_id": job.id}

    def _job(job_id: str, user: dict):
        job = manager.get(job_id)
        if job is None or job.owner != user["username"]:
            raise HTTPException(404, "Job not found or expired.")
        return job

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, user: dict = Depends(current_user)):
        return _job(job_id, user).snapshot()

    @app.get("/api/jobs/{job_id}/review")
    def review_status(job_id: str, user: dict = Depends(current_user)):
        job = _job(job_id, user)
        return job.review.to_dict() if job.review else {"status": "off", "findings": [], "notes": [], "error": None, "progress": ""}

    class Decision(BaseModel):
        decision: str

    @app.post("/api/jobs/{job_id}/review/{fid}")
    def review_decide(job_id: str, fid: str, body: Decision, user: dict = Depends(current_user)):
        job = _job(job_id, user)
        if body.decision not in ("open", "accepted", "ignored") or not job.review:
            raise HTTPException(400, "Invalid decision.")
        for f in job.review.findings:
            if f.id == fid:
                f.decision = body.decision   # records the human's call only; the document is never changed
                return {"ok": True}
        raise HTTPException(404, "Finding not found.")

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str, user: dict = Depends(current_user)):
        job = _job(job_id, user)

        def gen():
            i = 0
            while True:
                with job.cond:
                    while i >= len(job.events) and job.status != "done":
                        job.cond.wait(timeout=15)
                        if i >= len(job.events) and job.status != "done":
                            yield ": keepalive\n\n"
                    batch = job.events[i:]
                    i += len(batch)
                    finished = job.status == "done" and i >= len(job.events)
                for ev in batch:
                    yield f"data: {json.dumps(ev)}\n\n"
                if finished:
                    yield 'data: {"type": "end"}\n\n'
                    return

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/files/{name}")
    def download(job_id: str, name: str, user: dict = Depends(current_user)):
        job = _job(job_id, user)
        if job.files_deleted or name not in job.outputs:
            raise HTTPException(404, "File not available (already downloaded or expired).")
        resp = FileResponse(job.dir / "out" / name, filename=name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        resp.background = BackgroundTask(manager.mark_downloaded, job, name)
        return resp

    return app


def main() -> None:
    import uvicorn
    s = load_settings()
    uvicorn.run(create_app(s), host=s.host, port=s.port)


if __name__ == "__main__":
    main()
