"""Upload validation: real .docx, size cap, no macros, not encrypted."""
from __future__ import annotations

import zipfile
from pathlib import Path

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class InvalidDocx(Exception):
    """User-presentable validation failure."""


def validate_docx(path: Path, max_bytes: int) -> None:
    if path.suffix.lower() == ".docm":
        raise InvalidDocx("Macro-enabled files (.docm) are not accepted.")
    if path.suffix.lower() != ".docx":
        raise InvalidDocx("Only .docx files are accepted.")
    size = path.stat().st_size
    if size == 0:
        raise InvalidDocx("The file is empty.")
    if size > max_bytes:
        raise InvalidDocx(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    with path.open("rb") as fh:
        head = fh.read(8)
    if head == OLE_MAGIC:
        raise InvalidDocx("This file is password protected (encrypted). Remove the password and try again.")
    if not zipfile.is_zipfile(path):
        raise InvalidDocx("This is not a valid .docx file.")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise InvalidDocx("This is not a valid Word document (word/document.xml is missing).")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise InvalidDocx("Macro-enabled documents are not accepted.")
        ct = z.read("[Content_Types].xml").decode("utf-8", "ignore") if "[Content_Types].xml" in names else ""
        if "macroEnabled" in ct:
            raise InvalidDocx("Macro-enabled documents are not accepted.")
