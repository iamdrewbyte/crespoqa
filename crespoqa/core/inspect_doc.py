"""Structure-only report about a .docx. Prints counts and positions, never document text.

Safe to share when the document itself contains PII.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .docx_validate import validate_docx
from .package import DocxPackage
from .pages import element_pages
from .steps.tables import _cell_text
from .xmlutil import para_text, w


def _shape(text: str) -> str:
    """Mask content: letters -> a/A, digits -> 9, everything else kept. Reveals layout, not words."""
    return re.sub(r"[a-zà-ÿ]", "a", re.sub(r"[A-ZÀ-Þ]", "A", re.sub(r"\d", "9", text)))


def _depth(el) -> int:
    n, p = 0, el.getparent()
    while p is not None:
        n += p.tag == w("tbl")
        p = p.getparent()
    return n


def inspect_docx(path: Path, labels: list[str], details_needles: list[str], max_bytes: int = 200 * 1024 * 1024,
                 probe: str = "period") -> dict:
    validate_docx(path, max_bytes)
    pkg = DocxPackage(path)
    body = pkg.document.find(w("body"))
    epages = element_pages(body)
    pages = max(epages.values(), default=1)
    out: dict = {"estimated_pages": pages, "has_page_markers": any(True for _ in body.iter(w("lastRenderedPageBreak")))}
    out["tracked_changes"] = pkg.track_revisions_on() or any(True for _ in body.iter(w("ins"), w("del")))
    out["text_boxes"] = sum(1 for _ in body.iter(w("txbxContent")))
    out["content_controls"] = sum(1 for _ in body.iter(w("sdt")))
    out["parts"] = {"headers": sum(1 for n in pkg.story_parts() if "header" in n), "footers": sum(1 for n in pkg.story_parts() if "footer" in n)}

    found = {}
    for label in labels:
        hits = []
        by_page: Counter = Counter(epages.get(p) for p in body.iter(w("p")) if label in para_text(p))
        hits = [{"page": pg, "count": n} for pg, n in sorted(by_page.items())]
        hdr = sum(1 for part in pkg.story_parts() if part != "word/document.xml"
                  for p in pkg.xml(part).iter(w("p")) if label in para_text(p))
        found[label] = {"body": hits, "in_headers_footers_notes": hdr}
    out["label_search"] = found

    # Case-insensitive probe: shows *where* and *how* a label is stored without revealing its neighbours' text.
    hits = []
    for part in pkg.story_parts():
        root = pkg.xml(part)
        for p in root.iter(w("p")):
            full = para_text(p)
            for mt in re.finditer(re.escape(probe), full, re.I):
                ts = [t for t in p.iter(w("t"))]
                hits.append({
                    "part": part.replace("word/", ""), "page": epages.get(p) if part == "word/document.xml" else None,
                    "table_depth": _depth(p), "w_t_elements_in_paragraph": len(ts),
                    "matched_as": mt.group(0), "before": repr(full[max(0, mt.start() - 1):mt.start()]),
                    "after_shape": _shape(full[mt.end():mt.end() + 14]),
                    "paragraph_chars": len(full),
                    "any_non_ascii_near": any(ord(c) > 127 for c in full[max(0, mt.start() - 2):mt.end() + 3]),
                })
        for it in root.iter(w("instrText")):
            if probe.lower() in (it.text or "").lower():
                hits.append({"part": part.replace("word/", ""), "in_field_instruction": True})
    out["probe"] = {"word": probe, "hits": hits[:40], "total_hits": len(hits)}

    pagerefs = sum(1 for it in pkg.document.iter(w("instrText")) if (it.text or "").strip().upper().startswith("PAGEREF"))
    toc_styles = [(epages.get(pp), pp) for pp in body.iter(w("p"))
                  for st in pp.findall(f"{w('pPr')}/{w('pStyle')}")
                  if (st.get(w("val")) or "").lower().startswith(("toc", "contents", "indholdsfortegnelse"))]
    out["toc_like"] = {
        "pageref_fields": pagerefs,
        "toc_bookmarks": sum(1 for b in pkg.document.iter(w("bookmarkStart")) if (b.get(w("name")) or "").startswith("_Toc")),
        "internal_hyperlinks": sum(1 for h in pkg.document.iter(w("hyperlink")) if h.get(w("anchor"))),
        "paragraphs_with_toc_style": len(toc_styles),
        "toc_style_pages": sorted({pg for pg, _ in toc_styles}),
        "pageref_pages": sorted({epages.get(pp) for pp in body.iter(w("p")) if any((i.text or "").strip().upper().startswith("PAGEREF") for i in pp.iter(w("instrText")))}),
    }

    vals = []
    stack = []
    for el in body.iter(w("fldChar"), w("instrText"), w("t")):
        if el.tag == w("fldChar"):
            k = el.get(w("fldCharType"))
            if k == "begin":
                stack.append({"i": "", "ph": "i", "t": ""})
            elif k == "separate" and stack:
                stack[-1]["ph"] = "r"
            elif k == "end" and stack:
                f = stack.pop()
                if f["i"].strip().upper().startswith("PAGEREF"):
                    vals.append(f["t"].strip() if f["t"].strip().isdigit() else "?")
        elif el.tag == w("instrText") and stack and stack[-1]["ph"] == "i":
            stack[-1]["i"] += el.text or ""
        elif el.tag == w("t") and stack and stack[-1]["ph"] == "r":
            stack[-1]["t"] += el.text or ""
    out["pageref_cached_values"] = vals
    out["update_fields_on_open"] = bool(pkg.has("word/settings.xml") and pkg.xml("word/settings.xml").find(w("updateFields")) is not None)

    kinds: Counter = Counter()
    for part in pkg.story_parts():
        for it in pkg.xml(part).iter(w("instrText")):
            word = (it.text or "").strip().split(" ", 1)[0].upper()
            if word:
                kinds[word] += 1
        for fs in pkg.xml(part).iter(w("fldSimple")):
            word = (fs.get(w("instr")) or "").strip().split(" ", 1)[0].upper()
            if word:
                kinds[word] += 1
    out["field_types"] = dict(kinds)

    tables = []
    needles = [n.lower() for n in details_needles]
    for i, tbl in enumerate(body.iter(w("tbl")), 1):
        first = tbl.find(f"{w('tr')}/{w('tc')}")
        first_text = _cell_text(first).lower() if first is not None else ""
        top_page = epages.get(tbl)
        tables.append({
            "n": i, "page": top_page, "depth": _depth(tbl), "rows": len(tbl.findall(w("tr"))), "nested": _depth(tbl) > 0,
            "fixed_row_heights": sum(1 for _ in tbl.iter(w("trHeight"))),
            "rows_with_cantSplit": sum(1 for _ in tbl.iter(w("cantSplit"))),
            "page_breaks_in_cells": sum(1 for br in tbl.iter(w("br")) if br.get(w("type")) == "page"),
            "text_compression": sum(1 for _ in tbl.iter(w("tcFitText"), w("fitText"))),
            "first_cell_matches_details": any(n in first_text for n in needles),
        })
    out["tables"] = tables
    out["toc_start_pages"] = sorted({epages.get(pp) for pp in body.iter(w("p"))
                                     if any((i.text or "").strip().upper().startswith("TOC") for i in pp.iter(w("instrText")))})
    return out
