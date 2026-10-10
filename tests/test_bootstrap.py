from dataclasses import replace

from fastapi.testclient import TestClient

from crespoqa.api.app import create_app

H = {"X-Requested-With": "CrespoQA"}


def test_first_editor_created_from_env_once(settings, monkeypatch):
    monkeypatch.setenv("CRESPOQA_ADMIN_USER", "admin")
    monkeypatch.setenv("CRESPOQA_ADMIN_PASSWORD", "a-long-first-password")
    app = create_app(replace(settings, auth_enabled=True))
    with TestClient(app, headers=H) as c:
        r = c.post("/api/login", json={"username": "admin", "password": "a-long-first-password"})
        assert r.status_code == 200 and r.json()["role"] == "editor"
        assert app.state.store.user_count() == 1
    monkeypatch.setenv("CRESPOQA_ADMIN_PASSWORD", "changed-in-env-later")     # a restart must not touch existing accounts
    app2 = create_app(replace(settings, auth_enabled=True), store=app.state.store)
    with TestClient(app2, headers=H) as c:
        assert c.post("/api/login", json={"username": "admin", "password": "changed-in-env-later"}).status_code == 401
        assert app2.state.store.user_count() == 1


def test_short_password_or_auth_off_creates_nothing(settings, monkeypatch):
    monkeypatch.setenv("CRESPOQA_ADMIN_USER", "admin")
    monkeypatch.setenv("CRESPOQA_ADMIN_PASSWORD", "short")
    app = create_app(replace(settings, auth_enabled=True))
    with TestClient(app, headers=H):
        assert app.state.store.user_count() == 0
    monkeypatch.setenv("CRESPOQA_ADMIN_PASSWORD", "a-long-first-password")
    app = create_app(replace(settings, auth_enabled=False, db_path=settings.db_path.with_name("other.db")))
    with TestClient(app, headers=H):
        assert app.state.store.user_count() == 0
