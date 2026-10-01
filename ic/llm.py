"""
ic/llm.py — the single choke point for every LLM call in Incident Commander.

Design goals:
  * ONE call shape for the whole system: `json_call(system, user, schema_model)`.
    Every agent returns strict JSON validated against a Pydantic model.
  * ONE place to swap providers. Iterate for free on OpenRouter, run the harness on a
    pinned free model for reproducible numbers, do the final quality pass on Claude —
    without touching any agent code.
  * Provider swap is just base_url + model + key. We use the Anthropic Messages format
    throughout, because OpenRouter exposes an Anthropic-compatible /messages endpoint,
    so the same client works for both OpenRouter-free and Claude direct.
  * Open models: the "vllm" provider speaks the OpenAI-compatible
    /v1/chat/completions API that vLLM serves (e.g. on AMD Developer Cloud).
  * Token accounting: every successful call adds to USAGE, so experiments can
    report tokens per incident (see reset_usage / USAGE).
  * JSON-contract failover: if a (weak, free) model returns unparseable or
    schema-invalid JSON, retry once, then fail over to the next provider in the chain.
    This keeps a flaky free model from silently corrupting your ablation numbers.

You never pass a key to anyone. Keys are read from the local environment (.env).

NOTE ON base_url: the Anthropic SDK appends "/v1/messages" to base_url, so for
OpenRouter the base must be "https://openrouter.ai/api" (→ /api/v1/messages).
Using ".../api/v1" would double the prefix to ".../api/v1/v1/messages" (404). This was
verified against anthropic SDK 0.116 `_prepare_url`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

try:
    from dotenv import load_dotenv
    load_dotenv()  # pulls OPENROUTER_API_KEY / ANTHROPIC_API_KEY / IC_* from .env
except Exception:  # python-dotenv optional; env may already be populated
    pass

T = TypeVar("T", bound=BaseModel)

# base ends at /api on purpose — the SDK adds /v1/messages (see module docstring).
OPENROUTER_BASE_URL = "https://openrouter.ai/api"


# --------------------------------------------------------------------------- #
# Provider configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    base_url: str | None      # None => Anthropic's own default endpoint
    api_key_env: str
    api: str = "anthropic"    # "anthropic" (Messages) | "openai" (chat/completions)

    def has_key(self) -> bool:
        if self.name == "vllm":
            return bool(self.base_url)  # vLLM accepts any key; the URL is what matters
        key = os.environ.get(self.api_key_env)
        return bool(key) and not key.endswith("REPLACE_ME")

    def client(self):
        from anthropic import Anthropic  # lazy: SDK not needed for the offline engine
        key = os.environ.get(self.api_key_env)
        if not key or key.endswith("REPLACE_ME"):
            raise RuntimeError(
                f"[{self.name}] missing/placeholder key in env var {self.api_key_env}. "
                f"Put your real key in .env (see .env.example)."
            )
        kwargs = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return Anthropic(**kwargs)


def _pinned_slug() -> str:
    return os.environ.get("IC_PINNED_MODEL", "openai/gpt-oss-120b:free")


def _anthropic_model() -> str:
    return os.environ.get("IC_ANTHROPIC_MODEL", "claude-sonnet-5")


def _provider(name: str) -> Provider:
    if name == "openrouter_free":
        # Resilient auto-router. Use WHILE TUNING PROMPTS. Not for calibration numbers
        # (it picks a random model per call, so Brier/accuracy would be a grab-bag).
        return Provider("openrouter_free", "openrouter/free", OPENROUTER_BASE_URL, "OPENROUTER_API_KEY")
    if name == "openrouter_pinned":
        # A single fixed free slug. Use FOR THE HARNESS so every incident is scored by
        # the same model and the numbers mean something. Verify it's still ":free".
        return Provider("openrouter_pinned", _pinned_slug(), OPENROUTER_BASE_URL, "OPENROUTER_API_KEY")
    if name == "anthropic":
        # Claude direct. Use for the FINAL quality pass — confirm the agent-disagreement
        # and probe mechanism survive on a strong model before you stake the demo on it.
        return Provider("anthropic", _anthropic_model(), None, "ANTHROPIC_API_KEY")
    if name == "vllm":
        # Open model served by vLLM (OpenAI-compatible). Base URL ends at /v1.
        return Provider("vllm", os.environ.get("IC_VLLM_MODEL", "Qwen/Qwen3-32B"),
                        os.environ.get("IC_VLLM_BASE_URL"), "IC_VLLM_API_KEY", api="openai")
    if name.startswith("openrouter_oai:"):
        # OpenRouter via its OpenAI-compatible endpoint. Reasoning models (Nemotron,
        # Qwen3.x, Dots…) return separate reasoning there instead of breaking the
        # Anthropic-format adapter. Used by the benchmark (ic/bench).
        return Provider(name, name.split(":", 1)[1], "https://openrouter.ai/api/v1",
                        "OPENROUTER_API_KEY", api="openai")
    raise ValueError(f"Unknown provider: {name!r}")


# Failover order per active provider. If the primary breaks the JSON contract, we walk
# down the chain rather than crashing mid-investigation.
_FAILOVER = {
    "openrouter_free":   ["openrouter_free", "openrouter_pinned", "anthropic"],
    "openrouter_pinned": ["openrouter_pinned", "openrouter_free", "anthropic"],
    "anthropic":         ["anthropic"],  # no silent fallback off Claude during a graded run
    "vllm":              ["vllm"],       # experiments must stay on one model
}


def active_chain() -> list[Provider]:
    primary = os.environ.get("IC_PROVIDER", "openrouter_free")
    if primary == "openrouter_models":
        # IC_OPENROUTER_MODELS="model-a,model-b,model-c": try in order (development).
        # With IC_STRICT_PROVIDER=1 only the first is used (experiments).
        models = [m.strip() for m in os.environ.get("IC_OPENROUTER_MODELS", "").split(",") if m.strip()]
        chain = [_provider(f"openrouter_oai:{m}") for m in models]
        return chain[:1] if os.environ.get("IC_STRICT_PROVIDER") == "1" else chain
    if os.environ.get("IC_STRICT_PROVIDER") == "1":  # experiments: one model, no failover
        return [_provider(primary)]
    return [_provider(n) for n in _FAILOVER.get(primary, [primary])]


# --------------------------------------------------------------------------- #
# Availability gate — lets the offline engine stay the default
# --------------------------------------------------------------------------- #
def llm_enabled() -> bool:
    """Master switch. Agents use the model only when IC_USE_LLM=1 AND at least one
    provider in the active chain has a real key. Otherwise the deterministic analytical
    engine runs — so the repo works fully offline with no key."""
    if os.environ.get("IC_USE_LLM") != "1":
        return False
    return any(p.has_key() for p in active_chain())


# --------------------------------------------------------------------------- #
# Token accounting
# --------------------------------------------------------------------------- #
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "contract_failures": 0,
         "answered_by": []}


def reset_usage() -> dict:
    """Zero the counters and return the previous totals."""
    prev = dict(USAGE)
    USAGE.update(calls=0, input_tokens=0, output_tokens=0, contract_failures=0, answered_by=[])
    return prev


def _record(input_tokens: int, output_tokens: int) -> None:
    USAGE["calls"] += 1
    USAGE["input_tokens"] += input_tokens or 0
    USAGE["output_tokens"] += output_tokens or 0


def _reasoning_opts(provider: Provider) -> dict:
    """Reasoning control for OpenRouter models. Default: ask for NO reasoning —
    our prompts need a structured answer, not a long think. Measured on the
    19-service AIOpsLab prompt, an explicit effort="low" switched reasoning ON for
    Laguna S 2.1 (10k reasoning tokens, 181 s vs 551 tokens, 11 s off).
    IC_REASONING_EFFORT=low|medium|high opts back in; =default sends nothing."""
    if not provider.name.startswith("openrouter_oai:"):
        return {}
    effort = os.environ.get("IC_REASONING_EFFORT", "off")
    if effort == "default":
        return {}
    if effort == "off":
        return {"reasoning": {"enabled": False}}
    return {"reasoning": {"effort": effort}}


def _openai_chat(provider: Provider, system: str, user: str,
                 max_tokens: int, temperature: float) -> str:
    """One call to an OpenAI-compatible /chat/completions endpoint (vLLM)."""
    import httpx  # ships with the anthropic SDK
    key = os.environ.get(provider.api_key_env) or "EMPTY"
    r = httpx.post(
        f"{provider.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": provider.model, "max_tokens": max_tokens, "temperature": temperature,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              **_reasoning_opts(provider)},
        timeout=float(os.environ.get("IC_LLM_TIMEOUT", "120")),
    )
    r.raise_for_status()
    body = r.json()
    u = body.get("usage") or {}
    _record(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return body["choices"][0]["message"]["content"] or ""


def _strip_think(s: str) -> str:
    """Reasoning models (Qwen3, DeepSeek-R1) may prefix output with <think>…</think>."""
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    return s


# --------------------------------------------------------------------------- #
# The one call everything uses
# --------------------------------------------------------------------------- #
def _extract_text(resp) -> str:
    """Concatenate text blocks from an Anthropic-format response."""
    parts = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _strip_fences(s: str) -> str:
    """Weak models love to wrap JSON in ```json fences. Strip them."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip().strip("`").strip()


def _parse_into(schema: Type[T], raw: str) -> T:
    """Parse model text into the Pydantic schema, or raise. Open models often wrap
    the JSON in a sentence of prose; fall back to the outermost {...} span."""
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    return schema.model_validate(data)


def json_call(
    system: str,
    user: str,
    schema: Type[T],
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> T:
    """
    Call the active provider chain and return an instance of `schema`.

    - Appends a hard instruction to emit ONLY JSON matching the schema.
    - Retries once on the same provider (parse/validation failure).
    - Fails over to the next provider in the chain if the retry also fails.
    - Raises RuntimeError only if EVERY provider fails the contract.
    """
    schema_hint = (
        "Respond with a SINGLE JSON object and nothing else — no prose, no markdown, "
        "no code fences. It must validate against this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )
    full_system = f"{system}\n\n{schema_hint}"

    errors: list[str] = []
    for provider in active_chain():
        client = None
        if provider.api == "anthropic":
            try:
                client = provider.client()
            except RuntimeError as e:
                errors.append(str(e))
                continue  # key missing for this provider; try the next
        elif not provider.has_key():
            errors.append(f"[{provider.name}] missing base URL or API key")
            continue

        for attempt in (1, 2):  # one retry on the same provider
            try:
                if provider.api == "openai":
                    raw = _openai_chat(provider, full_system, user, max_tokens, temperature)
                else:
                    resp = client.messages.create(
                        model=provider.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=full_system,
                        messages=[{"role": "user", "content": user}],
                    )
                    _record(getattr(resp.usage, "input_tokens", 0),
                            getattr(resp.usage, "output_tokens", 0))
                    raw = _extract_text(resp)
                parsed = _parse_into(schema, _strip_think(raw))
                USAGE["answered_by"].append(provider.model)
                return parsed
            except (json.JSONDecodeError, ValidationError) as e:
                USAGE["contract_failures"] += 1
                errors.append(f"[{provider.name} attempt {attempt}] bad JSON: {e}")
                continue
            except Exception as e:  # network / rate limit / auth — move to next provider
                errors.append(f"[{provider.name} attempt {attempt}] call failed: {e}")
                break

    raise RuntimeError(
        "json_call: every provider failed the JSON contract.\n  " + "\n  ".join(errors)
    )


# --------------------------------------------------------------------------- #
# Free-text multi-turn call — used only by the pure-LLM benchmark baselines,
# which must talk to the environment the way a ReAct agent does.
# --------------------------------------------------------------------------- #
def chat(system: str, messages: list[dict], *, max_tokens: int = 2000,
         temperature: float = 0.2) -> str:
    """messages: [{"role": "user"|"assistant", "content": str}, ...]. No failover:
    a baseline must stay on one model, so the first provider in the chain is used."""
    provider = active_chain()[0]
    if provider.api == "openai":
        import httpx
        key = os.environ.get(provider.api_key_env) or "EMPTY"
        r = httpx.post(
            f"{provider.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": provider.model, "max_tokens": max_tokens, "temperature": temperature,
                  "messages": [{"role": "system", "content": system}, *messages],
                  **_reasoning_opts(provider)},
            timeout=float(os.environ.get("IC_LLM_TIMEOUT", "120")),
        )
        r.raise_for_status()
        body = r.json()
        u = body.get("usage") or {}
        _record(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        return _strip_think(body["choices"][0]["message"]["content"] or "")
    resp = provider.client().messages.create(
        model=provider.model, max_tokens=max_tokens, temperature=temperature,
        system=system, messages=messages)
    _record(getattr(resp.usage, "input_tokens", 0), getattr(resp.usage, "output_tokens", 0))
    return _strip_think(_extract_text(resp))


# --------------------------------------------------------------------------- #
# Prompt helper — evidence enters as delimited, typed, hashed data, never as an
# instruction (prompt-injection containment; see README guardrails).
# --------------------------------------------------------------------------- #
def evidence_block(items) -> str:
    lines = []
    for e in items:
        lines.append(
            f"<evidence source_uri={e.source_uri!r} type={e.source_type!r} "
            f"hash={e.content_hash()[:12]} measures={e.measures}>\n"
            f"{json.dumps(e.payload, separators=(',', ':'))}\n</evidence>"
        )
    return "\n".join(lines)
