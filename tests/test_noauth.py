from dataclasses import replace

from fastapi.testclient import TestClient

from crespoqa.api.app import create_app

H = {"X-Requested-With": "CrespoQA"}


def test_no_login_needed_when_auth_off(settings, sample_docx):
    app = create_app(replace(settings, auth_enabled=False))
    with TestClient(app, headers=H) as c:
        assert c.get("/", follow_redirects=False).status_code == 200            # no redirect to /login
        assert c.get("/login", follow_redirects=False).headers["location"] == "/"
        me = c.get("/api/me").json()
        assert me["role"] == "editor" and me["auth"] is False
        t = c.get("/api/templates").json()["templates"][0]                     # templates are editable without an account
        got = c.get(f"/api/templates/{t['id']}").json()
        assert c.put(f"/api/templates/{t['id']}", json={"name": t["name"], "text": got["text"]}).status_code == 200
        b = sample_docx.read_bytes()
        r = c.post("/api/jobs", data={"service": "VA", "consultant": "A"}, files={"technical": ("t.docx", b), "executive": ("e.docx", b)})
        assert r.status_code == 200 and c.get(f"/api/jobs/{r.json()['job_id']}").status_code == 200


def test_csrf_header_still_required_without_auth(settings):
    app = create_app(replace(settings, auth_enabled=False))
    with TestClient(app) as c:
        assert c.put("/api/templates/1", json={"name": "x", "text": "toc"}).status_code == 403


def test_auth_on_still_protects(settings):
    app = create_app(replace(settings, auth_enabled=True))
    with TestClient(app) as c:
        assert c.get("/api/templates").status_code == 401
