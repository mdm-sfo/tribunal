#!/usr/bin/env python3
"""
The Tribunal — Executive Brief (1–2 page PDF).

Reads a session-summary.md and distills it into a structured, scannable
executive brief. Designed for a smart, busy reader — think Goldman research
flash note or McKinsey one-pager. Heavy use of bullets, bold lead-ins,
no paragraph walls.

Page 1: Question, Ruling, Key Evidence, What Was Rejected
Page 2: Open Questions / Risks, Panel Snapshot, Process Metadata

Standalone usage:
    python exec_brief_pdf.py /path/to/session-summary.md [output.pdf]

Programmatic usage:
    from exec_brief_pdf import generate_exec_brief
    pdf_path = generate_exec_brief("/path/to/session-summary.md")

Requires: reportlab>=4.0
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle,
    Frame, PageTemplate, BaseDocTemplate, Flowable, KeepTogether,
)

# Re-use the session-summary parser from the full PDF generator
from summary_pdf import (
    parse_session_summary, _md_inline_to_xml, _escape_xml,
    _parse_table, _parse_bullets,
)

# ---------------------------------------------------------------------------
# Color palette — same academic aesthetic, tighter spacing
# ---------------------------------------------------------------------------
NAVY       = HexColor("#0D1B2A")
DARK_BLUE  = HexColor("#1B2A4A")
MED_BLUE   = HexColor("#2C3E6B")
ACCENT     = HexColor("#8B1A1A")   # Deep crimson
ACCENT2    = HexColor("#B8860B")   # Dark goldenrod
LIGHT_GRAY = HexColor("#F5F5F0")
MED_GRAY   = HexColor("#E8E6E0")
DARK_GRAY  = HexColor("#4A4A4A")
TEXT_COLOR  = HexColor("#1A1A1A")
MUTED      = HexColor("#6B6B6B")
TABLE_HEAD = HexColor("#1B2A4A")
TABLE_ALT  = HexColor("#F0EDE6")
RULE_COLOR = HexColor("#C0B8A8")
WHITE      = HexColor("#FFFFFF")

PAGE_W, PAGE_H = letter
LEFT_MARGIN = 0.75 * inch
RIGHT_MARGIN = 0.75 * inch
TOP_MARGIN = 0.65 * inch
BOTTOM_MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN


# ---------------------------------------------------------------------------
# Custom flowables
# ---------------------------------------------------------------------------

class ThinRule(Flowable):
    def __init__(self, width, color=RULE_COLOR, thickness=0.5):
        Flowable.__init__(self)
        self.width = width
        self.color = color
        self.thickness = thickness
        self.height = 4

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 2, self.width, 2)


class AccentBar(Flowable):
    def __init__(self, width=30, color=ACCENT, thickness=2.5):
        Flowable.__init__(self)
        self.width = width
        self.color = color
        self.thickness = thickness
        self.height = 5

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 2.5, self.width, 2.5)


class RulingBox(Flowable):
    """Highlighted ruling box with left accent bar — the money quote."""
    def __init__(self, text, width, style, bg_color=LIGHT_GRAY, accent_color=ACCENT):
        Flowable.__init__(self)
        self.text = text
        self.box_width = width
        self.style = style
        self.bg_color = bg_color
        self.accent_color = accent_color
        p = Paragraph(text, style)
        w, h = p.wrap(width - 24, 1000)
        self.box_height = h + 16

    def wrap(self, availWidth, availHeight):
        return self.box_width, self.box_height

    def draw(self):
        self.canv.setFillColor(self.bg_color)
        self.canv.roundRect(0, 0, self.box_width, self.box_height, 3, fill=1, stroke=0)
        self.canv.setFillColor(self.accent_color)
        self.canv.rect(0, 0, 4, self.box_height, fill=1, stroke=0)
        p = Paragraph(self.text, self.style)
        p.wrap(self.box_width - 24, self.box_height)
        p.drawOn(self.canv, 16, 8)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def build_styles():
    s = {}

    s['title'] = ParagraphStyle(
        'BriefTitle', fontName='Helvetica-Bold', fontSize=15, leading=18,
        textColor=NAVY, spaceAfter=1, alignment=TA_LEFT,
    )
    s['subtitle'] = ParagraphStyle(
        'BriefSubtitle', fontName='Helvetica', fontSize=8, leading=10,
        textColor=MUTED, spaceAfter=0, alignment=TA_LEFT,
    )
    s['section'] = ParagraphStyle(
        'BriefSection', fontName='Helvetica-Bold', fontSize=10.5, leading=13,
        textColor=NAVY, spaceBefore=10, spaceAfter=3, alignment=TA_LEFT,
    )
    s['subsection'] = ParagraphStyle(
        'BriefSubsection', fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=DARK_BLUE, spaceBefore=6, spaceAfter=2, alignment=TA_LEFT,
    )
    s['body'] = ParagraphStyle(
        'BriefBody', fontName='Helvetica', fontSize=8.5, leading=12,
        textColor=TEXT_COLOR, spaceAfter=4, alignment=TA_JUSTIFY,
    )
    s['ruling'] = ParagraphStyle(
        'BriefRuling', fontName='Helvetica', fontSize=9, leading=13,
        textColor=TEXT_COLOR, spaceAfter=0, alignment=TA_LEFT,
    )
    s['bullet'] = ParagraphStyle(
        'BriefBullet', fontName='Helvetica', fontSize=8.5, leading=12,
        textColor=TEXT_COLOR, spaceAfter=3, leftIndent=14, bulletIndent=2,
        alignment=TA_LEFT,
    )
    s['numbered'] = ParagraphStyle(
        'BriefNumbered', fontName='Helvetica', fontSize=8.5, leading=12,
        textColor=TEXT_COLOR, spaceAfter=3, leftIndent=18, bulletIndent=2,
        alignment=TA_LEFT,
    )
    s['meta'] = ParagraphStyle(
        'BriefMeta', fontName='Helvetica', fontSize=7.5, leading=10,
        textColor=MUTED, spaceAfter=1, alignment=TA_LEFT,
    )
    s['question'] = ParagraphStyle(
        'BriefQuestion', fontName='Helvetica-Oblique', fontSize=9, leading=13,
        textColor=DARK_GRAY, spaceAfter=4, alignment=TA_LEFT,
    )
    s['table_header'] = ParagraphStyle(
        'BriefTH', fontName='Helvetica-Bold', fontSize=7.5, leading=10,
        textColor=WHITE, alignment=TA_LEFT,
    )
    s['table_cell'] = ParagraphStyle(
        'BriefTC', fontName='Helvetica', fontSize=7.5, leading=10,
        textColor=TEXT_COLOR, alignment=TA_LEFT,
    )
    s['table_cell_bold'] = ParagraphStyle(
        'BriefTCB', fontName='Helvetica-Bold', fontSize=7.5, leading=10,
        textColor=TEXT_COLOR, alignment=TA_LEFT,
    )
    s['dissent_label'] = ParagraphStyle(
        'BriefDissentLabel', fontName='Helvetica-Bold', fontSize=8, leading=11,
        textColor=ACCENT, spaceAfter=1, alignment=TA_LEFT,
    )
    s['footer_note'] = ParagraphStyle(
        'BriefFooterNote', fontName='Helvetica-Oblique', fontSize=7, leading=9,
        textColor=MUTED, spaceAfter=0, alignment=TA_LEFT,
    )

    return s


# ---------------------------------------------------------------------------
# Document template
# ---------------------------------------------------------------------------

class ExecBriefTemplate(BaseDocTemplate):
    """Executive brief with branded header/footer."""

    def __init__(self, filename, session_meta=None, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)
        self.session_meta = session_meta or {}
        self._saved_page_count = 0

        frame = Frame(
            LEFT_MARGIN, BOTTOM_MARGIN,
            CONTENT_W, PAGE_H - TOP_MARGIN - BOTTOM_MARGIN,
            id='brief',
        )
        template = PageTemplate(id='Brief', frames=[frame], onPage=self._draw_page)
        self.addPageTemplates([template])

    def _draw_page(self, canvas, doc):
        canvas.saveState()
        self._saved_page_count = max(self._saved_page_count, canvas.getPageNumber())

        # Top double rule
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(1.5)
        canvas.line(LEFT_MARGIN, PAGE_H - TOP_MARGIN + 10,
                    PAGE_W - RIGHT_MARGIN, PAGE_H - TOP_MARGIN + 10)
        canvas.setStrokeColor(RULE_COLOR)
        canvas.setLineWidth(0.4)
        canvas.line(LEFT_MARGIN, PAGE_H - TOP_MARGIN + 7,
                    PAGE_W - RIGHT_MARGIN, PAGE_H - TOP_MARGIN + 7)

        # Header text
        canvas.setFont('Helvetica-Bold', 6.5)
        canvas.setFillColor(NAVY)
        canvas.drawString(LEFT_MARGIN, PAGE_H - TOP_MARGIN + 14,
                          "EXECUTIVE BRIEF")

        session_id = self.session_meta.get("session_id", "")
        if session_id:
            canvas.setFont('Helvetica', 6)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(PAGE_W - RIGHT_MARGIN, PAGE_H - TOP_MARGIN + 14,
                                   session_id)

        # Bottom rule
        canvas.setStrokeColor(RULE_COLOR)
        canvas.setLineWidth(0.4)
        canvas.line(LEFT_MARGIN, BOTTOM_MARGIN - 6,
                    PAGE_W - RIGHT_MARGIN, BOTTOM_MARGIN - 6)

        # Page number
        page_num = canvas.getPageNumber()
        canvas.setFont('Helvetica', 6.5)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE_W / 2, BOTTOM_MARGIN - 16,
                                 "Page %d" % page_num)

        # Attribution
        canvas.setFont('Helvetica', 5.5)
        canvas.setFillColor(HexColor("#999999"))
        canvas.drawRightString(PAGE_W - RIGHT_MARGIN, BOTTOM_MARGIN - 16,
                               "The Tribunal \u2014 github.com/mdm-sfo/tribunal")

        canvas.restoreState()


# ---------------------------------------------------------------------------
# Content extraction helpers
# ---------------------------------------------------------------------------

def _extract_ruling_line(bottom_line_text):
    """Pull just the **Ruling:** sentence(s) — the money quote."""
    if not bottom_line_text:
        return ""
    for line in bottom_line_text.split("\n"):
        stripped = line.strip()
        if "Ruling" in stripped[:20] and stripped.startswith("**"):
            # Strip all variants: **Ruling:** , **Ruling**: , **Ruling**:
            result = re.sub(r'^\*{1,2}Ruling\*{0,2}:?\s*\*{0,2}:?\s*', '', stripped).strip()
            # Clean any remaining leading asterisks or colons
            result = result.lstrip("*: ").strip()
            return result
    # Fallback: first sentence
    first_para = bottom_line_text.strip().split("\n\n")[0]
    return first_para.replace("\n", " ").strip()


_MODEL_RE = (
    r"(?:Perplexity Sonar Pro|GPT-5|GPT-OSS 120B|Gemini 2\.5 Pro|Claude Sonnet|"
    r"DeepSeek (?:V3|R1)|Qwen 3(?:\.5)?\s*(?:235B|397B)?(?:\s*\([^)]*\))?|"
    r"Zhipu GLM-[\d.]+|Kimi K2(?:\s+Instruct)?|MiniMax M[\d.]+|"
    r"Essential AI RNJ-1|Advocate-[A-Z]|Judge-[A-Z])"
)


def _strip_model_attribution(text):
    """Aggressively remove all model names, vote counts, and Tribunal process language.

    The brief should read as pure analysis — no reader needs to know which AI said what.
    """
    if not text:
        return text

    # Remove any parenthetical containing model names or process attribution
    text = re.sub(r'\s*\([^)]*(?:supported by|consensus across|accepted by)[^)]*\)', '', text, flags=re.I)
    # Remove parentheticals that are just model name lists
    text = re.sub(r'\s*\(' + _MODEL_RE + r'(?:[,\s]+(?:and\s+)?' + _MODEL_RE + r')*\s*\)', '', text)

    # Remove "From ModelName[, ModelName...][)]:" prefixes
    text = re.sub(r'From\s+' + _MODEL_RE + r'(?:[,\s]+(?:and\s+)?' + _MODEL_RE + r')*\s*\)?\s*:\s*', '', text)

    # Remove "ModelName argues/emphasizes/notes that" sentence openers
    text = re.sub(
        _MODEL_RE + r'\s+(?:argues?|emphasizes?|notes?|contends?|claims?|maintains?)\s+that\s+',
        '', text, flags=re.I,
    )

    # Remove "ModelName's thesis/position" possessives
    text = re.sub(
        _MODEL_RE + r"'s\s+(?:thesis|position|model|power infrastructure arbitrage thesis)",
        "the thesis", text, flags=re.I,
    )

    # Remove ", as conceded by all advocates" type trailing clauses
    text = re.sub(r',?\s*as conceded by\s+(?:all\s+)?(?:advocates?|' + _MODEL_RE + r')', '', text, flags=re.I)

    # Filter vote-count and process-focused lines
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if re.search(r'judges?\s+accepted|majority\s+accept|vote\s+in\s+favor|\d\s+out\s+of\s+\d', stripped, re.I):
            continue
        if re.search(r'no\s+judge\s+accepted|only\s+\w+\s+received\s+majority', stripped, re.I):
            continue
        if re.search(r'position\s+across\s+the\s+Bench|majority-supported\s+foundation', stripped, re.I):
            continue
        lines.append(line)
    text = "\n".join(lines)

    # Final pass: scrub any remaining standalone model name references
    text = re.sub(_MODEL_RE + r"'s\s+", '', text)  # possessives
    text = re.sub(r',?\s*' + _MODEL_RE + r'(?:\s*,\s*' + _MODEL_RE + r')*\s*\)?', '', text)

    # Cleanup artifacts: empty parens, dangling commas/colons, double spaces
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r',\s*\)', ')', text)
    text = re.sub(r'\(\s*,', '(', text)
    text = re.sub(r',\s*:', ':', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s+([.,;:])', r'\1', text)
    text = re.sub(r'^\s*,\s*', '', text)
    # Capitalize first letter after stripping a sentence opener
    text = re.sub(r'^([a-z])', lambda m: m.group(1).upper(), text)

    return text.strip()


def _extract_numbered_points(text, max_points=5):
    """Extract numbered or bulleted points from markdown text."""
    points = []
    for line in text.strip().split("\n"):
        stripped = line.strip()
        m = re.match(r'^(?:\d+\.\s+|\-\s+|\*\s+)(.*)', stripped)
        if m:
            points.append(m.group(1).strip())
    return points[:max_points]


def _extract_rejected_items(bottom_line_text):
    """Extract 'this synthesis does not adopt' items from the bottom line."""
    items = []
    in_rejected = False
    for line in bottom_line_text.split("\n"):
        stripped = line.strip()
        if "does not adopt" in stripped.lower() or "rejected" in stripped.lower():
            in_rejected = True
            continue
        if in_rejected and stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif in_rejected and stripped.startswith("Instead"):
            break
        elif in_rejected and not stripped:
            if items:
                break
    return items


def _extract_key_evidence(bottom_line_text, max_points=4):
    """Extract the key synthesis elements (numbered points) from bottom line.

    Strips model attribution. Extracts the concept name from the text if the
    label is just a model attribution.
    """
    points = []
    for line in bottom_line_text.split("\n"):
        stripped = line.strip()
        m = re.match(r'^\d+\.\s+\*\*(.+?)\*\*[:\s]*(.*)', stripped)
        if m:
            label = m.group(1).strip()
            rest = m.group(2).strip()

            # Strip model attribution from label
            label = _strip_model_attribution(label).strip()

            # If label starts with "From" or is now empty, extract concept from content
            if not label or label.lower().startswith("from") or len(label) < 5:
                concept_m = re.search(r'\*\*(.+?)\*\*', rest)
                if concept_m:
                    label = concept_m.group(1).strip()
                else:
                    label = "Key element %d" % (len(points) + 1)

            # Capitalize first letter
            if label and label[0].islower():
                label = label[0].upper() + label[1:]

            # Strip model attribution from rest text
            rest = _strip_model_attribution(rest)

            if len(rest) > 280:
                last_period = rest[:280].rfind(". ")
                if last_period > 140:
                    rest = rest[:last_period + 1]
                else:
                    rest = rest[:277] + "..."
            points.append(("<b>%s:</b> %s" % (_escape_xml(label), _md_inline_to_xml(rest))))
    return points[:max_points]


def _make_compact_table(headers, rows, col_widths, styles):
    """Build a compact styled table."""
    header_cells = [Paragraph(_escape_xml(h), styles['table_header']) for h in headers]
    data_rows = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            style = styles['table_cell_bold'] if idx == 0 else styles['table_cell']
            cells.append(Paragraph(_md_inline_to_xml(cell), style))
        data_rows.append(cells)

    table_data = [header_cells] + data_rows
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEAD),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, 0), 5),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, 0), 1, NAVY),
        ('LINEBELOW', (0, -1), (-1, -1), 0.75, NAVY),
        ('LINEBELOW', (0, 1), (-1, -2), 0.3, RULE_COLOR),
    ]
    for idx in range(1, len(table_data)):
        if idx % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), TABLE_ALT))
    t.setStyle(TableStyle(style_cmds))
    return t


# ---------------------------------------------------------------------------
# Story builder
# ---------------------------------------------------------------------------

def _extract_analysis_paragraphs(bottom_line_text, max_paras=2):
    """Extract the analytical 'why' paragraphs from the bottom line.

    Skips the Ruling: line, process/vote-count paragraphs, and rejection lists.
    Strips model attribution to focus on substance.
    Returns prose paragraphs that explain the reasoning.
    """
    paragraphs = bottom_line_text.strip().split("\n\n")
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Skip the ruling line
        if para.startswith("**Ruling"):
            continue
        # Skip rejection lists
        if "does not adopt" in para.lower() or "instead, the court" in para.lower():
            continue
        # Skip bare bullet lists (rejected items)
        if all(line.strip().startswith("- ") for line in para.split("\n") if line.strip()):
            continue
        # Skip numbered evidence items (these go in the evidence section)
        if re.match(r'^\d+\.\s+\*\*', para.strip()):
            continue
        # Skip paragraphs that are primarily about judicial vote counts
        if re.search(r'(?:judges?\s+accepted|majority\s+position\s+across\s+the\s+Bench|out\s+of\s+\d\))', para, re.I):
            continue
        # Skip the "The synthesis incorporates..." bridge sentence
        if para.startswith("The synthesis incorporates"):
            continue

        cleaned = _strip_model_attribution(para.replace("\n", " "))
        if cleaned and len(cleaned) > 40:
            result.append(cleaned)
        if len(result) >= max_paras:
            break
    return result


def _extract_risks_and_caveats(parsed):
    """Build a list of risks/caveats from dissents.

    Focuses on the core disagreements — what a decision-maker needs to watch.
    Strips model attribution.
    """
    risks = []

    # From dissenting opinions — the core counter-arguments
    dissent_text = parsed.get("dissenting_opinions", "")
    if dissent_text:
        for block in re.split(r'###\s+', dissent_text):
            block = block.strip()
            if not block:
                continue
            for line in block.split("\n"):
                stripped = line.strip()
                for prefix in ("**Core Disagreement:**", "**Strongest Evidence:**"):
                    if stripped.startswith(prefix):
                        text = stripped.replace(prefix, "").strip()
                        if text and len(text) > 30:
                            risks.append(_strip_model_attribution(text))

    # Deduplicate similar risks (keep first occurrence)
    seen = set()
    unique_risks = []
    for r in risks:
        # Simple dedup: check if first 50 chars are similar
        key = r[:50].lower()
        if key not in seen:
            seen.add(key)
            unique_risks.append(r)

    return unique_risks[:4]


def _build_brief_story(parsed, styles):
    """Build the executive brief story — substance over process."""
    story = []
    meta = parsed["header_meta"]

    # ==================================================================
    # HEADER
    # ==================================================================
    story.append(Paragraph("Executive Brief", styles['title']))

    meta_parts = []
    date = meta.get("date", "")
    if date:
        meta_parts.append(date)
    if meta.get("depth"):
        meta_parts.append("Depth: %s" % meta["depth"])
    if meta.get("advocates"):
        meta_parts.append("%s Advocates" % meta["advocates"])
    if meta.get("judges") or meta.get("cardinals"):
        meta_parts.append("%s Judges" % (meta.get("judges") or meta.get("cardinals")))
    if meta.get("cost"):
        meta_parts.append(meta["cost"])
    if meta.get("time"):
        meta_parts.append(meta["time"])
    if meta_parts:
        story.append(Paragraph(_escape_xml("  |  ".join(meta_parts)), styles['subtitle']))

    story.append(Spacer(1, 6))
    story.append(ThinRule(CONTENT_W, NAVY, 1))
    story.append(Spacer(1, 8))

    # ==================================================================
    # 1. THE QUESTION
    # ==================================================================
    story.append(Paragraph("The Question", styles['section']))
    story.append(AccentBar(color=MED_BLUE))
    story.append(Spacer(1, 4))

    if parsed["the_prompt"]:
        prompt_text = parsed["the_prompt"].strip().replace("\n", " ")
        if len(prompt_text) > 400:
            prompt_text = prompt_text[:397] + "..."
        story.append(Paragraph(
            "\u201c" + _escape_xml(prompt_text) + "\u201d",
            styles['question'],
        ))

    # ==================================================================
    # 2. SUMMARY / RULING
    # ==================================================================
    # Detect new executive briefing format
    is_briefing_format = bool(parsed.get("summary_paragraph") or parsed.get("key_assertions"))

    if is_briefing_format and parsed.get("summary_paragraph"):
        story.append(Paragraph("Summary", styles['section']))
        story.append(AccentBar(color=ACCENT))
        story.append(Spacer(1, 4))
        # Render the SCR summary paragraph
        summary_text = _strip_model_attribution(parsed["summary_paragraph"].strip().replace("\n", " "))
        if summary_text:
            story.append(Paragraph(_md_inline_to_xml(summary_text), styles['body']))
        story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("Ruling", styles['section']))
        story.append(AccentBar(color=ACCENT))
        story.append(Spacer(1, 4))
        ruling = _extract_ruling_line(parsed["recommended_outcome"])
        if ruling:
            story.append(RulingBox(
                _md_inline_to_xml(ruling),
                CONTENT_W, styles['ruling'], LIGHT_GRAY, ACCENT,
            ))
            story.append(Spacer(1, 6))

    # ==================================================================
    # 3. KEY ASSERTIONS (new format) or WHY (legacy format)
    # ==================================================================
    if is_briefing_format and parsed.get("key_assertions"):
        analysis_paras = []
        evidence_points = []
    else:
        analysis_paras = _extract_analysis_paragraphs(parsed["recommended_outcome"], max_paras=3)
        evidence_points = _extract_key_evidence(parsed["recommended_outcome"])

    if analysis_paras or evidence_points:
        story.append(Paragraph("Why This Is the Answer", styles['section']))
        story.append(AccentBar(color=ACCENT2))
        story.append(Spacer(1, 4))

        # Lead with the analytical prose — the reasoning and mechanisms
        for para in analysis_paras:
            story.append(Paragraph(_md_inline_to_xml(para), styles['body']))

        # Then the structured evidence points
        if evidence_points:
            story.append(Spacer(1, 2))
            for i, point in enumerate(evidence_points, 1):
                story.append(Paragraph(
                    "<bullet>%d.</bullet> %s" % (i, point),
                    styles['numbered'],
                ))
        story.append(Spacer(1, 2))

    # ==================================================================
    # 3b. KEY ASSERTIONS (new briefing format)
    # ==================================================================
    if is_briefing_format and parsed.get("key_assertions"):
        story.append(Paragraph("Key Assertions", styles['section']))
        story.append(AccentBar(color=ACCENT2))
        story.append(Spacer(1, 4))

        assertions_text = parsed["key_assertions"]
        for para in assertions_text.strip().split("\n\n"):
            para = para.strip()
            if not para:
                continue
            cleaned = _strip_model_attribution(para.replace("\n", " "))
            if cleaned and len(cleaned) > 20:
                story.append(Paragraph(_md_inline_to_xml(cleaned), styles['body']))

    # ==================================================================
    # 3c. FAULT LINES (new briefing format — replaces KEY RISKS)
    # ==================================================================
    if is_briefing_format and parsed.get("fault_lines"):
        story.append(Paragraph("Fault Lines", styles['section']))
        story.append(AccentBar(color=ACCENT))
        story.append(Spacer(1, 4))

        fl_text = parsed["fault_lines"]
        for para in fl_text.strip().split("\n\n"):
            para = para.strip()
            if not para:
                continue
            cleaned = _strip_model_attribution(para.replace("\n", " "))
            if cleaned:
                if len(cleaned) > 220:
                    last_period = cleaned[:220].rfind(". ")
                    if last_period > 110:
                        cleaned = cleaned[:last_period + 1]
                    else:
                        cleaned = cleaned[:217] + "..."
                story.append(Paragraph(_md_inline_to_xml(cleaned), styles['body']))

    # ==================================================================
    # 4. KEY RISKS & CAVEATS (legacy format only)
    # ==================================================================
    risks = _extract_risks_and_caveats(parsed) if not is_briefing_format else []
    if risks:
        story.append(Paragraph("Key Risks", styles['section']))
        story.append(AccentBar(color=ACCENT))
        story.append(Spacer(1, 4))

        for risk in risks:
            if len(risk) > 220:
                last_period = risk[:220].rfind(". ")
                if last_period > 110:
                    risk = risk[:last_period + 1]
                else:
                    risk = risk[:217] + "..."
            story.append(Paragraph(
                "<bullet>\u2022</bullet> " + _md_inline_to_xml(risk),
                styles['bullet'],
            ))
        story.append(Spacer(1, 2))

    # ==================================================================
    # 5. NEXT STEPS
    # ==================================================================
    if parsed.get("next_steps"):
        story.append(Paragraph("Next Steps", styles['section']))
        story.append(AccentBar(color=MED_BLUE))
        story.append(Spacer(1, 4))

        next_items = _extract_numbered_points(parsed["next_steps"], max_points=5)
        if next_items:
            for i, item in enumerate(next_items, 1):
                if len(item) > 200:
                    item = item[:197] + "..."
                story.append(Paragraph(
                    "<bullet>%d.</bullet> %s" % (i, _md_inline_to_xml(item)),
                    styles['numbered'],
                ))
        else:
            paras = parsed["next_steps"].strip().split("\n\n")[:3]
            for p in paras:
                p = p.strip().replace("\n", " ")
                if len(p) > 250:
                    p = p[:247] + "..."
                story.append(Paragraph(_md_inline_to_xml(p), styles['body']))

    # ==================================================================
    # 6. BUILD THIS (pointer only)
    # ==================================================================
    if parsed.get("build_this"):
        story.append(Spacer(1, 4))
        story.append(Paragraph("Build This", styles['subsection']))
        story.append(Paragraph(
            "<i>An implementation-ready prompt is included in the full session summary. "
            "See the full PDF for the complete spec.</i>",
            styles['meta'],
        ))

    # ==================================================================
    # 7. PANEL SCORECARD — compact
    # ==================================================================
    perf = parsed.get("council_performance")
    if perf:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Panel Scorecard", styles['subsection']))
        story.append(Spacer(1, 3))

        if isinstance(perf, list) and perf and isinstance(perf[0], dict):
            headers = ["#", "Model", "Final Position"]
            rows = []
            for adv in perf:
                rank = adv.get("rank", "")
                rank_display = rank if rank and rank != "-" else "\u2014"
                final = adv.get("final", adv.get("opening", ""))
                if len(final) > 100:
                    last_period = final[:100].rfind(". ")
                    if last_period > 50:
                        final = final[:last_period + 1]
                    else:
                        final = final[:97] + "..."
                rows.append([rank_display, adv.get("model", ""), final])

            col_widths = [CONTENT_W * 0.06, CONTENT_W * 0.22, CONTENT_W * 0.72]
            table = _make_compact_table(headers, rows, col_widths, styles)
            story.append(table)

        elif isinstance(perf, tuple):
            headers, rows = perf
            if headers and rows:
                n_cols = len(headers)
                col_widths = [CONTENT_W / n_cols] * n_cols
                table = _make_compact_table(headers, rows, col_widths, styles)
                story.append(table)

    # ==================================================================
    # FOOTER
    # ==================================================================
    story.append(Spacer(1, 10))
    story.append(ThinRule(CONTENT_W, RULE_COLOR, 0.4))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<i>This executive brief is auto-generated from the full Tribunal session summary. "
        "For complete deliberation records, judicial opinions, and debate transcripts, "
        "see the full session directory.</i>",
        styles['footer_note'],
    ))

    return story


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_exec_brief(md_path, output_path=None):
    """Generate a 1-2 page executive brief PDF from a session-summary.md file.

    Args:
        md_path: Path to session-summary.md.
        output_path: Where to write the PDF. Defaults to same directory,
            named *-exec-brief-*.pdf.

    Returns:
        The absolute path to the generated PDF.
    """
    md_path = str(md_path)
    md_text = Path(md_path).read_text(encoding="utf-8")
    parsed = parse_session_summary(md_text)

    if output_path is None:
        md_name = Path(md_path).stem
        brief_name = md_name.replace("session-summary", "exec-brief")
        if brief_name == md_name:
            brief_name = md_name + "-exec-brief"
        output_path = str(Path(md_path).parent / (brief_name + ".pdf"))

    meta = parsed["header_meta"]
    session_id = meta.get("session", "")

    doc = ExecBriefTemplate(
        output_path,
        session_meta={"session_id": session_id},
        pagesize=letter,
        title="Tribunal Executive Brief",
        author="The Tribunal",
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
    )

    styles = build_styles()
    story = _build_brief_story(parsed, styles)
    doc.build(story)
    return os.path.abspath(output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python exec_brief_pdf.py <session-summary.md> [output.pdf]")
        sys.exit(1)

    md_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(md_path):
        print("Error: file not found: %s" % md_path)
        sys.exit(1)

    pdf_path = generate_exec_brief(md_path, output_path)
    print("Executive brief generated: %s" % pdf_path)


if __name__ == "__main__":
    main()
