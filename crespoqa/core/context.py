from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .package import DocxPackage


@dataclass
class RunForm:
    consultant: str = ""
    reviewer: str = ""
    client: str = ""
    report_date: str = ""
    period: str = ""

    def value(self, name: str) -> str:
        return getattr(self, name, "") or ""


@dataclass
class Ctx:
    pkg: DocxPackage
    form: RunForm
    output_name: str
    log: logging.Logger
    dry_run: bool = False
    page_texts: list[str] | None = None  # rendered page text (read-only cross-check), if available
    extras: dict = field(default_factory=dict)
