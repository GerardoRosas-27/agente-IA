"""
Orquestador: hasta 5 sub-agentes (LLM pequeño) que leen/escriben en
SharedFlyMemory y devuelven una respuesta unificada. Un paso de aprendizaje
conjunto sobre la memoria compartida (reptil + plastico).
"""
from __future__ import annotations

import ast
import hashlib
from typing import Callable

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
                options={"temperature": 0.35, "num_predict": int(num_predict)},
            )
            if isinstance(r, dict):
                raw = (r.get("message") or {}).get("content")
                if raw is not None:
                    t = str(raw).strip()
                    if t:
                        return t
        except Exception:
            pass
    return _heuristic_llm_text(system, user)
