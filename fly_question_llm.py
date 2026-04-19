"""
Generacion de preguntas de la mosca: el LLM solo 'articula' lo que la red
plastica elige (tema + matices). Los pesos del LLM siguen congelados.

Si Ollama no esta, se usa solo el tema semilla aprendido (lexicon).
"""
from __future__ import annotations

from typing import Callable

# Semillas fijas; lo APRENDIBLE es cual indice elige la red (distribucion).
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
    ollama_chat: Callable | None,
) -> str:
    tema = QUESTION_LEXICON[lexicon_idx % len(QUESTION_LEXICON)]
    if ollama_chat is None:
        return f"…¿qué pasa con {tema}?"
    try:
        msgs = build_question_messages(lexicon_idx, inst_vec)
        resp = ollama_chat(
            model=model,
            messages=msgs,
            options={"temperature": 0.65, "num_predict": 80},
        )
        text = resp["message"]["content"].strip().split("\n")[0].strip()
        if len(text) < 4:
            raise ValueError("vacío")
        if "?" not in text:
            text = text.rstrip(".") + "?"
        return text
    except Exception:
        return f"…¿qué pasa con {tema}?"
