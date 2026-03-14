#!/usr/bin/env python3
"""
Regenerate a session summary as an executive briefing.

Reads the existing session record (submissions, debates, judgments, majority
opinion) and re-synthesizes it through the new SUMMARY_SYSTEM_PROMPT to produce
a clean executive briefing. Then generates PDF and exec-brief PDF.

Usage:
    python3 regenerate_briefing.py /path/to/session-dir [--output /path/to/output.md]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config_loader import BISHOPS, load_config, ModelDef
from model_client import call_model
from progress import Progress
from council_orchestrator import (
    SUMMARY_SYSTEM_PROMPT,
    _deanonymize_text,
    SessionDir,
)
from summary_pdf import _strip_yaml_frontmatter


def _select_model(estimated_tokens: int) -> ModelDef:
    # Prefer Cerebras (fast, reliable) if context fits
    for m in BISHOPS:
        if "cerebras" in m.id.lower() and m.context_window >= int(estimated_tokens * 1.2):
            return m
    for m in BISHOPS:
        if m.context_window >= int(estimated_tokens * 1.2):
            return m
    return max(BISHOPS, key=lambda m: m.context_window)


def build_record_from_session(session_dir: Path) -> str:
    """Build a condensed record focused on what matters for the briefing.

    Includes: briefing, submissions, majority opinion, judicial opinions,
    claim-evidence matrix, dissents, and identity reveals.
    Skips: full debate transcripts and challenge rounds (too large, already
    synthesized into the judicial opinions and majority opinion).
    Only includes the last 2 debate rounds if no majority opinion exists.
    """
    sdir = SessionDir(session_dir) if not isinstance(session_dir, SessionDir) else session_dir
    root = sdir.root

    parts = []

    # Briefing
    briefing_path = root / "briefing.md"
    if briefing_path.exists():
        parts.append("## BRIEFING (the original question)\n\n" + briefing_path.read_text())

    # Submissions (always include -- they're the core positions)
    sub_dir = root / "submissions"
    if sub_dir.is_dir():
        parts.append("\n\n## ADVOCATE SUBMISSIONS\n")
        for f in sorted(sub_dir.glob("submission-*.md")):
            parts.append(f"### {f.stem}\n{f.read_text()}\n\n---\n")

    # Majority opinion (the canonical synthesis -- most important for the briefing)
    judicial_dir = root / "judicial"
    has_majority = False
    if judicial_dir.is_dir():
        majority_path = judicial_dir / "majority-opinion.md"
        if majority_path.exists():
            has_majority = True
            parts.append("\n## MAJORITY OPINION (Opinion of the Court)\n")
            parts.append(majority_path.read_text())

        # Judicial opinions (include these -- they contain the fact-checking)
        judgment_files = sorted(judicial_dir.glob("judgment-*.md"))
        if judgment_files:
            parts.append("\n## JUDICIAL OPINIONS\n")
            for f in judgment_files:
                parts.append(f"### {f.stem}\n{f.read_text()}\n\n---\n")

        # Fresh eyes
        fresh_path = judicial_dir / "fresh-eyes-review.md"
        if fresh_path.exists():
            parts.append("\n## FRESH EYES REVIEW\n")
            parts.append(fresh_path.read_text())

        # Dissents
        dissent_files = sorted(judicial_dir.glob("dissent-*.md"))
        if dissent_files:
            parts.append("\n## DISSENTING OPINIONS\n")
            for f in dissent_files:
                parts.append(f"### {f.stem}\n{f.read_text()}\n\n---\n")

    # Only include last 2 debate rounds if no majority opinion exists
    delib_dir = root / "deliberation"
    if not has_majority and delib_dir.is_dir():
        debate_files = sorted(delib_dir.glob("debate-round-*.md"))
        if debate_files:
            last_files = debate_files[-min(10, len(debate_files)):]
            parts.append("\n## FINAL DEBATE ROUNDS\n")
            for f in last_files:
                parts.append(f"### {f.stem}\n{f.read_text()}\n\n---\n")

    # Claim-evidence matrix
    if delib_dir.is_dir():
        matrix_path = delib_dir / "claim-evidence-matrix.md"
        if matrix_path.exists():
            parts.append("\n## CLAIM-EVIDENCE MATRIX\n")
            parts.append(matrix_path.read_text())

    # Identity reveals
    meta_dir = root / "meta"
    if meta_dir.is_dir():
        alias_path = meta_dir / "alias-map.json"
        cardinal_path = meta_dir / "cardinal-alias-map.json"
        if alias_path.exists() or cardinal_path.exists():
            parts.append("\n## Identity Reveal\n")
            if alias_path.exists():
                alias_map = json.loads(alias_path.read_text())
                for alias, info in alias_map.items():
                    parts.append(f"- {alias} = {info['model']} ({info['provider']})")
            if cardinal_path.exists():
                cardinal_map = json.loads(cardinal_path.read_text())
                for alias, info in cardinal_map.items():
                    role = info.get('role', 'judge')
                    parts.append(f"- {alias} = {info['model']} ({info['provider']}, {role})")

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Regenerate session summary as executive briefing")
    parser.add_argument("session_dir", help="Path to session directory")
    parser.add_argument("--output", "-o", default=None, help="Output markdown path (default: auto)")
    args = parser.parse_args()

    session_dir = Path(args.session_dir).resolve()
    if not session_dir.exists():
        print(f"Error: {session_dir} does not exist")
        sys.exit(1)

    session_id = session_dir.name
    progress = Progress(session_id, "regen")

    print(f"Reading session record from: {session_dir}")
    full_record = build_record_from_session(session_dir)

    estimated_tokens = len(full_record) // 4
    model = _select_model(estimated_tokens)
    print(f"Using model: {model.display_name} (est. {estimated_tokens} tokens input)")

    summary_prompt = (
        f"Produce the executive briefing for this research record.\n\n"
        f"{full_record}"
    )

    print(f"Calling {model.display_name}...")
    resp = call_model(
        model=model,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=summary_prompt,
        alias="Briefing-Synthesizer",
        timeout=300,
        temperature=0.3,
        max_tokens=8192,
        progress=progress,
    )

    if resp.status != "success":
        print(f"Error: LLM call failed: {resp.error}")
        sys.exit(1)

    content_len = len(resp.content) if resp.content else 0
    print(f"Briefing generated: {resp.output_tokens} tokens, {resp.elapsed:.1f}s, {content_len} chars")

    if content_len == 0:
        print("WARNING: LLM returned empty content. Retrying with fallback model...")
        # Try with first Bishop (Cerebras Qwen) explicitly
        fallback = BISHOPS[0]
        print(f"Retrying with: {fallback.display_name}")
        resp = call_model(
            model=fallback,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            user_prompt=summary_prompt,
            alias="Briefing-Retry",
            timeout=300,
            temperature=0.3,
            max_tokens=8192,
            progress=progress,
        )
        content_len = len(resp.content) if resp.content else 0
        print(f"Retry: {resp.output_tokens} tokens, {resp.elapsed:.1f}s, {content_len} chars")
        if resp.status != "success" or content_len == 0:
            print(f"Retry also failed: {resp.error}")
            sys.exit(1)

    # Load identity maps for de-anonymization
    alias_map = {}
    cardinal_alias_map = {}
    meta_dir = session_dir / "meta"
    if (meta_dir / "alias-map.json").exists():
        alias_map = json.loads((meta_dir / "alias-map.json").read_text())
    if (meta_dir / "cardinal-alias-map.json").exists():
        cardinal_alias_map = json.loads((meta_dir / "cardinal-alias-map.json").read_text())

    # Read existing session-summary for frontmatter metadata
    existing_summaries = sorted(session_dir.glob("*-session-summary-*.md")) + \
                         ([session_dir / "session-summary.md"] if (session_dir / "session-summary.md").exists() else [])
    existing_meta = {}
    if existing_summaries:
        existing_text = existing_summaries[0].read_text()
        yaml_meta, _ = _strip_yaml_frontmatter(existing_text)
        existing_meta = yaml_meta

    # Build frontmatter
    date_str = existing_meta.get("date", datetime.now().strftime("%Y-%m-%d"))
    depth = existing_meta.get("depth", "T3")
    advocates = existing_meta.get("advocates", "?")
    judges = existing_meta.get("judges", "?")
    cost = existing_meta.get("cost", "?")
    time_str = existing_meta.get("time", "?")

    frontmatter = (
        f"---\n"
        f"topic: {existing_meta.get('topic', session_id)}\n"
        f"session: {session_id}\n"
        f"date: {date_str}\n"
        f"depth: {depth}\n"
        f"advocates: {advocates}\n"
        f"judges: {judges}\n"
        f"cost: {cost}\n"
        f"time: {time_str}\n"
        f"status: completed\n"
        f"---\n\n"
    )

    header = (
        f"# Executive Briefing\n"
        f"**Session: {session_id} | Depth: {depth} | "
        f"Analysts: {advocates} | Reviewers: {judges} | "
        f"Cost: {cost} | Time: {time_str}**\n\n"
        f"---\n\n"
    )

    briefing_text = frontmatter + header + (resp.content or "")
    briefing_text = _deanonymize_text(briefing_text, alias_map, cardinal_alias_map)

    # Determine output path
    if args.output:
        md_path = Path(args.output)
    else:
        # Build canonical name
        _sid_parts = session_id.split("-")
        _date_stamp = _sid_parts[0] if _sid_parts and re.match(r"^\d{8}$", _sid_parts[0]) else datetime.now().strftime("%Y%m%d")
        _slug_parts = _sid_parts[1:] if len(_sid_parts) > 1 else ["session"]
        _topic_slug = "-".join(_slug_parts)
        md_name = f"{_date_stamp}-exec-briefing-{_topic_slug}.md"
        md_path = session_dir / md_name

    md_path.write_text(briefing_text)
    print(f"Executive briefing written: {md_path}")

    # Generate full PDF
    try:
        from summary_pdf import generate_summary_pdf
        pdf_path = str(md_path).replace(".md", ".pdf")
        generate_summary_pdf(str(md_path), pdf_path)
        print(f"Full PDF generated: {pdf_path}")
    except Exception as e:
        print(f"PDF generation failed: {e}")

    # Generate exec brief PDF
    try:
        from exec_brief_pdf import generate_exec_brief
        brief_path = str(md_path).replace(".md", "-brief.pdf")
        generate_exec_brief(str(md_path), brief_path)
        print(f"Exec brief PDF generated: {brief_path}")
    except Exception as e:
        print(f"Exec brief PDF failed: {e}")


if __name__ == "__main__":
    main()
