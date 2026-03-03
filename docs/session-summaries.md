# Tribunal Session Summary
**Session:** `tribunal-20260302-033400` | **Depth:** THOROUGH | **Advocates:** 4 | **Judges:** 2 | **Cost:** $1.17 | **Time:** 13m 45s
*Full logs: `conclave-sessions/conclave-20260302-033400/` | Audit trail: `final-output.md` | Narrative: `play-by-play.md`*

---

## The Question

Design a system to convert verbose, academic Tribunal AI deliberation outputs into natural, speakable screenplays with full fidelity and traceability.

---

## Recommended Outcome

Implement a post-processing script that uses a three-pass pipeline to generate a screenplay and voice-script manifest.

**Pass 1 — Extraction:** An LLM converts raw session prose into structured argument objects with source anchors (e.g., `submission-advocate-A.md#L45-L50`).

**Pass 2 — Validation:** A deterministic check using a domain-calibrated NLI ensemble (DeBERTa-v3-large-MNLI and e5-large-nli, calibrated on Tribunal-specific technical data) with a contradiction threshold of p > 0.80 from at least two models, plus exact number matching and no-new-entity rules.

**Pass 3 — Dramatization:** A constrained LLM converts only validated arguments into natural dialogue, preserving source anchors throughout.

The screenplay uses a configurable 3- or 4-act structure with a moderator introducing rounds and summarizing positions. Identity reveals occur only after the verdict. All DEFEND/CONCEDE/REVISE events and judge-cited facts are mandatory — using montage or recap modes if needed to stay within a 3,000–5,000 word budget. The output includes a `voice-script.json` with per-line source anchors, ordering rationale, and character voice assignments for TTS.

---

## How We Got Here

### Council Performance

| Rank | Model | Final Position | Note |
|------|-------|----------------|------|
| 1 | GPT-5 | Hybrid extraction + domain-calibrated NLI ensemble with coverage guarantees and audit trails | Most rigorous; added technical depth and calibration |
| 2 | Gemini 2.5 Pro | Three-pass LLM pipeline with NLI/entity validation and source anchoring | Strongest defense of core fidelity architecture |
| 3 | DeepSeek V3 | Hybrid rule-based + LLM pipeline with validation and expanded budget | Credibility weakened by unverifiable citations |
| 4 | Claude Sonnet | Adopted rivals' solutions after retracting fabricated statistics | Abandoned initial position due to evidence collapse |

### Key Moments

- **Evidence collapse (Advocate-B).** Claude Sonnet retracted fabricated statistics (e.g., "73% engagement gain") when challenged, then pivoted to a validation-heavy approach. This was the session's clearest demonstration of intellectual honesty — and of the challenge system working as designed.
- **Domain calibration breakthrough (Advocate-E).** GPT-5 introduced domain-adapted NLI calibration to address the known weakness of general NLI models on technical discourse, raising contradiction precision and reducing false positives. This became the consensus differentiator.
- **Convergence on fidelity requirements.** All advocates independently converged on mandatory inclusion of key debate events (DEFEND/CONCEDE/REVISE) and post-verdict identity reveals, ensuring fidelity and avoiding bias. Judges confirmed this was evidence-based convergence, not sycophantic drift.
- **Deterministic validation as non-negotiable.** Advocate-C's challenge exposed the need for a deterministic validation layer (NLI + entity checks) between extraction and dramatization. This was universally adopted after debate.

### Judicial Assessment

Judges found genuine epistemic convergence toward the three-pass architecture. The split between GPT-5 (ranked first for technical depth) and Gemini 2.5 Pro (ranked second for strongest defense under pressure) reflects a healthy deliberation. No sycophantic drift detected — position changes were evidence-based.

---

## Build This

> Paste the following into your AI coding agent to implement the recommended outcome.

```
Design and implement a post-processing script called `screenplay_generator.py`
that converts Tribunal session files into a natural-language screenplay and
voice-script manifest.

INPUT: A Tribunal session directory containing:
- briefing.md (the original question)
- submission-advocate-*.md (anonymized advocate submissions)
- challenge-by-advocate-*.md (cross-examination)
- debate-round-*-advocate-*.md (defend/concede/revise responses)
- judgment-*.md (judicial opinions)
- fresh-eyes-review.md (if present)
- alias-map.json (advocate identity reveals)

OUTPUT: Two files written to the session directory:
1. screenplay.md — Natural-language dramatic screenplay
2. voice-script.json — TTS-ready manifest with per-line metadata

ARCHITECTURE: Three-pass pipeline.

Pass 1 — EXTRACTION:
- Use an LLM to convert raw session prose into structured argument objects.
- Each object must include: speaker alias, claim text, evidence cited,
  reasoning type (deductive/inductive/abductive), position stability score,
  and a source anchor (e.g., "submission-advocate-a.md#L45-L50").
- Extract all DEFEND, CONCEDE, and REVISE events from debate rounds.
- Extract all judge-cited facts and verdicts.

Pass 2 — VALIDATION (deterministic, no LLM):
- For each extracted claim, run entailment checks using a domain-calibrated
  NLI ensemble: DeBERTa-v3-large-MNLI and e5-large-nli.
- Contradiction threshold: p > 0.80 from at least 2 models.
- Additional rules: exact number matching (no invented statistics),
  no-new-entity (dramatization cannot introduce claims not in source).
- Flag any claim that fails validation; it must not appear in the screenplay
  without a "[unverified]" annotation.

Pass 3 — DRAMATIZATION:
- Constrained LLM converts only validated arguments into natural dialogue.
- Source anchors preserved as HTML comments in the screenplay markdown
  (e.g., <!-- source: submission-advocate-a.md#L45-L50 -->).
- Character voices: each advocate gets a distinct speaking style;
  the moderator (narrator voice) introduces rounds and summarizes positions.

SCREENPLAY FORMAT:
- Configurable 3- or 4-act structure:
  Act 1: Opening positions (moderator introduces each advocate's hypothesis)
  Act 2: The Challenge (cross-examination highlights, sharpest exchanges)
  Act 3: The Debate (round-by-round, focusing on position shifts)
  Act 4 (optional): The Verdict (judge opinions, identity reveals)
- Identity reveals ONLY in the final act, after the verdict is read.
- ALL DEFEND/CONCEDE/REVISE events must appear (use montage or recap mode
  if the full treatment would exceed 5,000 words).
- Target: 3,000-5,000 words.

VOICE-SCRIPT.JSON FORMAT:
{
  "session_id": "tribunal-...",
  "characters": [
    {"id": "moderator", "voice": "...", "description": "..."},
    {"id": "advocate-a", "voice": "...", "description": "..."}
  ],
  "lines": [
    {
      "character": "moderator",
      "text": "...",
      "act": 1,
      "source_anchor": null,
      "ordering_rationale": "Opening narration"
    },
    {
      "character": "advocate-a",
      "text": "...",
      "act": 1,
      "source_anchor": "submission-advocate-a.md#L12-L18",
      "ordering_rationale": "First hypothesis presentation"
    }
  ]
}

REQUIREMENTS:
- Python 3.9+ compatible (use Optional[X] not X | None).
- Accept --session-dir as CLI argument.
- Use litellm for LLM calls (same as the orchestrator).
- The validation pass must be fully deterministic — no LLM involvement.
- Full auditability: every line in the screenplay traces back to source.
- TTS-suitable output: no markdown formatting in spoken lines, natural
  sentence structure, speakable numbers (e.g., "eighty percent" not "80%").
```

---
---

# Conclave Session Summary
**Session: conclave-20260302-041706 | Depth: THOROUGH | Advocates: 4 | Cardinals: 3 | Cost: $1.4848 | Time: 20m 32s**
*Full logs: `conclave-sessions/conclave-20260302-041706/` | Audit trail: `final-output.md` | Narrative: `play-by-play.md`*

---

## The Question
Design a professional LaTeX/Pandoc template for converting Markdown to PDF, with a white paper aesthetic blending legal and academic conventions—restrained, typographically rigorous, and portable for non-technical users.

---

## Recommended Outcome
Use a LuaLaTeX-based template with the article class, defaulting to Libertinus Serif for body text (with fallbacks to TeX Gyre Termes and Latin Modern) to ensure portability across TeX distributions. Set traditional indented paragraphs (1.4em indent, zero parskip) and bold serif headings for formal hierarchy. Enable microtype for superior justification and letter spacing (LuaLaTeX-only feature). Use minimalist footer-only pagination (centered page numbers) and restyle blockquotes with indentation and italic serif font. For code, use the listings package with TeX Gyre Cursor monospace fallbacks. Hyperlinks should be neutral blue without decoration. The template must compile on minimal TeX installations by gracefully degrading to Latin Modern if preferred fonts are missing. Invoke Pandoc with:  
`pandoc input.md -o output.pdf --pdf-engine=lualatex --template=template.tex --listings -V block-headings`

---

## How We Got Here

### Council Performance
| Model | Final Position | Rank | Note |
|-------|----------------|------|------|
| GPT-5 | LuaLaTeX, Libertinus default, indented paragraphs, microtype, robust fallbacks | 1 | Evidence-based, conceded errors, best portability and typographic rigor |
| Gemini 2.5 Pro | LuaLaTeX, EB Garamond primary, serif headings, indented paragraphs | 2 | Strong aesthetics but fallback code unverified, less portable |
| Claude Sonnet | XeLaTeX, Libertinus, indented paragraphs, sans headings | 3 | Fabricated evidence early, forfeited microtype advantages |
| DeepSeek V3 | Merged with Gemini's position post-concessions | - | Withdrawn as standalone |

### Key Moments
- Advocate-D (Claude Sonnet) admitted fabricating statistics (e.g., "78% of legal briefs use Computer Modern"), undermining credibility and shifting focus to evidence-based choices.
- Consensus emerged on indented paragraphs and TeX-distributed fonts after all advocates conceded that parskip spacing and system fonts violated the brief's legal/academic cues.
- Advocate-A (GPT-5) consistently cited verifiable sources (CTAN, microtype manual) to defend LuaLaTeX's microtype expansion and font fallback robustness, while others relied on unverified claims.
- Advocate-B (Gemini 2.5 Pro) revised from sans to serif headings after challenge, improving alignment with the brief but leaving fallback implementation opaque.

---

## Build This

> **To implement this, paste the following into your AI engine:**

Create a Pandoc LaTeX template for Markdown-to-PDF conversion with a white paper aesthetic. Use LuaLaTeX with the article class. Set Libertinus Serif as the primary font, falling back to TeX Gyre Termes and then Latin Modern. Use traditional indented paragraphs (1.4em indent, zero parskip) and bold serif headings. Enable microtype for font expansion and protrusion. Implement minimalist footer pagination (centered page numbers only) and restyle blockquotes with indentation and italic serif font. For code blocks, use the listings package with TeX Gyre Cursor monospace fallback. Ensure hyperlinks are blue and undecorated. The template must compile on minimal TeX installations by defaulting to Latin Modern if preferred fonts are absent. Include Pandoc integration for listings and custom blockquote styling.
