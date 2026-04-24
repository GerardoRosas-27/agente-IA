"""
Razonamiento interno vía LLM (LM Studio, API OpenAI; pesos fijos en el servidor).

La salida es intenciones abstractas (one-hot) inyectadas en la capa plástica.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np

from fly_world import FlyWorld
from llm_api_client import get_resolved_model, parse_assistant_message, remote_openai_chat

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


def intent_to_logits_bias(n_actions: int, intent_idx: int, confidence: float) -> np.ndarray:
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
        model: str = "",
        n_actions: int = 16,
        timeout: float = 120.0,
    ):
        self._model = (model or "").strip()
        self.n_actions = n_actions
        self.timeout = timeout
        self.state = ReasonerState()
        self._llm: Callable = remote_openai_chat

    def _model_id(self) -> str:
        return get_resolved_model(self._model)

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
        user = self._world_prompt(world)
        try:
            resp = self._llm(
                self._model_id(),
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                options={"temperature": 0.1, "num_predict": 80},
            )
            raw = (parse_assistant_message(resp) or "").strip()
            if not raw:
                raise ValueError("respuesta vacia del LLM")
            self.state.last_raw = raw[:200]
            data = _parse_json_line(raw)
            if not data:
                raise ValueError("JSON no parseable")
            name = str(data.get("intent", "")).strip()
            conf = float(data.get("confidence", 0.6))
            if name not in _INTENT_INDEX:
                raise ValueError(f"intent desconocido: {name}")
            idx = _INTENT_INDEX[name]
            self._record(idx, conf, "lm_studio", raw)
            vec = np.zeros(len(INTENTS), dtype=np.float32)
            vec[idx] = 1.0
            self.state.calls_ok += 1
            return vec, intent_to_logits_bias(self.n_actions, idx, conf)
        except Exception as exc:
            self.state.calls_fail += 1
            self.state.last_raw = str(exc)[:120]
            raise RuntimeError(
                f"Razonador LLM: {exc}. Comprueba LM Studio y .env (LLM_API_BASE_URL, LLM_MODEL)."
            ) from exc

    def _record(self, idx: int, conf: float, source: str, raw: str) -> None:
        self.state.last_intent = INTENTS[idx]
        self.state.last_confidence = float(conf)
        self.state.last_source = source
        if raw:
            self.state.last_raw = raw[:200]
