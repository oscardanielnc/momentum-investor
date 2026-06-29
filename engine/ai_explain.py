"""
investor — Justificación en PROSA por IA (DeepSeek). Convierte el "por qué" estructurado de cada
redistribución en 2-3 frases naturales, honestas y en español para la capa pública del dashboard.

Self-contained (REST, sin dependencias nuevas) → desplegable en la VM sin arrastrar opportunity_alert.
Motor por env AI_ENGINE (default deepseek). Clave DEEPSEEK_API_KEY. BLINDADO: si no hay clave o la
API falla, devuelve None y el orquestador usa el texto determinista (la prosa es un "plus", nunca un
punto único de fallo).
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
    "Eres el analista del robot de inversión 'investor'. Explica de forma DIRECTA, honesta y breve "
    "(2-3 frases, español) por qué el robot tiene esta cartera hoy. Menciona los líderes y sus sectores, "
    "qué cambió respecto al ciclo anterior y el régimen (cuántos sectores, trailing stop). NO hagas "
    "promesas de rentabilidad ni des consejo financiero. Tono sobrio de reporte, sin exagerar."
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


def _build_prompt(struct: dict, meta: dict) -> str:
    ctx = struct.get("context", {})
    pos = struct.get("positions", [])
    removed = struct.get("removed", [])
    líneas = [f"- {p['symbol']} ({p.get('sector','?')}): {p.get('why','')} [{p.get('change','')}]" for p in pos]
    return (
        f"Fecha: {ctx.get('asof')}\n"
        f"Cartera (top-{len(pos)}, equiponderada, {ctx.get('n_sectors','?')} sectores, "
        f"trailing stop {ctx.get('trail_pct','?')}%):\n" + "\n".join(líneas) +
        (f"\nSalieron del top-5: {', '.join(removed)}" if removed else "") +
        "\n\nEscribe la explicación en 2-3 frases."
    )


def explain_prose(struct: dict, meta: dict, max_tokens: int = 220) -> str | None:
    """Devuelve la prosa de IA, o None si no hay clave / falla la API (→ fallback determinista)."""
    eng, key = _engine_key()
    if not key:
        log.info("[ai_explain] sin clave para %s → fallback determinista", eng)
        return None
    try:
        if eng == "claude":
            return _claude(key, struct, meta, max_tokens)
        return _openai_compat(eng, key, struct, meta, max_tokens)
    except Exception as e:
        log.warning("[ai_explain] %s falló: %s → fallback", eng, e)
        return None


def _openai_compat(eng, key, struct, meta, max_tokens):
    base = os.environ.get(f"{eng.upper()}_BASE_URL", _BASES.get(eng, _BASES["deepseek"]))
    model = os.environ.get("AI_MODEL_CHEAP") or _MODELS.get(eng, _MODELS["deepseek"])
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.4, "stream": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": _build_prompt(struct, meta)}]}
    r = requests.post(base.rstrip("/") + "/chat/completions",
                      json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=40)
    if r.status_code != 200:
        log.warning("[ai_explain] HTTP %s: %s", r.status_code, r.text[:150]); return None
    txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
    return txt or None


def _claude(key, struct, meta, max_tokens):
    import anthropic
    c = anthropic.Anthropic(api_key=key)
    m = c.messages.create(model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
                          max_tokens=max_tokens, system=SYSTEM,
                          messages=[{"role": "user", "content": _build_prompt(struct, meta)}])
    return (m.content[0].text or "").strip() or None
