"""Lightweight local auth: scrypt password hashes, in-memory server sessions, HttpOnly cookie."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE = "crespoqa_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "CrespoQA"
_N, _R, _P = 2 ** 14, 8, 1
MAX_FAILS, LOCK_SECONDS = 5, 300


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


# A dummy hash so unknown usernames cost the same time as known ones.
_DUMMY = hash_password("dummy-password-for-timing")


class Sessions:
    def __init__(self, hours: int):
        self.ttl = hours * 3600
        self._s: dict[str, tuple[dict, float]] = {}
        self._fails: dict[tuple[str, str], tuple[int, float]] = {}
        self._lock = threading.Lock()

    def create(self, user: dict) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self._s[tok] = (user, time.time() + self.ttl)
        return tok

    def get(self, tok: str | None) -> dict | None:
        if not tok:
            return None
        with self._lock:
            item = self._s.get(tok)
            if not item:
                return None
            if item[1] < time.time():
                del self._s[tok]
                return None
            return item[0]

    def destroy(self, tok: str | None) -> None:
        with self._lock:
            self._s.pop(tok or "", None)

    def drop_user(self, username: str) -> None:
        with self._lock:
            for t in [t for t, (u, _) in self._s.items() if u["username"].lower() == username.lower()]:
                del self._s[t]

    # -- brute-force throttle -----------------------------------------
    def locked(self, ip: str, username: str) -> bool:
        with self._lock:
            n, until = self._fails.get((ip, username.lower()), (0, 0))
            return n >= MAX_FAILS and until > time.time()

    def fail(self, ip: str, username: str) -> None:
        with self._lock:
            key = (ip, username.lower())
            n, until = self._fails.get(key, (0, 0))
            n = 1 if until and until < time.time() else n + 1
            self._fails[key] = (n, time.time() + LOCK_SECONDS)

    def ok(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop((ip, username.lower()), None)


def check_login(store, username: str, password: str) -> dict | None:
    row = store.get_user(username)
    stored = row["password_hash"] if row else _DUMMY
    good = verify_password(password, stored)
    if row and good and not row["disabled"]:
        return {"username": row["username"], "role": row["role"]}
    return None
