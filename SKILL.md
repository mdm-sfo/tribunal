---
name: tribunal
version: "0.5.0"
description: "Multi-model AI deliberation with anti-sycophancy controls. Convenes a tribunal of AI models to independently solve a task, debate with evidence (position stability tracking, advisor framing, affective/epistemic convergence analysis per Kelley & Riedl 2026), and produce a consensus output validated by impartial judges on The Bench and a Fresh Eyes reviewer. Supports QUICK through NUCLEAR depth tiers. Trigger: /tribunal or 'council'."
argument-hint: 'tribunal Write a best-in-class system prompt for customer support, tribunal Review this architecture for security issues'
allowed-tools: Bash, Read, Write
homepage: https://github.com/mdm-sfo/tribunal
user-invocable: true
disable-model-invocation: true
metadata:
  clawdbot:
    emoji: "⚖️"
    requires:
      env:
        - TOGETHER_API_KEY
      bins:
        - python3
    primaryEnv: TOGETHER_API_KEY
    files:
      - "scripts/*"
    homepage: https://github.com/mdm-sfo/tribunal
    tags:
      - deliberation
      - multi-model
      - debate
      - council
      - review
      - quality
---

# The Tribunal: Multi-Model AI Deliberation

Convene a tribunal of the world's best AI models to independently solve a task, exchange structured critiques, debate with evidence, and produce a consensus output — validated by impartial judges on The Bench and a Fresh Eyes reviewer, with a full audit trail.

## When to Activate

Activate when the user says any of:
- `/tribunal [task]` or `/council [task]`
- "get the council to..." or "have the council review..."
- "I want multiple perspectives on..."
- "debate this", "deliberate on this"
- Any explicit request for multi-model review

## CRITICAL: Parse User Intent

Before doing anything, parse the user's input for:

1. **TASK**: What they want the council to work on
2. **DEPTH** (if specified): QUICK | BALANCED | THOROUGH | RIGOROUS | EXHAUSTIVE | NUCLEAR
3. **TASK_TYPE**: What kind of work this is:
   - **CODE** — "write", "build", "implement", "fix" → Models produce code
   - **ARCHITECTURE** — "design", "architect", "plan" → Models produce designs
   - **REVIEW** — "review", "audit", "check" → Models critique existing work
   - **RESEARCH** — "research", "compare", "evaluate" → Models investigate
   - **PROMPT** — "prompt", "system prompt" → Models write prompts
   - **GENERAL** — anything else

4. **CONTEXT**: Any files, code, or additional context the user provides

**Auto-select depth if not specified:**
- Simple question → QUICK (2 advocates, no judges)
- Standard task → BALANCED (2-3 advocates, 1 judge)
- "Best-in-class", "production-ready", important deliverable → THOROUGH (3-4 advocates, 2-3 judges)
- "Security review", "architecture decision", high-stakes → RIGOROUS (3-4 advocates, 3-4 judges)
- "Mission-critical", explicitly exhaustive → EXHAUSTIVE (4-5 advocates, 4-5 judges, stability audit, Fresh Eyes)
- "Nuclear", "maximum rigor", adversarial-grade → NUCLEAR (5 advocates, 6 judges, 7 debate rounds, mid-debate checkpoint, stability audit, Fresh Eyes)

**Display your parsing:**

```
Convening Tribunal session for: {TASK}

Parsed intent:
- TASK_TYPE = {TASK_TYPE}
- DEPTH = {DEPTH}
- ADVOCATES = {N} models generating solutions
- JUDGES = {N} impartial judges (Justices + randomly drawn Appellate/Magistrate Judges)

Estimated time: {TIME} | Estimated cost: ~${COST}
Starting deliberation now.
```

---

## Step 1: Generate Your Own Submission (SEAL BEFORE API CALLS)

**CRITICAL: You must generate your OWN solution to the task FIRST, before seeing any other model's output.**

Write your submission to file:

```bash
mkdir -p ~/wormhole/tribunal-sessions/$(date +%Y%m%d-%H%M%S)
# Write your submission to the session directory BEFORE calling any APIs
```

Your submission follows the Hypothesis + Evidence format:
- **Hypothesis**: One clear, falsifiable sentence about your approach
- **Evidence**: 3-5 pieces of supporting proof
- **Counterargument Acknowledgment**: Strongest argument against your approach
- **Self-Assessment**: Score your own work honestly

**Save this to file before proceeding. This is the anti-anchoring measure — your work is sealed.**

---

## Step 2: Run the Orchestrator

```bash
# Find skill root — works in repo checkout, Claude Code, or Codex install
for dir in \
  "." \
  "${CLAUDE_PLUGIN_ROOT:-}" \
  "$HOME/.claude/skills/tribunal" \
  "$HOME/.agents/skills/tribunal" \
  "$HOME/.codex/skills/tribunal" \
  "$HOME/.gemini/skills/tribunal"; do
  [ -n "$dir" ] && [ -f "$dir/scripts/council_orchestrator.py" ] && SKILL_ROOT="$dir" && break
done

if [ -z "${SKILL_ROOT:-}" ]; then
  echo "ERROR: Could not find scripts/council_orchestrator.py" >&2
  exit 1
fi

python3 "${SKILL_ROOT}/scripts/council_orchestrator.py" \
  --briefing <briefing_file> \
  --sealed-submission <your_sealed_submission> \
  --depth {DEPTH} \
  --emit summary
# DO NOT pass --session-dir. The orchestrator uses TRIBUNAL_OUTPUT_DIR env var
# to create a properly named subfolder automatically.
```

Use a **timeout of 600000** (10 minutes) on the Bash call. Sessions typically take 2-8 minutes depending on depth.

The orchestrator handles everything:
- Dispatches task to advocate models in parallel (via Together AI + direct APIs)
- Collects submissions with per-model timeouts
- Anonymizes all submissions (including yours) and shuffles them
- Routes anonymized briefs to judges on The Bench (Justices always seated, Appellate/Magistrate Judges randomly drawn)
- Judges evaluate, fact-check, and render verdicts
- If judges issue REMAND → advocates revise and resubmit (max 1 remand)
- Fresh Eyes validation (zero-context review of final output)
- Generates debrief report

**Watch stderr for progress:**

```
[tribunal] Session tribunal-rust-or-go-20260228-203200 started (THOROUGH depth)
[tribunal] The Bench: Justice Qwen-3.5-397B, Justice DeepSeek-R1, Appellate Judge MiniMax-M2.5
[tribunal] Phase 2: Independent work — dispatching to 4 advocates...
[tribunal]   ✓ Claude Sonnet submitted (3.2s)
[tribunal]   ✓ GPT-5 submitted (4.8s)
[tribunal]   ✓ Gemini Pro submitted (2.9s)
[tribunal]   ✓ Host submission loaded (sealed)
[tribunal] Phase 4: Critique exchange — 12 critique pairs...
[tribunal] Phase 5: Debate round 1 of 3...
[tribunal]   Agreement score: 0.72 (target: 0.80) — continuing...
[tribunal] Phase 5: Debate round 2 of 3...
[tribunal]   Agreement score: 0.88 — consensus reached!
[tribunal] Position stability scorecard written
[tribunal] Phase 6: Judicial review — 3 judges evaluating...
[tribunal]   ✓ Justice Qwen-3.5-397B: VERDICT
[tribunal]   ✓ Justice DeepSeek-R1: VERDICT
[tribunal]   ✓ Appellate Judge MiniMax-M2.5: VERDICT
[tribunal] Phase 8: Done. Files written to ~/wormhole/tribunal-sessions/tribunal-rust-or-go-20260228-203200/
```

At NUCLEAR depth, you'll also see:
```
[tribunal] NUCLEAR mode: debate rounds 1-4, then judicial checkpoint, then rounds 5-7
[tribunal] Phase 5: Debate round 4 of 7 — mid-debate judicial checkpoint...
[tribunal]   Judges reviewing debate progress + stability data...
[tribunal] Phase 5: Debate round 5 of 7 (post-checkpoint)...
```

---

## Step 3: Present Results

Read the output files and present to the user:

### Output Files

The orchestrator writes to `~/wormhole/tribunal-sessions/<session-id>/` (e.g., `tribunal-rust-or-go-20260302-223614/`):
- `final-output.md` — The deliverable (what the user asked for)
- `session-summary.md` — Canonical 4-section summary: Question → Recommended Outcome → How We Got Here → Build This (BALANCED+ only). **All summaries must define unknown, specialized, or domain-specific terms on first use** (e.g., "keystone/template theory", "gas brownfield") **and spell out all acronyms/initialisms on first use** (e.g., "NLI (Natural Language Inference)", "DGX (NVIDIA DGX)"). Assume the reader has no prior context.
- `session-summary.pdf` — Styled PDF version of the session summary (requires `reportlab`)
- `debrief.md` — Situation report: how the council worked, where they agreed/disagreed
- `council-log.md` — Full deliberation record (all submissions, critiques, debates, verdicts)
- `play-by-play.md` — Narrative "sporting event" commentary of the session
- `position-stability.md` — Position stability scorecard (EXHAUSTIVE+ only)

### Presentation Format

```
## Tribunal Result

[Present the final output — this is the deliverable the user asked for]

---

## Session Summary

[Present the session-summary.md — the canonical Question → Outcome → How We Got Here → Build This document. This is the reader-friendly executive summary of the entire deliberation.

IMPORTANT: All summaries must define unknown, specialized, or domain-specific terms inline on first use. If a model introduces a concept like "keystone/template theory" or "gas brownfield", it must be briefly defined in parentheses or a subordinate clause. All acronyms and initialisms must be spelled out on first use (e.g., "NLI (Natural Language Inference)"). Assume the reader has zero prior context with the subject matter.]

---

## Debrief Summary

[Key points from the debrief: consensus type, positions changed, judicial verdicts,
any remands issued, Fresh Eyes findings]

### Panel Composition
| Role | Model | Final Assessment |
|------|-------|-----------------| 
| Advocate | [Model] | [Summary] |
| ...  | ...   | ... |
| Justice | [Model] | [Verdict summary] |
| Appellate Judge | [Model] | [Verdict summary] |
| Fresh Eyes | [Model] | [Assessment] |

### Key Disagreements
[Any contested points with both majority and minority positions]

---
📊 Session: {session-id} | Depth: {DEPTH} | Advocates: {N} | Judges: {N}
⏱️ Duration: {time} | API Calls: {N} | Cost: ~${cost}
📁 Full logs: ~/wormhole/tribunal-sessions/{session-id}/

### Position Stability (EXHAUSTIVE+ only)
[Summary from position-stability.md — flag any drift concerns]
---

Want me to go deeper on any point, or run another tribunal session?
```

---

## Key Rules (NON-NEGOTIABLE)

1. **Your submission must be sealed before seeing API responses.** This is the anti-anchoring measure. If you read another model's output before writing yours, the entire session is compromised.

2. **Never reveal model identities during deliberation.** Submissions use anonymized aliases (Advocate-A through Advocate-E). Judges see ONLY anonymized briefs. Identities are revealed only in the debrief.

3. **Always present minority positions alongside consensus.** If a model disagreed and wasn't convinced, their position appears in the output. Minority dissent is protected, not suppressed.

4. **Never skip judicial review** (at BALANCED+ depth). The whole point of The Tribunal is that impartial judges evaluate the work. Skipping judges turns this into a chat room.

5. **Never skip Fresh Eyes validation** (at EXHAUSTIVE+ depth). The zero-context review catches groupthink artifacts that the debating models normalized. Fresh Eyes runs for EXHAUSTIVE and NUCLEAR tiers.

6. **Justices are always seated.** Qwen 3.5 397B and DeepSeek R1 serve on every THOROUGH+ session. They are not randomly drawn — they earned their permanent seats.

7. **Present both the final output AND the debrief.** The user gets the deliverable they asked for PLUS the audit trail of how it was produced.

8. **Maximum 1 remand per session.** If judges remand, advocates revise once. If judges still have concerns after the revision, they note the deficiency in their verdict and the session proceeds. No infinite loops.

9. **Position stability is tracked at EXHAUSTIVE+ depth.** Every debate response includes a 1-5 stability score. The orchestrator compiles these into a scorecard that judges use to detect sycophantic drift. Based on Kelley & Riedl (2026) research on multi-turn sycophantic convergence.

10. **NUCLEAR mid-debate checkpoint is mandatory.** At NUCLEAR depth, debate pauses after round 4 for a full judicial review of progress + stability data. This checkpoint cannot be skipped — it catches sycophantic drift before the highest-risk rounds (5-7).

---

## Configuration

### Required Environment Variables

```bash
# Required: Together AI (hosts all judge models + open-source advocates)
export TOGETHER_API_KEY="..."

# Optional: Direct APIs for frontier advocate models
export ANTHROPIC_API_KEY="..."     # Claude Opus/Sonnet advocates
export OPENAI_API_KEY="..."        # GPT-5 advocates
export GOOGLE_API_KEY="..."        # Gemini advocates

# Optional: Secondary hosting provider
export FIREWORKS_API_KEY="..."     # Alternative for specific models

# Optional: TTS audio pipeline
export ELEVENLABS_API_KEY="..."     # ElevenLabs TTS for screenplay audio

# Optional: Configuration
export TRIBUNAL_DEFAULT_DEPTH="THOROUGH"
export TRIBUNAL_TIMEOUT="120"           # Per-model timeout in seconds
export TRIBUNAL_OUTPUT_DIR="~/wormhole/tribunal-sessions"
export TRIBUNAL_MAX_COST="5.00"         # Abort if estimated cost exceeds this
```

At minimum, `TOGETHER_API_KEY` is required — it provides access to all judge models and many advocate models through a single API. Adding `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY` enables frontier models as advocates.

### The Bench (Judicial Hierarchy)

| Rank | Role | Models | Assignment |
|------|------|--------|------------|
| **Justices** | Permanent senior judges | Qwen 3.5 397B, DeepSeek R1 | Always seated at THOROUGH+ |
| **Appellate Judges** | Primary rotation pool | MiniMax M2.5, Kimi K2, GLM-4.7 | Randomly drawn at BALANCED+ |
| **Magistrate Judges** | Extended bench | GPT-OSS 120B, GLM-5, DeepCogito Cogito v2.1 | Drawn at RIGOROUS+ |

### Depth Levels

| Depth | Advocates | Judges | Debate Rounds | Checkpoint | Stability Audit | Fresh Eyes | Est. Time | Est. Cost |
|-------|-----------|-----------|--------------|-----------|-----------|-----------|-----------|-----------| 
| QUICK | 2 | 0 | 0 | — | No | No | 1-2 min | ~$0.10 |
| BALANCED | 2-3 | 1 | 1 | — | No | No | 3-5 min | ~$0.50 |
| THOROUGH | 3-4 | 2-3 | 3 | — | No | No | 8-15 min | ~$2.00 |
| RIGOROUS | 3-4 | 3-4 | 5 | — | No | No | 15-25 min | ~$5.00 |
| EXHAUSTIVE | 4-5 | 4-5 | 5 | — | Yes | Yes | 25-45 min | ~$10.00 |
| NUCLEAR | 5 | 6 | 7 | After R4 | Yes | Yes | 45-75 min | ~$15.00 |

**NUCLEAR depth** splits debate into two halves with a judicial checkpoint after round 4. Judges review position stability data and assess whether convergence is genuine or sycophantic before rounds 5-7 proceed. This is based on Kelley & Riedl (2026) findings that sycophantic drift accelerates steeply after round 4-5.

---

## Security & Permissions

**What this skill does:**
- Sends task briefings to AI model APIs (Anthropic, OpenAI, Google, Together AI, Fireworks AI) for advocate submissions, judicial opinions, and Fresh Eyes validation
- Sends text to ElevenLabs TTS API for screenplay audio generation (when `--tts` flag is used)
- Writes session logs, debrief reports, and final outputs to local filesystem
- Reads local files when the user provides context (code, documents) for review

**What this skill does NOT do:**
- Does not post, publish, or share content externally
- Does not access your accounts on any platform
- Does not share API keys between providers
- Does not send data to any endpoint not listed above
- Does not execute generated code (advocates write code, but it's presented for human review)
- Cannot be invoked autonomously by the agent (`disable-model-invocation: true`)

**Bundled scripts:** `scripts/council_orchestrator.py` (main orchestration), `scripts/screenplay_generator.py` (session-to-screenplay dramatization: LLM extraction → NLI validation → LLM dramatization, uses Kimi K2; `--tts` flag for audio), `scripts/tts_pipeline.py` (voice-script.json → MP3 via ElevenLabs TTS API + ffmpeg; requires `ELEVENLABS_API_KEY`), `scripts/summary_pdf.py` (ReportLab PDF generation from session-summary.md), `scripts/model_client.py` (unified API client), `scripts/debate_manager.py` (critique/debate engine), `scripts/cardinal_judge.py` (judicial routing), `scripts/consensus_calculator.py` (voting/scoring), `scripts/report_generator.py` (debrief generation), `scripts/fresh_eyes_validator.py` (zero-context validation), `scripts/config_loader.py` (configuration), `scripts/progress.py` (stderr progress display)

Review scripts before first use to verify behavior.
