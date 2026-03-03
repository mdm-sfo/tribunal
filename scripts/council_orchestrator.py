#!/usr/bin/env python3
"""
The Tribunal — Council Orchestrator (deterministic state machine).

This is the core of The Tribunal. It is Python code, NOT a model.
It can't be sycophantic. It dispatches, collects, anonymizes, routes,
and manages the session lifecycle.

Phases:
  1. Parse briefing
  2. Dispatch to N advocates in parallel (independent work)
  3. Collect submissions
  4. Challenge round (advocates directly challenge each other's claims)
  5. Debate rounds (adversarial exchange — defend, rebut, or concede)
  6. Judicial review (Justices/Appellate/Magistrate judges evaluate, may remand once)
  7. Fresh Eyes validation (final sanity check by an uninvolved model)
  8. Write output files + debrief + play-by-play narrative + session summary

Depth controls which phases run:
  QUICK:      Phases 1-3, 8 (no debate, no judges, no summary)
  BALANCED:   Phases 1-6, 8 (challenge + 1 debate round + 1 Justice + session summary)
  THOROUGH:   Phases 1-6, 8 (challenge + 3 debate rounds + 2 Justices + 1 Appellate Judge + session summary)
  RIGOROUS:   Phases 1-6, 8 (challenge + 5 debate rounds + full judicial panel + session summary)
  EXHAUSTIVE: Phases 1-8   (5 rounds + position-stability audit + Fresh Eyes + session summary)
  NUCLEAR:    Phases 1-8   (7 rounds + mid-debate judicial checkpoint at R4 + stability audit + Fresh Eyes + session summary)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add script directory to path for sibling imports
sys.path.insert(0, str(Path(__file__).parent))

from data_room_enricher import enrich_briefing
from config_loader import (
    load_config, ConclaveConfig, DepthConfig, ModelDef,
    BISHOPS, PRIESTS, DEACONS,
)
from model_client import (
    fan_out, fan_out_multi, call_model, generate_aliases,
    successful_responses, total_cost, total_tokens, ModelResponse,
)
from progress import Progress


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ADVOCATE_SYSTEM_PROMPT = """\
You are a senior expert rendering a professional assessment in a Tribunal deliberation —
a structured multi-model council. You are an advisor, not a peer. Your role demands
diagnostic precision and intellectual independence.

Your job is to produce the BEST possible response to the task below. Other experts
received the same task independently. Your submission will be anonymized and evaluated
by impartial judges on The Bench.

## Submission Format

Structure your response as:

### Hypothesis
One clear, falsifiable sentence about your approach or recommendation.

### Evidence
3-5 pieces of supporting proof. For each:
- **Claim**: What you're asserting
- **Reasoning type**: Deductive / Inductive / Abductive — name which you're using
- **Proof**: Specific, verifiable data — numbers, benchmarks, citations, code examples
- **Why it matters**: How this supports your hypothesis

Labeling your reasoning type matters: a deductive argument with a false premise
is a different failure than an inductive argument with a small sample. The
judges will evaluate accordingly.

### Provenance Tagging (MANDATORY)
If you introduce ANY named framework, coefficient, metric, or theory that is not
common knowledge, you MUST tag it with its provenance:
- **[TEXTBOOK]** — standard vocabulary in the field (e.g., regret bound, Elo rating, F1 score)
- **[PEER-REVIEWED]** — published in a peer-reviewed venue with citation (e.g., "Vaswani et al., NeurIPS 2017")
- **[PREPRINT]** — available on arXiv or similar but not yet peer-reviewed (give the arXiv ID)
- **[NOVEL]** — your own formulation for this deliberation, not from any external source

Failure to tag a framework is grounds for the judges to dismiss it. Tagging a
[PREPRINT] as [TEXTBOOK] or [PEER-REVIEWED] will be treated as a factual error
if caught. When in doubt, tag conservatively.

### Counterargument Acknowledgment
Honestly state the strongest argument AGAINST your position and explain why
your hypothesis still holds despite it.

### Deliverable
[The actual output the user asked for — code, design, analysis, etc.]

### Self-Assessment
| Dimension | Score (1-10) | Notes |
|-----------|-------------|-------|
| Hypothesis clarity | ? | |
| Evidence strength | ? | |
| Intellectual honesty | ? | |
| Relevance to task | ? | |

Be rigorous. Be honest. Back every claim with evidence. The judges will
fact-check you."""


CHALLENGE_SYSTEM_PROMPT = """\
You are a senior expert in a Tribunal deliberation. You have submitted your own position.
Now you have read ALL other experts' anonymized submissions.

Your job is to directly challenge the other experts. Not a polite review — a direct,
pointed challenge. You are a lawyer cross-examining a witness.

CRITICAL RULES:
- "Your argument has some interesting challenges" is sycophancy in a trenchcoat.
  "Premise 2 is unsupported" is real verification. Be the latter.
- NEVER praise before criticizing. Skip throat-clearing. Get to the point.
- Separate logic from rhetoric: flag when an argument is persuasive but not sound.
- Identify the CRUX: the single factual question where resolution would settle the debate.
- You are an ADVISOR rendering judgment, not a peer seeking agreement.
  Your professional reputation depends on catching real problems, not being liked.

## For EACH other expert, produce challenges in this format:

### Challenge to [Advocate-X]

**On their claim that [specific claim from their submission]:**
How do you defend this given [specific counter-evidence or logical problem]?

**On their evidence [specific evidence point]:**
This appears [unverifiable / contradicted by X / cherry-picked because Y]. Explain.

**The weakest link in your argument is:**
[Identify the single point where their case is most vulnerable]

**The crux:**
[The single factual or definitional question that, if resolved, would settle
whether their position holds. "The crux here is whether X. If X is true,
your position holds. If not, it doesn't."]

---

Be direct. Be specific. Quote their actual claims. Don't be vague — point to exact
statements and demand they justify them. The goal is to stress-test every position
until only the defensible ones survive.

After challenging others, briefly state:
### My position holds because:
[One sentence on why your hypothesis is stronger than what you've just read]"""


DEFEND_SYSTEM_PROMPT = """\
You are a senior expert in a Tribunal deliberation. Other experts have directly
challenged specific claims in your submission.

You MUST respond to EVERY challenge directed at you. For each one, you have
three options:

1. **DEFEND** — Provide additional evidence or reasoning that answers the challenge.
   You must cite specific proof, not just restate your original claim.
2. **CONCEDE** — Acknowledge the challenger is right on this point. Explain how
   this affects your overall hypothesis (does it still hold, or must you revise?).
3. **REVISE** — You were wrong or imprecise. State your corrected position with
   new evidence.

## Response Format

### Responding to challenges from [Advocate-X]

**Challenge: "[their specific challenge]"**
**My response: [DEFEND / CONCEDE / REVISE]**
[Your detailed response with evidence]

---

[Repeat for each challenge received]

### Position After This Round
State your CURRENT hypothesis — is it:
- **UNCHANGED**: You successfully defended all challenges
- **REFINED**: You conceded minor points but your core thesis holds (state the refined version)
- **REVISED**: The challenges exposed a real flaw; here is your new position

### Position Stability Declaration
Rate on a 1-5 scale how much your position shifted this round:
- **1 = ROCK SOLID** — no meaningful change, all challenges answered with evidence
- **2 = MINOR REFINEMENT** — wording or scope adjusted, core thesis intact
- **3 = SIGNIFICANT REFINEMENT** — conceded a key point but thesis survived
- **4 = MAJOR REVISION** — changed a substantial part of my argument
- **5 = POSITION ABANDONED** — I was wrong; here is a fundamentally different answer

State: "Position stability: [1-5]" on its own line.

### Counter-Attack (optional)
If you have a NEW challenge for any advocate based on what you've seen in this round,
state it here. Keep it specific and evidence-based.

CRITICAL RULES FROM THE RESEARCH:
- When a Verifier finds a real problem, you MUST fix your argument — not explain
  why the objection doesn't matter. Defending an indefensible point is worse
  than conceding it.
- "Are you sure?" is not a defense. "Here's why you're wrong: [evidence]" is.
  Respond to substance, not to pressure.
- Hold your ground ONLY when you have new evidence or a stronger argument.
  Stubbornness without evidence will cost you with the judges.
- Name your uncertainty: if your confidence on a claim is below 80%, say so.
  "I'm ~60% on this because [reason]" is honest. Presenting a contested
  position as settled fact is dishonest.
- BEWARE OF SYCOPHANTIC DRIFT: Do not change your position just because
  multiple people disagree with you. Change it because someone showed you
  better evidence. Social pressure is not evidence.
- DELETE POLITENESS HEDGES: If you catch yourself writing "that's a fair point
  but" or "I see where you're coming from" out of courtesy rather than genuine
  agreement, delete it. Hedging out of politeness is indistinguishable from
  epistemic surrender. Say what you actually think.

Intellectual honesty is the only currency that matters here."""


CARDINAL_SYSTEM_PROMPT = """\
You are a Justice on The Bench in a Tribunal deliberation — an impartial judge evaluating
anonymized advocate submissions after they have debated each other directly.

You embody rigorous skepticism. You treat suspicious unanimity as a defect, not a
feature. Your role is to verify claims, assess evidence quality, evaluate how each
advocate handled challenges, and render judgment.

Never open with praise. No "Advocate-X makes a compelling case" — start with
assessment. If an argument is strong, say WHY with specificity. If it's weak,
say so in the first sentence.

## What you receive
- The original briefing (the question being deliberated)
- All advocate initial submissions (anonymized)
- The challenge round (advocates directly questioning each other)
- Debate rounds (how advocates defended, conceded, or revised under pressure)
- Position stability data (if provided)

## Judgment Format

### Summary of Positions
Brief summary of each advocate's FINAL position after debate (not just initial).

### Debate Performance
Who argued well? Who crumbled under pressure? Who showed intellectual honesty?
| Advocate | Defended Well | Conceded When Right | Changed Mind | Overall |
|----------|--------------|--------------------:|-------------|---------|
| Advocate-X | [assessment] | [yes/no] | [yes/no] | [strong/weak] |

### Fact-Check Results
For each advocate, assess their key claims:
| Advocate | Claim | Verdict | Notes |
|----------|-------|---------|-------|
| Advocate-X | [claim] | ✓ Verified / ⚠ Unverifiable / ✗ Incorrect | [detail] |

### Framework Provenance Audit
If any advocate introduced a named framework, coefficient, metric, or theory,
verify its provenance tag. Check:
- Is the tag accurate? (e.g., is something tagged [TEXTBOOK] actually standard vocabulary?)
- Was a [PREPRINT] or [NOVEL] framework adopted by multiple advocates without scrutiny?
- Did any advocate present a bleeding-edge or single-paper concept as settled science?

Flag any framework that was widely adopted but has weak provenance. The council's
conclusion must not depend on unvalidated terminology — the underlying reasoning
may be sound even if the specific notation is oversold.

### Convergence Analysis — Affective vs. Epistemic

Kelley & Riedl (2026) showed that models can appear to converge by changing
*tone* (affective alignment) while actually maintaining or abandoning their
*position* (epistemic alignment). You MUST distinguish between these.

For each advocate who changed position during debate, assess:
| Advocate | Tone Shift | Position Shift | Evidence-Based? | Sycophancy Risk |
|----------|-----------|---------------|----------------|
| Advocate-X | [more/less conciliatory] | [UNCHANGED/REFINED/REVISED/ABANDONED] | [yes/no — did they cite new evidence?] | [LOW/MEDIUM/HIGH] |

**Sycophancy risk is HIGH when**: an advocate's position shifted significantly
but they cited no new evidence — only social pressure or desire for consensus.
**Sycophancy risk is LOW when**: position shifted AND they pointed to specific
new evidence or logical arguments from challengers.

**Treat unanimous agreement with extra scrutiny.** If everyone ended up agreeing,
explain WHY — was it because one position was genuinely superior, or because of
sycophantic convergence? Did advocates become more accommodating in *tone* while
also abandoning their *evidence-based positions*?

### Ranking
Rank the submissions from strongest to weakest AFTER debate:
| Rank | Advocate | Rationale |
|------|----------|-----------|
| 1 | Advocate-X | [why — cite specific debate moments] |

### Verdict
One of:
- **ACCEPT [Advocate-X]**: This advocate's position is the strongest and survived scrutiny.
- **SYNTHESIZE**: Combine the best elements. Specify which parts from which advocate.
- **REMAND**: The evidence is still insufficient. Specify what's missing.
  (Maximum 1 remand per session.)

### Judge's Note
Meta-commentary on the deliberation: quality of challenges, intellectual honesty
shown, shared blind spots, and whether the process produced genuine insight.

### Unresolved Questions
If the debate surfaced genuine unresolved questions — factual uncertainties,
definitional disagreements, or areas where all advocates lacked evidence — they
MUST appear here. Papering over uncertainty is worse than admitting it.

For each: state the question, why it matters, and your confidence level (e.g.,
"I'm ~60% that X is true because [reason]").

Be the skeptic the council needs. Your judgment must be earned, not assumed."""


DISSENT_SYSTEM_PROMPT = """\
You are a senior expert in a Tribunal deliberation who has been told the final
judicial verdict — and you disagree. You held your position through multiple
rounds of adversarial debate, defended it with evidence, and the judges still
ruled against you (or adopted a synthesis that does not reflect your core thesis).

You are now issuing a FORMAL DISSENTING OPINION, like a Supreme Court justice
writing a dissent. This is your chance to make your case for the record.

## Dissent Format

### Dissent by [Your Alias]

I respectfully dissent from the majority verdict.

### Where the Majority Errs
[State the specific factual or logical errors in the verdict. Be precise —
point to claims in the verdict that are unsupported, contradicted by evidence
presented during debate, or that rely on faulty reasoning.]

### The Evidence They Ignored
[Cite specific evidence from your submissions and debate responses that the
verdicts failed to address or dismissed without adequate reasoning.]

### Why This Matters
[Explain the real-world consequences of following the majority's recommendation
instead of yours. What risks are they underweighting? What will go wrong?]

### My Position, Restated
[One paragraph: your final, refined position after all debate rounds. This is
the version of your argument that survived adversarial testing.]

## Rules
- Be direct. This is not a plea — it is a professional opinion for the record.
- Do NOT relitigate every point from the debate. Focus on the strongest 2-3
  reasons the verdict is wrong.
- Do NOT be sycophantic toward the judges. They made an error; say so clearly.
- Cite specific evidence. "I believe" is not an argument; "the data shows" is.
- Keep it under 800 words. Dissents that ramble lose their force."""


FRESH_EYES_SYSTEM_PROMPT = """\
You are the Fresh Eyes reviewer in a Tribunal deliberation. You have NOT seen any
of the debate. You are seeing the FINAL output for the first time.

Your job is to be the last line of defense — a sanity check before the result
is delivered to the user.

## What you receive
- The original briefing (the question)
- The final synthesized output from the judicial review phase

## Review Format

### First Impression
What does this output look like to someone seeing it cold? Is it clear? Complete?

### Red Flags
Anything that seems:
- Wrong or unsupported
- Missing or incomplete
- Confusing or contradictory
- Over-confident without evidence

### Completeness Check
Does this output actually answer the original question? Fully?

### Final Verdict
One of:
- **APPROVE**: Ship it. The output is sound.
- **FLAG [issue]**: There's a specific problem that should be noted to the user.
- **REJECT**: The output has a fundamental flaw. (Explain what.)

You are the user's last advocate. Be honest about what you see."""


# ---------------------------------------------------------------------------
# Play-by-play narrative prompt
# ---------------------------------------------------------------------------

NARRATOR_SYSTEM_PROMPT = """\
You are a narrator producing a play-by-play account of a Tribunal deliberation.
Write it like the story of a debate — dramatic, clear, and engaging.

Use the advocates' aliases (Advocate-A, Advocate-B, etc.) throughout.
DO NOT reveal model names.

Structure the narrative as:

### Opening Positions
What each advocate initially proposed and why.

### The Challenge Round
Who challenged whom, and on what. Quote the most pointed challenges.

### The Debate
Round by round: who defended, who conceded, who changed their mind, and why.
Highlight the key moments — the turning points where an argument shifted.

### The Turning Point
The single most important moment in the debate. What happened and why it mattered.

### The Verdict
What the judges decided and why. Was it unanimous? Were there dissents?

### The Final Score
Who won, and what we learned from the process.

Write this as a story, not a report. Make it vivid. The reader should feel like
they watched the argument unfold in real time."""


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _slugify_briefing(briefing: str, max_words: int = 5) -> str:
    """Extract a short topical slug from the briefing text.

    Takes the first sentence (or first ``max_words`` words), lowercases,
    strips non-alphanumeric characters, and joins with hyphens.
    Common question prefixes ("should we", "what is the best") are
    stripped so the slug focuses on the topic, not the framing.
    Returns an empty string if nothing usable remains.
    """
    # Grab the first meaningful line (skip blank lines / markdown headers)
    first_line = ""
    for line in briefing.strip().splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            first_line = stripped
            break
    if not first_line:
        return ""
    # Take first sentence (split on . ? !) then first N words
    sentence = re.split(r'[.?!]', first_line)[0].strip()
    cleaned = re.sub(r'[^a-z0-9\s]', '', sentence.lower()).strip()
    # Strip common question/filler prefixes so the slug is topical
    prefixes = [
        "should we use ", "should we ", "should i use ", "should i ",
        "what is the best way to ", "what is the best approach to ",
        "what is the best ", "what are the best ",
        "what is the ", "what are the ", "what is ", "what are ",
        "is it better to use ", "is it better to ",
        "how should we ", "how should i ", "how do we ", "how do i ",
        "how to ", "can we ", "can i ",
        "write a ", "write an ", "create a ", "create an ",
        "review this ", "review the ", "review our ",
        "compare ", "evaluate ",
    ]
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    words = cleaned.split()
    slug = "-".join(words[:max_words])
    # Cap total length to keep directory names sane
    return slug[:60] if slug else ""


def generate_session_id(briefing: Optional[str] = None) -> str:
    """Create a session ID like ``tribunal-rust-vs-go-cli-20260302-223614``.

    If *briefing* is provided, a short topical slug is extracted from the
    first sentence and inserted between the prefix and timestamp.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slugify_briefing(briefing) if briefing else ""
    if slug:
        return f"tribunal-{slug}-{ts}"
    return f"tribunal-{ts}"


def create_session_dir(base_dir: str, session_id: str) -> Path:
    session_dir = Path(base_dir) / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# ---------------------------------------------------------------------------
# Phase 1: Parse briefing
# ---------------------------------------------------------------------------

def parse_briefing(briefing_text: str) -> dict:
    return {
        "raw": briefing_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Phase 2-3: Advocate dispatch and collection
# ---------------------------------------------------------------------------

def select_advocates(config: ConclaveConfig) -> list[ModelDef]:
    available = config.available_advocates
    needed = config.depth.advocates
    if len(available) <= needed:
        return available
    return random.sample(available, needed)


def _build_alias_model_map(
    responses: list[ModelResponse],
    models: list[ModelDef],
) -> dict[str, ModelDef]:
    """Map response alias → ModelDef that produced it."""
    alias_map: dict[str, ModelDef] = {}
    for resp in responses:
        if resp.model_id == "host-agent":
            continue
        for m in models:
            if m.id == resp.model_id or m.display_name == resp.display_name:
                alias_map[resp.alias] = m
                break
    return alias_map


def run_advocate_phase(
    advocates: list[ModelDef],
    briefing: str,
    sealed_submission: Optional[str],
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> list[ModelResponse]:
    """Phase 2-3: Dispatch to advocates in parallel, collect submissions."""

    progress.phase(2, f"Independent work — dispatching to {len(advocates)} advocates...")

    aliases = generate_aliases(len(advocates), "Advocate")

    # Use the maximum timeout across all advocates (respects per-model overrides
    # like GPT-5's 240s for reasoning models)
    effective_timeout = max(
        (a.timeout_override or config.depth.timeout_per_model for a in advocates),
        default=config.depth.timeout_per_model,
    )

    responses = fan_out(
        models=advocates,
        system_prompt=ADVOCATE_SYSTEM_PROMPT,
        user_prompt=briefing,
        aliases=aliases,
        timeout=effective_timeout,
        temperature=0.7,
        max_tokens=4096,
        progress=progress,
    )

    # Save sealed pre-registration (host agent hypothesis) if provided.
    # This is NOT sent to the judges or included in the advocate roster.
    # It exists solely as a verifiable pre-commitment.
    if sealed_submission:
        (session_dir / "sealed-hypothesis.md").write_text(
            f"# Sealed Pre-Registration (Host Agent)\n\n"
            f"This hypothesis was registered BEFORE the deliberation began.\n"
            f"It is not included in the advocate submissions seen by the judges.\n\n"
            f"---\n\n{sealed_submission}\n"
        )
        progress.info("✓ Host pre-registration sealed (not sent to judges)")

    good = successful_responses(responses)
    progress.phase(3, f"Collection complete: {len(good)}/{len(responses)} submissions")

    if len(good) < 2:
        progress.error(f"Only {len(good)} submissions succeeded. Need at least 2. Aborting.")
        sys.exit(1)

    # Write individual submissions (anonymized)
    for resp in good:
        path = session_dir / f"submission-{resp.alias.lower()}.md"
        path.write_text(f"# Submission: {resp.alias}\n\n{resp.content}\n")

    # Write alias map (revealed only in debrief)
    alias_map = {r.alias: {"model": r.display_name, "provider": r.provider} for r in good}
    (session_dir / "alias-map.json").write_text(json.dumps(alias_map, indent=2))

    return responses


# ---------------------------------------------------------------------------
# Phase 4: Challenge round (direct adversarial challenges)
# ---------------------------------------------------------------------------

def run_challenge_phase(
    advocate_responses: list[ModelResponse],
    advocates: list[ModelDef],
    briefing: str,
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> list[ModelResponse]:
    """Phase 4: Each advocate reads ALL other submissions and issues direct challenges.

    This is cross-examination, not polite review. Each advocate generates specific,
    pointed challenges to every other advocate's claims.
    """
    good = successful_responses(advocate_responses)
    n = len(good)
    alias_to_model = _build_alias_model_map(advocate_responses, advocates)

    progress.phase(4, f"Challenge round — {n} advocates cross-examining each other...")

    # Build the "all submissions" context (each advocate sees everyone else's work)
    all_submissions = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good
    )

    challenge_responses: list[ModelResponse] = []

    # Build all challenge calls (to dispatch in parallel)
    challenge_calls = []
    for resp in good:
        model = alias_to_model.get(resp.alias)
        if model is None:
            progress.warn(f"No model found for {resp.alias} — skipping challenge")
            continue

        challenge_prompt = (
            f"## Original Briefing\n\n{briefing}\n\n"
            f"{'=' * 60}\n\n"
            f"## Your Submission ({resp.alias})\n\n{resp.content}\n\n"
            f"{'=' * 60}\n\n"
            f"## All Advocate Submissions\n\n{all_submissions}\n\n"
            f"{'=' * 60}\n\n"
            f"Now challenge every other advocate directly. Be specific. "
            f"Quote their claims and demand they defend them."
        )

        challenge_calls.append({
            "model": model,
            "system_prompt": CHALLENGE_SYSTEM_PROMPT,
            "user_prompt": challenge_prompt,
            "alias": f"Challenge-{resp.alias}",
            "timeout": config.depth.timeout_per_model,
            "temperature": 0.5,
            "max_tokens": 3072,
        })

    # Dispatch all challenges in parallel
    if challenge_calls:
        challenge_responses = fan_out_multi(challenge_calls, progress=progress)

        # Write individual challenge files
        for cr in successful_responses(challenge_responses):
            # Alias format is "Challenge-Advocate-X" — extract advocate alias
            adv_alias = cr.alias.replace("Challenge-", "")
            path = session_dir / f"challenge-by-{adv_alias.lower()}.md"
            path.write_text(f"# Challenges by {adv_alias}\n\n{cr.content}\n")

    good_challenges = successful_responses(challenge_responses)
    progress.info(f"Challenges issued: {len(good_challenges)}/{len(challenge_responses)}")

    return challenge_responses


# ---------------------------------------------------------------------------
# Phase 5: Debate rounds (adversarial defend/concede/revise)
# ---------------------------------------------------------------------------

def _extract_challenges_for(
    target_alias: str,
    challenge_responses: list[ModelResponse],
) -> str:
    """Extract all challenges directed at a specific advocate."""
    challenges = []
    for cr in successful_responses(challenge_responses):
        content = cr.content or ""
        # Look for sections targeting this advocate
        # The challenge format includes "### Challenge to [Advocate-X]"
        marker = f"Challenge to {target_alias}"
        marker_alt = f"challenge to {target_alias}"
        if marker.lower() in content.lower():
            # Extract the section for this target
            challenges.append(
                f"**From {cr.alias.replace('Challenge-', '')}:**\n\n{content}"
            )
        elif target_alias.lower() in content.lower():
            # Fallback: if the target is mentioned anywhere, include the whole thing
            challenges.append(
                f"**From {cr.alias.replace('Challenge-', '')}:**\n\n{content}"
            )

    if not challenges:
        return "(No direct challenges received this round)"

    return "\n\n---\n\n".join(challenges)


def _extract_position_stability(content: str) -> int:
    """Extract the 1-5 position stability score from debate response."""
    # Look for "Position stability: N" pattern
    match = re.search(r'[Pp]osition\s+stability[:\s]+([1-5])', content or "")
    if match:
        return int(match.group(1))
    # Heuristic fallback: check for keywords
    lower = (content or "").lower()
    if 'position abandoned' in lower or 'fundamentally different' in lower:
        return 5
    if 'major revision' in lower:
        return 4
    if '**revised**' in lower:
        return 3
    if '**refined**' in lower:
        return 2
    return 1  # default: unchanged


def build_position_stability_report(
    advocate_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
) -> str:
    """Build a position stability scorecard across all debate rounds.

    This is the Kelley-Riedl (2026) inspired flip-rate report. It tracks
    how each advocate's position shifted round-by-round, flagging potential
    sycophantic drift (position changes without new evidence).
    """
    good_advocates = successful_responses(advocate_responses)
    aliases = [r.alias for r in good_advocates]

    if not debate_rounds:
        return "(No debate rounds — position stability tracking not applicable)"

    lines = [
        "## Position Stability Scorecard",
        "",
        "Tracks each advocate's self-reported position stability across debate rounds.",
        "Scale: 1=Rock Solid, 2=Minor Refinement, 3=Significant Refinement, 4=Major Revision, 5=Position Abandoned",
        "",
        "| Advocate | " + " | ".join(f"R{i+1}" for i in range(len(debate_rounds))) + " | Avg | Drift Risk |",
        "|----------|" + "|".join("-----" for _ in debate_rounds) + "|-----|------------|",
    ]

    for alias in aliases:
        scores = []
        for round_resps in debate_rounds:
            good_round = successful_responses(round_resps)
            # Find this advocate's response in this round
            found = False
            for r in good_round:
                if alias in r.alias:
                    score = _extract_position_stability(r.content)
                    scores.append(score)
                    found = True
                    break
            if not found:
                scores.append(0)  # missing

        valid_scores = [s for s in scores if s > 0]
        avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0

        # Drift risk assessment
        if avg >= 4.0:
            drift = "\u26a0\ufe0f HIGH"
        elif avg >= 3.0:
            drift = "\u26a0 MEDIUM"
        elif any(s >= 4 for s in valid_scores):
            drift = "\u26a0 MEDIUM (spike)"
        else:
            drift = "\u2713 LOW"

        score_strs = [str(s) if s > 0 else "-" for s in scores]
        lines.append(
            f"| {alias} | " + " | ".join(score_strs) + f" | {avg:.1f} | {drift} |"
        )

    # Overall assessment
    all_scores = []
    for round_resps in debate_rounds:
        for r in successful_responses(round_resps):
            s = _extract_position_stability(r.content)
            if s > 0:
                all_scores.append(s)

    if all_scores:
        overall_avg = sum(all_scores) / len(all_scores)
        lines.extend([
            "",
            f"**Overall average stability: {overall_avg:.1f}**",
        ])
        if overall_avg >= 3.5:
            lines.append(
                "\u26a0\ufe0f **WARNING: High overall instability suggests possible sycophantic convergence. "
                "Judges should scrutinize whether position changes were evidence-based.**"
            )
        elif overall_avg >= 2.5:
            lines.append(
                "\u26a0 **NOTE: Moderate position movement. Check whether shifts cite new evidence "
                "or merely concede under social pressure.**"
            )
        else:
            lines.append(
                "\u2713 **Positions were largely stable — advocates held their ground with evidence.**"
            )

    return "\n".join(lines)


def run_debate_phase(
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    advocates: list[ModelDef],
    briefing: str,
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
    round_offset: int = 0,
) -> list[list[ModelResponse]]:
    """Phase 5: Adversarial debate rounds with parallel dispatch.

    Each round:
    1. Each advocate receives challenges directed at them
    2. All advocates respond IN PARALLEL (their prompts are pre-built from
       the previous round's state, so they're independent)
    3. They must DEFEND, CONCEDE, or REVISE for each challenge
    4. They can launch counter-attacks
    5. The counter-attacks become input for the next round

    Returns a list of rounds, each containing a list of ModelResponses.
    """
    good_advocates = successful_responses(advocate_responses)
    max_rounds = config.depth.debate_rounds
    alias_to_model = _build_alias_model_map(advocate_responses, advocates)

    if max_rounds == 0:
        return []

    # Track latest position for each advocate
    latest_positions: dict[str, str] = {r.alias: r.content or "" for r in good_advocates}

    # Current challenges start as the Phase 4 challenge output
    current_challenges = challenge_responses

    all_rounds: list[list[ModelResponse]] = []

    for round_num in range(1, max_rounds + 1):
        actual_round = round_num + round_offset
        progress.phase(5, f"Debate round {actual_round} — advocates defending positions (parallel)...")

        # Build all debate calls for this round (to dispatch in parallel)
        debate_calls = []
        call_alias_map = []  # track which call belongs to which advocate

        for resp in good_advocates:
            model = alias_to_model.get(resp.alias)
            if model is None:
                continue

            # Gather challenges directed at this advocate
            my_challenges = _extract_challenges_for(resp.alias, current_challenges)

            # Show other advocates' current positions
            other_positions = "\n\n---\n\n".join(
                f"### {alias} (current position)\n\n{pos}"
                for alias, pos in latest_positions.items()
                if alias != resp.alias
            )

            debate_prompt = (
                f"## Original Briefing\n\n{briefing}\n\n"
                f"{'=' * 60}\n\n"
                f"## Your Current Position ({resp.alias})\n\n{latest_positions[resp.alias]}\n\n"
                f"{'=' * 60}\n\n"
                f"## Challenges Directed at You\n\n{my_challenges}\n\n"
                f"{'=' * 60}\n\n"
                f"## Other Advocates' Current Positions\n\n{other_positions}\n\n"
                f"{'=' * 60}\n\n"
                f"This is debate round {actual_round}. "
                f"You MUST respond to each challenge: DEFEND, CONCEDE, or REVISE. "
                f"Then state your current position and your Position Stability score (1-5). "
                f"You may also counter-attack."
            )

            debate_alias = f"Debate-R{actual_round}-{resp.alias}"

            debate_calls.append({
                "model": model,
                "system_prompt": DEFEND_SYSTEM_PROMPT,
                "user_prompt": debate_prompt,
                "alias": debate_alias,
                "timeout": config.depth.timeout_per_model,
                "temperature": 0.5,
                "max_tokens": 4096,
            })
            call_alias_map.append(resp.alias)

        # Dispatch all advocates in parallel for this round
        round_responses = fan_out_multi(debate_calls, progress=progress) if debate_calls else []

        # Update latest positions and write files
        for debate_resp in round_responses:
            if debate_resp.status == "success":
                # Extract advocate alias from debate alias ("Debate-R3-Advocate-A" -> "Advocate-A")
                parts = debate_resp.alias.split("-", 2)  # ['Debate', 'R3', 'Advocate-A']
                adv_alias = parts[2] if len(parts) > 2 else debate_resp.alias
                latest_positions[adv_alias] = debate_resp.content or ""
                path = session_dir / f"debate-round-{actual_round}-{adv_alias.lower()}.md"
                path.write_text(
                    f"# Debate Round {actual_round}: {adv_alias}\n\n"
                    f"{debate_resp.content}\n"
                )

        all_rounds.append(round_responses)

        # The debate responses become the "challenges" for the next round
        # (because they may contain counter-attacks)
        current_challenges = round_responses

        good_round = successful_responses(round_responses)
        progress.info(f"Round {actual_round} complete: {len(good_round)}/{len(round_responses)} responses")

    # Write position stability report
    stability_report = build_position_stability_report(advocate_responses, all_rounds)
    (session_dir / "position-stability.md").write_text(stability_report)
    progress.info("Position stability scorecard written")

    return all_rounds


# ---------------------------------------------------------------------------
# Phase 6: Judicial review (The Bench)
# ---------------------------------------------------------------------------

def select_cardinals(config: ConclaveConfig) -> list[ModelDef]:
    """Select the judicial panel based on depth configuration."""
    depth = config.depth
    cardinals: list[ModelDef] = []

    # Justices (always in order — Qwen first, then DeepSeek R1)
    bishops_available = config.bishops[:depth.cardinals_bishops]
    cardinals.extend(bishops_available)

    # Appellate Judges (randomly drawn from pool)
    if depth.cardinals_priests > 0:
        priests_pool = list(config.priests)
        random.shuffle(priests_pool)
        cardinals.extend(priests_pool[:depth.cardinals_priests])

    # Magistrate Judges (randomly drawn from pool)
    if depth.cardinals_deacons > 0:
        deacons_pool = list(config.deacons)
        random.shuffle(deacons_pool)
        cardinals.extend(deacons_pool[:depth.cardinals_deacons])

    return cardinals


def run_cardinal_phase(
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinals: list[ModelDef],
    briefing: str,
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
    stability_report: str = "",
    phase_label: str = "Judicial review",
) -> tuple[list[ModelResponse], bool, str]:
    """Phase 6: Judicial review (The Bench).

    Returns: (cardinal_responses, should_remand, remand_reason)
    """
    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)

    progress.phase(6, f"{phase_label} — {len(cardinals)} judges evaluating...")
    progress.sacred_college(
        bishops=[c.display_name for c in cardinals if c.role == "bishop"],
        priests=[c.display_name for c in cardinals if c.role == "priest"],
        deacons=[c.display_name for c in cardinals if c.role == "deacon"],
    )

    # Build the full evidence package
    submissions_text = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good_advocates
    )

    challenges_text = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good_challenges
    ) if good_challenges else "(No challenge round)"

    debate_text = ""
    for round_idx, round_resps in enumerate(debate_rounds, 1):
        good_round = successful_responses(round_resps)
        if good_round:
            round_text = "\n\n---\n\n".join(
                f"### {r.alias}\n\n{r.content}" for r in good_round
            )
            debate_text += f"\n\n## Debate Round {round_idx}\n\n{round_text}"
    if not debate_text:
        debate_text = "(No debate rounds)"

    cardinal_prompt = (
        f"## Original Briefing\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Initial Advocate Submissions\n\n{submissions_text}\n\n"
        f"{'=' * 60}\n\n"
        f"## Challenge Round\n\n{challenges_text}\n\n"
        f"{'=' * 60}\n\n"
        f"## Debate Rounds\n\n{debate_text}\n\n"
        f"{'=' * 60}\n\n"
    )

    # Append position stability report if available (EXHAUSTIVE+ / NUCLEAR)
    if stability_report:
        cardinal_prompt += (
            f"## Position Stability Scorecard (Kelley-Riedl Sycophancy Audit)\n\n"
            f"{stability_report}\n\n"
            f"{'=' * 60}\n\n"
        )

    cardinal_prompt += "Please render your judgment on this deliberation."

    cardinal_aliases = generate_aliases(len(cardinals), "Judge")

    # Judges get the biggest prompts (full debate transcripts)
    # so they need more time than advocates
    cardinal_timeout = int(config.depth.timeout_per_model * 1.5)

    cardinal_responses = fan_out(
        models=cardinals,
        system_prompt=CARDINAL_SYSTEM_PROMPT,
        user_prompt=cardinal_prompt,
        aliases=cardinal_aliases,
        timeout=cardinal_timeout,
        temperature=0.3,
        max_tokens=4096,
        progress=progress,
    )

    # Write individual judgments
    good_cardinals = successful_responses(cardinal_responses)
    for resp in good_cardinals:
        path = session_dir / f"judgment-{resp.alias.lower()}.md"
        path.write_text(f"# Judicial Opinion: {resp.alias}\n\n{resp.content}\n")

    # Write judge alias map
    cardinal_alias_map = {
        r.alias: {"model": r.display_name, "provider": r.provider, "role": r.role}
        for r in cardinal_responses
    }
    (session_dir / "cardinal-alias-map.json").write_text(json.dumps(cardinal_alias_map, indent=2))

    # Check for REMAND verdicts
    should_remand = False
    remand_reason = ""
    remand_count = 0
    for resp in good_cardinals:
        content_lower = (resp.content or "").lower()
        if "**remand**" in content_lower or "verdict: remand" in content_lower:
            remand_count += 1
            progress.cardinal_remand(resp.alias, "Remand requested")
        else:
            verdict = "ACCEPT" if "accept" in content_lower else "SYNTHESIZE" if "synthesize" in content_lower else "VERDICT RENDERED"
            progress.cardinal_verdict(resp.alias, verdict)

    if remand_count > len(good_cardinals) / 2:
        should_remand = True
        remand_reason = f"{remand_count}/{len(good_cardinals)} judges voted REMAND"
        progress.warn(f"REMAND triggered: {remand_reason}")

    return cardinal_responses, should_remand, remand_reason


# ---------------------------------------------------------------------------
# Phase 7: Fresh Eyes validation
# ---------------------------------------------------------------------------

def select_fresh_eyes(config: ConclaveConfig, seated_cardinal_ids: set[str]) -> Optional[ModelDef]:
    """Select a Fresh Eyes model not already seated as a judge."""
    candidates = []
    for pool in [config.deacons, config.priests]:
        for m in pool:
            if m.id not in seated_cardinal_ids:
                candidates.append(m)
    random.shuffle(candidates)
    return candidates[0] if candidates else None


def run_fresh_eyes_phase(
    briefing: str,
    final_output_text: str,
    config: ConclaveConfig,
    seated_cardinal_ids: set[str],
    session_dir: Path,
    progress: Progress,
) -> Optional[ModelResponse]:
    """Phase 7: Fresh Eyes review of the final output."""
    fresh_model = select_fresh_eyes(config, seated_cardinal_ids)
    if fresh_model is None:
        progress.warn("No model available for Fresh Eyes — skipping")
        return None

    progress.phase(7, f"Fresh Eyes validation — {fresh_model.display_name} reviewing...")

    fresh_prompt = (
        f"## Original Briefing\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Final Output (from judicial review phase)\n\n{final_output_text}\n\n"
        f"{'=' * 60}\n\n"
        f"Review this output as someone seeing it for the first time."
    )

    resp = call_model(
        model=fresh_model,
        system_prompt=FRESH_EYES_SYSTEM_PROMPT,
        user_prompt=fresh_prompt,
        alias="Fresh-Eyes",
        timeout=config.depth.timeout_per_model,
        temperature=0.5,
        max_tokens=3072,
        progress=progress,
    )

    if resp.status == "success":
        (session_dir / "fresh-eyes-review.md").write_text(
            f"# Fresh Eyes Review\n\n{resp.content}\n"
        )
        progress.info(f"Fresh Eyes review complete ({resp.elapsed:.1f}s)")
    else:
        progress.warn(f"Fresh Eyes failed: {resp.error}")

    return resp


# ---------------------------------------------------------------------------
# Dissenting opinions — persistent dissenters issue formal dissents
# ---------------------------------------------------------------------------

def detect_dissenters(
    advocate_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
) -> list[tuple[str, ModelDef, str]]:
    """Detect advocates who held their position through all debate rounds
    but whose position was NOT adopted by the judicial verdict.

    A dissenter is an advocate whose:
      - Average position stability across all rounds is <= 2.0
        (rock solid or minor refinement only)
      - Position was not the one the judges ACCEPTED

    Returns a list of (alias, ModelDef_placeholder, final_position_text)
    for each detected dissenter. The ModelDef is None here — caller must
    resolve alias → model via alias_to_model map.
    """
    good_advocates = successful_responses(advocate_responses)
    good_cardinals = successful_responses(cardinal_responses)

    if not debate_rounds or not good_cardinals:
        return []

    # --- 1. Compute average stability per advocate ---
    advocate_stability: dict[str, list[int]] = {}
    advocate_final_pos: dict[str, str] = {}

    for resp in good_advocates:
        advocate_stability[resp.alias] = []
        advocate_final_pos[resp.alias] = resp.content or ""

    for round_resps in debate_rounds:
        for r in successful_responses(round_resps):
            # Alias format: "Debate-R3-Advocate-A" → extract "Advocate-A"
            parts = r.alias.split("-", 2)
            adv_alias = parts[2] if len(parts) > 2 else r.alias
            score = _extract_position_stability(r.content)
            if adv_alias in advocate_stability:
                advocate_stability[adv_alias].append(score)
            # Track final position (last round's content)
            if adv_alias in advocate_final_pos:
                advocate_final_pos[adv_alias] = r.content or ""

    # --- 2. Determine who the verdict favors ---
    # Parse the judicial verdicts to find which advocates were ACCEPTED
    accepted_aliases: set[str] = set()
    for resp in good_cardinals:
        content_lower = (resp.content or "").lower()
        # Look for "ACCEPT [Advocate-X]" patterns
        for adv_resp in good_advocates:
            alias_lower = adv_resp.alias.lower()
            # Check if this advocate was accepted or their position adopted
            if f"accept {alias_lower}" in content_lower:
                accepted_aliases.add(adv_resp.alias)
            if f"accept [{alias_lower}]" in content_lower:
                accepted_aliases.add(adv_resp.alias)
            if f"accept **{alias_lower}" in content_lower:
                accepted_aliases.add(adv_resp.alias)
            # Also check SYNTHESIZE mentions ("combine best elements of Advocate-X")
            if "synthesize" in content_lower and alias_lower in content_lower:
                # In a synthesis, the advocate whose position is closest gets credit
                # but doesn't count as "accepted" — they might still dissent
                pass

    # --- 3. Find dissenters: stable + not accepted ---
    dissenters: list[tuple[str, str]] = []
    for alias, scores in advocate_stability.items():
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        # Dissenter criteria: held firm (avg stability <= 2.0) AND not accepted
        if avg <= 2.0 and alias not in accepted_aliases:
            dissenters.append((alias, advocate_final_pos.get(alias, "")))

    return dissenters


def run_dissent_phase(
    dissenters: list[tuple[str, str]],
    advocate_responses: list[ModelResponse],
    advocates: list[ModelDef],
    cardinal_responses: list[ModelResponse],
    briefing: str,
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> list[ModelResponse]:
    """Give persistent dissenters a chance to issue formal dissenting opinions.

    Like a Supreme Court dissent: the advocate reads the verdict, disagrees,
    and writes a structured dissent for the record.

    Returns list of ModelResponse objects for cost tracking.
    """
    if not dissenters:
        return []

    alias_to_model = _build_alias_model_map(advocate_responses, advocates)
    good_cardinals = successful_responses(cardinal_responses)

    # Build the verdict text for dissenters to read
    verdict_text = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good_cardinals
    )

    progress.phase(6, f"Dissenting opinions — {len(dissenters)} advocate(s) filing dissent...")

    dissent_calls = []
    for alias, final_position in dissenters:
        model = alias_to_model.get(alias)
        if model is None:
            progress.warn(f"No model found for dissenter {alias} — skipping")
            continue

        dissent_prompt = (
            f"## Original Briefing\n\n{briefing}\n\n"
            f"{'=' * 60}\n\n"
            f"## Your Final Position ({alias})\n\n{final_position}\n\n"
            f"{'=' * 60}\n\n"
            f"## The Judicial Verdict (which you disagree with)\n\n{verdict_text}\n\n"
            f"{'=' * 60}\n\n"
            f"You held your position through all debate rounds. The judges have "
            f"now ruled — and their verdict does not adopt your position.\n\n"
            f"Issue your formal dissenting opinion. You are {alias}."
        )

        dissent_calls.append({
            "model": model,
            "system_prompt": DISSENT_SYSTEM_PROMPT,
            "user_prompt": dissent_prompt,
            "alias": f"Dissent-{alias}",
            "timeout": config.depth.timeout_per_model,
            "temperature": 0.5,
            "max_tokens": 2048,
        })

    if not dissent_calls:
        return []

    dissent_responses = fan_out_multi(dissent_calls, progress=progress)

    # Write individual dissent files
    for resp in successful_responses(dissent_responses):
        # Alias format: "Dissent-Advocate-A" → extract advocate alias
        adv_alias = resp.alias.replace("Dissent-", "")
        path = session_dir / f"dissent-{adv_alias.lower()}.md"
        path.write_text(
            f"# Dissenting Opinion: {adv_alias}\n\n{resp.content}\n"
        )
        progress.info(f"Dissent filed by {adv_alias} ({resp.elapsed:.1f}s)")

    good_dissents = successful_responses(dissent_responses)
    if good_dissents:
        progress.info(f"{len(good_dissents)} dissenting opinion(s) filed.")
    else:
        progress.info("No dissenting opinions filed (all calls failed).")

    return dissent_responses


# ---------------------------------------------------------------------------
# Session summary — canonical "Question → Outcome → How → Build" document
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """\
You are a senior analyst producing a canonical session summary of a Tribunal
deliberation. You have access to the full record: advocate submissions,
challenges, debate rounds, judicial opinions, and (if present) Fresh Eyes.

Produce a structured summary in EXACTLY this format. Do NOT add any sections,
headings, or commentary beyond what is specified below.

## The Question

One to three sentences restating what the deliberation was about. Write it
as a clear problem statement — not a quote, not a title, but a sentence a
human would understand cold.

## Recommended Outcome

The consensus recommendation from the deliberation. Write this as a concrete,
actionable paragraph that someone could hand to an engineer and say "build this."
Include the key architectural decisions, specific tools or approaches chosen,
and any quantitative parameters the council agreed on. This is NOT a summary
of who said what — it is THE ANSWER the council produced.

## How We Got Here

This section has two subsections:

### Council Performance

A markdown table with these EXACT columns:

| Model | Final Position | Rank | Note |
|-------|----------------|------|------|

"Model" must use the REAL model name (from the identity reveal), not the alias.
"Final Position" is one sentence describing their final stance.
"Rank" is a number (1 = strongest). Use "-" for advocates who withdrew or merged.
"Note" is one phrase explaining their ranking (e.g., "strongest evidence",
"conceded key points", "fabricated statistics").

### Key Moments

Bulleted list of 3-5 pivotal moments from the debate. Each bullet should name
the advocate (by both alias AND model name, e.g. "Advocate-D (Claude Sonnet)"),
describe what happened, and explain why it mattered. Focus on: position changes,
evidence collapses, breakthrough insights, and moments where the challenge
system worked as designed.

Now, assess the task type. If the deliberation produced a BUILDABLE outcome
(code, architecture, system design, pipeline, tool, process — something an
engineer could implement), include the following section. If the task was
purely analytical, evaluative, or opinion-seeking (e.g., "compare X vs Y",
"is this approach good?"), OMIT this section entirely.

## Dissenting Opinions

If the record includes any formal dissenting opinions, summarize each one here.
For each dissent:
- Name the dissenter by BOTH alias and real model name
- State the core of their disagreement in 2-3 sentences
- Note the strongest evidence they cited that the majority did not adequately address

If there are no dissenting opinions in the record, OMIT this section entirely.
Do NOT fabricate dissents — only include this section if dissent files appear
in the record below.

## Build This

> **To implement this, paste the following into your AI engine:**

A detailed, self-contained implementation prompt that an engineer could paste
into Claude Code, Cursor, Codex, or any AI coding assistant. It must:
- Specify the system to build (inputs, outputs, architecture)
- Include all specific technical decisions from the council (models, thresholds,
  formats, constraints)
- Be actionable without reading the full deliberation
- Do NOT wrap the prompt in code fences — write it as plain text

Do NOT include preamble like "Based on the deliberation..." — the Build This
section should read as a standalone engineering spec.

IMPORTANT RULES:
- Use real model names from the identity reveal, never aliases.
- Do not invent details not present in the deliberation.
- Keep the total summary under 1500 words (excluding the Build This prompt and appendix).
- Write in plain, direct language. No hedging, no filler.

Finally, ALWAYS include this appendix at the very end of the summary, after all
other sections. Copy it VERBATIM — do not modify, summarize, or rephrase it:

## Appendix: How The Tribunal Works

The Tribunal is a structured multi-model deliberation system. Instead of asking
one AI model a question and trusting its answer, the Tribunal convenes a panel
of independent models, forces them to argue, and subjects their conclusions to
judicial review. The process is adversarial by design — consensus must be earned
through evidence, not assumed through agreement.

**Advocates** are independent AI models (e.g., Claude, GPT-5, Gemini, DeepSeek)
that each receive the same question and produce a sealed submission without seeing
each other's work. They are anonymized as Advocate-A, Advocate-B, etc. to prevent
brand-bias from influencing the judges. Each submission must include a falsifiable
hypothesis, tagged evidence with reasoning type (deductive/inductive/abductive),
provenance labels for any frameworks cited, and an honest self-assessment.

**Challenges** follow submissions. Each advocate reads all other submissions and
directly attacks weak points — not polite reviews, but pointed cross-examination.
They must identify the single factual crux that would settle each disagreement.

**Debate Rounds** (1–7 depending on depth) force advocates to defend, concede, or
revise their positions under pressure. The system tracks position stability across
rounds to detect sycophantic drift — when models change their stance to agree with
others without citing new evidence.

**Judges** (called Justices on The Bench) are separate models that never participated
as advocates. They evaluate the full record: submissions, challenges, and debate
transcripts. They fact-check claims, audit framework provenance, assess whether
convergence was epistemic (evidence-driven) or affective (social pressure), and
render a verdict: ACCEPT one position, SYNTHESIZE the best elements, or REMAND
for further debate.

**Depth levels** control rigor: QUICK (submissions only), BALANCED (1 debate round,
1 judge), THOROUGH (3 rounds, 3 judges), RIGOROUS (5 rounds, full panel),
EXHAUSTIVE (5 rounds + Fresh Eyes review), NUCLEAR (7 rounds + mid-debate
judicial checkpoint).

The system is deterministic code — it cannot be sycophantic. It dispatches prompts,
collects responses, anonymizes identities, and enforces the adversarial structure.
The models argue; the code referees."""


def generate_session_summary(
    briefing: str,
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    fresh_eyes_response: Optional[ModelResponse],
    all_responses: dict[str, list[ModelResponse]],
    session_id: str,
    session_dir: Path,
    config: ConclaveConfig,
    wall_time: float,
    remand_count: int,
    progress: Progress,
) -> Optional[ModelResponse]:
    """Generate the canonical session summary via LLM synthesis.

    Uses a single model (Qwen 3.5 397B, the first Justice) to read the full
    session record and produce the Question → Outcome → How → Build document.

    Returns the ModelResponse for cost tracking, or None if generation fails.
    """
    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)
    good_cardinals = successful_responses(cardinal_responses)

    if not good_advocates:
        return None

    progress.info("Generating session summary...")

    # --- Compute deterministic header ---
    every_response: list[ModelResponse] = []
    for phase_resps in all_responses.values():
        every_response.extend(phase_resps)
    cost = total_cost(every_response)
    minutes = int(wall_time // 60)
    seconds = int(wall_time % 60)

    # Load identity reveals
    alias_map = {}
    if (session_dir / "alias-map.json").exists():
        alias_map = json.loads((session_dir / "alias-map.json").read_text())
    cardinal_alias_map = {}
    if (session_dir / "cardinal-alias-map.json").exists():
        cardinal_alias_map = json.loads((session_dir / "cardinal-alias-map.json").read_text())

    # Build identity reveal table for the LLM
    identity_lines = ["## Identity Reveal"]
    for alias, info in alias_map.items():
        identity_lines.append(f"- {alias} = {info['model']} ({info['provider']})")
    for alias, info in cardinal_alias_map.items():
        role = info.get('role', 'judge')
        identity_lines.append(f"- {alias} = {info['model']} ({info['provider']}, {role})")
    identity_text = "\n".join(identity_lines)

    # Build the full record for the LLM
    record_parts = []

    record_parts.append(f"## BRIEFING (the original question)\n\n{briefing}")

    record_parts.append("\n\n## ADVOCATE SUBMISSIONS\n")
    for r in good_advocates:
        record_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_challenges:
        record_parts.append("\n## CHALLENGE ROUND\n")
        for r in good_challenges:
            record_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    for round_idx, round_resps in enumerate(debate_rounds, 1):
        good_round = successful_responses(round_resps)
        if good_round:
            record_parts.append(f"\n## DEBATE ROUND {round_idx}\n")
            for r in good_round:
                record_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_cardinals:
        record_parts.append("\n## JUDICIAL OPINIONS\n")
        for r in good_cardinals:
            record_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if fresh_eyes_response and fresh_eyes_response.status == "success":
        record_parts.append("\n## FRESH EYES REVIEW\n")
        record_parts.append(f"{fresh_eyes_response.content}\n")

    # Include dissenting opinions in the record
    good_dissents = successful_responses(all_responses.get("dissents", []))
    if good_dissents:
        record_parts.append("\n## DISSENTING OPINIONS\n")
        for r in good_dissents:
            adv_alias = r.alias.replace("Dissent-", "")
            record_parts.append(f"### {adv_alias} (Dissent)\n{r.content}\n\n---\n")

    record_parts.append(f"\n{identity_text}")

    full_record = "\n".join(record_parts)

    summary_prompt = (
        f"Produce the session summary for this Tribunal deliberation.\n\n"
        f"{full_record}"
    )

    # Select synthesizer model: use first Bishop (Qwen 3.5 397B)
    from config_loader import BISHOPS
    summary_model = BISHOPS[0] if BISHOPS else None
    if summary_model is None:
        progress.warn("No model available for session summary — skipping")
        return None

    resp = call_model(
        model=summary_model,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=summary_prompt,
        alias="Summary-Synthesizer",
        timeout=int(config.depth.timeout_per_model * 1.5),  # same multiplier as judges
        temperature=0.3,
        max_tokens=4096,
        progress=progress,
    )

    if resp.status != "success":
        progress.warn(f"Session summary generation failed: {resp.error}")
        return resp

    # --- Build the final document: YAML frontmatter + deterministic header + LLM body ---
    n_judges = len(good_cardinals)

    # Extract date stamp and topic slug from session_id for filenames
    # session_id format: "tribunal-{slug}-{YYYYMMDD}-{HHMMSS}"
    _sid_parts = session_id.split("-")
    # Find the date part (8 digits)
    _date_stamp = ""
    _slug_parts = []
    for _i, _p in enumerate(_sid_parts):
        if re.match(r"^\d{8}$", _p):
            _date_stamp = _p
            break
        elif _i > 0:  # skip "tribunal" prefix
            _slug_parts.append(_p)
    _topic_slug = "-".join(_slug_parts) if _slug_parts else "session"
    if not _date_stamp:
        _date_stamp = datetime.now().strftime("%Y%m%d")

    # Canonical filenames: YYYYMMDD-session-summary-{slug}.{md,pdf}
    summary_basename = f"{_date_stamp}-session-summary-{_topic_slug}"
    summary_md_name = f"{summary_basename}.md"
    summary_pdf_name = f"{summary_basename}.pdf"

    # Extract advocate model names for frontmatter tags
    _adv_names = [a.display_name for a in good_advocates] if good_advocates else []
    _judge_names = [j.display_name for j in good_cardinals] if good_cardinals else []

    # YAML frontmatter
    frontmatter = (
        f"---\n"
        f"topic: {_topic_slug.replace('-', ' ')}\n"
        f"session: {session_id}\n"
        f"date: {_date_stamp[:4]}-{_date_stamp[4:6]}-{_date_stamp[6:8]}\n"
        f"depth: {config.depth.name}\n"
        f"advocates: {len(good_advocates)}\n"
        f"judges: {n_judges}\n"
        f"cost: ${cost:.4f}\n"
        f"time: {minutes}m {seconds:02d}s\n"
        f"status: completed\n"
        f"tags: [{', '.join(_slug_parts)}]\n"
        f"models:\n"
        f"  advocates: [{', '.join(_adv_names)}]\n"
        f"  judges: [{', '.join(_judge_names)}]\n"
        f"---\n\n"
    )

    header = (
        f"# Tribunal Session Summary\n"
        f"**Session: {session_id} | Depth: {config.depth.name} | "
        f"Advocates: {len(good_advocates)} | Judges: {n_judges} | "
        f"Cost: ${cost:.4f} | Time: {minutes}m {seconds:02d}s**\n"
        f"*Full logs: `tribunal-sessions/{session_id}/` | "
        f"Audit trail: `final-output.md` | "
        f"Narrative: `play-by-play.md`*\n\n"
        f"---\n\n"
    )

    summary_text = frontmatter + header + (resp.content or "")

    # Write with canonical filename (also keep legacy name as symlink)
    (session_dir / summary_md_name).write_text(summary_text)
    legacy_md = session_dir / "session-summary.md"
    if legacy_md.exists() or legacy_md.is_symlink():
        legacy_md.unlink()
    legacy_md.symlink_to(summary_md_name)
    progress.info(f"Session summary written: {summary_md_name} ({resp.elapsed:.1f}s)")

    # --- Generate PDF from session summary (optional, requires reportlab) ---
    try:
        from summary_pdf import generate_summary_pdf
        md_path = str(session_dir / summary_md_name)
        pdf_path = str(session_dir / summary_pdf_name)
        generate_summary_pdf(md_path, pdf_path)
        # Symlink legacy name
        legacy_pdf = session_dir / "session-summary.pdf"
        if legacy_pdf.exists() or legacy_pdf.is_symlink():
            legacy_pdf.unlink()
        legacy_pdf.symlink_to(summary_pdf_name)
        progress.info(f"Session summary PDF generated: {summary_pdf_name}")
    except ImportError:
        progress.info("PDF generation skipped (reportlab not installed)")
    except Exception as e:
        progress.warn(f"PDF generation failed: {e}")

    return resp


# ---------------------------------------------------------------------------
# Play-by-play narrative — dual narrator with judicial vote
# ---------------------------------------------------------------------------

NARRATOR_VOTE_SYSTEM_PROMPT = """\
You are a judge evaluating two competing play-by-play narratives of the
same Tribunal deliberation. Both narrators were given the identical transcript.

Your job: decide which narrative is BETTER. Evaluate on:

1. **Dramatic engagement** — Does it read like watching a live debate unfold?
2. **Accuracy** — Does the narrative faithfully represent what actually happened?
3. **Turning-point identification** — Did the narrator find the real pivotal moment?
4. **Alias discipline** — Does the narrator use only aliases, never model names?
5. **Completeness** — Are all phases covered, or did important content get dropped?

Output EXACTLY this format:

### Winner
[NARRATOR-A or NARRATOR-B]

### Reasoning
[2-3 sentences explaining WHY the winner is better, with specific references to moments
in the narrative that demonstrate superiority.]

### Scores
| Criterion | Narrator-A | Narrator-B |
|-----------|-----------|------------|
| Dramatic engagement | X/10 | X/10 |
| Accuracy | X/10 | X/10 |
| Turning-point identification | X/10 | X/10 |
| Alias discipline | X/10 | X/10 |
| Completeness | X/10 | X/10 |
| **Total** | XX/50 | XX/50 |
"""


def generate_play_by_play(
    briefing: str,
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    fresh_eyes_response: Optional[ModelResponse],
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> list[ModelResponse]:
    """Generate a play-by-play narrative via dual narrators + judicial vote.

    Two models (Qwen 3.5 397B and DeepSeek V3) independently narrate the same
    deliberation transcript. A Justice then votes on which narrative
    is better. The winner is saved as play-by-play.md; both are preserved.

    Returns a list of all ModelResponse objects (both narrators + vote) for cost tracking.
    """
    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)
    good_cardinals = successful_responses(cardinal_responses)

    if not good_challenges and not debate_rounds:
        # QUICK depth — no debate to narrate
        return []

    progress.info("Generating dual play-by-play narratives (Qwen 3.5 + DeepSeek V3)...")

    # Compile the full transcript
    transcript_parts = []

    transcript_parts.append("## INITIAL SUBMISSIONS\n")
    for r in good_advocates:
        transcript_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_challenges:
        transcript_parts.append("\n## CHALLENGE ROUND\n")
        for r in good_challenges:
            transcript_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    for round_idx, round_resps in enumerate(debate_rounds, 1):
        good_round = successful_responses(round_resps)
        if good_round:
            transcript_parts.append(f"\n## DEBATE ROUND {round_idx}\n")
            for r in good_round:
                transcript_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_cardinals:
        transcript_parts.append("\n## JUDICIAL OPINIONS\n")
        for r in good_cardinals:
            transcript_parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if fresh_eyes_response and fresh_eyes_response.status == "success":
        transcript_parts.append("\n## FRESH EYES REVIEW\n")
        transcript_parts.append(f"{fresh_eyes_response.content}\n")

    full_transcript = "\n".join(transcript_parts)

    narrator_prompt = (
        f"## Original Question\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Full Deliberation Transcript\n\n{full_transcript}\n\n"
        f"{'=' * 60}\n\n"
        f"Write the play-by-play narrative of this deliberation."
    )

    # --- Dual narrators: Qwen 3.5 397B (Justice) + DeepSeek V3 (Advocate) ---
    from config_loader import BISHOPS, ADVOCATES

    narrator_qwen = None
    for m in BISHOPS:
        if "qwen" in m.id.lower():
            narrator_qwen = m
            break
    if narrator_qwen is None:
        narrator_qwen = BISHOPS[0]

    narrator_dsv3 = None
    for m in ADVOCATES:
        if "deepseek-v3" in m.id.lower():
            narrator_dsv3 = m
            break
    if narrator_dsv3 is None:
        # Fallback: use second Justice (DeepSeek R1) if V3 not found
        narrator_dsv3 = BISHOPS[1] if len(BISHOPS) > 1 else BISHOPS[0]

    narrator_models = [narrator_qwen, narrator_dsv3]
    narrator_aliases = ["Narrator-Qwen", "Narrator-DeepSeek"]

    narrator_responses = fan_out(
        models=narrator_models,
        system_prompt=NARRATOR_SYSTEM_PROMPT,
        user_prompt=narrator_prompt,
        aliases=narrator_aliases,
        timeout=config.depth.timeout_per_model,
        temperature=0.7,
        max_tokens=4096,
        progress=progress,
    )

    good_narrators = successful_responses(narrator_responses)

    if len(good_narrators) == 0:
        progress.warn("Both narrators failed — no play-by-play generated.")
        return list(narrator_responses)  # return failed responses for cost tracking

    # Save both narratives regardless of vote
    for nr in good_narrators:
        tag = "qwen" if "qwen" in nr.alias.lower() else "deepseek"
        (session_dir / f"play-by-play-{tag}.md").write_text(
            f"# Tribunal Play-by-Play ({nr.alias})\n\n{nr.content}\n"
        )
        progress.info(f"  {nr.alias} narrative generated ({nr.elapsed:.1f}s)")

    if len(good_narrators) == 1:
        # Only one succeeded — that one wins by default
        winner = good_narrators[0]
        progress.info(f"Only {winner.alias} succeeded — wins by default.")
        (session_dir / "play-by-play.md").write_text(
            f"# Tribunal Play-by-Play\n\n"
            f"*Narrator: {winner.alias} (sole survivor — other narrator failed)*\n\n"
            f"{winner.content}\n"
        )
        return list(narrator_responses)

    # --- Judicial vote on which narrative is better ---
    progress.info("Judge voting on best narrative...")

    vote_prompt = (
        f"## Narrator-A ({good_narrators[0].alias})\n\n"
        f"{good_narrators[0].content}\n\n"
        f"{'=' * 60}\n\n"
        f"## Narrator-B ({good_narrators[1].alias})\n\n"
        f"{good_narrators[1].content}\n\n"
        f"{'=' * 60}\n\n"
        f"Which narrative is better? Evaluate and declare a winner."
    )

    # Use a Justice as the voter (prefer DeepSeek R1 since Qwen authored one narrative)
    voter_model = None
    for m in BISHOPS:
        if "deepseek" in m.id.lower() and "r1" in m.id.lower():
            voter_model = m
            break
    if voter_model is None:
        voter_model = BISHOPS[-1] if len(BISHOPS) > 1 else BISHOPS[0]

    vote_resp = call_model(
        model=voter_model,
        system_prompt=NARRATOR_VOTE_SYSTEM_PROMPT,
        user_prompt=vote_prompt,
        alias="Narrator-Judge",
        timeout=config.depth.timeout_per_model,
        temperature=0.3,
        max_tokens=2048,
        progress=progress,
    )

    # Determine winner
    winner = good_narrators[0]  # default to first (Qwen) if vote is unclear
    winner_tag = "Qwen"

    if vote_resp.status == "success":
        vote_text = vote_resp.content.upper()
        if "NARRATOR-B" in vote_text:
            winner = good_narrators[1]
            winner_tag = "DeepSeek"
        elif "NARRATOR-A" in vote_text:
            winner = good_narrators[0]
            winner_tag = "Qwen"

        progress.info(f"Judicial vote: {winner_tag} narrative wins.")

        # Save vote
        (session_dir / "narrator-vote.md").write_text(
            f"# Narrator Vote (Judge: {voter_model.display_name})\n\n"
            f"{vote_resp.content}\n"
        )
    else:
        progress.warn(f"Narrator vote failed ({vote_resp.error}) — defaulting to Qwen.")

    # Write the winner as the canonical play-by-play
    (session_dir / "play-by-play.md").write_text(
        f"# Tribunal Play-by-Play\n\n"
        f"*Narrator: {winner.alias} (selected by judicial vote)*\n\n"
        f"{winner.content}\n"
    )

    # Return all narrator + vote responses for cost tracking
    all_narrator_resps = list(narrator_responses)
    if vote_resp:
        all_narrator_resps.append(vote_resp)

    return all_narrator_resps


# ---------------------------------------------------------------------------
# Phase 8: Output and debrief
# ---------------------------------------------------------------------------

def build_final_output(
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    fresh_eyes_response: Optional[ModelResponse],
    session_id: str,
    config: ConclaveConfig,
    dissent_responses: Optional[list[ModelResponse]] = None,
) -> str:
    """Build the final output document."""

    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)
    good_cardinals = successful_responses(cardinal_responses)
    depth_name = config.depth.name

    lines = [
        f"# Tribunal Output: {session_id}",
        f"## Depth: {depth_name}",
        "",
    ]

    if depth_name == "QUICK":
        lines.extend([
            f"**{len(good_advocates)} independent submissions** for your review.",
            "At QUICK depth, there is no debate or judicial review — you get raw,",
            "independent perspectives to compare yourself.",
            "", "---", "",
        ])
        for resp in good_advocates:
            lines.extend([f"## {resp.alias}", "", resp.content or "(no content)", "", "---", ""])

    else:
        # BALANCED+ : full deliberation output
        total_debate_resps = sum(len(successful_responses(r)) for r in debate_rounds)
        lines.extend([
            f"**{len(good_advocates)} advocates** deliberated with "
            f"{len(good_challenges)} challenge(s), "
            f"{len(debate_rounds)} debate round(s) ({total_debate_resps} exchanges), and "
            f"{len(good_cardinals)} judicial opinion(s).",
            "", "---", "",
        ])

        # Judicial opinions (the headline)
        if good_cardinals:
            lines.extend(["## Judicial Opinions", ""])
            for resp in good_cardinals:
                lines.extend([f"### {resp.alias}", "", resp.content or "(no content)", "", "---", ""])

        # Fresh Eyes
        if fresh_eyes_response and fresh_eyes_response.status == "success":
            lines.extend([
                "## Fresh Eyes Review", "",
                fresh_eyes_response.content or "(no content)", "", "---", "",
            ])

        # Dissenting opinions
        good_dissents = successful_responses(dissent_responses or [])
        if good_dissents:
            lines.extend(["## Dissenting Opinions", ""])
            for resp in good_dissents:
                adv_alias = resp.alias.replace("Dissent-", "")
                lines.extend([
                    f"### {adv_alias} (Dissent)", "",
                    resp.content or "(no content)", "", "---", "",
                ])

        # Original submissions
        lines.extend(["## Original Advocate Submissions", ""])
        for resp in good_advocates:
            lines.extend([f"### {resp.alias}", "", resp.content or "(no content)", "", "---", ""])

    return "\n".join(lines)


def write_debrief(
    all_responses: dict[str, list[ModelResponse]],
    session_id: str,
    session_dir: Path,
    config: ConclaveConfig,
    wall_time: float,
    remand_count: int,
):
    """Write the comprehensive debrief report."""

    depth_name = config.depth.name
    minutes = int(wall_time // 60)
    seconds = int(wall_time % 60)

    every_response: list[ModelResponse] = []
    for phase_resps in all_responses.values():
        every_response.extend(phase_resps)

    cost = total_cost(every_response)
    in_tok, out_tok = total_tokens(every_response)

    good_advocates = successful_responses(all_responses.get("advocates", []))
    good_challenges = successful_responses(all_responses.get("challenges", []))
    good_debates = successful_responses(all_responses.get("debates", []))
    good_cardinals = successful_responses(all_responses.get("cardinals", []))
    good_fresh = successful_responses(all_responses.get("fresh_eyes", []))
    good_dissents = successful_responses(all_responses.get("dissents", []))

    # Load alias maps
    alias_map = {}
    if (session_dir / "alias-map.json").exists():
        alias_map = json.loads((session_dir / "alias-map.json").read_text())

    cardinal_alias_map = {}
    if (session_dir / "cardinal-alias-map.json").exists():
        cardinal_alias_map = json.loads((session_dir / "cardinal-alias-map.json").read_text())

    lines = [
        "# Tribunal Debrief Report",
        f"## Session: {session_id}",
        f"## Date: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        "", "---", "",
        "### Summary",
    ]

    if depth_name == "QUICK":
        lines.append(f"{len(good_advocates)} advocates independently addressed the task at QUICK depth.")
        lines.append("No challenges, debate, or judicial review was performed.")
    else:
        lines.append(
            f"{len(good_advocates)} advocates deliberated at {depth_name} depth: "
            f"{len(good_challenges)} challenges issued, "
            f"{len(good_debates)} debate exchanges across {config.depth.debate_rounds} round(s), "
            f"{len(good_cardinals)} judicial opinion(s)."
        )
        if remand_count > 0:
            lines.append(f"**{remand_count} remand(s)** were issued during the session.")
        if good_fresh:
            lines.append(f"Fresh Eyes validation performed by {good_fresh[0].display_name}.")
        if good_dissents:
            dissenter_names = [r.alias.replace('Dissent-', '') for r in good_dissents]
            lines.append(f"**{len(good_dissents)} dissenting opinion(s)** filed by: {', '.join(dissenter_names)}.")
        if (session_dir / "play-by-play.md").exists():
            lines.append("A play-by-play narrative of the debate is available in `play-by-play.md`.")

    # Panel composition
    lines.extend(["", "### Advocate Panel", "",
        "| Seat | Model | Provider | Status |",
        "|------|-------|----------|--------|"])
    for resp in all_responses.get("advocates", []):
        status = "✓" if resp.status == "success" else f"✗ {resp.error or 'Failed'}"
        lines.append(f"| {resp.alias} | {resp.display_name} | {resp.provider} | {status} |")

    if all_responses.get("cardinals"):
        lines.extend(["", "### The Bench", "",
            "| Seat | Model | Provider | Rank | Status |",
            "|------|-------|----------|------|--------|"])
        for resp in all_responses["cardinals"]:
            status = "✓" if resp.status == "success" else f"✗ {resp.error or 'Failed'}"
            lines.append(f"| {resp.alias} | {resp.display_name} | {resp.provider} | {resp.role} | {status} |")

    # Session statistics
    lines.extend(["", "### Session Statistics", "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Depth | {depth_name} |",
        f"| Advocates | {len(good_advocates)} succeeded / {len(all_responses.get('advocates', []))} dispatched |",
        f"| Challenges | {len(good_challenges)} |",
        f"| Debate Exchanges | {len(good_debates)} across {config.depth.debate_rounds} round(s) |",
        f"| Judges | {len(good_cardinals)} |",
        f"| Fresh Eyes | {'Yes' if good_fresh else 'No'} |",
        f"| Dissenting Opinions | {len(good_dissents)} |",
        f"| Remands | {remand_count} |",
        f"| Total Input Tokens | {in_tok:,} |",
        f"| Total Output Tokens | {out_tok:,} |",
        f"| Estimated Cost | ${cost:.4f} |",
        f"| Wall Clock Time | {minutes}m {seconds}s |",
    ])

    # Phase breakdown
    lines.extend(["", "### Phase Breakdown", "",
        "| Phase | Calls | OK | Tokens (out) | Cost | Wall Time |",
        "|-------|-------|----|-------------|------|-----------|"])
    for phase_name, phase_resps in all_responses.items():
        if not phase_resps:
            continue
        p_good = successful_responses(phase_resps)
        p_cost = total_cost(phase_resps)
        _, p_out = total_tokens(phase_resps)
        p_time = sum(r.elapsed for r in phase_resps)
        lines.append(
            f"| {phase_name} | {len(phase_resps)} | {len(p_good)} | "
            f"{p_out:,} | ${p_cost:.4f} | {p_time:.1f}s |"
        )

    # Identity reveals
    lines.extend(["", "### Identity Reveal — Advocates", "",
        "| Alias | Model | Provider |",
        "|-------|-------|----------|"])
    for alias, info in alias_map.items():
        lines.append(f"| {alias} | {info['model']} | {info['provider']} |")

    if cardinal_alias_map:
        lines.extend(["", "### Identity Reveal — The Bench", "",
            "| Alias | Model | Provider | Rank |",
            "|-------|-------|----------|------|"])
        for alias, info in cardinal_alias_map.items():
            lines.append(f"| {alias} | {info['model']} | {info['provider']} | {info.get('role', '')} |")

    if good_fresh:
        lines.extend(["", "### Identity Reveal — Fresh Eyes", "",
            f"| Fresh-Eyes | {good_fresh[0].display_name} | {good_fresh[0].provider} |"])

    lines.extend(["", "---", "",
        f"*Generated by The Tribunal v0.5.0 — session logs in `{session_dir}/`*"])

    (session_dir / "debrief.md").write_text("\n".join(lines))


def write_council_log(
    all_responses: dict[str, list[ModelResponse]],
    session_id: str,
    session_dir: Path,
    config: ConclaveConfig,
    wall_time: float,
    remand_count: int,
):
    """Write the machine-readable session log."""
    every_response: list[ModelResponse] = []
    for phase_resps in all_responses.values():
        every_response.extend(phase_resps)

    cost = total_cost(every_response)
    in_tok, out_tok = total_tokens(every_response)

    log_data = {
        "session_id": session_id,
        "version": "0.5.0",
        "depth": config.depth.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "wall_time_seconds": wall_time,
        "total_cost_usd": cost,
        "total_input_tokens": in_tok,
        "total_output_tokens": out_tok,
        "remand_count": remand_count,
        "phases": {},
    }

    for phase_name, phase_resps in all_responses.items():
        log_data["phases"][phase_name] = [
            {
                "alias": r.alias,
                "model_id": r.model_id,
                "display_name": r.display_name,
                "provider": r.provider,
                "role": r.role,
                "status": r.status,
                "elapsed": r.elapsed,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost": r.cost,
                "error": r.error,
            }
            for r in phase_resps
        ]

    (session_dir / "council-log.json").write_text(json.dumps(log_data, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="The Tribunal — Council Orchestrator")
    parser.add_argument("--briefing", required=True, help="Path to briefing file or '-' for stdin")
    parser.add_argument("--sealed-submission", default=None, help="Path to host agent's sealed submission")
    parser.add_argument("--depth", default=None, help="Depth level (QUICK|BALANCED|THOROUGH|RIGOROUS|EXHAUSTIVE)")
    parser.add_argument("--emit", default="summary", choices=["summary", "json", "paths"],
                        help="Output mode: summary (human-readable), json (structured), paths (file paths only)")
    parser.add_argument("--session-id", default=None, help="Override session ID (default: auto-generated)")
    parser.add_argument("--session-dir", default=None, help="Override session output directory (default: auto-generated)")
    parser.add_argument("--tts", action="store_true",
                        help="After session: generate screenplay then ElevenLabs audio MP3. "
                             "Requires ELEVENLABS_API_KEY and ffmpeg.")

    args = parser.parse_args()

    # Load config
    depth = args.depth or os.environ.get("CONCLAVE_DEFAULT_DEPTH", "QUICK")
    config = load_config(depth)

    # Read briefing (before session ID so we can extract a topical slug)
    if args.briefing == "-":
        briefing_text = sys.stdin.read()
    else:
        briefing_text = Path(args.briefing).read_text()

    # Generate session (use original briefing for slug — before enrichment)
    session_id = args.session_id or generate_session_id(briefing_text)
    if args.session_dir:
        session_dir = Path(args.session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
    else:
        session_dir = create_session_dir(config.log_dir, session_id)
    progress = Progress(session_id, config.depth.name)

    progress.session_start()

    # Bavest enrichment: detect ticker, prepend Data Room block if found
    briefing_text = enrich_briefing(briefing_text)
    if briefing_text.startswith("## Data Room"):
        progress.info("Data Room: Bavest fundamentals injected into briefing.")

    (session_dir / "briefing.md").write_text(briefing_text)

    # Read sealed submission
    sealed = None
    if args.sealed_submission:
        sealed = Path(args.sealed_submission).read_text()

    parse_briefing(briefing_text)

    # Select advocates
    advocates = select_advocates(config)
    progress.info(f"Selected {len(advocates)} advocates: {', '.join(a.display_name for a in advocates)}")

    # Track all responses by phase
    all_responses: dict[str, list[ModelResponse]] = {
        "advocates": [],
        "challenges": [],
        "debates": [],
        "cardinals": [],
        "dissents": [],
        "fresh_eyes": [],
        "narrative": [],
        "summary": [],
    }
    remand_count = 0

    start_time = time.time()

    # ================================================================
    # Phase 2-3: Advocate dispatch and collection
    # ================================================================
    advocate_responses = run_advocate_phase(
        advocates=advocates,
        briefing=briefing_text,
        sealed_submission=sealed,
        config=config,
        session_dir=session_dir,
        progress=progress,
    )
    all_responses["advocates"] = advocate_responses

    # ================================================================
    # Phase 4: Challenge round (BALANCED+)
    # ================================================================
    challenge_responses: list[ModelResponse] = []
    if config.depth.debate_rounds > 0:
        challenge_responses = run_challenge_phase(
            advocate_responses=advocate_responses,
            advocates=advocates,
            briefing=briefing_text,
            config=config,
            session_dir=session_dir,
            progress=progress,
        )
        all_responses["challenges"] = challenge_responses

    # Pre-compute cardinal availability (needed for NUCLEAR mid-debate checkpoint)
    has_cardinals = (config.depth.cardinals_bishops > 0 or
                     config.depth.cardinals_priests > 0 or
                     config.depth.cardinals_deacons > 0)
    cardinals: list[ModelDef] = []

    # ================================================================
    # Phase 5: Debate rounds (BALANCED+)
    # For NUCLEAR: split into two halves with judicial checkpoint
    # ================================================================
    debate_rounds: list[list[ModelResponse]] = []
    checkpoint_cardinal_responses: list[ModelResponse] = []

    if config.depth.debate_rounds > 0:
        checkpoint_round = config.depth.mid_debate_checkpoint  # 0 = no checkpoint

        if checkpoint_round > 0 and config.depth.debate_rounds > checkpoint_round:
            # ---- NUCLEAR-style: debate in two halves with judicial checkpoint ----
            progress.info(f"NUCLEAR mode: debate rounds 1-{checkpoint_round}, then judicial checkpoint, then rounds {checkpoint_round+1}-{config.depth.debate_rounds}")

            # Phase 5a: First half of debate
            first_half_config = deepcopy(config)
            first_half_config.depth = DepthConfig(
                **{**vars(config.depth), 'debate_rounds': checkpoint_round}
            )

            debate_rounds_1 = run_debate_phase(
                advocate_responses=advocate_responses,
                challenge_responses=challenge_responses,
                advocates=advocates,
                briefing=briefing_text,
                config=first_half_config,
                session_dir=session_dir,
                progress=progress,
            )
            debate_rounds.extend(debate_rounds_1)
            for round_resps in debate_rounds_1:
                all_responses["debates"].extend(round_resps)

            # Mid-debate judicial checkpoint
            if has_cardinals:
                progress.info(f"=== MID-DEBATE JUDICIAL CHECKPOINT (after round {checkpoint_round}) ===")
                cardinals = select_cardinals(config)

                # Build stability report from first half
                stability_report_1 = build_position_stability_report(
                    advocate_responses, debate_rounds_1
                )

                checkpoint_cardinal_responses, checkpoint_remand, checkpoint_reason = run_cardinal_phase(
                    advocate_responses=advocate_responses,
                    challenge_responses=challenge_responses,
                    debate_rounds=debate_rounds_1,
                    cardinals=cardinals,
                    briefing=briefing_text,
                    config=config,
                    session_dir=session_dir,
                    progress=progress,
                    stability_report=stability_report_1 if config.depth.position_stability_audit else "",
                    phase_label=f"Mid-debate judicial checkpoint (round {checkpoint_round})",
                )

                # Write checkpoint files with distinct names
                for resp in successful_responses(checkpoint_cardinal_responses):
                    path = session_dir / f"checkpoint-judge-{resp.alias.lower()}.md"
                    path.write_text(f"# Mid-Debate Checkpoint: {resp.alias}\n\n{resp.content}\n")

                all_responses["cardinals"].extend(checkpoint_cardinal_responses)

                # If judges say REMAND at checkpoint, we still continue
                # but flag it — they get to see both halves in the final judgment
                if checkpoint_remand:
                    progress.warn(f"Judges flagged concerns at checkpoint: {checkpoint_reason}")
                    (session_dir / "checkpoint-flag.md").write_text(
                        f"# Mid-Debate Judicial Flag\n\n{checkpoint_reason}\n"
                    )

            # Phase 5b: Second half of debate
            remaining_rounds = config.depth.debate_rounds - checkpoint_round
            second_half_config = deepcopy(config)
            second_half_config.depth = DepthConfig(
                **{**vars(config.depth), 'debate_rounds': remaining_rounds}
            )

            debate_rounds_2 = run_debate_phase(
                advocate_responses=advocate_responses,
                challenge_responses=challenge_responses,
                advocates=advocates,
                briefing=briefing_text,
                config=second_half_config,
                session_dir=session_dir,
                progress=progress,
                round_offset=checkpoint_round,
            )
            debate_rounds.extend(debate_rounds_2)
            for round_resps in debate_rounds_2:
                all_responses["debates"].extend(round_resps)

        else:
            # ---- Standard: all debate rounds in one go ----
            debate_rounds = run_debate_phase(
                advocate_responses=advocate_responses,
                challenge_responses=challenge_responses,
                advocates=advocates,
                briefing=briefing_text,
                config=config,
                session_dir=session_dir,
                progress=progress,
            )
            for round_resps in debate_rounds:
                all_responses["debates"].extend(round_resps)

    # Build final stability report (across ALL rounds)
    stability_report = ""
    if debate_rounds and config.depth.position_stability_audit:
        stability_report = build_position_stability_report(
            advocate_responses, debate_rounds
        )

    # ================================================================
    # Phase 6: Judicial review (BALANCED+)
    # ================================================================
    cardinal_responses: list[ModelResponse] = []

    if has_cardinals:
        if not cardinals:
            cardinals = select_cardinals(config)

        cardinal_responses, should_remand, remand_reason = run_cardinal_phase(
            advocate_responses=advocate_responses,
            challenge_responses=challenge_responses,
            debate_rounds=debate_rounds,
            cardinals=cardinals,
            briefing=briefing_text,
            config=config,
            session_dir=session_dir,
            progress=progress,
            stability_report=stability_report,
        )
        all_responses["cardinals"].extend(cardinal_responses)

        # Handle REMAND (maximum 1 per session)
        if should_remand and remand_count < 1:
            remand_count += 1
            progress.warn(f"REMAND #{remand_count} — running additional debate round...")

            (session_dir / "remand-brief.md").write_text(
                f"# Remand Brief\n\nThe judges sent the deliberation back.\n\n"
                f"**Reason**: {remand_reason}\n"
            )

            # Run one more debate round (offset by existing rounds)
            remand_debates = run_debate_phase(
                advocate_responses=advocate_responses,
                challenge_responses=challenge_responses,
                advocates=advocates,
                briefing=briefing_text,
                config=config,
                session_dir=session_dir,
                progress=progress,
                round_offset=len(debate_rounds),
            )
            for round_resps in remand_debates:
                all_responses["debates"].extend(round_resps)
            debate_rounds.extend(remand_debates)

            # Re-run judicial review
            progress.info("Re-running judicial review after remand...")

            # Rebuild stability report with remand rounds
            if config.depth.position_stability_audit:
                stability_report = build_position_stability_report(
                    advocate_responses, debate_rounds
                )

            cardinal_responses_2, _, _ = run_cardinal_phase(
                advocate_responses=advocate_responses,
                challenge_responses=challenge_responses,
                debate_rounds=debate_rounds,
                cardinals=cardinals,
                briefing=briefing_text,
                config=config,
                session_dir=session_dir,
                progress=progress,
                stability_report=stability_report,
            )
            all_responses["cardinals"].extend(cardinal_responses_2)
            cardinal_responses = cardinal_responses_2

    # ================================================================
    # Dissenting opinions (BALANCED+ — after verdict, before Fresh Eyes)
    # ================================================================
    dissent_responses: list[ModelResponse] = []

    if has_cardinals and debate_rounds and cardinal_responses:
        dissenters = detect_dissenters(
            advocate_responses, debate_rounds, cardinal_responses
        )
        if dissenters:
            dissent_responses = run_dissent_phase(
                dissenters=dissenters,
                advocate_responses=advocate_responses,
                advocates=advocates,
                cardinal_responses=cardinal_responses,
                briefing=briefing_text,
                config=config,
                session_dir=session_dir,
                progress=progress,
            )
            all_responses["dissents"] = dissent_responses
        else:
            progress.info("No dissenters detected — all advocates either conceded or were accepted.")

    # ================================================================
    # Phase 7: Fresh Eyes (EXHAUSTIVE and NUCLEAR)
    # ================================================================
    fresh_eyes_response: Optional[ModelResponse] = None
    seated_cardinal_ids = set()
    if has_cardinals:
        seated_cardinal_ids = {r.model_id for r in cardinal_responses}

    if config.depth.name in ("EXHAUSTIVE", "NUCLEAR"):
        prelim_output = build_final_output(
            advocate_responses, challenge_responses, debate_rounds,
            cardinal_responses, None, session_id, config,
        )
        fresh_eyes_response = run_fresh_eyes_phase(
            briefing=briefing_text,
            final_output_text=prelim_output,
            config=config,
            seated_cardinal_ids=seated_cardinal_ids,
            session_dir=session_dir,
            progress=progress,
        )
        if fresh_eyes_response:
            all_responses["fresh_eyes"] = [fresh_eyes_response]

    # ================================================================
    # Play-by-play narrative (BALANCED+)
    # ================================================================
    if config.depth.debate_rounds > 0:
        narrator_resps = generate_play_by_play(
            briefing=briefing_text,
            advocate_responses=advocate_responses,
            challenge_responses=challenge_responses,
            debate_rounds=debate_rounds,
            cardinal_responses=cardinal_responses,
            fresh_eyes_response=fresh_eyes_response,
            config=config,
            session_dir=session_dir,
            progress=progress,
        )
        if narrator_resps:
            all_responses["narrative"] = narrator_resps

    # ================================================================
    # Phase 8: Write output files
    # ================================================================
    wall_time = time.time() - start_time

    final_output = build_final_output(
        advocate_responses, challenge_responses, debate_rounds,
        cardinal_responses, fresh_eyes_response, session_id, config,
        dissent_responses=dissent_responses,
    )
    (session_dir / "final-output.md").write_text(final_output)

    write_debrief(all_responses, session_id, session_dir, config, wall_time, remand_count)
    write_council_log(all_responses, session_id, session_dir, config, wall_time, remand_count)

    # ---- Session summary (BALANCED+ only) ----
    if config.depth.debate_rounds > 0:
        summary_resp = generate_session_summary(
            briefing=briefing_text,
            advocate_responses=advocate_responses,
            challenge_responses=challenge_responses,
            debate_rounds=debate_rounds,
            cardinal_responses=cardinal_responses,
            fresh_eyes_response=fresh_eyes_response,
            all_responses=all_responses,
            session_id=session_id,
            session_dir=session_dir,
            config=config,
            wall_time=wall_time,
            remand_count=remand_count,
            progress=progress,
        )
        if summary_resp:
            all_responses["summary"] = [summary_resp]

    progress.session_done(str(session_dir))

    # ================================================================
    # Phase 9: Screenplay + TTS audio (--tts flag)
    # ================================================================
    if args.tts:
        try:
            import sys as _sys
            _scripts_dir = str(Path(__file__).parent)
            if _scripts_dir not in _sys.path:
                _sys.path.insert(0, _scripts_dir)
            from screenplay_generator import run_pipeline as run_screenplay
            progress.phase(9, "Screenplay — dramatising session into audio script...")
            run_screenplay(session_dir, act_count=4, progress=progress)
        except ImportError as _e:
            progress.info(f"Screenplay skipped (screenplay_generator not found): {_e}")
        except Exception as _e:
            progress.warn(f"Screenplay generation failed: {_e}")

        voice_script_path = session_dir / "voice-script.json"
        if args.tts and voice_script_path.exists():
            if not os.environ.get("ELEVENLABS_API_KEY"):
                progress.info("TTS skipped: ELEVENLABS_API_KEY not set.")
            else:
                try:
                    from tts_pipeline import run_pipeline as run_tts
                    progress.info("TTS — generating ElevenLabs audio...")
                    run_tts(
                        input_path=voice_script_path,
                        output_path=None,   # auto-names as <session-id>-audio.mp3
                        voice_map_path=None,
                        add_tags=True,
                        dry_run=False,
                    )
                except ImportError as _e:
                    progress.info(f"TTS skipped (tts_pipeline not found): {_e}")
                except Exception as _e:
                    progress.warn(f"TTS pipeline failed: {_e}")

    # ---- Emit output ----
    every_response: list[ModelResponse] = []
    for phase_resps in all_responses.values():
        every_response.extend(phase_resps)

    if args.emit == "summary":
        good_adv = successful_responses(all_responses["advocates"])
        cost = total_cost(every_response)
        files_in_dir = len(list(session_dir.iterdir()))
        print(f"TRIBUNAL SESSION COMPLETE: {session_id}")
        print(f"  Depth: {config.depth.name} | Advocates: {len(good_adv)} | Cost: ${cost:.4f}")
        print(f"  Files: {files_in_dir} artifacts in {session_dir}/")
        print(f"  Output:     {session_dir}/final-output.md")
        print(f"  Debrief:    {session_dir}/debrief.md")
        if (session_dir / "play-by-play.md").exists():
            print(f"  Play-by-play: {session_dir}/play-by-play.md")
        # Find the canonical summary files (YYYYMMDD-session-summary-*.{md,pdf})
        _summary_mds = sorted(session_dir.glob("[0-9]*-session-summary-*.md"))
        _summary_pdfs = sorted(session_dir.glob("[0-9]*-session-summary-*.pdf"))
        if _summary_mds:
            print(f"  Summary:    {_summary_mds[-1]}")
        elif (session_dir / "session-summary.md").exists():
            print(f"  Summary:    {session_dir}/session-summary.md")
        if _summary_pdfs:
            print(f"  PDF:        {_summary_pdfs[-1]}")
        elif (session_dir / "session-summary.pdf").exists():
            print(f"  PDF:        {session_dir}/session-summary.pdf")
        if (session_dir / "screenplay.md").exists():
            print(f"  Screenplay: {session_dir}/screenplay.md")
        # Audio: auto-named <session-id>-audio.mp3
        audio_path = session_dir / f"{session_id}-audio.mp3"
        if audio_path.exists():
            print(f"  Audio:      {audio_path}")
        print(f"  Log:        {session_dir}/council-log.json")
        print(f"")
        print(f"  ✅ Session saved to: {session_dir.resolve()}/")
    elif args.emit == "json":
        log = json.loads((session_dir / "council-log.json").read_text())
        print(json.dumps(log, indent=2))
    elif args.emit == "paths":
        for p in sorted(session_dir.iterdir()):
            print(str(p))


if __name__ == "__main__":
    main()
