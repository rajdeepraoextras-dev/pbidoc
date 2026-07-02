"""Minimal hand-written OOXML ``.docx`` writer, shared by every document-type
DOCX renderer (technical, audit, and future executive/user-guide renderers).

A ``.docx`` is just a ZIP of XML parts (OOXML). We hand-write the minimal
valid set (content types, relationships, styles, and the document body) so a
real, editable Word document is produced without ``python-docx``/``lxml``/
Pandoc. The named heading styles make it navigable and TOC-able in Word.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Poppins" w:hAnsi="Poppins" w:cs="Poppins"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="44"/><w:color w:val="1F2933"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="280" w:after="120"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="2" w:color="C9D2DA"/></w:pBdr></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:color w:val="2B6CB0"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="220" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="160" w:after="60"/></w:pPr><w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="60" w:after="120"/><w:shd w:val="clear" w:color="auto" w:fill="F6F8FA"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="18"/></w:rPr></w:style>
</w:styles>"""

_TABLE_BORDERS = (
    "<w:tblBorders>"
    + "".join(
        f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="E2E6EA"/>'
        for e in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    + "</w:tblBorders>"
)


def _x(text) -> str:
    return (
        ("" if text is None else str(text))
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


class _Docx:
    def __init__(self) -> None:
        self.body: list[str] = []

    def _run(self, text: str, *, bold=False, italic=False) -> str:
        rpr = ""
        if bold or italic:
            rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + "</w:rPr>"
        return f'<w:r>{rpr}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'

    def para(self, runs, style: str | None = None) -> None:
        if isinstance(runs, str):
            runs = [self._run(runs)]
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        self.body.append(f"<w:p>{ppr}{''.join(runs)}</w:p>")

    def heading(self, level: int, text: str) -> None:
        style = "Title" if level == 0 else f"Heading{level}"
        self.para([self._run(text)], style=style)

    def bullet(self, text: str) -> None:
        self.para([self._run("•  " + text)])

    def label(self, label: str, value: str) -> None:
        self.para([self._run(f"{label}: ", bold=True), self._run(value)])

    def code(self, text: str) -> None:
        lines = text.replace("\t", "    ").split("\n")
        runs: list[str] = []
        for i, line in enumerate(lines):
            if i:
                runs.append("<w:r><w:br/></w:r>")
            runs.append(f'<w:r><w:t xml:space="preserve">{_x(line)}</w:t></w:r>')
        self.para(runs, style="Code")

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        def cell(text: str, header: bool) -> str:
            shd = '<w:shd w:val="clear" w:color="auto" w:fill="F0F2F5"/>' if header else ""
            run = self._run(text, bold=header)
            return f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/>{shd}</w:tcPr><w:p>{run}</w:p></w:tc>'

        def row(cells: list[str], header: bool) -> str:
            return "<w:tr>" + "".join(cell(c, header) for c in cells) + "</w:tr>"

        tbl = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>' + _TABLE_BORDERS + "</w:tblPr>"]
        tbl.append(row(headers, header=True))
        for r in rows:
            tbl.append(row(r, header=False))
        tbl.append("</w:tbl>")
        # a table must be followed by a paragraph
        self.body.append("".join(tbl))
        self.body.append("<w:p/>")

    def document_xml(self) -> str:
        body = "".join(self.body)
        sect = '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{_W}"><w:body>{body}{sect}</w:body></w:document>'
        )

    def save(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
            zf.writestr("_rels/.rels", _RELS)
            zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
            zf.writestr("word/styles.xml", _STYLES)
            zf.writestr("word/document.xml", self.document_xml())
