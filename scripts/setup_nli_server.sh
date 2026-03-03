#!/bin/bash
# =============================================================================
# setup_nli_server.sh — Deploy the Tribunal NLI server on DGX Spark
# =============================================================================
#
# Run FROM a machine on the same network as your GPU host:
#     cd conclave
#     bash setup_nli_server.sh
#
# Or copy to the GPU host and run locally:
#     scp setup_nli_server.sh user@your-gpu-host:~/
#     ssh user@your-gpu-host
#     bash ~/setup_nli_server.sh
#
# What this does:
#   1. Creates a Python venv at ~/tribunal-nli/
#   2. Installs torch, transformers, fastapi, uvicorn
#   3. Downloads DeBERTa-v3-large-MNLI model weights (~1.2GB)
#   4. Copies nli_server.py into the venv
#   5. Creates a systemd user service for auto-start
#   6. Starts the server on port 8787
#
# After setup, the NLI server is at:
#   http://your-gpu-host:8787/predict
#   http://your-gpu-host:8787/docs  (Swagger UI)
#
# Apache 2.0 — github.com/mdm-sfo/conclave
# =============================================================================

set -euo pipefail

# --- Configuration ---
# Set these to match your GPU host. The defaults are placeholders.
SPARK_HOST="${NLI_GPU_HOST:-your-gpu-host}"
SPARK_USER="${NLI_GPU_USER:-$(whoami)}"
NLI_DIR="tribunal-nli"
NLI_PORT=8787

echo "=============================================="
echo " Tribunal NLI Server — DGX Spark Setup"
echo "=============================================="
echo ""

# Detect if we're running on the Spark itself or remotely
if hostname -f 2>/dev/null | grep -q "spark\|dgx\|gpu"; then
    echo "[local] Running directly on DGX Spark"
    REMOTE=false
else
    echo "[remote] Will SSH into ${SPARK_HOST}"
    REMOTE=true
fi

# ---------------------------------------------------------------------------
# Write the setup script to a temp file (avoids heredoc-in-subshell issues)
# ---------------------------------------------------------------------------
SETUP_TMPFILE=$(mktemp /tmp/tribunal-nli-setup.XXXXXX.sh)
trap "rm -f ${SETUP_TMPFILE}" EXIT

cat > "${SETUP_TMPFILE}" << 'INNEREOF'
#!/bin/bash
set -euo pipefail

NLI_DIR="$HOME/tribunal-nli"
NLI_PORT=8787

echo "[1/6] Creating directory: ${NLI_DIR}"
mkdir -p "${NLI_DIR}"

echo "[2/6] Setting up Python virtual environment..."
if [ ! -d "${NLI_DIR}/venv" ]; then
    python3 -m venv "${NLI_DIR}/venv"
    echo "  Created new venv"
else
    echo "  Venv already exists, reusing"
fi

source "${NLI_DIR}/venv/bin/activate"

echo "[3/6] Installing Python dependencies..."
pip install --upgrade pip -q
pip install torch transformers fastapi uvicorn pydantic -q
echo "  Installed: torch, transformers, fastapi, uvicorn, pydantic"

echo "[4/6] Pre-downloading model weights (this may take a minute)..."
python3 << 'PYEOF'
from transformers import AutoModelForSequenceClassification, AutoTokenizer
model_name = "cross-encoder/nli-deberta-v3-large"
print(f"  Downloading {model_name}...")
tok = AutoTokenizer.from_pretrained(model_name)
mdl = AutoModelForSequenceClassification.from_pretrained(model_name)
print("  Model downloaded and cached.")
PYEOF

echo "[5/6] Setting up systemd user service..."
mkdir -p "${HOME}/.config/systemd/user"

cat > "${HOME}/.config/systemd/user/tribunal-nli.service" << SVCEOF
[Unit]
Description=Tribunal NLI Inference Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${NLI_DIR}
ExecStart=${NLI_DIR}/venv/bin/python3 ${NLI_DIR}/nli_server.py --port ${NLI_PORT} --model large
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
SVCEOF

# Enable lingering so service runs without active login
loginctl enable-linger "${USER}" 2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable tribunal-nli.service

echo "[6/6] Starting NLI server..."
systemctl --user restart tribunal-nli.service

# Wait for server to be ready
echo "  Waiting for server startup..."
for i in $(seq 1 30); do
    if curl -s "http://localhost:${NLI_PORT}/health" > /dev/null 2>&1; then
        echo "  Server is ready!"
        curl -s "http://localhost:${NLI_PORT}/health" | python3 -m json.tool 2>/dev/null || true
        break
    fi
    sleep 2
    echo "  ...waiting (${i}/30)"
done

SPARK_HOSTNAME=$(hostname -f 2>/dev/null || echo "localhost")
echo ""
echo "=============================================="
echo " NLI Server deployed!"
echo " Endpoint: http://${SPARK_HOSTNAME}:${NLI_PORT}"
echo " Docs:     http://${SPARK_HOSTNAME}:${NLI_PORT}/docs"
echo " Status:   systemctl --user status tribunal-nli"
echo " Logs:     journalctl --user -u tribunal-nli -f"
echo "=============================================="
INNEREOF

chmod +x "${SETUP_TMPFILE}"

if [ "${REMOTE}" = true ]; then
    echo ""
    echo "[step 1] Copying nli_server.py to Spark..."

    # Find nli_server.py — check a few likely locations
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    NLI_SCRIPT=""
    for path in \
        "${SCRIPT_DIR}/nli_server.py" \
        "${HOME}/.claude/skills/conclave/scripts/nli_server.py" \
        "./nli_server.py" \
    ; do
        if [ -f "${path}" ]; then
            NLI_SCRIPT="${path}"
            break
        fi
    done

    if [ -z "${NLI_SCRIPT}" ]; then
        echo "ERROR: Cannot find nli_server.py."
        echo "Looked in:"
        echo "  ${SCRIPT_DIR}/nli_server.py"
        echo "  ${HOME}/.claude/skills/conclave/scripts/nli_server.py"
        echo "  ./nli_server.py"
        echo ""
        echo "Place nli_server.py next to this script or in the current directory."
        exit 1
    fi

    echo "  Found: ${NLI_SCRIPT}"

    # Ensure remote directory exists, then copy files
    ssh "${SPARK_USER}@${SPARK_HOST}" "mkdir -p ~/tribunal-nli"
    scp "${NLI_SCRIPT}" "${SPARK_USER}@${SPARK_HOST}:~/tribunal-nli/nli_server.py"
    echo "  Copied nli_server.py to ${SPARK_HOST}:~/tribunal-nli/"

    echo ""
    echo "[step 2] Running setup on Spark via SSH..."
    # Pipe the setup script over SSH
    cat "${SETUP_TMPFILE}" | ssh "${SPARK_USER}@${SPARK_HOST}" "bash -s"
else
    # Running locally on the Spark
    echo ""
    echo "[step 1] Checking for nli_server.py..."
    if [ ! -f "${HOME}/${NLI_DIR}/nli_server.py" ]; then
        echo "ERROR: ${HOME}/${NLI_DIR}/nli_server.py not found."
        echo "Copy it there first:  cp /path/to/nli_server.py ~/${NLI_DIR}/"
        exit 1
    fi
    echo "  Found: ${HOME}/${NLI_DIR}/nli_server.py"

    echo ""
    echo "[step 2] Running setup..."
    bash "${SETUP_TMPFILE}"
fi
