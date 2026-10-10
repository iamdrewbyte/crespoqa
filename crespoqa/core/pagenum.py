"""Contents / page-reference numbers.

Two sources of page numbers for each bookmark:
  * a rendered layout (LibreOffice, run AFTER all edits so the edits' effect on pagination is included), found by
    matching the text that follows each bookmark against the text of each rendered page; or
  * Word's saved `lastRenderedPageBreak` markers, a coarse estimate used only when no layout engine is present.

Generated reports contain "Area | Page Number" tables whose cells hold a literal digit (sometimes followed by a
PAGEREF, which Word shows as '28' for page 8). Those cells are cleaned and turned into real PAGEREF fields, so
pressing F9 in Word still works as a fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from .package import DocxPackage
from .pages import element_pages
from .xmlutil import set_text, w


@dataclass
class PageNumStats:
    source: str = "none"          # layout | markers | none
    available: bool = False
    pagerefs: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    missing_bookmarks: int = 0
    contents_cells: int = 0
    contents_cleaned: int = 0
    contents_fields_made: int = 0
    contents_tables: int = 0
    total_pages: int = 0
    unresolved: int = 0           # bookmarks the layout could not place


# ---------------------------------------------------------------- bookmarks -> pages from a rendered layout
_WORD = re.compile(r"[^\W_]{3,}", re.U)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def _following_text(bm: etree._Element, body: etree._Element, limit_chars: int = 90, max_paras: int = 6) -> str:
    """Text of the paragraphs from the bookmark onwards, in document order."""
    started = False
    out, paras = [], 0
    for el in body.iter(w("bookmarkStart"), w("p")):
        if el is bm:
            started = True
            p = bm.getparent()
            while p is not None and p.tag != w("p"):
                p = p.getparent()
            if p is not None:
                out.append("".join(t.text or "" for t in p.iter(w("t"))))
                paras += 1
            continue
        if started and el.tag == w("p"):
            if sum(len(x) for x in out) >= limit_chars or paras >= max_paras:
                break
            txt = "".join(t.text or "" for t in el.iter(w("t")))
            if txt.strip():
                out.append(txt); paras += 1
    return " ".join(out)


def contents_labels(pkg: DocxPackage) -> dict[str, str]:
    """bookmark name -> the contents row label that points at it (second anchor for hard-to-place bookmarks)."""
    body = pkg.document.find(w("body"))
    out: dict[str, str] = {}
    for plan in _plan_contents(body):
        for i, nm in plan["names"].items():
            out.setdefault(nm, plan["labels"][i])
    return out


def pages_from_layout(pkg: DocxPackage, page_texts: list[str], labels: dict[str, str] | None = None) -> dict[str, int]:
    """bookmark name -> 1-based rendered page. Sections are in document order, so the search never moves backwards."""
    body = pkg.document.find(w("body"))
    labels = labels or {}
    page_tokens = [set(_tokens(t)) for t in page_texts]
    result: dict[str, int] = {}
    prev = 1
    for bm in body.iter(w("bookmarkStart")):
        name = bm.get(w("name")) or ""
        if name in result:
            continue
        anchors = [list(dict.fromkeys(_tokens(_following_text(bm, body, lim))))[:14] for lim in (40, 90)]
        if labels.get(name):
            anchors.append(list(dict.fromkeys(_tokens(labels[name])))[:10])
        for toks in anchors:
            if len(toks) < 2:
                continue
            need = max(2, int(len(toks) * 0.85 + 0.999))
            hit = next((i for i in range(prev - 1, len(page_tokens))
                        if sum(1 for t in toks if t in page_tokens[i]) >= need), None)
            if hit is not None:
                result[name] = hit + 1
                prev = hit + 1
                break
    return result


# ---------------------------------------------------------------- applying numbers
_PAGEREF = re.compile(r'^\s*PAGEREF\s+"?([^\s"\\]+)"?(.*)$', re.I | re.S)
_PAGE_HEADER = re.compile(r"^\s*page(\s*(no\.?|num\.?|number))?\s*$", re.I)
_TAGS = {w("fldChar"), w("instrText"), w("t"), w("fldSimple")}


def _section_offsets(body: etree._Element, pages: dict) -> dict:
    out: dict = {}
    offset, pending = 0, []
    for p in body.iter(w("p")):
        pending.append(p)
        sect = p.find(f"{w('pPr')}/{w('sectPr')}")
        if sect is not None:
            offset = _apply_section(sect, pending, pages, out, offset)
            pending = []
    _apply_section(body.find(w("sectPr")), pending, pages, out, offset)
    return out


def _apply_section(sect, paras, pages, out, prev_offset) -> int:
    offset = prev_offset
    if sect is not None and paras:
        pg = sect.find(w("pgNumType"))
        start = pg.get(w("start")) if pg is not None else None
        if start and start.lstrip("-").isdigit():
            offset = int(start) - pages.get(paras[0], 1)
    for p in paras:
        out[p] = offset
    return offset


def refresh_page_numbers(pkg: DocxPackage, layout_pages: dict[str, int] | None = None) -> PageNumStats:
    """Set cached numbers. `layout_pages` (bookmark -> page) comes from a rendered layout; without it the saved
    markers are used. Returns what was done; touches the document part only when something changed."""
    st = PageNumStats()
    body = pkg.document.find(w("body"))
    has_markers = next(body.iter(w("lastRenderedPageBreak")), None) is not None
    pages = element_pages(body)
    offsets = _section_offsets(body, pages)

    if layout_pages is not None:
        st.source, st.available = "layout", True
        marks = dict(layout_pages)
        st.total_pages = max(marks.values(), default=0)
    elif has_markers:
        st.source, st.available = "markers", True
        marks = {}
        for bm in body.iter(w("bookmarkStart")):
            p = bm.getparent()
            while p is not None and p.tag != w("p"):
                p = p.getparent()
            if p is None:
                nxt = bm.getnext()
                while nxt is not None and nxt.tag != w("p"):
                    nxt = nxt.getnext()
                p = nxt
            if p is not None and p in pages:
                marks[bm.get(w("name"))] = pages[p] + offsets.get(p, 0)
        st.total_pages = max(pages.values(), default=1)
    else:
        st.pagerefs = sum(1 for i in body.iter(w("instrText")) if (i.text or "").strip().upper().startswith("PAGEREF"))
        return st

    names_in_order = [b.get(w("name")) for b in body.iter(w("bookmarkStart"))]
    changed = _contents_tables(body, marks, names_in_order, st)

    stack: list[dict] = []
    for el in body.iter(*_TAGS):
        if el.tag == w("fldSimple"):
            changed |= _write(el.get(w("instr")) or "", list(el.iter(w("t"))), marks, st)
            continue
        if el.tag == w("fldChar"):
            kind = el.get(w("fldCharType"))
            if kind == "begin":
                stack.append({"instr": "", "phase": "instr", "ts": []})
            elif kind == "separate" and stack:
                stack[-1]["phase"] = "result"
            elif kind == "end" and stack:
                f = stack.pop()
                changed |= _write(f["instr"], f["ts"], marks, st)
        elif el.tag == w("instrText") and stack and stack[-1]["phase"] == "instr":
            stack[-1]["instr"] += el.text or ""
        elif el.tag == w("t") and stack and stack[-1]["phase"] == "result":
            stack[-1]["ts"].append(el)
    if changed:
        pkg.touch("word/document.xml")
    return st


def _write(instr: str, ts: list, marks: dict, st: PageNumStats) -> bool:
    mt = _PAGEREF.match(instr or "")
    if not mt:
        return False
    st.pagerefs += 1
    switches = mt.group(2)
    if re.search(r"\\p\b", switches) or re.search(r"\\\*\s*(roman|alphabetic)", switches, re.I) or not ts:
        st.skipped += 1
        return False
    page = marks.get(mt.group(1))
    if page is None:
        st.missing_bookmarks += 1
        return False
    if (ts[0].text or "").strip() == str(page):
        st.unchanged += 1
        return False
    set_text(ts[0], str(page))
    for t in ts[1:]:
        t.text = ""
    st.updated += 1
    return True


# ---------------------------------------------------------------- "Area | Page Number" tables
def _cell_of(p):
    a = p.getparent()
    while a is not None and a.tag != w("tc"):
        a = a.getparent()
    return a


def _own_paras(tc):
    return [p for p in tc.iter(w("p")) if _cell_of(p) is tc]


def _scan_cell(tc):
    """(literal w:t outside fields, field-result w:t, PAGEREF bookmark name or None)."""
    literal, result, name = [], [], None
    stack: list[dict] = []
    for el in tc.iter(w("fldChar"), w("instrText"), w("t")):
        if el.tag == w("fldChar"):
            k = el.get(w("fldCharType"))
            if k == "begin":
                stack.append({"i": "", "ph": "i"})
            elif k == "separate" and stack:
                stack[-1]["ph"] = "r"
            elif k == "end" and stack:
                mt = _PAGEREF.match(stack.pop()["i"])
                if mt and name is None:
                    name = mt.group(1)
        elif el.tag == w("instrText") and stack and stack[-1]["ph"] == "i":
            stack[-1]["i"] += el.text or ""
        elif el.tag == w("t"):
            (result if stack else literal).append(el)
    return literal, result, name


def _make_field_runs(after_run: etree._Element, name: str, page: int) -> etree._Element:
    """Insert begin/instr/separate/result/end runs after `after_run`; return the result w:t."""
    rpr = after_run.find(w("rPr"))
    prev = after_run
    result_t = None

    def run(child: etree._Element) -> etree._Element:
        nonlocal prev
        r = etree.Element(w("r"))
        if rpr is not None:
            r.append(etree.fromstring(etree.tostring(rpr)))
        r.append(child)
        prev.addnext(r)
        prev = r
        return r

    def fld(kind: str) -> etree._Element:
        e = etree.Element(w("fldChar")); e.set(w("fldCharType"), kind); return e

    run(fld("begin"))
    instr = etree.Element(w("instrText")); instr.text = f" PAGEREF {name} \\h "
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    run(instr)
    run(fld("separate"))
    result_t = etree.Element(w("t")); set_text(result_t, str(page))
    run(result_t)
    run(fld("end"))
    return result_t


def _plan_contents(body) -> list[dict]:
    """Find 'Page Number' tables and decide which bookmark each data row refers to (never a blind guess)."""
    visible = [b.get(w("name")) for b in body.iter(w("bookmarkStart"))]
    visible = [n for n in visible if n and not n.startswith("_")]
    plans = []
    for tbl in body.iter(w("tbl")):
        rows = tbl.findall(w("tr"))
        if len(rows) < 3:
            continue
        hdr = rows[0].findall(w("tc"))
        col = next((i for i, tc in enumerate(hdr) if _PAGE_HEADER.match(
            " ".join("".join(t.text or "" for t in p.iter(w("t"))) for p in _own_paras(tc)))), None)
        if col is None:
            continue
        data = [r for r in rows[1:] if len(r.findall(w("tc"))) > col]
        cells = [r.findall(w("tc"))[col] for r in data]
        scans = [_scan_cell(c) for c in cells]
        labels = {i: " ".join("".join(t.text or "" for t in p.iter(w("t"))) for p in _own_paras(r.findall(w("tc"))[0])).strip()
                  for i, r in enumerate(data)}
        inside = {b.get(w("name")) for tr in rows for b in tr.iter(w("bookmarkStart"))}
        seq = [n for n in visible if n not in inside]
        names = {i: sc[2] for i, sc in enumerate(scans) if sc[2]}
        if len(seq) == len(data) and all(seq[i] == nm for i, nm in names.items()):
            names = {i: seq[i] for i in range(len(data))}     # positional mapping confirmed by every known row
        if names:
            plans.append({"cells": cells, "scans": scans, "names": names, "labels": labels})
    return plans


def _contents_tables(body, marks: dict, names_in_order: list, st: PageNumStats) -> bool:
    changed = False
    for plan in _plan_contents(body):
        st.contents_tables += 1
        for i, nm in plan["names"].items():
            literal, result, field_name = plan["scans"][i]
            digits = [t for t in literal if (t.text or "").strip().isdigit()]
            page = marks.get(nm)
            if field_name is not None:
                for t in digits:                          # '2' + field -> '28'
                    t.text = ""; st.contents_cleaned += 1; changed = True
                if page is not None and result:
                    set_text(result[0], str(page))
                    for t in result[1:]:
                        t.text = ""
                    st.contents_cells += 1; changed = True
                elif page is None:
                    st.unresolved += 1
            elif digits:                                  # literal number: make it a real, refreshable field
                if page is None:
                    st.unresolved += 1
                    continue
                run = digits[0].getparent()
                _make_field_runs(run, nm, page)
                run.getparent().remove(run)
                for t in digits[1:]:
                    t.text = ""
                st.contents_cells += 1; st.contents_fields_made += 1; changed = True
    return changed
