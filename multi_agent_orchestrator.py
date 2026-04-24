"""
Orquestador: sub-agentes que leen/escriben en SharedFlyMemory. Solo LLM vía
API OpenAI (LM Studio); no hay heurísticos ni Ollama local.
"""
from __future__ import annotations

import ast
import hashlib
from typing import Any, Callable

import numpy as np
import torch

from llm_api_client import parse_assistant_message

# Compat. pruebas antiguas
_ollama_response_text = parse_assistant_message
assistant_message_text = parse_assistant_message


def text_hash_embed(text: str, dim: int, device: torch.device | str = "cpu") -> torch.Tensor:
    h = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    v = np.zeros(dim, dtype=np.float32)
    for i in range(dim):
        v[i] = (h[i % len(h)] / 128.0) - 1.0
    return torch.from_numpy(v.copy()).to(device)


def syntax_score(code: str) -> float:
    if not code.strip():
        return 0.0
    try:
        ast.parse(code)
        return 1.0
    except SyntaxError:
        return 0.0


def call_llm(
    llm_chat: Callable[..., Any],
    model: str,
    system: str,
    user: str,
    num_predict: int = 350,
) -> str:
    try:
        r = llm_chat(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user[:12000]},
            ],
            {"temperature": 0.45, "num_predict": int(num_predict)},
        )
    except Exception as exc:
        raise RuntimeError(
            f"Error de red o LM Studio: {exc}"
        ) from exc
    out = parse_assistant_message(r)
    if out:
        return out
    raise RuntimeError(
        "LM Studio no devolvió texto. Revisa servidor, modelo, LLM_API_BASE_URL y LLM_MODEL en .env."
    )


# Nombre usado en imports antiguos; delega a call_llm
def _ollama(
    llm_chat: Callable[..., Any] | None,
    model: str,
    system: str,
    user: str,
    num_predict: int = 350,
) -> str:
    if llm_chat is None:
        raise RuntimeError("Falta la función de chat LM Studio (configuración interna).")
    return call_llm(llm_chat, model, system, user, num_predict=num_predict)
