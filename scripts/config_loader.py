"""
Tribunal configuration — env vars, depth levels, model roster, The Bench.

Design principle: deterministic configuration. No model-based routing decisions.
The orchestrator is code, not a model — it can't be sycophantic.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Depth levels
# ---------------------------------------------------------------------------

@dataclass
class DepthConfig:
    name: str
    advocates: int            # number of advocate models
    debate_rounds: int        # max debate rounds (0 = no debate)
    consensus_target: float   # agreement threshold to exit debate early
    cardinals_bishops: int    # how many bishops (0, 1, or 2)
    cardinals_priests: int    # how many priests to draw
    cardinals_deacons: int    # how many deacons to draw
    timeout_per_model: int    # seconds per API call
    timeout_global: int       # seconds for entire session
    estimated_cost: str       # display string
    mid_debate_checkpoint: int = 0  # round after which to run judicial checkpoint (0 = none)
    position_stability_audit: bool = False  # whether to include flip-rate scorecard for judges


DEPTH_LEVELS: dict[str, DepthConfig] = {
    "QUICK": DepthConfig(
        name="QUICK",
        advocates=2, debate_rounds=0, consensus_target=0.50,
        cardinals_bishops=0, cardinals_priests=0, cardinals_deacons=0,
        timeout_per_model=60, timeout_global=120,
        estimated_cost="~$0.10",
    ),
    "BALANCED": DepthConfig(
        name="BALANCED",
        advocates=4, debate_rounds=1, consensus_target=0.66,
        cardinals_bishops=1, cardinals_priests=0, cardinals_deacons=0,
        timeout_per_model=120, timeout_global=480,
        estimated_cost="~$0.50",
    ),
    "THOROUGH": DepthConfig(
        name="THOROUGH",
        advocates=5, debate_rounds=3, consensus_target=0.80,
        cardinals_bishops=2, cardinals_priests=1, cardinals_deacons=0,
        timeout_per_model=120, timeout_global=900,
        estimated_cost="~$2.00",
    ),
    "RIGOROUS": DepthConfig(
        name="RIGOROUS",
        advocates=5, debate_rounds=5, consensus_target=0.90,
        cardinals_bishops=2, cardinals_priests=1, cardinals_deacons=1,
        timeout_per_model=150, timeout_global=1800,
        estimated_cost="~$5.00",
    ),
    "EXHAUSTIVE": DepthConfig(
        name="EXHAUSTIVE",
        advocates=6, debate_rounds=5, consensus_target=0.95,
        cardinals_bishops=2, cardinals_priests=2, cardinals_deacons=1,
        timeout_per_model=180, timeout_global=2700,
        estimated_cost="~$10.00",
        position_stability_audit=True,
    ),
    "NUCLEAR": DepthConfig(
        name="NUCLEAR",
        advocates=6, debate_rounds=7, consensus_target=0.95,
        cardinals_bishops=2, cardinals_priests=2, cardinals_deacons=2,
        timeout_per_model=180, timeout_global=3600,
        estimated_cost="~$15.00",
        mid_debate_checkpoint=4,
        position_stability_audit=True,
    ),
}


# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

@dataclass
class ModelDef:
    """A model available for Tribunal sessions."""
    id: str               # unique key used in config and logs
    litellm_model: str    # the string you pass to litellm.completion(model=...)
    display_name: str     # human-readable name for logs and debrief
    provider: str         # provider name (for debrief / independence checks)
    role: str             # "advocate" | "bishop" | "priest" | "deacon" | "fresh_eyes"
    cost_tier: str        # "low" | "medium" | "high"
    env_key: str          # which env var holds the API key for this model
    is_reasoning: bool = False  # reasoning models need max_completion_tokens, no temperature
    context_window: int = 131072  # context window in tokens (default 128K)
    timeout_override: Optional[int] = None  # per-model timeout in seconds (overrides depth default)
    web_search: bool = False  # inject web search tools (Gemini: Google grounding; Perplexity: implicit)


# --- Advocates (frontier models — the "big boys") ---

ADVOCATES: list[ModelDef] = [
    ModelDef(
        id="claude-sonnet",
        litellm_model="anthropic/claude-sonnet-4-20250514",
        display_name="Claude Sonnet",
        provider="Anthropic",
        role="advocate",
        cost_tier="medium",
        env_key="ANTHROPIC_API_KEY",
    ),
    ModelDef(
        id="gpt5",
        litellm_model="openai/gpt-5",
        display_name="GPT-5",
        provider="OpenAI",
        role="advocate",
        cost_tier="high",
        env_key="OPENAI_API_KEY",
        is_reasoning=True,
        context_window=131072,
        timeout_override=240,  # reasoning model — needs more time
    ),
    ModelDef(
        id="gemini-pro",
        litellm_model="gemini/gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        provider="Google",
        role="advocate",
        cost_tier="medium",
        env_key="GOOGLE_API_KEY",
        is_reasoning=True,
        web_search=True,  # Google Search grounding — injected as tool at call time
    ),
    ModelDef(
        id="perplexity-sonar",
        litellm_model="perplexity/sonar-pro",
        display_name="Perplexity Sonar Pro",
        provider="Perplexity AI",
        role="advocate",
        cost_tier="medium",
        env_key="PERPLEXITY_API_KEY",
        # search is always on in Sonar Pro — no web_search flag needed
    ),
    ModelDef(
        id="deepseek-v3",
        litellm_model="together_ai/deepseek-ai/DeepSeek-V3",
        display_name="DeepSeek V3",
        provider="DeepSeek (Together AI)",
        role="advocate",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
    ),
]

# --- Justices (permanent — always seated at THOROUGH+) ---

BISHOPS: list[ModelDef] = [
    ModelDef(
        id="bishop-qwen-cerebras",
        litellm_model="cerebras/qwen-3-235b-a22b-instruct-2507",
        display_name="Qwen 3 235B (Cerebras)",
        provider="Qwen (Cerebras)",
        role="bishop",
        cost_tier="low",
        env_key="CEREBRAS_API_KEY",
        context_window=131072,  # 128K
    ),
    ModelDef(
        id="bishop-qwen",
        litellm_model="together_ai/Qwen/Qwen3.5-397B-A17B",
        display_name="Qwen 3.5 397B",
        provider="Qwen (Together AI)",
        role="bishop",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=262144,  # 256K
    ),
    ModelDef(
        id="bishop-deepseek-r1",
        litellm_model="together_ai/deepseek-ai/DeepSeek-R1",
        display_name="DeepSeek R1",
        provider="DeepSeek (Together AI)",
        role="bishop",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        is_reasoning=True,
        context_window=131072,  # 128K
    ),
]

# --- Appellate Judges (rotation pool — randomly drawn at BALANCED+) ---

PRIESTS: list[ModelDef] = [
    # RNJ-1 REMOVED — 32K context too small for judicial duty at THOROUGH+.
    # Transcripts routinely exceed 33K tokens by the judicial review phase.
    ModelDef(
        id="priest-minimax",
        litellm_model="together_ai/MiniMaxAI/MiniMax-M2.5",
        display_name="MiniMax M2.5",
        provider="MiniMax (Together AI)",
        role="priest",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=196608,  # 192K
    ),
    ModelDef(
        id="priest-kimi",
        litellm_model="together_ai/moonshotai/kimi-k2-instruct",
        display_name="Kimi K2 Instruct",
        provider="Moonshot AI (Together AI)",
        role="priest",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=131072,  # 128K
    ),
    ModelDef(
        id="priest-glm47",
        litellm_model="together_ai/zai-org/GLM-4.7",
        display_name="Zhipu GLM-4.7",
        provider="Zhipu AI (Together AI)",
        role="priest",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=202752,  # ~198K
    ),
]

# --- Magistrate Judges (extended bench — drawn at RIGOROUS+) ---

DEACONS: list[ModelDef] = [
    ModelDef(
        id="deacon-gptoss",
        litellm_model="cerebras/gpt-oss-120b",
        display_name="GPT-OSS 120B",
        provider="OpenAI (Cerebras)",
        role="deacon",
        cost_tier="low",
        env_key="CEREBRAS_API_KEY",
        context_window=131072,  # 128K — ~300 tok/s on Cerebras wafer (vs ~75 on Together)
    ),
    ModelDef(
        id="deacon-qwen3-cerebras",
        litellm_model="cerebras/qwen-3-235b-a22b-instruct-2507",
        display_name="Qwen 3 235B",
        provider="Qwen (Cerebras)",
        role="deacon",
        cost_tier="low",
        env_key="CEREBRAS_API_KEY",
        context_window=131072,  # 128K paid tier — ~1400 tok/s on Cerebras wafer
    ),
    ModelDef(
        id="deacon-glm5",
        litellm_model="together_ai/zai-org/GLM-5",
        display_name="Zhipu GLM-5",
        provider="Zhipu AI (Together AI)",
        role="deacon",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=202752,  # ~198K
    ),
    ModelDef(
        id="deacon-cogito",
        litellm_model="together_ai/deepcogito/cogito-v2-1-671b",
        display_name="DeepCogito v2.1 671B",
        provider="Deep Cogito (Together AI)",
        role="deacon",
        cost_tier="low",
        env_key="TOGETHER_API_KEY",
        context_window=163840,  # 160K
    ),
]


# --- Dramatist (dedicated creative writing model for screenplay generation) ---

DRAMATIST: ModelDef = ModelDef(
    id="dramatist-kimi-k2",
    litellm_model="together_ai/moonshotai/Kimi-K2-Instruct",
    display_name="Kimi K2",
    provider="Moonshot AI (Together AI)",
    role="dramatist",
    cost_tier="low",
    env_key="TOGETHER_API_KEY",
    context_window=131072,  # 128K
)


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

@dataclass
class ConclaveConfig:
    """Resolved configuration for a Tribunal session."""
    depth: DepthConfig
    available_advocates: list[ModelDef]
    bishops: list[ModelDef]
    priests: list[ModelDef]
    deacons: list[ModelDef]
    log_dir: str
    max_cost: float
    together_api_key: Optional[str] = None
    fireworks_api_key: Optional[str] = None


def load_config(depth_name: str = "THOROUGH") -> ConclaveConfig:
    """Load configuration from environment variables."""

    # Resolve depth
    depth_name = os.environ.get("CONCLAVE_DEFAULT_DEPTH", depth_name).upper()
    if depth_name not in DEPTH_LEVELS:
        raise ValueError(f"Unknown depth: {depth_name}. Must be one of: {', '.join(DEPTH_LEVELS)}")
    depth = DEPTH_LEVELS[depth_name]

    # Check which API keys are available
    together_key = os.environ.get("TOGETHER_API_KEY")
    fireworks_key = os.environ.get("FIREWORKS_API_KEY")

    if not together_key:
        raise EnvironmentError(
            "TOGETHER_API_KEY is required. It provides access to all judge models. "
            "Set it in your environment: export TOGETHER_API_KEY='...'"
        )

    # Filter advocates to those with available API keys
    available_advocates = []
    for adv in ADVOCATES:
        if os.environ.get(adv.env_key):
            available_advocates.append(adv)

    # Open-model advocate backfill pool (Together AI models that are NOT judges)
    open_advocate_pool = [
        ModelDef(
            id="advocate-qwen3-235b",
            litellm_model="together_ai/Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
            display_name="Qwen 3 235B",
            provider="Qwen (Together AI)",
            role="advocate",
            cost_tier="low",
            env_key="TOGETHER_API_KEY",
        ),
        ModelDef(
            id="advocate-minimax-m1",
            litellm_model="together_ai/MiniMaxAI/MiniMax-M1-40k",
            display_name="MiniMax M1",
            provider="MiniMax (Together AI)",
            role="advocate",
            cost_tier="low",
            env_key="TOGETHER_API_KEY",
        ),
        ModelDef(
            id="advocate-kimi-k2",
            litellm_model="together_ai/moonshotai/kimi-k2-instruct",
            display_name="Kimi K2 Instruct",
            provider="Moonshot AI (Together AI)",
            role="advocate",
            cost_tier="low",
            env_key="TOGETHER_API_KEY",
        ),
        ModelDef(
            id="advocate-llama4",
            litellm_model="together_ai/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            display_name="Llama 4 Maverick",
            provider="Meta (Together AI)",
            role="advocate",
            cost_tier="low",
            env_key="TOGETHER_API_KEY",
        ),
    ]

    # Backfill: if we don't have enough frontier advocates, pull from open pool
    if len(available_advocates) < depth.advocates and together_key:
        # Exclude any open models already in available list
        existing_ids = {a.id for a in available_advocates}
        backfill = [m for m in open_advocate_pool if m.id not in existing_ids]
        random.shuffle(backfill)
        needed = depth.advocates - len(available_advocates)
        available_advocates.extend(backfill[:needed])

    # Filter deacons by available API keys — Cerebras deacons are silently skipped
    # if CEREBRAS_API_KEY is not set, falling back to Together AI deacons only.
    available_deacons = [d for d in DEACONS if os.environ.get(d.env_key)]
    if not available_deacons:
        available_deacons = list(DEACONS)  # last resort: try all (some will fail at runtime)

    return ConclaveConfig(
        depth=depth,
        available_advocates=available_advocates,
        bishops=BISHOPS,
        priests=PRIESTS,
        deacons=available_deacons,
        log_dir=os.environ.get("TRIBUNAL_OUTPUT_DIR", "./conclave-sessions"),
        max_cost=float(os.environ.get("CONCLAVE_MAX_COST", "5.00")),
        together_api_key=together_key,
        fireworks_api_key=fireworks_key,
    )
