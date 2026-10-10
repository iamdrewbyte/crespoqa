"""In-memory view of a .docx package with surgical XML edits.

Only parts that were parsed *and* touched through this class are rewritten; every other
part is copied byte-for-byte so nothing else in the document changes.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .xmlutil import R_NS, w

CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
CUSTOM_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
CORE_CT = "application/vnd.openxmlformats-package.core-properties+xml"
CUSTOM_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"

_BUILTIN = {  # lowercase name -> (namespace, local tag)
    "author": (DC_NS, "creator"),
    "title": (DC_NS, "title"),
    "subject": (DC_NS, "subject"),
    "keywords": (CP_NS, "keywords"),
    "comments": (DC_NS, "description"),
    "lastsavedby": (CP_NS, "lastModifiedBy"),
}


class DocxPackage:
    def __init__(self, path: Path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self._infos = z.infolist()
            self._raw: dict[str, bytes] = {i.filename: z.read(i.filename) for i in self._infos}
        self._xml: dict[str, etree._Element] = {}
        self._dirty: set[str] = set()

    # -- part access -------------------------------------------------
    def has(self, name: str) -> bool:
        return name in self._raw or name in self._xml

    def xml(self, name: str) -> etree._Element:
        if name not in self._xml:
            self._xml[name] = etree.fromstring(self._raw[name])
        return self._xml[name]

    def touch(self, name: str) -> None:
        self._dirty.add(name)

    @property
    def document(self) -> etree._Element:
        return self.xml("word/document.xml")

    def story_parts(self) -> list[str]:
        """Parts containing document text: body, headers, footers, footnotes, endnotes."""
        pat = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
        return sorted((n for n in self._raw if pat.match(n)), key=lambda n: (n != "word/document.xml", n))

    # -- relationships -----------------------------------------------
    def doc_rels(self) -> dict[str, str]:
        name = "word/_rels/document.xml.rels"
        if name not in self._raw:
            return {}
        return {r.get("Id"): r.get("Target") for r in self.xml(name)}

    # -- properties --------------------------------------------------
    def _ensure_core(self) -> etree._Element:
        name = "docProps/core.xml"
        if not self.has(name):
            root = etree.Element(f"{{{CP_NS}}}coreProperties", nsmap={"cp": CP_NS, "dc": DC_NS})
            self._xml[name] = root
            self._add_part_registration(name, CORE_CT, CORE_REL)
        self.touch(name)
        return self.xml(name)

    def _ensure_custom(self) -> etree._Element:
        name = "docProps/custom.xml"
        if not self.has(name):
            root = etree.Element(f"{{{CUSTOM_NS}}}Properties", nsmap={None: CUSTOM_NS, "vt": VT_NS})
            self._xml[name] = root
            self._add_part_registration(name, CUSTOM_CT, CUSTOM_REL)
        self.touch(name)
        return self.xml(name)

    def _add_part_registration(self, part: str, content_type: str, rel_type: str) -> None:
        ct = self.xml("[Content_Types].xml")
        etree.SubElement(ct, f"{{{CT_NS}}}Override", PartName="/" + part, ContentType=content_type)
        self.touch("[Content_Types].xml")
        rels = self.xml("_rels/.rels")
        ids = {r.get("Id") for r in rels}
        n = 1
        while f"rId{n}" in ids:
            n += 1
        etree.SubElement(rels, f"{{{PKG_REL_NS}}}Relationship", Id=f"rId{n}", Type=rel_type, Target=part)
        self.touch("_rels/.rels")

    def get_property(self, name: str) -> str | None:
        key = name.lower()
        if key in _BUILTIN and self.has("docProps/core.xml"):
            ns, tag = _BUILTIN[key]
            el = self.xml("docProps/core.xml").find(f"{{{ns}}}{tag}")
            return el.text if el is not None else None
        if self.has("docProps/custom.xml"):
            for p in self.xml("docProps/custom.xml"):
                if (p.get("name") or "").lower() == key:
                    return "".join(p.itertext())
        return None

    def set_property(self, name: str, value: str) -> None:
        key = name.lower()
        if key in _BUILTIN:
            ns, tag = _BUILTIN[key]
            root = self._ensure_core()
            el = root.find(f"{{{ns}}}{tag}")
            if el is None:
                el = etree.SubElement(root, f"{{{ns}}}{tag}")
            el.text = value
            return
        root = self._ensure_custom()
        for p in root:
            if (p.get("name") or "").lower() == key:
                for child in list(p):
                    p.remove(child)
                etree.SubElement(p, f"{{{VT_NS}}}lpwstr").text = value
                return
        pids = [int(p.get("pid", "1")) for p in root] or [1]
        p = etree.SubElement(
            root, f"{{{CUSTOM_NS}}}property",
            fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}", pid=str(max(pids) + 1), name=name,
        )
        etree.SubElement(p, f"{{{VT_NS}}}lpwstr").text = value

    # -- settings ----------------------------------------------------
    def set_update_fields_on_open(self) -> None:
        name = "word/settings.xml"
        if not self.has(name):
            return
        root = self.xml(name)
        el = root.find(w("updateFields"))
        if el is None:
            el = etree.Element(w("updateFields"))
            # CT_Settings is a sequence; updateFields sits late. Insert before the first
            # element known to follow it, else append.
            followers = {"hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids",
                         "mathPr", "attachedSchema", "themeFontLang", "clrSchemeMapping",
                         "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures", "forceUpgrade",
                         "captions", "readModeInkLockDown", "smartTagType", "schemaLibrary",
                         "shapeDefaults", "doNotEmbedSmartTags", "decimalSymbol", "listSeparator"}
            for i, child in enumerate(root):
                if etree.QName(child).localname in followers:
                    root.insert(i, el)
                    break
            else:
                root.append(el)
        el.set(w("val"), "true")
        self.touch(name)

    def track_revisions_on(self) -> bool:
        if not self.has("word/settings.xml"):
            return False
        return self.xml("word/settings.xml").find(w("trackRevisions")) is not None

    # -- save --------------------------------------------------------
    def save(self, dest: Path) -> None:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
            written = set()
            for info in self._infos:
                self._write(out, info.filename)
                written.add(info.filename)
            for name in self._xml:
                if name not in written:
                    self._write(out, name)

    def _write(self, out: zipfile.ZipFile, name: str) -> None:
        if name in self._xml and (name in self._dirty or name not in self._raw):
            data = etree.tostring(self._xml[name], xml_declaration=True, encoding="UTF-8", standalone=True)
        else:
            data = self._raw[name]
        out.writestr(name, data)
