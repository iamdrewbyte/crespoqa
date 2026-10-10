import time
from dataclasses import replace

from fastapi.testclient import TestClient

from crespoqa.api.app import create_app

H = {"X-Requested-With": "CrespoQA"}


def _run(c, sample):
    b = sample.read_bytes()
    r = c.post("/api/jobs", data={"service": "VA", "consultant": "A"}, files={"technical": ("Report T.docx", b), "executive": ("Report E.docx", b)})
    jid = r.json()["job_id"]
    for _ in range(100):
        snap = c.get(f"/api/jobs/{jid}").json()
        if snap["status"] == "done":
            return snap
        time.sleep(0.1)
    raise AssertionError("not done")


def test_files_replace_same_named_files_in_save_dir(settings, sample_docx, tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "Report T.docx").write_bytes(b"OLD RAW FILE")
    (folder / "unrelated.txt").write_text("keep me")
    app = create_app(replace(settings, auth_enabled=False, save_dir=str(folder)))
    with TestClient(app, headers=H) as c:
        snap = _run(c, sample_docx)
    assert [x["ok"] for x in snap["saved"]] == [True, True]
    assert (folder / "Report T.docx").read_bytes()[:2] == b"PK"          # replaced in place, same name
    assert (folder / "Report E.docx").exists() and (folder / "unrelated.txt").read_text() == "keep me"
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]    # no temp debris
    assert not any("(1)" in p.name or "QA_Processed" in p.name for p in folder.iterdir())


def test_nothing_written_when_save_dir_unset(settings, sample_docx, tmp_path):
    app = create_app(replace(settings, auth_enabled=False, save_dir=""))
    with TestClient(app, headers=H) as c:
        assert _run(c, sample_docx)["saved"] == []
