import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from crespoqa.api.app import create_app
from crespoqa.api.auth import hash_password, verify_password
from crespoqa.templates_store.db import Store, StoreError

H = {"X-Requested-With": "CrespoQA"}


def steps_of(store, service="WebApp", rtype="technical"):
    return [s.model_dump(mode="json") for s in store.get_active(service, rtype).steps]


# ---- store -------------------------------------------------------------
def test_seed_and_services():
    s = Store(":memory:")
    assert s.runnable_services() == ["VA", "WebApp"]
    t = s.get_active("webapp", "technical")          # case-insensitive
    assert t.version == 1 and [x.type for x in t.steps][0] == "period_removal"
    assert not any(x["service"] == "PCI" for x in s.list_templates())


def test_versioning_keeps_history():
    s = Store(":memory:")
    tid = s.list_templates()[0]["id"]
    steps = [x.model_dump(mode="json") for x in s.get(tid)[1].steps]
    steps[0]["enabled"] = False
    assert s.new_version(tid, "Renamed", steps, "bob") == 2
    assert s.get(tid)[1].version == 2 and s.get(tid)[1].name == "Renamed"
    old = s.get(tid, 1)[1]
    assert old.steps[0].enabled is True and old.name != "Renamed"
    assert [v["version"] for v in s.versions(tid)] == [2, 1]


def test_create_pci_duplicate_delete_and_uniqueness():
    s = Store(":memory:")
    base = steps_of(s)
    s.create("PCI tech", "PCI", "technical", base, "ed")
    assert "PCI" not in s.runnable_services()          # executive missing
    s.create("PCI exec", "PCI", "executive", base, "ed")
    assert "PCI" in s.runnable_services()
    with pytest.raises(StoreError, match="already exists"):
        s.create("dup", "pci", "technical", base, "ed")
    tid = next(t["id"] for t in s.list_templates() if t["service"] == "PCI" and t["report_type"] == "technical")
    cid = s.duplicate(tid, "PCI-lite", "ed")
    assert s.get(cid)[1].report_type == "technical" and s.get(cid)[1].name.endswith("(copy)")
    s.soft_delete(tid)
    assert "PCI" not in s.runnable_services()
    assert all(t["id"] != tid for t in s.list_templates())
    assert any(t["id"] == tid and t["deleted"] for t in s.list_templates(include_deleted=True))
    with pytest.raises(StoreError, match="deleted"):
        s.new_version(tid, "x", base, "ed")
    s.create("PCI tech again", "PCI", "technical", base, "ed")   # name free again after soft delete


def test_validation_errors():
    s = Store(":memory:")
    with pytest.raises(StoreError, match="Invalid step"):
        s.create("bad", "X1", "technical", [{"type": "table_fix", "enabled": True, "params": {"header_repeat": "nope"}}], "ed")
    with pytest.raises(StoreError, match="Invalid step"):
        s.create("bad", "X1", "technical", [{"type": "rm_rf", "params": {}}], "ed")
    with pytest.raises(StoreError, match="Service name"):
        s.create("n", "../x", "technical", [], "ed")


# ---- auth + API ----------------------------------------------------------
@pytest.fixture
def app_env(settings):
    app = create_app(replace(settings))
    app.state.store.add_user("ed", hash_password("editor-pass-1"), "editor")
    app.state.store.add_user("usr", hash_password("user-pass-12"), "user")
    return app


def client_for(app, user, pw):
    c = TestClient(app, headers=H)
    r = c.post("/api/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return c


def test_password_hashing():
    h = hash_password("secret-secret")
    assert verify_password("secret-secret", h) and not verify_password("nope", h) and "secret-secret" not in h


def test_unauthenticated_and_csrf(app_env):
    with TestClient(app_env) as c:
        assert c.get("/api/templates").status_code == 401
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
        assert c.get("/login").status_code == 200
        assert c.post("/api/login", json={"username": "ed", "password": "editor-pass-1"}).status_code == 403   # no CSRF header
        c.headers.update(H)
        assert c.post("/api/login", json={"username": "ed", "password": "wrong"}).status_code == 401


def test_lockout_after_failures(app_env):
    with TestClient(app_env, headers=H) as c:
        for _ in range(5):
            assert c.post("/api/login", json={"username": "ed", "password": "bad"}).status_code == 401
        assert c.post("/api/login", json={"username": "ed", "password": "editor-pass-1"}).status_code == 429


def test_roles(app_env):
    with client_for(app_env, "usr", "user-pass-12") as u, client_for(app_env, "ed", "editor-pass-1") as e:
        tid = u.get("/api/templates").json()["templates"][0]["id"]
        t = u.get(f"/api/templates/{tid}").json()
        assert t["description"] and "Period" in t["description"][0]["text"] or t["description"]
        body = {"name": "x", "steps": t["steps"]}
        assert u.put(f"/api/templates/{tid}", json=body).status_code == 403
        assert u.delete(f"/api/templates/{tid}").status_code == 403
        assert u.post("/api/templates", json={**body, "service": "PCI", "report_type": "technical"}).status_code == 403
        assert e.put(f"/api/templates/{tid}", json=body).json() == {"version": 2}
        assert u.get(f"/api/templates/{tid}/versions").json()["versions"][0]["created_by"] == "ed"
        bad = e.put(f"/api/templates/{tid}", json={"name": "x", "steps": [{"type": "table_fix", "params": {"header_min_rows": "abc"}}]})
        assert bad.status_code == 400 and "Invalid step" in bad.json()["detail"]
        pv = u.post("/api/templates/preview", json={"steps": t["steps"]}).json()["description"]
        assert len(pv) == len(t["steps"])


def test_disabled_user_loses_access(app_env):
    with client_for(app_env, "usr", "user-pass-12") as u:
        assert u.get("/api/me").status_code == 200
        app_env.state.store.update_user("usr", disabled=1)
        assert u.get("/api/me").status_code == 401


def test_job_isolated_between_users(app_env, sample_docx):
    import time
    with client_for(app_env, "usr", "user-pass-12") as u, client_for(app_env, "ed", "editor-pass-1") as e:
        b = sample_docx.read_bytes()
        jid = u.post("/api/jobs", data={"service": "VA", "consultant": "A"}, files={"technical": ("a.docx", b), "executive": ("b.docx", b)}).json()["job_id"]
        assert e.get(f"/api/jobs/{jid}").status_code == 404
        assert u.get(f"/api/jobs/{jid}").status_code == 200


def test_new_service_runs_with_pinned_version(app_env, sample_docx):
    """A PCI template made through the API runs, and the report records its version."""
    import time
    store = app_env.state.store
    with client_for(app_env, "ed", "editor-pass-1") as e:
        steps = [{"type": "table_fix", "enabled": True, "params": {"header_repeat": "off"}}]
        for rt in ("technical", "executive"):
            assert e.post("/api/templates", json={"name": f"PCI {rt}", "service": "PCI", "report_type": rt, "steps": steps}).status_code == 200
        assert "PCI" in e.get("/api/services").json()["services"]
        b = sample_docx.read_bytes()
        jid = e.post("/api/jobs", data={"service": "PCI", "consultant": ""}, files={"technical": ("a.docx", b), "executive": ("b.docx", b)}).json()["job_id"]
        for _ in range(100):
            snap = e.get(f"/api/jobs/{jid}").json()
            if snap["status"] == "done":
                break
            time.sleep(0.1)
        assert [r["template_version"] for r in snap["reports"]] == [1, 1]
        assert [t["key"] for t in snap["reports"][0]["tasks"] if t["key"] != "prechecks"] == ["table_fix"]
