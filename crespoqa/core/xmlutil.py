"""Shared WordprocessingML helpers."""
from __future__ import annotations

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W_NS, "r": R_NS}


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def wattr(el: etree._Element, name: str) -> str | None:
    return el.get(w(name))


def nearest_p(el: etree._Element) -> etree._Element | None:
    parent = el.getparent()
    while parent is not None and parent.tag != w("p"):
        parent = parent.getparent()
    return parent


def own_texts(p: etree._Element) -> list[etree._Element]:
    """w:t elements belonging to paragraph p itself (not to paragraphs nested in text boxes)."""
    return [t for t in p.iter(w("t")) if nearest_p(t) is p]


def para_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in own_texts(p))


def set_text(t: etree._Element, text: str) -> None:
    t.text = text
    t.set(f"{{{XML_NS}}}space", "preserve")


def replace_in_paragraph(p: etree._Element, find: str, replace: str, *, strip_trailing_space: bool = False) -> int:
    """Replace every occurrence of `find` in paragraph p, even when split across runs.

    The replacement inherits the formatting of the run holding the first matched character.
    With strip_trailing_space and an empty replacement, one space following the match is removed too.
    """
    if not find:
        return 0
    count = 0
    start_from = 0
    while True:
        ts = own_texts(p)
        full = "".join(t.text or "" for t in ts)
        idx = full.find(find, start_from)
        if idx < 0:
            return count
        end = idx + len(find)
        if strip_trailing_space and not replace and full[end:end + 1] == " ":
            end += 1
        pos = 0
        first = True
        for t in ts:
            txt = t.text or ""
            lo, hi = pos, pos + len(txt)
            pos = hi
            if hi <= idx or lo >= end:
                continue
            a, b = max(idx, lo) - lo, min(end, hi) - lo
            new = txt[:a] + (replace if first else "") + txt[b:]
            first = False
            set_text(t, new)
        count += 1
        start_from = idx + len(replace)
