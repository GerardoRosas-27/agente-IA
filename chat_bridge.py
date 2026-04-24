"""
Puente de LENGUAJE: el LLM no conversa con el humano; solo etiqueta el texto
en valencias que el cerebro de la mosca puede 'oler' como estimulos.

Salida JSON (0.0 - 1.0 cada una):
  hostil         -> canal social peligro / hostilidad
  amable         -> canal refugio / seguridad afectiva
  conversacion   -> atencion / 'comida' metaforica (le hablan)
  curiosidad     -> pregunta o tono exploratorio (refuerza explorar)
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from fly_world import FlyWorld

# `ollama.chat` usa un Client() global con httpx.Client compartido; httpx no es
# thread-safe. El pipeline multi-agente llama al LLM desde un Thread de Tk →
# hay que usar un Client por hilo (mismo patrón recomendado por httpx).
_ollama_tls = threading.local()


def _thread_local_ollama_client() -> Any:
    """Un `httpx.Client` por hilo (Ollama Python >= 0.3)."""
    from ollama import Client

    client = getattr(_ollama_tls, "client", None)
    if client is None:
        client = Client()
        _ollama_tls.client = client
    return client


def local_llm_chat_call(
    model: str,
    messages: list,
    options: dict | None = None,
) -> Any:
    """
    Cliente Ollama local (daemon). Devuelve ChatResponse del paquete `ollama`
    o None si falla el import o la petición.

    No usar `ollama.chat` a nivel módulo desde hilos secundarios.
    """
    try:
        return _thread_local_ollama_client().chat(
            model=model,
            messages=messages,
            options=options or {},
            stream=False,
        )
    except Exception:
        return None


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


def heuristic_scores(text: str) -> dict[str, float]:
    low = text.lower()
    hostil = 0.0
    if any(w in low for w in (
        "odio", "matar", "idiota", "imbecil", "mierda", "calla", "shut",
        "hate", "stupid", "kill", "die", "loser", "basura", "hostil",
    )):
        hostil = 0.85
    elif any(w in low for w in ("mal", "feo", "no quiero", "vete", "deja")):
        hostil = 0.35

    amable = 0.0
    if any(w in low for w in (
        "gracias", "te quiero", "cariño", "amor", "bonito", "bien hecho",
        "love", "kind", "gentil", "tranquil", "descansa", "bravo",
    )):
        amable = 0.9
    elif any(w in low for w in ("hola", "buenos", "buenas", "ok", "vale", "bien")):
        amable = 0.25

    curiosidad = 0.0
    if "?" in text or any(w in low for w in (
        "qué ", "que ", "cómo", "como ", "por qué", "por que", "quién",
        "donde", "dónde", "cuándo", "what", "how", "why",
    )):
        curiosidad = min(1.0, 0.45 + 0.15 * low.count("?"))

    conversacion = 0.35
    if len(text.strip()) > 8:
        conversacion = min(1.0, 0.45 + 0.02 * len(text))
    if hostil > 0.5:
        conversacion *= 0.4
    if curiosidad > 0.5:
        conversacion = min(1.0, conversacion + 0.15)

    return {
        "hostil": float(hostil),
        "amable": float(amable),
        "conversacion": float(conversacion),
        "curiosidad": float(curiosidad),
    }


@dataclass
class BridgeResult:
    scores: dict[str, float]
    source: str
    raw_snippet: str


class LanguageBridge:
    def __init__(self, model: str = "gemma3:270m"):
        self.model = model
        self._ollama_available = False
        try:
            import ollama  # noqa: F401

            self._ollama_available = True
        except ImportError:
            self._ollama_available = False

    def classify(self, user_text: str) -> BridgeResult:
        t = user_text.strip()
        if not t:
            return BridgeResult(
                scores={k: 0.0 for k in ("hostil", "amable", "conversacion", "curiosidad")},
                source="vacio",
                raw_snippet="",
            )
        if not self._ollama_available:
            sc = heuristic_scores(t)
            return BridgeResult(scores=sc, source="heuristica", raw_snippet="")

        try:
            resp = _thread_local_ollama_client().chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": t},
                ],
                options={"temperature": 0.05, "num_predict": 120},
                stream=False,
            )
            raw = resp["message"]["content"].strip()
            parsed = _parse_scores(raw)
            if not parsed:
                raise ValueError("json")
            return BridgeResult(scores=parsed, source="ollama", raw_snippet=raw[:160])
        except Exception as exc:
            sc = heuristic_scores(t)
            return BridgeResult(
                scores=sc,
                source=f"heuristica(fallback:{exc})",
                raw_snippet=str(exc)[:80],
            )

    def apply_to_world(self, world: FlyWorld, br: BridgeResult) -> None:
        s = br.scores
        world.inject_language_stimulus(
            hostil=s["hostil"],
            amable=s["amable"],
            conversacion=s["conversacion"],
            curiosidad=s["curiosidad"],
            gain=0.7,
        )
