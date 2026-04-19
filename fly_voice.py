"""
Voz del CEREBRO (no del LLM): frases cortas derivadas solo de estado neural
y del mundo. Sin llamadas a modelo de lenguaje para generar la respuesta.

Las preguntas curiosas salen ahora de la red plastica + LLM articulador
(ver chat_sim_session y fly_question_llm).
"""
from __future__ import annotations

import numpy as np
import torch

from fly_world import FlyWorld


def _motor_summary(motor: torch.Tensor) -> tuple[float, float]:
    m = motor.detach().float().cpu().numpy()
    return float(np.mean(np.abs(m))), float(np.max(np.abs(m)))


def brain_utterance(
    world: FlyWorld,
    motor: torch.Tensor,
    last_action_name: str,
    reward_ema: float,
) -> str:
    """Una linea que resume lo que 'dice' el cerebro con numeros y accion."""
    mn, mx = _motor_summary(motor)
    s = world.sense()
    food = float(s[0])
    threat = float(s[4])
    safe = float(s[8])
    e = float(s[9])
    b = float(s[10])
    sh = float(s[11]) if len(s) > 11 else 0.0
    sa = float(s[12]) if len(s) > 12 else 0.0
    sc = float(s[13]) if len(s) > 13 else 0.0

    parts = []
    if threat > 0.75 or sh > 0.35:
        parts.append("mis vias de alerta estan muy altas")
    elif threat > 0.45:
        parts.append("algo me huele a peligro")
    if safe > 0.55 and e < 0.45:
        parts.append("busco tono de refugio")
    if food > 0.55 or sc > 0.25:
        parts.append("siento atraccion hacia lo nutritivo")
    if b > 0.65:
        parts.append("el borde me aprieta el mapa")
    if mn > 0.12:
        parts.append(f"mis neuronas motoras vibran fuerte (|m| medio {mn:.2f})")
    elif mn < 0.02:
        parts.append("casi no enciendo motores, estoy muy quieto por dentro")

    if not parts:
        parts.append("un murmullo neutro en el lobulo")

    tail = f" Ultima orden motora: {last_action_name}. Energia {e:.2f}."
    return "[Cerebro] " + "; ".join(parts) + tail + f" (EMA recompensa {reward_ema:+.2f})"
