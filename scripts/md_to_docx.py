#!/usr/bin/env python3
"""Render markdown to a clean, plain .docx. The default document builder.

Deliberately unbranded. If you have your own branded builder, point
`QSA_DOCX_BUILDER` at it and this file is never used. Your builder is called as:

    <builder> <input.md> <output.docx> <eyebrow> <label> [internal]

Supports the subset of markdown the audit templates actually use: headings,
paragraphs, bold, italic, inline code, bullet and numbered lists, tables,
blockquotes and horizontal rules.

Two Word-specific details are handled here on purpose, because both render
wrong while a text comparison of source against output still passes:

  - Every numbered list is pinned to one indent. Word's decimal numbering
    definitions carry a different indent per numbering id, so three lists in one
    document otherwise step further right each time.
  - Heading 4 is mapped explicitly and un-hidden. Builders commonly stop at
    Heading 3, and Word ships Heading 4 flagged semiHidden so it stays out of
    the gallery.

Usage:
    python3 md_to_docx.py input.md output.docx "Eyebrow text" "Footer label" [internal]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError:
    sys.exit("python-docx is required: python3 -m pip install --user python-docx")

INK = RGBColor(0x2B, 0x2B, 0x2B)
MUTED = RGBColor(0x6E, 0x6E, 0x6E)
RULE = "BBBBBB"
BODY_PT = 10.5


def _inline(par, text: str):
    """Render **bold**, *italic* and `code` inside one paragraph."""
    for tok in re.split(r"(\*\*[^*]+\*\*|(?<!\*)\*[^*]+\*(?!\*)|`[^`]+`)", text):
        if not tok:
            continue
        run = par.add_run()
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            run.text, run.bold = tok[2:-2], True
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            run.text = tok[1:-1]
            run.font.name = "Consolas"
            run.font.size = Pt(BODY_PT - 1)
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            run.text, run.italic = tok[1:-1], True
        else:
            run.text = tok
        if run.font.size is None:
            run.font.size = Pt(BODY_PT)
        run.font.color.rgb = INK


def _hr(doc):
    p = doc.add_paragraph()
    pr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"),
                 ("w:color", RULE)):
        bottom.set(qn(k), v)
    borders.append(bottom)
    pr.append(borders)
    p.paragraph_format.space_after = Pt(8)


def _table(doc, rows: list[list[str]]):
    if not rows:
        return
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            if ci >= len(t.columns):
                break
            c = t.cell(ri, ci)
            c.text = ""
            _inline(c.paragraphs[0], cell)
            if ri == 0:
                for run in c.paragraphs[0].runs:
                    run.bold = True
    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def _unhide_heading4(doc):
    try:
        style = doc.styles["Heading 4"]
    except KeyError:
        return
    el = style.element.find(qn("w:semiHidden"))
    if el is not None:
        el.getparent().remove(el)
    el = style.element.find(qn("w:unhideWhenUsed"))
    if el is not None:
        el.getparent().remove(el)


def _pin_list_indents(doc, inches: float = 0.3):
    """One indent for every list in the document. See the module docstring."""
    for p in doc.paragraphs:
        if p.style.name.startswith(("List Bullet", "List Number")):
            p.paragraph_format.left_indent = Inches(inches)


def build(md_path, out_path, eyebrow="", label="", internal=False):
    src = Path(md_path).read_text().splitlines()
    doc = Document()
    _unhide_heading4(doc)

    normal = doc.styles["Normal"]
    normal.font.size = Pt(BODY_PT)
    normal.font.name = "Calibri"

    if eyebrow:
        p = doc.add_paragraph()
        r = p.add_run(eyebrow.upper())
        r.bold = True
        r.font.size = Pt(8)
        r.font.color.rgb = MUTED
        p.paragraph_format.space_after = Pt(2)
    if internal:
        p = doc.add_paragraph()
        r = p.add_run("INTERNAL. NOT FOR THE CLIENT.")
        r.bold = True
        r.font.size = Pt(8)
        r.font.color.rgb = RGBColor(0xB0, 0x30, 0x30)
        p.paragraph_format.space_after = Pt(6)

    i = 0
    while i < len(src):
        line = src[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            _hr(doc)
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            h = doc.add_heading(level=level)
            _inline(h, m.group(2))
            for run in h.runs:
                run.font.color.rgb = INK
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(src) and \
                re.match(r"^\s*\|[\s\-:|]+\|\s*$", src[i + 1]):
            rows = []
            while i < len(src) and src[i].strip().startswith("|"):
                if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", src[i]):
                    rows.append([c.strip() for c in src[i].strip().strip("|").split("|")])
                i += 1
            _table(doc, rows)
            continue

        if stripped.startswith(">"):
            buf = []
            while i < len(src) and src[i].strip().startswith(">"):
                buf.append(src[i].strip().lstrip("> ").rstrip())
                i += 1
            p = doc.add_paragraph(style="Quote")
            _inline(p, " ".join(buf))
            p.paragraph_format.left_indent = Inches(0.3)
            for run in p.runs:
                run.italic = True
                run.font.color.rgb = MUTED
            continue

        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _inline(p, m.group(1))
            i += 1
            continue

        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _inline(p, m.group(1))
            i += 1
            continue

        buf = [stripped]
        while i + 1 < len(src) and src[i + 1].strip() and not re.match(
                r"^\s*(#{1,6}\s|\||>|[-*]\s|\d+\.\s|-{3,}$)", src[i + 1]):
            i += 1
            buf.append(src[i].strip())
        p = doc.add_paragraph()
        _inline(p, " ".join(buf))
        p.paragraph_format.space_after = Pt(6)
        i += 1

    _pin_list_indents(doc)

    if label:
        footer = doc.sections[0].footer.paragraphs[0]
        footer.text = label
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in footer.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = MUTED

    doc.save(out_path)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    md, out = sys.argv[1], sys.argv[2]
    eyebrow = sys.argv[3] if len(sys.argv) > 3 else ""
    label = sys.argv[4] if len(sys.argv) > 4 else ""
    internal = len(sys.argv) > 5 and sys.argv[5] == "internal"
    print(build(md, out, eyebrow, label, internal))
