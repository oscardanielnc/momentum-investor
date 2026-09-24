"""
Optional natural-language explanation of each rebalance.

Turns the structured rationale produced by allocator.rationale() into two or three plain
sentences for the dashboard. Uses plain REST for OpenAI-compatible providers, so no extra
dependency is needed.

Provider: AI_ENGINE (deepseek | glm | claude, default deepseek) with the matching API key.
Fail-safe by design: with no key, or if the API call fails, explain_prose() returns None and
the orchestrator stores the deterministic markdown instead. The prose is never a single point
of failure.
"""
from __future__ import annotations
import os, logging
import requests
from _env import load_env
load_env()

log = logging.getLogger("investor.ai_explain")

_BASES = {"deepseek": "https://api.deepseek.com", "glm": "https://api.z.ai/api/openai/v1"}
_MODELS = {"deepseek": "deepseek-chat", "glm": "glm-4.7-flash"}

SYSTEM = (
    "You are the analyst for the 'investor' paper-trading robot. In 2-3 short, direct, honest "
    "sentences, explain why the robot holds this portfolio today. Mention the holdings and their "
    "sectors, what changed since the previous cycle, and the regime (number of sectors, trailing "
    "stop). Do not promise returns or give financial advice. Sober reporting tone."
)


def _engine_key():
    eng = os.environ.get("AI_ENGINE", "deepseek").lower()
    if eng == "deepseek":
        return eng, os.environ.get("DEEPSEEK_API_KEY", "")
    if eng == "glm":
        return eng, os.environ.get("GLM_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
    if eng == "claude":
        return eng, os.environ.get("ANTHROPIC_API_KEY", "")
    return eng, ""


def _build_prompt(struct: dict) -> str:
    ctx = struct.get("context", {})
    pos = struct.get("positions", [])
    removed = struct.get("removed", [])
    lines = [f"- {p['symbol']} ({p.get('sector','?')}): {p.get('why','')} [{p.get('change','')}]" for p in pos]
    return (
        f"Date: {ctx.get('asof')}\n"
        f"Portfolio (top-{len(pos)}, equal weight, {ctx.get('n_sectors','?')} sectors, "
        f"trailing stop {ctx.get('trail_pct','?')}%):\n" + "\n".join(lines) +
        (f"\nDropped from the top 5: {', '.join(removed)}" if removed else "") +
        "\n\nWrite the explanation in 2-3 sentences."
    )


def explain_prose(struct: dict, max_tokens: int = 220) -> str | None:
    """Return the AI-written explanation, or None if no key is set or the call fails."""
    eng, key = _engine_key()
    if not key:
        log.info("[ai_explain] no key for %s, using deterministic fallback", eng)
        return None
    try:
        if eng == "claude":
            return _claude(key, struct, max_tokens)
        return _openai_compat(eng, key, struct, max_tokens)
    except Exception as e:
        log.warning("[ai_explain] %s failed: %s, using fallback", eng, e)
        return None


def _openai_compat(eng, key, struct, max_tokens):
    base = os.environ.get(f"{eng.upper()}_BASE_URL", _BASES.get(eng, _BASES["deepseek"]))
    model = os.environ.get("AI_MODEL_CHEAP") or _MODELS.get(eng, _MODELS["deepseek"])
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.4, "stream": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": _build_prompt(struct)}]}
    r = requests.post(base.rstrip("/") + "/chat/completions",
                      json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=40)
    if r.status_code != 200:
        log.warning("[ai_explain] HTTP %s: %s", r.status_code, r.text[:150]); return None
    txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
    return txt or None


def _claude(key, struct, max_tokens):
    import anthropic
    c = anthropic.Anthropic(api_key=key)
    m = c.messages.create(model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
                          max_tokens=max_tokens, system=SYSTEM,
                          messages=[{"role": "user", "content": _build_prompt(struct)}])
    return (m.content[0].text or "").strip() or None
