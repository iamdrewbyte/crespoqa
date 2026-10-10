import copy
from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from crespoqa.core.config import Settings


def add_field(paragraph, instr: str, result: str = "old"):
    """Append a complex field (begin/instr/separate/result/end) to a paragraph."""
    xml = (
        '<w:r %s><w:fldChar w:fldCharType="begin"/></w:r>' % nsdecls("w"),
        '<w:r %s><w:instrText xml:space="preserve"> %s </w:instrText></w:r>' % (nsdecls("w"), instr),
        '<w:r %s><w:fldChar w:fldCharType="separate"/></w:r>' % nsdecls("w"),
        '<w:r %s><w:t>%s</w:t></w:r>' % (nsdecls("w"), result),
        '<w:r %s><w:fldChar w:fldCharType="end"/></w:r>' % nsdecls("w"),
    )
    for x in xml:
        paragraph._p.append(parse_xml(x))


def set_row_height(row, twips=200):
    trpr = row._tr.get_or_add_trPr()
    trpr.append(parse_xml('<w:trHeight %s w:val="%d" w:hRule="exact"/>' % (nsdecls("w"), twips)))


@pytest.fixture
def settings(tmp_path):
    return Settings(temp_dir=tmp_path / "work", max_file_bytes=50 * 1024 * 1024, doc_timeout_s=60,
                    soffice="", log_dir=tmp_path / "logs", log_level="INFO", db_path=tmp_path / "test.db", auth_enabled=True)


@pytest.fixture
def sample_docx(tmp_path) -> Path:
    doc = Document()
    doc.core_properties.author = "Template Author"
    doc.add_paragraph("Security Report")
    p = doc.add_paragraph()
    p.add_run("Per")          # label split across runs on purpose
    p.add_run("iod: ").bold = True
    p.add_run("Jan 2026")
    doc.add_paragraph("Prepared for [Consultant Name]")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("Contents")
    add_field(doc.add_paragraph(), 'TOC \\o "1-3" \\h', "stale toc")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("Period: appears on page 3 and must stay")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("Document Details")
    t = doc.add_table(rows=3, cols=2)
    t.cell(0, 0).text = "Title"; t.cell(1, 0).text = "Author"; t.cell(2, 0).text = "Pages"
    add_field(t.cell(0, 1).paragraphs[0], "TITLE", "old title")
    add_field(t.cell(1, 1).paragraphs[0], "AUTHOR", "old author")
    add_field(t.cell(2, 1).paragraphs[0], "NUMPAGES", "9")
    for r in t.rows:
        set_row_height(r)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    f = doc.add_table(rows=3, cols=2)
    for r in f.rows:
        for c in r.cells:
            c.text = "x"
    # empty manual page break in a cell + a legitimate one with text
    f.cell(1, 0).add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    # a row that already has cantSplit
    f.rows[2]._tr.get_or_add_trPr().append(parse_xml('<w:cantSplit %s/>' % nsdecls("w")))
    # nested table
    inner = f.cell(0, 1).add_table(rows=2, cols=1)
    inner.cell(0, 0).text = "nested"
    f.cell(0, 1).add_paragraph()
    # merged cells
    f.cell(2, 0).merge(f.cell(2, 1))
    doc.core_properties.title = "Quarterly Report"
    path = tmp_path / "Tech Report.docx"
    doc.save(path)
    return path
