# The Tribunal

A multi-model deliberation engine that makes AI models argue with each other before reaching a conclusion. Models serve as advocates (who argue), judges (who evaluate), and a Fresh Eyes reviewer (who sanity-checks the final output). The orchestrator is deterministic Python — it dispatches, anonymizes, and routes, but never opinionates.

The premise: if you want a reliable answer from AI, don't ask one model — make several models argue about it, challenge each other's evidence, and have impartial judges evaluate who's right.

## How It Works

```
Briefing → Advocates argue independently → Cross-examination → Adversarial debate
    → Judicial review → (optional) Fresh Eyes → Session summary + play-by-play
```

1. **Advocates** receive the question independently and submit structured arguments (hypothesis + evidence + self-assessment)
2. **Challenge round** — each advocate reads all other submissions and issues direct, pointed challenges
3. **Debate rounds** — advocates must DEFEND, CONCEDE, or REVISE for each challenge. Position stability is tracked.
4. **Dissenting opinions** — advocates who held their ground throughout debate but lost the verdict can issue a formal dissent for the record
5. **The Bench** — impartial judges (Justices, Appellate Judges, Magistrate Judges) evaluate the full transcript and render a verdict: ACCEPT, SYNTHESIZE, or REMAND
6. **Fresh Eyes** — a model that never saw the debate reviews the final output cold
7. **Session summary** — structured output: Question → Recommended Outcome → How We Got Here → Build This

## Feature Tiers

The Tribunal works with just a Together AI key. Additional features unlock as you add more keys:

| Tier | What You Need | What You Get |
|------|---------------|--------------|
| **Core** | `TOGETHER_API_KEY` | Full deliberation engine — advocates, debate, judges, session summary, play-by-play. Uses open-weight models (DeepSeek V3, Qwen, Kimi K2, Llama 4) for advocates and all judge roles. |
| **Enhanced** | + `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` | Frontier model advocates (Claude Sonnet, GPT-5, Gemini 2.5 Pro) alongside open-weight models. Stronger panels, more diverse reasoning. Add any combination — each key adds one frontier advocate. |
| **Audio** | + `ELEVENLABS_API_KEY` + `ffmpeg` | Dramatized podcast-style MP3 of the deliberation. Screenplay generation + TTS voice casting + ffmpeg stitching. |
| **PDF** | + `reportlab` (pip install) | Polished academic-style PDF of the session summary. Auto-generated alongside the markdown. |
| **NLI Validation** | + GPU host running `nli_server.py` | DeBERTa-v3-large validates advocate claims against source text via Natural Language Inference. Without this, uses rule-based validation (number/entity matching) — still functional, just less precise. |

> **Minimum viable setup**: `pip install -r requirements.txt`, set `TOGETHER_API_KEY`, run. Everything else is optional.

## Depth Levels

| Depth | Advocates | Debate Rounds | Judges | Est. Cost | Use When |
|-------|-----------|---------------|--------|-----------|----------|
| QUICK | 2 | 0 | 0 | ~$0.10 | Fast first-pass, no debate |
| BALANCED | 4 | 1 | 1 Justice | ~$0.50 | Standard questions |
| THOROUGH | 5 | 3 | 2 Justices + 1 Appellate | ~$2.00 | Important decisions |
| RIGOROUS | 5 | 5 | 2 Justices + 1 Appellate + 1 Magistrate | ~$5.00 | High-stakes decisions |
| EXHAUSTIVE | 6 | 5 | Full panel + stability audit + Fresh Eyes | ~$10.00 | Critical decisions |
| NUCLEAR | 6 | 7 | Full panel + mid-debate checkpoint + Fresh Eyes | ~$15.00 | Maximum rigor |

## Quick Start

### Prerequisites

- Python 3.9+
- A [Together AI](https://together.ai) API key (required — provides access to all judge models and open-weight advocates)
- Optional: Anthropic, OpenAI, Google API keys for frontier advocate models

### Setup

```bash
# Clone the repo
git clone https://github.com/mdm-sfo/conclave.git
cd conclave

# Install Python dependencies
pip install -r requirements.txt

# Copy the environment template and fill in your API keys
cp .env.example .env
# Edit .env with your keys, then:
source .env
# Or on EC2/remote, add exports to ~/.bashrc
```

> **Detailed installation instructions** for macOS, Ubuntu, Amazon Linux (EC2), and aarch64 (DGX Spark): see **[docs/INSTALL.md](docs/INSTALL.md)**

### Running a Deliberation

```bash
# Write your question in a briefing file
cat > briefing.md << 'EOF'
Should we use Rust or Go for our new CLI tool?
Our team has 8 engineers with deep Python experience.
Key requirements: fast startup, low memory, good concurrency.
EOF

# Run at THOROUGH depth
python3 scripts/council_orchestrator.py --briefing briefing.md --depth THOROUGH

# Output goes to ./conclave-sessions/tribunal-rust-or-go-for-our-20260302-223614/
# Or to TRIBUNAL_OUTPUT_DIR if set
```

### With Audio (TTS)

The screenplay pipeline generates a dramatized audio version of the deliberation using ElevenLabs voices.

```bash
# Requires: ELEVENLABS_API_KEY and ffmpeg
python3 scripts/screenplay_generator.py --demo --tts

# Or from a real session:
python3 scripts/screenplay_generator.py --session-dir ./conclave-sessions/tribunal-rust-or-go-20260301-061125 --tts
```

**Installing ffmpeg on Amazon Linux:**
```bash
cd /tmp
curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar xf ffmpeg-release-amd64-static.tar.xz
sudo cp ffmpeg-*-static/ffmpeg /usr/local/bin/
sudo cp ffmpeg-*-static/ffprobe /usr/local/bin/
```

### With NLI Validation (GPU)

NLI validation uses DeBERTa-v3-large to check advocate claims against source text. It requires a GPU host (any NVIDIA GPU with 4GB+ VRAM).

```bash
# On your GPU host:
bash setup_nli_server.sh

# Then set the URL in your environment:
export TRIBUNAL_NLI_URL=http://your-gpu-host:8787

# The screenplay pipeline will auto-detect the NLI server and use it.
# If unreachable, it falls back to rule-based validation automatically.
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TOGETHER_API_KEY` | **Yes** | Together AI — all judge models + open-weight advocates |
| `ANTHROPIC_API_KEY` | No | Anthropic — Claude Sonnet advocate |
| `OPENAI_API_KEY` | No | OpenAI — GPT-5 advocate |
| `GOOGLE_API_KEY` | No | Google — Gemini 2.5 Pro advocate |
| `FIREWORKS_API_KEY` | No | Fireworks AI — backup model routing |
| `ELEVENLABS_API_KEY` | No | ElevenLabs — TTS audio generation |
| `TRIBUNAL_OUTPUT_DIR` | No | Session output directory (default: `./conclave-sessions`) |
| `TRIBUNAL_NLI_URL` | No | NLI validation server endpoint (e.g., `http://your-gpu-host:8787`) |
| `CONCLAVE_DEFAULT_DEPTH` | No | Default depth level (default: `QUICK`) |
| `CONCLAVE_MAX_COST` | No | Max cost per session in USD (default: `5.00`) |

See `.env.example` for a complete template.

## Model Roster

### Advocates (Frontier Models)
- Claude Sonnet (Anthropic) — requires `ANTHROPIC_API_KEY`
- GPT-5 (OpenAI) — requires `OPENAI_API_KEY`
- Gemini 2.5 Pro (Google) — requires `GOOGLE_API_KEY`
- DeepSeek V3 (Together AI) — included with `TOGETHER_API_KEY`

### Advocate Backfill Pool (Together AI)
When fewer frontier keys are available than the depth level requires, the panel is filled from:
- Qwen 3 235B, MiniMax M1, Kimi K2, Llama 4 Maverick

### The Bench (Judges)
All judges run on Together AI — no additional keys needed.
- **Justices** (always seated at THOROUGH+): Qwen 3.5 397B, DeepSeek R1
- **Appellate Judges** (rotation pool): MiniMax M2.5, Kimi K2, Zhipu GLM-4.7
- **Magistrate Judges** (extended bench): GPT-OSS 120B, Zhipu GLM-5, DeepCogito v2.1 671B

### NLI Validation
- DeBERTa-v3-large-MNLI on any NVIDIA GPU — validates advocate claims against source text
- Falls back to rule-based validation (number/entity matching) if no GPU server is configured
- Setup: run `setup_nli_server.sh` on your GPU host, then set `TRIBUNAL_NLI_URL`

## Output Files

Each session produces a directory with:

| File | Description |
|------|-------------|
| `briefing.md` | The original question |
| `submission-advocate-*.md` | Each advocate's initial submission |
| `challenge-by-advocate-*.md` | Cross-examination challenges |
| `debate-round-*-advocate-*.md` | Debate responses (DEFEND/CONCEDE/REVISE) |
| `dissent-advocate-*.md` | Formal dissenting opinions (if any) |
| `judgment-judge-*.md` | Judicial opinions from The Bench |
| `fresh-eyes-review.md` | Fresh Eyes sanity check |
| `session-summary.md` | Canonical summary: Question → Outcome → How → Build This |
| `session-summary.pdf` | Styled PDF version of the session summary (requires `reportlab`) |
| `play-by-play.md` | Dramatic narrative of the debate |
| `screenplay.md` | TTS-ready screenplay (if `--demo` or screenplay pipeline) |
| `voice-script.json` | TTS manifest with character/line data |
| `*-audio.mp3` | Dramatized audio (if `--tts`) |
| `debrief.md` | Full panel composition, statistics, identity reveals |
| `council-log.json` | Machine-readable session log |
| `position-stability.md` | Kelley-Riedl sycophancy audit scorecard |
| `alias-map.json` | Advocate identity reveal |
| `cardinal-alias-map.json` | Judge identity reveal |

## Architecture

```
scripts/council_orchestrator.py    — Core state machine (deterministic, not a model)
scripts/config_loader.py           — Model roster, depth levels, env var resolution
scripts/model_client.py            — LiteLLM wrapper: fan_out, fan_out_multi, call_model
scripts/progress.py                — Terminal output formatting
scripts/screenplay_generator.py    — Three-pass pipeline: extraction → validation → dramatization
scripts/summary_pdf.py             — ReportLab PDF generation from session-summary.md
scripts/tts_pipeline.py            — ElevenLabs TTS: voice casting, delivery tags, ffmpeg stitch
scripts/nli_server.py              — FastAPI NLI server (DeBERTa-v3-large on GPU)
setup_nli_server.sh                — One-command NLI server setup for GPU hosts
```

## Design Principles

1. **The orchestrator is code, not a model.** It can't be sycophantic. It dispatches, collects, anonymizes, routes, and manages — but never opinionates.
2. **Advocates argue with hypothesis + evidence.** Every claim must cite reasoning type (deductive/inductive/abductive) and specific proof.
3. **Cardinals embody rigorous skepticism** — treating suspicious unanimity as a defect, not a feature.
4. **Position stability tracking** catches sycophantic drift: when models change positions under social pressure without new evidence.
5. **Dissenting opinions** ensure that minority positions survive to the final output when backed by evidence.
6. **Maximum 1 remand per session** — judges can send it back once, but not create infinite loops.
7. **Uncertainty must survive to final output** — papering over disagreement is worse than admitting it.

## License

Apache 2.0
