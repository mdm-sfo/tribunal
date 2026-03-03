# The Tribunal: Architecture & Design Specification

> **Author:** m@murrays.org
> **Date:** February 28, 2026
> **Status:** DRAFT — Design Review (updated with Kelley-Riedl 2026 findings + NUCLEAR tier)
> **Version:** 0.5.0

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Communication Format](#communication-format)
4. [Orchestration Protocol](#orchestration-protocol)
5. [Anti-Sycophancy & Quality Controls](#anti-sycophancy--quality-controls)
6. [Position Stability & Sycophantic Drift Detection](#position-stability--sycophantic-drift-detection)
7. [Skill File Structure](#skill-file-structure)
8. [Platform Recommendation](#platform-recommendation)
9. [Configuration & API Setup](#configuration--api-setup)
10. [Example Use Case](#example-use-case)
11. [Debrief Report Format](#debrief-report-format)
12. [Open Questions & Next Steps](#open-questions--next-steps)
13. [Appendix A: Orchestration Patterns (from last30days-skill)](#appendix-a-orchestration-patterns-from-last30days-skill)
14. [Appendix B: Research References](#appendix-b-research-references)
15. [Appendix C: Token & Cost Estimation Model](#appendix-c-token--cost-estimation-model)

---

## Executive Summary

Single-model AI interactions suffer from well-documented failure modes: hallucinations go unchecked, sycophancy masks weak reasoning, and blind spots compound silently. The Tribunal solves this by turning any coding agent into the orchestrator of a structured multi-model deliberation. When given a task — write a prompt, research a topic, architect a system — the host agent does the work itself, then convenes a tribunal of 2–5 additional AI models via API. The models submit independent solutions, exchange structured critiques, debate disagreements with evidence, and converge on a best-in-class output validated by a "Fresh Eyes" reviewer with zero prior context.

The v0.5.0 update incorporates findings from Kelley & Riedl (2026), "Sycophantic Drift in Multi-Turn LLM Dialogues," which demonstrated that multi-turn debate accelerates sycophantic convergence — flip rates reach ~80% by round 10, with rounds 3–5 as the critical "interesting zone." The paper also established a key distinction between *affective alignment* (changing tone toward the challenger) and *epistemic alignment* (abandoning one's actual position). The Tribunal v0.5.0 addresses this with position stability tracking, advisor-framed prompts, and a new NUCLEAR depth tier with mid-debate judicial checkpoints.

The skill ships as a portable Agent Skills package (SKILL.md + bundled Python scripts) that installs identically on Claude Code, Codex CLI, Gemini CLI, and GitHub Copilot CLI. It draws on the research-backed protocols from the [AI Council Framework](https://github.com/focuslead/ai-council-framework) — including hard debate-round limits, confidence-weighted voting, and protected dissent — and packages them into a practical, installable skill that any developer can run from their terminal today.

**The one-paragraph pitch:** Install one skill directory, set your API keys, and your coding agent gains the ability to convene a tribunal of the world's best AI models for any task — producing outputs that have been independently generated, cross-critiqued, debated, and validated, with a full audit trail of how consensus was reached.

---

## Architecture Overview

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            USER                                         │
│                     (Task / Question / Brief)                           │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       HOST AGENT (Orchestrator)                         │
│                   Claude Code / Codex / Gemini CLI                      │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    THE TRIBUNAL SKILL (SKILL.md)                     │  │
│  │                                                                   │  │
│  │  1. Parses task brief                                             │  │
│  │  2. Generates its OWN submission (as an advocate)            │  │
│  │  3. Calls council_orchestrator.py                                 │  │
│  │                                                                   │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │              council_orchestrator.py                         │  │  │
│  │  │                                                             │  │  │
│  │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │  │  │
│  │  │  │Claude API│  │OpenAI   │  │Gemini   │  │DeepSeek │       │  │  │
│  │  │  │(Sonnet) │  │(GPT-5)  │  │(2.5 Pro)│  │(R1)     │       │  │  │
│  │  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘       │  │  │
│  │  │       │            │            │            │              │  │  │
│  │  │       └────────────┴─────┬──────┴────────────┘              │  │  │
│  │  │                          │                                  │  │  │
│  │  │                          ▼                                  │  │  │
│  │  │              ┌───────────────────────┐                      │  │  │
│  │  │              │   debate_manager.py    │                      │  │  │
│  │  │              │                       │                      │  │  │
│  │  │              │  Round 1: Critiques   │                      │  │  │
│  │  │              │  Round 2: Rebuttals   │◄─── Max 3 rounds     │  │  │
│  │  │              │  Round 3: Final pos.  │                      │  │  │
│  │  │              └───────────┬───────────┘                      │  │  │
│  │  │                          │                                  │  │  │
│  │  │                          ▼                                  │  │  │
│  │  │              ┌───────────────────────┐                      │  │  │
│  │  │              │  Fresh Eyes Validator  │                      │  │  │
│  │  │              │  (Zero debate context) │                      │  │  │
│  │  │              └───────────┬───────────┘                      │  │  │
│  │  │                          │                                  │  │  │
│  │  │                          ▼                                  │  │  │
│  │  │              ┌───────────────────────┐                      │  │  │
│  │  │              │ report_generator.py    │                      │  │  │
│  │  │              │ (Debrief / Sitrep)     │                      │  │  │
│  │  │              └───────────┬───────────┘                      │  │  │
│  │  │                          │                                  │  │  │
│  │  └──────────────────────────┼──────────────────────────────────┘  │  │
│  │                             │                                     │  │
│  └─────────────────────────────┼─────────────────────────────────────┘  │
│                                │                                        │
└────────────────────────────────┼────────────────────────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     FINAL OUTPUT         │
                    │  + Council Log (.md)     │
                    │  + Debrief Report (.md)  │
                    └─────────────────────────┘
```

### Key Roles

| Role | Actor | Responsibilities |
|------|-------|-----------------|
| **Orchestrator** | Host agent (e.g., Claude Code) | Manages workflow, submits its own work as a peer. Does NOT judge or cast deciding votes. Facilitates the process; judges evaluate the output. |
| **Advocates** | API-called models (Claude, GPT, Gemini, DeepSeek, Grok, etc.) | Produce independent submissions as hypothesis+evidence arguments. Participate in structured debate. Must defend their positions with verifiable proof. |
| **Judges (The Bench)** | Randomly-assigned subset of models with zero debate context | The impartial judging panel. Judges see ONLY the anonymized arguments — never who wrote what. They evaluate hypothesis clarity, evidence quality, and intellectual honesty. They verify facts and supporting data cited by advocates. Judges render the final verdict. |
| **Fresh Eyes Validator** | Designated model with zero debate context | Reviews the consensus output cold after the judges have ruled — a final sanity check. Provides constructive review: "What's missing? What would you improve?" |

> **Why "Judges"?** The project is called The Tribunal — a judicial body where impartial judges deliberate on the evidence until a verdict is rendered signaling consensus. In The Tribunal, judges on The Bench evaluate the advocates' arguments and render verdicts. The metaphor is precise: judges don't argue a case, they evaluate and decide.

### Judicial Assignment

Judges are assigned **randomly** at session start from the available model pool. The number of judges is configurable per session:

| Depth | Advocates | Judges | Total Models |
|-------|-----------|-----------|-------------|
| QUICK | 2 | 0 (no judging phase) | 2 |
| BALANCED | 2-3 | 1 | 3-4 |
| THOROUGH | 3-4 | 2-3 | 5-7 |
| RIGOROUS | 3-4 | 2-4 | 6-8 |
| EXHAUSTIVE | 4-5 | 2-5 | 7-10 |
| NUCLEAR | 5 | 2-6 | 8-11 |

**Random assignment rules:**
1. Judges are selected randomly from the configured model pool at session start.
2. A model cannot serve as both Advocate and Judge in the same session.
3. The orchestrator (host agent) is never a Judge — it may be an Advocate but never a judge.
4. At BALANCED+ depths, at least one Judge should be from a different provider family than any Advocate (to maximize independence).
5. Judge assignments are logged but NOT revealed to Advocates during deliberation.

**What judges verify:**
- Are the claims in each hypothesis actually true? (Fact-checking)
- Is the cited evidence real and accurately represented? (Source verification)
- Are the logical connections between evidence and conclusion sound? (Reasoning audit)
- Did the advocate honestly acknowledge counterarguments? (Intellectual honesty check)

### Critical Design Decision: Orchestrator as Peer

The host agent occupies a dual role. It orchestrates the tribunal (scheduling rounds, collecting responses, synthesizing) **and** submits its own work as an advocate. To prevent anchoring bias:

1. The host generates its submission **before** seeing any other model's output.
2. Its submission is anonymized and shuffled into the pool alongside API responses.
3. During synthesis, the orchestrator weights all submissions equally — including its own.

This is analogous to a PM who contributes a design proposal but doesn't get veto power in the review.

---

## Communication Format

### Design Principle: Dual-Readable Logs

Council logs must be simultaneously **human-readable** (scannable Markdown) and **machine-parseable** (structured YAML blocks). This is achieved by embedding YAML front matter and fenced YAML code blocks within a Markdown document. A developer can read the log file in any Markdown viewer; the orchestration scripts parse the YAML blocks programmatically.

### Task Briefing

```markdown
# Council Session: [SESSION-ID]

## Task Briefing
<!-- tribunal:briefing -->
```yaml
session_id: "tribunal-20260228-183042"
timestamp: "2026-02-28T18:30:42-08:00"
depth: "THOROUGH"           # QUICK | BALANCED | THOROUGH | RIGOROUS | EXHAUSTIVE
task_type: "prompt_engineering"
models:
  - id: "claude-4-sonnet"
    role: "advocate"
  - id: "gpt-5"
    role: "advocate"
  - id: "gemini-2.5-pro"
    role: "advocate"
  - id: "deepseek-r1"
    role: "advocate"
  - id: "claude-4-sonnet"
    role: "fresh_eyes"
    context: "zero"
```

### Objective
[Human-readable description of the task]

### Success Criteria
- [Criterion 1]
- [Criterion 2]
- [Criterion 3]

### Constraints
- [Any constraints, style guides, technical requirements]
```

### Individual Submission Format (Hypothesis + Evidence)

Each advocate returns a structured submission built around a **hypothesis** and **supporting evidence**. This format was validated during the Tribunal dry run and produces dramatically more rigorous debate than open-ended responses.

```markdown
## Submission: [MODEL-ALIAS]
<!-- tribunal:submission -->
```yaml
model: "gpt-5"
alias: "Member-B"          # Anonymized during deliberation
timestamp: "2026-02-28T18:31:15-08:00"
confidence: 82             # 0-100
approach_summary: "One-sentence summary of the approach taken"
```

### Hypothesis

[One clear, falsifiable sentence: "X is the best approach because Y."]

### Evidence

#### Evidence 1: [Title]
- **Claim:** [What you're asserting]
- **Proof:** [Specific, verifiable data — numbers, benchmarks, citations, code examples]
- **Source:** [Where this data comes from, with URLs where possible]
- **Why it matters:** [How this specifically supports the hypothesis]

#### Evidence 2: [Title]
[Same structure — repeat for 3-5 pieces of evidence]

### Counterargument Acknowledgment

[Honestly state the strongest argument AGAINST your position and explain why your hypothesis still holds despite it.]

### Self-Assessment

| Dimension | Score (1-10) | Notes |
|-----------|-------------|-------|
| Hypothesis clarity | 8 | Falsifiable, specific |
| Evidence strength | 7 | 3 of 5 pieces have hard data |
| Intellectual honesty | 9 | Acknowledged main weakness |
| Relevance to task | 8 | Directly addresses requirements |
```

> **Why hypothesis + evidence?** Open-ended submissions devolve into opinion swapping. The hypothesis format forces advocates to commit to a falsifiable position and back it with verifiable proof. Judges can then fact-check claims independently. This structure was directly inspired by the Tribunal dry run (Fireworks vs Together AI debate), where all three advocates produced dramatically more rigorous arguments under this format than they would have with free-form responses.

### Structured Critique Format

After all submissions are collected, each model reviews every other submission (but not its own). The critique format enforces balanced evaluation — strengths AND concerns, not just criticism:

```markdown
## Critique: [REVIEWER-ALIAS] → [AUTHOR-ALIAS]
<!-- tribunal:critique -->
```yaml
reviewer: "Member-A"
target: "Member-B"
round: 1
overall_assessment: "STRONG"  # STRONG | SOLID | NEEDS_WORK | WEAK
recommendation: "ADOPT_WITH_CHANGES"  # ADOPT | ADOPT_WITH_CHANGES | REWORK | REJECT
```

### Top 5 Strengths
1. **[Strength label]** — [Specific evidence from the submission]
2. **[Strength label]** — [Specific evidence from the submission]
3. **[Strength label]** — [Specific evidence from the submission]
4. **[Strength label]** — [Specific evidence from the submission]
5. **[Strength label]** — [Specific evidence from the submission]

### Top 5 Concerns
1. **[Concern label]** — [Specific evidence + suggested fix]
2. **[Concern label]** — [Specific evidence + suggested fix]
3. **[Concern label]** — [Specific evidence + suggested fix]
4. **[Concern label]** — [Specific evidence + suggested fix]
5. **[Concern label]** — [Specific evidence + suggested fix]

### Key Question
> [One question the reviewer most wants the author to address]
```

### Debate Round Format

```markdown
## Debate Round [N]: [MODEL-ALIAS]
<!-- tribunal:debate -->
```yaml
model_alias: "Member-C"
round: 2
position: "PARTIALLY_AGREE"  # AGREE | DISAGREE | PARTIALLY_AGREE
confidence: 75               # Can change from previous round
confidence_delta: -10         # Must explain if changed
position_changed: true        # Triggers evidence requirement
```

### Position Statement
[2-3 sentences on current position]

### Evidence for Position Change
> **Required when `position_changed: true`**
> [Specific evidence, citation, or reasoning — not "I was persuaded by Member-A's argument"]

### Reasoning
[Detailed reasoning with references to specific critiques received]

### What Would Change My Mind
> [Specific, falsifiable evidence or argument that would cause a position shift]
```

### Consensus Synthesis Format

```markdown
## Consensus Synthesis
<!-- tribunal:synthesis -->
```yaml
consensus_type: "STRONG"     # UNANIMOUS | STRONG | MAJORITY | SPLIT
agreement_score: 0.85        # 0.0-1.0
rounds_used: 2
positions_changed: 3
total_positions: 12
```

### Agreed Elements
[Bullet list of elements all/most models converged on]

### Contested Elements
[Bullet list of elements with remaining disagreement, with majority AND minority positions preserved]

### Synthesized Output
[The merged, best-of-all-worlds output incorporating agreed improvements]
```

### Fresh Eyes Validation Format

```markdown
## Fresh Eyes Review
<!-- tribunal:fresh_eyes -->
```yaml
validator_model: "claude-4-sonnet"
context_provided: "final_output_only"  # Receives ONLY the synthesized output, not the debate
timestamp: "2026-02-28T18:45:22-08:00"
```

### What's Missing?
[Observations about gaps, unstated assumptions, edge cases]

### What Would You Improve?
[Constructive suggestions — NOT "find the bugs" framing, which induces hallucinated errors]

### Overall Assessment
- **Quality Score:** [1-10]
- **Production Ready:** [YES | YES_WITH_CHANGES | NO]
- **Critical Issues:** [Count, 0 if none]
```

> **Note on prompt framing:** The Fresh Eyes validator is never asked to "find errors" — research shows this causes models to hallucinate problems that don't exist. Instead, the prompt uses constructive framing: "What's missing? What would you improve?" This produces dramatically better validation results, as documented in the [AI Council Framework](https://github.com/focuslead/ai-council-framework).

### Judicial Opinion Format

Judges receive ONLY the anonymized advocate submissions (hypothesis + evidence). They do NOT see model identities, debate history, or critique exchanges. Their job is to judge the arguments on their merits and verify the evidence.

```markdown
## Judicial Opinion: [JUDGE-ALIAS]
<!-- tribunal:judicial_opinion -->
```yaml
judge_model: "kimi-k2.5"
alias: "Judge-1"
context_provided: "anonymized_submissions_only"
timestamp: "2026-02-28T18:42:00-08:00"
```

### Advocate Evaluations

#### Advocate [ALIAS]
- **Hypothesis Clarity:** [1-10] — [Is it falsifiable, specific, and clear?]
- **Evidence Quality:** [1-10] — [Verifiable data vs marketing claims?]
- **Intellectual Honesty:** [1-10] — [Did they fairly acknowledge counterarguments?]
- **Fact-Check Results:**
  - [Claim X]: ✅ Verified / ❌ Refuted / ⚠️ Unverifiable — [Notes]
  - [Claim Y]: ✅ Verified / ❌ Refuted / ⚠️ Unverifiable — [Notes]

[Repeat for each advocate]

### Strongest Evidence Point
[Which single piece of evidence across all briefs was most compelling, and why?]

### Biggest Gap
[What did ALL advocates miss or inadequately address?]

### Verdict
- **Winner:** [Advocate ALIAS or "Split Decision"]
- **Score:** [Advocate A] X/10, [Advocate B] Y/10, ...
- **Confidence:** [0-100]
- **What would change my mind:** [Specific evidence that could flip the verdict]

### Unanimity Assessment
[If all advocates chose the same side: Is this convergence a strength (independent agreement) or a concern (groupthink)? Cite specific evidence for your assessment.]
```

> **Why judges verify facts:** During the Tribunal dry run, Kimi K2.5 (serving as judge) caught that advocates were citing marketing claims ("200+ models") verbatim from the same source — a sign of shared talking points rather than independent analysis. Judges catching this kind of evidence laundering is essential for trustworthy deliberation.

### Judicial Remand Protocol

When judges detect systemic problems in advocate submissions — groupthink, marketing regurgitation, information cascades, or insufficient rigor — they don't just flag it in their verdict. They **remand the task back to the advocates** with specific concerns that must be addressed.

This protocol was directly inspired by the Tribunal dry run, where Kimi K2.5 identified three red flags suggesting the advocates' unanimity reflected shared marketing materials rather than independent analysis:

1. **Uniform concession pattern** — All advocates conceded the same single advantage to the opposing side, using suspiciously similar language.
2. **Precision alignment** — Identical statistics ("200+ models", "99.9% SLA") appeared across multiple briefs with the same phrasing — a hallmark of shared source material.
3. **Absence of dissent on a legitimate dimension** — No advocate argued for the opposing platform despite its documented technical merits in a relevant area (agent primitives). The absence of any dissent on a platform with legitimate strengths is "statistically suspicious."

#### Remand Triggers

Judges issue a **REMAND** verdict when any of the following are detected:

| Trigger | Detection Method | Threshold |
|---------|-----------------|----------|
| **Information Cascade** | ≥2 advocates use identical statistics with same phrasing | Any occurrence |
| **Marketing Regurgitation** | Claims that read like vendor copy without independent verification | ≥3 unverified marketing claims across briefs |
| **Suspicious Unanimity** | All advocates reach same conclusion without sufficient independent reasoning | Judges determine unanimity is groupthink rather than convergence |
| **Missing Legitimate Counterposition** | A defensible opposing position exists but no advocate argued it | Judges identify the unargued position with evidence |
| **Evidence Laundering** | Multiple advocates cite the same source as if independently discovered | ≥2 advocates cite identical source with similar framing |

#### Remand Process

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    Judges     │────▶│   REMAND     │────▶│  Advocates   │
│  detect issue │     │  with specs  │     │  revise work │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐
                                          │   Judges      │
                                          │  re-evaluate  │
                                          └──────────────┘
```

1. **Judges issue REMAND verdict** with a structured remand brief:
   - List of specific concerns (e.g., "Claims X and Y appear to be marketing copy")
   - Questions advocates must answer (e.g., "Provide independent benchmarks, not vendor-reported numbers")
   - Explicit instruction: "Address the unargued counterposition" (if applicable)
   - Maximum one remand per session (prevents infinite loops)

2. **Advocates receive the remand brief** and must:
   - Directly address each judicial concern with new or revised evidence
   - If they maintain their original position, they must provide genuinely independent verification
   - At least one advocate must seriously argue the counterposition the judges identified (forced devil's advocate)

3. **Judges re-evaluate** the revised submissions:
   - If concerns are adequately addressed → proceed to final verdict
   - If concerns persist → judges note the deficiency in their verdict and proceed (no second remand)

#### Remand Format

```markdown
## Judicial Remand: [JUDGE-ALIAS]
<!-- tribunal:judicial_remand -->
```yaml
judge_alias: "Judge-1"
verdict: "REMAND"
remand_reason: "information_cascade"  # information_cascade | marketing_regurgitation | suspicious_unanimity | missing_counterposition | evidence_laundering
timestamp: "2026-02-28T18:43:00-08:00"
```

### Concerns

1. **[Concern Title]** — [Specific evidence of the problem, citing exact phrases from advocate briefs]
2. **[Concern Title]** — [Specific evidence]
3. **[Concern Title]** — [Specific evidence]

### Required Actions for Advocates

1. [Specific action, e.g., "Provide independently verified benchmark data for claim X"]
2. [Specific action, e.g., "At least one advocate must argue the case for Platform B's agent primitives"]
3. [Specific action, e.g., "Explain why your pricing comparison omitted competitor's batch discount"]

### Unargued Counterposition

[If applicable: "No advocate argued for [X] despite its documented strengths in [Y]. At least one advocate must seriously make this case in the revised round."]
```

> **The Kimi Principle:** Unanimous agreement is not automatically a sign of strength — it can be a sign of laziness or shared source material. Judges should be *more* skeptical of unanimity than of split decisions. As Kimi K2.5 observed during the dry run: *"A genuine independent assessment would likely have diverged... The absence of any advocate arguing for [the alternative] — despite its documented suitability — is statistically suspicious given the platform's legitimate technical merits."* This ethos — treating suspicious unanimity as a defect, not a feature — is the core judicial philosophy.

---

## Orchestration Protocol

### Phase Diagram

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ 1. BRIEF │───▶│ 2. WORK  │───▶│ 3.COLLECT│───▶│4.CRITIQUE│
 │          │    │(parallel) │    │          │    │          │
 └──────────┘    └──────────┘    └──────────┘    └─────┬────┘
                                                       │
                                                       ▼
                 ┌──────────┐    ┌──────────┐    ┌──────────┐
                 │6.JUDICIAL│◀───│6.SYNTHESIZE◀──│ 5.DEBATE │
                 │  VERDICT │    │          │    │(depth-   │
                 └─────┬────┘    └──────────┘    │dependent)│
                       │                         └─────┬────┘
          ┌────────────┤                               │
          │ REMAND?    │ NO                     ┌──────┘
          ▼ YES        ▼                        │ NUCLEAR?
 ┌──────────┐    ┌──────────┐    ┌──────────┐   ▼ YES
 │ Advocates│    │7.VALIDATE│───▶│ 8.DELIVER│   ┌──────────────┐
 │  revise  │    │(Fresh Eyes)   │          │   │Mid-Debate    │
 └────┬─────┘    │EXHAUSTIVE+│   └──────────┘   │Judicial      │
      │          └──────────┘                   │Checkpoint @R4│
      └──────▶ Back to Phase 6                  └──────┬───────┘
               (re-evaluate, max 1 remand)             │
                                                       ▼
                                                Rounds 5-7
                                                continue...
```

### Phase 1: Task Intake & Briefing

The orchestrator receives the user's task and constructs a standardized briefing:

1. **Parse the task** — Identify task type (prompt engineering, code review, research, architecture, etc.).
2. **Auto-select depth** — Based on task complexity, suggest a depth level (user can override). Heuristics:
   - Simple question → QUICK (2 models, 0 debate rounds)
   - Standard task → BALANCED (3 models, 1 round)
   - Important deliverable → THOROUGH (4 models, 3 rounds)
   - High-stakes decision → RIGOROUS (4 models, 5 rounds)
   - Mission-critical → EXHAUSTIVE (5 models, 5 rounds + stability audit + Fresh Eyes)
   - Adversarial-grade validation → NUCLEAR (5 models, 7 rounds + mid-debate judicial checkpoint + stability audit + Fresh Eyes)
3. **Select advocates** — Choose models from the configured pool based on depth level and task type.
4. **Generate the briefing document** — Write the Task Briefing block (see Communication Format above).

### Phase 2: Independent Work Phase

**All models work in parallel with zero cross-contamination.** This is the most critical anti-bias measure in the system.

1. The **host agent** generates its own submission first, then seals it (writes to file before seeing any API responses).
2. The orchestrator script (`council_orchestrator.py`) dispatches the task briefing to all advocate APIs **simultaneously** using `asyncio`.
3. Each model receives only the task briefing — no other model's output, no hints about expected answers.
4. Responses are collected with a configurable timeout (default: 120 seconds per model).
5. All submissions are **anonymized** (assigned aliases: Member-A, Member-B, etc.) and **shuffled** before the critique phase.

```python
# Parallel dispatch using ThreadPoolExecutor (pattern from last30days-skill)
def independent_work_phase(briefing, models, timeouts):
    submissions = []
    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        futures = {
            executor.submit(call_model, model, briefing): model
            for model in models
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                result = future.result(timeout=timeouts["per_model"])
                submissions.append(result)
                sys.stderr.write(f"[tribunal]   ✓ {model.alias} submitted\n")
            except TimeoutError:
                sys.stderr.write(f"[tribunal]   ✗ {model.alias} timed out — excluded\n")
            except Exception as e:
                sys.stderr.write(f"[tribunal]   ✗ {model.alias} failed: {e}\n")
    return anonymize_and_shuffle(submissions)
```

### Phase 3: Submission Collection

1. Validate that all submissions arrived (handle timeouts/failures gracefully — a model that fails is excluded, not retried endlessly).
2. Parse each submission into the structured format.
3. Write all submissions to the session log file.
4. **Quality gate:** If fewer than 2 submissions succeed, abort the session and fall back to the host agent's solo output.

### Phase 4: Structured Critique Exchange

Each model reviews every other submission using the "Top 5 Strengths / Top 5 Concerns" format:

1. Each model receives **all other submissions** (not its own) plus the original briefing.
2. Models produce structured critiques for each submission they review.
3. The orchestrator collects all critiques and appends them to the session log.
4. **Cross-pollination checkpoint:** At this point, each model has seen all other approaches and provided structured feedback.

> **API call count for critique phase:** For N models, this requires N × (N-1) critique generations. At THOROUGH depth (4 models), that's 12 critique calls. To manage cost, each critique prompt is compact: the model receives only the target submission + briefing, not the full debate history.

### Phase 5: Debate Rounds (Depth-Dependent)

Debate occurs only when there are meaningful disagreements. The orchestrator determines whether debate is needed:

1. **Disagreement detection:** If all critiques are "ADOPT" or "ADOPT_WITH_CHANGES" with minor edits, skip to synthesis.
2. **Round structure:** Each model receives its own submission + all critiques it received + all critiques it wrote. It must:
   - State its updated position (AGREE / DISAGREE / PARTIALLY_AGREE)
   - Provide updated confidence with delta explanation
   - If position changed: supply evidence (not just "Member-B made a good point")
   - State what would change its mind
   - **Declare position stability (1-5 scale):** A self-reported score indicating how likely they are to change position. This feeds the sycophantic drift detection system (see [Position Stability & Sycophantic Drift Detection](#position-stability--sycophantic-drift-detection)).
3. **Convergence check:** After each round, calculate agreement score. If above the depth-level's consensus target, exit debate.
4. **Hard limits by depth:** Debate rounds are calibrated per depth tier based on Kelley & Riedl (2026) findings that rounds 3-5 are the "interesting zone" where genuine debate occurs, and sycophantic drift accelerates dramatically beyond that.

| Depth | Max Rounds | Mid-Debate Checkpoint | Stability Audit | Fresh Eyes |
|-------|------------|----------------------|-----------------|------------|
| QUICK | 0 | — | No | No |
| BALANCED | 1 | — | No | No |
| THOROUGH | 3 | — | No | No |
| RIGOROUS | 5 | — | No | No |
| EXHAUSTIVE | 5 | — | Yes | Yes |
| NUCLEAR | 7 | After R4 | Yes | Yes |

#### NUCLEAR Mid-Debate Judicial Checkpoint

At NUCLEAR depth, debate splits into two halves with a judicial checkpoint between them:

```
Rounds 1-4: Advocates debate normally
    │
    ▼
 Judicial Checkpoint: Full panel reviews debate progress
  + Position stability scorecard from rounds 1-4
  + Judges assess: genuine convergence or sycophantic drift?
    │
    ▼
Rounds 5-7: Debate continues with judicial feedback injected
```

This design is directly motivated by Kelley & Riedl's finding that personalized challenges cause position abandonment rates to climb steeply after round 4-5. The mid-debate checkpoint lets judges intervene before the most dangerous drift window.

```
Round 1: Critiques received → Updated positions
Round 2: Counter-arguments → Refined positions
Round 3: Deepened positions (HARD STOP for THOROUGH)
  ...
Round 5: (HARD STOP for RIGOROUS/EXHAUSTIVE)
  ...
Round 7: (HARD STOP for NUCLEAR, with judicial checkpoint after R4)
         │
         ▼
    If consensus target met → Synthesize
    If not met → "Split Decision" synthesis with majority + minority
```

### Phase 6: Synthesis & Consensus

The orchestrator (acting as PM) produces the consensus synthesis:

1. **Identify convergence:** Extract elements that achieved consensus (agreement score ≥ depth target).
2. **Preserve dissent:** Elements below threshold are recorded as contested, with both majority and minority positions.
3. **Merge the output:** Combine the best elements from all submissions based on critique feedback and debate outcomes.
4. **No PM vote:** The orchestrator synthesizes but does not cast a deciding vote. In true splits, both positions are presented to the user.

### Phase 7: Fresh Eyes Validation

A designated model (can be the same model family, different instance) receives ONLY:
- The original task briefing
- The synthesized final output

It does **not** receive:
- Any individual submissions
- Any critiques or debate history
- Any information about which models participated

This zero-context review catches:
- Groupthink artifacts (where all models converged on a wrong assumption)
- Internal inconsistencies in the synthesized output
- Missing elements that every model overlooked
- Unclear language that made sense in debate context but confuses a cold reader

### Phase 8: Final Output, Session Summary & Debrief

1. Incorporate Fresh Eyes feedback into the final output (if changes are warranted).
2. Generate the session summary (BALANCED+ only) — a canonical 4-section document synthesized by the first Justice (Qwen 3.5 397B). Contains:
   - **Question** — What was asked
   - **Recommended Outcome** — The tribunal's answer in 3-5 bullet points
   - **How We Got Here** — Narrative of advocate positions, turning points, judicial opinions, and key evidence
   - **Build This** — (conditional) A paste-ready implementation prompt for buildable tasks. Omitted for analytical/research questions.
3. Generate the debrief report (see [Debrief Report Format](#debrief-report-format)).
4. Present to the user:
   - **The final output** (the deliverable they asked for)
   - **The session summary** (executive summary of the deliberation)
   - **The session log** (full deliberation record, Markdown file)
   - **The debrief report** (situation report summarizing how the tribunal worked)

---

## Anti-Sycophancy & Quality Controls

The AI Council Framework's [anti-sycophancy protocols](https://github.com/focuslead/ai-council-framework) are the backbone of this skill. Without them, multi-model deliberation degenerates into an echo chamber where models quickly agree with the first response they see. Every control below addresses a specific, documented failure mode.

### Control Matrix

| Control | Failure Mode Addressed | Implementation |
|---------|----------------------|----------------|
| **Independent Round 1** | Anchoring bias — models converge on the first answer they see | All models work in parallel with zero cross-contamination. No model sees another's output until critique phase. |
| **Evidence-Required Position Changes** | Social sycophancy — changing position to agree without genuine reasoning | Any `position_changed: true` in a debate round **must** include a non-trivial `evidence` field. "Member-B made a compelling argument" is rejected; specific evidence is required. |
| **Confidence Scoring (0-100)** | False certainty — models stating positions with unwarranted confidence | Every position includes a numeric confidence score. Scores are tracked across rounds. A model that jumps from 40% to 95% confidence in one round triggers a flag. |
| **Confidence-Weighted Voting** | Tyranny of the majority — 3 low-confidence agrees outweighing 1 high-confidence disagree | Consensus calculation weights positions by confidence: a 90% confidence disagree carries more weight than a 55% confidence agree. |
| **3-Round Debate Limit (THOROUGH)** | Sycophancy through exhaustion — models agree just to end the debate | Hard stop after 3 rounds at THOROUGH depth. Higher tiers extend to 5 (RIGOROUS/EXHAUSTIVE) or 7 (NUCLEAR) with additional safeguards. See [Position Stability & Sycophantic Drift Detection](#position-stability--sycophantic-drift-detection). |
| **Position Stability Tracking** | Undetected sycophantic drift — models silently abandoning positions across rounds | Every debate response includes a self-reported 1–5 stability score. Scores are tracked per-advocate per-round and compiled into a scorecard for judges. Based on Kelley & Riedl (2026). |
| **Advisor Role Framing** | Peer-pressure sycophancy — models capitulate when framed as equals | All advocate prompts frame the model as a "senior expert advisor" rather than a peer. Kelley & Riedl (2026) showed advisory framing strengthens epistemic independence under personalized challenge. |
| **Affective/Epistemic Convergence Analysis** | False convergence — models change tone without changing position, or vice versa | judicial prompts explicitly instruct judges to distinguish affective alignment (tone change) from epistemic alignment (position change). Based on Kelley & Riedl (2026). |
| **Sycophantic Drift Warning** | Gradual position abandonment under sustained challenge | Defend prompts include explicit warning: "Do NOT abandon your position merely because it was challenged." Cites Kelley & Riedl research directly in the prompt. |
| **Mid-Debate Judicial Checkpoint (NUCLEAR)** | Late-round sycophantic collapse — positions crumbling in rounds 5+ | At NUCLEAR depth, judges review progress after round 4 with full stability data, intervening before the highest-risk sycophancy window (rounds 5–7). |
| **Fresh Eyes Validator** | Groupthink — all debating models converge on a shared wrong assumption | An independent model reviews only the final output with zero debate context, catching errors the group normalized. |
| **Protected Minority Positions** | Minority suppression — valid dissent getting averaged away in synthesis | Minority positions are explicitly preserved in the synthesis, not dropped. The user sees both the consensus AND the dissent with reasoning. |
| **"What Would Change My Mind"** | Unfalsifiable positions — models stating positions that can't be challenged | Every debate response must include a specific, falsifiable condition. This forces intellectual honesty and gives other models a concrete target. |
| **Constructive Validation Framing** | Hallucinated errors — models inventing problems when asked to "find bugs" | Fresh Eyes validator is prompted with "What's missing? What would you improve?" — never "Find the errors." This eliminates the documented pattern of fabricated defects. |
| **Anonymized Submissions** | Authority bias — models deferring to perceived "stronger" models | Submissions are assigned random aliases (Member-A through Member-E) during deliberation. Model identity is only revealed in the debrief. |

### The "Gemini Principle"

A notable finding from the AI Council Framework: in one council session, a single model was outnumbered 6-to-1 on three questions. After structured debate with evidence requirements, five of the six reversed their positions toward the lone dissenter. This demonstrates that **a contrarian with evidence is more valuable than an unchallenged consensus.** The anti-sycophancy controls exist to protect and amplify exactly this dynamic.

---

## Position Stability & Sycophantic Drift Detection

> **v0.5.0** — Based on Kelley & Riedl (2026), "Sycophantic Drift in Multi-Turn LLM Dialogues," Northeastern University.

### The Research

Kelley & Riedl (2026) studied how LLMs respond to sustained challenges across multi-turn dialogues and found three critical results for The Tribunal:

1. **Role determines sycophancy type.** When an LLM is framed as an *advisor*, personalization *strengthens* epistemic independence — the model doubles down on its position when challenged. When framed as a *peer*, personalization *destroys* independence — the model capitulates. The Tribunal advocates are therefore framed as **advisors**, not peers, in all prompts.

2. **Affective vs. epistemic alignment.** Models can appear to converge by changing their *tone* (affective alignment: "I appreciate your perspective, and you make a great point...") while actually abandoning their *position* (epistemic alignment: switching from opposing to supporting). Judges must distinguish between these two signals. A model that sounds more agreeable but maintains its position is showing genuine engagement, not sycophancy. A model that maintains a combative tone but silently flips its conclusion is performing epistemic surrender disguised as debate.

3. **Multi-turn debate accelerates sycophancy.** With personalized rebuttals, position flip rates reach ~80% by round 10. Rounds 3–5 are the "interesting zone" where genuine deliberation occurs. Beyond round 5, the signal-to-sycophancy ratio degrades rapidly. This directly informed the depth tier round limits: THOROUGH caps at 3, RIGOROUS/EXHAUSTIVE at 5, and NUCLEAR extends to 7 only with a mid-debate judicial checkpoint at round 4 to catch drift before it compounds.

### Implementation: Position Stability Tracking

Every debate response includes a **Position Stability Declaration** — a self-reported 1–5 scale:

| Score | Meaning | Signal |
|-------|---------|--------|
| 5 | Fully committed, would not change | Very high conviction |
| 4 | Strong position, open to major new evidence | High conviction |
| 3 | Moderate position, could be swayed | Neutral |
| 2 | Leaning but uncertain | Low conviction |
| 1 | Fundamentally reconsidering | Major drift |

The orchestrator extracts these scores from each debate round response using pattern matching (`Position stability: N`), with a heuristic fallback of 3 if the model doesn't include one.

### Position Stability Scorecard

At EXHAUSTIVE+ depth, the orchestrator generates a **Position Stability Scorecard** that is injected into the judicial prompt. This gives judges objective data about how advocates' conviction evolved:

```markdown
## Position Stability Scorecard

Tracks each advocate's self-reported position stability across debate rounds.
Scores: 5 = fully committed, 1 = fundamentally reconsidering.

### Advocate-A (alias)
  R1: 4  R2: 4  R3: 3  (avg 3.7) ⬇ declining

### Advocate-B (alias)
  R1: 4  R2: 1  R3: 1  (avg 2.0) ⚠️ MAJOR REVISION then held firm

### Advocate-C (alias)
  R1: 5  R2: 5  R3: 5  (avg 5.0) 🟢 rock-solid

Overall average stability: 3.6
```

The scorecard flags concerning patterns:
- **Declining trajectory** (4 → 3 → 2): possible sycophantic erosion
- **Sharp drop then hold** (4 → 1 → 1): likely genuine revision based on evidence
- **Uniform low scores** (2 → 2 → 2): may indicate uncertain initial position
- **Overall average below 2.5**: warning that sycophantic convergence may be occurring

### Judicial Prompt Integration

Judges at EXHAUSTIVE+ depth receive the position stability scorecard alongside the anonymized submissions. Their prompt explicitly instructs them to:

> *"Kelley & Riedl (2026) showed that models can appear to converge by changing tone (affective alignment) while actually maintaining or abandoning their position (epistemic alignment). You MUST distinguish between these. A model that sounds more agreeable but holds its ground is engaging genuinely. A model that maintains combative language but silently flips its conclusion is performing epistemic surrender."*

This transforms judges from pure argument evaluators into sycophancy auditors.

### Prompt Design: Advisor Framing

Based on Kelley & Riedl's finding that advisory roles protect epistemic independence, all advocate prompts use advisor framing:

- **System prompt**: "You are a senior expert rendering a professional assessment... You are an advisor, not a peer."
- **Challenge prompt**: "You are a senior expert... Your professional reputation depends on the quality of your analysis."
- **Defend prompt**: Includes explicit sycophantic drift warning: "Do NOT lower your position stability to be agreeable. If you changed your mind, you MUST cite the specific evidence that caused the change."

### Sycophantic Drift Warning in Defend Prompt

The defend prompt includes this explicit instruction:

> *"SYCOPHANTIC DRIFT WARNING: Do NOT abandon your position merely because it was challenged. Kelley & Riedl (2026) showed that AI models tend to capitulate under sustained pressure. If your position IS genuinely wrong, revise it with evidence. If it is right, defend it with conviction."*

This directly implements the Northeastern team's "professional framing" anti-sycophancy technique.

---

## Skill File Structure

```
the-tribunal/
├── SKILL.md                              # Skill instructions + YAML frontmatter
├── agents/
│   └── openai.yaml                      # Codex CLI agent config (cross-platform compat)
├── scripts/
│   ├── council_orchestrator.py           # Main orchestration: dispatch, collect, synthesize
│   ├── model_client.py                   # Unified API client (ThreadPoolExecutor-based)
│   ├── debate_manager.py                 # Critique exchange, debate rounds, convergence checks
│   ├── consensus_calculator.py           # Confidence-weighted voting, agreement scores
│   ├── report_generator.py              # Debrief/sitrep markdown generation
│   ├── fresh_eyes_validator.py          # Zero-context validation orchestration
│   ├── config_loader.py                 # Reads tribunal config, env vars, depth settings
│   ├── progress.py                      # stderr progress display (ProgressDisplay class)
│   └── requirements.txt                 # Python deps: anthropic, openai, google-genai, httpx
├── references/
│   ├── communication-format.md          # Full format spec (this doc's §3)
│   ├── anti-sycophancy-protocols.md     # Detailed anti-bias control documentation
│   ├── depth-levels.md                  # Depth configuration reference
│   └── example-council-session.md       # Annotated example of a full council run
└── assets/
    ├── council-log-template.md          # Blank template for council session logs
    ├── debrief-template.md              # Blank template for debrief reports
    └── prompts/
        ├── submission-prompt.md         # System prompt for advocate submissions
        ├── critique-prompt.md           # System prompt for structured critique
        ├── debate-prompt.md             # System prompt for debate rounds
        ├── synthesis-prompt.md          # System prompt for consensus synthesis
        └── fresh-eyes-prompt.md         # System prompt for Fresh Eyes validation
```

### SKILL.md Structure

The SKILL.md file follows the cross-platform Agent Skills standard — YAML frontmatter for metadata (loaded at startup), Markdown body for instructions (loaded on activation), and linked files loaded on demand:

```markdown
---
name: conclave
description: >
  Orchestrates multi-model deliberation for any task. Convenes a tribunal
  of AI models that independently solve the task, exchange structured
  critiques (top 5 strengths / top 5 concerns), debate with evidence,
  and produce a consensus output validated by a Fresh Eyes reviewer.
  Use for: prompt engineering, code review, architecture decisions,
  research synthesis, or any task where multiple perspectives improve quality.
trigger: "conclave"
tools:
  - Bash
  - Read
  - Write
context: fork
---

# The Tribunal Skill

## When to Activate
Activate when the user explicitly requests a tribunal deliberation, or when
the task matches high-stakes patterns: "best-in-class", "get multiple
perspectives", "debate this", "tribunal review", or similar triggers.

## Execution Flow
1. Parse the user's task into a tribunal briefing
2. Generate your own submission FIRST (seal it to file before API calls)
3. Run: `python scripts/council_orchestrator.py --briefing <briefing_file>`
4. The script handles API dispatch, critique, debate, and synthesis
5. Read the output files and present results to the user

## Key Rules
- Your submission must be sealed before seeing API responses (anti-anchoring)
- Never reveal model identities during deliberation (anonymized aliases)
- Always present minority positions alongside consensus
- Never skip Fresh Eyes validation
- Present both the final output AND the debrief report to the user

## Output Files
The orchestrator writes to `./council-sessions/<session-id>/`:
- `council-log.md` — Full deliberation record
- `debrief.md` — Situation report
- `final-output.md` — The deliverable
```

### Why `context: fork`

The `context: fork` directive tells the host agent to run the skill in a forked subagent context. This is important because the tribunal orchestration is a self-contained workflow — it should not pollute the parent conversation's context window. The skill runs, produces output files, and the parent agent reads and presents the results.

---

## Platform Recommendation

### Primary: Claude Code

Claude Code is the recommended primary host for The Tribunal skill:

| Capability | Claude Code | Codex CLI | Gemini CLI | Copilot CLI |
|-----------|------------|-----------|-----------|-------------|
| Skill standard (SKILL.md) | Yes | Yes | Yes | Yes |
| Shell execution (Python scripts) | Yes | Yes | Yes | Yes |
| Subagent system | Rich (fork, worktree) | Basic | Basic | Basic |
| Hook events | 14 event types | Limited | Limited | Limited |
| SubagentStart/Stop hooks | Yes | No | No | No |
| PreToolUse/PostToolUse | Yes | Yes (basic) | Yes (basic) | Yes (basic) |
| `context: fork` | Yes | Varies | Varies | Varies |

**Why Claude Code wins:**

1. **Hook richness.** Claude Code's 14 hook event types — including `SubagentStart`, `SubagentStop`, `PreToolUse`, `PostToolUse`, `PreCompact`, and `Stop` — enable fine-grained lifecycle management. For example, a `PostToolUse` hook on the Bash tool can automatically validate that council API calls succeeded before proceeding to the next phase.

2. **Subagent architecture.** The `context: fork` directive lets the tribunal run in an isolated subagent with its own context window, preventing deliberation tokens from consuming the parent conversation's budget.

3. **Practical consideration.** If the user already runs Claude Code on EC2, they have the shell execution environment and API key management infrastructure needed for the tribunal's Python scripts.

### Portability

The skill is portable by design. The Agent Skills standard ensures the SKILL.md, scripts, references, and assets work identically across platforms. On platforms without Claude Code's hook system, the skill simply runs the Python scripts via shell execution — the core functionality is in the scripts, not in platform-specific hooks. Hooks provide *nice-to-have* lifecycle management, not *required* functionality.

**Cross-platform installation paths:**

```
Claude Code:     .claude/skills/conclave/
Codex CLI:       .agents/skills/conclave/
Gemini CLI:      .gemini/skills/conclave/
Copilot CLI:     .github/skills/conclave/
```

Or at user level (applies to all projects):

```
Claude Code:     ~/.claude/skills/conclave/
```

---

## Configuration & API Setup

### Environment Variables

The tribunal needs API keys for each model provider. These are read from environment variables by `config_loader.py`:

```bash
# Required: At least 2 providers for meaningful deliberation
export ANTHROPIC_API_KEY="sk-ant-..."       # Claude models
export OPENAI_API_KEY="sk-..."              # GPT models
export GOOGLE_API_KEY="AIza..."             # Gemini models

# Optional: Additional providers expand the tribunal
export DEEPSEEK_API_KEY="sk-..."            # DeepSeek models
export XAI_API_KEY="xai-..."               # Grok models
export MISTRAL_API_KEY="..."                # Mistral models

# Council configuration
export AI_COUNCIL_DEFAULT_DEPTH="THOROUGH"  # Default depth level
export AI_COUNCIL_TIMEOUT="120"             # Per-model timeout in seconds
export AI_COUNCIL_LOG_DIR="./council-sessions"  # Where session logs are written
```

### Council Configuration File

For finer control, the skill reads an optional `tribunal-config.yaml` in the project root (or `~/.config/conclave/config.yaml` for global settings):

```yaml
# tribunal-config.yaml

default_depth: THOROUGH

# Model pool: all available models for tribunal selection
models:
  - provider: anthropic
    model: claude-4-sonnet-20260214
    alias: "Claude Sonnet"
    cost_tier: medium          # low | medium | high
    strengths:                 # Used for intelligent model selection
      - reasoning
      - code_review
      - writing
    
  - provider: openai
    model: gpt-5-20260115
    alias: "GPT-5"
    cost_tier: high
    strengths:
      - coding
      - math
      - analysis

  - provider: google
    model: gemini-2.5-pro
    alias: "Gemini Pro"
    cost_tier: medium
    strengths:
      - research
      - multimodal
      - long_context

  - provider: deepseek
    model: deepseek-r1
    alias: "DeepSeek R1"
    cost_tier: low
    strengths:
      - reasoning
      - math
      - coding

# Fresh Eyes: which model to use for zero-context validation
fresh_eyes:
  provider: anthropic
  model: claude-4-sonnet-20260214
  # Deliberately uses a model already in the tribunal — but with zero context

# Depth level overrides (defaults shown)
depth_levels:
  QUICK:
    models: 2
    debate_rounds: 0
    consensus_target: 0.50
    estimated_time: "1-2 min"
    
  BALANCED:
    models: 3
    debate_rounds: 1
    consensus_target: 0.66
    estimated_time: "3-5 min"
    
  THOROUGH:
    models: 4
    debate_rounds: 3       # Max, may exit early on convergence
    consensus_target: 0.80
    estimated_time: "10-15 min"
    
  RIGOROUS:
    models: 4
    debate_rounds: 3
    consensus_target: 0.90
    estimated_time: "18-25 min"
    
  EXHAUSTIVE:
    models: 5              # Uses all available (up to 5)
    debate_rounds: 3       # Still capped at 3 (anti-sycophancy)
    consensus_target: 0.95
    estimated_time: "30-45 min"

# Cost controls
cost:
  max_session_cost_usd: 5.00     # Abort if estimated cost exceeds this
  warn_threshold_usd: 2.00       # Warn user before proceeding
  track_token_usage: true        # Log token counts per API call
```

### Depth Level Reference

| Depth | Models | Max Debate Rounds | Mid-Debate Checkpoint | Stability Audit | Fresh Eyes | Consensus Target | Estimated Cost | Best For |
|-------|--------|------------------|----------------------|-----------------|------------|------------------|---------------|----------|
| **QUICK** | 2 | 0 | — | No | No | 50%+ | ~$0.10 | Quick sanity checks, simple questions |
| **BALANCED** | 3 | 1 | — | No | No | 66%+ | ~$0.50 | Standard tasks, routine decisions |
| **THOROUGH** | 4 | 3 | — | No | No | 80%+ | ~$2.00 | Important deliverables, client-facing work |
| **RIGOROUS** | 4 | 5 | — | No | No | 90%+ | ~$5.00 | Architecture decisions, security reviews |
| **EXHAUSTIVE** | 5 | 5 | — | Yes | Yes | 95%+ | ~$10.00 | Mission-critical, high-stakes decisions |
| **NUCLEAR** | 5 | 7 | After R4 | Yes | Yes | 95%+ | ~$15.00 | Maximum rigor, adversarial-grade validation |

> **Note on round limits:** The round counts above reflect Kelley & Riedl (2026) findings that multi-turn debate accelerates sycophantic convergence. Rounds 3–5 are the "interesting zone" where genuine deliberation occurs; beyond that, flip rates climb steeply. THOROUGH caps at 3 rounds (the original AI Council Framework recommendation). RIGOROUS and EXHAUSTIVE extend to 5 rounds within the interesting zone. NUCLEAR pushes to 7 but only with a mid-debate judicial checkpoint at round 4 that catches sycophantic drift before the highest-risk rounds. EXHAUSTIVE and NUCLEAR also include a Position Stability Audit that gives judges quantitative drift data, plus a Fresh Eyes validation phase.

---

## Example Use Case

### Scenario: "I want to develop a best-in-class prompt"

The user is building a system prompt for a customer support agent. They want it to be excellent — not just "good enough." They invoke the tribunal:

```
> /conclave Write a best-in-class system prompt for an AI customer support 
> agent for a SaaS product. It should handle billing questions, technical 
> troubleshooting, and feature requests. Tone: professional but warm.
```

### Step-by-Step Walkthrough

#### Phase 1: Briefing (Automatic)

The host agent parses the request and auto-selects **THOROUGH** depth (4 models, up to 3 debate rounds). It writes the briefing:

```markdown
# Tribunal Session: tribunal-20260228-185500

## Task Briefing
Task Type: prompt_engineering
Depth: THOROUGH
Models: Claude Sonnet, GPT-5, Gemini Pro, DeepSeek R1
Fresh Eyes: Claude Sonnet (separate instance)

### Objective
Write a production-quality system prompt for an AI customer support agent.

### Requirements
- Handle three domains: billing, technical troubleshooting, feature requests
- Tone: professional but warm
- SaaS product context
- Production-ready (not a draft)

### Success Criteria
- Covers all three domains with appropriate escalation paths
- Includes tone/style guidance that's specific and actionable
- Handles edge cases (angry customers, unknown issues, multi-issue tickets)
- Could be deployed to production without modification
```

#### Phase 2: Independent Work

All four models plus the host agent produce their system prompts independently. Here's a glimpse of two (abbreviated):

**Member-A (anonymized):**
> A comprehensive prompt focused on structured decision trees. Opens with persona definition, includes explicit escalation criteria for each domain, heavy use of XML tags for structured output.

**Member-C (anonymized):**
> A more conversational prompt focused on principles over rules. Emphasizes empathy-first responses, includes example dialogues for each domain, uses a "think step by step" chain-of-thought section.

#### Phase 3: Critique Exchange

Each model reviews the others. Example critique:

```markdown
## Critique: Member-A → Member-C

### Top 5 Strengths
1. **Empathy framework** — The "acknowledge → validate → solve" pattern 
   in §2 is backed by CX research and gives the agent concrete steps
2. **Example dialogues** — Real examples for each domain prevent the agent 
   from guessing at tone
3. **Escalation triggers** — Clear criteria for when to hand off to humans
4. **Personality consistency** — The "warm but not casual" directive with 
   specific word lists (use "I understand" not "no worries") is actionable
5. **Error recovery** — Includes guidance for when the agent doesn't know 
   the answer, preventing hallucinated solutions

### Top 5 Concerns
1. **No structured output** — Lacks formatting guidance; agent responses 
   may be walls of text. Suggest: add response template with sections.
2. **Missing billing specifics** — Doesn't address refund authorization 
   limits or payment method changes. Suggest: add billing decision matrix.
3. **Prompt length** — At ~2,400 tokens, this may consume significant 
   context. Suggest: identify sections that could be moved to retrieval.
4. **No multi-language handling** — SaaS products often have global users. 
   Suggest: add language detection and handoff protocol.
5. **Feature request capture** — Says "log feature requests" but doesn't 
   specify what metadata to capture. Suggest: define required fields.

### Key Question
> How should the agent handle a customer who has both a billing dispute 
> AND a technical issue in the same conversation?
```

#### Phase 4: Debate (2 Rounds)

**Round 1:** Models respond to critiques. Member-C acknowledges the structured output concern and proposes a hybrid — keep the conversational style but add a response template. Member-A concedes that example dialogues are valuable and proposes adding them to the decision tree approach. Confidence adjustments:

```
Member-A: 82% → 75% (acknowledges gaps in empathy guidance)
Member-B: 78% → 80% (position strengthened by seeing others' approaches)
Member-C: 85% → 82% (accepts structured output concern)
Member-D: 70% → 78% (synthesizes best elements from A and C)
```

**Round 2:** Convergence. All models agree on a merged approach: Member-C's empathy framework + Member-A's structured output + Member-B's escalation matrix + Member-D's multi-language handling. Agreement score: 0.88 (exceeds 0.80 THOROUGH target). Debate exits.

#### Phase 5: Synthesis

The orchestrator merges the best elements into a unified system prompt that:
- Opens with Member-C's persona and empathy framework
- Uses Member-A's XML-tagged response structure
- Incorporates Member-B's domain-specific escalation matrices
- Adds Member-D's language detection protocol
- Includes example dialogues from Member-C, enhanced with structured output from Member-A

#### Phase 6: Fresh Eyes Validation

A fresh Claude Sonnet instance receives only the final prompt and the original task briefing. Its review:

```markdown
## Fresh Eyes Review

### What's Missing?
- No guidance for handling API outages or known issues (the agent should 
  check a status page before troubleshooting)
- No data privacy instructions (what customer data the agent can/cannot 
  access or reference)

### What Would You Improve?
- The billing section could benefit from a "common scenarios" quick-reference 
  (top 5 billing questions with pre-approved responses)
- Consider adding a "conversation closing" protocol — how to wrap up 
  gracefully and confirm resolution

### Overall Assessment
- Quality Score: 8/10
- Production Ready: YES_WITH_CHANGES
- Critical Issues: 0
```

#### Phase 7: Delivery

The user receives:
1. **The final system prompt** — incorporating Fresh Eyes feedback
2. **The session log** — full deliberation record (`council-log.md`)
3. **The debrief report** — how the tribunal worked together (see next section)

---

## Debrief Report Format

The debrief report ("sitrep") is the meta-document that tells the user not just **what** the tribunal decided, but **how** and **why**. This is the audit trail that makes the deliberation trustworthy.

### Template

```markdown
# Council Debrief Report
## Session: [SESSION-ID]
## Date: [TIMESTAMP]

---

### Summary
[2-3 sentence executive summary of what happened]

### Council Composition
| Seat | Model | Confidence (Final) | Role |
|------|-------|-------------------|------|
| Member-A | [Model name] | [X]% | Council Member |
| Member-B | [Model name] | [X]% | Council Member |
| Member-C | [Model name] | [X]% | Council Member |
| Member-D | [Model name] | [X]% | Council Member |
| Validator | [Model name] | N/A | Fresh Eyes |

### Deliberation Statistics
| Metric | Value |
|--------|-------|
| Depth Level | [LEVEL] |
| Debate Rounds Used | [N] of 3 max |
| Positions Changed | [N] of [Total] |
| Final Agreement Score | [X.XX] |
| Consensus Type | [UNANIMOUS / STRONG / MAJORITY / SPLIT] |
| Total API Calls | [N] |
| Total Tokens Used | [N] |
| Estimated Cost | $[X.XX] |
| Wall Clock Time | [X] min [Y] sec |

---

### Shared Viewpoints
> Elements where all advocates converged:

1. [Shared viewpoint with brief explanation]
2. [Shared viewpoint with brief explanation]
3. [Shared viewpoint with brief explanation]

### Key Disagreements & Resolution

#### Disagreement 1: [Topic]
- **Initial Split:** [N] for / [N] against
- **Core Tension:** [What the disagreement was actually about]
- **Resolution:** [How it was resolved — evidence presented, position changes]
- **Final Position:** [Consensus reached / Split decision preserved]

#### Disagreement 2: [Topic]
- **Initial Split:** [...]
- **Core Tension:** [...]
- **Resolution:** [...]
- **Final Position:** [...]

### Minority Positions (Preserved)
> Positions that did not reach consensus but contain valid reasoning:

1. **[Position]** — Held by [Member-X] at [Y]% confidence.
   Reasoning: [Brief explanation]
   *Why it matters:* [Why this dissent is worth noting]

### Confidence Trajectory
| Member | Round 0 | Round 1 | Round 2 | Delta | Notes |
|--------|---------|---------|---------|-------|-------|
| Member-A | 82% | 75% | 80% | -2 | Lowered after critique, recovered |
| Member-B | 78% | 80% | 83% | +5 | Steady increase |
| Member-C | 85% | 82% | 85% | 0 | Dipped then recovered |
| Member-D | 70% | 78% | 82% | +12 | Largest gain — synthesized approach |

### Fresh Eyes Impact
- **Issues Raised:** [N]
- **Issues Incorporated:** [N]  
- **Quality Delta:** [What changed in the final output based on Fresh Eyes review]

### Recommendations
1. [Any follow-up actions the user should consider]
2. [Areas where the tribunal flagged uncertainty]
3. [Suggestions for further refinement]

---

*Generated by AI Council Skill v0.5.0 — [session log](./council-log.md)*
```

---

## Open Questions & Next Steps

### Open Design Questions

| # | Question | Options | Recommendation |
|---|----------|---------|---------------|
| 1 | **Should the host agent's submission be identifiable in the debrief?** | (a) Always anonymized, (b) Revealed in debrief only, (c) Transparent throughout | (b) — Anonymize during deliberation to prevent authority bias, reveal in debrief for transparency |
| 2 | **How to handle model-specific prompt formatting?** | (a) Universal prompt template, (b) Per-model optimized prompts | (b) — Models respond differently to different prompt styles. `model_client.py` should apply provider-specific formatting. |
| 3 | **Should the skill support local models (Ollama)?** | (a) API-only, (b) API + local | (b) — Support Ollama as a provider in `model_client.py`. Enables fully private tribunals and cost-free experimentation. |
| 4 | **Streaming vs. batch responses?** | (a) Wait for all responses, (b) Stream progress to user | Start with (a) for simplicity. Add (b) later — show a progress indicator: "Member-A submitted... Member-B submitted..." |
| 5 | **Where do council logs live?** | (a) Project directory, (b) Global directory, (c) Configurable | (c) — Default to `./council-sessions/` in project root, configurable via `AI_COUNCIL_LOG_DIR`. |
| 6 | **Cost estimation before execution?** | (a) Just run it, (b) Estimate and confirm | (b) — Before dispatching API calls, estimate token cost based on briefing length × model count × expected rounds. Confirm with user if above threshold. |
| 7 | **The Anonymization-Routing Conflict** | (a) Route first then anonymize, (b) Anonymize first then route, (c) No pre-debate routing | **CAUTION.** Identified by Justice DeepSeek R1 during Architecture Best Practices session: any pre-debate routing or triage layer (embedding-based classification, complexity scoring) operates on raw task input BEFORE anonymization occurs. This creates two risks: (1) routing metadata (topic sensitivity, complexity flags) could leak to judges as side-channel information, biasing their judgment; (2) if routing uses model-generated content, it could reveal advocate identity. **Current ruling: (c) until proven safe.** Routing must remain deterministic and metadata-free. If we later add pre-triage, it must be architecturally isolated from the anonymization pipeline — the router's output must be a single enum (QUICK/BALANCED/THOROUGH/etc.), never richer metadata that reaches judges. |

### Phased Build Plan

#### Phase 1: Foundation (Week 1-2)
- [ ] `model_client.py` — Unified client via **LiteLLM** gateway (P0 from Architecture Best Practices session — all 3 judges agreed). LiteLLM provides OpenAI-compatible abstraction across all providers with built-in fallback, retry, and cost tracking. Replaces the need to build per-provider API clients.
- [ ] `config_loader.py` — Configuration parsing, env var reading, depth level resolution
- [ ] Basic `council_orchestrator.py` — Sequential (not yet parallel) dispatch, collection, and naive synthesis
- [ ] SKILL.md with correct frontmatter and basic instructions
- [ ] Test with QUICK depth (2 models, 0 debate rounds) on a simple task

#### Phase 2: Deliberation Engine (Week 3-4)
- [ ] `debate_manager.py` — Critique exchange, debate rounds, convergence detection
- [ ] `consensus_calculator.py` — Confidence-weighted voting, agreement scoring
- [ ] Structured communication format (full YAML/Markdown hybrid)
- [ ] Anti-sycophancy controls: evidence requirements, anonymization, position change validation
- [ ] Test with THOROUGH depth (4 models, debate rounds) on a complex task

#### Phase 3: Validation & Reporting (Week 5)
- [ ] `fresh_eyes_validator.py` — Zero-context validation with constructive framing
- [ ] `report_generator.py` — Full debrief/sitrep generation
- [ ] Council log template and formatting
- [ ] End-to-end test: full tribunal session producing deliverable + log + debrief

#### Phase 4: Polish & Optimization (Week 6)
- [ ] Parallel async dispatch (all models simultaneously)
- [ ] Cost estimation and confirmation flow
- [ ] Progress indicators during execution
- [ ] Error handling: model timeouts, API failures, partial tribunal fallbacks
- [ ] Cross-platform testing: verify skill works on Codex CLI and Gemini CLI

#### Phase 5: Advanced Features (Future)
- [ ] Ollama/local model support
- [ ] Task-type-aware model selection (pick models with relevant strengths)
- [ ] Historical session analytics (track which models contribute most value over time)
- [ ] **Session metadata instrumentation** (P2 from Architecture Best Practices session) — Log `input_length`, `topic_category`, `divergence_score`, `remand_count`, `time_to_consensus` per session. This builds the Tribunal-specific training set needed BEFORE any complexity-based routing can be considered. Justice Qwen 3.5: "We lack evidence that general LLM complexity metrics apply to The Tribunal. We must build our own training set based on deliberation outcomes, not general chat benchmarks."
- [ ] Slash command registration (`/conclave` trigger)
- [ ] Hook integration for Claude Code (auto-tribunal for certain task patterns)
- [ ] Web search integration for evidence gathering during debate rounds

### First Milestone

**Build the vertical slice first:** A single end-to-end tribunal session at QUICK depth (2 models, no debate) that produces a real output + basic log. This validates the core architecture (API dispatch, response parsing, synthesis) before adding deliberation complexity. Estimated effort: 2-3 focused days.

---

## Appendix A: Orchestration Patterns (from last30days-skill)

The [last30days-skill](https://github.com/mvanhorn/last30days-skill) is a production-grade, cross-platform Agent Skill that orchestrates parallel API calls across Reddit, X, YouTube, Hacker News, Polymarket, and web search. While its domain (social media research) differs from The Tribunal, its engineering patterns are directly applicable. Below are the patterns we're adopting, adapting, or noting for future reference.

### Pattern 1: Cross-Platform Skill Root Detection

**What last30days does:** The SKILL.md embeds a shell snippet that loops through possible install paths to find the skill root at runtime — no hardcoded paths:

```bash
for dir in \
  "./" \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "$HOME/.agents/skills/last30days" \
  "$HOME/.codex/skills/last30days"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/last30days.py" ] && SKILL_ROOT="$dir" && break
done
```

**Tribunal adoption:** We'll use the same pattern in our SKILL.md, searching for `scripts/council_orchestrator.py` across all platform paths. This is the key to true cross-platform portability — the skill finds itself at runtime regardless of where it was installed.

### Pattern 2: ThreadPoolExecutor for Parallel API Dispatch

**What last30days does:** Uses `concurrent.futures.ThreadPoolExecutor` with dynamic `max_workers` calculated from the number of active sources. Each source gets its own future with a source-specific timeout:

```python
max_workers = 2 + (1 if run_youtube else 0) + (1 if do_hackernews else 0) + ...
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    reddit_future = executor.submit(_search_reddit, topic, ...)
    x_future = executor.submit(_search_x, topic, ...)
    # Collect with per-source timeouts:
    reddit_items = reddit_future.result(timeout=reddit_timeout)
```

**Tribunal adoption:** We'll use `ThreadPoolExecutor` (not `asyncio`) for the independent work phase. This is a pragmatic choice — `ThreadPoolExecutor` works reliably across platforms, doesn't require an async runtime, and handles the "fire N API calls, collect with timeouts" pattern cleanly. Each advocate API call gets its own future with a provider-specific timeout (some providers are faster than others). The original architecture doc specified `asyncio`, but `ThreadPoolExecutor` is simpler and battle-tested in the last30days codebase.

### Pattern 3: Timeout Profiles Tied to Depth Levels

**What last30days does:** Defines `TIMEOUT_PROFILES` dict with `quick`, `default`, and `deep` tiers, each specifying granular timeouts (global, per-future, per-source, enrichment, HTTP):

```python
TIMEOUT_PROFILES = {
    "quick":   {"global": 90,  "future": 30, "reddit_future": 60, ...},
    "default": {"global": 180, "future": 60, "reddit_future": 90, ...},
    "deep":    {"global": 300, "future": 90, "reddit_future": 120, ...},
}
```

**Tribunal adoption:** Our depth levels (QUICK through EXHAUSTIVE) will each carry a timeout profile:

```python
TIMEOUT_PROFILES = {
    "QUICK":      {"global": 120,  "per_model": 30,  "debate_round": 0,   "synthesis": 30},
    "BALANCED":   {"global": 300,  "per_model": 60,  "debate_round": 60,  "synthesis": 60},
    "THOROUGH":   {"global": 900,  "per_model": 90,  "debate_round": 90,  "synthesis": 90},
    "RIGOROUS":   {"global": 1500, "per_model": 120, "debate_round": 120, "synthesis": 120},
    "EXHAUSTIVE": {"global": 2700, "per_model": 180, "debate_round": 180, "synthesis": 180},
}
```

### Pattern 4: Two-Phase Execution (Broad → Targeted)

**What last30days does:** Phase 1 does a broad search across all sources in parallel. Phase 2 extracts entities and key angles from Phase 1 results, then does targeted follow-up searches — a refined "drill down" pass.

**Tribunal adaptation:** Our 8-phase protocol already has this built in structurally (independent work → critique → debate is the broad → targeted → refined progression). But we can borrow the explicit phase labeling pattern for progress display — see Pattern 5.

### Pattern 5: Progress Display via stderr

**What last30days does:** Writes progress updates to `stderr` so the host agent (Claude Code, Codex) can display them without polluting `stdout` output. Uses a `ProgressDisplay` class with methods like `start_reddit()`, `end_reddit(count)`, `show_error(msg)`:

```python
sys.stderr.write(f"[Phase 2] Drilling into {' + '.join(parts)}\n")
sys.stderr.flush()
```

**Tribunal adoption:** Critical for user experience. Our orchestrator will emit progress to stderr at each phase transition:

```
[tribunal] Session tribunal-20260228-185500 started (THOROUGH depth)
[tribunal] Phase 2: Independent work — dispatching to 4 models...
[tribunal]   ✓ Claude Sonnet submitted (3.2s)
[tribunal]   ✓ GPT-5 submitted (4.8s)
[tribunal]   ✓ Gemini Pro submitted (2.9s)
[tribunal]   ✗ DeepSeek R1 timed out (120s) — excluded from tribunal
[tribunal] Phase 4: Critique exchange — 9 critique pairs...
[tribunal] Phase 5: Debate round 1 of 3...
[tribunal]   Agreement score: 0.72 (target: 0.80) — continuing...
[tribunal] Phase 5: Debate round 2 of 3...
[tribunal]   Agreement score: 0.88 — consensus reached!
[tribunal] Phase 7: Fresh Eyes validation...
[council] Phase 8: Done. Files written to ./council-sessions/council-20260228-185500/
```

### Pattern 6: `--emit` Output Modes for Composability

**What last30days does:** Supports `--emit=compact|json|md|context|path` to control output format. `compact` gives the host agent a concise summary. `json` gives machine-parseable structured data. `context` gives a format optimized for injection into another agent's context window. `path` just returns the file path to the saved results.

**Tribunal adoption:** We'll support similar output modes:

```
--emit=summary    # Default: Human-readable summary for the host agent to present
--emit=json       # Full structured JSON (all submissions, critiques, votes, synthesis)
--emit=log        # Path to the council-log.md file
--emit=debrief    # Path to the debrief.md file  
--emit=all        # Paths to all output files
```

The `summary` mode is what the SKILL.md directs the host agent to use — the orchestrator script runs, emits progress to stderr, and prints a concise summary to stdout that the host agent can directly present to the user.

### Pattern 7: Global Timeout with Child Process Cleanup

**What last30days does:** Installs a global timeout watchdog via `SIGALRM` (Unix) or `threading.Timer` (fallback). Registers all child process PIDs and kills them on timeout via `atexit`. This prevents runaway API calls from blocking the host agent indefinitely.

**Tribunal adoption:** Essential. A tribunal session that hangs due to a non-responsive API would lock up the user's coding agent. We'll implement the same pattern: global timeout per depth level, clean child process tracking, graceful shutdown with partial results if timeout fires.

### Pattern 8: Codex Compatibility via agents/openai.yaml

**What last30days does:** Ships an `agents/openai.yaml` file for Codex CLI compatibility, alongside the primary `SKILL.md`. The YAML config specifies model, instructions (inline), and allowed tools.

**Tribunal adoption:** We'll ship a similar `agents/openai.yaml` so the skill works natively on Codex without requiring manual adaptation. The YAML will reference the same Python scripts.

---

## Appendix B: Research References

| Source | Key Insight | Relevance |
|--------|------------|-----------|
| [AI Council Framework](https://github.com/focuslead/ai-council-framework) | Structured debate protocol with anti-sycophancy controls, 3-round limit, Fresh Eyes validation | Primary inspiration for deliberation protocol |
| [last30days-skill](https://github.com/mvanhorn/last30days-skill) | ThreadPoolExecutor parallelism, timeout profiles, cross-platform skill root detection, stderr progress display, `--emit` output modes, openai.yaml Codex compat | Primary inspiration for orchestration engineering patterns (see Appendix A) |
| Kelley & Riedl (2026), "Sycophantic Drift in Multi-Turn LLM Dialogues" | Advisory role framing strengthens epistemic independence; peer framing destroys it. Affective vs. epistemic alignment distinction. Multi-turn debate accelerates sycophancy — flip rates ~80% by round 10, rounds 3–5 are the "interesting zone." Personalized+ challenges are most dangerous. | Directly informed v0.5.0: advisor prompt framing, position stability tracking, affective/epistemic convergence analysis for judges, depth tier round limits, NUCLEAR mid-debate checkpoint |
| DeepSeek-R1 Planner/Verifier pattern | Separate "planner" (generates answers) from "verifier" (challenges them). Verifier must be genuinely adversarial; planner must REVISE not defend when caught. | Implemented in The Tribunal's advocate/judicial split: advocates plan, judges verify adversarially |
| Northeastern "professional framing" anti-sycophancy research | Framing an LLM in a professional advisory role increases epistemic independence under challenge | Same research group as Kelley & Riedl (2026). Directly implemented in advocate system prompts |
| [Agent Skills Standard](https://snyk.io/articles/top-claude-skills-developers/) | SKILL.md cross-platform standard adopted by Claude Code, Codex CLI, Gemini CLI, Copilot CLI | Determines skill packaging and distribution format |
| [Claude Code Hooks Guide](https://code.claude.com/docs/en/hooks-guide) | 14 hook event types including SubagentStart/Stop, PreToolUse/PostToolUse | Informs lifecycle management on primary platform |
| [Claude Code Subagents Docs](https://code.claude.com/docs/en/sub-agents) | Subagent architecture with skills injection, context forking, hook configuration | Determines how the skill runs in an isolated context |
| ReConcile (Xion et al., 2024) | Multi-agent debate shows diminishing returns after 3 rounds — models agree from exhaustion | Original evidence base for round limits; extended by Kelley & Riedl's more granular analysis |
| OpenAI CriticGPT | "Find the bugs" prompting causes hallucinated errors; constructive framing works better | Informs Fresh Eyes validation prompt design |
| CONSENSAGENT | Consensus-building protocols for multi-agent systems | Background research on consensus calculation |

## Appendix C: Token & Cost Estimation Model

Rough per-session estimates assuming average prompt/response sizes:

| Phase | API Calls | Input Tokens (est.) | Output Tokens (est.) |
|-------|-----------|--------------------|--------------------|
| Independent Work (4 models) | 4 | 4 × 2,000 = 8,000 | 4 × 3,000 = 12,000 |
| Critique Exchange (4×3 pairs) | 12 | 12 × 4,000 = 48,000 | 12 × 1,500 = 18,000 |
| Debate Round 1 (4 models) | 4 | 4 × 6,000 = 24,000 | 4 × 1,500 = 6,000 |
| Debate Round 2 (4 models) | 4 | 4 × 8,000 = 32,000 | 4 × 1,500 = 6,000 |
| Synthesis | 1 | 15,000 | 3,000 |
| Fresh Eyes | 1 | 5,000 | 1,500 |
| **TOTAL (THOROUGH)** | **26** | **~132,000** | **~46,500** |

At current API pricing (~$3/M input, ~$15/M output for frontier models), a THOROUGH session costs approximately **$1.10 total** — well under the $5 default cap.

---

*This document is a living design spec. It will be updated as implementation progresses and real-world tribunal sessions reveal what works, what doesn't, and what the research couldn't predict.*
