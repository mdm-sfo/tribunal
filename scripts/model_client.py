"""
Conclave model client — unified API dispatch via LiteLLM.

Handles:
- Parallel fan-out to multiple models (ThreadPoolExecutor)
- Per-model timeouts
- Cost tracking per call
- Anonymization (alias assignment + shuffle)
- Graceful failure (model that fails is excluded, not retried forever)

Design principle: "Optimize the engine, not the judge."
This is plumbing — fast, reliable, transparent. No routing decisions.
"""

import os
import time
import random
import string
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import litellm

from config_loader import ModelDef
from progress import Progress


# Suppress LiteLLM's own logging — we use our own progress display
litellm.suppress_debug_info = True
litellm.set_verbose = False


@dataclass
class ModelResponse:
    """Result from a single model call."""
    model_id: str
    display_name: str
    provider: str
    role: str
    alias: str                          # anonymized alias (e.g. "Advocate-A")
    content: Optional[str] = None       # the response text
    elapsed: float = 0.0                # wall clock seconds
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0                   # USD cost of this call
    status: str = "pending"             # "success" | "failed" | "timeout"
    error: Optional[str] = None


def _set_api_keys():
    """
    Ensure LiteLLM can find the right API keys.
    LiteLLM reads from env vars automatically, but Together AI
    needs to be explicitly mapped.
    """
    together_key = os.environ.get("TOGETHER_API_KEY")
    if together_key:
        os.environ["TOGETHERAI_API_KEY"] = together_key

    fireworks_key = os.environ.get("FIREWORKS_API_KEY")
    if fireworks_key:
        os.environ["FIREWORKS_AI_API_KEY"] = fireworks_key

    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    if perplexity_key:
        os.environ["PERPLEXITYAI_API_KEY"] = perplexity_key


def call_model(
    model: ModelDef,
    system_prompt: str,
    user_prompt: str,
    alias: str,
    timeout: int = 120,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    progress: Optional[Progress] = None,
) -> ModelResponse:
    """
    Call a single model via LiteLLM and return a ModelResponse.
    This is the unit of work dispatched to ThreadPoolExecutor.
    """
    _set_api_keys()

    start = time.time()
    resp = ModelResponse(
        model_id=model.id,
        display_name=model.display_name,
        provider=model.provider,
        role=model.role,
        alias=alias,
    )

    if progress:
        progress.info(f"→ Dispatching to {model.display_name} ({alias})...")

    try:
        # Build messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Web search tools (injected for models that support it)
        # Gemini: Google Search grounding via tool definition
        # Perplexity Sonar: always searches — no tool injection needed
        search_tools = [{"google_search": {}}] if model.web_search else None

        # Reasoning models need special handling:
        #   - max_completion_tokens instead of max_tokens (reasoning eats budget)
        #   - no temperature (fixed at 1.0 for reasoning models)
        #   - higher token budget (16k minimum) to allow reasoning room
        if model.is_reasoning:
            kwargs = dict(
                model=model.litellm_model,
                messages=messages,
                max_completion_tokens=max(max_tokens * 4, 16384),
                timeout=max(timeout, 180),  # reasoning models need at least 3 min
                num_retries=0,  # no retry for reasoning (too slow)
            )
            if search_tools:
                kwargs["tools"] = search_tools
            response = litellm.completion(**kwargs)
        else:
            kwargs = dict(
                model=model.litellm_model,
                messages=messages,
                max_tokens=max_tokens,
                timeout=timeout,
                num_retries=1,
            )
            if search_tools:
                kwargs["tools"] = search_tools
            try:
                response = litellm.completion(temperature=temperature, **kwargs)
            except litellm.UnsupportedParamsError:
                if progress:
                    progress.info(f"  {model.display_name}: retrying without temperature")
                response = litellm.completion(**kwargs)

        resp.elapsed = time.time() - start
        resp.content = response.choices[0].message.content
        resp.status = "success"

        # Token usage
        usage = response.usage
        if usage:
            resp.input_tokens = usage.prompt_tokens or 0
            resp.output_tokens = usage.completion_tokens or 0

        # Cost tracking (LiteLLM provides this)
        try:
            resp.cost = litellm.completion_cost(completion_response=response)
        except Exception:
            resp.cost = 0.0

        if progress:
            progress.model_success(
                model.display_name,
                resp.elapsed,
                resp.output_tokens,
                resp.cost,
            )

    except Exception as e:
        resp.elapsed = time.time() - start
        resp.status = "failed"
        resp.error = str(e)

        if progress:
            progress.model_fail(model.display_name, resp.elapsed, str(e)[:100])

    return resp


def fan_out(
    models: list[ModelDef],
    system_prompt: str,
    user_prompt: str,
    aliases: list[str],
    timeout: int = 120,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    progress: Optional[Progress] = None,
) -> list[ModelResponse]:
    """
    Dispatch to multiple models in parallel. Returns list of ModelResponse
    (successful and failed). Results are shuffled to prevent ordering bias.
    """
    _set_api_keys()

    if len(aliases) < len(models):
        raise ValueError(f"Need {len(models)} aliases, got {len(aliases)}")

    results: list[ModelResponse] = []

    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        futures = {
            executor.submit(
                call_model,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                alias=aliases[i],
                timeout=timeout,
                temperature=temperature,
                max_tokens=max_tokens,
                progress=progress,
            ): model.id
            for i, model in enumerate(models)
        }

        for future in as_completed(futures):
            model_id = futures[future]
            try:
                result = future.result(timeout=timeout + 210)  # extra buffer for reasoning models
                results.append(result)
            except Exception as e:
                # Future itself failed (shouldn't happen, but be safe)
                results.append(ModelResponse(
                    model_id=model_id,
                    display_name=model_id,
                    provider="unknown",
                    role="unknown",
                    alias="unknown",
                    status="failed",
                    error=f"Future error: {e}",
                    elapsed=0.0,
                ))

    # Shuffle results to prevent ordering bias
    random.shuffle(results)

    return results


def fan_out_multi(
    calls: list[dict],
    progress: Optional[Progress] = None,
) -> list[ModelResponse]:
    """
    Dispatch multiple DIFFERENT prompts to different models in parallel.
    Each call is a dict with keys: model, system_prompt, user_prompt, alias,
    timeout, temperature, max_tokens.
    Returns list of ModelResponse (shuffled).
    """
    _set_api_keys()
    results: list[ModelResponse] = []

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = {
            executor.submit(
                call_model,
                model=c["model"],
                system_prompt=c["system_prompt"],
                user_prompt=c["user_prompt"],
                alias=c["alias"],
                timeout=c.get("timeout", 120),
                temperature=c.get("temperature", 0.7),
                max_tokens=c.get("max_tokens", 4096),
                progress=progress,
            ): c["alias"]
            for c in calls
        }

        max_timeout = max(c.get("timeout", 120) for c in calls)
        for future in as_completed(futures):
            alias = futures[future]
            try:
                result = future.result(timeout=max_timeout + 210)
                results.append(result)
            except Exception as e:
                results.append(ModelResponse(
                    model_id="unknown", display_name="unknown",
                    provider="unknown", role="unknown", alias=alias,
                    status="failed", error=f"Future error: {e}", elapsed=0.0,
                ))

    random.shuffle(results)
    return results


def generate_aliases(count: int, role_prefix: str = "Advocate") -> list[str]:
    """Generate anonymized aliases: Advocate-A, Advocate-B, etc."""
    labels = list(string.ascii_uppercase[:count])
    random.shuffle(labels)
    return [f"{role_prefix}-{label}" for label in labels]


def successful_responses(responses: list[ModelResponse]) -> list[ModelResponse]:
    """Filter to only successful responses."""
    return [r for r in responses if r.status == "success"]


def total_cost(responses: list[ModelResponse]) -> float:
    """Sum cost across all responses."""
    return sum(r.cost for r in responses)


def total_tokens(responses: list[ModelResponse]) -> tuple[int, int]:
    """Sum (input_tokens, output_tokens) across all responses."""
    return (
        sum(r.input_tokens for r in responses),
        sum(r.output_tokens for r in responses),
    )
