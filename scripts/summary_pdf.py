#!/usr/bin/env python3
"""
The Tribunal — Session Summary PDF Generator.

Reads a session-summary.md file (the canonical output from council_orchestrator.py)
and renders it as a clean, simple PDF using ReportLab.

Standalone usage:
    python summary_pdf.py /path/to/session-summary.md [output.pdf]

Programmatic usage:
    from summary_pdf import generate_summary_pdf
    pdf_path = generate_summary_pdf("/path/to/session-summary.md")

Requires: reportlab>=4.0
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle,
    Frame, PageTemplate, BaseDocTemplate, Flowable,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BLACK      = HexColor("#000000")
DARK_GRAY  = HexColor("#333333")
MED_GRAY   = HexColor("#666666")
LIGHT_GRAY = HexColor("#999999")
RULE_GRAY  = HexColor("#CCCCCC")
WHITE      = HexColor("#FFFFFF")
TABLE_ALT  = HexColor("#F5F5F5")

PAGE_W, PAGE_H = letter
LEFT_MARGIN = 0.65 * inch
RIGHT_MARGIN = 0.65 * inch
TOP_MARGIN = 0.6 * inch
BOTTOM_MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN


class ThinRule(Flowable):
    def __init__(self, width, color=RULE_GRAY, thickness=0.5):
        Flowable.__init__(self)
        self.width = width
        self.color = color
        self.thickness = thickness
        self.height = 6

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 3, self.width, 3)


# ---------------------------------------------------------------------------
# Styles — Times-Roman, simple sizes, tight spacing for 2-page fit
# ---------------------------------------------------------------------------

def build_styles():
    styles = {}

    styles['title'] = ParagraphStyle(
        'Title', fontName='Times-Bold', fontSize=17, leading=20,
        textColor=BLACK, spaceAfter=2, alignment=TA_LEFT,
    )
    styles['subtitle'] = ParagraphStyle(
        'Subtitle', fontName='Times-Roman', fontSize=9.5, leading=11.5,
        textColor=MED_GRAY, spaceAfter=6, alignment=TA_LEFT,
    )
    styles['h1'] = ParagraphStyle(
        'H1', fontName='Times-Bold', fontSize=13.5, leading=16.5,
        textColor=BLACK, spaceBefore=10, spaceAfter=3, alignment=TA_LEFT,
    )
    styles['h2'] = ParagraphStyle(
        'H2', fontName='Times-Bold', fontSize=12.5, leading=15,
        textColor=DARK_GRAY, spaceBefore=7, spaceAfter=2, alignment=TA_LEFT,
    )
    styles['h3'] = ParagraphStyle(
        'H3', fontName='Times-BoldItalic', fontSize=11.5, leading=14,
        textColor=DARK_GRAY, spaceBefore=5, spaceAfter=2, alignment=TA_LEFT,
    )
    styles['body'] = ParagraphStyle(
        'Body', fontName='Times-Roman', fontSize=11, leading=14,
        textColor=BLACK, spaceAfter=4, alignment=TA_JUSTIFY,
    )
    styles['body_bold'] = ParagraphStyle(
        'BodyBold', fontName='Times-Bold', fontSize=11, leading=14,
        textColor=BLACK, spaceAfter=4, alignment=TA_LEFT,
    )
    styles['bullet'] = ParagraphStyle(
        'Bullet', fontName='Times-Roman', fontSize=11, leading=13.5,
        textColor=BLACK, spaceAfter=2, leftIndent=14, bulletIndent=4,
        alignment=TA_LEFT,
    )
    styles['sub_bullet'] = ParagraphStyle(
        'SubBullet', fontName='Times-Roman', fontSize=10.5, leading=13,
        textColor=DARK_GRAY, spaceAfter=1, leftIndent=28, bulletIndent=18,
        alignment=TA_LEFT,
    )
    styles['quote'] = ParagraphStyle(
        'Quote', fontName='Times-Italic', fontSize=11, leading=14,
        textColor=MED_GRAY, spaceAfter=4, leftIndent=14, alignment=TA_LEFT,
    )
    styles['callout'] = ParagraphStyle(
        'Callout', fontName='Times-Roman', fontSize=11, leading=14,
        textColor=BLACK, alignment=TA_LEFT,
    )
    styles['callout_bold'] = ParagraphStyle(
        'CalloutBold', fontName='Times-Bold', fontSize=11, leading=14,
        textColor=BLACK, alignment=TA_LEFT,
    )
    styles['table_header'] = ParagraphStyle(
        'TableHeader', fontName='Times-Bold', fontSize=10, leading=12.5,
        textColor=WHITE, alignment=TA_LEFT,
    )
    styles['table_cell'] = ParagraphStyle(
        'TableCell', fontName='Times-Roman', fontSize=10, leading=12.5,
        textColor=BLACK, alignment=TA_LEFT,
    )
    styles['table_cell_bold'] = ParagraphStyle(
        'TableCellBold', fontName='Times-Bold', fontSize=10, leading=12.5,
        textColor=BLACK, alignment=TA_LEFT,
    )
    styles['build_this'] = ParagraphStyle(
        'BuildThis', fontName='Times-Roman', fontSize=11, leading=14,
        textColor=BLACK, spaceAfter=4, alignment=TA_LEFT,
    )

    return styles


# ---------------------------------------------------------------------------
# Document template — page numbers only
# ---------------------------------------------------------------------------

class SimpleDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)
        frame = Frame(
            LEFT_MARGIN, BOTTOM_MARGIN,
            CONTENT_W, PAGE_H - TOP_MARGIN - BOTTOM_MARGIN,
            id='normal'
        )
        self.addPageTemplates([
            PageTemplate(id='Normal', frames=[frame], onPage=self._page_footer),
        ])

    def _page_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont('Times-Roman', 7)
        canvas.setFillColor(LIGHT_GRAY)
        canvas.drawCentredString(PAGE_W / 2, BOTTOM_MARGIN - 16,
                                 str(canvas.getPageNumber()))
        canvas.restoreState()


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _escape_xml(text):
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _md_inline_to_xml(text):
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<font face="Courier" size="7">\1</font>', text)
    return text


def _parse_header_meta(header_block):
    meta = {}
    for line in header_block.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            meta["title"] = line[2:].strip()
        elif line.startswith("**") and "Session:" in line:
            inner = line.strip("*").strip()
            for part in inner.split("|"):
                part = part.strip()
                if ":" in part:
                    key, val = part.split(":", 1)
                    meta[key.strip().lower()] = val.strip()
        elif line.startswith("*") and "Full logs:" in line:
            meta["logs_line"] = line.strip("*").strip()
        elif line.startswith("*Note:") or line.startswith("*note:"):
            meta["anonymization_note"] = line.strip("*").strip()
    return meta


def _parse_table(lines):
    headers = []
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue
        if all(re.match(r'^[-:]+$', c) for c in cells):
            continue
        if not headers:
            headers = cells
        else:
            rows.append(cells)
    return headers, rows


def _parse_council_subsections(text):
    advocates = []
    current = None

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#### "):
            if current:
                advocates.append(current)
            heading = stripped[5:].strip()
            parts = re.split(r'\s*[—–-]\s*Rank\s*', heading, maxsplit=1)
            model = parts[0].strip() if parts else heading
            rank = ""
            if len(parts) > 1:
                rank_match = re.search(r'#?(\d+)', parts[1])
                if rank_match:
                    rank = rank_match.group(1)
                elif "-" in parts[1]:
                    rank = "-"
            current = {"model": model, "rank": rank, "opening": "", "final": "", "catalyst": ""}
            continue
        if current is None:
            continue
        if stripped.startswith("**Opening Position:**"):
            current["opening"] = stripped.replace("**Opening Position:**", "").strip()
        elif stripped.startswith("**Final Position:**"):
            current["final"] = stripped.replace("**Final Position:**", "").strip()
        elif stripped.startswith("**Key Catalyst:**"):
            current["catalyst"] = stripped.replace("**Key Catalyst:**", "").strip()

    if current:
        advocates.append(current)
    return advocates


def _strip_yaml_frontmatter(md_text):
    stripped = md_text.lstrip()
    if not stripped.startswith("---"):
        return {}, md_text
    lines = stripped.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, md_text
    meta = {}
    for line in lines[1:end_idx]:
        line = line.strip()
        if ":" in line and not line.startswith("-") and not line.startswith("#"):
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    remaining = "\n".join(lines[end_idx + 1:]).lstrip("\n")
    return meta, remaining


# ---------------------------------------------------------------------------
# Markdown parser — targeted at the session-summary.md format
# ---------------------------------------------------------------------------

def parse_session_summary(md_text):
    """Parse session-summary.md into structured sections.

    Backward-compatible: accepts old names (Recommended Outcome, Bottom Line,
    Council Performance, Scorecard) and new executive briefing names (Summary,
    Key Assertions, Context, The Landscape, Fault Lines, So What).
    """
    result = {
        "header_meta": {},
        "the_prompt": "",
        "recommended_outcome": "",
        "summary_paragraph": None,
        "key_assertions": None,
        "majority_opinion": None,
        "council_performance": ([], []),
        "convergence_assessment": None,
        "context": None,
        "key_moments": [],
        "landscape": None,
        "fault_lines": None,
        "next_steps": None,
        "dissenting_opinions": None,
        "build_this": None,
        "supplemental": None,
        "how_tribunal_works": None,
        "glossary": None,
        "inline_appendices": [],
    }

    yaml_meta, md_text = _strip_yaml_frontmatter(md_text)

    lines = md_text.split("\n")
    current_section = "header"
    current_subsection = None
    buffer = []

    def flush_buffer():
        text = "\n".join(buffer).strip()
        buffer.clear()
        return text

    def _is_heading(stripped_line, *labels):
        for label in labels:
            if stripped_line.startswith("## " + label) or stripped_line.startswith("### " + label):
                return True
            if stripped_line.lower() == label.lower():
                return True
        return False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if _is_heading(stripped, "The Question", "The Prompt"):
            if current_section == "header":
                result["header_meta"] = _parse_header_meta(flush_buffer())
            else:
                flush_buffer()
            current_section = "the_prompt"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Recommended Outcome", "Bottom Line"):
            if current_section == "the_prompt":
                result["the_prompt"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "recommended_outcome"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Summary"):
            if current_section == "the_prompt":
                result["the_prompt"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "summary_paragraph"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Key Assertions"):
            if current_section == "summary_paragraph":
                result["summary_paragraph"] = flush_buffer()
                result["recommended_outcome"] = result["summary_paragraph"]
            else:
                flush_buffer()
            current_section = "key_assertions"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Context"):
            if current_section == "key_assertions":
                result["key_assertions"] = flush_buffer()
            elif current_section == "summary_paragraph":
                result["summary_paragraph"] = flush_buffer()
                result["recommended_outcome"] = result["summary_paragraph"]
            else:
                flush_buffer()
            current_section = "context"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "The Landscape", "Landscape"):
            if current_section == "context":
                result["context"] = flush_buffer()
            elif current_section == "key_assertions":
                result["key_assertions"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "landscape"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Fault Lines", "The Fault Lines"):
            if current_section == "landscape":
                result["landscape"] = flush_buffer()
            elif current_section == "context":
                result["context"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "fault_lines"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "So What"):
            if current_section == "fault_lines":
                result["fault_lines"] = flush_buffer()
            elif current_section == "landscape":
                result["landscape"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "next_steps"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Supplemental"):
            if current_section == "next_steps":
                result["next_steps"] = flush_buffer()
            elif current_section == "fault_lines":
                result["fault_lines"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "supplemental"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Opinion of the Court", "Majority Opinion"):
            if current_section == "recommended_outcome":
                result["recommended_outcome"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "majority_opinion"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "How We Got Here"):
            if current_section == "majority_opinion":
                result["majority_opinion"] = flush_buffer()
            elif current_section == "recommended_outcome":
                result["recommended_outcome"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "how_we_got_here"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Council Performance", "Scorecard"):
            if current_subsection == "key_moments":
                moments_text = flush_buffer()
                result["key_moments"] = _parse_bullets(moments_text)
            else:
                flush_buffer()
            current_section = "how_we_got_here"
            current_subsection = "council_performance"
            i += 1
            continue

        if _is_heading(stripped, "Convergence Assessment"):
            if current_subsection == "council_performance":
                perf_text = flush_buffer()
                advocates = _parse_council_subsections(perf_text)
                if advocates:
                    result["council_performance"] = advocates
                else:
                    result["council_performance"] = _parse_table(perf_text.split("\n"))
            else:
                flush_buffer()
            current_section = "how_we_got_here"
            current_subsection = "convergence_assessment"
            i += 1
            continue

        if _is_heading(stripped, "Key Moments"):
            if current_subsection == "convergence_assessment":
                result["convergence_assessment"] = flush_buffer()
            elif current_subsection == "council_performance":
                perf_text = flush_buffer()
                advocates = _parse_council_subsections(perf_text)
                if advocates:
                    result["council_performance"] = advocates
                else:
                    result["council_performance"] = _parse_table(perf_text.split("\n"))
            else:
                flush_buffer()
            current_section = "how_we_got_here"
            current_subsection = "key_moments"
            i += 1
            continue

        if _is_heading(stripped, "Next Steps"):
            if current_subsection == "key_moments":
                moments_text = flush_buffer()
                result["key_moments"] = _parse_bullets(moments_text)
            elif current_subsection == "convergence_assessment":
                result["convergence_assessment"] = flush_buffer()
            elif current_subsection == "council_performance":
                perf_text = flush_buffer()
                advocates = _parse_council_subsections(perf_text)
                if advocates:
                    result["council_performance"] = advocates
                else:
                    result["council_performance"] = _parse_table(perf_text.split("\n"))
            else:
                flush_buffer()
            current_section = "next_steps"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Dissenting Opinions"):
            if current_section == "next_steps":
                result["next_steps"] = flush_buffer()
            elif current_subsection == "key_moments":
                moments_text = flush_buffer()
                result["key_moments"] = _parse_bullets(moments_text)
            else:
                flush_buffer()
            current_section = "dissenting_opinions"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Build This"):
            if current_section == "next_steps":
                result["next_steps"] = flush_buffer()
            elif current_subsection == "key_moments":
                moments_text = flush_buffer()
                result["key_moments"] = _parse_bullets(moments_text)
            elif current_section == "dissenting_opinions":
                result["dissenting_opinions"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "build_this"
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "How The Tribunal Works", "How the Tribunal Works"):
            if current_section == "next_steps":
                result["next_steps"] = flush_buffer()
            elif current_section == "build_this":
                result["build_this"] = flush_buffer()
            elif current_subsection == "key_moments":
                moments_text = flush_buffer()
                result["key_moments"] = _parse_bullets(moments_text)
            elif current_section == "dissenting_opinions":
                result["dissenting_opinions"] = flush_buffer()
            else:
                flush_buffer()
            current_section = "how_tribunal_works"
            current_subsection = None
            i += 1
            continue

        appendix_match = re.match(r'^(?:#{1,3}\s+)?Appendix\s+([A-Z]):\s*(.*)', stripped)
        if appendix_match:
            appendix_title = appendix_match.group(2).strip()
            if current_section == "how_tribunal_works":
                result["how_tribunal_works"] = flush_buffer()
            elif current_section == "build_this":
                result["build_this"] = flush_buffer()
            elif current_section == "inline_appendix":
                result["inline_appendices"].append((_current_appendix_title, flush_buffer()))
            elif current_section == "glossary":
                result["glossary"] = flush_buffer()
            else:
                flush_buffer()
            if "glossary" in appendix_title.lower():
                current_section = "glossary"
            else:
                current_section = "inline_appendix"
                _current_appendix_title = appendix_title
            current_subsection = None
            i += 1
            continue

        if _is_heading(stripped, "Glossary"):
            if current_section == "how_tribunal_works":
                result["how_tribunal_works"] = flush_buffer()
            elif current_section == "build_this":
                result["build_this"] = flush_buffer()
            elif current_section == "inline_appendix":
                result["inline_appendices"].append((_current_appendix_title, flush_buffer()))
            else:
                flush_buffer()
            current_section = "glossary"
            current_subsection = None
            i += 1
            continue

        if stripped == "---" and current_section == "header":
            i += 1
            continue

        buffer.append(line)
        i += 1

    # Flush remaining buffer
    remaining = flush_buffer()
    if current_section == "glossary":
        result["glossary"] = remaining
    elif current_section == "inline_appendix":
        result["inline_appendices"].append((_current_appendix_title, remaining))
    elif current_section == "how_tribunal_works":
        result["how_tribunal_works"] = remaining
    elif current_section == "next_steps":
        result["next_steps"] = remaining
    elif current_section == "build_this":
        result["build_this"] = remaining
    elif current_section == "supplemental":
        result["supplemental"] = remaining
    elif current_section == "dissenting_opinions":
        result["dissenting_opinions"] = remaining
    elif current_section == "majority_opinion":
        result["majority_opinion"] = remaining
    elif current_section == "summary_paragraph":
        result["summary_paragraph"] = remaining
        result["recommended_outcome"] = remaining
    elif current_section == "key_assertions":
        result["key_assertions"] = remaining
    elif current_section == "context":
        result["context"] = remaining
    elif current_section == "landscape":
        result["landscape"] = remaining
    elif current_section == "fault_lines":
        result["fault_lines"] = remaining
    elif current_subsection == "convergence_assessment":
        result["convergence_assessment"] = remaining
    elif current_subsection == "key_moments":
        result["key_moments"] = _parse_bullets(remaining)
    elif current_subsection == "council_performance":
        advocates = _parse_council_subsections(remaining)
        if advocates:
            result["council_performance"] = advocates
        else:
            result["council_performance"] = _parse_table(remaining.split("\n"))
    elif current_section == "the_prompt":
        result["the_prompt"] = remaining
    elif current_section == "recommended_outcome":
        result["recommended_outcome"] = remaining

    if yaml_meta:
        for k, v in yaml_meta.items():
            if k not in result["header_meta"]:
                result["header_meta"][k] = v

    return result


def _parse_bullets(text):
    bullets = []
    current = []
    for line in text.split("\n"):
        stripped = line.strip()
        is_bullet = stripped.startswith("- ") or stripped.startswith("* ")
        numbered_match = re.match(r'^(\d+)\.\s+', stripped) if not is_bullet else None
        if is_bullet:
            if current:
                bullets.append(" ".join(current))
            current = [stripped[2:]]
        elif numbered_match:
            if current:
                bullets.append(" ".join(current))
            current = [stripped[numbered_match.end():]]
        elif stripped and current:
            current.append(stripped)
        elif not stripped and current:
            bullets.append(" ".join(current))
            current = []
    if current:
        bullets.append(" ".join(current))
    return bullets


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------

def make_data_table(headers, rows, col_widths=None, styles_dict=None):
    s = styles_dict
    header_cells = [Paragraph(_escape_xml(h), s['table_header']) for h in headers]
    data_rows = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            if idx == 0:
                cells.append(Paragraph(_md_inline_to_xml(cell), s['table_cell_bold']))
            else:
                cells.append(Paragraph(_md_inline_to_xml(cell), s['table_cell']))
        data_rows.append(cells)

    table_data = [header_cells] + data_rows
    if col_widths is None:
        n_cols = len(headers)
        col_widths = [CONTENT_W / n_cols] * n_cols if n_cols > 0 else [CONTENT_W]

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), DARK_GRAY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
        ('FONTNAME', (0, 0), (-1, 0), 'Times-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, BLACK),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, BLACK),
    ]
    for idx in range(2, len(table_data), 2):
        style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), TABLE_ALT))
    t.setStyle(TableStyle(style_cmds))
    return t


# ---------------------------------------------------------------------------
# Markdown content renderer
# ---------------------------------------------------------------------------

def _render_markdown_content(md_text, styles):
    flowables = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            flowables.append(ThinRule(CONTENT_W))
            i += 1
            continue

        if stripped.startswith("#### "):
            flowables.append(Paragraph(_md_inline_to_xml(stripped.lstrip("#").strip()), styles['h3']))
            i += 1
            continue
        if stripped.startswith("### "):
            flowables.append(Paragraph(_md_inline_to_xml(stripped[4:]), styles['h3']))
            i += 1
            continue
        if stripped.startswith("## "):
            flowables.append(Paragraph(_md_inline_to_xml(stripped[3:]), styles['h2']))
            i += 1
            continue
        if stripped.startswith("# "):
            i += 1
            continue

        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            headers, rows = _parse_table(table_lines)
            if headers and rows:
                n_cols = len(headers)
                col_widths = [CONTENT_W / n_cols] * n_cols
                flowables.append(make_data_table(headers, rows, col_widths=col_widths, styles_dict=styles))
                flowables.append(Spacer(1, 4))
            continue

        if stripped.startswith("- ") or (stripped.startswith("* ") and not stripped.startswith("**")):
            bullet_items = []
            while i < len(lines):
                raw = lines[i]
                s = raw.strip()
                leading_spaces = len(raw) - len(raw.lstrip())
                is_bullet = s.startswith("- ") or (s.startswith("* ") and not s.startswith("**"))
                if is_bullet:
                    indent_level = 1 if leading_spaces >= 2 else 0
                    bullet_items.append((indent_level, s[2:]))
                    i += 1
                elif s and bullet_items and not s.startswith("#") and not s.startswith("|"):
                    prev_level, prev_text = bullet_items[-1]
                    bullet_items[-1] = (prev_level, prev_text + " " + s)
                    i += 1
                else:
                    break
            for level, bl in bullet_items:
                if level >= 1:
                    flowables.append(Paragraph(
                        "<bullet>&#8211;</bullet> " + _md_inline_to_xml(bl), styles['sub_bullet']))
                else:
                    flowables.append(Paragraph(
                        "<bullet>&bull;</bullet> " + _md_inline_to_xml(bl), styles['bullet']))
            continue

        num_match = re.match(r'^(\d+)\.[\s]+', stripped)
        if num_match:
            numbered_items = []
            while i < len(lines):
                raw = lines[i]
                s = raw.strip()
                nm = re.match(r'^(\d+)\.[\s]+', s)
                leading_spaces = len(raw) - len(raw.lstrip())
                if nm:
                    indent_level = 1 if leading_spaces >= 2 else 0
                    numbered_items.append((indent_level, nm.group(1), s[nm.end():]))
                    i += 1
                elif s and numbered_items and not s.startswith("#") and not s.startswith("|"):
                    prev_level, prev_num, prev_text = numbered_items[-1]
                    numbered_items[-1] = (prev_level, prev_num, prev_text + " " + s)
                    i += 1
                else:
                    break
            for level, num, text in numbered_items:
                style = styles['sub_bullet'] if level >= 1 else styles['bullet']
                flowables.append(Paragraph(
                    "<bullet>%s.</bullet> " % _escape_xml(num) + _md_inline_to_xml(text), style))
            continue

        if stripped.startswith(">"):
            bq_parts = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                bq_parts.append(lines[i].strip().lstrip("> ").strip())
                i += 1
            flowables.append(Paragraph(_md_inline_to_xml(" ".join(bq_parts)), styles['quote']))
            continue

        para_parts = []
        while i < len(lines):
            s = lines[i].strip()
            if not s or s.startswith("#") or s.startswith("|") or s.startswith(">"):
                break
            if s.startswith("- ") or s.startswith("---"):
                break
            if s.startswith("* ") and not s.startswith("**"):
                break
            if re.match(r'^\d+\.\s+', s):
                break
            para_parts.append(s)
            i += 1
        if para_parts:
            flowables.append(Paragraph(_md_inline_to_xml(" ".join(para_parts)), styles['body']))

    return flowables


# ---------------------------------------------------------------------------
# Story builder — simple, linear, no cover page or TOC
# ---------------------------------------------------------------------------

def _build_story(parsed, styles, appendices=None, briefing_name=None):
    story = []
    meta = parsed["header_meta"]
    is_briefing_format = bool(parsed.get("summary_paragraph") or parsed.get("key_assertions"))

    # Title — "Executive Briefing — {name}" or just "Executive Briefing"
    if briefing_name:
        title = "Executive Briefing — %s" % briefing_name
    else:
        title = "Executive Briefing"
    story.append(Paragraph(_escape_xml(title), styles['title']))
    story.append(ThinRule(CONTENT_W, RULE_GRAY, 0.5))
    story.append(Spacer(1, 6))

    if is_briefing_format:
        # Summary
        if parsed.get("summary_paragraph"):
            story.append(Paragraph("Summary", styles['h1']))
            story.extend(_render_markdown_content(parsed["summary_paragraph"], styles))

        # Key Assertions
        if parsed.get("key_assertions"):
            story.append(Paragraph("Key Assertions", styles['h1']))
            story.extend(_render_markdown_content(parsed["key_assertions"], styles))

        # Context
        if parsed.get("context"):
            story.append(Paragraph("Context", styles['h1']))
            story.extend(_render_markdown_content(parsed["context"], styles))

        # The Landscape
        if parsed.get("landscape"):
            story.append(Paragraph("The Landscape", styles['h1']))
            story.extend(_render_markdown_content(parsed["landscape"], styles))

        # Fault Lines
        if parsed.get("fault_lines"):
            story.append(Paragraph("Fault Lines", styles['h1']))
            story.extend(_render_markdown_content(parsed["fault_lines"], styles))

        # So What
        if parsed.get("next_steps"):
            story.append(Paragraph("So What", styles['h1']))
            story.extend(_render_markdown_content(parsed["next_steps"], styles))

        # Supplemental
        if parsed.get("supplemental"):
            story.append(Paragraph("Supplemental", styles['h1']))
            story.extend(_render_markdown_content(parsed["supplemental"], styles))

    else:
        # Legacy format
        if parsed["recommended_outcome"]:
            story.append(Paragraph("Bottom Line", styles['h1']))
            story.extend(_render_markdown_content(parsed["recommended_outcome"], styles))

        if parsed.get("majority_opinion"):
            story.append(Paragraph("Majority Opinion", styles['h1']))
            story.extend(_render_markdown_content(parsed["majority_opinion"], styles))

        if parsed.get("next_steps"):
            story.append(Paragraph("Next Steps", styles['h1']))
            story.extend(_render_markdown_content(parsed["next_steps"], styles))

        if parsed.get("dissenting_opinions"):
            story.append(Paragraph("Dissenting Opinions", styles['h1']))
            story.extend(_render_markdown_content(parsed["dissenting_opinions"], styles))

    return story


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_summary_pdf(md_path, output_path=None, briefing_name=None):
    md_path = str(md_path)
    md_text = Path(md_path).read_text(encoding="utf-8")
    parsed = parse_session_summary(md_text)

    if output_path is None:
        output_path = str(Path(md_path).with_suffix(".pdf"))

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        title="Executive Briefing",
        author="Tribunal",
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
    )

    styles = build_styles()
    story = _build_story(parsed, styles, briefing_name=briefing_name)
    doc.build(story)
    return os.path.abspath(output_path)


def main():
    if len(sys.argv) < 2:
        print("Usage: python summary_pdf.py <session-summary.md> [output.pdf]")
        sys.exit(1)

    md_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(md_path):
        print("Error: file not found: %s" % md_path)
        sys.exit(1)

    pdf_path = generate_summary_pdf(md_path, output_path)
    print("PDF generated: %s" % pdf_path)


if __name__ == "__main__":
    main()
