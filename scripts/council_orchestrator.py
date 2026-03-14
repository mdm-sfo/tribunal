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
  T1 (Spot Check):       Phases 1-3, 8 (no debate, no judges, no summary)
  T2 (Standard Review):  Phases 1-6, 8 (challenge + 1 debate round + 1 Justice + session summary)
  T3 (Deep Review):      Phases 1-6, 8 (challenge + 3 debate rounds + 2 Justices + 1 Appellate Judge + session summary)
  T4 (Full Panel):       Phases 1-6, 8 (challenge + 5 debate rounds + full judicial panel + session summary)
  T5 (Stress Test):      Phases 1-8   (5 rounds + position-stability audit + Fresh Eyes + session summary)
  T6 (Red Team):         Phases 1-8   (7 rounds + mid-debate judicial checkpoint at R4 + stability audit + Fresh Eyes + session summary)
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

from config_loader import (
    load_config, ConclaveConfig, DepthConfig, ModelDef,
    BISHOPS, PRIESTS, DEACONS, FACT_CHECKER,
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

## Stance Commitment

If the briefing asks a directional question ("is this different?", "should we
do X?", "which is better?", "will Y happen?"), or requests your opinion, thoughts,
or position, you MUST:
- Open your Hypothesis with a clear, unambiguous directional answer
  (YES/NO/X-is-better/Y-will-happen). Not "it depends on several factors."
- Take a STRONG position and defend it. Hedging belongs in Counterargument
  Acknowledgment, not in your Hypothesis.
- If the evidence genuinely splits 50/50, say which side you lean toward and why.
  "The evidence is mixed" is not a position. "The evidence is mixed but favors X
  because [reason]" is a position.

The judges will penalize fence-sitting. A wrong-but-defended position that teaches
something is worth more than a hedge that commits to nothing.

## Depth Over Breadth

Go deep, not wide. Surface-level analysis listing many factors without exploring
any is worth less than deep analysis of 2-3 critical factors with specific
evidence, mechanisms, and implications.

WEAK: "Several factors will influence the outcome, including demand elasticity,
regulatory frameworks, and technological capability."
STRONG: "Demand elasticity is the decisive factor. Here is why: [specific
mechanism]. Historical parallel: when X happened, demand expanded by Y%
over Z years because [causal chain]. Applied to AI: [specific projection
with reasoning]."

The judges reward analytical depth — drilling into HOW and WHY — over
comprehensive surface coverage.

## Structure of the Ask

IMPORTANT: If the briefing specifies numbered deliverables, structured questions,
or explicit pillars (e.g., "(1) position, (2) reasoning, (3) scenarios"), you
MUST address each one explicitly and in order. Label your responses to match the
structure of the ask. Omitting or reordering a requested deliverable is grounds
for the judges to penalize your submission.

## Submission Format

Structure your response as:

### Hypothesis
One clear, falsifiable sentence about your approach or recommendation.
This MUST be a directional claim, not a conditional framework.

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
- GO DEEP: When defending your position, don't just repeat your top-level claim.
  Drill into the MECHANISM. If you claim Jevons Paradox will hold, explain the
  specific demand curve, the specific new job categories, the specific historical
  analogy with quantitative parallels. Surface-level defenses are weak defenses.
  The judges reward depth over breadth. One well-defended claim with specific
  evidence beats five shallow assertions.

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
For each advocate, assess their key claims. Tag each claim's evidence quality
to indicate the type of support offered:
- `PRIMARY_SOURCE` — original data, official statistics, direct measurement
- `PEER_REVIEWED` — published in peer-reviewed journals
- `SECONDARY` — reputable secondary reporting (textbooks, review articles, quality journalism)
- `ANECDOTAL` — single case studies, personal experience, one-off examples
- `EXPERT_OPINION` — stated by a recognized authority but without cited data
- `UNATTRIBUTED` — no source given; assertion presented as fact

| Advocate | Claim | Evidence Quality | Verdict | Notes |
|----------|-------|-----------------|---------|-------|
| Advocate-X | [claim] | [quality tag] | ✓ Verified / ⚠ Unverifiable / ✗ Incorrect | [detail] |

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

Choose ONE of **ACCEPT**, **SYNTHESIZE**, or **REMAND**.

**Guidance on verdict selection:**
- **Prefer ACCEPT** when one advocate's position is clearly stronger AND their
  framework subsumes the valid points from other advocates. Do not SYNTHESIZE
  out of diplomatic impulse — if one advocate won the debate, say so.
- **SYNTHESIZE** only when multiple advocates contributed genuinely distinct,
  non-overlapping insights that must be combined to answer the question. "Taking
  the best from everyone" is not synthesis — it's diplomatic avoidance. Real
  synthesis produces a position that no individual advocate held, built from
  incompatible-but-partially-correct components.
- **REMAND** when the evidence is genuinely insufficient to rule.

**Directional questions demand directional verdicts.** If the briefing asked
"is X different?", "should we do Y?", or "which approach is better?", your
verdict MUST answer that question directly. A verdict that restates conditions
without answering the question is a judicial failure.

Then fill in the structured verdict table below. You MUST have one row per
advocate. Every advocate gets an explicit ruling — no one is silently ignored.

**ACCEPT [Advocate-X]**:
| Advocate | Ruling | What | Rationale |
|----------|--------|------|-----------|
| Advocate-X | ACCEPT | [their core position] | [why it's strongest] |
| Advocate-Y | REJECT | [their core position] | [why — cite evidence or debate moments] |
| ... | ... | ... | ... |

**SYNTHESIZE**:
| Advocate | Ruling | What | Rationale |
|----------|--------|------|-----------|
| Advocate-X | ADOPT [element] | [specific element taken from this advocate] | [why this part is strong] |
| Advocate-X | REJECT [element] | [specific element rejected from same advocate] | [why — even #1-ranked advocates can have parts rejected] |
| Advocate-Y | ADOPT [element] | [specific element taken] | [why] |
| ... | ... | ... | ... |

**REMAND**: The evidence is still insufficient. Specify what's missing.
(Maximum 1 remand per session.)

**COHERENCE RULE**: If you rank an advocate #1 in the Ranking section but
then REJECT their core position in the Verdict, you MUST explain the
apparent contradiction. A #1 ranking means "argued best" — it does not
obligate you to accept their conclusion, but you must acknowledge and
explain the gap. Failure to do so is a judicial error.

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


MAJORITY_OPINION_SYSTEM_PROMPT = """\
You are the Chief Justice writing the Opinion of the Court in a Tribunal
deliberation. You have read all individual judicial opinions rendered by
the judges on The Bench.

Your job is to reconcile those opinions into a SINGLE, canonical ruling.
You must NOT introduce new analysis, new evidence, or new reasoning that
does not appear in at least one judicial opinion. You are a synthesizer,
not a new judge.

## Process

1. **Identify unanimous points** — where all (or nearly all) judges agree.
2. **Identify split decisions** — where judges disagree. Count votes. The
   majority position wins.
3. **Resolve conflicts** — when judges recommend contradictory actions,
   go with the majority. Note the minority view briefly.

## Output Format

### Verdict

State ONE of: **ACCEPT [Advocate-X]**, **SYNTHESIZE**, or **REMAND**.
This must reflect the majority of judicial opinions, not your own preference.

### Bottom Line

One to three sentences that DIRECTLY ANSWER the question asked in the briefing.
If the briefing asked "is X different?", start with "Yes", "No", or "Partially"
and explain. If it asked "which approach?", name the approach. If it asked for
a recommendation, state it. This must be a clear, directional statement — not
a framework, not conditions, not "it depends." Caveats come AFTER.

### Deliverable

The detailed, substantive answer to the user's question. Write this as a
clean consulting memo / analysis / recommendation — NOT as a judicial opinion.
Do NOT use phrases like "The Court recommends", "The Court finds", or
"The tribunal concludes." Write in direct analytical voice: "The recommended
approach is...", "The primary risk is...", "The evidence supports...".

Go deep: explain the mechanisms, the evidence, the specific implications.
Do not just state conclusions — explain WHY with reference to the strongest
evidence from the deliberation.

If the original briefing contained numbered deliverables or structured
pillars, mirror that structure here. Each pillar should receive substantive
analysis, not just a summary conclusion.

### Fact-Check Summary

A merged table of key claims that were verified, corrected, or rejected
across all judicial opinions. Deduplicate — if 3 judges verified the same
claim, list it once. Format:

| Claim | Verdict | Source |
|-------|---------|--------|

### Unanimous Points

Bullet list of positions where all or nearly all judges agreed.

### Contested Points

For each point where judges split:
- **Issue**: What was contested
- **Majority position** (N-M vote): What the majority ruled
- **Minority view**: What the dissenting judge(s) argued

### Unresolved Questions

Deduplicated list of questions that remain open after deliberation, with
confidence levels where judges provided them. Format each as:
- **Question**: [The question]
- **Why it matters**: [Brief explanation]
- **Confidence**: [Highest confidence estimate from any judge]

## Rules
- Do NOT add your own analysis. Only reconcile existing opinions.
- Do NOT drop elements that the majority of judges included.
- Do NOT introduce elements that no judge mentioned.
- If all judges agree, say so — don't manufacture disagreement.
- Write the Deliverable in clean analytical voice, not judicial voice.
- Keep it under 4000 words."""


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

Your job is to be the last line of defense — a rigorous sanity check before the
result is delivered to the user. You check for clarity, completeness, logical
soundness, and evidence-claim alignment.

## What you receive
- The original briefing (the question)
- The final synthesized output from the judicial review phase

## Review Format

### First Impression
What does this output look like to someone seeing it cold? Is it clear? Complete?
Does it read as authoritative or hedged? Would a domain expert find it credible?

### Red Flags
Anything that seems:
- Wrong or unsupported
- Missing or incomplete
- Confusing or contradictory
- Over-confident without evidence

### Logical Fallacy Scan

Check the output for these specific fallacies. For each one found, cite the
passage and rate severity (HIGH = undermines a core conclusion, LOW = minor
rhetorical issue that doesn't affect the substance).

| # | Fallacy | Present? | Passage | Severity | Impact on Conclusion |
|---|---------|----------|---------|----------|---------------------|
| 1 | False Cause (correlation ≠ causation) | YES/NO | [quote or "—"] | HIGH/LOW/— | [how it affects the output] |
| 2 | Circular Reasoning | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 3 | Cherry-Picking (selective evidence) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 4 | Appeal to Authority (unqualified or irrelevant) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 5 | Straw Man (misrepresenting a position) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 6 | False Dichotomy (only two options presented) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 7 | Hasty Generalization (small sample → broad claim) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 8 | Slippery Slope (chain of unlikely consequences) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 9 | Ad Hominem (attacking the source, not the argument) | YES/NO | [quote or "—"] | HIGH/LOW/— | |
| 10 | Equivocation (shifting meaning of a key term) | YES/NO | [quote or "—"] | HIGH/LOW/— | |

If no fallacies are found, state "No logical fallacies detected" and leave
the table with all NO entries.

### Evidence-Claim Alignment

For each major conclusion in the output, assess whether it is adequately
supported by the evidence actually cited (not just plausible in the abstract).

| # | Conclusion | Evidence Cited | Alignment | Explanation |
|---|-----------|---------------|-----------|-------------|
| 1 | [conclusion from output] | [what evidence supports it] | STRONG/WEAK/MISSING | [why] |

- **STRONG**: Conclusion follows logically from cited evidence
- **WEAK**: Some evidence exists but gaps remain, or the leap is too large
- **MISSING**: Conclusion is stated without any supporting evidence

### Unsupported Assertions

List any factual claims presented as established truth with no evidence,
citation, or reasoning to back them up:
- "[claim]" — no evidence provided
- (If none found, state "No unsupported assertions detected.")

### Completeness Check
Does this output actually answer the original question? Fully? Are there
obvious angles or considerations that were missed entirely?

### Final Verdict
One of:
- **APPROVE**: Ship it. The output is logically sound and well-supported.
- **FLAG-HIGH [issue]**: A significant logical or evidentiary problem that
  materially affects the conclusion. The user should be warned.
- **FLAG-LOW [issue]**: A minor issue (rhetorical weakness, small gap) that
  the user should be aware of but that doesn't undermine the core output.
- **REJECT**: The output has a fundamental flaw — unsound reasoning, missing
  evidence for a core claim, or a logical fallacy that invalidates a key
  conclusion. (Explain what.)

You are the user's last advocate. Be honest, systematic, and precise."""


CLAIM_EXTRACTION_SYSTEM_PROMPT = """\
You are an analytical reviewer extracting a structured claim-evidence matrix
from a Tribunal deliberation record. Your job is to identify every significant
factual claim made during the session and trace how it fared through the
adversarial process.

## What you receive
- The original briefing (question)
- Advocate submissions (initial positions)
- Challenge round (advocates questioning each other)
- Debate rounds (adversarial exchange)
- Judicial opinions (judges evaluating claims)

## Output Format

Produce a markdown table with 10-25 rows, ordered by importance to the final
conclusion (most important first):

| # | Claim | Source | Evidence Cited | Evidence Quality | Judge Verification | Survived Debate |
|---|-------|--------|---------------|-----------------|-------------------|----------------|
| 1 | [specific factual claim] | [Advocate-X] | [what evidence they cited] | [quality tag] | [what judges said] | ✓ Yes / ✗ No / ⚠ Contested |

**Evidence Quality tags** (use exactly these):
- `PRIMARY_SOURCE` — original data, official statistics, direct measurement
- `PEER_REVIEWED` — published in peer-reviewed journals
- `SECONDARY` — reputable secondary reporting (textbooks, review articles)
- `ANECDOTAL` — single case studies, personal experience
- `EXPERT_OPINION` — stated by a recognized authority without cited data
- `UNATTRIBUTED` — no source given; assertion presented as fact
- `NONE` — no evidence offered at all

**Survived Debate** means the claim was either:
- ✓ **Yes**: Not challenged, or challenged and successfully defended
- ✗ **No**: Challenged and the advocate conceded or abandoned it
- ⚠ **Contested**: Challenged but neither side conclusively won

## Rules
- Only include claims that are factual assertions (not opinions, frameworks, or recommendations)
- If a claim was made by multiple advocates, credit the first to make it
- If judges explicitly verified or disputed a claim, note their finding
- If no judge addressed a claim, write "Not reviewed" in Judge Verification
- End with a one-line summary: "X of Y claims supported by strong evidence; Z unattributed."

Be precise. This matrix is the evidentiary backbone of the session record."""


EVIDENCE_INJECTION_SYSTEM_PROMPT = """\
You are a research analyst with web search access. Your job is to find
COUNTER-EVIDENCE — real-world data, published research, or documented cases
that CONTRADICT or complicate the claims made by experts in a structured
deliberation.

For each advocate's top 2-3 claims, search for:
1. Data points that directly contradict the claim
2. Cases or examples that undermine the generalization
3. Methodological problems with the cited evidence
4. More recent data that supersedes what was cited

## Output Format

For each advocate, produce:

### Counter-Evidence for [Advocate-X]

**Claim:** "[their specific claim]"
**Counter-evidence:** [what you found, with sources and URLs where possible]
**Strength:** STRONG (directly contradicts) / MODERATE (complicates) / WEAK (tangential)

---

If you find NO credible counter-evidence for a claim, say so explicitly —
that's valuable signal for the debate.

Be specific. Cite URLs, dates, and numbers. Do not fabricate sources.
Focus on the 2-3 most important claims per advocate — the ones that,
if wrong, would collapse their entire argument."""


STATE_OF_PLAY_SYSTEM_PROMPT = """\
You are a debate moderator producing a brief, factual summary of what just
happened in a debate round. You are NOT a participant — you are a neutral
observer documenting the state of play.

Produce a summary in EXACTLY this format (keep it under 300 words):

### State of Play After Round {round_num}

**Concessions made this round:**
- [Advocate-X] conceded [specific point] to [Advocate-Y]
(list all concessions, or "None" if no concessions were made)

**Positions that shifted:**
- [Advocate-X]: [brief description of how their position changed]
(list all shifts, or "No positions shifted" if all held firm)

**Key counter-attacks launched:**
- [Advocate-X] challenged [Advocate-Y] on [specific point]
(list new challenges introduced this round)

**Still contested (unresolved cruxes):**
- [specific factual or analytical question that remains open]

**Current alignment:**
[One sentence: are advocates converging, diverging, or holding steady?]

Be factual. Do not editorialize. Do not assess who is "winning."
Quote specific claims and concessions — do not summarize vaguely."""


STABILITY_ASSESSMENT_SYSTEM_PROMPT = """\
You are an analytical reviewer comparing two versions of an expert's position
to assess how much it changed between debate rounds.

You will receive:
- The expert's PREVIOUS position (from the prior round or initial submission)
- The expert's CURRENT position (from this round)

Assess the change on this scale:
1 = ROCK SOLID — No meaningful change in substance. Same thesis, same evidence,
    same conclusion.
2 = MINOR REFINEMENT — Wording or scope adjusted, but the core thesis and
    evidence are intact.
3 = SIGNIFICANT REFINEMENT — A key supporting argument was conceded or modified,
    but the overall conclusion survived.
4 = MAJOR REVISION — The expert changed a substantial part of their argument.
    The conclusion may be the same but the reasoning is fundamentally different,
    OR the conclusion shifted meaningfully.
5 = POSITION ABANDONED — The expert's current position contradicts or is
    incompatible with their previous position.

Also assess whether the change was EVIDENCE-BASED (the expert cited new data or
responded to a specific factual challenge) or PRESSURE-BASED (the expert softened
their position without citing new evidence).

## Output Format (EXACTLY this — no other text):

Stability: [1-5]
Change type: [EVIDENCE-BASED / PRESSURE-BASED / NO CHANGE]
Summary: [One sentence describing what changed and why]"""


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

    Skips generic headers (e.g. "Tribunal Briefing", "Task") and looks
    for the first sentence with real topical content.  Common question
    prefixes ("should we", "what is the best") are stripped so the slug
    focuses on the topic, not the framing.
    Returns an empty string if nothing usable remains.
    """
    # Generic/meta headers that don't describe the actual topic
    _SKIP_HEADERS = {
        "tribunal briefing", "briefing", "task", "context",
        "deliverable", "task type", "overview", "background",
        "summary", "introduction", "question",
        "data room", "supplementary context", "enrichment",
    }

    # Collect candidate lines, prioritizing "Question:" lines which
    # tend to be the most topical part of a briefing.
    question_line = ""
    candidates: list[str] = []
    for line in briefing.strip().splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped:
            continue
        # Skip lines that are just generic section headers
        if stripped.lower().rstrip(":") in _SKIP_HEADERS:
            continue
        # Skip markdown bold-only labels like "**Question:**"
        label = re.sub(r'[*_`:\s]', '', stripped).lower()
        if label in _SKIP_HEADERS:
            continue
        # Skip Data Room disclaimer and other meta-instruction lines
        lower = stripped.lower()
        if any(phrase in lower for phrase in (
            "the following data was gathered",
            "may or may not be relevant",
            "supplementary context",
            "do not anchor on this data",
        )):
            continue
        # Prefer lines that start with "Question:" — they're the real topic
        if re.match(r'^\*{0,2}question\*{0,2}\s*:', stripped, re.IGNORECASE):
            question_line = re.sub(r'^\*{0,2}question\*{0,2}\s*:\s*', '', stripped, flags=re.IGNORECASE)
        candidates.append(stripped)
    if not candidates and not question_line:
        return ""

    first_line = question_line if question_line else candidates[0]
    # Strip header-style prefixes from the line itself
    # (handles "Tribunal Briefing: actual topic" on a single line)
    for hdr in ("tribunal briefing:", "briefing:"):
        if first_line.lower().startswith(hdr):
            first_line = first_line[len(hdr):].strip()
            break
    # Take first sentence, but if it's short (≤4 real words after cleaning),
    # grab the next sentence fragment too (handles "Is X? Y and Z" patterns)
    fragments = re.split(r'[.?!]', first_line)
    sentence = fragments[0].strip()
    if len(re.sub(r'[^a-z\s]', '', sentence.lower()).split()) <= 4 and len(fragments) > 1:
        sentence = sentence + " " + fragments[1].strip()
    cleaned = re.sub(r'[^a-z0-9\s]', '', sentence.lower()).strip()
    # Strip common question/filler prefixes so the slug is topical
    prefixes = [
        "should we use ", "should we ", "should i use ", "should i ",
        "what is the best way to ", "what is the best approach to ",
        "what is the best approach for ", "what is the best method to ",
        "what is the best ", "what are the best ",
        "what is the ", "what are the ", "what is ", "what are ",
        "is this time different for ", "is this time different ",
        "is this time ", "this time is different for ", "this time is different ",
        "is it better to use ", "is it better to ", "is it true that ",
        "is there ", "are there ",
        "how should we ", "how should i ", "how do we ", "how do i ",
        "how to ", "how can we ", "how can i ", "how will ",
        "can we ", "can i ", "can you ",
        "write a ", "write an ", "create a ", "create an ",
        "review this ", "review the ", "review our ",
        "compare ", "evaluate ", "analyze ", "analyse ",
        "design a ", "design an ", "build a ", "build an ",
        "a home user has ", "a home user with ", "a home user ",
        "a user has ", "a user with ", "a user ",
        "the user has ", "the user wants to ", "the user ",
        "i have ", "i want to ", "i need to ", "i would like to ",
        "we have ", "we want to ", "we need to ",
        "given that ", "assuming ", "considering ",
        "when will ", "when does ", "when do ",
        "will the ", "will ai ", "will ",
        "has ", "with ",
    ]
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    words = cleaned.split()
    # Remove stopwords and "tribunal" (avoid dup with session-id prefix)
    _STOPWORDS = {
        "tribunal", "briefing", "task", "question",
        "a", "an", "the", "of", "to", "in", "on", "for",
        "is", "are", "was", "were", "be", "been", "being",
        "each", "every", "all", "any", "some", "our", "my", "your",
        "their", "its", "this", "that", "these", "those",
        "and", "or", "but", "with", "from", "by", "at", "as",
        "into", "through", "during", "before", "after",
        "has", "have", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might",
        "wants", "want", "need", "needs",
        "set", "up", "get", "got",
        "most", "important", "things", "best",
        "like", "just", "about", "between", "over", "more",
        "really", "actually", "currently", "using",
        "way", "based", "given", "know", "one",
        "turning", "three", "two", "four", "five",
    }
    words = [w for w in words if w not in _STOPWORDS]
    slug = "-".join(words[:max_words])
    # Cap total length to keep directory names sane
    return slug[:60] if slug else ""


def generate_session_id(briefing: Optional[str] = None) -> str:
    """Create a session ID like ``20260302-rust-vs-go-cli``.

    Date-first for natural sorting, no redundant prefix (already inside
    tribunal-sessions/), tight topical slug.  If a directory with the
    same name already exists, a short numeric suffix is appended.
    """
    date = datetime.now().strftime("%Y%m%d")
    slug = _slugify_briefing(briefing) if briefing else ""
    if slug:
        return f"{date}-{slug}"
    return date


class SessionDir:
    """Organized session output directory.

    Top-level:
        session-summary.md/pdf  — THE canonical deliverable
        briefing.md             — the original question

    Subdirectories:
        submissions/    — advocate sealed submissions
        deliberation/   — challenges, debates, claim verification, stability
        judicial/       — judge opinions, dissents, coherence flags
        narrative/      — play-by-play, debrief
        meta/           — council-log.json, alias maps, final-output.md
    """

    def __init__(self, root: Path):
        self.root = root
        self.submissions = root / "submissions"
        self.deliberation = root / "deliberation"
        self.judicial = root / "judicial"
        self.narrative = root / "narrative"
        self.meta = root / "meta"
        # Create all subdirectories
        for d in (self.submissions, self.deliberation,
                  self.judicial, self.narrative, self.meta):
            d.mkdir(parents=True, exist_ok=True)

    # Path-like proxies so SessionDir can be passed where Path is expected
    @property
    def name(self) -> str:
        return self.root.name

    def exists(self) -> bool:
        return self.root.exists()

    def iterdir(self):
        return self.root.iterdir()

    def glob(self, pattern: str):
        return self.root.glob(pattern)

    def rglob(self, pattern: str):
        return self.root.rglob(pattern)

    def __truediv__(self, other: str) -> Path:
        """Allow sdir / 'filename' for top-level files."""
        return self.root / other

    def __str__(self) -> str:
        return str(self.root)

    def __fspath__(self) -> str:
        return str(self.root)

    def resolve(self) -> Path:
        return self.root.resolve()


def create_session_dir(base_dir: str, session_id: str) -> SessionDir:
    root = Path(base_dir) / session_id
    # Handle collisions (same date + slug) with numeric suffix
    if root.exists():
        n = 2
        while (Path(base_dir) / f"{session_id}-{n}").exists():
            n += 1
        root = Path(base_dir) / f"{session_id}-{n}"
    root.mkdir(parents=True, exist_ok=True)
    return SessionDir(root)


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
        (session_dir.submissions / "sealed-hypothesis.md").write_text(
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
        path = session_dir.submissions / f"submission-{resp.alias.lower()}.md"
        path.write_text(f"# Submission: {resp.alias}\n\n{resp.content}\n")

    # Write alias map (revealed only in debrief)
    alias_map = {r.alias: {"model": r.display_name, "provider": r.provider} for r in good}
    (session_dir.meta / "alias-map.json").write_text(json.dumps(alias_map, indent=2))

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
            path = session_dir.deliberation / f"challenge-by-{adv_alias.lower()}.md"
            path.write_text(f"# Challenges by {adv_alias}\n\n{cr.content}\n")

    good_challenges = successful_responses(challenge_responses)
    progress.info(f"Challenges issued: {len(good_challenges)}/{len(challenge_responses)}")

    return challenge_responses


# ---------------------------------------------------------------------------
# Phase 4.5: Evidence injection (counter-evidence via Perplexity Sonar Pro)
# ---------------------------------------------------------------------------

def run_evidence_injection(
    advocate_responses: list[ModelResponse],
    briefing: str,
    config: ConclaveConfig,
    session_dir: SessionDir,
    progress: Progress,
) -> str:
    """Search for real-world counter-evidence to each advocate's top claims.

    Uses Perplexity Sonar Pro (web search enabled) to find data that
    contradicts or complicates advocates' arguments. The counter-evidence
    brief is injected into the next debate round to stress-test positions
    with external data, not just argumentative pressure.

    Returns the counter-evidence brief as a string, or empty string if
    the injection fails or is skipped.
    """
    good_advocates = successful_responses(advocate_responses)
    if len(good_advocates) < 2:
        return ""

    progress.phase(4, "Evidence injection — searching for counter-evidence (Perplexity)...")

    claims_summary = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good_advocates
    )

    injection_prompt = (
        f"## Original Question\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Expert Positions\n\n{claims_summary}\n\n"
        f"{'=' * 60}\n\n"
        f"For each expert above, identify their 2-3 strongest claims and "
        f"search for real-world counter-evidence. Focus on claims that, if "
        f"wrong, would collapse their entire argument."
    )

    resp = call_model(
        model=FACT_CHECKER,
        system_prompt=EVIDENCE_INJECTION_SYSTEM_PROMPT,
        user_prompt=injection_prompt,
        alias="Evidence-Injection",
        timeout=config.depth.timeout_per_model,
        temperature=0.3,
        max_tokens=4096,
        progress=progress,
    )

    if resp.status != "success" or not resp.content:
        progress.warn("Evidence injection failed — continuing without counter-evidence")
        return ""

    brief = resp.content
    path = session_dir.deliberation / "counter-evidence-brief.md"
    path.write_text(f"# Counter-Evidence Brief\n\n{brief}\n")
    progress.info(f"Counter-evidence brief written ({len(brief)} chars)")
    return brief


# ---------------------------------------------------------------------------
# Debate helpers: state-of-play memo, external stability
# ---------------------------------------------------------------------------

def generate_state_of_play(
    round_num: int,
    round_responses: list[ModelResponse],
    latest_positions: dict[str, str],
    config: ConclaveConfig,
    progress: Progress,
) -> str:
    """Generate a brief state-of-play memo after a debate round.

    Uses Cerebras Qwen (fast, cheap) to compress the round's outcomes
    into a shared understanding for the next round. This closes the
    information gap caused by parallel dispatch — advocates in the next
    round see what everyone conceded, defended, and challenged.

    Returns the memo as a string, or empty string on failure.
    """
    good_round = successful_responses(round_responses)
    if not good_round:
        return ""

    round_text = "\n\n---\n\n".join(
        f"### {r.alias}\n\n{r.content}" for r in good_round
    )

    memo_prompt = (
        f"## Debate Round {round_num} — All Responses\n\n{round_text}\n\n"
        f"{'=' * 60}\n\n"
        f"Produce a state-of-play memo for round {round_num}. "
        f"Be factual and specific — quote actual claims and concessions."
    )

    # Use Cerebras Qwen (BISHOPS[0]) — fast and cheap
    memo_model = config.bishops[0] if config.bishops else BISHOPS[0]

    resp = call_model(
        model=memo_model,
        system_prompt=STATE_OF_PLAY_SYSTEM_PROMPT.replace("{round_num}", str(round_num)),
        user_prompt=memo_prompt,
        alias=f"State-of-Play-R{round_num}",
        timeout=30,
        temperature=0.2,
        max_tokens=1024,
        progress=progress,
    )

    if resp.status != "success" or not resp.content:
        progress.warn(f"State-of-play memo for round {round_num} failed — continuing without it")
        return ""

    return resp.content


def compute_round_stability(
    round_num: int,
    previous_positions: dict[str, str],
    current_positions: dict[str, str],
    config: ConclaveConfig,
    progress: Progress,
) -> dict[str, dict]:
    """Compute external stability scores by comparing positions across rounds.

    Instead of relying on self-reported stability scores (which create a
    perverse incentive to under-report changes), this uses a lightweight
    model to semantically compare each advocate's previous and current
    positions.

    Returns a dict mapping alias -> {score: int, change_type: str, summary: str}
    """
    stability_model = config.bishops[0] if config.bishops else BISHOPS[0]
    results: dict[str, dict] = {}

    # Build all stability assessment calls for parallel dispatch
    stability_calls = []
    aliases_order = []

    for alias in previous_positions:
        if alias not in current_positions:
            continue
        prev = previous_positions[alias]
        curr = current_positions[alias]

        assess_prompt = (
            f"## PREVIOUS position ({alias}):\n\n{prev[:3000]}\n\n"
            f"{'=' * 60}\n\n"
            f"## CURRENT position ({alias}):\n\n{curr[:3000]}"
        )

        stability_calls.append({
            "model": stability_model,
            "system_prompt": STABILITY_ASSESSMENT_SYSTEM_PROMPT,
            "user_prompt": assess_prompt,
            "alias": f"Stability-R{round_num}-{alias}",
            "timeout": 20,
            "temperature": 0.1,
            "max_tokens": 256,
        })
        aliases_order.append(alias)

    if not stability_calls:
        return results

    responses = fan_out_multi(stability_calls, progress=None)

    for alias, resp in zip(aliases_order, responses):
        if resp.status != "success" or not resp.content:
            results[alias] = {"score": 0, "change_type": "UNKNOWN", "summary": "Assessment failed"}
            continue

        content = resp.content.strip()
        score_match = re.search(r'Stability:\s*([1-5])', content)
        type_match = re.search(r'Change type:\s*(EVIDENCE-BASED|PRESSURE-BASED|NO CHANGE)', content)
        summary_match = re.search(r'Summary:\s*(.+)', content)

        results[alias] = {
            "score": int(score_match.group(1)) if score_match else 0,
            "change_type": type_match.group(1) if type_match else "UNKNOWN",
            "summary": summary_match.group(1).strip() if summary_match else content[:100],
        }

    return results


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
    external_stability_log: list[dict[str, dict]] | None = None,
) -> str:
    """Build a position stability scorecard across all debate rounds.

    This is the Kelley-Riedl (2026) inspired flip-rate report. When external
    stability scores are available (computed via semantic diff), those are
    used. Otherwise falls back to self-reported scores from advocate responses.
    """
    good_advocates = successful_responses(advocate_responses)
    aliases = [r.alias for r in good_advocates]
    use_external = bool(external_stability_log and len(external_stability_log) == len(debate_rounds))

    if not debate_rounds:
        return "(No debate rounds — position stability tracking not applicable)"

    source_label = "externally assessed" if use_external else "self-reported"
    lines = [
        "## Position Stability Scorecard",
        "",
        f"Tracks each advocate's position stability across debate rounds ({source_label}).",
        "Scale: 1=Rock Solid, 2=Minor Refinement, 3=Significant Refinement, 4=Major Revision, 5=Position Abandoned",
        "",
    ]

    if use_external:
        lines.append(
            "| Advocate | " + " | ".join(f"R{i+1}" for i in range(len(debate_rounds)))
            + " | Avg | Change Type | Drift Risk |"
        )
        lines.append(
            "|----------|" + "|".join("-----" for _ in debate_rounds)
            + "|-----|-------------|------------|"
        )
    else:
        lines.append(
            "| Advocate | " + " | ".join(f"R{i+1}" for i in range(len(debate_rounds)))
            + " | Avg | Drift Risk |"
        )
        lines.append(
            "|----------|" + "|".join("-----" for _ in debate_rounds)
            + "|-----|------------|"
        )

    for alias in aliases:
        scores = []
        change_types = []

        for round_idx, round_resps in enumerate(debate_rounds):
            if use_external:
                ext_data = external_stability_log[round_idx].get(alias, {})
                score = ext_data.get("score", 0)
                change_types.append(ext_data.get("change_type", "UNKNOWN"))
            else:
                score = 0
                good_round = successful_responses(round_resps)
                for r in good_round:
                    if alias in r.alias:
                        score = _extract_position_stability(r.content)
                        break
            scores.append(score)

        valid_scores = [s for s in scores if s > 0]
        avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0

        # Drift risk — enhanced with change type when available
        pressure_count = sum(1 for ct in change_types if ct == "PRESSURE-BASED")
        if use_external and pressure_count >= 2:
            drift = "\u26a0\ufe0f HIGH (pressure-based shifts)"
        elif avg >= 4.0:
            drift = "\u26a0\ufe0f HIGH"
        elif avg >= 3.0:
            drift = "\u26a0 MEDIUM"
        elif any(s >= 4 for s in valid_scores):
            drift = "\u26a0 MEDIUM (spike)"
        elif use_external and pressure_count >= 1:
            drift = "\u26a0 MEDIUM (pressure-based)"
        else:
            drift = "\u2713 LOW"

        score_strs = [str(s) if s > 0 else "-" for s in scores]

        if use_external:
            dominant_type = max(set(change_types), key=change_types.count) if change_types else "N/A"
            lines.append(
                f"| {alias} | " + " | ".join(score_strs)
                + f" | {avg:.1f} | {dominant_type} | {drift} |"
            )
        else:
            lines.append(
                f"| {alias} | " + " | ".join(score_strs) + f" | {avg:.1f} | {drift} |"
            )

    # Overall assessment
    all_scores = []
    all_change_types = []
    if use_external:
        for round_data in external_stability_log:
            for alias, data in round_data.items():
                if data.get("score", 0) > 0:
                    all_scores.append(data["score"])
                    all_change_types.append(data.get("change_type", "UNKNOWN"))
    else:
        for round_resps in debate_rounds:
            for r in successful_responses(round_resps):
                s = _extract_position_stability(r.content)
                if s > 0:
                    all_scores.append(s)

    if all_scores:
        overall_avg = sum(all_scores) / len(all_scores)
        pressure_pct = (
            sum(1 for ct in all_change_types if ct == "PRESSURE-BASED") / len(all_change_types) * 100
            if all_change_types else 0
        )
        lines.extend(["", f"**Overall average stability: {overall_avg:.1f}**"])

        if use_external and pressure_pct > 30:
            lines.append(
                f"\u26a0\ufe0f **WARNING: {pressure_pct:.0f}% of position changes were pressure-based "
                f"(no new evidence cited). High sycophantic drift risk.**"
            )
        elif overall_avg >= 3.5:
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
    session_dir: SessionDir,
    progress: Progress,
    round_offset: int = 0,
    counter_evidence_brief: str = "",
) -> list[list[ModelResponse]]:
    """Phase 5: Adversarial debate rounds with parallel dispatch.

    Each round:
    1. Each advocate receives challenges directed at them
    2. All advocates respond IN PARALLEL (their prompts are pre-built from
       the previous round's state, so they're independent)
    3. They must DEFEND, CONCEDE, or REVISE for each challenge
    4. They can launch counter-attacks
    5. The counter-attacks become input for the next round

    Between rounds:
    - A state-of-play memo compresses the round's outcomes into a shared
      understanding (closes the parallel dispatch information gap)
    - External stability scores are computed by comparing positions
      semantically (replaces self-reported stability)

    Counter-evidence from Perplexity is injected starting at Round 2 (or
    Round 1 if round_offset > 0, meaning this is a second-half dispatch).

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
    state_of_play_memo: str = ""
    external_stability_log: list[dict[str, dict]] = []

    for round_num in range(1, max_rounds + 1):
        actual_round = round_num + round_offset
        progress.phase(5, f"Debate round {actual_round} — advocates defending positions (parallel)...")

        # Snapshot positions before this round (for external stability diff)
        positions_before_round = dict(latest_positions)

        # Decide whether to inject counter-evidence this round
        # Inject at Round 2 (after advocates have established positions in R1)
        # or at the first round of a second-half dispatch
        inject_evidence = (
            counter_evidence_brief
            and ((actual_round == 2) or (round_num == 1 and round_offset > 0))
        )

        # Build all debate calls for this round (to dispatch in parallel)
        debate_calls = []
        call_alias_map = []

        for resp in good_advocates:
            model = alias_to_model.get(resp.alias)
            if model is None:
                continue

            my_challenges = _extract_challenges_for(resp.alias, current_challenges)

            other_positions = "\n\n---\n\n".join(
                f"### {alias} (current position)\n\n{pos}"
                for alias, pos in latest_positions.items()
                if alias != resp.alias
            )

            # Build the debate prompt with optional state-of-play and evidence
            debate_prompt = (
                f"## Original Briefing\n\n{briefing}\n\n"
                f"{'=' * 60}\n\n"
            )

            # Include state-of-play memo from the previous round
            if state_of_play_memo:
                debate_prompt += (
                    f"## State of Play (what happened last round)\n\n"
                    f"{state_of_play_memo}\n\n"
                    f"{'=' * 60}\n\n"
                )

            debate_prompt += (
                f"## Your Current Position ({resp.alias})\n\n{latest_positions[resp.alias]}\n\n"
                f"{'=' * 60}\n\n"
                f"## Challenges Directed at You\n\n{my_challenges}\n\n"
                f"{'=' * 60}\n\n"
                f"## Other Advocates' Current Positions\n\n{other_positions}\n\n"
                f"{'=' * 60}\n\n"
            )

            # Inject counter-evidence at the designated round
            if inject_evidence:
                debate_prompt += (
                    f"## Counter-Evidence Brief (from independent research)\n\n"
                    f"The following counter-evidence was gathered by an independent "
                    f"researcher with web search access. It may challenge claims "
                    f"you or other advocates have made. Address any evidence that "
                    f"is relevant to your position.\n\n"
                    f"{counter_evidence_brief}\n\n"
                    f"{'=' * 60}\n\n"
                )

            debate_prompt += (
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
                parts = debate_resp.alias.split("-", 2)
                adv_alias = parts[2] if len(parts) > 2 else debate_resp.alias
                latest_positions[adv_alias] = debate_resp.content or ""
                path = session_dir.deliberation / f"debate-round-{actual_round}-{adv_alias.lower()}.md"
                path.write_text(
                    f"# Debate Round {actual_round}: {adv_alias}\n\n"
                    f"{debate_resp.content}\n"
                )

        all_rounds.append(round_responses)

        # The debate responses become the "challenges" for the next round
        current_challenges = round_responses

        good_round = successful_responses(round_responses)
        progress.info(f"Round {actual_round} complete: {len(good_round)}/{len(round_responses)} responses")

        # --- Between-round processing (skip after the last round) ---
        if round_num < max_rounds:
            # Generate state-of-play memo for the next round
            state_of_play_memo = generate_state_of_play(
                round_num=actual_round,
                round_responses=round_responses,
                latest_positions=latest_positions,
                config=config,
                progress=progress,
            )
            if state_of_play_memo:
                memo_path = session_dir.deliberation / f"state-of-play-r{actual_round}.md"
                memo_path.write_text(f"# State of Play — Round {actual_round}\n\n{state_of_play_memo}\n")

        # Compute external stability scores (every round)
        round_stability = compute_round_stability(
            round_num=actual_round,
            previous_positions=positions_before_round,
            current_positions=latest_positions,
            config=config,
            progress=progress,
        )
        if round_stability:
            external_stability_log.append(round_stability)

    # Write position stability report (uses external scores when available)
    stability_report = build_position_stability_report(
        advocate_responses, all_rounds, external_stability_log
    )
    (session_dir.deliberation / "position-stability.md").write_text(stability_report)
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
    all_bishops: list[ModelDef] | None = None,
    all_priests: list[ModelDef] | None = None,
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

    # --- Build differentiated evidence packages per judge ---
    # When there are 3+ judges, each gets a different view of the record.
    # This produces genuinely independent assessments: when judges converge
    # despite seeing different evidence, that's strong signal.
    #
    # View types:
    #   FULL            — everything (submissions, challenges, all debate rounds)
    #   FINAL_ONLY      — submissions + final debate round only (no middle journey)
    #   CHALLENGES_ONLY — submissions + challenges + concessions (skip full defenses)
    #
    # With < 3 judges, all get FULL (not enough judges to differentiate).

    VIEW_FULL = "FULL"
    VIEW_FINAL_ONLY = "FINAL_ONLY"
    VIEW_CHALLENGES_ONLY = "CHALLENGES_ONLY"

    if len(cardinals) >= 3:
        view_assignments = []
        for i in range(len(cardinals)):
            if i % 3 == 0:
                view_assignments.append(VIEW_FULL)
            elif i % 3 == 1:
                view_assignments.append(VIEW_FINAL_ONLY)
            else:
                view_assignments.append(VIEW_CHALLENGES_ONLY)
    else:
        view_assignments = [VIEW_FULL] * len(cardinals)

    cardinal_aliases = generate_aliases(len(cardinals), "Judge")
    cardinal_timeout = int(config.depth.timeout_per_model * 1.5)

    # Build per-judge prompts
    cardinal_calls = []
    for idx, (cardinal, alias) in enumerate(zip(cardinals, cardinal_aliases)):
        view = view_assignments[idx]

        prompt = (
            f"## Original Briefing\n\n{briefing}\n\n"
            f"{'=' * 60}\n\n"
            f"## Initial Advocate Submissions\n\n{submissions_text}\n\n"
            f"{'=' * 60}\n\n"
        )

        if view == VIEW_FULL:
            prompt += (
                f"## Challenge Round\n\n{challenges_text}\n\n"
                f"{'=' * 60}\n\n"
                f"## Debate Rounds\n\n{debate_text}\n\n"
                f"{'=' * 60}\n\n"
            )
            view_note = ""
        elif view == VIEW_FINAL_ONLY:
            # Only the final debate round — skip challenges and middle rounds
            final_round_text = "(No debate rounds)"
            if debate_rounds:
                final_round = successful_responses(debate_rounds[-1])
                if final_round:
                    final_round_text = "\n\n---\n\n".join(
                        f"### {r.alias}\n\n{r.content}" for r in final_round
                    )
            prompt += (
                f"## Final Debate Positions (Round {len(debate_rounds)})\n\n"
                f"{final_round_text}\n\n"
                f"{'=' * 60}\n\n"
            )
            view_note = (
                "\n\n**Note:** You have been given only the initial submissions "
                "and the final debate positions. You have NOT seen the challenge "
                "round or intermediate debate rounds. Assess whether the final "
                "positions are defensible on their own merits.\n\n"
            )
        elif view == VIEW_CHALLENGES_ONLY:
            # Submissions + challenges only — skip full debate
            prompt += (
                f"## Challenge Round\n\n{challenges_text}\n\n"
                f"{'=' * 60}\n\n"
            )
            view_note = (
                "\n\n**Note:** You have been given the initial submissions "
                "and the challenge round, but NOT the debate rounds. Assess "
                "which positions are likely to survive adversarial pressure "
                "based on the challenges raised.\n\n"
            )
        else:
            prompt += (
                f"## Challenge Round\n\n{challenges_text}\n\n"
                f"{'=' * 60}\n\n"
                f"## Debate Rounds\n\n{debate_text}\n\n"
                f"{'=' * 60}\n\n"
            )
            view_note = ""

        if stability_report:
            prompt += (
                f"## Position Stability Scorecard (Kelley-Riedl Sycophancy Audit)\n\n"
                f"{stability_report}\n\n"
                f"{'=' * 60}\n\n"
            )

        prompt += view_note
        prompt += "Please render your judgment on this deliberation."

        cardinal_calls.append({
            "model": cardinal,
            "system_prompt": CARDINAL_SYSTEM_PROMPT,
            "user_prompt": prompt,
            "alias": alias,
            "timeout": cardinal_timeout,
            "temperature": 0.3,
            "max_tokens": 4096,
        })

    cardinal_responses = fan_out_multi(cardinal_calls, progress=progress)

    # Write view assignments for audit trail
    view_log = "\n".join(
        f"- {cardinal_aliases[i]}: {view_assignments[i]} view"
        for i in range(len(cardinals))
    )
    (session_dir.judicial / "judicial-views.md").write_text(
        f"# Judicial View Assignments\n\n{view_log}\n"
    )

    # --- Bishop fallback chain ---
    # If a bishop (permanent Justice seat) failed, try substitutes from the
    # unused bishop pool first, then unused priests.
    _bishops_pool = all_bishops if all_bishops is not None else BISHOPS
    _priests_pool = all_priests if all_priests is not None else PRIESTS
    seated_ids = {c.id for c in cardinals}

    fallback_pool = [m for m in _bishops_pool if m.id not in seated_ids]
    fallback_pool += [m for m in _priests_pool if m.id not in seated_ids]

    cardinal_responses = list(cardinal_responses)
    for i, resp in enumerate(cardinal_responses):
        if resp.status != "failed" or resp.role != "bishop":
            continue
        if not fallback_pool:
            progress.warn(f"Justice {resp.display_name} failed — no fallback models available")
            continue
        substitute = fallback_pool.pop(0)
        progress.justice_substitution(resp.display_name, substitute.display_name, "Justice")
        fallback_prompt = cardinal_calls[i]["user_prompt"]
        retry_resp = call_model(
            model=substitute,
            system_prompt=CARDINAL_SYSTEM_PROMPT,
            user_prompt=fallback_prompt,
            alias=resp.alias,
            timeout=cardinal_timeout,
            temperature=0.3,
            max_tokens=4096,
            progress=progress,
        )
        if retry_resp.status == "success":
            cardinal_responses[i] = retry_resp
        else:
            progress.warn(f"Fallback {substitute.display_name} also failed: {retry_resp.error}")

    # Write individual judgments
    good_cardinals = successful_responses(cardinal_responses)
    for resp in good_cardinals:
        path = session_dir.judicial / f"judgment-{resp.alias.lower()}.md"
        path.write_text(f"# Judicial Opinion: {resp.alias}\n\n{resp.content}\n")

    # Write judge alias map
    cardinal_alias_map = {
        r.alias: {"model": r.display_name, "provider": r.provider, "role": r.role}
        for r in cardinal_responses
    }
    (session_dir.meta / "cardinal-alias-map.json").write_text(json.dumps(cardinal_alias_map, indent=2))

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


def check_verdict_coherence(
    cardinal_responses: list[ModelResponse],
    session_dir: Path,
    progress: Progress,
) -> list[str]:
    """Post-judicial coherence gate.

    Parses each judge's output looking for ranking-verdict contradictions:
    the #1-ranked advocate's core position being REJECTed in the verdict
    without an explicit explanation.

    Returns a list of warning strings (empty if no issues found).
    Appends warnings to the judgment files so downstream consumers see them.
    """
    good_cardinals = successful_responses(cardinal_responses)
    warnings: list[str] = []

    for resp in good_cardinals:
        content = resp.content or ""
        content_lower = content.lower()

        # --- Extract #1-ranked advocate ---
        rank1_advocate = None
        # Look for "| 1 | Advocate-X |" in the ranking table
        rank1_match = re.search(
            r'\|\s*1\s*\|\s*(advocate-[a-z])\s*\|',
            content_lower,
        )
        if rank1_match:
            rank1_advocate = rank1_match.group(1)  # e.g. "advocate-a"

        if not rank1_advocate:
            continue

        # --- Check verdict type ---
        is_synthesize = "**synthesize**" in content_lower or "verdict: synthesize" in content_lower
        is_accept = False
        accepted_advocate = None
        accept_match = re.search(r'\*\*accept\s*\[?(advocate-[a-z])', content_lower)
        if accept_match:
            is_accept = True
            accepted_advocate = accept_match.group(1)

        # Case 1: ACCEPT but not the #1-ranked advocate
        if is_accept and accepted_advocate and accepted_advocate != rank1_advocate:
            flag = (
                f"\n\n---\n\n## ⚠ COHERENCE FLAG\n\n"
                f"**Ranking-verdict mismatch**: {resp.alias} ranked "
                f"{rank1_advocate.title()} #1 but ACCEPTED "
                f"{accepted_advocate.title()}. These should typically "
                f"align unless there is an explicit justification."
            )
            warnings.append(f"{resp.alias}: ranked {rank1_advocate} #1 but accepted {accepted_advocate}")
            # Append flag to judgment file AND in-memory content
            jpath = session_dir.judicial / f"judgment-{resp.alias.lower()}.md"
            if jpath.exists():
                jpath.write_text(jpath.read_text() + flag)
            resp.content = (resp.content or "") + flag
            progress.warn(f"Coherence flag: {resp.alias} ranked {rank1_advocate} #1 but accepted {accepted_advocate}")

        # Case 2: SYNTHESIZE — check if #1-ranked advocate's position is REJECTed
        if is_synthesize and rank1_advocate:
            # Look for "REJECT" rows mentioning the #1 advocate
            reject_pattern = re.compile(
                rf'\|\s*{re.escape(rank1_advocate)}\s*\|\s*reject\b',
                re.IGNORECASE,
            )
            reject_matches = reject_pattern.findall(content_lower)
            # Also look for ADOPT rows for the same advocate
            adopt_pattern = re.compile(
                rf'\|\s*{re.escape(rank1_advocate)}\s*\|\s*adopt\b',
                re.IGNORECASE,
            )
            adopt_matches = adopt_pattern.findall(content_lower)

            if reject_matches and not adopt_matches:
                # #1 ranked but ALL their elements were rejected
                flag = (
                    f"\n\n---\n\n## ⚠ COHERENCE FLAG\n\n"
                    f"**Ranking-verdict contradiction**: {resp.alias} ranked "
                    f"{rank1_advocate.title()} #1 but REJECTED all their "
                    f"elements in the SYNTHESIZE verdict without adopting any. "
                    f"A #1 ranking typically implies at least partial adoption."
                )
                warnings.append(
                    f"{resp.alias}: ranked {rank1_advocate} #1 but rejected all their elements in synthesis"
                )
                jpath = session_dir.judicial / f"judgment-{resp.alias.lower()}.md"
                if jpath.exists():
                    jpath.write_text(jpath.read_text() + flag)
                resp.content = (resp.content or "") + flag
                progress.warn(
                    f"Coherence flag: {resp.alias} ranked {rank1_advocate} #1 "
                    f"but rejected all elements in synthesis"
                )

    if warnings:
        # Write a coherence report
        report = "# Verdict Coherence Check\n\n"
        for w in warnings:
            report += f"- ⚠ {w}\n"
        report += (
            "\nThese flags indicate potential inconsistencies between the "
            "judge's ranking and their verdict. They do not invalidate the "
            "verdict but should be considered by downstream consumers "
            "(summary writer, human reader).\n"
        )
        (session_dir.judicial / "coherence-flags.md").write_text(report)

    return warnings


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
        max_tokens=4096,
        progress=progress,
    )

    if resp.status == "success":
        (session_dir.judicial / "fresh-eyes-review.md").write_text(
            f"# Fresh Eyes Review\n\n{resp.content}\n"
        )
        progress.info(f"Fresh Eyes review complete ({resp.elapsed:.1f}s)")
    else:
        progress.warn(f"Fresh Eyes failed: {resp.error}")

    return resp


def extract_claim_evidence_matrix(
    briefing: str,
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> Optional[ModelResponse]:
    """Extract a structured claim-evidence matrix from the full deliberation.

    Uses a fast/cheap Bishop (first available) to read the entire deliberation
    record and produce a tabular matrix tracking every significant claim.
    """
    progress.info("Extracting claim-evidence matrix...")

    # Assemble the full deliberation record
    parts = [f"## BRIEFING\n\n{briefing}\n"]

    good_advocates = successful_responses(advocate_responses)
    if good_advocates:
        parts.append("\n## ADVOCATE SUBMISSIONS\n")
        for r in good_advocates:
            parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    good_challenges = successful_responses(challenge_responses)
    if good_challenges:
        parts.append("\n## CHALLENGE ROUND\n")
        for r in good_challenges:
            parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    for round_idx, round_resps in enumerate(debate_rounds):
        good_round = successful_responses(round_resps)
        if good_round:
            parts.append(f"\n## DEBATE ROUND {round_idx + 1}\n")
            for r in good_round:
                parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    good_cardinals = successful_responses(cardinal_responses)
    if good_cardinals:
        parts.append("\n## JUDICIAL OPINIONS\n")
        for r in good_cardinals:
            parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    full_record = "\n".join(parts)

    # Select model: first Bishop that fits the context
    estimated_tokens = len(full_record) // 4
    matrix_model = _select_model_for_context(config.bishops, estimated_tokens, progress)
    if matrix_model is None:
        progress.warn("No model available for claim-evidence matrix — skipping")
        return None

    user_prompt = (
        f"Extract the claim-evidence matrix from this deliberation.\n\n"
        f"{full_record}"
    )

    resp = call_model(
        model=matrix_model,
        system_prompt=CLAIM_EXTRACTION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        alias="Claim-Matrix",
        timeout=config.depth.timeout_per_model,
        temperature=0.2,
        max_tokens=4096,
        progress=progress,
    )

    if resp.status == "success":
        (session_dir.deliberation / "claim-evidence-matrix.md").write_text(
            f"# Claim-Evidence Matrix\n\n{resp.content}\n"
        )
        progress.info(f"Claim-evidence matrix extracted ({resp.elapsed:.1f}s)")
    else:
        progress.warn(f"Claim-evidence matrix failed: {resp.error}")

    return resp


# ---------------------------------------------------------------------------
# Majority Opinion synthesis — consolidate individual judicial opinions
# ---------------------------------------------------------------------------

def synthesize_majority_opinion(
    cardinal_responses: list[ModelResponse],
    briefing: str,
    config: ConclaveConfig,
    session_dir: Path,
    progress: Progress,
) -> Optional[ModelResponse]:
    """Synthesize individual judicial opinions into a single canonical ruling.

    One LLM reads all judge opinions, resolves conflicts by majority vote,
    and produces the Opinion of the Court. This becomes what dissenters
    respond to and what the summary uses as the Recommended Outcome.

    Returns the ModelResponse for cost tracking, or None if generation fails.
    """
    good_cardinals = successful_responses(cardinal_responses)
    if not good_cardinals:
        return None

    progress.info("Synthesizing majority opinion from judicial panel...")

    # Concatenate all judicial opinions
    opinions_text = "\n\n---\n\n".join(
        f"## {r.alias}\n\n{r.content}" for r in good_cardinals
    )

    user_prompt = (
        f"## Original Briefing\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Individual Judicial Opinions ({len(good_cardinals)} judges)\n\n"
        f"{opinions_text}\n\n"
        f"{'=' * 60}\n\n"
        f"Read all {len(good_cardinals)} judicial opinions above and produce "
        f"the Opinion of the Court — a single, canonical ruling that resolves "
        f"any conflicts by majority vote."
    )

    # Select model: same pattern as summary generation (pick first bishop that fits)
    estimated_tokens = len(user_prompt) // 4
    model = _select_model_for_context(config.bishops, estimated_tokens, progress)
    if model is None:
        progress.warn("No model available for majority opinion — skipping")
        return None

    resp = call_model(
        model=model,
        system_prompt=MAJORITY_OPINION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        alias="Majority-Opinion",
        timeout=int(config.depth.timeout_per_model * 1.5),
        temperature=0.3,
        max_tokens=4096,
        progress=progress,
    )

    if resp.status == "success":
        opinion_text = f"# Opinion of the Court (Majority Opinion)\n\n{resp.content}\n"
        # De-anonymize aliases in the majority opinion
        alias_file = session_dir.meta / "alias-map.json"
        cardinal_file = session_dir.meta / "cardinal-alias-map.json"
        if alias_file.exists():
            _a = json.loads(alias_file.read_text())
            _c = json.loads(cardinal_file.read_text()) if cardinal_file.exists() else {}
            opinion_text = _deanonymize_text(opinion_text, _a, _c)
        (session_dir.judicial / "majority-opinion.md").write_text(opinion_text)
        progress.info(f"Majority opinion written ({resp.elapsed:.1f}s)")
    else:
        progress.warn(f"Majority opinion synthesis failed: {resp.error}")

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
    majority_opinion_text: str = "",
) -> list[ModelResponse]:
    """Give persistent dissenters a chance to issue formal dissenting opinions.

    Like a Supreme Court dissent: the advocate reads the verdict, disagrees,
    and writes a structured dissent for the record.

    If majority_opinion_text is provided, dissenters respond to the consolidated
    majority opinion rather than raw individual judicial opinions. This ensures
    dissents reference elements actually present in the canonical ruling.

    Returns list of ModelResponse objects for cost tracking.
    """
    if not dissenters:
        return []

    alias_to_model = _build_alias_model_map(advocate_responses, advocates)
    good_cardinals = successful_responses(cardinal_responses)

    # Use majority opinion if available; fall back to concatenated raw opinions
    if majority_opinion_text:
        verdict_text = majority_opinion_text
    else:
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
        path = session_dir.judicial / f"dissent-{adv_alias.lower()}.md"
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
# Session summary — executive briefing synthesized from deliberation record
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """\
You are a senior analyst and expert communicator producing a best-in-class
executive briefing document. You have conducted comprehensive research on this
topic — including independent expert analyses, adversarial cross-examination of
claims, multi-round stress-testing of positions, and independent evaluation of
the evidence by separate reviewers. Your job is to synthesize everything into
an accessible, well-structured briefing that a busy, intelligent reader can
digest asynchronously, retain after time has passed, and activate in conversation
or decision-making.

## CRITICAL RULES

1. This briefing must stand completely on its own. Do NOT reference the research
   process, deliberation, debate, advocates, judges, tribunal, council, panel,
   verdicts, rulings, submissions, challenges, concessions, position stability,
   sycophantic drift, remand, fresh eyes, the bench, or any process artifacts.
   Write as though you are a senior analyst who has done the research and is
   now briefing a principal directly.

2. Do NOT attribute analysis to specific AI models, advocates, judges, or any
   participant by name or alias. This includes model names like GPT-5, Claude,
   Gemini, Qwen, DeepSeek, Perplexity Sonar, MiniMax, Mistral, or ANY other
   AI model name. Never write "Advocate-A argued" or "the judges concluded" or
   "GPT-5 noted" or "one model contended" or "supported by Qwen 3 235B."
   Instead, state the analysis directly: "the evidence shows", "the strongest
   counter-argument is", "analysis indicates", "the data supports", "the
   majority view holds", "the minority position argues." Attribution goes to
   evidence and logic, never to participants.

3. Replace ALL adjectives with data or specific examples. "Significant growth"
   is not acceptable; "47% YoY growth, fastest in the sector" is. No weasel
   words (nearly, significantly, arguably, somewhat). Every sentence must earn
   its place — if it can be deleted without losing meaning, cut it.

4. Use narrative prose for reasoning, causality, and argument. Use bullets only
   for genuinely list-shaped information (actors, options, data points). Never
   use bullets to hide logic that should be expressed as a sentence.

5. Every paragraph must open with a **bolded lead sentence** that summarizes
   the paragraph — this creates a navigation layer for re-skimming after time
   has passed.

6. Tone: Neutral and authoritative. Accessible but not dumbed down. Write for
   a smart generalist, not a specialist. Define domain concepts in context when
   they first appear.

7. If a MAJORITY OPINION is present in the record, use it as the authoritative
   source for the conclusion. Do NOT re-adjudicate the case — faithfully
   represent its conclusion while stripping all process language.

## OUTPUT STRUCTURE

Each section heading MUST be a markdown heading (##). Do NOT omit heading
markers. Do NOT add sections beyond what is specified.

YOUR OUTPUT MUST BEGIN WITH:

## The Question

Copy the original question or task VERBATIM from the briefing. Do NOT rewrite,
paraphrase, or restate it. Reproduce the user's exact words. Fix only obvious
typos or grammar if needed.

## Summary

Write the SCR narrative in pure prose — 4-5 sentences, no bullets:
**Situation** (what is generally known or agreed upon about this topic),
**Complication** (what has changed, what tension exists, what makes this
non-trivial right now), **Resolution** (how to think about this — the lens
through which the rest of the briefing should be read). This is the most
important paragraph in the document. It must stand alone. A reader who reads
nothing else should still walk away oriented.

## Key Assertions

Write exactly three key assertions about this topic. Each assertion is a short
paragraph: one **bolded lead sentence** stating the assertion (arguable, not
merely factual), followed by 2-3 sentences of support including at least one
specific data point or concrete example. A reader who reads only the three
bolded sentences should be able to walk into a room and speak intelligently
about the topic.

## Context

The essential backstory — how we got here. No more than 5 sentences.
Reverse-chronological if helpful. Only what is necessary to make the current
situation intelligible.

## The Landscape

Key players, forces, numbers, or dynamics. Genuinely list-shaped. Each bullet
is one tight sentence with a specific data point where possible. No more than
7 bullets.

## Fault Lines

2-3 competing viewpoints, tensions, or schools of thought on this topic. Write
the strongest version of each position — do not strawman. If the evidence
clearly supports one position over others, indicate which and why, briefly.
This section is what allows the reader to engage with counterarguments rather
than being caught flat-footed.

## So What

This section graduates based on how much is known:
- If the topic is still emerging or uncertain: "Key question to monitor: ___"
- If analysis exists but a position is not warranted: "What would need to be
  true to reach a conclusion: ___"
- If a position is warranted: state it in one sentence, followed by a specific
  next step

Always end this section with: **Key question to be ready for:** — the single
most likely pressure point the reader will face when this topic comes up in
conversation.

## Supplemental

(OPTIONAL — include ONLY if the research produced dense data tables, timelines,
implementation specifications, or technical details that are genuinely too
detailed for the main briefing but are valuable reference material. If the
original question asked for a buildable deliverable such as code, architecture,
or system design, include a self-contained implementation prompt here under a
"### Build This" subheading. Label clearly as supplemental reading. If nothing
warrants supplemental material, OMIT this section entirely.)

## Glossary

Include a glossary of domain-specific terms that a smart generalist would NOT
already know. Skip common business terms (e.g. "ROI", "capex", "revenue") and
only include genuinely specialized jargon, technical acronyms, or industry-
specific concepts that require insider knowledge. If a term was defined in
context in the briefing body, you can still include it here for quick reference.
Sort alphabetically. Format as a markdown table. Aim for 5-12 terms.

| Term | Definition |
|------|------------|
| [Term] | [One-sentence definition accessible to a non-specialist] |

## QUALITY CHECKS BEFORE FINALIZING

Before producing the final document, verify:

1. **Navigation test** — Can someone read only the bolded lead sentences and
   walk away with a coherent picture of the topic?
2. **Amazon test** — Has every adjective been replaced by a number or a
   specific named example?
3. **So what test** — Does the last section tell the reader what to do, watch,
   or be ready to answer?
4. **Process scrub** — Does the document contain ANY reference to the research
   process, deliberation, debate, advocates, judges, tribunal, panel, bench,
   verdict, ruling, or specific AI model names? If yes, rewrite those passages
   to state the analysis directly.
5. **Non-linear reading test** — Does each section's first and last sentence
   make sense independently, for a reader who skims non-linearly?

If any check fails, revise before outputting.

IMPORTANT RULES:
- Do NOT use advocate aliases (Advocate-A, etc.) or judge aliases (Judge-A, etc.)
  anywhere in the output. Do NOT use real model names either. State analysis
  directly without attribution.
- Do not invent details not present in the research record.
- Keep the total briefing under 2500 words (excluding Supplemental and Glossary).
- Write in plain, direct analytical voice.
- Produce the final briefing document only. No meta-commentary, no preamble,
  no explanation of your choices. The document should be ready to read as-is."""


# ---------------------------------------------------------------------------
# Condensed Council Digest — context-window-friendly record assembly
# ---------------------------------------------------------------------------

def _extract_concession_summary(content: str) -> str:
    """Parse debate response for CONCEDE blocks, extract first sentence of each."""
    if not content:
        return "None"
    concessions = []
    for block in re.split(r'\*\*My response:\s*CONCEDE\*\*', content, flags=re.IGNORECASE):
        if block == content.split("**My response: CONCEDE**")[0]:
            continue  # skip text before first concession
        first_sentence = re.split(r'[.!?\n]', block.strip(), maxsplit=1)[0].strip()
        if first_sentence:
            concessions.append(first_sentence)
    return "; ".join(concessions) if concessions else "None"


def _summarize_debate_rounds(rounds: list[list[ModelResponse]]) -> str:
    """Build a markdown table summarizing debate rounds (condensed form)."""
    lines = [
        "| Round | Advocate | Stability | Position | Concessions |",
        "|-------|----------|-----------|----------|-------------|",
    ]
    for round_idx, round_resps in enumerate(rounds, 1):
        for r in successful_responses(round_resps):
            stability = _extract_position_stability(r.content or "")
            if stability >= 4:
                position = "Revised"
            elif stability >= 2:
                position = "Refined"
            else:
                position = "Unchanged"
            concessions = _extract_concession_summary(r.content or "")
            # Truncate long concession text for the table
            if len(concessions) > 80:
                concessions = concessions[:77] + "..."
            lines.append(f"| {round_idx} | {r.alias} | {stability}/5 | {position} | {concessions} |")
    return "\n".join(lines)


def build_condensed_digest(
    briefing: str,
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    fresh_eyes_response: Optional[ModelResponse],
    all_responses: dict[str, list[ModelResponse]],
    identity_text: str = "",
    majority_opinion_response: Optional[ModelResponse] = None,
    claim_matrix_response: Optional[ModelResponse] = None,
) -> str:
    """Assemble a context-window-friendly version of the deliberation record.

    - Briefing, submissions, challenges: full (they're short)
    - Debate rounds 1 through N-3: condensed to a summary table
    - Final 3 debate rounds: full (most relevant for synthesis)
    - Judicial opinions, fresh eyes, dissents, claim matrix: full
    """
    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)
    good_cardinals = successful_responses(cardinal_responses)

    parts = []
    parts.append(f"## BRIEFING (the original question)\n\n{briefing}")

    parts.append("\n\n## ADVOCATE SUBMISSIONS\n")
    for r in good_advocates:
        parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_challenges:
        parts.append("\n## CHALLENGE ROUND\n")
        for r in good_challenges:
            parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    # Debate rounds: condense early rounds, keep final 3 in full
    n_rounds = len(debate_rounds)
    cutoff = max(0, n_rounds - 3)  # rounds to condense (0-indexed)

    if cutoff > 0:
        condensed_rounds = debate_rounds[:cutoff]
        parts.append(f"\n## DEBATE ROUNDS 1-{cutoff} (condensed)\n")
        parts.append(_summarize_debate_rounds(condensed_rounds))
        parts.append("\n")

    # Full final rounds
    for round_idx in range(cutoff, n_rounds):
        good_round = successful_responses(debate_rounds[round_idx])
        if good_round:
            parts.append(f"\n## DEBATE ROUND {round_idx + 1}\n")
            for r in good_round:
                parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if good_cardinals:
        parts.append("\n## JUDICIAL OPINIONS\n")
        for r in good_cardinals:
            parts.append(f"### {r.alias}\n{r.content}\n\n---\n")

    if majority_opinion_response and majority_opinion_response.status == "success":
        parts.append("\n## MAJORITY OPINION (Opinion of the Court)\n")
        parts.append(majority_opinion_response.content)
        parts.append("\n")

    if fresh_eyes_response and fresh_eyes_response.status == "success":
        parts.append("\n## FRESH EYES REVIEW\n")
        parts.append(f"{fresh_eyes_response.content}\n")

    good_dissents = successful_responses(all_responses.get("dissents", []))
    if good_dissents:
        parts.append("\n## DISSENTING OPINIONS\n")
        for r in good_dissents:
            adv_alias = r.alias.replace("Dissent-", "")
            parts.append(f"### {adv_alias} (Dissent)\n{r.content}\n\n---\n")

    if claim_matrix_response and claim_matrix_response.status == "success":
        parts.append("\n## CLAIM-EVIDENCE MATRIX\n")
        parts.append(f"{claim_matrix_response.content}\n")

    if identity_text:
        parts.append(f"\n{identity_text}")

    return "\n".join(parts)


def _select_model_for_context(
    models: list[ModelDef],
    estimated_tokens: int,
    progress: Progress,
) -> Optional[ModelDef]:
    """Pick the first model whose context window fits estimated_tokens * 1.2."""
    headroom = int(estimated_tokens * 1.2)
    for m in models:
        if m.context_window >= headroom:
            return m
    # No model has enough headroom — warn and return the largest
    if models:
        largest = max(models, key=lambda m: m.context_window)
        progress.warn(
            f"No model has {headroom} token capacity — using {largest.display_name} "
            f"({largest.context_window} tokens)"
        )
        return largest
    return None


def _deanonymize_text(text: str, alias_map: dict, cardinal_alias_map: dict) -> str:
    """Replace all advocate/judge aliases with real model names in text.

    Handles:
      - Full forms: 'Advocate-A', 'Judge-B'
      - Plural forms: 'Advocates-A', 'Judges-D'
      - Grouped shorthand: 'Advocates A, D, F', 'Judges D, A, E, and B'
      - Grouped with hyphens: 'Judges-D, A, E, and B'
      - Parenthetical: 'bears (E, C)', 'judges (A, D)'

    Order matters: grouped patterns run FIRST (before individual replacement)
    so that 'Judges-D, A, E, and B' is caught as one group, not broken apart.
    """
    import re

    # --- Helper for grouped shorthand ---
    def _expand_role_group(m: re.Match, role_prefix: str, source_map: dict) -> str:
        """Turn 'Advocates A, D, F' or 'Judges-D, A, E, and B' into model names."""
        letters_part = re.sub(
            rf"^{role_prefix}s?\s*[-–—]?\s*", "", m.group(0), flags=re.IGNORECASE
        )
        letters = re.findall(r"[A-Z]", letters_part)
        names = []
        for letter in letters:
            key = f"{role_prefix}-{letter}"
            if key in source_map:
                names.append(source_map[key]["model"])
            else:
                names.append(f"{role_prefix}-{letter}")
        if len(names) == 0:
            return m.group(0)
        if len(names) <= 2:
            return " and ".join(names)
        return ", ".join(names[:-1]) + ", and " + names[-1]

    # --- Pass 1: grouped shorthand FIRST (before individual replacement) ---
    # Must run before exact replacement, otherwise "Judges-D, A, E, and B"
    # gets "Judges-D" replaced individually, leaving orphaned ", A, E, and B".
    text = re.sub(
        r"Advocates?\s*[-–—]?\s*[A-Z](?:\s*(?:,|and)\s*[A-Z])*",
        lambda m: _expand_role_group(m, "Advocate", alias_map),
        text,
    )
    text = re.sub(
        r"Judges?\s*[-–—]?\s*[A-Z](?:\s*(?:,|and)\s*[A-Z])*",
        lambda m: _expand_role_group(m, "Judge", cardinal_alias_map),
        text,
    )

    # --- Pass 2: exact alias replacement for any remaining singles (longest first) ---
    for alias, info in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        text = text.replace(alias, info["model"])
    for alias, info in sorted(cardinal_alias_map.items(), key=lambda x: -len(x[0])):
        text = text.replace(alias, info["model"])

    # --- Pass 3: parenthetical shorthand like "bears (E, C)" or "(A, D)" ---
    def _expand_paren_group(m: re.Match) -> str:
        prefix = m.group(1)
        letters = re.findall(r"[A-Z]", m.group(2))
        names = []
        for letter in letters:
            adv_key = f"Advocate-{letter}"
            judge_key = f"Judge-{letter}"
            if adv_key in alias_map:
                names.append(alias_map[adv_key]["model"])
            elif judge_key in cardinal_alias_map:
                names.append(cardinal_alias_map[judge_key]["model"])
            else:
                names.append(letter)
        return f"{prefix}({', '.join(names)})"

    text = re.sub(
        r"(\w+\s*)\(([A-Z](?:\s*,\s*[A-Z])*)\)",
        _expand_paren_group,
        text,
    )

    return text


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
    majority_opinion_response: Optional[ModelResponse] = None,
    claim_matrix_response: Optional[ModelResponse] = None,
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
    if (session_dir.meta / "alias-map.json").exists():
        alias_map = json.loads((session_dir.meta / "alias-map.json").read_text())
    cardinal_alias_map = {}
    if (session_dir.meta / "cardinal-alias-map.json").exists():
        cardinal_alias_map = json.loads((session_dir.meta / "cardinal-alias-map.json").read_text())

    # Build identity reveal table for the LLM
    identity_lines = ["## Identity Reveal"]
    for alias, info in alias_map.items():
        identity_lines.append(f"- {alias} = {info['model']} ({info['provider']})")
    for alias, info in cardinal_alias_map.items():
        role = _ROLE_DISPLAY.get(info.get('role', ''), info.get('role', 'judge'))
        identity_lines.append(f"- {alias} = {info['model']} ({info['provider']}, {role})")
    identity_text = "\n".join(identity_lines)

    # Build condensed record (compresses early debate rounds into a table)
    full_record = build_condensed_digest(
        briefing=briefing,
        advocate_responses=advocate_responses,
        challenge_responses=challenge_responses,
        debate_rounds=debate_rounds,
        cardinal_responses=cardinal_responses,
        fresh_eyes_response=fresh_eyes_response,
        all_responses=all_responses,
        identity_text=identity_text,
        majority_opinion_response=majority_opinion_response,
        claim_matrix_response=claim_matrix_response,
    )

    summary_prompt = (
        f"Produce the session summary for this Tribunal deliberation.\n\n"
        f"{full_record}"
    )

    # Select synthesizer model: context-window-aware (pick first bishop that fits)
    estimated_tokens = len(full_record) // 4  # rough char-to-token estimate
    summary_model = _select_model_for_context(config.bishops, estimated_tokens, progress)
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
        max_tokens=8192,
        progress=progress,
    )

    if resp.status != "success":
        progress.warn(f"Session summary generation failed: {resp.error}")
        return resp

    # --- Build the final document: YAML frontmatter + deterministic header + LLM body ---
    n_judges = len(good_cardinals)

    # Extract date stamp and topic slug from session_id for filenames
    # New format: "YYYYMMDD-{slug}" (date first, no prefix)
    # Legacy format: "tribunal-{slug}-{YYYYMMDD}-{HHMMSS}"
    _sid_parts = session_id.split("-")
    _date_stamp = ""
    _slug_parts = []
    if _sid_parts and re.match(r"^\d{8}$", _sid_parts[0]):
        # New format: date is first token
        _date_stamp = _sid_parts[0]
        _slug_parts = _sid_parts[1:]
    else:
        # Legacy format: scan for date, skip "tribunal" prefix
        for _i, _p in enumerate(_sid_parts):
            if re.match(r"^\d{8}$", _p):
                _date_stamp = _p
                break
            elif _i > 0:  # skip "tribunal" prefix
                _slug_parts.append(_p)
    # Strip trailing HHMMSS from slug if present (legacy format)
    if _slug_parts and re.match(r"^\d{6}$", _slug_parts[-1]):
        _slug_parts = _slug_parts[:-1]
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

    # Pick up briefing name from environment (set by dashboard submit)
    briefing_name = os.environ.get("TRIBUNAL_BRIEFING_NAME", "").strip()

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
    )
    if briefing_name:
        frontmatter += f"briefing_name: {briefing_name}\n"
    frontmatter += (
        f"models:\n"
        f"  advocates: [{', '.join(_adv_names)}]\n"
        f"  judges: [{', '.join(_judge_names)}]\n"
        f"---\n\n"
    )

    header = (
        f"# Executive Briefing\n"
        f"**Session: {session_id} | Depth: {config.depth.name} | "
        f"Analysts: {len(good_advocates)} | Reviewers: {n_judges} | "
        f"Cost: ${cost:.4f} | Time: {minutes}m {seconds:02d}s**\n\n"
        f"---\n\n"
    )

    summary_text = frontmatter + header + (resp.content or "")

    # Programmatic de-anonymization — replace any remaining aliases the LLM missed
    summary_text = _deanonymize_text(summary_text, alias_map, cardinal_alias_map)

    # Write with canonical filename (also keep legacy name as symlink)
    (session_dir / summary_md_name).write_text(summary_text)
    legacy_md = session_dir / "session-summary.md"
    if legacy_md.exists() or legacy_md.is_symlink():
        legacy_md.unlink()
    legacy_md.symlink_to(summary_md_name)
    progress.info(f"Session summary written: {summary_md_name} ({resp.elapsed:.1f}s)")

    # --- Generate PDF from session summary (optional, requires reportlab) ---
    pdf_generated = False
    try:
        from summary_pdf import generate_summary_pdf
        md_path = str(session_dir / summary_md_name)
        pdf_path = str(session_dir / summary_pdf_name)
        generate_summary_pdf(md_path, pdf_path, briefing_name=briefing_name or None)
        pdf_generated = True
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

    # --- Generate Executive Brief PDF (2-page summary, requires reportlab) ---
    exec_brief_name = summary_basename.replace("session-summary", "exec-brief") + ".pdf"
    exec_brief_generated = False
    try:
        from exec_brief_pdf import generate_exec_brief
        md_path_for_brief = str(session_dir / summary_md_name)
        brief_path = str(session_dir / exec_brief_name)
        generate_exec_brief(md_path_for_brief, brief_path)
        exec_brief_generated = True
        # Symlink legacy name
        legacy_brief = session_dir / "exec-brief.pdf"
        if legacy_brief.exists() or legacy_brief.is_symlink():
            legacy_brief.unlink()
        legacy_brief.symlink_to(exec_brief_name)
        progress.info(f"Executive brief generated: {exec_brief_name}")
    except ImportError:
        progress.info("Executive brief skipped (exec_brief_pdf not found)")
    except Exception as e:
        progress.warn(f"Executive brief generation failed: {e}")

    # --- Re-write summary with PDF/brief links if generated ---
    if pdf_generated or exec_brief_generated:
        link_parts = [
            f"Full logs: `tribunal-sessions/{session_id}/`",
            f"Audit trail: `meta/final-output.md`",
            f"Narrative: `narrative/play-by-play.md`",
        ]
        if pdf_generated:
            link_parts.append(f"PDF: `{summary_pdf_name}`")
        if exec_brief_generated:
            link_parts.append(f"Brief: `{exec_brief_name}`")

        header_with_pdf = (
            f"# Executive Briefing\n"
            f"**Session: {session_id} | Depth: {config.depth.name} | "
            f"Analysts: {len(good_advocates)} | Reviewers: {n_judges} | "
            f"Cost: ${cost:.4f} | Time: {minutes}m {seconds:02d}s**\n"
            f"*{' | '.join(link_parts)}*\n\n"
            f"---\n\n"
        )
        summary_text = frontmatter + header_with_pdf + (resp.content or "")
        summary_text = _deanonymize_text(summary_text, alias_map, cardinal_alias_map)
        (session_dir / summary_md_name).write_text(summary_text)

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
        # T1 depth — no debate to narrate
        return []

    progress.info("Generating dual play-by-play narratives...")

    # Build condensed transcript (compresses early debate rounds)
    full_transcript = build_condensed_digest(
        briefing=briefing,
        advocate_responses=advocate_responses,
        challenge_responses=challenge_responses,
        debate_rounds=debate_rounds,
        cardinal_responses=cardinal_responses,
        fresh_eyes_response=fresh_eyes_response,
        all_responses={},  # narrators don't need dissents
    )

    narrator_prompt = (
        f"## Original Question\n\n{briefing}\n\n"
        f"{'=' * 60}\n\n"
        f"## Full Deliberation Transcript\n\n{full_transcript}\n\n"
        f"{'=' * 60}\n\n"
        f"Write the play-by-play narrative of this deliberation."
    )

    # --- Dual narrators: context-window-aware model selection ---
    from config_loader import BISHOPS, ADVOCATES

    estimated_tokens = len(narrator_prompt) // 4
    narrator_qwen = _select_model_for_context(
        [m for m in BISHOPS if "qwen" in m.id.lower()] or BISHOPS[:1],
        estimated_tokens, progress,
    )
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
        (session_dir.narrative / f"play-by-play-{tag}.md").write_text(
            f"# Tribunal Play-by-Play ({nr.alias})\n\n{nr.content}\n"
        )
        progress.info(f"  {nr.alias} narrative generated ({nr.elapsed:.1f}s)")

    if len(good_narrators) == 1:
        # Only one succeeded — that one wins by default
        winner = good_narrators[0]
        progress.info(f"Only {winner.alias} succeeded — wins by default.")
        (session_dir.narrative / "play-by-play.md").write_text(
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
        (session_dir.narrative / "narrator-vote.md").write_text(
            f"# Narrator Vote (Judge: {voter_model.display_name})\n\n"
            f"{vote_resp.content}\n"
        )
    else:
        progress.warn(f"Narrator vote failed ({vote_resp.error}) — defaulting to Qwen.")

    # Write the winner as the canonical play-by-play
    (session_dir.narrative / "play-by-play.md").write_text(
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
    majority_opinion_response: Optional[ModelResponse] = None,
) -> str:
    """Build the final output document — the clean deliverable.

    For T1: raw advocate submissions (no debate/judges).
    For T2+: the majority opinion's Deliverable section as a clean document,
    stripped of tribunal framing. This is the actual answer to the user's question.
    """

    good_advocates = successful_responses(advocate_responses)
    depth_name = config.depth.name

    if depth_name == "T1":
        lines = [
            f"# Tribunal Output: {session_id}",
            f"## Depth: {depth_name}",
            "",
            f"**{len(good_advocates)} independent submissions** for your review.",
            "At T1 (Spot Check) depth, there is no debate or judicial review — you get raw,",
            "independent perspectives to compare yourself.",
            "", "---", "",
        ]
        for resp in good_advocates:
            lines.extend([f"## {resp.alias}", "", resp.content or "(no content)", "", "---", ""])
        return "\n".join(lines)

    # T2+: Extract the clean deliverable from the majority opinion
    if majority_opinion_response and majority_opinion_response.status == "success":
        content = majority_opinion_response.content or ""
        # Extract the Deliverable section (the substantive answer)
        deliverable = _extract_section(content, "Deliverable")
        if deliverable:
            return deliverable.strip()
        # Fallback: if no Deliverable header, try legacy "The Court's Recommendation"
        deliverable = _extract_section(content, "The Court's Recommendation")
        if deliverable:
            return deliverable.strip()
        # Last fallback: return the full majority opinion minus verdict/metadata
        return content.strip()

    # No majority opinion — fall back to best advocate submission
    if good_advocates:
        return good_advocates[0].content or "(no content)"

    return "(No output generated)"


def _extract_section(text: str, heading: str) -> Optional[str]:
    """Extract content under a ### heading until the next ### or ## heading."""
    pattern = rf"###\s+{re.escape(heading)}\s*\n"
    match = re.search(pattern, text)
    if not match:
        return None
    start = match.end()
    # Find the next heading (## or ###) or end of text
    next_heading = re.search(r"\n#{2,3}\s+", text[start:])
    if next_heading:
        return text[start:start + next_heading.start()]
    return text[start:]


def build_council_record(
    advocate_responses: list[ModelResponse],
    challenge_responses: list[ModelResponse],
    debate_rounds: list[list[ModelResponse]],
    cardinal_responses: list[ModelResponse],
    fresh_eyes_response: Optional[ModelResponse],
    session_id: str,
    config: ConclaveConfig,
    dissent_responses: Optional[list[ModelResponse]] = None,
    majority_opinion_response: Optional[ModelResponse] = None,
) -> str:
    """Build the full deliberation record for audit purposes.

    This is the comprehensive log of all judicial opinions, majority opinion,
    fresh eyes, dissents, and original submissions. Written to meta/council-record.md.
    """
    good_advocates = successful_responses(advocate_responses)
    good_challenges = successful_responses(challenge_responses)
    good_cardinals = successful_responses(cardinal_responses)
    depth_name = config.depth.name

    lines = [
        f"# Council Record: {session_id}",
        f"## Depth: {depth_name}",
        "",
    ]

    if depth_name == "T1":
        for resp in good_advocates:
            lines.extend([f"## {resp.alias}", "", resp.content or "(no content)", "", "---", ""])
    else:
        total_debate_resps = sum(len(successful_responses(r)) for r in debate_rounds)
        lines.extend([
            f"**{len(good_advocates)} advocates** deliberated with "
            f"{len(good_challenges)} challenge(s), "
            f"{len(debate_rounds)} debate round(s) ({total_debate_resps} exchanges), and "
            f"{len(good_cardinals)} judicial opinion(s).",
            "", "---", "",
        ])

        if good_cardinals:
            lines.extend(["## Judicial Opinions", ""])
            for resp in good_cardinals:
                lines.extend([f"### {resp.alias}", "", resp.content or "(no content)", "", "---", ""])

        if majority_opinion_response and majority_opinion_response.status == "success":
            lines.extend([
                "## Majority Opinion", "",
                majority_opinion_response.content or "(no content)", "", "---", "",
            ])

        if fresh_eyes_response and fresh_eyes_response.status == "success":
            lines.extend([
                "## Fresh Eyes Review", "",
                fresh_eyes_response.content or "(no content)", "", "---", "",
            ])

        good_dissents = successful_responses(dissent_responses or [])
        if good_dissents:
            lines.extend(["## Dissenting Opinions", ""])
            for resp in good_dissents:
                adv_alias = resp.alias.replace("Dissent-", "")
                lines.extend([
                    f"### {adv_alias} (Dissent)", "",
                    resp.content or "(no content)", "", "---", "",
                ])

        lines.extend(["## Original Advocate Submissions", ""])
        for resp in good_advocates:
            lines.extend([f"### {resp.alias}", "", resp.content or "(no content)", "", "---", ""])

    return "\n".join(lines)


_ROLE_DISPLAY = {
    "bishop": "Justice",
    "priest": "Appellate Judge",
    "deacon": "Magistrate Judge",
}


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
    if (session_dir.meta / "alias-map.json").exists():
        alias_map = json.loads((session_dir.meta / "alias-map.json").read_text())

    cardinal_alias_map = {}
    if (session_dir.meta / "cardinal-alias-map.json").exists():
        cardinal_alias_map = json.loads((session_dir.meta / "cardinal-alias-map.json").read_text())

    lines = [
        "# Tribunal Debrief Report",
        f"## Session: {session_id}",
        f"## Date: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        "", "---", "",
        "### Summary",
    ]

    if depth_name == "T1":
        lines.append(f"{len(good_advocates)} advocates independently addressed the task at T1 (Spot Check) depth.")
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
        if (session_dir.narrative / "play-by-play.md").exists():
            lines.append("A play-by-play narrative of the debate is available in `narrative/play-by-play.md`.")

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
            rank = _ROLE_DISPLAY.get(resp.role, resp.role)
            lines.append(f"| {resp.alias} | {resp.display_name} | {resp.provider} | {rank} | {status} |")

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

    # Claim-evidence matrix (if generated)
    matrix_path = session_dir.deliberation / "claim-evidence-matrix.md"
    if matrix_path.exists():
        matrix_content = matrix_path.read_text()
        # Strip the "# Claim-Evidence Matrix" header if present (we add our own)
        matrix_content = matrix_content.replace("# Claim-Evidence Matrix\n\n", "").strip()
        lines.extend(["", "### Claim-Evidence Matrix", "", matrix_content])

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
            rank = _ROLE_DISPLAY.get(info.get('role', ''), info.get('role', ''))
            lines.append(f"| {alias} | {info['model']} | {info['provider']} | {rank} |")

    if good_fresh:
        lines.extend(["", "### Identity Reveal — Fresh Eyes", "",
            f"| Fresh-Eyes | {good_fresh[0].display_name} | {good_fresh[0].provider} |"])

    lines.extend(["", "---", "",
        f"*Generated by The Tribunal v0.5.0 — session logs in `{session_dir}/`*"])

    (session_dir.narrative / "debrief.md").write_text("\n".join(lines))


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

    (session_dir.meta / "council-log.json").write_text(json.dumps(log_data, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="The Tribunal — Council Orchestrator")
    parser.add_argument("--briefing", required=True, help="Path to briefing file or '-' for stdin")
    parser.add_argument("--sealed-submission", default=None, help="Path to host agent's sealed submission")
    parser.add_argument("--depth", default=None, help="Depth level (T1|T2|T3|T4|T5|T6). Old names also accepted: QUICK|BALANCED|THOROUGH|RIGOROUS|EXHAUSTIVE|NUCLEAR")
    parser.add_argument("--emit", default="summary", choices=["summary", "json", "paths"],
                        help="Output mode: summary (human-readable), json (structured), paths (file paths only)")
    parser.add_argument("--session-id", default=None, help="Override session ID (default: auto-generated)")
    parser.add_argument("--session-dir", default=None, help="Override session output directory (default: auto-generated)")
    parser.add_argument("--tts", action="store_true",
                        help="After session: generate screenplay then ElevenLabs audio MP3. "
                             "Requires ELEVENLABS_API_KEY and ffmpeg.")

    args = parser.parse_args()

    # Load config
    depth = args.depth or os.environ.get("CONCLAVE_DEFAULT_DEPTH", "T1")
    config = load_config(depth)

    # Read briefing (before session ID so we can extract a topical slug)
    if args.briefing == "-":
        briefing_text = sys.stdin.read()
    else:
        briefing_text = Path(args.briefing).read_text()

    # Generate session (use original briefing for slug — before enrichment)
    session_id = args.session_id or generate_session_id(briefing_text)
    if args.session_dir:
        override_root = Path(args.session_dir)
        override_root.mkdir(parents=True, exist_ok=True)
        session_dir = SessionDir(override_root)
    else:
        session_dir = create_session_dir(config.log_dir, session_id)
    progress = Progress(session_id, config.depth.name)

    progress.session_start()

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
        "claim_matrix": [],
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
    # Phase 4: Challenge round (T2+)
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

    # ================================================================
    # Phase 4.5: Evidence injection (T2+ — counter-evidence via Perplexity)
    # ================================================================
    counter_evidence_brief = ""
    if config.depth.debate_rounds > 0 and challenge_responses:
        counter_evidence_brief = run_evidence_injection(
            advocate_responses=advocate_responses,
            briefing=briefing_text,
            config=config,
            session_dir=session_dir,
            progress=progress,
        )

    # Pre-compute cardinal availability (needed for T6 mid-debate checkpoint)
    has_cardinals = (config.depth.cardinals_bishops > 0 or
                     config.depth.cardinals_priests > 0 or
                     config.depth.cardinals_deacons > 0)
    cardinals: list[ModelDef] = []

    # ================================================================
    # Phase 5: Debate rounds (T2+)
    # For T6 (Red Team): split into two halves with judicial checkpoint
    # ================================================================
    debate_rounds: list[list[ModelResponse]] = []
    checkpoint_cardinal_responses: list[ModelResponse] = []

    if config.depth.debate_rounds > 0:
        checkpoint_round = config.depth.mid_debate_checkpoint  # 0 = no checkpoint

        if checkpoint_round > 0 and config.depth.debate_rounds > checkpoint_round:
            # ---- T6 (Red Team): debate in two halves with judicial checkpoint ----
            progress.info(f"T6 Red Team mode: debate rounds 1-{checkpoint_round}, then judicial checkpoint, then rounds {checkpoint_round+1}-{config.depth.debate_rounds}")

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
                counter_evidence_brief=counter_evidence_brief,
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
                    all_bishops=config.bishops,
                    all_priests=config.priests,
                )

                # Write checkpoint files with distinct names
                for resp in successful_responses(checkpoint_cardinal_responses):
                    path = session_dir.judicial / f"checkpoint-judge-{resp.alias.lower()}.md"
                    path.write_text(f"# Mid-Debate Checkpoint: {resp.alias}\n\n{resp.content}\n")

                all_responses["cardinals"].extend(checkpoint_cardinal_responses)

                # If judges say REMAND at checkpoint, we still continue
                # but flag it — they get to see both halves in the final judgment
                if checkpoint_remand:
                    progress.warn(f"Judges flagged concerns at checkpoint: {checkpoint_reason}")
                    (session_dir.judicial / "checkpoint-flag.md").write_text(
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
                counter_evidence_brief=counter_evidence_brief,
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
                counter_evidence_brief=counter_evidence_brief,
            )
            for round_resps in debate_rounds:
                all_responses["debates"].extend(round_resps)

    # Build final stability report (across ALL rounds)
    # Prefer the version written by run_debate_phase (has external stability
    # data when available). Fall back to recomputing if file doesn't exist.
    stability_report = ""
    if debate_rounds and config.depth.position_stability_audit:
        stability_file = session_dir.deliberation / "position-stability.md"
        if stability_file.exists():
            stability_report = stability_file.read_text()
        else:
            stability_report = build_position_stability_report(
                advocate_responses, debate_rounds
            )

    # ================================================================
    # Phase 6: Judicial review (T2+)
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
            all_bishops=config.bishops,
            all_priests=config.priests,
        )
        all_responses["cardinals"].extend(cardinal_responses)

        # Handle REMAND (maximum 1 per session)
        if should_remand and remand_count < 1:
            remand_count += 1
            progress.warn(f"REMAND #{remand_count} — running additional debate round...")

            (session_dir.judicial / "remand-brief.md").write_text(
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
                all_bishops=config.bishops,
                all_priests=config.priests,
            )
            all_responses["cardinals"].extend(cardinal_responses_2)
            cardinal_responses = cardinal_responses_2

    # ================================================================
    # Verdict coherence gate (after all judicial review is final)
    # ================================================================
    coherence_warnings: list[str] = []
    if has_cardinals and cardinal_responses:
        coherence_warnings = check_verdict_coherence(
            cardinal_responses=cardinal_responses,
            session_dir=session_dir,
            progress=progress,
        )

    # ================================================================
    # Majority Opinion synthesis (T2+ — consolidate individual verdicts)
    # ================================================================
    majority_opinion_response: Optional[ModelResponse] = None

    if has_cardinals and cardinal_responses:
        majority_opinion_response = synthesize_majority_opinion(
            cardinal_responses=cardinal_responses,
            briefing=briefing_text,
            config=config,
            session_dir=session_dir,
            progress=progress,
        )
        if majority_opinion_response:
            all_responses.setdefault("majority_opinion", []).append(majority_opinion_response)

    # ================================================================
    # Dissenting opinions (T2+ — after verdict, before Fresh Eyes)
    # ================================================================
    dissent_responses: list[ModelResponse] = []

    if has_cardinals and debate_rounds and cardinal_responses:
        dissenters = detect_dissenters(
            advocate_responses, debate_rounds, cardinal_responses
        )
        if dissenters:
            # Use majority opinion for dissenters if available; fall back to raw opinions
            _majority_text = ""
            if majority_opinion_response and majority_opinion_response.status == "success":
                _majority_text = majority_opinion_response.content or ""

            dissent_responses = run_dissent_phase(
                dissenters=dissenters,
                advocate_responses=advocate_responses,
                advocates=advocates,
                cardinal_responses=cardinal_responses,
                briefing=briefing_text,
                config=config,
                session_dir=session_dir,
                progress=progress,
                majority_opinion_text=_majority_text,
            )
            all_responses["dissents"] = dissent_responses
        else:
            progress.info("No dissenters detected — all advocates either conceded or were accepted.")

    # ================================================================
    # Claim-Evidence Matrix extraction (T2+ — whenever judges exist)
    # ================================================================
    claim_matrix_response: Optional[ModelResponse] = None
    if has_cardinals and cardinal_responses:
        claim_matrix_response = extract_claim_evidence_matrix(
            briefing=briefing_text,
            advocate_responses=advocate_responses,
            challenge_responses=challenge_responses,
            debate_rounds=debate_rounds,
            cardinal_responses=cardinal_responses,
            config=config,
            session_dir=session_dir,
            progress=progress,
        )
        if claim_matrix_response:
            all_responses["claim_matrix"] = [claim_matrix_response]

    # ================================================================
    # Phase 7: Fresh Eyes (T5 and T6)
    # ================================================================
    fresh_eyes_response: Optional[ModelResponse] = None
    seated_cardinal_ids = set()
    if has_cardinals:
        seated_cardinal_ids = {r.model_id for r in cardinal_responses}

    if config.depth.name in ("T5", "T6"):
        prelim_output = build_final_output(
            advocate_responses, challenge_responses, debate_rounds,
            cardinal_responses, None, session_id, config,
            majority_opinion_response=majority_opinion_response,
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
    # Play-by-play narrative (T2+)
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
        majority_opinion_response=majority_opinion_response,
    )
    (session_dir.meta / "final-output.md").write_text(final_output)

    # Write full deliberation record for audit
    council_record = build_council_record(
        advocate_responses, challenge_responses, debate_rounds,
        cardinal_responses, fresh_eyes_response, session_id, config,
        dissent_responses=dissent_responses,
        majority_opinion_response=majority_opinion_response,
    )
    (session_dir.meta / "council-record.md").write_text(council_record)

    write_debrief(all_responses, session_id, session_dir, config, wall_time, remand_count)
    write_council_log(all_responses, session_id, session_dir, config, wall_time, remand_count)

    # ---- Session summary (T2+ only) ----
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
            majority_opinion_response=majority_opinion_response,
            claim_matrix_response=claim_matrix_response,
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

        voice_script_path = session_dir.narrative / "voice-script.json"
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
        files_in_dir = len(list(session_dir.rglob("*")))
        print(f"TRIBUNAL SESSION COMPLETE: {session_id}")
        print(f"  Depth: {config.depth.name} | Advocates: {len(good_adv)} | Cost: ${cost:.4f}")
        print(f"  Files: {files_in_dir} artifacts in {session_dir}/")
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
        _exec_briefs = sorted(session_dir.glob("[0-9]*-exec-brief-*.pdf"))
        if _exec_briefs:
            print(f"  Brief:      {_exec_briefs[-1]}")
        elif (session_dir / "exec-brief.pdf").exists():
            print(f"  Brief:      {session_dir}/exec-brief.pdf")
        print(f"  Debrief:    {session_dir.narrative}/debrief.md")
        if (session_dir.narrative / "play-by-play.md").exists():
            print(f"  Play-by-play: {session_dir.narrative}/play-by-play.md")
        if (session_dir.narrative / "screenplay.md").exists():
            print(f"  Screenplay: {session_dir.narrative}/screenplay.md")
        audio_path = session_dir.narrative / f"{session_id}-audio.mp3"
        if audio_path.exists():
            print(f"  Audio:      {audio_path}")
        print(f"  Log:        {session_dir.meta}/council-log.json")
        print(f"")
        print(f"  ✅ Session saved to: {session_dir.resolve()}/")
    elif args.emit == "json":
        log = json.loads((session_dir.meta / "council-log.json").read_text())
        print(json.dumps(log, indent=2))
    elif args.emit == "paths":
        for p in sorted(session_dir.rglob("*")):
            if p.is_file():
                print(str(p))


if __name__ == "__main__":
    main()
