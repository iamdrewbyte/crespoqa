"""Page estimation without a layout engine, plus optional LibreOffice rendering.

Word stores `w:lastRenderedPageBreak` markers where pages broke when the file was last saved,
and authors add explicit page breaks. Together they give a good page map of top-level body
children. LibreOffice (if installed) is used only as a read-only cross-check; its output is
never used to build the delivered file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from lxml import etree

from .xmlutil import w


_BREAK_TAGS = None


def _is_page_br(el) -> bool:
    return el.tag == w("br") and el.get(w("type")) == "page"


def _leading_break(el):
    """A lastRenderedPageBreak that comes before any text of `el`: Word marks the START of a new page with it,
    so `el` begins on that new page. (An explicit page break instead ENDS a page, so it never counts here.)"""
    for sub in el.iter():
        if sub.tag == w("lastRenderedPageBreak"):
            return sub
        if sub.tag == w("t") and (sub.text or "").strip():
            return None
    return None


def element_pages(body: etree._Element) -> dict[etree._Element, int]:
    """element -> 1-based page on which each paragraph and table (at any nesting depth) starts.

    Uses Word's saved `lastRenderedPageBreak` markers when present (they reflect the real layout of the
    last save); otherwise explicit page breaks, page-break-before and next-page section breaks.
    A break at the very start of an element moves that element to the new page.
    """
    has_lrpb = next(body.iter(w("lastRenderedPageBreak")), None) is not None
    explicit = not has_lrpb
    page, pending = 1, 0
    out: dict[etree._Element, int] = {}
    consumed: set = set()
    for el in body.iter():
        tag = el.tag
        if tag == w("p") or tag == w("tbl"):
            page += pending; pending = 0
            if tag == w("p"):
                ppr = el.find(w("pPr"))
                if explicit and ppr is not None and ppr.find(w("pageBreakBefore")) is not None:
                    page += 1
            lead = _leading_break(el)
            if lead is not None:
                consumed.add(lead); page += 1
            out[el] = page
            if tag == w("p") and explicit:
                sect = el.find(f"{w('pPr')}/{w('sectPr')}")
                if sect is not None:
                    typ = sect.find(w("type"))
                    if typ is None or typ.get(w("val")) in (None, "nextPage", "oddPage", "evenPage"):
                        pending = 1
        elif (tag == w("lastRenderedPageBreak") or (explicit and _is_page_br(el))) and el not in consumed:
            page += 1
    return out


def body_page_map(body: etree._Element) -> list[tuple[etree._Element, int]]:
    """[(top-level paragraph/table/sdt, page where it starts)]."""
    pages = element_pages(body)
    out = []
    for child in body:
        if child.tag in (w("p"), w("tbl")):
            out.append((child, pages[child]))
        elif child.tag == w("sdt"):
            first = next((e for e in child.iter(w("p"), w("tbl"))), None)
            out.append((child, pages.get(first, 1) if first is not None else 1))
    return out


def find_soffice(configured: str = "") -> str | None:
    if configured and Path(configured).exists():
        return configured
    return shutil.which("soffice") or shutil.which("libreoffice")


def render_page_texts(docx: Path, soffice: str, timeout: int) -> list[str] | None:
    """Convert to PDF in a throwaway dir and return the text of each page, or None."""
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return None
    with tempfile.TemporaryDirectory(prefix="crespoqa_lo_") as tmp:
        profile = Path(tmp, "profile").as_uri()
        proc = subprocess.Popen(
            [soffice, "--headless", "--norestore", f"-env:UserInstallation={profile}",
             "--convert-to", "pdf", "--outdir", tmp, str(docx)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        finally:
            _kill_group(proc)  # soffice may spawn soffice.bin; always reap our own process group
        pdf = Path(tmp, docx.stem + ".pdf")
        if not pdf.exists():
            return None
        res = subprocess.run([pdftotext, "-layout", str(pdf), "-"], capture_output=True, timeout=60)
        text = res.stdout.decode("utf-8", "ignore")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        return pages


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
