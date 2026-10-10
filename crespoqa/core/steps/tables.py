"""Task 3 (Document Details table) and Task 4 (table split fixes)."""
from __future__ import annotations

from lxml import etree

from ...templates_store import models as m
from ..change_report import TaskResult
from ..context import Ctx
from ..fields import update_fields
from ..pages import element_pages
from ..xmlutil import para_text, set_text, w

_TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
                "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar",
                "tblLook", "tblCaption", "tblDescription"]


def _ensure(parent: etree._Element, name: str, order: list[str]) -> etree._Element:
    el = parent.find(w(name))
    if el is not None:
        return el
    el = etree.Element(w(name))
    idx = order.index(name)
    later = set(order[idx + 1:])
    for i, child in enumerate(parent):
        if etree.QName(child).localname in later:
            parent.insert(i, el)
            return el
    parent.append(el)
    return el


def _own_paragraphs(tc):
    """Paragraphs of this cell only, not those of tables nested inside it."""
    for p in tc.iter(w("p")):
        anc = p.getparent()
        while anc is not None and anc.tag != w("tc"):
            anc = anc.getparent()
        if anc is tc:
            yield p


def _cell_text(tc) -> str:
    return " ".join(para_text(p) for p in _own_paragraphs(tc)).strip()


def _first_cell_text(tbl) -> str:
    tc = tbl.find(f"{w('tr')}/{w('tc')}")
    return _cell_text(tc) if tc is not None else ""


def _preceding_text(tbl, n: int = 2) -> str:
    out, el = [], tbl.getprevious()
    while el is not None and len(out) < n:
        if el.tag == w("p"):
            t = para_text(el).strip()
            if t:
                out.append(t)
        el = el.getprevious()
    return " ".join(out)


def find_details_table(ctx: Ctx, params: m.DocumentDetailsParams):
    """Title match (any depth; if several, the one on the expected page) -> table on the expected page.

    Pages are computed per element, so a table nested inside a tall front-matter wrapper table gets the
    page it really sits on, not the page where the wrapper starts.
    """
    body = ctx.pkg.document.find(w("body"))
    pages = element_pages(body)
    needles = [s.lower() for s in params.matcher_text if s]
    matches = []
    for tbl in body.iter(w("tbl")):
        cap = tbl.find(f"{w('tblPr')}/{w('tblCaption')}")
        hay = " ".join([(cap.get(w("val")) if cap is not None else "") or "", _first_cell_text(tbl), _preceding_text(tbl)]).lower()
        if any(n in hay for n in needles):
            matches.append(tbl)
    fp = params.fallback_page
    if matches:
        on_page = [t for t in matches if fp and pages.get(t) == fp]
        return (on_page or matches)[0], "title match"
    if fp:
        cands = [t for t in body.iter(w("tbl")) if pages.get(t) == fp]
        if cands:
            return cands[0], f"page {fp} (fallback)"
        if ctx.page_texts and len(ctx.page_texts) >= fp:
            text = " ".join(ctx.page_texts[fp - 1].split())
            for tbl in body.iter(w("tbl")):
                first = " ".join(_first_cell_text(tbl).split())
                if first and first in text:
                    return tbl, f"rendered page {fp} (fallback)"
    return None, ""


def _set_cell_value(tc, value: str) -> None:
    p = tc.find(w("p"))
    ts = [t for t in p.iter(w("t"))]
    if ts:
        set_text(ts[0], value)
        for t in ts[1:]:
            t.text = ""
    else:
        r = etree.SubElement(p, w("r"))
        set_text(etree.SubElement(r, w("t")), value)


def expand_layout(tbl) -> dict[str, int]:
    stats = {"rows": 0, "tables": 0, "fit_text_removed": 0}
    for t in [tbl, *tbl.iterdescendants(w("tbl"))]:
        stats["tables"] += 1
        tblpr = _ensure_tblpr(t)
        layout = _ensure(tblpr, "tblLayout", _TBLPR_ORDER)
        layout.set(w("type"), "autofit")
        tw = _ensure(tblpr, "tblW", _TBLPR_ORDER)
        if tw.get(w("type")) != "pct":
            tw.set(w("type"), "pct"); tw.set(w("w"), "5000")
        if tblpr.find(w("tblCellMar")) is None:
            mar = _ensure(tblpr, "tblCellMar", _TBLPR_ORDER)
            for side, val in (("top", 40), ("left", 100), ("bottom", 40), ("right", 100)):
                e = etree.SubElement(mar, w(side)); e.set(w("w"), str(val)); e.set(w("type"), "dxa")
    for tr in tbl.iter(w("tr")):
        trpr = tr.find(w("trPr"))
        if trpr is not None:
            for h in trpr.findall(w("trHeight")):
                trpr.remove(h); stats["rows"] += 1
    for tag, parent in ((w("tcFitText"), w("tcPr")), (w("noWrap"), w("tcPr")), (w("fitText"), w("rPr"))):
        for el in list(tbl.iter(tag)):
            if el.getparent() is not None and el.getparent().tag == parent:
                el.getparent().remove(el); stats["fit_text_removed"] += 1
    return stats


def _ensure_tblpr(tbl):
    tblpr = tbl.find(w("tblPr"))
    if tblpr is None:
        tblpr = etree.Element(w("tblPr"))
        tbl.insert(0, tblpr)
    return tblpr


def document_details(ctx: Ctx, params: m.DocumentDetailsParams) -> TaskResult:
    res = TaskResult("document_details", "Update and format Document Details table")
    # 1. properties first, so fields populate correctly
    for prop, field in params.property_mappings.items():
        val = ctx.form.value(field).strip()
        if val:
            ctx.pkg.set_property(prop, val)
            res.changes.append(f"Set document property '{prop}' from {field}")
    tbl, how = find_details_table(ctx, params)
    if tbl is None:
        res.status = "warning"
        res.warnings.append("Document Details table not found (title match and page fallback both failed).")
        return res
    res.changes.append(f"Document Details table found via {how}")
    if how == "title match" and params.fallback_page:
        body = ctx.pkg.document.find(w("body"))
        pages = element_pages(body)
        if pages.get(tbl) != params.fallback_page and any(
                pages.get(t) == params.fallback_page for t in body.iter(w("tbl"))):
            res.changes.append(f"Note: the matched table is on page {pages.get(tbl)}, the template expected page {params.fallback_page}")
    for cm in params.cell_mappings:
        label = cm.get("row_label", "").strip().rstrip(":").lower()
        val = ctx.form.value(cm.get("field", "")).strip()
        for tr in tbl.iter(w("tr")):
            cells = tr.findall(w("tc"))
            if len(cells) >= 2 and _cell_text(cells[0]).strip().rstrip(":").lower() == label and val:
                _set_cell_value(cells[-1], val)
                res.changes.append(f"Filled row '{cm['row_label']}' from {cm['field']}")
    # 2. field update inside the table
    fs = update_fields(ctx.pkg, tbl, "word/document.xml", ctx.output_name)
    res.counts.update(fields_updated=fs.updated, fields_flagged=fs.flagged)
    res.changes.append(f"Updated {fs.updated} field(s) in the table; {fs.flagged} left to refresh on open")
    # 3. layout expansion
    st = expand_layout(tbl)
    ctx.pkg.touch("word/document.xml")
    res.counts.update(st)
    res.changes.append(f"Cleared {st['rows']} fixed row height(s), enabled auto-fit, removed {st['fit_text_removed']} text-compression setting(s)")
    return res


# ---- Task 4 ----------------------------------------------------------------

def _row_trpr(tr) -> etree._Element:
    trpr = tr.find(w("trPr"))
    if trpr is None:
        trpr = etree.Element(w("trPr"))
        idx = 1 if (len(tr) and tr[0].tag == w("tblPrEx")) else 0
        tr.insert(idx, trpr)
    return trpr


_EMPTY_RUN_OK = {w("rPr"), w("br"), w("lastRenderedPageBreak")}
_NEUTRAL = {w("pPr"), w("bookmarkStart"), w("bookmarkEnd"), w("proofErr")}


def _is_pagebreak_only(p) -> bool:
    ppr = p.find(w("pPr"))
    if ppr is not None and ppr.find(w("sectPr")) is not None:
        return False
    has_break = False
    for child in p:
        if child.tag in _NEUTRAL:
            continue
        if child.tag != w("r"):
            return False
        for sub in child:
            if sub.tag not in _EMPTY_RUN_OK:
                return False
            if sub.tag == w("br"):
                if sub.get(w("type")) != "page":
                    return False
                has_break = True
    return has_break and not para_text(p).strip()


def _keep_next(p) -> bool:
    """Set 'keep with next' on a paragraph (schema order: it sits right after pStyle). True if newly set."""
    ppr = p.find(w("pPr"))
    if ppr is None:
        ppr = etree.Element(w("pPr"))
        p.insert(0, ppr)
    if ppr.find(w("keepNext")) is not None:
        return False
    el = etree.Element(w("keepNext"))
    ppr.insert(1 if (len(ppr) and ppr[0].tag == w("pStyle")) else 0, el)
    return True


def keep_tables_together(root, max_rows: int) -> tuple[int, int]:
    """Top-level tables of 2..max_rows rows with no nested tables: keep every row with the next, plus the
    paragraph that introduces the table. Word then moves the whole table to the next page when it won't fit."""
    tables = intro = 0
    body = root.find(w("body"))
    for tbl in body.findall(w("tbl")):
        rows = tbl.findall(w("tr"))
        if not (2 <= len(rows) <= max_rows) or next(tbl.iterdescendants(w("tbl")), None) is not None:
            continue
        changed = False
        for tr in rows[:-1]:
            for p in tr.iter(w("p")):
                changed |= _keep_next(p)
        prev = tbl.getprevious()
        if prev is not None and prev.tag == w("p") and para_text(prev).strip():
            if _keep_next(prev):
                intro += 1
                changed = True
        tables += changed
    return tables, intro


def table_fix(ctx: Ctx, params: m.TableFixParams) -> TaskResult:
    res = TaskResult("table_fix", "Fix table splits across pages")
    root = ctx.pkg.document
    added = existing = 0
    headers_added = 0
    breaks = 0
    tables = list(root.iter(w("tbl")))
    if params.cant_split:
        for tr in root.iter(w("tr")):
            trpr = _row_trpr(tr)
            if trpr.find(w("cantSplit")) is None:
                etree.SubElement(trpr, w("cantSplit")); added += 1
            else:
                existing += 1
    if params.header_repeat != "off":
        for tbl in tables:
            rows = tbl.findall(w("tr"))
            if len(rows) >= params.header_min_rows:
                trpr = _row_trpr(rows[0])
                if trpr.find(w("tblHeader")) is None:
                    etree.SubElement(trpr, w("tblHeader")); headers_added += 1
    if params.remove_empty_page_breaks_in_cells:
        for tc in root.iter(w("tc")):
            for p in [c for c in tc if c.tag == w("p")]:
                if not _is_pagebreak_only(p):
                    continue
                n = sum(1 for _ in p.iter(w("br")))
                remaining = [c for c in tc if c is not p]
                if remaining and remaining[-1].tag == w("p"):
                    tc.remove(p)
                else:
                    for r in [c for c in p if c.tag == w("r")]:
                        p.remove(r)
                breaks += n
    kept = intro = 0
    if params.keep_together_max_rows:
        kept, intro = keep_tables_together(root, params.keep_together_max_rows)
    if added or headers_added or breaks or kept:
        ctx.pkg.touch("word/document.xml")
    res.counts.update(tables=len(tables), cant_split_added=added, cant_split_existing=existing,
                      headers_added=headers_added, page_breaks_removed=breaks, kept_together=kept)
    res.changes.append(f"Applied cantSplit to {added} row(s) ({existing} already had it) across {len(tables)} table(s)")
    res.changes.append(f"Set repeat-header on {headers_added} table(s)")
    res.changes.append(f"Removed {breaks} empty manual page break(s) inside table cells")
    if params.keep_together_max_rows:
        res.changes.append(f"Kept {kept} table(s) together on one page (a table that does not fit moves whole to the next page)")
    if not tables:
        res.warnings.append("No tables found in the document.")
        res.status = "warning"
    return res
