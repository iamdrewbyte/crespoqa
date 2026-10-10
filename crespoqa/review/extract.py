"""Plain-text extraction of a .docx for review. Read-only: nothing here modifies a document."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..core.context import Ctx, RunForm
from ..core.package import DocxPackage
from ..core.steps.tables import find_details_table
from ..core.xmlutil import para_text, w
from ..templates_store import models as m


@dataclass
class ExtractedDoc:
    report_type: str
    units: list[str] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)       # index, rows, preview
    details_index: int | None = None
    details_method: str = ""
    rules: list[str] = field(default_factory=list)       # house rules from the template (or the defaults)

    @property
    def text(self) -> str:
        return "\n".join(self.units)

    def chunks(self, max_chars: int) -> list[str]:
        out, cur, size = [], [], 0
        for u in self.units:
            while len(u) > max_chars:                      # hard-split pathological long paragraphs
                if cur:
                    out.append("\n".join(cur)); cur, size = [], 0
                out.append(u[:max_chars]); u = u[max_chars:]
            if size + len(u) + 1 > max_chars and cur:
                out.append("\n".join(cur)); cur, size = [], 0
            cur.append(u); size += len(u) + 1
        if cur:
            out.append("\n".join(cur))
        return out


def _cell_text(tc: etree._Element) -> str:
    return " / ".join(t for t in (para_text(p).strip() for p in tc.iter(w("p"))) if t)


def _table_lines(tbl: etree._Element) -> list[str]:
    lines = []
    for tr in tbl.findall(w("tr")):
        cells = [_cell_text(tc) for tc in tr.findall(w("tc"))]
        if any(cells):
            lines.append(" | ".join(cells))
    return lines


def extract_for_review(path: Path, report_type: str, details: m.DocumentDetailsParams | None = None,
                       rules: list[str] | None = None) -> ExtractedDoc:
    pkg = DocxPackage(path)
    doc = ExtractedDoc(report_type)
    doc.rules = list(rules) if rules is not None else []
    for part in pkg.story_parts():
        if part == "word/document.xml":
            continue
        for p in pkg.xml(part).iter(w("p")):
            t = para_text(p).strip()
            if t:
                doc.units.append(t)
    body = pkg.document.find(w("body"))
    all_tables = list(body.iter(w("tbl")))
    for child in body:
        if child.tag == w("p"):
            t = para_text(child).strip()
            if t:
                doc.units.append(t)
        elif child.tag == w("tbl"):
            doc.units.extend(_table_lines(child))
    for i, tbl in enumerate(all_tables, 1):
        lines = _table_lines(tbl)
        doc.tables.append({"index": i, "rows": len(tbl.findall(w("tr"))), "preview": [l[:100] for l in lines[:3]]})
    if details is not None:
        ctx = Ctx(pkg=pkg, form=RunForm(), output_name=path.name, log=logging.getLogger("crespoqa.review"))
        tbl, how = find_details_table(ctx, details)
        if tbl is not None:
            doc.details_index = all_tables.index(tbl) + 1
            doc.details_method = how
    return doc
