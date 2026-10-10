"""SQLite persistence: templates (versioned, soft-deleted) and local user accounts."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from . import models as m
from .seed import seed_templates

_STEPS = TypeAdapter(list[m.Step])
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, service TEXT NOT NULL,
  report_type TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS template_versions(
  template_id INTEGER NOT NULL REFERENCES templates(id), version INTEGER NOT NULL, name TEXT NOT NULL,
  steps_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(template_id, version));
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','editor')),
  disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
"""


class StoreError(ValueError):
    """User-presentable error (validation, conflict, not found)."""


def parse_steps(raw: list) -> list:
    try:
        return _STEPS.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        raise StoreError(f"Invalid step settings ({loc}): {first['msg']}") from None


class Store:
    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()
        self.seed_if_empty()

    # -- templates -----------------------------------------------------
    def seed_if_empty(self) -> None:
        with self.lock:
            if self.db.execute("SELECT 1 FROM templates LIMIT 1").fetchone():
                return
            for t in seed_templates():
                self._insert(t.name, t.service, t.report_type, t.steps, "system")

    def _insert(self, name, service, rtype, steps, user) -> int:
        now = time.time()
        cur = self.db.execute("INSERT INTO templates(name,service,report_type,created_at) VALUES(?,?,?,?)",
                              (name, service, rtype, now))
        tid = cur.lastrowid
        self._add_version(tid, 1, name, steps, user)
        self.db.commit()
        return tid

    def _add_version(self, tid, version, name, steps, user) -> None:
        blob = json.dumps([s.model_dump(mode="json") for s in steps])
        self.db.execute("INSERT INTO template_versions VALUES(?,?,?,?,?,?)", (tid, version, name, blob, user, time.time()))

    def _row_to_template(self, tr, vr) -> m.Template:
        return m.Template(name=vr["name"], service=tr["service"], report_type=tr["report_type"],
                          version=vr["version"], steps=_STEPS.validate_python(json.loads(vr["steps_json"])))

    def _latest(self, tid: int):
        return self.db.execute("SELECT * FROM template_versions WHERE template_id=? ORDER BY version DESC LIMIT 1", (tid,)).fetchone()

    def _check_unique(self, service: str, rtype: str, exclude: int | None = None) -> None:
        row = self.db.execute("SELECT id FROM templates WHERE deleted=0 AND lower(service)=lower(?) AND report_type=? AND id IS NOT ?",
                              (service, rtype, exclude)).fetchone()
        if row:
            raise StoreError(f"An active {rtype} template for '{service}' already exists.")

    def list_templates(self, include_deleted: bool = False) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM templates WHERE deleted=0 OR ? ORDER BY lower(service), report_type", (include_deleted,)).fetchall()
            out = []
            for r in rows:
                v = self._latest(r["id"])
                out.append({"id": r["id"], "name": v["name"], "service": r["service"], "report_type": r["report_type"],
                            "version": v["version"], "deleted": bool(r["deleted"]), "updated_at": v["created_at"], "updated_by": v["created_by"]})
            return out

    def get(self, tid: int, version: int | None = None) -> tuple[dict, m.Template]:
        with self.lock:
            tr = self.db.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
            if not tr:
                raise StoreError("Template not found.")
            vr = (self.db.execute("SELECT * FROM template_versions WHERE template_id=? AND version=?", (tid, version)).fetchone()
                  if version else self._latest(tid))
            if not vr:
                raise StoreError("Version not found.")
            meta = {"id": tid, "deleted": bool(tr["deleted"]), "updated_by": vr["created_by"], "updated_at": vr["created_at"]}
            return meta, self._row_to_template(tr, vr)

    def get_active(self, service: str, report_type: str) -> m.Template:
        with self.lock:
            tr = self.db.execute("SELECT * FROM templates WHERE deleted=0 AND lower(service)=lower(?) AND report_type=?",
                                 (service, report_type)).fetchone()
            if not tr:
                raise KeyError(f"No template for service={service!r} type={report_type!r}")
            return self._row_to_template(tr, self._latest(tr["id"]))

    def runnable_services(self) -> list[str]:
        """Services that have an active Technical AND Executive template."""
        with self.lock:
            rows = self.db.execute("SELECT service FROM templates WHERE deleted=0 GROUP BY lower(service) "
                                   "HAVING COUNT(DISTINCT report_type)=2 ORDER BY lower(service)").fetchall()
            return [r["service"] for r in rows]

    def versions(self, tid: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT version, name, created_by, created_at FROM template_versions WHERE template_id=? ORDER BY version DESC", (tid,)).fetchall()
            if not rows:
                raise StoreError("Template not found.")
            return [dict(r) for r in rows]

    def create(self, name: str, service: str, report_type: str, steps: list, user: str) -> int:
        name, service = name.strip(), service.strip()
        if not name or len(name) > 100:
            raise StoreError("Template name is required (max 100 characters).")
        if not _SERVICE_RE.match(service):
            raise StoreError("Service name must be 1-40 letters, digits, spaces, '.', '_' or '-'.")
        if report_type not in ("technical", "executive"):
            raise StoreError("Report type must be technical or executive.")
        steps = parse_steps(steps)
        with self.lock:
            self._check_unique(service, report_type)
            return self._insert(name, service, report_type, steps, user)

    def new_version(self, tid: int, name: str, steps: list, user: str) -> int:
        name = name.strip()
        if not name or len(name) > 100:
            raise StoreError("Template name is required (max 100 characters).")
        steps = parse_steps(steps)
        with self.lock:
            meta, _ = self.get(tid)
            if meta["deleted"]:
                raise StoreError("This template is deleted.")
            v = self._latest(tid)["version"] + 1
            self._add_version(tid, v, name, steps, user)
            self.db.commit()
            return v

    def duplicate(self, tid: int, new_service: str, user: str) -> int:
        _, t = self.get(tid)
        return self.create(f"{t.name} (copy)", new_service, t.report_type,
                           [s.model_dump(mode="json") for s in t.steps], user)

    def soft_delete(self, tid: int) -> None:
        with self.lock:
            if not self.db.execute("UPDATE templates SET deleted=1 WHERE id=?", (tid,)).rowcount:
                raise StoreError("Template not found.")
            self.db.commit()

    # -- users ---------------------------------------------------------
    def add_user(self, username: str, password_hash: str, role: str) -> None:
        if not re.match(r"^[A-Za-z0-9_.@\-]{2,60}$", username):
            raise StoreError("Username must be 2-60 characters: letters, digits, '.', '_', '-', '@'.")
        with self.lock:
            try:
                self.db.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)", (username, password_hash, role, time.time()))
                self.db.commit()
            except sqlite3.IntegrityError:
                raise StoreError("That username already exists.") from None

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def list_users(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT username, role, disabled FROM users ORDER BY username")]

    def update_user(self, username: str, **fields) -> None:
        allowed = {"password_hash", "role", "disabled"}
        assert set(fields) <= allowed
        with self.lock:
            sets = ", ".join(f"{k}=?" for k in fields)
            if not self.db.execute(f"UPDATE users SET {sets} WHERE username=?", (*fields.values(), username)).rowcount:
                raise StoreError("User not found.")
            self.db.commit()

    def user_count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
