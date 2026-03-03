# Installation Guide

Step-by-step setup for The Tribunal on macOS, Ubuntu/Debian, Amazon Linux (EC2), and aarch64 (NVIDIA DGX Spark, Jetson, Raspberry Pi).

## Quick Start (All Platforms)

```bash
git clone https://github.com/mdm-sfo/conclave.git
cd conclave
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
source .env
```

Then run a test:

```bash
echo "What is the best programming language for CLI tools?" > /tmp/test-briefing.md
python3 scripts/council_orchestrator.py --briefing /tmp/test-briefing.md --depth QUICK --emit summary
```

If that works, you're good. Read on for platform-specific details and optional features.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.9+ | 3.10-3.12 recommended |
| pip | Comes with Python |
| git | For cloning the repo |
| A [Together AI](https://together.ai) API key | **Required** — provides all judge models and open-weight advocates |

Optional API keys (each adds one frontier advocate model):
- `ANTHROPIC_API_KEY` — Claude Sonnet
- `OPENAI_API_KEY` — GPT-5
- `GOOGLE_API_KEY` — Gemini 2.5 Pro

---

## Platform-Specific Setup

### macOS

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python (if not already installed)
brew install python@3.12

# Clone and install
git clone https://github.com/mdm-sfo/conclave.git
cd conclave
pip3 install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys, then:
source .env

# For audio output (optional):
brew install ffmpeg
```

**Apple Silicon note**: All dependencies are pure Python or have ARM wheels. No Rosetta needed for core features. The NLI server (GPU validation) is not supported on macOS — it requires an NVIDIA GPU.

### Ubuntu / Debian

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git

# Clone and install
git clone https://github.com/mdm-sfo/conclave.git
cd conclave

# Option A: Install globally (simple, may need --break-system-packages on Ubuntu 23.04+)
pip3 install --break-system-packages -r requirements.txt

# Option B: Use a venv (cleaner)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys, then:
source .env

# For audio output (optional):
sudo apt install -y ffmpeg
```

### Amazon Linux / EC2

```bash
# System packages
sudo yum install -y python3 python3-pip git

# Clone and install
git clone https://github.com/mdm-sfo/conclave.git
cd conclave
pip3 install -r requirements.txt

# Set up environment — on EC2, put keys in .bashrc for persistence
cp .env.example .env
# Edit .env with your keys, then append to .bashrc:
cat .env | grep -v '^#' | grep '=' | sed 's/^/export /' >> ~/.bashrc
source ~/.bashrc

# For audio output (optional):
# ffmpeg isn't in default Amazon Linux repos — install from static binary:
cd /tmp
curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar xf ffmpeg-release-amd64-static.tar.xz
sudo cp ffmpeg-*-static/ffmpeg /usr/local/bin/
sudo cp ffmpeg-*-static/ffprobe /usr/local/bin/
cd -
```

### aarch64 (DGX Spark, Jetson, Raspberry Pi)

```bash
# System packages
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git

# Clone and install
git clone https://github.com/mdm-sfo/conclave.git
cd conclave
pip3 install --break-system-packages -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your keys, then:
echo 'source ~/conclave/.env' >> ~/.bashrc
source .env

# For audio output (optional):
sudo apt install -y ffmpeg

# For NLI validation (optional — DGX Spark / Jetson only):
# See "NLI Server Setup" section below
```

**DGX Spark note**: If you're using the system Python and hit PEP 668 errors, use `--break-system-packages` as shown above.

---

## Python Dependencies Explained

### Core (Required)

| Package | Why |
|---------|-----|
| `litellm` | Routes API calls to Together AI, Anthropic, OpenAI, Google, Fireworks through a single interface. This is the only hard dependency. |
| `tenacity` | Retry logic for flaky judge model API calls (Cerebras endpoints, Together AI under load). Without this, judge seats that hit transient errors will fail instead of retrying. |

### Optional

| Package | Why | What happens without it |
|---------|-----|------------------------|
| `reportlab` | Generates styled PDF of session summaries | PDF step is skipped; markdown summary still generated |
| `ffmpeg` (system binary) | Stitches TTS audio segments into a single MP3 | Audio pipeline fails; everything else works |

### NLI Server Only (GPU host)

These are only needed on the machine running the NLI validation server — NOT on the machine running the orchestrator:

| Package | Why |
|---------|-----|
| `torch` | GPU inference for DeBERTa |
| `transformers` | Hugging Face model loading |
| `fastapi` + `uvicorn` | HTTP API server |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

### Required

```bash
export TOGETHER_API_KEY="your-key-here"       # Together AI — all judges + open-weight advocates
```

### Optional (Each Adds a Frontier Advocate)

```bash
export ANTHROPIC_API_KEY="your-key-here"      # Claude Sonnet
export OPENAI_API_KEY="your-key-here"          # GPT-5
export GOOGLE_API_KEY="your-key-here"          # Gemini 2.5 Pro
export FIREWORKS_API_KEY="your-key-here"       # Backup model routing
```

### Optional (Features)

```bash
export ELEVENLABS_API_KEY="your-key-here"      # TTS audio generation
export TRIBUNAL_OUTPUT_DIR="~/tribunal-output"  # Where sessions are saved (default: ./conclave-sessions)
export CONCLAVE_DEFAULT_DEPTH="THOROUGH"        # Default depth level
export CONCLAVE_MAX_COST="5.00"                 # Safety limit per session in USD
export TRIBUNAL_NLI_URL="http://gpu-host:8787"  # NLI validation server endpoint
```

### Making Keys Persist

On remote machines (EC2, DGX), add exports to `~/.bashrc` so they survive SSH sessions:

```bash
# Append all non-comment, non-empty lines from .env as exports
cat .env | grep -v '^#' | grep '=' | sed 's/^/export /' >> ~/.bashrc
source ~/.bashrc
```

---

## Verifying Your Installation

### 1. Check Python deps

```bash
python3 -c "import litellm; import tenacity; print('Core deps OK')"
python3 -c "import reportlab; print('PDF support OK')" 2>/dev/null || echo "PDF support: not installed (optional)"
```

### 2. Check API keys

```bash
python3 -c "
import os
keys = ['TOGETHER_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'ELEVENLABS_API_KEY']
for k in keys:
    v = os.environ.get(k)
    status = 'SET' if v and not v.startswith('your_') else 'NOT SET'
    print(f'{k}: {status}')
"
```

### 3. Run a QUICK test

```bash
echo "Is water wet?" > /tmp/test.md
python3 scripts/council_orchestrator.py --briefing /tmp/test.md --depth QUICK --emit summary
```

This should complete in under 2 minutes and cost ~$0.10. If it works, your setup is good.

### 4. Check ffmpeg (for audio)

```bash
ffmpeg -version 2>/dev/null && echo "ffmpeg OK" || echo "ffmpeg not installed (needed for audio only)"
```

---

## NLI Server Setup (Optional, GPU Required)

The NLI server runs DeBERTa-v3-large to validate advocate claims using Natural Language Inference. It's optional — without it, the screenplay pipeline falls back to rule-based validation (number/entity matching).

### Requirements
- NVIDIA GPU with 4GB+ VRAM
- Python 3.9+
- CUDA toolkit

### Setup

```bash
# On your GPU host:
cd conclave
bash scripts/setup_nli_server.sh

# This will:
# 1. Create a venv at ~/tribunal-nli/
# 2. Install torch, transformers, fastapi, uvicorn
# 3. Download the DeBERTa-v3-large-MNLI model (~1.2GB)
# 4. Create a systemd user service for auto-start
# 5. Start the server on port 8787

# Then on your orchestrator machine, set:
export TRIBUNAL_NLI_URL=http://your-gpu-host:8787
```

### Remote setup (e.g., orchestrator on EC2, NLI on DGX Spark):

```bash
# From your EC2 instance:
bash scripts/setup_nli_server.sh --remote gpu-host-alias

# Then:
export TRIBUNAL_NLI_URL=http://gpu-host-ip:8787
```

---

## Using with Claude Code

The Tribunal is designed as a Claude Code skill. To install it:

```bash
# Clone into your Claude Code skills directory
git clone https://github.com/mdm-sfo/conclave.git ~/.claude/skills/tribunal

# Install deps
pip install -r ~/.claude/skills/tribunal/requirements.txt

# Add API keys to ~/.bashrc (Claude Code inherits your shell environment)
```

Then invoke it in Claude Code with `/tribunal [your question]`.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'litellm'`
```bash
pip install litellm
# Or on Ubuntu 23.04+:
pip install --break-system-packages litellm
```

### `ModuleNotFoundError: No module named 'tenacity'`
```bash
pip install tenacity
# Or on Ubuntu 23.04+:
pip install --break-system-packages tenacity
```

### `OSError: TOGETHER_API_KEY is required`
Your API keys aren't in the current shell environment. Either:
```bash
source .env          # If using .env file
source ~/.bashrc     # If keys are in .bashrc
```

### Judge seats showing "✗ tenacity import failed"
Install the `tenacity` package (see above). This affects Justice seats (Qwen 3.5 397B, Qwen 3 235B on Cerebras).

### PDF generation skipped
```bash
pip install reportlab
```

### TTS audio fails with "ffmpeg not found"
Install ffmpeg for your platform (see platform-specific sections above).

### `externally-managed-environment` error (PEP 668)
On Ubuntu 23.04+ and some Debian systems, use `--break-system-packages`:
```bash
pip install --break-system-packages -r requirements.txt
```
Or use a venv:
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### Advocate timeout errors
Some models (especially GPT-5) can take 3+ minutes for long briefings. The orchestrator uses per-model timeouts and will continue with available submissions. This is normal — the session degrades gracefully.

### Cost safety limit hit
If you see "Estimated cost exceeds limit", increase `CONCLAVE_MAX_COST`:
```bash
export CONCLAVE_MAX_COST=25.00   # For NUCLEAR depth sessions
```
