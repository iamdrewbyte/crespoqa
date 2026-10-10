"""Logging that never carries report content: callers pass only IDs, step names, counts, status."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import Settings


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger("crespoqa")
    if root.handlers:
        return
    root.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(settings.log_dir / "crespoqa.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        root.warning("Could not open log file; logging to terminal only")


class JobLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[job {self.extra['job']}] {msg}", kwargs
