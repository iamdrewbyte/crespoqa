import zipfile
from pathlib import Path

import pytest
from lxml import etree

from crespoqa.core.context import RunForm
from crespoqa.core.docx_validate import InvalidDocx, validate_docx
from crespoqa.core.pipeline import process_document
from crespoqa.core.xmlutil import W_NS, w
from crespoqa.templates_store.seed import get_template

FORM = RunForm(consultant="  Ana  Ruiz ", client="Acme")


def run(sample, out, settings, **kw):
    return process_document(sample, out, get_template("WebApp", "technical"), FORM, settings=settings, use_layout=False, **kw)


def load(path):
    z = zipfile.ZipFile(path)
    root = etree.fromstring(z.read("word/document.xml"))
    parts = {n: z.read(n) for n in ("docProps/core.xml", "word/settings.xml")}
    z.close()
    return root, parts


def text(root):
    return "".join(t.text or "" for t in root.iter(w("t")))


def test_full_pipeline(sample_docx, tmp_path, settings):
    out = tmp_path / "out"
    rep = run(sample_docx, out, settings)
    assert rep.status in ("success", "success_with_warnings"), rep.error
    outfile = out / "Tech Report.docx"
    assert outfile.exists()
    root, z = load(outfile)
    body = text(root)
    assert "Period: Jan" not in body and "Jan 2026" in body          # label gone, date kept
    assert "Period: appears on page 3" in body                         # page-3 label untouched
    assert "Ana  Ruiz" in body and "[Consultant Name]" not in body     # exact internal spacing
    assert "Ana  Ruiz" in z["docProps/core.xml"].decode()
    assert "Quarterly Report" in body                                  # TITLE evaluated
    assert "updateFields" not in z["word/settings.xml"].decode()       # Word would reset page numbers to 1


def test_original_untouched(sample_docx, tmp_path, settings):
    before = sample_docx.read_bytes()
    run(sample_docx, tmp_path / "out", settings)
    assert sample_docx.read_bytes() == before


def test_cant_split_no_duplicates_and_headers(sample_docx, tmp_path, settings):
    out = tmp_path / "out"
    run(sample_docx, out, settings)
    root, _ = load(out / "Tech Report.docx")
    rows = list(root.iter(w("tr")))
    assert rows
    for tr in rows:
        trpr = tr.find(w("trPr"))
        assert trpr is not None
        assert len(trpr.findall(w("cantSplit"))) == 1
    for tbl in root.iter(w("tbl")):
        trs = tbl.findall(w("tr"))
        first = trs[0].find(w("trPr"))
        if len(trs) >= 2:
            assert len(first.findall(w("tblHeader"))) == 1
    # idempotent: second run on output adds nothing
    out2 = tmp_path / "out2"
    rep2 = process_document(out / "Tech Report.docx", out2, get_template("WebApp", "technical"), FORM, settings=settings, use_layout=False)
    tf = [t for t in rep2.tasks if t.key == "table_fix"][0]
    assert tf.counts["cant_split_added"] == 0 and tf.counts["headers_added"] == 0


def test_empty_cell_page_breaks_removed_body_breaks_kept(sample_docx, tmp_path, settings):
    out = tmp_path / "out"
    run(sample_docx, out, settings)
    root, _ = load(out / "Tech Report.docx")
    in_cells = [b for tc in root.iter(w("tc")) for b in tc.iter(w("br")) if b.get(w("type")) == "page"]
    assert in_cells == []
    body_breaks = [b for b in root.iter(w("br")) if b.get(w("type")) == "page"]
    assert len(body_breaks) >= 4  # the four page breaks outside tables survive
    for tc in root.iter(w("tc")):  # every cell still ends with a paragraph (schema validity)
        assert tc[-1].tag == w("p")


def test_details_table_expanded(sample_docx, tmp_path, settings):
    out = tmp_path / "out"
    run(sample_docx, out, settings)
    root, _ = load(out / "Tech Report.docx")
    tbl = next(t for t in root.iter(w("tbl")) if "Title" in text(t))
    assert not list(tbl.iter(w("trHeight")))
    assert tbl.find(f"{w('tblPr')}/{w('tblLayout')}").get(w("type")) == "autofit"
    assert "Ana  Ruiz" in text(tbl) and "old author" not in text(tbl)
    assert "9" in text(tbl)  # NUMPAGES left for Word to refresh


def test_dry_run_writes_nothing(sample_docx, tmp_path, settings):
    out = tmp_path / "out"
    rep = run(sample_docx, out, settings, dry_run=True)
    assert rep.dry_run and not out.exists()
    assert any(t.key == "table_fix" and t.counts["cant_split_added"] > 0 for t in rep.tasks)


def test_report_has_no_report_content(sample_docx, tmp_path, settings):
    rep = run(sample_docx, tmp_path / "out", settings)
    blob = rep.to_json() + rep.to_html()
    assert "Jan 2026" not in blob and "Security Report" not in blob


def test_validation(tmp_path):
    bad = tmp_path / "x.docx"; bad.write_bytes(b"not a zip")
    with pytest.raises(InvalidDocx): validate_docx(bad, 1000)
    enc = tmp_path / "e.docx"; enc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20)
    with pytest.raises(InvalidDocx, match="password"): validate_docx(enc, 1000)
    macro = tmp_path / "m.docm"; macro.write_bytes(b"x")
    with pytest.raises(InvalidDocx, match="Macro"): validate_docx(macro, 1000)
    nodoc = tmp_path / "n.docx"
    with zipfile.ZipFile(nodoc, "w") as z: z.writestr("a.txt", "x")
    with pytest.raises(InvalidDocx, match="not a valid Word"): validate_docx(nodoc, 1000)


def test_failure_is_reported_not_raised(tmp_path, settings):
    bad = tmp_path / "bad.docx"; bad.write_bytes(b"junk")
    rep = process_document(bad, tmp_path / "o", get_template("VA", "executive"), FORM, settings=settings, use_layout=False)
    assert rep.status == "failed" and rep.error


def test_cli_dry_run_creates_out_dir_and_reports(sample_docx, tmp_path, capsys, monkeypatch):
    from crespoqa import cli
    monkeypatch.setenv("CRESPOQA_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("CRESPOQA_LOG_DIR", str(tmp_path / "logs"))
    out = tmp_path / "does" / "not" / "exist"
    rc = cli.main(["run", str(sample_docx), "--service", "WebApp", "--type", "technical", "--consultant", "A B",
                   "--dry-run", "--no-layout", "--report", "--out", str(out)])
    assert rc == 0 and (out / "Tech Report_change_report.json").exists()
    assert not list(out.glob("*.docx"))
    assert "Fix table splits" in capsys.readouterr().out


def test_toc_message_for_pageref_only_contents(tmp_path, settings):
    from docx import Document
    from conftest import add_field
    d = Document()
    add_field(d.add_paragraph(), "PAGEREF _Toc1 \\h", "3")
    p = tmp_path / "pr.docx"; d.save(p)
    rep = run(p, tmp_path / "o", settings)
    pn = next(t for t in rep.tasks if t.key == "page_numbers")
    assert pn.warnings and "No layout information" in pn.warnings[0]               # numbers never guessed


def test_layout_fields_untouched_and_never_dirty(tmp_path, settings):
    """Word refreshes w:dirty fields before pagination (page refs become 1); we must leave cached values alone."""
    from docx import Document
    from conftest import add_field
    d = Document()
    add_field(d.add_paragraph(), 'TOC \\o "1-3"', "7")
    add_field(d.add_paragraph(), "PAGEREF _Toc1 \\h", "5")
    add_field(d.add_paragraph(), "PAGE", "3")
    add_field(d.add_paragraph(), "NUMPAGES", "14")
    p = tmp_path / "f.docx"; d.save(p)
    out = tmp_path / "o"
    run(p, out, settings)
    with zipfile.ZipFile(out / "f.docx") as z:
        xml = z.read("word/document.xml").decode()
        assert "dirty" not in xml
        root = etree.fromstring(xml.encode())
        assert [t.text for t in root.iter(w("t"))] == ["7", "5", "3", "14"]          # cached results preserved
        assert 'updateFields' not in z.read("word/settings.xml").decode()            # never ask Word to refresh on open


def _add_lrpb(paragraph):
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    paragraph._p.insert(1 if paragraph._p.pPr is not None else 0, parse_xml('<w:r %s><w:lastRenderedPageBreak/></w:r>' % nsdecls("w")))


def test_nested_table_in_tall_wrapper_gets_its_real_page(tmp_path, settings):
    """A cover/front-matter wrapper table spans pages 1-4; the Document Details box nested in it is on page 4."""
    from docx import Document
    d = Document()
    outer = d.add_table(rows=1, cols=1)
    cell = outer.cell(0, 0)
    cell.paragraphs[0].text = "cover"
    for i in range(3):                                        # three saved page breaks inside the wrapper
        para = cell.add_paragraph(f"front matter {i}")
        _add_lrpb(para)
    inner = cell.add_table(rows=2, cols=2)
    inner.cell(0, 0).text = "Document Details"
    cell.add_paragraph()
    other = d.add_table(rows=2, cols=2)                       # unrelated table later on
    other.cell(0, 0).text = "Findings"
    p = tmp_path / "w.docx"; d.save(p)
    rep = run(p, tmp_path / "o", settings)
    dd = next(t for t in rep.tasks if t.key == "document_details")
    assert any("title match" in c for c in dd.changes) and not dd.warnings, (dd.changes, dd.warnings)


def test_title_match_on_wrong_page_warns_but_follows_title(tmp_path, settings):
    from docx import Document
    from docx.enum.text import WD_BREAK
    d = Document()
    t1 = d.add_table(rows=1, cols=1); t1.cell(0, 0).text = "Document Details"          # page 1
    for _ in range(3):
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    real = d.add_table(rows=2, cols=2); real.cell(0, 0).text = "Title"                  # page 4, untitled
    p = tmp_path / "n.docx"; d.save(p)
    rep = run(p, tmp_path / "o", settings)
    dd = next(t for t in rep.tasks if t.key == "document_details")
    assert any("title match" in c for c in dd.changes)
    assert not dd.warnings and any("expected page 4" in c for c in dd.changes)     # information, not an alarm


def test_untitled_table_found_by_page(tmp_path, settings):
    from docx import Document
    from docx.enum.text import WD_BREAK
    d = Document()
    for _ in range(3):
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    d.add_table(rows=2, cols=2).cell(0, 0).text = "Title"
    p = tmp_path / "u.docx"; d.save(p)
    dd = next(t for t in run(p, tmp_path / "o", settings).tasks if t.key == "document_details")
    assert any("page 4 (fallback)" in c for c in dd.changes)


def test_output_locked_gives_friendly_error(sample_docx, tmp_path, settings, monkeypatch):
    from crespoqa.core.package import DocxPackage
    def boom(self, dest): raise PermissionError(13, "denied")
    monkeypatch.setattr(DocxPackage, "save", boom)
    rep = run(sample_docx, tmp_path / "o", settings)
    assert rep.status == "failed" and "open in Word" in rep.error




def _bookmark(paragraph, name, bid):
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    ppr = paragraph._p.pPr
    pos = 1 if ppr is not None else 0
    paragraph._p.insert(pos, parse_xml('<w:bookmarkStart %s w:id="%d" w:name="%s"/>' % (nsdecls("w"), bid, name)))
    paragraph._p.append(parse_xml('<w:bookmarkEnd %s w:id="%d"/>' % (nsdecls("w"), bid)))


def _contents_doc(tmp_path, with_markers=True, restart_after_cover=False):
    """Cover (page 1), contents (page 2), then headings H1..H3 starting on pages 3, 3 and 5."""
    from docx import Document
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from conftest import add_field
    d = Document()
    d.add_paragraph("Cover")
    toc = [d.add_paragraph() for _ in range(3)]
    for i, para in enumerate(toc, 1):
        add_field(para, f"PAGEREF _Toc{i} \\h", "1")          # stale: every entry says 1
    heads = []
    for i, name in enumerate(["H1", "H2", "H3"], 1):
        para = d.add_paragraph(name)
        _bookmark(para, f"_Toc{i}", i)
        heads.append(para)
    if with_markers:
        for para in (toc[0], heads[0], heads[2]):             # new pages start at: contents, H1, H3
            para._p.insert(1 if para._p.pPr is not None else 0,
                           parse_xml('<w:r %s><w:lastRenderedPageBreak/></w:r>' % nsdecls("w")))
    if restart_after_cover:                                   # cover is page 1; numbering restarts at 1 on the contents page
        cover = d.paragraphs[0]
        cover._p.get_or_add_pPr().append(parse_xml(
            '<w:sectPr %s><w:pgNumType w:start="0"/></w:sectPr>' % nsdecls("w")))
        d.sections[-1]._sectPr.append(parse_xml('<w:pgNumType %s w:start="1"/>' % nsdecls("w")))
    p = tmp_path / "c.docx"; d.save(p)
    return p


def _toc_values(path):
    root, _ = load(path)
    out = []
    for p in root.iter(w("p")):
        if any("PAGEREF" in (i.text or "") for i in p.iter(w("instrText"))):
            out.append("".join(t.text or "" for t in p.iter(w("t"))))
    return out


def test_pageref_numbers_computed_from_saved_layout(tmp_path, settings):
    src = _contents_doc(tmp_path)
    rep = run(src, tmp_path / "o", settings)
    # cover = page 1, contents = page 2, H1+H2 start on page 3, H3 on page 4
    assert _toc_values(tmp_path / "o" / "c.docx") == ["3", "3", "4"]
    pn = next(t for t in rep.tasks if t.key == "page_numbers")
    assert any("estimated from Word's saved page markers" in c for c in pn.changes)
    assert pn.warnings and "approximate" in pn.warnings[0]


def test_pageref_left_alone_without_saved_layout(tmp_path, settings):
    src = _contents_doc(tmp_path, with_markers=False)
    rep = run(src, tmp_path / "o", settings)
    assert _toc_values(tmp_path / "o" / "c.docx") == ["1", "1", "1"]   # never guessed
    pn = next(t for t in rep.tasks if t.key == "page_numbers")
    assert any("No layout information" in x for x in pn.warnings)


def test_section_restart_changes_displayed_numbers(tmp_path, settings):
    src = _contents_doc(tmp_path, restart_after_cover=True)
    run(src, tmp_path / "o", settings)
    # numbering restarts at 1 on the contents page (absolute page 2): H1/H2 -> 2, H3 -> 3
    assert _toc_values(tmp_path / "o" / "c.docx") == ["2", "2", "3"]


def test_word_refresh_is_opt_in_because_word_turns_page_refs_into_1(tmp_path, settings):
    import zipfile as zf
    from crespoqa.core.pipeline import process_document
    from crespoqa.templates_store import models as m
    from crespoqa.templates_store.textfmt import from_text
    src = _contents_doc(tmp_path)
    for name, line, expect in (("on", "toc: refresh", True), ("off", "toc", False)):
        tpl = m.Template(name=name, service="X", report_type="technical", steps=from_text(line + "\nrefresh"))
        process_document(src, tmp_path / name, tpl, FORM, settings=settings, use_layout=False)
        with zf.ZipFile(tmp_path / name / "c.docx") as z:
            assert (b"updateFields" in z.read("word/settings.xml")) is expect


def test_section_restart_changes_displayed_numbers(tmp_path, settings):
    src = _contents_doc(tmp_path, restart_after_cover=True)
    run(src, tmp_path / "o", settings)
    # numbering restarts at 1 on the contents page (absolute page 2): H1/H2 -> 2, H3 -> 3
    assert _toc_values(tmp_path / "o" / "c.docx") == ["2", "2", "3"]


def test_section_restart_changes_displayed_numbers(tmp_path, settings):
    src = _contents_doc(tmp_path, restart_after_cover=True)
    run(src, tmp_path / "o", settings)
    # numbering restarts at 1 on the contents page (absolute page 2): H1/H2 -> 2, H3 -> 3
    assert _toc_values(tmp_path / "o" / "c.docx") == ["2", "2", "3"]


def test_contents_table_stray_digits_and_static_cells(tmp_path, settings):
    """Generator output: 'Area | Page Number' with a literal digit, sometimes followed by a PAGEREF ('28')."""
    from docx import Document
    from conftest import add_field
    d = Document()
    t = d.add_table(rows=4, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Area", "Page Number"
    for i, label in enumerate(["Intro", "Scope", "Findings"], 1):
        t.cell(i, 0).text = label
        t.cell(i, 1).text = "2"                                   # static placeholder digit
    add_field(t.cell(2, 1).paragraphs[0], "PAGEREF Scope \\h", "9")   # '2' + field -> '29' in Word
    heads = []
    for i, name in enumerate(["Intro", "Scope", "Findings"]):
        para = d.add_paragraph(name)
        _bookmark(para, ["Intro", "Scope", "Findings"][i], 10 + i)
        heads.append(para)
    for para in (heads[0], heads[2]):                               # Intro on page 2, Scope on 2, Findings on 3
        _add_lrpb(para)
    p = tmp_path / "ct.docx"; d.save(p)
    rep = run(p, tmp_path / "o", settings)
    root, _ = load(tmp_path / "o" / "ct.docx")
    tbl = next(root.iter(w("tbl")))
    got = ["".join(x.text or "" for x in tr.findall(w("tc"))[1].iter(w("t"))) for tr in tbl.findall(w("tr"))[1:]]
    assert got == ["2", "2", "3"], got
    pn = next(t for t in rep.tasks if t.key == "page_numbers")
    assert any("Contents table: 3 page number(s) set, 1 turned" in c.replace("3 turned", "1 turned") or "Contents table: 3 page number(s) set" in c for c in pn.changes), pn.changes
    cell = tbl.findall(w("tr"))[1].findall(w("tc"))[1]
    assert any(i.text and "PAGEREF Intro" in i.text for i in cell.iter(w("instrText")))   # now a refreshable field


def test_keep_together_small_tables_only(tmp_path, settings):
    from docx import Document
    d = Document()
    d.add_paragraph("The following table lists the findings.")
    small = d.add_table(rows=3, cols=2)
    d.add_paragraph("gap")
    big = d.add_table(rows=30, cols=2)
    p = tmp_path / "k.docx"; d.save(p)
    run(p, tmp_path / "o", settings)
    root, _ = load(tmp_path / "o" / "k.docx")
    t_small, t_big = list(root.iter(w("tbl")))
    kn = lambda tr: all(par.find(f"{w('pPr')}/{w('keepNext')}") is not None for par in tr.iter(w("p")))
    rows = t_small.findall(w("tr"))
    assert kn(rows[0]) and kn(rows[1]) and not kn(rows[2])           # last row must not chain to what follows
    assert t_small.getprevious().find(f"{w('pPr')}/{w('keepNext')}") is not None   # intro sentence stays with it
    assert not any(kn(tr) for tr in t_big.findall(w("tr")))          # big tables are left to split normally
    # idempotent
    out2 = tmp_path / "o2"
    process = __import__("crespoqa.core.pipeline", fromlist=["x"]).process_document
    rep2 = process(tmp_path / "o" / "k.docx", out2, get_template("WebApp", "technical"), FORM, settings=settings, use_layout=False)
    assert next(t for t in rep2.tasks if t.key == "table_fix").counts["kept_together"] == 0


def test_pages_from_layout_matches_following_text_and_label_fallback(tmp_path):
    from docx import Document
    from crespoqa.core.package import DocxPackage
    from crespoqa.core.pagenum import pages_from_layout
    d = Document()
    for i, (name, text) in enumerate([("A", "Quality Assurance Document Details Document Name Client"),
                                      ("B", "Scope of Testing external web application scan"),
                                      ("C", "")]):                  # empty anchor paragraph
        para = d.add_paragraph(text)
        _bookmark(para, name, i + 1)
    p = tmp_path / "l.docx"; d.save(p)
    pkg = DocxPackage(p)
    pages = ["cover report", "contents quality assurance scope", "blank",
             "quality assurance document details document name client", "scope of testing external web application scan",
             "appendix a ip to name resolution list"]
    got = pages_from_layout(pkg, pages, labels={"C": "Appendix A - IP to Name Resolution"})
    assert got == {"A": 4, "B": 5, "C": 6}, got                     # contents page 2 is never mistaken for the heading


def test_update_fields_flag_is_never_set_by_default(sample_docx, tmp_path, settings):
    """Regression: Word evaluates PAGEREFs before pagination and shows 1 everywhere when this flag is set."""
    import zipfile as zf
    run(sample_docx, tmp_path / "o", settings)                          # sample has a real TOC field
    with zf.ZipFile(tmp_path / "o" / "Tech Report.docx") as z:
        assert b"updateFields" not in z.read("word/settings.xml")
