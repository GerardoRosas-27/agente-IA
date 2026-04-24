"""
Puente de LENGUAJE: el LLM no conversa con el humano; solo etiqueta el texto
en valencias. Solo inferencia vía API LM Studio (OpenAI).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import numpy as np

from fly_world import FlyWorld
from llm_api_client import get_resolved_model, parse_assistant_message, remote_openai_chat

SYSTEM = (
    "Eres un modulo de ENRUTAMIENTO sensorial. No escribes respuestas al humano. "
    "Solo clasificas el MENSAJE en cuatro intensidades 0.0-1.0.\n"
    "Responde UNA sola linea JSON valida, sin markdown:\n"
    '{"hostil":0.0,"amable":0.0,"conversacion":0.0,"curiosidad":0.0}\n'
    "hostil: insultos, amenazas, ordenes agresivas, sarcasmo cruel.\n"
    "amable: carino, agradecimiento, calma, apoyo.\n"
    "conversacion: hablarle a la mosca, contar cosas, saludo neutro con sustancia.\n"
    "curiosidad: preguntas (?), 'que', 'como', 'por que', tono investigador."
)


def _parse_scores(text: str) -> dict[str, float] | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[^{}]+\}", text)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for k in ("hostil", "amable", "conversacion", "curiosidad"):
        out[k] = float(np.clip(float(d.get(k, 0.0)), 0.0, 1.0))
    return out


@dataclass
class BridgeResult:
    scores: dict[str, float]
    source: str
    raw_snippet: str


class LanguageBridge:
    def __init__(self, model: str = ""):
        self._model = (model or "").strip()

    def _model_id(self) -> str:
        return get_resolved_model(self._model)

    def classify(self, user_text: str) -> BridgeResult:
        t = user_text.strip()
        if not t:
            return BridgeResult(
                scores={k: 0.0 for k in ("hostil", "amable", "conversacion", "curiosidad")},
                source="vacio",
                raw_snippet="",
            )
        r = remote_openai_chat(
            self._model_id(),
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": t},
            ],
            options={"temperature": 0.05, "num_predict": 120},
        )
        raw = parse_assistant_message(r) or ""
        if not raw.strip():
            raise RuntimeError(
                "LM Studio no devolvió texto al puente de lenguaje. Revisa .env y el servidor."
            )
        parsed = _parse_scores(raw)
        if not parsed:
            raise RuntimeError(
                f"El modelo no devolvió JSON de puntuaciones. Recorte: {raw[:200]!r}"
            )
        return BridgeResult(scores=parsed, source="lm_studio", raw_snippet=raw[:160])

    def apply_to_world(self, world: FlyWorld, br: BridgeResult) -> None:
        s = br.scores
        world.inject_language_stimulus(
            hostil=s["hostil"],
            amable=s["amable"],
            conversacion=s["conversacion"],
            curiosidad=s["curiosidad"],
            gain=0.7,
        )
