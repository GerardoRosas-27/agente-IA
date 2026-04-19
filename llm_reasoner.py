"""
Razonamiento interno con LLM local (pesos INMOVIBLES).

No se entrena el modelo de lenguaje: solo inferencia (Ollama carga pesos
fijos). La salida es un conjunto pequeno de INTENCIONES ABSTRACTAS que se
codifican como vector one-hot y se inyectan en la capa plastica.

Modelo recomendado (ligero, Google Gemma en Ollama):
    ollama pull gemma3:270m
    # alternativas: gemma2:2b, gemma2:1b

Si Ollama no esta disponible, se usa un clasificador heuristico determinista
basado en los mismos sensores que ve la mosca (sin "magia").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np

from fly_world import FlyWorld

INTENTS = [
    "buscar_comida",
    "huir_peligro",
    "buscar_refugio",
    "explorar_territorio",
    "ahorrar_energia",
    "re_evaluar",
    "buscar_espacio_abierto",
]

_INTENT_INDEX = {name: i for i, name in enumerate(INTENTS)}

SYSTEM_PROMPT = (
    "Eres el nucleo de razonamiento INTERNO de una mosca (no eres chat publico). "
    "No inventes texto libre: responde SOLO un JSON en una sola linea, sin markdown.\n"
    'Formato exacto: {"intent":"<NOMBRE>","confidence":0.0}\n'
    "NOMBRE debe ser EXACTAMENTE uno de estos: "
    + ", ".join(INTENTS)
    + ".\n"
    "confidence entre 0.0 y 1.0 (tu certeza).\n"
    "Criterios: si hay peligro muy cerca prioriza huir_peligro; si energia baja "
    "y hay refugio cerca prioriza buscar_refugio; si hay comida cerca prioriza "
    "buscar_comida; si todo esta lejos prioriza explorar_territorio; si energia "
    "muy baja y no hay refugio cerca prioriza ahorrar_energia; si estas encerrado "
    "en bordes prioriza buscar_espacio_abierto; si la situacion es ambigua usa "
    "re_evaluar."
)


def _parse_json_line(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[^{}]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def heuristic_intent(world: FlyWorld) -> tuple[int, float, str]:
    """Replica reglas simples usando el mismo vector sensorial (11 dims)."""
    s = world.sense()
    food_p = float(s[0])
    threat_p = float(s[4])
    safe_p = float(s[8])
    energy = float(s[9])
    boundary = float(s[10])

    if boundary > 0.75:
        return _INTENT_INDEX["buscar_espacio_abierto"], 0.75, "heuristica"
    if threat_p > 0.82:
        return _INTENT_INDEX["huir_peligro"], 0.9, "heuristica"
    if energy < 0.35 and safe_p > 0.55:
        return _INTENT_INDEX["buscar_refugio"], 0.85, "heuristica"
    if energy < 0.25:
        return _INTENT_INDEX["ahorrar_energia"], 0.7, "heuristica"
    if food_p > 0.65:
        return _INTENT_INDEX["buscar_comida"], 0.8, "heuristica"
    if threat_p > 0.55 and food_p < 0.4:
        return _INTENT_INDEX["re_evaluar"], 0.55, "heuristica"
    return _INTENT_INDEX["explorar_territorio"], 0.6, "heuristica"


def intent_to_logits_bias(n_actions: int, intent_idx: int, confidence: float) -> np.ndarray:
    """
    Sesgo FIJO (no aprendible) de intencion -> acciones motoras.
    La capa plastica aprende a COMBINAR esto con el conectoma; el LLM no cambia.
    """
    bias = np.zeros(n_actions, dtype=np.float32)
    c = float(np.clip(confidence, 0.0, 1.0))
    scale = 0.55 * c

    def add(idxs: list[int], w: float) -> None:
        for i in idxs:
            if 0 <= i < n_actions:
                bias[i] += w * scale

    name = INTENTS[intent_idx]
    if name == "buscar_comida":
        add([0, 5, 8], 1.0)
    elif name == "huir_peligro":
        add([3, 5, 9, 10, 11, 12], 1.0)
    elif name == "buscar_refugio":
        add([5, 6, 7, 13, 14], 1.0)
    elif name == "explorar_territorio":
        add([5, 0, 9, 10, 15], 1.0)
    elif name == "ahorrar_energia":
        add([4, 15, 7], 1.0)
    elif name == "re_evaluar":
        add([5, 9, 10, 15], 1.0)
    elif name == "buscar_espacio_abierto":
        add([5, 6, 7, 8, 9, 10, 11, 12], 1.0)
    return bias


@dataclass
class ReasonerState:
    last_intent: str = "-"
    last_confidence: float = 0.0
    last_source: str = "-"
    last_raw: str = ""
    calls_ok: int = 0
    calls_fail: int = 0


class FrozenLLMReasoner:
    def __init__(
        self,
        model: str = "gemma3:270m",
        n_actions: int = 16,
        timeout: float = 120.0,
    ):
        self.model = model
        self.n_actions = n_actions
        self.timeout = timeout
        self.state = ReasonerState()
        self._ollama_chat: Callable | None = None
        try:
            import ollama as olm
            self._ollama_chat = olm.chat
        except ImportError:
            self._ollama_chat = None

    def _world_prompt(self, world: FlyWorld) -> str:
        s = world.sense()
        return (
            f"Estado interno (normalizado 0-1 salvo energia 0-1):\n"
            f"  cercania_comida={s[0]:.3f}\n"
            f"  cercania_peligro={s[4]:.3f}\n"
            f"  cercania_refugio_seguro={s[8]:.3f}\n"
            f"  energia={s[9]:.3f}\n"
            f"  cercania_borde_caja={s[10]:.3f}\n"
            f"  posicion=({world.fly_pos[0]:+.2f},{world.fly_pos[1]:+.2f},{world.fly_pos[2]:+.2f})\n"
            f"  pasos_simulados={world.steps}\n"
            "Elige intent y confidence en JSON de una linea."
        )

    def reason(self, world: FlyWorld) -> tuple[np.ndarray, np.ndarray]:
        """
        Retorna:
            intent_onehot: (len(INTENTS),) float32
            logits_bias:   (n_actions,) float32  (sin gradiente; se suma en no_grad)
        """
        if self._ollama_chat is None:
            idx, conf, src = heuristic_intent(world)
            self._record(idx, conf, src, "")
            vec = np.zeros(len(INTENTS), dtype=np.float32)
            vec[idx] = 1.0
            return vec, intent_to_logits_bias(self.n_actions, idx, conf)

        user = self._world_prompt(world)
        try:
            resp = self._ollama_chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                options={"temperature": 0.1, "num_predict": 80},
            )
            raw = resp["message"]["content"].strip()
            self.state.last_raw = raw[:200]
            data = _parse_json_line(raw)
            if not data:
                raise ValueError("JSON no parseable")
            name = str(data.get("intent", "")).strip()
            conf = float(data.get("confidence", 0.6))
            if name not in _INTENT_INDEX:
                raise ValueError(f"intent desconocido: {name}")
            idx = _INTENT_INDEX[name]
            self._record(idx, conf, "ollama", raw)
            vec = np.zeros(len(INTENTS), dtype=np.float32)
            vec[idx] = 1.0
            self.state.calls_ok += 1
            return vec, intent_to_logits_bias(self.n_actions, idx, conf)
        except Exception as exc:
            self.state.calls_fail += 1
            self.state.last_raw = str(exc)[:120]
            idx, conf, src = heuristic_intent(world)
            self._record(idx, conf, f"fallback({src})", "")
            vec = np.zeros(len(INTENTS), dtype=np.float32)
            vec[idx] = 1.0
            return vec, intent_to_logits_bias(self.n_actions, idx, conf)

    def _record(self, idx: int, conf: float, source: str, raw: str) -> None:
        self.state.last_intent = INTENTS[idx]
        self.state.last_confidence = float(conf)
        self.state.last_source = source
        if raw:
            self.state.last_raw = raw[:200]
