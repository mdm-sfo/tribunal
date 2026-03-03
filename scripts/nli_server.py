#!/usr/bin/env python3
"""
NLI Inference Server for Conclave / The Tribunal
=================================================

Serves DeBERTa-v3-large-MNLI (and optionally cross-encoder/nli-deberta-v3-small)
as a REST API endpoint for natural language inference.

Designed to run on NVIDIA DGX Spark (GB10 Grace Blackwell, 128GB unified memory).
Called by screenplay_generator.py's validation pass.

Usage:
    # Install dependencies first (see setup_nli_server.sh)
    python3 nli_server.py                    # default: port 8787, DeBERTa-v3-large
    python3 nli_server.py --port 8787        # custom port
    python3 nli_server.py --model small      # use the smaller/faster model
    python3 nli_server.py --model large      # use DeBERTa-v3-large (default)

API Endpoints:
    POST /predict       - Single premise/hypothesis pair
    POST /predict_batch - Batch of premise/hypothesis pairs
    GET  /health        - Health check + loaded model info
    GET  /              - Server info

Apache 2.0 — github.com/mdm-sfo/conclave
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

MODELS = {
    "large": {
        "name": "cross-encoder/nli-deberta-v3-large",
        "description": "DeBERTa-v3-large fine-tuned on SNLI+MNLI (304M params, ~91.5% MNLI acc)",
        "labels": ["contradiction", "entailment", "neutral"],
    },
    "small": {
        "name": "cross-encoder/nli-deberta-v3-small",
        "description": "DeBERTa-v3-small fine-tuned on SNLI+MNLI (44M params, ~87.5% MNLI acc)",
        "labels": ["contradiction", "entailment", "neutral"],
    },
    "base": {
        "name": "cross-encoder/nli-deberta-v3-base",
        "description": "DeBERTa-v3-base fine-tuned on SNLI+MNLI (86M params, ~90.0% MNLI acc)",
        "labels": ["contradiction", "entailment", "neutral"],
    },
}

DEFAULT_MODEL = "large"
DEFAULT_PORT = 8787

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[nli-server] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nli-server")

# ---------------------------------------------------------------------------
# FastAPI app (created at module level, configured at startup)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError:
    log.error("FastAPI not installed. Run: pip install fastapi uvicorn")
    sys.exit(1)

app = FastAPI(
    title="Tribunal NLI Server",
    description="Natural Language Inference endpoint for Conclave screenplay validation",
    version="0.1.0",
)

# Global state — populated during startup
_model = None
_tokenizer = None
_model_config = None
_device = None
_load_time = None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class NLIPair(BaseModel):
    """A single premise–hypothesis pair for NLI classification."""
    premise: str = Field(..., description="The source text (evidence)")
    hypothesis: str = Field(..., description="The claim to verify against the premise")


class NLIRequest(BaseModel):
    """Single NLI prediction request."""
    premise: str = Field(..., description="The source text (evidence)")
    hypothesis: str = Field(..., description="The claim to verify against the premise")


class NLIBatchRequest(BaseModel):
    """Batch NLI prediction request."""
    pairs: list = Field(..., description="List of {premise, hypothesis} pairs")


class NLIPrediction(BaseModel):
    """NLI prediction result."""
    label: str = Field(..., description="Predicted label: entailment, contradiction, or neutral")
    scores: dict = Field(..., description="Probability scores for each label")
    entailment: float = Field(..., description="Entailment probability (convenience field)")
    contradiction: float = Field(..., description="Contradiction probability (convenience field)")
    neutral: float = Field(..., description="Neutral probability (convenience field)")


class NLIResponse(BaseModel):
    """Single prediction response."""
    prediction: NLIPrediction
    model: str
    inference_ms: float


class NLIBatchResponse(BaseModel):
    """Batch prediction response."""
    predictions: list
    model: str
    inference_ms: float
    count: int


# ---------------------------------------------------------------------------
# Inference logic
# ---------------------------------------------------------------------------

def predict_nli(premise: str, hypothesis: str) -> dict:
    """
    Run NLI inference on a single premise/hypothesis pair.
    Returns dict with label, scores, and individual probabilities.
    """
    import torch  # noqa: PLC0415

    inputs = _tokenizer(
        premise,
        hypothesis,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        padding=True,
    )
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = _model(**inputs)
        logits = outputs.logits
        probs = torch.softmax(logits, dim=-1)[0].cpu().tolist()

    labels = _model_config["labels"]
    score_map = {label: round(prob, 4) for label, prob in zip(labels, probs)}
    predicted_label = labels[probs.index(max(probs))]

    return {
        "label": predicted_label,
        "scores": score_map,
        "entailment": score_map.get("entailment", 0.0),
        "contradiction": score_map.get("contradiction", 0.0),
        "neutral": score_map.get("neutral", 0.0),
    }


def predict_nli_batch(pairs: list) -> list:
    """
    Run NLI inference on a batch of premise/hypothesis pairs.
    More efficient than calling predict_nli in a loop.
    """
    import torch  # noqa: PLC0415

    if not pairs:
        return []

    premises = [p["premise"] for p in pairs]
    hypotheses = [p["hypothesis"] for p in pairs]

    inputs = _tokenizer(
        premises,
        hypotheses,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        padding=True,
    )
    inputs = {k: v.to(_device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = _model(**inputs)
        logits = outputs.logits
        all_probs = torch.softmax(logits, dim=-1).cpu().tolist()

    labels = _model_config["labels"]
    results = []
    for probs in all_probs:
        score_map = {label: round(prob, 4) for label, prob in zip(labels, probs)}
        predicted_label = labels[probs.index(max(probs))]
        results.append({
            "label": predicted_label,
            "scores": score_map,
            "entailment": score_map.get("entailment", 0.0),
            "contradiction": score_map.get("contradiction", 0.0),
            "neutral": score_map.get("neutral", 0.0),
        })

    return results


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    """Server info."""
    return {
        "service": "Tribunal NLI Server",
        "version": "0.1.0",
        "model": _model_config["name"] if _model_config else "not loaded",
        "status": "ready" if _model is not None else "loading",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    """Health check with model info."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not yet loaded")

    import torch  # noqa: PLC0415
    return {
        "status": "healthy",
        "model": _model_config["name"],
        "model_description": _model_config["description"],
        "device": str(_device),
        "gpu_available": torch.cuda.is_available(),
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "load_time_seconds": round(_load_time, 2) if _load_time else None,
    }


@app.post("/predict", response_model=NLIResponse)
def predict(request: NLIRequest):
    """Predict NLI label for a single premise/hypothesis pair."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not yet loaded")

    t0 = time.time()
    result = predict_nli(request.premise, request.hypothesis)
    elapsed_ms = (time.time() - t0) * 1000

    return NLIResponse(
        prediction=NLIPrediction(**result),
        model=_model_config["name"],
        inference_ms=round(elapsed_ms, 2),
    )


@app.post("/predict_batch", response_model=NLIBatchResponse)
def predict_batch(request: NLIBatchRequest):
    """Predict NLI labels for a batch of premise/hypothesis pairs."""
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not yet loaded")

    if len(request.pairs) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 pairs per batch")

    t0 = time.time()
    pairs_dicts = []
    for pair in request.pairs:
        if isinstance(pair, dict):
            pairs_dicts.append(pair)
        else:
            pairs_dicts.append({"premise": pair.premise, "hypothesis": pair.hypothesis})

    results = predict_nli_batch(pairs_dicts)
    elapsed_ms = (time.time() - t0) * 1000

    return NLIBatchResponse(
        predictions=[NLIPrediction(**r) for r in results],
        model=_model_config["name"],
        inference_ms=round(elapsed_ms, 2),
        count=len(results),
    )


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_model(model_key: str):
    """Load the NLI model and tokenizer."""
    global _model, _tokenizer, _model_config, _device, _load_time

    import torch  # noqa: PLC0415

    _model_config = MODELS[model_key]
    model_name = _model_config["name"]

    log.info(f"Loading model: {model_name}")
    log.info(f"Description: {_model_config['description']}")

    # Device selection
    if torch.cuda.is_available():
        _device = torch.device("cuda:0")
        log.info(f"Using GPU: {torch.cuda.get_device_name(0)}")
        log.info(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
    else:
        _device = torch.device("cpu")
        log.info("No GPU detected — using CPU (will be slower)")

    t0 = time.time()

    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

        _tokenizer = AutoTokenizer.from_pretrained(model_name)
        _model = AutoModelForSequenceClassification.from_pretrained(model_name)
        _model.to(_device)
        _model.eval()

        _load_time = time.time() - t0
        log.info(f"Model loaded in {_load_time:.1f}s")

        # Quick self-test
        test_result = predict_nli(
            premise="The cat sat on the mat.",
            hypothesis="An animal was on the mat.",
        )
        log.info(f"Self-test: 'cat on mat' entails 'animal on mat' -> {test_result['label']} "
                 f"(entailment={test_result['entailment']:.3f})")

    except Exception as e:
        log.error(f"Failed to load model: {e}")
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tribunal NLI Inference Server")
    parser.add_argument(
        "--model", "-m",
        choices=list(MODELS.keys()),
        default=DEFAULT_MODEL,
        help=f"Model size to load (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 — accessible from network)",
    )
    args = parser.parse_args()

    # Load model before starting server
    load_model(args.model)

    log.info(f"Starting server on {args.host}:{args.port}")
    log.info(f"API docs at http://{args.host}:{args.port}/docs")

    try:
        import uvicorn  # noqa: PLC0415
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except ImportError:
        log.error("uvicorn not installed. Run: pip install uvicorn")
        sys.exit(1)


if __name__ == "__main__":
    main()
