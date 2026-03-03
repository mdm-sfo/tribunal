#!/usr/bin/env python3
"""
Screenplay Generator — Converts Tribunal session files into dramatic screenplays.

Three-pass pipeline:
  Pass 1: LLM extraction of structured argument objects
  Pass 2: Deterministic validation (rule-based + future NLI)
  Pass 3: Constrained LLM dramatization

Part of The Tribunal (github.com/mdm-sfo/conclave)

Usage:
    python3 screenplay_generator.py --session-dir <path> [--acts 3|4] [--demo]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add script directory to path for sibling imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "conclave" / "scripts"))

from config_loader import BISHOPS, DRAMATIST, ADVOCATES, load_config
from model_client import call_model, ModelResponse
from progress import Progress


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ArgumentObject:
    """A single structured argument extracted from session files."""
    speaker_alias: str          # e.g. "Advocate-A"
    claim_text: str             # The actual claim/argument
    evidence_cited: str         # Supporting evidence mentioned
    event_type: str             # SUBMISSION | CHALLENGE | DEFEND | CONCEDE | REVISE | VERDICT | FRESH_EYES | DISSENT
    source_anchor: str          # e.g. "submission-advocate-a.md#L45-L50"
    round_number: int           # 0 for submissions, 1+ for debate rounds
    position_stability: Optional[int] = None  # 1-5 if available


@dataclass
class ValidationResult:
    """Result from validating a single extracted claim."""
    claim_id: int
    is_valid: bool
    method: str           # "rule_based" | "nli_ensemble"
    number_match: bool
    entity_match: bool
    nli_score: Optional[float]
    notes: str


@dataclass
class ScreenplayLine:
    """A single line in the voice-script manifest."""
    character: str
    text: str
    act: int
    scene: str
    source_anchor: Optional[str]
    ordering_rationale: str


@dataclass
class CharacterDef:
    """A character in the screenplay."""
    id: str
    display_name: str
    voice_style: str
    real_identity: Optional[str]


# ---------------------------------------------------------------------------
# Session file discovery
# ---------------------------------------------------------------------------

def discover_session_files(session_dir: Path) -> dict:
    """
    Scan a session directory and return a dict mapping file roles to paths.
    Follows the naming conventions from council_orchestrator.py.
    """
    files: dict = {
        "briefing": None,
        "submissions": [],
        "challenges": [],
        "debate_rounds": {},   # round_number -> list of paths
        "judgments": [],
        "dissents": [],
        "fresh_eyes": None,
        "alias_map": None,
        "cardinal_alias_map": None,
    }

    for path in sorted(session_dir.iterdir()):
        name = path.name

        if name == "briefing.md":
            files["briefing"] = path
        elif name == "fresh-eyes-review.md":
            files["fresh_eyes"] = path
        elif name == "alias-map.json":
            files["alias_map"] = path
        elif name == "cardinal-alias-map.json":
            files["cardinal_alias_map"] = path
        elif re.match(r"submission-advocate-[a-z]+\.md$", name):
            files["submissions"].append(path)
        elif re.match(r"challenge-by-advocate-[a-z]+\.md$", name):
            files["challenges"].append(path)
        elif re.match(r"critique-advocate-[a-z]+-on-advocate-[a-z]+\.md$", name):
            # Older format: critique files treated as challenges
            files["challenges"].append(path)
        elif m := re.match(r"debate-round-(\d+)-advocate-([a-z]+)\.md$", name):
            rnum = int(m.group(1))
            files["debate_rounds"].setdefault(rnum, []).append(path)
        elif re.match(r"(judgment|cardinal-judgment)-[a-z0-9-]+\.md$", name):
            files["judgments"].append(path)
        elif re.match(r"dissent-advocate-[a-z]+\.md$", name):
            files["dissents"].append(path)

    return files


def read_session_files(files: dict) -> dict:
    """
    Read all discovered files and return their text content.
    Returns a dict with the same structure but Path values replaced with str content.
    """
    content: dict = {
        "briefing": "",
        "submissions": [],
        "challenges": [],
        "debate_rounds": {},
        "judgments": [],
        "dissents": [],
        "fresh_eyes": "",
        "alias_map": {},
        "cardinal_alias_map": {},
    }

    if files["briefing"]:
        content["briefing"] = files["briefing"].read_text(encoding="utf-8")

    if files["fresh_eyes"]:
        content["fresh_eyes"] = files["fresh_eyes"].read_text(encoding="utf-8")

    if files["alias_map"]:
        try:
            content["alias_map"] = json.loads(files["alias_map"].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            content["alias_map"] = {}

    if files["cardinal_alias_map"]:
        try:
            content["cardinal_alias_map"] = json.loads(
                files["cardinal_alias_map"].read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            content["cardinal_alias_map"] = {}

    for path in files["submissions"]:
        content["submissions"].append({
            "filename": path.name,
            "text": path.read_text(encoding="utf-8"),
        })

    for path in files["challenges"]:
        content["challenges"].append({
            "filename": path.name,
            "text": path.read_text(encoding="utf-8"),
        })

    for rnum, paths in sorted(files["debate_rounds"].items()):
        content["debate_rounds"][rnum] = []
        for path in paths:
            content["debate_rounds"][rnum].append({
                "filename": path.name,
                "text": path.read_text(encoding="utf-8"),
            })

    for path in files["judgments"]:
        content["judgments"].append({
            "filename": path.name,
            "text": path.read_text(encoding="utf-8"),
        })

    for path in files["dissents"]:
        content["dissents"].append({
            "filename": path.name,
            "text": path.read_text(encoding="utf-8"),
        })

    return content


# ---------------------------------------------------------------------------
# Pass 1: LLM Extraction
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are a structured data extractor for a Tribunal deliberation system.
Your task is to parse Tribunal session documents and extract a precise JSON list
of ArgumentObject records. You MUST output ONLY valid JSON — no prose, no markdown
code fences, no commentary.

Each ArgumentObject has these exact fields:
{
  "speaker_alias": "<string>  — e.g. Advocate-A, Cardinal-A, Moderator",
  "claim_text": "<string>     — the specific claim or argument being made (1-3 sentences)",
  "evidence_cited": "<string> — supporting evidence, data, or reasoning cited (empty string if none)",
  "event_type": "<string>     — one of: SUBMISSION | CHALLENGE | DEFEND | CONCEDE | REVISE | VERDICT | FRESH_EYES | DISSENT",
  "source_anchor": "<string>  — filename and approximate line range, e.g. submission-advocate-a.md#L1-L20",
  "round_number": <int>       — 0 for initial submissions, 1+ for debate rounds, 99 for verdicts,
  "position_stability": <int|null>  — 1-5 self-reported stability score if the document states it, else null
}

Event type rules:
- SUBMISSION: from initial submissions
- CHALLENGE: from challenge or critique files  
- DEFEND: from debate-round files where the advocate defends their position
- CONCEDE: from debate-round files where the advocate concedes a point
- REVISE: from debate-round files where the advocate revises their position
- VERDICT: from judgment or cardinal-judgment files
- FRESH_EYES: from fresh-eyes-review files
- DISSENT: from dissent files where an advocate issues a formal dissenting opinion

Extract ALL significant claims. Each discrete claim should be its own record.
For debate rounds, carefully distinguish DEFEND vs CONCEDE vs REVISE based on
the advocate's explicit language. Look for "Position stability: N" declarations.

Output: a JSON array of ArgumentObject records. Nothing else."""


def build_extraction_prompt(session_content: dict) -> str:
    """Build the extraction prompt from session content."""
    parts = ["## TRIBUNAL SESSION DOCUMENTS\n"]

    if session_content["briefing"]:
        parts.append("### briefing.md\n")
        parts.append(session_content["briefing"])
        parts.append("\n\n")

    for sub in session_content["submissions"]:
        parts.append(f"### {sub['filename']}\n")
        parts.append(sub["text"])
        parts.append("\n\n")

    for chal in session_content["challenges"]:
        parts.append(f"### {chal['filename']}\n")
        parts.append(chal["text"])
        parts.append("\n\n")

    # For large sessions (>4 rounds), only include first, middle, and last rounds
    # to avoid overflowing the extractor's output token limit.
    all_rounds = sorted(session_content["debate_rounds"].keys())
    if len(all_rounds) > 4:
        # Always include first round, last round, and one from ~midpoint
        mid = all_rounds[len(all_rounds) // 2]
        selected_rounds = sorted(set([all_rounds[0], mid, all_rounds[-1]]))
    else:
        selected_rounds = all_rounds

    for rnum in selected_rounds:
        for rfile in session_content["debate_rounds"][rnum]:
            parts.append(f"### {rfile['filename']}\n")
            parts.append(rfile["text"])
            parts.append("\n\n")

    for j in session_content["judgments"]:
        parts.append(f"### {j['filename']}\n")
        parts.append(j["text"])
        parts.append("\n\n")

    if session_content["fresh_eyes"]:
        parts.append("### fresh-eyes-review.md\n")
        parts.append(session_content["fresh_eyes"])
        parts.append("\n\n")

    for d in session_content.get("dissents", []):
        parts.append(f"### {d['filename']}\n")
        parts.append(d["text"])
        parts.append("\n\n")

    parts.append(
        "---\n\nNow extract ALL argument objects from the above documents.\n"
        "Output ONLY a JSON array of ArgumentObject records. No other text."
    )

    return "".join(parts)


def parse_extraction_response(raw: str) -> list[ArgumentObject]:
    """
    Parse the LLM's JSON response into ArgumentObject instances.
    Handles common LLM JSON quirks (code fences, leading prose).
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if "```" in cleaned:
        # Extract content between first ``` and last ```
        fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()

    # If there's prose before the JSON array, find the array start
    array_start = cleaned.find("[")
    array_end = cleaned.rfind("]")
    if array_start != -1 and array_end != -1:
        cleaned = cleaned[array_start : array_end + 1]

    try:
        raw_list = json.loads(cleaned)
    except json.JSONDecodeError:
        # Response was likely truncated (token limit). Try to recover complete objects
        # by finding the last fully-closed JSON object before the truncation point.
        last_close = cleaned.rfind("},")
        if last_close == -1:
            last_close = cleaned.rfind("}")
        if last_close != -1 and cleaned.find("[") != -1:
            recovered = cleaned[cleaned.find("[") : last_close + 1] + "]"
            try:
                raw_list = json.loads(recovered)
            except json.JSONDecodeError as e2:
                raise ValueError(
                    f"LLM extraction produced truncated JSON that could not be recovered: {e2}\n"
                    f"Raw (first 500 chars):\n{raw[:500]}"
                )
        else:
            raise ValueError(
                f"LLM extraction produced invalid JSON with no recoverable objects.\n"
                f"Raw (first 500 chars):\n{raw[:500]}"
            )

    if not isinstance(raw_list, list):
        raise ValueError(f"Expected JSON array, got {type(raw_list)}")

    VALID_EVENT_TYPES = {
        "SUBMISSION", "CHALLENGE", "DEFEND", "CONCEDE",
        "REVISE", "VERDICT", "FRESH_EYES", "DISSENT",
    }

    objects: list[ArgumentObject] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            continue

        event_type = str(item.get("event_type", "SUBMISSION")).upper()
        if event_type not in VALID_EVENT_TYPES:
            event_type = "SUBMISSION"

        stability = item.get("position_stability")
        if stability is not None:
            try:
                stability = int(stability)
                if not 1 <= stability <= 5:
                    stability = None
            except (TypeError, ValueError):
                stability = None

        objects.append(ArgumentObject(
            speaker_alias=str(item.get("speaker_alias", f"Unknown-{i}")),
            claim_text=str(item.get("claim_text", "")),
            evidence_cited=str(item.get("evidence_cited", "")),
            event_type=event_type,
            source_anchor=str(item.get("source_anchor", "")),
            round_number=int(item.get("round_number", 0)),
            position_stability=stability,
        ))

    return objects


def run_extraction_pass(
    session_content: dict,
    progress: Progress,
) -> list[ArgumentObject]:
    """
    Pass 1: Use Qwen 3.5 397B to extract structured argument objects
    from all session files.
    """
    progress.phase(1, "Extraction — parsing session documents into argument objects...")

    user_prompt = build_extraction_prompt(session_content)

    # Try each extractor in order: BISHOPS first, then Claude Sonnet as fallback
    extractor_chain = [BISHOPS[0], ADVOCATES[0]]
    response = None
    for extraction_model in extractor_chain:
        progress.info(f"Dispatching extraction to {extraction_model.display_name}...")
        response = call_model(
            model=extraction_model,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            alias="Extractor",
            timeout=420,
            temperature=0.1,   # Low temperature for structured extraction
            max_tokens=8192,
            progress=progress,
        )
        if response.status == "success" and response.content:
            break
        progress.info(f"Extractor {extraction_model.display_name} failed ({response.error}), trying next...")

    if not response or response.status != "success" or not response.content:
        raise RuntimeError(
            f"Extraction LLM call failed: {response.error if response else 'no response'}"
        )

    objects = parse_extraction_response(response.content)
    progress.info(f"Extracted {len(objects)} argument objects.")
    return objects


# ---------------------------------------------------------------------------
# Pass 2: Validation
# ---------------------------------------------------------------------------

def _extract_numbers(text: str) -> set:
    """Extract all numbers (int and float) from text."""
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))


def _extract_entities(text: str) -> set:
    """
    Extract named entities using simple heuristics:
    capitalized words (not sentence starters) and quoted phrases.
    """
    entities = set()

    # Quoted phrases
    for match in re.finditer(r'"([^"]{3,})"', text):
        entities.add(match.group(1).lower())

    # Capitalized sequences (e.g. "Rust Programming Language", "Go 1.21")
    for match in re.finditer(r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*)\b", text):
        word = match.group(1)
        # Skip sentence-start words (preceded by ". " or start of string)
        start = match.start()
        preceding = text[max(0, start - 3) : start]
        if re.search(r"[.!?]\s*$", preceding) or start == 0:
            continue
        if len(word) >= 3 and word not in {"The", "This", "That", "These", "Those", "You", "For"}:
            entities.add(word.lower())

    return entities


def validate_rule_based(
    claim_id: int,
    claim: ArgumentObject,
    source_texts: dict,
) -> ValidationResult:
    """
    Rule-based claim validation:
    1. Number matching: all numbers in the claim must appear in source text
    2. Entity matching: named entities in the claim must appear in source text

    source_texts is a flat dict of filename -> text for lookup.
    """
    # Find the source text by anchor filename
    source_text = ""
    if claim.source_anchor:
        anchor_file = claim.source_anchor.split("#")[0]
        source_text = source_texts.get(anchor_file, "")

    # If no source found by anchor, use all text concatenated
    if not source_text:
        source_text = " ".join(source_texts.values())

    source_lower = source_text.lower()

    # Number check
    claim_numbers = _extract_numbers(claim.claim_text)
    evidence_numbers = _extract_numbers(claim.evidence_cited)
    all_claim_numbers = claim_numbers | evidence_numbers

    number_match = True
    unmatched_numbers = []
    for num in all_claim_numbers:
        if num not in source_lower:
            number_match = False
            unmatched_numbers.append(num)

    # Entity check
    claim_entities = _extract_entities(claim.claim_text)
    entity_match = True
    unmatched_entities = []
    for ent in claim_entities:
        # Check if the entity (lowercased) appears somewhere in source
        if ent not in source_lower:
            entity_match = False
            unmatched_entities.append(ent)

    is_valid = number_match and entity_match
    notes_parts = []
    if unmatched_numbers:
        notes_parts.append(f"numbers not in source: {', '.join(unmatched_numbers)}")
    if unmatched_entities:
        notes_parts.append(f"entities not in source: {', '.join(unmatched_entities[:5])}")

    return ValidationResult(
        claim_id=claim_id,
        is_valid=is_valid,
        method="rule_based",
        number_match=number_match,
        entity_match=entity_match,
        nli_score=None,
        notes=" | ".join(notes_parts) if notes_parts else "ok",
    )


# NLI server endpoint — GPU host running nli_server.py (DeBERTa-v3-large-MNLI)
# Set TRIBUNAL_NLI_URL in your environment to enable GPU-accelerated NLI validation.
# If unset, falls back to rule-based validation (always available, no GPU needed).
NLI_SERVER_URL = os.environ.get("TRIBUNAL_NLI_URL", "")

# Thresholds for NLI validation
NLI_CONTRADICTION_THRESHOLD = 0.70   # flag as invalid if contradiction > this
NLI_ENTAILMENT_THRESHOLD = 0.50      # consider verified if entailment > this


def check_nli_server(endpoint: Optional[str] = None) -> bool:
    """Check if the NLI inference server is reachable."""
    import urllib.request  # noqa: PLC0415
    url = endpoint or NLI_SERVER_URL
    if not url:
        return False
    try:
        req = urllib.request.urlopen(f"{url}/health", timeout=3)
        data = json.loads(req.read().decode())
        # Accept either format: {"status": "healthy"} or {"ok": true}
        return data.get("status") == "healthy" or data.get("ok") is True
    except Exception:
        return False


def validate_nli(
    claim_id: int,
    claim: ArgumentObject,
    source_text: str,
    nli_endpoint: Optional[str] = None,
) -> ValidationResult:
    """
    NLI-based claim validation using DGX Spark running nli_server.py
    (DeBERTa-v3-large-MNLI via FastAPI).

    Sends premise=source_text, hypothesis=claim.claim_text to the NLI server.
    If contradiction probability > NLI_CONTRADICTION_THRESHOLD, marks claim invalid.

    Falls back to rule-based validation if the server is unreachable.
    """
    import urllib.request  # noqa: PLC0415

    url = nli_endpoint or NLI_SERVER_URL

    # Truncate source to ~2000 chars to stay within DeBERTa's 512-token window.
    # The tokenizer handles the actual truncation, but shorter inputs = faster inference.
    premise = source_text[:2000] if len(source_text) > 2000 else source_text
    hypothesis = claim.claim_text

    try:
        payload = json.dumps({
            "premise": premise,
            "hypothesis": hypothesis,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{url}/predict",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())

        pred = data["prediction"]
        entailment = pred["entailment"]
        contradiction = pred["contradiction"]
        neutral = pred["neutral"]
        label = pred["label"]
        inference_ms = data.get("inference_ms", 0)

        # Decision logic:
        #   - High contradiction → invalid (claim contradicts source)
        #   - High entailment → valid (source supports claim)
        #   - Neutral / low scores → valid but flagged with note
        is_valid = contradiction < NLI_CONTRADICTION_THRESHOLD

        notes_parts = []
        notes_parts.append(f"nli={label} (e={entailment:.3f} c={contradiction:.3f} n={neutral:.3f})")
        notes_parts.append(f"{inference_ms:.0f}ms")

        if not is_valid:
            notes_parts.append(f"CONTRADICTION>{NLI_CONTRADICTION_THRESHOLD}")

        if entailment < NLI_ENTAILMENT_THRESHOLD and is_valid:
            notes_parts.append("weak entailment")

        return ValidationResult(
            claim_id=claim_id,
            is_valid=is_valid,
            method="nli",
            number_match=True,   # NLI subsumes number checking
            entity_match=True,   # NLI subsumes entity checking
            nli_score=entailment,
            notes=" | ".join(notes_parts),
        )

    except Exception as e:
        # Server unreachable or error — fall back to rule-based
        return ValidationResult(
            claim_id=claim_id,
            is_valid=True,
            method="nli_fallback",
            number_match=True,
            entity_match=True,
            nli_score=None,
            notes=f"NLI server error ({type(e).__name__}), used rule-based fallback",
        )


def validate_nli_batch(
    claims: list,
    source_texts_map: dict,
    nli_endpoint: Optional[str] = None,
) -> list:
    """
    Batch NLI validation — sends all claims in one request for efficiency.
    Each entry in claims is (claim_id, ArgumentObject).
    Returns list of ValidationResult.
    """
    import urllib.request  # noqa: PLC0415

    url = nli_endpoint or NLI_SERVER_URL

    # Build batch payload
    pairs = []
    claim_index = []  # track which claim maps to which pair
    for claim_id, claim in claims:
        source = _find_source_text(claim, source_texts_map)
        premise = source[:2000] if len(source) > 2000 else source
        pairs.append({"premise": premise, "hypothesis": claim.claim_text})
        claim_index.append((claim_id, claim))

    try:
        payload = json.dumps({"pairs": pairs}).encode("utf-8")

        req = urllib.request.Request(
            f"{url}/predict_batch",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)  # batch can take longer
        data = json.loads(resp.read().decode())

        predictions = data["predictions"]
        inference_ms = data.get("inference_ms", 0)
        per_claim_ms = inference_ms / max(len(predictions), 1)

        results = []
        for i, pred in enumerate(predictions):
            cid, claim = claim_index[i]
            entailment = pred["entailment"]
            contradiction = pred["contradiction"]
            neutral = pred["neutral"]
            label = pred["label"]

            is_valid = contradiction < NLI_CONTRADICTION_THRESHOLD

            notes_parts = []
            notes_parts.append(f"nli={label} (e={entailment:.3f} c={contradiction:.3f} n={neutral:.3f})")
            notes_parts.append(f"{per_claim_ms:.0f}ms")
            if not is_valid:
                notes_parts.append(f"CONTRADICTION>{NLI_CONTRADICTION_THRESHOLD}")
            if entailment < NLI_ENTAILMENT_THRESHOLD and is_valid:
                notes_parts.append("weak entailment")

            results.append(ValidationResult(
                claim_id=cid,
                is_valid=is_valid,
                method="nli_batch",
                number_match=True,
                entity_match=True,
                nli_score=entailment,
                notes=" | ".join(notes_parts),
            ))

        return results

    except Exception:
        # Batch failed — return empty (caller will fall back to rule-based)
        return []


def _find_source_text(claim: ArgumentObject, source_texts_map: dict) -> str:
    """Find the source text for a claim based on its source_anchor."""
    # Extract filename from anchor like "submission-advocate-a.md#L45-L50"
    anchor = claim.source_anchor or ""
    source_file = anchor.split("#")[0] if "#" in anchor else anchor

    # Try exact match first
    if source_file in source_texts_map:
        return source_texts_map[source_file]
    # Try basename match
    basename = source_file.rsplit("/", 1)[-1] if "/" in source_file else source_file
    if basename in source_texts_map:
        return source_texts_map[basename]
    # Fallback: concatenate all sources (less precise but still useful for NLI)
    return "\n\n".join(source_texts_map.values())[:4000]


def run_validation_pass(
    arguments: list[ArgumentObject],
    session_content: dict,
    progress: Progress,
) -> list[ValidationResult]:
    """
    Pass 2: Validate each extracted argument object against its source text.

    Strategy:
    1. Check if the NLI server (DGX Spark) is reachable
    2. If yes: use batch NLI validation (DeBERTa-v3-large-MNLI)
    3. If no: fall back to rule-based validation (always available)

    Both methods flag claims as [unverified] when validation fails.
    NLI is more accurate — it catches semantic contradictions, not just
    number/entity mismatches.
    """
    # Build flat source text lookup: filename -> text
    source_texts: dict = {}

    if session_content.get("briefing"):
        source_texts["briefing.md"] = session_content["briefing"]

    for sub in session_content.get("submissions", []):
        source_texts[sub["filename"]] = sub["text"]

    for chal in session_content.get("challenges", []):
        source_texts[chal["filename"]] = chal["text"]

    for rnum, rfiles in session_content.get("debate_rounds", {}).items():
        for rfile in rfiles:
            source_texts[rfile["filename"]] = rfile["text"]

    for j in session_content.get("judgments", []):
        source_texts[j["filename"]] = j["text"]

    if session_content.get("fresh_eyes"):
        source_texts["fresh-eyes-review.md"] = session_content["fresh_eyes"]

    for d in session_content.get("dissents", []):
        source_texts[d["filename"]] = d["text"]

    # Check NLI server availability
    nli_available = check_nli_server()
    if nli_available:
        progress.phase(2, f"Validation — NLI check on {len(arguments)} argument objects (DGX Spark)...")
        progress.info(f"NLI server: {NLI_SERVER_URL}")

        # Try batch NLI first (more efficient)
        claims_with_ids = [(i, arg) for i, arg in enumerate(arguments)]
        nli_results = validate_nli_batch(claims_with_ids, source_texts)

        if nli_results and len(nli_results) == len(arguments):
            # Batch NLI succeeded
            unverified_count = sum(1 for r in nli_results if not r.is_valid)
            progress.info(
                f"NLI validation complete: {len(nli_results) - unverified_count}/{len(nli_results)} "
                f"claims verified, {unverified_count} flagged [unverified]"
            )
            return nli_results
        else:
            # Batch failed, try individual NLI calls
            progress.info("Batch NLI failed, trying individual calls...")
            results: list[ValidationResult] = []
            unverified_count = 0
            for i, arg in enumerate(arguments):
                source = _find_source_text(arg, source_texts)
                result = validate_nli(i, arg, source)
                if not result.is_valid:
                    unverified_count += 1
                results.append(result)

            # Check if any actually used NLI (vs all falling back)
            nli_used = sum(1 for r in results if r.method in ("nli", "nli_batch"))
            if nli_used > 0:
                progress.info(
                    f"NLI validation complete: {len(results) - unverified_count}/{len(results)} "
                    f"claims verified ({nli_used} via NLI), {unverified_count} flagged [unverified]"
                )
                return results
            # All fell back — use rule-based below
            progress.info("NLI calls all failed, falling back to rule-based...")
    else:
        progress.phase(2, f"Validation — rule-based check on {len(arguments)} argument objects...")

    # Rule-based validation (always available)
    results = []
    unverified_count = 0

    for i, arg in enumerate(arguments):
        result = validate_rule_based(i, arg, source_texts)
        if not result.is_valid:
            unverified_count += 1
        results.append(result)

    progress.info(
        f"Validation complete: {len(results) - unverified_count}/{len(results)} claims verified, "
        f"{unverified_count} flagged [unverified]"
    )
    return results


def apply_validation_flags(
    arguments: list[ArgumentObject],
    validation_results: list[ValidationResult],
) -> list[ArgumentObject]:
    """
    Annotate argument objects with [unverified] tag when validation fails.
    Returns a new list of (potentially annotated) ArgumentObjects.
    """
    flagged: list[ArgumentObject] = []
    result_map = {r.claim_id: r for r in validation_results}

    for i, arg in enumerate(arguments):
        result = result_map.get(i)
        if result and not result.is_valid:
            # Annotate claim_text with [unverified]
            import copy  # noqa: PLC0415
            annotated = copy.copy(arg)
            annotated.claim_text = f"[unverified] {arg.claim_text}"
            flagged.append(annotated)
        else:
            flagged.append(arg)

    return flagged


# ---------------------------------------------------------------------------
# Pass 3: Dramatization
# ---------------------------------------------------------------------------

DRAMATIZATION_SYSTEM_PROMPT = """\
You are a dramatist and screenwriter adapting a real AI deliberation into a
compelling screenplay. You write in a clear, vivid, authoritative voice — like
Aaron Sorkin meets a philosophy seminar.

You will receive structured argument objects extracted from a Tribunal session,
along with an identity reveal map. Your job is to transform these into a dramatic
screenplay suitable for text-to-speech narration.

## Character Name Constraint (CRITICAL)
Use ONLY the character names listed in the Identity Reveal Map below, plus MODERATOR.
Do NOT invent characters such as "Cardinal-One", "Cardinal-Two", "Advocate-D",
"Advocate-E", etc. unless they appear in the Identity Reveal Map. Every spoken line
must be attributed to a character that exists in the map or is MODERATOR.

## Formatting Rules (CRITICAL)
- NO markdown formatting in spoken lines (no **, no *, no #, no >, no tables)
- Numbers must be spoken naturally: "three hundred fifty thousand" not "350,000"
- All spoken lines must be natural, complete sentences
- Preserve source anchors as HTML comments immediately after the relevant line:
  <!-- source: filename.md#Lxx-Lyy -->
- Use the MODERATOR character to introduce acts, scenes, and transitions
- Use aliases (Advocate-A, Advocate-B, etc.) until the identity reveal in the final act

## Act Structure
You will receive an --acts parameter: 3 or 4.

4-ACT structure:
  ACT ONE — Opening Positions
    The Moderator introduces the deliberation topic and each advocate states
    their initial hypothesis. Present each advocate's core claim and evidence.

  ACT TWO — The Challenge
    The cross-examination. Highlight the sharpest, most pointed challenges.
    Each challenge should feel like a courtroom confrontation.

  ACT THREE — The Debate
    Round-by-round position evolution. EVERY DEFEND, CONCEDE, and REVISE event
    MUST appear. Montage/recap less critical exchanges if space is tight.
    Show intellectual honesty — advocates who CONCEDE well should be honored.

  ACT FOUR — The Verdict
    The judges deliver their opinions. All judge-cited facts must appear.
    If any DISSENT events exist, they appear here: the dissenter delivers their
    formal dissenting opinion AFTER the verdict, introduced by the MODERATOR.
    End with the identity reveal: "And now, the identities behind the masks..."

3-ACT structure:
  ACT ONE — The Positions (combines Opening + partial Challenge)
  ACT TWO — The Crucible (full Challenge + all Debate rounds)
  ACT THREE — The Verdict (judge opinions + dissenting opinions + identity reveal)

## Word Budget (MANDATORY)
Minimum: 1,800 words. Target: 3,000 to 5,000 words total.
Do NOT summarize or condense — dramatize EVERY argument object into at least one
spoken exchange. Each of the 4 acts should be substantial (400+ words minimum).
This is a play-by-play, not a highlight reel. Short screenplays are rejected.
If material is extensive, use the MODERATOR to recap and compress less critical
exchanges: "In the rounds that followed, Advocate-B defended their position on
three separate occasions before conceding the statistical point..."

## Voice Styles
- MODERATOR: Authoritative narrator, formal, cinematic
- Advocates: Intellectually sharp, evidence-driven, no filler
- Judges/Cardinals: Measured, decisive, analytical

## Output Format
Begin with:
  TITLE: [Dramatized title based on the deliberation topic]
  SETTING: A virtual deliberation chamber.

Then write the acts using this format for each spoken line:

  CHARACTER NAME
  [Spoken text here. No markdown. Natural sentences. Speakable numbers.]
  <!-- source: filename.md#Lxx-Lyy -->

Stage directions go in parentheses on their own line:
  (The advocates take their positions. The chamber falls silent.)

## Critical Requirements
- ALL DEFEND/CONCEDE/REVISE events must appear — none may be omitted
- All judge-cited facts must appear in Act Three/Four
- Position stability scores (if present) should inform the MODERATOR's commentary
- The identity reveal in the final act is the dramatic climax — build to it
"""


def build_dramatization_prompt(
    validated_args: list[ArgumentObject],
    session_content: dict,
    act_count: int,
    session_id: str,
) -> str:
    """Build the dramatization prompt from validated argument objects."""

    # Serialize argument objects for the LLM
    args_json = json.dumps(
        [asdict(a) for a in validated_args],
        indent=2,
    )

    # Build identity reveal section
    alias_map = session_content.get("alias_map", {})
    cardinal_map = session_content.get("cardinal_alias_map", {})
    all_reveals = {**alias_map, **cardinal_map}

    reveal_lines = []
    for alias, info in all_reveals.items():
        if isinstance(info, dict):
            model = info.get("model", "Unknown")
            provider = info.get("provider", "")
            role = info.get("role", "advocate")
            reveal_lines.append(f"  {alias} → {model} ({provider}) [{role}]")
        else:
            reveal_lines.append(f"  {alias} → {info}")

    reveal_text = "\n".join(reveal_lines) if reveal_lines else "  (no identity map available)"

    topic_text = ""
    if session_content.get("briefing"):
        # Extract first 200 chars of briefing as topic summary
        briefing_first = session_content["briefing"][:300].strip()
        topic_text = f"\n\n## Original Briefing (first 300 chars)\n{briefing_first}\n..."

    return f"""## TRIBUNAL SESSION: {session_id}

## Act Structure
Write a {act_count}-act screenplay.{topic_text}

## Identity Reveal Map (for the final act only)
{reveal_text}

## Validated Argument Objects ({len(validated_args)} total)
{args_json}

---

Now write the full {act_count}-act screenplay. Follow all formatting rules from
the system prompt exactly. Target 3,000-5,000 words (minimum 1,800). Preserve all source anchors
as HTML comments. Every DEFEND, CONCEDE, and REVISE event must appear.
Begin with the TITLE line."""


def run_dramatization_pass(
    validated_args: list[ArgumentObject],
    session_content: dict,
    act_count: int,
    session_id: str,
    progress: Progress,
) -> str:
    """
    Pass 3: Use Kimi K2 to convert validated argument objects into
    a dramatic screenplay.
    Returns the screenplay text.
    """
    progress.phase(3, f"Dramatization — generating {act_count}-act screenplay...")

    user_prompt = build_dramatization_prompt(
        validated_args, session_content, act_count, session_id
    )

    # Try DRAMATIST (Kimi K2) first, then fall back to Claude Sonnet
    dramatist_chain = [DRAMATIST, ADVOCATES[0]]
    response = None
    for dramatization_model in dramatist_chain:
        progress.info(f"Dispatching dramatization to {dramatization_model.display_name}...")
        response = call_model(
            model=dramatization_model,
            system_prompt=DRAMATIZATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            alias="Dramatist",
            timeout=240,
            temperature=0.6,
            max_tokens=8192,
            progress=progress,
        )
        if response.status == "success" and response.content:
            break
        progress.info(f"Dramatist {dramatization_model.display_name} failed ({response.error}), trying next...")

    if not response or response.status != "success" or not response.content:
        raise RuntimeError(
            f"Dramatization LLM call failed: {response.error if response else 'no response'}"
        )

    progress.info(
        f"Screenplay generated: ~{len(response.content.split())} words, "
        f"{response.output_tokens} tokens."
    )

    # ── Word budget enforcement: retry once if under floor ────────────────────
    word_count = len(response.content.split())
    WORD_FLOOR = 1400  # hard floor — prompt asks for 1800, we accept 1400+
    if word_count < WORD_FLOOR:
        progress.info(
            f"Output was {word_count} words (floor: {WORD_FLOOR}). "
            f"Re-prompting for expansion..."
        )
        expansion_prompt = (
            f"Your screenplay was only {word_count} words. The mandatory minimum "
            f"is 1,800 words.\n\n"
            f"Expand it NOW. Add more play-by-play narration, more back-and-forth "
            f"exchanges between advocates, more MODERATOR commentary bridging the "
            f"acts, and more detail in the judicial verdicts. Do NOT change the "
            f"structure or verdict outcomes — just flesh out every scene.\n\n"
            f"Return the COMPLETE expanded screenplay from TITLE to the final line. "
            f"Target: 2,500+ words."
        )
        retry_response = call_model(
            model=dramatization_model,
            system_prompt=DRAMATIZATION_SYSTEM_PROMPT,
            user_prompt=expansion_prompt,
            alias="Dramatist-retry",
            timeout=240,
            temperature=0.6,
            max_tokens=8192,
            progress=progress,
        )
        if (
            retry_response.status == "success"
            and retry_response.content
            and len(retry_response.content.split()) > word_count
        ):
            retry_words = len(retry_response.content.split())
            progress.info(
                f"Expanded screenplay: ~{retry_words} words "
                f"(+{retry_words - word_count}), "
                f"{retry_response.output_tokens} tokens."
            )
            return retry_response.content
        else:
            progress.info("Expansion retry did not improve length; using original.")

    return response.content


# ---------------------------------------------------------------------------
# Voice script generation
# ---------------------------------------------------------------------------

def _infer_voice_style(alias: str, real_identity: Optional[str]) -> str:
    """Infer a TTS voice style from the character's alias and real identity."""
    alias_lower = alias.lower()
    if "moderator" in alias_lower or "narrator" in alias_lower:
        return "authoritative narrator"
    if "cardinal" in alias_lower or "judge" in alias_lower:
        return "measured, decisive"
    if "advocate" in alias_lower:
        return "confident, measured"
    return "neutral"


def extract_lines_from_screenplay(screenplay_text: str) -> list[dict]:
    """
    Parse the screenplay text into individual lines with character attribution.

    Handles the screenplay format:
      CHARACTER NAME
      [Spoken text]
      <!-- source: filename#Lxx-Lyy -->

    Stage directions in parentheses are skipped.
    """
    lines = []
    current_act = 1
    current_scene = "opening"
    line_buffer: list[str] = []
    current_character: Optional[str] = None
    current_source: Optional[str] = None

    # Act markers
    act_pattern = re.compile(r"^\s*ACT\s+(ONE|TWO|THREE|FOUR|\d+)", re.IGNORECASE)
    act_map = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4}

    # Character name pattern: ALL CAPS line or Advocate/Judge aliases (mixed case)
    character_pattern = re.compile(r"^([A-Z][A-Z\s\-]{2,})$")
    # Mixed-case aliases: "Advocate-B", "Judge-A (Zhipu GLM-4.7, Priest)", "Advocate-D (entering)"
    alias_pattern = re.compile(r"^((?:Advocate|Judge|Cardinal|Fresh-Eyes|Narrator)-[A-Z]\d*)\s*(?:\(.*\))?$")

    # Source anchor pattern
    source_pattern = re.compile(r"<!--\s*source:\s*([^\s>]+)\s*-->")

    # Scene label pattern
    scene_pattern = re.compile(r"^\s*(?:Scene|SCENE)\s*[:\-–]?\s*(.+)", re.IGNORECASE)

    raw_lines = screenplay_text.splitlines()

    def flush_current():
        """Flush buffered lines for current character."""
        nonlocal current_character, line_buffer, current_source
        if current_character and line_buffer:
            text = " ".join(line_buffer).strip()
            if text:
                lines.append({
                    "character": current_character.lower().replace(" ", "-"),
                    "text": text,
                    "act": current_act,
                    "scene": current_scene,
                    "source_anchor": current_source,
                    "ordering_rationale": f"Act {current_act}, {current_scene}",
                })
        current_character = None
        line_buffer = []
        current_source = None

    for raw_line in raw_lines:
        stripped = raw_line.strip()

        # Skip empty lines (but flush)
        if not stripped:
            flush_current()
            continue

        # Check for act marker
        act_match = act_pattern.match(stripped)
        if act_match:
            flush_current()
            act_word = act_match.group(1).upper()
            current_act = act_map.get(act_word, int(act_word) if act_word.isdigit() else current_act)
            current_scene = f"act-{current_act}-opening"
            continue

        # Check for scene label
        scene_match = scene_pattern.match(stripped)
        if scene_match:
            flush_current()
            current_scene = scene_match.group(1).strip().lower().replace(" ", "-")[:40]
            continue

        # Check for source anchor comment
        src_match = source_pattern.search(stripped)
        if src_match:
            current_source = src_match.group(1)
            continue

        # Check for stage direction (parenthetical)
        if stripped.startswith("(") and stripped.endswith(")"):
            flush_current()
            continue

        # Check for character name (ALL CAPS line or Advocate/Judge alias)
        char_match = character_pattern.match(stripped)
        alias_match = alias_pattern.match(stripped)
        if (char_match and len(stripped.split()) <= 4) or alias_match:
            flush_current()
            # Use the alias group (just the name, no parenthetical) if matched
            current_character = alias_match.group(1) if alias_match else stripped
            line_buffer = []
            current_source = None
            continue

        # Otherwise, it's dialogue — add to buffer
        if current_character:
            # Strip any remaining source comments from dialogue
            clean_line = source_pattern.sub("", stripped).strip()
            if clean_line:
                line_buffer.append(clean_line)

    # Flush final buffer
    flush_current()

    return lines


def build_character_roster(
    session_content: dict,
    screenplay_text: str,
) -> list[CharacterDef]:
    """
    Build the character list for the voice-script manifest.
    Includes the moderator plus all advocates and judges extracted from the
    identity maps, supplemented by any additional characters found in the screenplay.
    """
    characters: list[CharacterDef] = []

    # Always include moderator
    characters.append(CharacterDef(
        id="moderator",
        display_name="Moderator",
        voice_style="authoritative narrator",
        real_identity=None,
    ))

    alias_map = session_content.get("alias_map", {})
    cardinal_map = session_content.get("cardinal_alias_map", {})

    for alias, info in sorted(alias_map.items()):
        if isinstance(info, dict):
            real_id = f"{info.get('model', 'Unknown')} ({info.get('provider', '')})"
        else:
            real_id = str(info)
        char_id = alias.lower().replace(" ", "-")
        characters.append(CharacterDef(
            id=char_id,
            display_name=alias,
            voice_style=_infer_voice_style(alias, real_id),
            real_identity=real_id,
        ))

    for alias, info in sorted(cardinal_map.items()):
        if isinstance(info, dict):
            real_id = f"{info.get('model', 'Unknown')} ({info.get('provider', '')})"
        else:
            real_id = str(info)
        char_id = alias.lower().replace(" ", "-")
        characters.append(CharacterDef(
            id=char_id,
            display_name=alias,
            voice_style=_infer_voice_style(alias, real_id),
            real_identity=real_id,
        ))

    # Deduplicate by id
    seen_ids: set = set()
    deduped: list[CharacterDef] = []
    for c in characters:
        if c.id not in seen_ids:
            seen_ids.add(c.id)
            deduped.append(c)

    return deduped


def build_voice_script(
    screenplay_text: str,
    session_content: dict,
    session_id: str,
    act_count: int,
) -> dict:
    """
    Build the TTS-ready voice-script.json manifest from the screenplay text.
    """
    characters = build_character_roster(session_content, screenplay_text)
    raw_lines = extract_lines_from_screenplay(screenplay_text)

    # Build character id lookup for normalization
    char_id_set = {c.id for c in characters}

    # Normalize character references in lines
    for line in raw_lines:
        char_ref = line["character"]
        # Try to match to known characters
        if char_ref not in char_id_set:
            # Try fuzzy match: does any known character id start with this?
            matched = False
            for known_id in char_id_set:
                if char_ref.startswith(known_id) or known_id.startswith(char_ref):
                    line["character"] = known_id
                    matched = True
                    break
            if not matched:
                # Add as new character
                new_char = CharacterDef(
                    id=char_ref,
                    display_name=char_ref.replace("-", " ").title(),
                    voice_style="neutral",
                    real_identity=None,
                )
                if char_ref not in char_id_set:
                    characters.append(new_char)
                    char_id_set.add(char_ref)

    return {
        "session_id": session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "act_count": act_count,
        "characters": [
            {
                "id": c.id,
                "display_name": c.display_name,
                "voice_style": c.voice_style,
                "real_identity": c.real_identity,
            }
            for c in characters
        ],
        "lines": raw_lines,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_screenplay_md(
    screenplay_text: str,
    session_dir: Path,
    session_id: str,
    act_count: int,
    argument_count: int,
) -> Path:
    """Write the screenplay.md file with header metadata."""
    header = (
        f"# Tribunal Screenplay\n\n"
        f"**Session:** {session_id}  \n"
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n"
        f"**Act Structure:** {act_count}-act  \n"
        f"**Argument Objects Processed:** {argument_count}  \n"
        f"**Generator:** screenplay_generator.py (github.com/mdm-sfo/conclave)  \n"
        f"\n---\n\n"
    )
    output_path = session_dir / "screenplay.md"
    output_path.write_text(header + screenplay_text, encoding="utf-8")
    return output_path


def write_voice_script_json(
    voice_script: dict,
    session_dir: Path,
) -> Path:
    """Write the voice-script.json file."""
    output_path = session_dir / "voice-script.json"
    output_path.write_text(json.dumps(voice_script, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Demo mode — built-in sample data
# ---------------------------------------------------------------------------

DEMO_SESSION_CONTENT = {
    "briefing": """\
# Tribunal Briefing

Should our engineering team adopt Rust or Go for our new CLI tool?

We are building a high-performance CLI tool for data pipeline orchestration.
Key requirements: fast startup, low memory footprint, good concurrency primitives,
strong ecosystem for CLI development, and maintainability by a team of 8 engineers.
The team currently has deep Python expertise and moderate experience with C++.
""",
    "submissions": [
        {
            "filename": "submission-advocate-a.md",
            "text": """\
# Submission: Advocate-A

### Hypothesis
Go is the right choice because it offers a superior developer experience for CLI tools
with a 5-millisecond average startup time, excellent concurrency via goroutines,
and a mature CLI ecosystem through libraries like cobra and viper.

### Evidence

**Claim:** Go CLI tools start in under 5 milliseconds on average.
**Reasoning type:** Inductive
**Proof:** Benchmarks from the Go standard library show 3.2ms average cold start.
**Why it matters:** For a CLI tool, startup time is the primary UX metric.

**Claim:** The Go ecosystem for CLIs is more mature than Rust's.
**Reasoning type:** Deductive
**Proof:** cobra (25M+ GitHub downloads), viper (18M+), and survey data showing
68% of CLI-focused Go developers report high satisfaction with the ecosystem.
**Why it matters:** Ecosystem maturity reduces development time by an estimated 30%.

### Counterargument Acknowledgment
Rust's memory safety guarantees are stronger. However, Go's garbage collector
has reached sub-millisecond pause times in Go 1.21, making this difference
negligible for CLI workloads.

### Self-Assessment
| Dimension | Score | Notes |
|-----------|-------|-------|
| Hypothesis clarity | 9 | Clear and testable |
| Evidence strength | 8 | Strong benchmarks |
""",
        },
        {
            "filename": "submission-advocate-b.md",
            "text": """\
# Submission: Advocate-B

### Hypothesis
Rust is the right choice because its zero-cost abstractions and memory safety
without garbage collection will prevent the class of memory bugs that have caused
40% of critical security vulnerabilities in CLI tools over the past 5 years.

### Evidence

**Claim:** Memory safety issues account for 40% of critical CVEs in CLI tools.
**Reasoning type:** Inductive
**Proof:** Analysis of 1,200 CVEs in popular CLI tools (2019-2024), consistent
with Mozilla's finding that 70% of Firefox CVEs were memory safety issues.
**Why it matters:** A single memory safety bug in a pipeline orchestration tool
can corrupt production data.

**Claim:** Rust's binary size and startup time are competitive with Go since 1.65.
**Reasoning type:** Deductive
**Proof:** Rust 1.65+ produces binaries with 8.1ms median startup; Go averages 5ms.
The 3ms difference is imperceptible in practice.
**Why it matters:** Undermines Advocate-A's primary differentiator.

### Counterargument Acknowledgment
Rust has a steeper learning curve. Our team would need 3-6 months to reach
proficiency. However, this is a one-time cost that pays dividends for years.

### Self-Assessment
| Dimension | Score | Notes |
|-----------|-------|-------|
| Hypothesis clarity | 9 | Clear security framing |
| Evidence strength | 9 | CVE data is compelling |
""",
        },
        {
            "filename": "submission-advocate-c.md",
            "text": """\
# Submission: Advocate-C

### Hypothesis
Neither Rust nor Go is optimal in isolation. The team should use Go for the
CLI shell and orchestration layer, with Rust for performance-critical inner loops
via FFI. This hybrid approach achieves both rapid development and maximum performance.

### Evidence

**Claim:** A hybrid Go+Rust architecture reduces time-to-market by 40% vs pure Rust.
**Reasoning type:** Abductive
**Proof:** Three case studies from comparable-sized teams (8-12 engineers) switching
from Python to Go+Rust: Vercel's CLI, Cloudflare's Argo tunnel, and Fly.io's flyctl.
**Why it matters:** Time-to-market is a real constraint; the team ships in 6 months.

**Claim:** FFI overhead between Go and Rust is under 200 nanoseconds per call.
**Reasoning type:** Deductive
**Proof:** CGO call overhead benchmark: 171ns average on Apple M2, 203ns on x86_64.
**Why it matters:** At this overhead, even 10,000 cross-boundary calls add only 2ms.

### Counterargument Acknowledgment
The hybrid approach adds architectural complexity. However, this complexity
is bounded and well-understood; it does not compound over time.

### Self-Assessment
| Dimension | Score | Notes |
|-----------|-------|-------|
| Hypothesis clarity | 8 | May seem like fence-sitting |
| Evidence strength | 8 | Case study evidence is strong |
""",
        },
    ],
    "challenges": [
        {
            "filename": "challenge-by-advocate-a.md",
            "text": """\
# Challenges by Advocate-A

### Challenge to Advocate-B

**On their claim that memory issues cause 40% of CLI CVEs:**
Your CVE analysis conflates server-side tools with CLI tools. Show me the
CLI-specific breakdown. The 40% figure appears to be extrapolated from server CVEs.

**On their evidence about Rust startup time:**
8.1ms vs 5ms is a 62% difference, not negligible. For a tool used 500 times per
day per developer, that is 1.5 seconds of extra wait time daily. Over a year:
9 minutes per developer.

**The weakest link:** Your learning curve estimate of 3-6 months is optimistic.
Industry data shows 12-18 months for meaningful Rust proficiency in production.

**The crux:** If Rust's startup time is not actually competitive with Go, does
the security argument justify the productivity cost?

### Challenge to Advocate-C

**On the hybrid architecture claim:**
Fly.io's flyctl is not purely Go+Rust — it is Go with select C bindings. You
are mischaracterizing your own case study evidence.

**The crux:** Can a team without Rust experience realistically maintain a
Go+Rust FFI boundary in production?

### My position holds because:
Go's 5ms startup and mature ecosystem represent a complete, ship-now solution.
""",
        },
        {
            "filename": "challenge-by-advocate-b.md",
            "text": """\
# Challenges by Advocate-B

### Challenge to Advocate-A

**On their claim that Go's ecosystem is more mature:**
Maturity of download count does not equal fitness for our use case. clap (Rust's
CLI library) has a richer feature set than cobra for argument parsing as of 2024.

**On the GC pause claim:**
Sub-millisecond GC pauses are average-case. P99 pauses in Go 1.21 reach 4-8ms
under memory pressure — unacceptable for a pipeline orchestration tool that may
run during high-memory operations.

**The crux:** If GC pauses under memory pressure exceed 5ms, does Go's startup
advantage survive real-world conditions?

### Challenge to Advocate-C

**On the hybrid approach:**
The FFI boundary is a correctness hazard. Go's garbage collector cannot track
Rust-owned memory; you need manual lifetime management at the boundary.
This reintroduces exactly the class of bugs that Rust was supposed to prevent.

**The crux:** Does the hybrid approach inherit Go's GC risks without gaining
the full safety benefits of Rust?

### My position holds because:
Memory safety is a non-negotiable requirement for production pipeline tooling.
""",
        },
    ],
    "debate_rounds": {
        1: [
            {
                "filename": "debate-round-1-advocate-a.md",
                "text": """\
# Debate Round 1: Advocate-A

### Responding to challenges from Advocate-B

**Challenge: "GC pauses under memory pressure reach 4-8ms"**
**My response: CONCEDE**
Advocate-B is correct that P99 GC pauses in Go 1.21 reach 4-8ms under memory
pressure. I was citing average-case numbers. This is a real weakness in my argument.
However, pipeline orchestration tools can mitigate this through worker pool sizing
and explicit GC tuning (GOGC=off + runtime.GC() at safe points).

**Challenge: "clap has a richer feature set than cobra"**
**My response: DEFEND**
Feature richness is not the same as ecosystem maturity. cobra has 25M downloads
and is used by Docker, Kubernetes, and GitHub CLI. This network effect means more
tutorials, more Stack Overflow answers, and faster onboarding for our team.
I stand on ecosystem maturity, not feature count.

### Position After This Round
**REFINED**: I concede the P99 GC pause point. Go is still the right choice,
but the GC argument needs qualification: teams must explicitly tune GC for
memory-intensive operations. My core claim about startup time and ecosystem
maturity stands.

Position stability: 2
""",
            },
            {
                "filename": "debate-round-1-advocate-b.md",
                "text": """\
# Debate Round 1: Advocate-B

### Responding to challenges from Advocate-A

**Challenge: "40% CVE figure conflates server and CLI tools"**
**My response: CONCEDE**
This is a fair methodological challenge. The 40% figure is from a mixed dataset.
CLI-specific CVE data shows 28%, not 40%. I was over-claiming. My hypothesis
stands but the magnitude of the risk is smaller than I stated.

**Challenge: "12-18 months for Rust proficiency, not 3-6"**
**My response: DEFEND**
The 12-18 month figure applies to systems-programming Rust (OS development, drivers).
For application-level Rust — which is what CLI tooling requires — Google's internal
data and the Rust Foundation survey show 4-6 months to productive contribution.
I stand on the 3-6 month estimate for this use case.

### Position After This Round
**REFINED**: I correct the CVE statistic to 28% for CLI-specific tools. The
memory safety argument remains compelling at 28%. My learning curve estimate
remains 3-6 months for application-level Rust.

Position stability: 2
""",
            },
            {
                "filename": "debate-round-1-advocate-c.md",
                "text": """\
# Debate Round 1: Advocate-C

### Responding to challenges from Advocate-A

**Challenge: "flyctl mischaracterized as Go+Rust"**
**My response: CONCEDE**
Advocate-A is correct. flyctl is Go + C bindings, not Go + Rust. I misread
the source. I withdraw flyctl as a case study. My remaining case studies
(Vercel CLI and Cloudflare Argo) still support the hybrid approach.

**Challenge: "Can the team maintain an FFI boundary?"**
**My response: REVISE**
On reflection, the FFI complexity risk is real and I was too optimistic. Given
our team's current skill set, I am revising my recommendation: use Go for the
entire CLI tool initially, with a documented extension point for Rust modules
once the team builds Rust proficiency over 12 months. This is a sequenced
adoption path, not a simultaneous hybrid.

### Position After This Round
**REVISED**: My recommendation is now sequenced adoption: Go-first CLI,
documented Rust extension points, Rust modules added in Year 2.
This preserves the long-term performance benefits while respecting current
team capabilities.

Position stability: 4
""",
            },
        ]
    },
    "judgments": [
        {
            "filename": "cardinal-judgment-cardinal-a.md",
            "text": """\
# Cardinal Judgment: Cardinal-A (Justice)

## Summary of Positions

Advocate-A recommends Go for its 5ms startup time and mature ecosystem (cobra, 25M downloads).
After debate, conceded P99 GC pauses reach 4-8ms under memory pressure.

Advocate-B recommends Rust for its memory safety guarantees.
Corrected CVE statistic from 40% to 28% (CLI-specific). Stood firm on learning curve.

Advocate-C initially recommended a hybrid Go+Rust approach but revised
to a sequenced adoption path (Go first, Rust extensions in Year 2) after
acknowledging FFI complexity risks.

## Debate Performance

| Advocate | Defended Well | Conceded When Right | Changed Mind | Overall |
|----------|--------------|---------------------|--------------|---------|
| Advocate-A | Yes (ecosystem) | Yes (GC pauses) | No | Strong |
| Advocate-B | Yes (learning curve) | Yes (CVE count) | No | Strong |
| Advocate-C | No (flyctl) | Yes (flyctl, FFI) | Yes | Mixed |

## Fact-Check Results

| Advocate | Claim | Verdict |
|----------|-------|---------|
| Advocate-A | Go startup: 3.2ms average | ✓ Verified — consistent with published benchmarks |
| Advocate-A | cobra: 25M downloads | ✓ Verified |
| Advocate-B | CLI CVEs with memory issues: 28% (revised) | ✓ Plausible — within range of published analyses |
| Advocate-C | FFI overhead: 171-203ns | ✓ Verified — consistent with CGO documentation |

## Verdict

SYNTHESIZE: Adopt Advocate-C's revised position (Go-first with documented
Rust extension points). Advocate-A's ecosystem evidence is strong; Advocate-B's
security concerns are real but manageable at 28% CLI CVE rate with Go's improving
memory safety tooling (e.g., address sanitizer, fuzzing in Go 1.21).

## Judge's Note

This was a high-quality deliberation. Advocate-B's willingness to correct
the CVE figure from 40% to 28% demonstrates intellectual honesty under pressure.
Advocate-C's position revision from hybrid to sequenced was the most substantive
change and ultimately produced the most practical recommendation.
""",
        }
    ],
    "fresh_eyes": None,
    "alias_map": {
        "Advocate-A": {"model": "Gemini 2.5 Pro", "provider": "Google"},
        "Advocate-B": {"model": "Claude Sonnet", "provider": "Anthropic"},
        "Advocate-C": {"model": "DeepSeek V3", "provider": "DeepSeek (Together AI)"},
    },
    "cardinal_alias_map": {
        "Cardinal-A": {"model": "Qwen 3.5 397B", "provider": "Qwen (Together AI)", "role": "bishop"},
    },
}


def build_demo_arguments() -> list[ArgumentObject]:
    """Build hardcoded ArgumentObjects from the demo session data."""
    return [
        # Submissions
        ArgumentObject(
            speaker_alias="Advocate-A",
            claim_text="Go is the right choice for our CLI tool because it offers 5ms average startup time, goroutines for concurrency, and a mature ecosystem through cobra and viper.",
            evidence_cited="Go benchmarks show 3.2ms average cold start. cobra has 25M+ GitHub downloads. 68% developer satisfaction in CLI-focused surveys.",
            event_type="SUBMISSION",
            source_anchor="submission-advocate-a.md#L1-L30",
            round_number=0,
            position_stability=None,
        ),
        ArgumentObject(
            speaker_alias="Advocate-B",
            claim_text="Rust is the right choice because memory safety without garbage collection prevents the class of vulnerabilities that account for 40% of critical CVEs in CLI tools over the past five years.",
            evidence_cited="Analysis of 1,200 CVEs in popular CLI tools (2019-2024). Mozilla's finding: 70% of Firefox CVEs were memory safety issues.",
            event_type="SUBMISSION",
            source_anchor="submission-advocate-b.md#L1-L30",
            round_number=0,
            position_stability=None,
        ),
        ArgumentObject(
            speaker_alias="Advocate-C",
            claim_text="Neither language alone is optimal. A hybrid Go plus Rust approach using Go for the shell and Rust for performance-critical inner loops via FFI achieves both rapid development and maximum performance.",
            evidence_cited="Three case studies: Vercel CLI, Cloudflare Argo tunnel, Fly.io flyctl. FFI overhead: 171ns on Apple M2, 203ns on x86-64.",
            event_type="SUBMISSION",
            source_anchor="submission-advocate-c.md#L1-L35",
            round_number=0,
            position_stability=None,
        ),
        # Challenges
        ArgumentObject(
            speaker_alias="Advocate-A",
            claim_text="The 40% CVE figure appears to conflate server-side tools with CLI tools specifically. The CLI-specific breakdown is not shown.",
            evidence_cited="Challenge to Advocate-B's CVE methodology.",
            event_type="CHALLENGE",
            source_anchor="challenge-by-advocate-a.md#L1-L20",
            round_number=0,
            position_stability=None,
        ),
        ArgumentObject(
            speaker_alias="Advocate-A",
            claim_text="Fly.io's flyctl is Go with C bindings, not Go plus Rust. The case study is mischaracterized.",
            evidence_cited="Direct challenge to Advocate-C's case study evidence.",
            event_type="CHALLENGE",
            source_anchor="challenge-by-advocate-a.md#L25-L40",
            round_number=0,
            position_stability=None,
        ),
        ArgumentObject(
            speaker_alias="Advocate-B",
            claim_text="Go's P99 GC pauses reach 4 to 8 milliseconds under memory pressure in Go 1.21, which is unacceptable for a pipeline orchestration tool that may run during high-memory operations.",
            evidence_cited="GC profiling data showing average versus P99 pause times diverge significantly under memory pressure.",
            event_type="CHALLENGE",
            source_anchor="challenge-by-advocate-b.md#L1-L20",
            round_number=0,
            position_stability=None,
        ),
        # Debate: Concessions
        ArgumentObject(
            speaker_alias="Advocate-A",
            claim_text="Conceding: Advocate-B is correct that P99 GC pauses reach 4 to 8 milliseconds under memory pressure. The earlier argument cited average-case numbers. Teams must explicitly tune GC for memory-intensive operations.",
            evidence_cited="Acknowledges the distinction between average and P99 GC pause times in Go 1.21.",
            event_type="CONCEDE",
            source_anchor="debate-round-1-advocate-a.md#L5-L15",
            round_number=1,
            position_stability=2,
        ),
        ArgumentObject(
            speaker_alias="Advocate-B",
            claim_text="Conceding: The 40% CVE figure is from a mixed dataset. CLI-specific CVE data shows 28 percent, not 40 percent. The magnitude of the risk is smaller than originally stated.",
            evidence_cited="Corrected CVE analysis limited to CLI-specific tools.",
            event_type="CONCEDE",
            source_anchor="debate-round-1-advocate-b.md#L5-L12",
            round_number=1,
            position_stability=2,
        ),
        ArgumentObject(
            speaker_alias="Advocate-C",
            claim_text="Conceding: flyctl is Go plus C bindings, not Go plus Rust. The flyctl case study is withdrawn.",
            evidence_cited="Acknowledges misreading of flyctl's architecture.",
            event_type="CONCEDE",
            source_anchor="debate-round-1-advocate-c.md#L5-L10",
            round_number=1,
            position_stability=4,
        ),
        # Debate: Defend
        ArgumentObject(
            speaker_alias="Advocate-A",
            claim_text="Defending: Feature richness is not the same as ecosystem maturity. cobra is used by Docker, Kubernetes, and GitHub CLI. That network effect means more tutorials, more community resources, and faster onboarding for the team.",
            evidence_cited="cobra adoption by Docker, Kubernetes, GitHub CLI. Stack Overflow answer count comparison.",
            event_type="DEFEND",
            source_anchor="debate-round-1-advocate-a.md#L18-L28",
            round_number=1,
            position_stability=2,
        ),
        ArgumentObject(
            speaker_alias="Advocate-B",
            claim_text="Defending: The 12 to 18 month proficiency timeline applies to systems-programming Rust such as OS development. Application-level Rust for CLI tooling shows 4 to 6 months to productive contribution per Google's internal data and the Rust Foundation survey.",
            evidence_cited="Google internal Rust adoption data. Rust Foundation developer survey on time-to-productivity.",
            event_type="DEFEND",
            source_anchor="debate-round-1-advocate-b.md#L16-L25",
            round_number=1,
            position_stability=2,
        ),
        # Debate: Revise
        ArgumentObject(
            speaker_alias="Advocate-C",
            claim_text="Revising position: Given the team's current skill set, the recommendation is now sequenced adoption: Go for the full CLI initially, with documented Rust extension points, and Rust modules added in Year 2 as the team builds proficiency.",
            evidence_cited="Acknowledges FFI complexity risk and team skill constraints. Preserves long-term performance path.",
            event_type="REVISE",
            source_anchor="debate-round-1-advocate-c.md#L14-L28",
            round_number=1,
            position_stability=4,
        ),
        # Verdict
        ArgumentObject(
            speaker_alias="Cardinal-A",
            claim_text="Synthesize: Adopt Advocate-C's revised position of Go-first development with documented Rust extension points. Advocate-A's ecosystem evidence is strong. Advocate-B's security concerns are real but manageable at 28 percent CLI CVE rate combined with Go's improving memory safety tooling.",
            evidence_cited="Go startup benchmarks verified at 3.2ms average. cobra downloads verified. CLI CVE rate at 28% is within published ranges. FFI overhead of 171 to 203 nanoseconds verified against CGO documentation.",
            event_type="VERDICT",
            source_anchor="cardinal-judgment-cardinal-a.md#L1-L50",
            round_number=99,
            position_stability=None,
        ),
        # Dissent (demo: Advocate-B held firm on Rust but was not accepted)
        ArgumentObject(
            speaker_alias="Advocate-B",
            claim_text="Dissenting: The majority underweights the long-term cost of memory safety vulnerabilities. Choosing Go-first with Rust as an afterthought means the team will ship with a 28 percent CVE surface area that is entirely preventable. The learning curve argument is a one-time cost; the security debt is permanent.",
            evidence_cited="CLI-specific CVE rate of 28%. Rust Foundation survey data on 4-6 month ramp time for application-level Rust. Google internal adoption data.",
            event_type="DISSENT",
            source_anchor="dissent-advocate-b.md#L1-L30",
            round_number=100,
            position_stability=2,
        ),
    ]


def run_demo_mode(act_count: int, progress: Progress) -> Path:
    """
    Generate a complete screenplay from built-in sample data.
    Writes output to a temporary directory and returns its path.
    """
    progress.phase(0, "Demo mode — generating screenplay from built-in sample data...")

    # Use TRIBUNAL_OUTPUT_DIR if set, otherwise fall back to temp dir
    output_base = os.environ.get("TRIBUNAL_OUTPUT_DIR", "")
    session_id = f"tribunal-demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if output_base:
        tmp_dir = Path(output_base) / session_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="tribunal-demo-"))

    # Write demo session files to the temp dir for provenance
    for sub in DEMO_SESSION_CONTENT["submissions"]:
        (tmp_dir / sub["filename"]).write_text(sub["text"], encoding="utf-8")
    for chal in DEMO_SESSION_CONTENT["challenges"]:
        (tmp_dir / chal["filename"]).write_text(chal["text"], encoding="utf-8")
    for rnum, rfiles in DEMO_SESSION_CONTENT["debate_rounds"].items():
        for rfile in rfiles:
            (tmp_dir / rfile["filename"]).write_text(rfile["text"], encoding="utf-8")
    for j in DEMO_SESSION_CONTENT["judgments"]:
        (tmp_dir / j["filename"]).write_text(j["text"], encoding="utf-8")
    (tmp_dir / "briefing.md").write_text(DEMO_SESSION_CONTENT["briefing"], encoding="utf-8")
    (tmp_dir / "alias-map.json").write_text(
        json.dumps(DEMO_SESSION_CONTENT["alias_map"], indent=2), encoding="utf-8"
    )
    (tmp_dir / "cardinal-alias-map.json").write_text(
        json.dumps(DEMO_SESSION_CONTENT["cardinal_alias_map"], indent=2), encoding="utf-8"
    )

    # Build argument objects from demo data
    demo_args = build_demo_arguments()
    progress.info(f"Using {len(demo_args)} pre-built argument objects (no LLM extraction in demo).")

    # Run validation pass (rule-based) on demo content
    validation_results = run_validation_pass(demo_args, DEMO_SESSION_CONTENT, progress)
    validated_args = apply_validation_flags(demo_args, validation_results)

    # Run dramatization pass (uses LLM)
    screenplay_text = run_dramatization_pass(
        validated_args, DEMO_SESSION_CONTENT, act_count, session_id, progress
    )

    # Write outputs
    screenplay_path = write_screenplay_md(
        screenplay_text, tmp_dir, session_id, act_count, len(validated_args)
    )
    voice_script = build_voice_script(
        screenplay_text, DEMO_SESSION_CONTENT, session_id, act_count
    )
    voice_script_path = write_voice_script_json(voice_script, tmp_dir)

    progress.session_done(str(tmp_dir))
    progress.info(f"screenplay.md → {screenplay_path}")
    progress.info(f"voice-script.json → {voice_script_path} ({len(voice_script['lines'])} lines)")

    return tmp_dir


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    session_dir: Path,
    act_count: int,
    progress: Progress,
) -> None:
    """
    Run the full three-pass pipeline on a Tribunal session directory.
    Writes screenplay.md and voice-script.json into the session directory.
    """
    if not session_dir.exists():
        progress.error(f"Session directory not found: {session_dir}")
        sys.exit(1)

    # Derive session ID from directory name
    session_id = session_dir.name

    progress.session_start()
    progress.info(f"Processing session: {session_id}")

    # Discover and read session files
    files = discover_session_files(session_dir)
    session_content = read_session_files(files)

    if not session_content["briefing"]:
        progress.warn("No briefing.md found — screenplay may lack context.")
    if not session_content["submissions"]:
        progress.error("No submission files found. Cannot proceed.")
        sys.exit(1)

    progress.info(
        f"Discovered: {len(session_content['submissions'])} submissions, "
        f"{len(session_content['challenges'])} challenges, "
        f"{len(session_content['debate_rounds'])} debate round(s), "
        f"{len(session_content['judgments'])} judgment(s)."
    )

    # Pass 1: LLM Extraction (skip if manifest already exists)
    extraction_manifest = session_dir / "screenplay-extraction.json"
    if extraction_manifest.exists():
        progress.info(f"Resuming from existing extraction manifest → {extraction_manifest.name}")
        manifest_data = json.loads(extraction_manifest.read_text())
        from dataclasses import fields as dc_fields
        validated_args = [ArgumentObject(**{f.name: a.get(f.name) for f in dc_fields(ArgumentObject)}) for a in manifest_data["arguments"]]
        progress.info(f"Loaded {len(validated_args)} validated argument objects from manifest.")
    else:
        arguments = run_extraction_pass(session_content, progress)
        if not arguments:
            progress.error("Extraction produced zero argument objects. Cannot proceed.")
            sys.exit(1)

        # Pass 2: Validation
        validation_results = run_validation_pass(arguments, session_content, progress)
        validated_args = apply_validation_flags(arguments, validation_results)

        # Write extraction manifest for debugging/resume
        extraction_manifest.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "arguments": [asdict(a) for a in validated_args],
                    "validation": [asdict(v) for v in validation_results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        progress.info(f"Extraction manifest written → {extraction_manifest.name}")

    # Pass 3: Dramatization
    screenplay_text = run_dramatization_pass(
        validated_args, session_content, act_count, session_id, progress
    )

    # Write outputs
    screenplay_path = write_screenplay_md(
        screenplay_text, session_dir, session_id, act_count, len(validated_args)
    )
    voice_script = build_voice_script(
        screenplay_text, session_content, session_id, act_count
    )
    voice_script_path = write_voice_script_json(voice_script, session_dir)

    progress.session_done(str(session_dir))
    progress.info(f"screenplay.md → {screenplay_path}")
    progress.info(
        f"voice-script.json → {voice_script_path} "
        f"({len(voice_script['lines'])} lines, "
        f"{len(voice_script['characters'])} characters)"
    )

    # Print output paths to stdout for downstream consumption
    print(str(screenplay_path))
    print(str(voice_script_path))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Screenplay Generator — Converts Tribunal session directories into "
            "dramatic screenplays and TTS-ready voice-script manifests. "
            "Part of The Tribunal (github.com/mdm-sfo/conclave)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 screenplay_generator.py --session-dir ./conclave-sessions/tribunal-20260301-061125
  python3 screenplay_generator.py --session-dir ./tribunal-20260301-061125 --acts 3
  python3 screenplay_generator.py --demo
  python3 screenplay_generator.py --demo --acts 3
        """,
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        help="Path to a Tribunal session directory containing session files.",
    )
    parser.add_argument(
        "--acts",
        type=int,
        choices=[3, 4],
        default=4,
        help="Number of acts in the screenplay (default: 4).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Generate a screenplay from built-in sample data. "
            "No --session-dir required. Useful for testing the pipeline."
        ),
    )
    parser.add_argument(
        "--tts",
        action="store_true",
        help=(
            "After generating the screenplay, run the TTS pipeline to "
            "produce an MP3 audio file. Requires ELEVENLABS_API_KEY "
            "and ffmpeg."
        ),
    )
    args = parser.parse_args()

    if not args.demo and not args.session_dir:
        parser.error("Either --session-dir or --demo is required.")

    # Build a progress reporter
    if args.demo:
        session_label = "demo"
    else:
        session_label = args.session_dir.name if args.session_dir else "demo"

    progress = Progress(session_label, "screenplay")

    if args.demo:
        output_dir = run_demo_mode(args.acts, progress)
        print(f"\nDemo output directory: {output_dir}")
        print(f"\n  \u2705 Session saved to: {Path(output_dir).resolve()}/")
    else:
        output_dir = args.session_dir
        run_pipeline(args.session_dir, args.acts, progress)

    # Optional TTS pass
    if args.tts:
        voice_script_path = Path(output_dir) / "voice-script.json"
        if not voice_script_path.exists():
            progress.info("No voice-script.json found — skipping TTS.")
        elif not os.environ.get("ELEVENLABS_API_KEY"):
            progress.info("ELEVENLABS_API_KEY not set — skipping TTS.")
        else:
            # Import and run TTS pipeline
            try:
                from tts_pipeline import run_pipeline as run_tts
                progress.phase(4, "TTS — generating audio from voice-script...")
                tts_exit = run_tts(
                    input_path=voice_script_path,
                    output_path=None,  # auto-names based on session_id
                    voice_map_path=None,
                    add_tags=True,
                    dry_run=False,
                )
                if tts_exit != 0:
                    progress.info("TTS pipeline finished with errors.")
            except ImportError:
                progress.info(
                    "tts_pipeline.py not found — run it separately: "
                    f"python3 tts_pipeline.py --input {voice_script_path}"
                )


if __name__ == "__main__":
    main()
