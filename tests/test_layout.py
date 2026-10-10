import os
import stat
import subprocess
import time

from crespoqa.core.pages import render_page_texts


def fake_soffice(tmp_path, body):
    f = tmp_path / "soffice"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(f)


def test_timeout_kills_whole_process_group(tmp_path):
    marker = tmp_path / "child.pid"
    # parent spawns a child (like soffice -> soffice.bin) and both would sleep for a minute
    soffice = fake_soffice(tmp_path, f"sleep 60 &\necho $! > {marker}\nsleep 60\n")
    docx = tmp_path / "a.docx"; docx.write_bytes(b"x")
    t0 = time.time()
    assert render_page_texts(docx, soffice, timeout=1) is None
    assert time.time() - t0 < 10
    pid = int(marker.read_text())
    time.sleep(0.3)
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "orphaned child process survived"


def test_missing_pdf_returns_none(tmp_path):
    soffice = fake_soffice(tmp_path, "exit 0\n")
    docx = tmp_path / "a.docx"; docx.write_bytes(b"x")
    assert render_page_texts(docx, soffice, timeout=5) is None
