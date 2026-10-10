import io
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from crespoqa.api.app import create_app
from crespoqa.api.auth import hash_password


@pytest.fixture
def client(settings):
    settings = replace(settings, ttl_minutes=30)
    app = create_app(settings)
    store = app.state.store
    store.add_user("editor1", hash_password("correct horse 1"), "editor")
    store.add_user("user1", hash_password("correct horse 2"), "user")
    with TestClient(app, headers={"X-Requested-With": "CrespoQA"}) as c:
        c.manager = app.state.manager
        c.store = app.state.store
        assert c.post("/api/login", json={"username": "user1", "password": "correct horse 2"}).status_code == 200
        yield c


def post(client, sample, **over):
    data = {"service": "WebApp", "consultant": "Ana Ruiz", "dry_run": "false"}
    data.update(over)
    b = sample.read_bytes()
    files = {"technical": ("Tech.docx", b), "executive": ("Exec.docx", b)}
    return client.post("/api/jobs", data=data, files=files)


def wait(client, job_id, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] == "done":
            return snap
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def job_dir_exists(client, job_id):
    return (client.manager.root / job_id).exists()


def test_index_and_services(client):
    assert "CrespoQA" in client.get("/").text
    assert client.get("/api/services").json()["services"] == ["VA", "WebApp"]
    assert client.get("/static/app.js").status_code == 200


def test_individual_downloads_then_deleted(client, sample_docx):
    r = post(client, sample_docx); assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    snap = wait(client, jid)
    assert sorted(snap["outputs"]) == ["Exec.docx", "Tech.docx"]
    assert all(rep["status"].startswith("success") for rep in snap["reports"])
    d = client.get(f"/api/jobs/{jid}/files/Tech.docx")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert job_dir_exists(client, jid)                      # one file still pending
    assert client.get(f"/api/jobs/{jid}/files/Exec.docx").status_code == 200
    assert not job_dir_exists(client, jid)                  # all fetched -> deleted
    assert client.get(f"/api/jobs/{jid}/files/Exec.docx").status_code == 404
    assert client.get(f"/api/jobs/{jid}/zip").status_code == 404                # no zip any more
    assert client.get(f"/api/jobs/{jid}/report/0.html").status_code == 404      # no report files either


def test_dry_run_has_no_files(client, sample_docx):
    jid = post(client, sample_docx, dry_run="true").json()["job_id"]
    snap = wait(client, jid)
    assert snap["outputs"] == [] and snap["reports"][0]["status"].startswith("success")


def test_validation_errors(client, sample_docx):
    assert post(client, sample_docx, consultant="  ").status_code == 400
    assert post(client, sample_docx, service="PCI").status_code == 400
    bad = client.post("/api/jobs", data={"service": "VA", "consultant": "A"},
                      files={"technical": ("t.docx", b"junk"), "executive": ("e.docx", sample_docx.read_bytes())})
    assert bad.status_code == 400 and "not a valid" in bad.json()["detail"]
    assert not any(client.manager.root.iterdir())           # rejected uploads leave nothing behind


def test_same_filename_for_both_uploads(client, sample_docx):
    b = sample_docx.read_bytes()
    r = client.post("/api/jobs", data={"service": "VA", "consultant": "A"},
                    files={"technical": ("Report.docx", b), "executive": ("Report.docx", b)})
    snap = wait(client, r.json()["job_id"])
    assert len(set(snap["outputs"])) == 2 and "Report.docx" in snap["outputs"]


def test_sse_stream_ends(client, sample_docx):
    jid = post(client, sample_docx).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{jid}/events") as s:
        body = "".join(s.iter_text())
    assert '"type": "end"' in body and '"key": "table_fix"' in body


def test_ttl_sweep_and_startup_cleanup(client, sample_docx):
    jid = post(client, sample_docx).json()["job_id"]
    wait(client, jid)
    client.manager.jobs[jid].created -= 31 * 60
    client.manager.sweep()
    assert client.get(f"/api/jobs/{jid}").status_code == 404 and not job_dir_exists(client, jid)
    stale = client.manager.root / "leftover"; stale.mkdir()
    client.manager.cleanup_startup()
    assert not stale.exists()


def test_path_traversal_rejected(client, sample_docx):
    jid = post(client, sample_docx).json()["job_id"]
    wait(client, jid)
    assert client.get(f"/api/jobs/{jid}/files/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_healthz_is_public(settings):
    app = create_app(replace(settings))
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}


def test_pages_have_no_inline_scripts_and_scripts_are_not_empty():
    """CSP is default-src 'self': inline <script> is blocked, so every script must be a non-empty file."""
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "crespoqa" / "web"
    for page in ("index.html", "login.html", "templates.html"):
        html = (web / page).read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), f"inline script in {page}"
        for src in re.findall(r'<script[^>]*src="/static/([^"]+)"', html):
            assert len((web / "static" / src).read_text(encoding="utf-8").strip()) > 50, f"{src} is empty"
