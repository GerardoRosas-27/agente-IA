"""
Preguntas de la mosca: el LLM solo articula lo que elige la red (tema + matices).
Solo API LM Studio: (model, messages, options) → respuesta vía `remote_openai_chat`.
"""
from __future__ import annotations

from typing import Callable

from llm_api_client import parse_assistant_message

# Semillas; lo aprendible es qué índice elige la red
QUESTION_LEXICON = [
    "el olor que viene y se va",
    "una sombra que no tiene nombre",
    "si el suelo vibra por debajo",
    "un borde que se acerca demasiado",
    "el aire caliente de arriba",
    "un punto rojo fijo alla lejos",
    "un manchon azul que invita a parar",
    "el silencio cuando dejo de moverme",
    "un zumbido que no entiendo",
    "la luz que cambia de golpe",
    "mis patas cuando resbalan",
    "el tiempo entre un olor y otro",
    "si girar me devuelve al mismo sitio",
    "una corriente que empuja hacia un lado",
    "algo dulce que no llega",
    "un hueco en el mapa",
    "la distancia hasta el siguiente refugio",
    "por que el peligro huele distinto hoy",
    "si la amabilidad tiene temperatura",
    "las palabras que no son olor",
    "un eco que repite mi vuelo",
    "la curva del techo invisible",
    "si hay comida detras de la esquina",
    "un frio que no es viento",
    "el momento justo antes de saltar",
    "si el miedo cansa las alas",
    "un rastro que se corta de golpe",
    "la pregunta que nadie huele",
    "si el azul miente a veces",
    "un ruido que viene del chat",
    "la forma del vacio entre dos cosas",
    "si puedo confiar en lo que no toco",
    "el mapa cuando se borra un poco",
    "una linea recta que se siente torcida",
    "si la curiosidad pesa",
    "el instante entre oler y moverme",
    "un olor a sal que no es mar",
    "la calma cuando alguien habla bajito",
    "un pinchazo sin aguijon",
    "si el refugio tiene final",
    "la pregunta que se queda en las antenas",
    "un brusco cambio de tono en el aire",
    "si la hostilidad tiene bordes afilados",
    "la conversacion como humedad",
    "un gesto amable que no veo pero intuyo",
    "si debo quedarme quieta para entender",
    "el siguiente paso cuando no veo nada",
    "una duda que zumba en el pecho",
    "si preguntar me acerca al olor",
]


def _fmt_inst(inst: list[float]) -> str:
    parts = []
    for i in range(0, min(len(inst), 24), 8):
        chunk = inst[i : i + 8]
        parts.append("[" + ", ".join(f"{v:+.2f}" for v in chunk) + "]")
    return "\n".join(parts)


def build_question_messages(
    lexicon_idx: int,
    inst_vec: list[float],
) -> list[dict]:
    tema = QUESTION_LEXICON[lexicon_idx % len(QUESTION_LEXICON)]
    sys = (
        "Eres la voz interior de una mosca (no un asistente humano). "
        "Escribe UNA sola pregunta corta en español (máximo 22 palabras), "
        "insegura, concreta, sin moralina ni filosofía barata. "
        "Debe resonar con el TEMA_SEMILLA. No saludes. No expliques tu tarea."
    )
    user = (
        f"TEMA_SEMILLA (obligatorio resonar): «{tema}»\n\n"
        "MATICES_CEREBRALES (vector de una red plástica interna, -1..1):\n"
        f"{_fmt_inst(inst_vec)}\n\n"
        "Escribe solo la pregunta, una linea."
    )
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


def generate_fly_question(
    model: str,
    lexicon_idx: int,
    inst_vec: list[float],
    llm_chat: Callable[..., object],
) -> str:
    msgs = build_question_messages(lexicon_idx, inst_vec)
    resp = llm_chat(
        model=model,
        messages=msgs,
        options={"temperature": 0.65, "num_predict": 80},
    )
    text = (parse_assistant_message(resp) or "").strip()
    if not text:
        raise RuntimeError("LM Studio no devolvió texto para la pregunta de la mosca.")
    line = text.split("\n")[0].strip()
    if len(line) < 4:
        raise RuntimeError("Respuesta demasiado corta del LLM para la pregunta.")
    if "?" not in line:
        line = line.rstrip(".") + "?"
    return line
