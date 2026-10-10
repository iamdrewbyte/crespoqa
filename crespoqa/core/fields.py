"""Field handling: evaluate simple metadata fields; leave layout-dependent fields (PAGEREF, PAGE, TOC...) untouched.

Never set w:dirty: Word refreshes dirty fields while loading, before pagination, so PAGEREF/TOC page
numbers all evaluate to 1. Only the document-level updateFields setting is used (it runs after layout).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from .package import DocxPackage
from .xmlutil import set_text, w

_SIMPLE = {"title", "author", "subject", "keywords", "comments", "lastsavedby"}
_INSTR_RE = re.compile(r'^\s*([A-Za-z]+)\s*(?:\\\S+\s*)*(?:"([^"]*)"|(\S+))?')


@dataclass
class FieldStats:
    updated: int = 0          # cached result rewritten by us
    flagged: int = 0          # left untouched; Word refreshes via the document-level updateFields setting
    tocs: int = 0
    other_indexes: int = 0    # table of figures / authorities


def _evaluate(instr: str, pkg: DocxPackage, filename: str) -> str | None:
    m = _INSTR_RE.match(instr)
    if not m:
        return None
    kind = m.group(1).lower()
    arg = m.group(2) or m.group(3)
    if kind in _SIMPLE:
        return pkg.get_property(kind) or ""
    if kind == "filename":
        return filename
    if kind == "docproperty" and arg:
        return pkg.get_property(arg)
    return None


def _classify_index(instr: str, stats: FieldStats) -> bool:
    word = instr.strip().split(None, 1)[0].upper() if instr.strip() else ""
    if word == "TOC":
        if re.search(r"\\c\b", instr):
            stats.other_indexes += 1
        else:
            stats.tocs += 1
        return True
    if word in ("TOA", "TOF"):
        stats.other_indexes += 1
        return True
    return False


def update_fields(pkg: DocxPackage, root: etree._Element, part: str, filename: str) -> FieldStats:
    """Process every field under `root`. `part` is the owning part name (touched only when a cached result was rewritten)."""
    stats = FieldStats()
    changed = False
    stack: list[dict] = []
    tags = {w("fldChar"), w("instrText"), w("t"), w("fldSimple")}
    for el in root.iter(*tags):
        if el.tag == w("fldSimple"):
            instr = el.get(w("instr")) or ""
            if _classify_index(instr, stats):
                continue
            val = _evaluate(instr, pkg, filename)
            ts = list(el.iter(w("t")))
            if val is not None and ts:
                set_text(ts[0], val)
                for t in ts[1:]:
                    t.text = ""
                stats.updated += 1
                changed = True
            else:
                stats.flagged += 1
            continue
        if el.tag == w("fldChar"):
            kind = el.get(w("fldCharType"))
            if kind == "begin":
                stack.append({"begin": el, "instr": "", "phase": "instr", "ts": []})
            elif kind == "separate" and stack:
                stack[-1]["phase"] = "result"
            elif kind == "end" and stack:
                f = stack.pop()
                if _classify_index(f["instr"], stats):
                    continue
                val = _evaluate(f["instr"], pkg, filename) if f["instr"].strip() else None
                if val is not None and f["ts"]:
                    set_text(f["ts"][0], val)
                    for t in f["ts"][1:]:
                        t.text = ""
                    stats.updated += 1
                    changed = True
                else:
                    stats.flagged += 1
        elif el.tag == w("instrText") and stack and stack[-1]["phase"] == "instr":
            stack[-1]["instr"] += el.text or ""
        elif el.tag == w("t") and stack and stack[-1]["phase"] == "result":
            stack[-1]["ts"].append(el)
    if changed:
        pkg.touch(part)
    return stats
