# The Tribunal

**Multiple AI models debate a question until only the truth survives!**

The Tribunal is a multi-model deliberation engine.
Instead of asking one model and trusting whatever comes back, it assigns the question to a panel of independent AI models (advocates) who argue, cross-examine, and debate — then routes the full transcript of their debate to other impartial judges (other AI models) who render a verdict.

The orchestrator is deterministic Python. It dispatches, anonymizes, and routes, but never opinionates.

<p align="center">
  <img src="screenshots/CleanShot 2026-03-19 at 05.42.48@2x.png" alt="Demo" width="600">
</p>

## How It Works

The Tribunal is a structured multi-model deliberation system. Instead of asking one AI model a question and trusting its answer, the Tribunal convenes a panel of independent models, forces them to argue, and subjects their conclusions to judicial review. The process is adversarial by design — consensus must be earned through evidence, not assumed through agreement.

**Advocates** are independent AI models (e.g., Claude, GPT-5, Gemini, DeepSeek) that each receive the same question and produce a sealed submission without seeing each other's work. They are anonymized to prevent brand-bias from influencing the judges. Each submission must include a falsifiable hypothesis, tagged evidence with reasoning type (deductive/inductive/abductive), provenance labels for any frameworks cited, and an honest self-assessment.

**Challenges** follow submissions. Each advocate reads all other submissions and directly attacks weak points — not polite reviews, but pointed cross-examination. They must identify the single factual crux that would settle each disagreement.

**Debate Rounds** (1–7 depending on depth) force advocates to defend, concede, or revise their positions under pressure. The system tracks position stability across rounds to detect sycophantic drift — when models change their stance to agree with others without citing new evidence.

**Judges** are separate models that never participated as advocates. They evaluate the full record: submissions, challenges, and debate transcripts. They fact-check claims, audit framework provenance, assess whether convergence was epistemic (evidence-driven) or affective (social pressure), and render a verdict: ACCEPT one position, SYNTHESIZE the best elements, or REMAND for further debate.

**Depth levels** control rigor:
- **T1/Standard Review** — 1 debate round, 1 judge
- **T2/Deep Review** — 3 rounds, 3 judges
- **T3/Full Panel** — 5 rounds, full panel
- **T6/Red Team** — 7 rounds + mid-debate judicial checkpoint

The system is deterministic code — it cannot be sycophantic. It dispatches prompts, collects responses, anonymizes identities, and enforces the adversarial structure. The models argue; the code referees.

## Why

Large language models are confident, articulate, and often wrong. A single model call gives you one perspective with no adversarial pressure. The Tribunal applies the oldest reliability mechanism humans have — structured argument — to AI output:

- **Advocates** receive the question independently. No anchoring, no groupthink.
- **Challenge rounds** force advocates to directly confront each other's claims.
- **Debate rounds** require advocates to DEFEND, CONCEDE, or REVISE under pressure. Position changes are tracked.
- **Judges** (who never advocated) evaluate the transcript and render a verdict: ACCEPT, SYNTHESIZE, or REMAND.
- **Fresh Eyes** — a model that never saw any of it reviews the final output cold.
- **Dissenting opinions** preserve minority positions backed by evidence.

The result is a structured session with full provenance: who said what, who changed their mind, and why.

## Quick Start

```bash
git clone https://github.com/mdm-sfo/tribunal.git
cd tribunal
pip install -r requirements.txt

# Set your API key (Together AI is the only requirement)
export TOGETHER_API_KEY="your_key_here"

# Write a question
echo "Should we use Rust or Go for our new CLI tool?" > briefing.md

# Run it
python3 scripts/council_orchestrator.py --briefing briefing.md --depth T3
```

Output lands in `./conclave-sessions/`. Each session is a directory with the briefing, all submissions, debate transcripts, judicial opinions, and a structured summary.

## Depth Levels

Depth controls how many models argue, how many rounds of debate occur, and how large the judicial panel is.

| Depth | Name | Advocates | Debate Rounds | Judges | Est. Cost |
|-------|------|-----------|---------------|--------|-----------|
| **T1** | Standard Review | 4 | 1 | 1 Bishop | ~$0.50 |
| **T2** | Deep Review | 5 | 3 | 2 Bishops + 1 Priest | ~$2.00 |
| **T3** | Full Panel | 5 | 5 | 2 Bishops + 1 Priest + 1 Deacon | ~$5.00 |
| **T6** | Red Team | 6 | 7 | Full bench + mid-debate checkpoint + Fresh Eyes | ~$15.00 |

## Feature Tiers

The Tribunal works with just a Together AI key. Additional features unlock as you add more keys:

| Tier | What You Need | What You Get |
|------|---------------|--------------|
| **Core** | `TOGETHER_API_KEY` | Full deliberation engine with open-weight advocates (DeepSeek V3, Qwen, Kimi K2, Llama 4) and all judge roles |
| **Enhanced** | + `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` | Frontier advocates (Claude Sonnet, GPT-5, Gemini 2.5 Pro). Each key adds one. Any combination works. |
| **Search** | + `PERPLEXITY_API_KEY` | Perplexity Sonar Pro advocate with live web search grounding |
| **Audio** | + `ELEVENLABS_API_KEY` + `ffmpeg` | Dramatized podcast-style MP3 of the deliberation |
| **PDF** | + `reportlab` (pip) | Polished PDF of the session summary |
| **NLI** | + GPU host running `nli_server.py` | DeBERTa-v3-large validates advocate claims via Natural Language Inference |

## Model Roster

### Advocates

| Model | Provider | Notes |
|-------|----------|-------|
| Claude Sonnet | Anthropic | Requires `ANTHROPIC_API_KEY` |
| GPT-5 | OpenAI | Requires `OPENAI_API_KEY`. Reasoning model. |
| Gemini 2.5 Pro | Google | Requires `GOOGLE_API_KEY`. Has Google Search grounding. |
| DeepSeek V3 | Together AI | Included with core tier |
| Perplexity Sonar Pro | Perplexity AI | Requires `PERPLEXITY_API_KEY`. Live web search. |

When fewer frontier keys are available than the depth requires, the panel is filled from an open-weight backfill pool: Qwen 3 235B, MiniMax M1, Kimi K2, Llama 4 Maverick — all via Together AI.

### Judges

All judges run on Together AI or Cerebras. No additional keys required beyond `TOGETHER_API_KEY`. Adding `CEREBRAS_API_KEY` and `MISTRAL_API_KEY` expands the available bench.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TOGETHER_API_KEY` | **Yes** | Together AI — judges + open-weight advocates |
| `ANTHROPIC_API_KEY` | No | Claude Sonnet advocate |
| `OPENAI_API_KEY` | No | GPT-5 advocate |
| `GOOGLE_API_KEY` | No | Gemini 2.5 Pro advocate |
| `PERPLEXITY_API_KEY` | No | Perplexity Sonar Pro advocate (web search) |
| `CEREBRAS_API_KEY` | No | Cerebras-hosted judges (Qwen 3 235B, GPT-OSS 120B) |
| `MISTRAL_API_KEY` | No | Mistral Large judge |
| `FIREWORKS_API_KEY` | No | Backup model routing |
| `ELEVENLABS_API_KEY` | No | TTS audio generation |
| `TRIBUNAL_OUTPUT_DIR` | No | Session output directory (default: `./conclave-sessions`) |
| `TRIBUNAL_NLI_URL` | No | NLI validation server (e.g., `http://gpu-host:8787`) |
| `CONCLAVE_DEFAULT_DEPTH` | No | Default depth (default: `T1`) |
| `CONCLAVE_MAX_COST` | No | Max cost per session in USD (default: `5.00`) |

See `.env.example` for a complete template.

## Audio Generation

The screenplay pipeline generates a dramatized podcast-style MP3 of the deliberation using ElevenLabs voices.

```bash
# Requires: ELEVENLABS_API_KEY and ffmpeg
python3 scripts/screenplay_generator.py \
    --session-dir ./conclave-sessions/your-session/ --tts
```

## NLI Validation

DeBERTa-v3-large validates advocate claims against source text. Requires any NVIDIA GPU with 4GB+ VRAM.

```bash
# On your GPU host:
bash setup_nli_server.sh

# Then point the orchestrator at it:
export TRIBUNAL_NLI_URL=http://your-gpu-host:8787
```

Falls back to rule-based validation (number/entity matching) if no GPU server is configured.

## Output Structure

Each session produces a directory:

```
tribunal-your-topic-20260310-223614/
├── briefing.md                  # Original question
├── submissions/                 # Advocate initial arguments
├── deliberation/                # Cross-examination + debate rounds
├── judicial/                    # Judge opinions + verdicts
├── narrative/                   # Play-by-play + screenplay + audio
├── session-summary.md           # Structured summary
├── session-summary.pdf          # PDF version (if reportlab installed)
├── council-log.json             # Machine-readable session log
├── position-stability.md        # Sycophancy audit scorecard
├── debrief.md                   # Panel composition + identity reveals
└── meta/                        # Alias maps, cost tracking
```

## Architecture

```
scripts/
  council_orchestrator.py   — Core state machine (deterministic, not a model)
  config_loader.py          — Model roster, depth levels, env var resolution
  model_client.py           — LiteLLM wrapper: fan_out, fan_out_multi, call_model
  data_room_enricher.py     — Live market data + web search enrichment
  screenplay_generator.py   — Three-pass pipeline: extraction → validation → dramatization
  summary_pdf.py            — ReportLab PDF generation
  tts_pipeline.py           — ElevenLabs TTS: voice casting + ffmpeg stitching
  nli_server.py             — FastAPI NLI server (DeBERTa-v3-large on GPU)
  progress.py               — Terminal output formatting
```

## Design Principles

1. **The orchestrator is code, not a model.** It can't be sycophantic.
2. **Every claim must cite reasoning type** — deductive, inductive, or abductive — and specific evidence.
3. **Suspicious unanimity is a defect**, not a feature. Judges treat it accordingly.
4. **Position stability tracking** catches sycophantic drift: models changing positions under social pressure without new evidence.
5. **Dissenting opinions survive to final output** when backed by evidence.
6. **Maximum 1 remand per session** — judges can send it back once, not forever.
7. **Uncertainty must survive.** Papering over disagreement is worse than admitting it.

## Installation

See **[docs/INSTALL.md](docs/INSTALL.md)** for detailed instructions covering macOS, Ubuntu, Amazon Linux (EC2), and aarch64 (DGX Spark).

## License

Apache 2.0
