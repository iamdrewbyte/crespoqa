"""Task 2 (TOC) and Task 5 (final refresh)."""
from __future__ import annotations

from ...templates_store import models as m
from ..change_report import TaskResult
from ..context import Ctx
from ..fields import FieldStats, update_fields
from ..pages import element_pages
from ..xmlutil import w


def toc_update(ctx: Ctx, params) -> TaskResult:
    res = TaskResult("toc_update", "Update table of contents")
    stats = update_fields(ctx.pkg, ctx.pkg.document, "word/document.xml", ctx.output_name)
    res.counts["tocs"] = stats.tocs
    refresh = bool(getattr(params, "ask_word_to_refresh", False))
    if not stats.tocs:
        pagerefs = sum(1 for i in ctx.pkg.document.iter(w("instrText")) if (i.text or "").strip().upper().startswith("PAGEREF"))
        ctx.extras["want_page_numbers"] = True            # a contents *table* may exist; applied after all edits
        res.counts["pageref_fields"] = pagerefs
        if refresh:
            ctx.pkg.set_update_fields_on_open()
        res.changes.append("No TOC field: contents page numbers are set after the other steps (see 'Contents page numbers')")
        return res
    res.changes.append(f"Found {stats.tocs} table(s) of contents")
    ctx.extras["want_page_numbers"] = True
    if refresh:
        ctx.pkg.set_update_fields_on_open()          # real TOC fields: Word rebuilds them (after its prompt)
    body = ctx.pkg.document.find(w("body"))
    pages = element_pages(body)
    for p in body.iter(w("p")):
        if any(i.text and i.text.strip().upper().startswith("TOC") for i in p.iter(w("instrText"))):
            res.changes.append(f"TOC located on page {pages.get(p)}")
            break
    return res


def final_refresh(ctx: Ctx, params: dict) -> TaskResult:
    res = TaskResult("final_refresh", "Final global refresh")
    total = FieldStats()
    for part in ctx.pkg.story_parts():
        s = update_fields(ctx.pkg, ctx.pkg.xml(part), part, ctx.output_name)
        for f in ("updated", "flagged", "tocs", "other_indexes"):
            setattr(total, f, getattr(total, f) + getattr(s, f))
    ctx.extras["want_page_numbers"] = True
    res.counts.update(updated=total.updated, flagged=total.flagged, tocs=total.tocs, other_indexes=total.other_indexes)
    res.changes.append(f"Updated {total.updated} metadata field(s) across body, headers and footers")
    if total.tocs + total.other_indexes:
        res.changes.append(f"{total.tocs + total.other_indexes} TOC/index field(s) keep their entries; select them in Word and press F9 to rebuild")
    return res
