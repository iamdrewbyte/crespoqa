"""Task 1 (Period label) plus placeholder and find/replace steps."""
from __future__ import annotations

import re

from lxml import etree

from ...templates_store import models as m
from ..change_report import TaskResult
from ..context import Ctx
from ..pages import element_pages
from ..xmlutil import R_NS, replace_in_paragraph, w


def _paragraphs(root: etree._Element):
    yield from root.iter(w("p"))


def _first_section_header_parts(ctx: Ctx) -> list[str]:
    body = ctx.pkg.document.find(w("body"))
    sect = None
    for p in body.iter(w("p")):
        s = p.find(f"{w('pPr')}/{w('sectPr')}")
        if s is not None:
            sect = s
            break
    if sect is None:
        sect = body.find(w("sectPr"))
    if sect is None:
        return []
    rels = ctx.pkg.doc_rels()
    parts = []
    for ref in sect.findall(w("headerReference")):
        target = rels.get(ref.get(f"{{{R_NS}}}id"))
        if target:
            part = "word/" + target.lstrip("/").removeprefix("word/")
            if ctx.pkg.has(part):
                parts.append(part)
    return parts


def _page1_roots(ctx: Ctx) -> list[tuple[str, etree._Element]]:
    body = ctx.pkg.document.find(w("body"))
    pages = element_pages(body)
    roots = [("word/document.xml", p) for p in body.iter(w("p")) if pages.get(p) == 1]
    roots += [(p, ctx.pkg.xml(p)) for p in _first_section_header_parts(ctx)]
    return roots


def _replace_in(ctx: Ctx, roots, find: str, replace: str, strip_space: bool) -> int:
    n = 0
    for part, root in roots:
        for p in _paragraphs(root):
            c = replace_in_paragraph(p, find, replace, strip_trailing_space=strip_space)
            if c:
                ctx.pkg.touch(part)
                n += c
    return n


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def period_removal(ctx: Ctx, params: m.PeriodRemovalParams) -> TaskResult:
    fill = params.period_label_action == "fill"
    res = TaskResult("period_removal", "Remove 'Period:' label on page 1" if not fill else "Fill 'Period:' label on page 1")
    if fill and not ctx.form.period.strip():
        res.warnings.append("Period fill requested but no engagement period was supplied; labels were left untouched.")
        res.status = "warning"
        return res
    total = 0
    roots = _page1_roots(ctx)
    for label in params.labels:
        total += _replace_in(ctx, roots, label, ctx.form.period.strip() if fill else "", strip_space=not fill)
        if ctx.page_texts and total == 0 and _norm(label) in _norm(ctx.page_texts[0]):
            res.warnings.append(f"Layout check sees '{label}' on page 1 but it was not found in the page-1 XML scope (text box, field or unusual structure?).")
    verb = "Replaced" if fill else "Removed"
    res.counts["occurrences"] = total
    if total:
        res.changes.append(f"{verb} {total} occurrence(s) of {', '.join(repr(l) for l in params.labels)} on page 1" + (" (dry run: would be)" if ctx.dry_run else ""))
    else:
        res.changes.append(f"No {', '.join(repr(l) for l in params.labels)} label found on page 1 (nothing to remove)")
    return res


def placeholders(ctx: Ctx, params: m.PlaceholdersParams) -> TaskResult:
    res = TaskResult("placeholders", "Fill placeholders")
    for mp in params.mappings:
        value = ctx.form.value(mp.field).strip()
        found = 0
        for part in ctx.pkg.story_parts():
            for p in _paragraphs(ctx.pkg.xml(part)):
                if value:
                    c = replace_in_paragraph(p, mp.token, value)
                else:
                    c = _count(p, mp.token)
                if c:
                    found += c
                    if value:
                        ctx.pkg.touch(part)
        if found and not value:
            res.warnings.append(f"Placeholder '{mp.token}' found {found}x but no {mp.field} value was supplied.")
            res.status = "warning"
        elif found:
            res.changes.append(f"Filled {found} placeholder(s) '{mp.token}' from {mp.field}")
        res.counts[mp.token] = found
    if not res.changes and not res.warnings:
        res.changes.append("No placeholders present.")
    return res


def _count(p, token: str) -> int:
    from ..xmlutil import para_text
    return para_text(p).count(token)


def find_replace(ctx: Ctx, params: m.FindReplaceParams) -> TaskResult:
    res = TaskResult("find_replace", "Custom find/replace")
    for rule in params.rules:
        roots = _page1_roots(ctx) if rule.scope == "page1" else [(p, ctx.pkg.xml(p)) for p in ctx.pkg.story_parts()]
        n = _replace_in(ctx, roots, rule.find, rule.replace, strip_space=False)
        res.counts[rule.find] = n
        res.changes.append(f"Rule '{rule.find}' ({rule.scope}): {n} replacement(s)")
    if not params.rules:
        res.changes.append("No rules configured.")
    return res
