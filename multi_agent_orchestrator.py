"""
Orquestador: hasta 5 sub-agentes (LLM pequeño) que leen/escriben en
SharedFlyMemory y devuelven una respuesta unificada. Un paso de aprendizaje
conjunto sobre la memoria compartida (reptil + plastico).
"""
from __future__ import annotations

import ast
import hashlib
from typing import Any, Callable

import numpy as np
import torch


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


def _heuristic_llm_text(system: str, user: str) -> str:
    """Respuesta de respaldo (mismo criterio que el puente sin red)."""
    if "fusiona" in system.lower():
        parts = user.split("PROPUESTAS:", 1)
        if len(parts) > 1:
            body = parts[1].strip()
            chunks = [c.strip() for c in body.split("---") if c.strip()]
            merged = "\n\n".join(chunks[:12])[:4000]
            return (
                "[borrador local — fusión]\n\n" + merged
                if merged
                else "[borrador local] Sin propuestas para fusionar."
            )
    if "TAREA:" in user:
        task = user.split("TAREA:", 1)[1].split("LECTURA", 1)[0].strip()[:400]
        return (
            "[borrador local]\n"
            f"# Aporte\n"
            f"- Objetivo: {task}\n"
            "```python\ndef esqueleto():\n    raise NotImplementedError\n```"
        )
    return user[:400]


def _ollama_response_text(r: Any) -> str | None:
    """
    El paquete `ollama` moderno devuelve ChatResponse (Pydantic), no dict.
    Antes solo se leía `dict` → siempre caía en heurística (texto tipo plantilla).

    Algunos modelos con “thinking” pueden dejar `content` vacío y rellenar
    `message.thinking`; lo usamos como respaldo.
    """
    if r is None:
        return None
    try:
        msg = r["message"]
    except Exception:
        return None
    if msg is None:
        return None
    try:
        raw = msg["content"]
    except Exception:
        raw = None
    if raw is not None:
        t = str(raw).strip()
        if t:
            return t
    try:
        think = msg["thinking"]
    except Exception:
        think = getattr(msg, "thinking", None)
    if think is not None:
        t2 = str(think).strip()
        if t2:
            return t2
    return None


def _ollama(
    ollama_chat: Callable | None,
    model: str,
    system: str,
    user: str,
    num_predict: int = 350,
) -> str:
    if ollama_chat is not None:
        try:
            r = ollama_chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user[:12000]},
                ],
                options={"temperature": 0.45, "num_predict": int(num_predict)},
            )
            out = _ollama_response_text(r)
            if out:
                return out
        except Exception:
            pass
    return _heuristic_llm_text(system, user)
