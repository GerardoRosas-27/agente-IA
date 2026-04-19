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

from unified_fly_memory import SharedFlyMemory


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


def _ollama(ollama_chat: Callable | None, model: str, system: str, user: str) -> str:
    if ollama_chat is None:
        if "fusiona" in system.lower():
            parts = user.split("PROPUESTAS:", 1)
            if len(parts) > 1:
                body = parts[1].strip()
                chunks = [c.strip() for c in body.split("---") if c.strip()]
                merged = "\n\n".join(chunks[:12])[:4000]
                return (
                    "[sin Ollama — fusión heurística]\n\n" + merged
                    if merged
                    else "[sin Ollama] Sin propuestas para fusionar."
                )
        if "TAREA:" in user:
            task = user.split("TAREA:", 1)[1].split("LECTURA", 1)[0].strip()[:400]
            return (
                "[sin Ollama — borrador]\n"
                f"# Aporte\n"
                f"- Objetivo: {task}\n"
                "```python\ndef esqueleto():\n    raise NotImplementedError\n```"
            )
        return user[:400]
    try:
        r = ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user[:12000]},
            ],
            options={"temperature": 0.35, "num_predict": 350},
        )
        return r["message"]["content"].strip()
    except Exception as exc:
        return f"[fallo_llm: {exc}]"


def run_collaborative_program(
    task: str,
    n_agents: int,
    memory: SharedFlyMemory,
    model: str,
    ollama_chat: Callable | None,
    rounds: int = 1,
) -> tuple[str, list[str], float]:
    """
    Una ronda = cada agente propone en orden; varias rondas repiten.
    Al final: fusion + loss sobre memoria y aprendizaje.
    """
    n_agents = max(1, min(5, int(n_agents)))
    rounds = max(1, min(3, int(rounds)))
    device = memory.mem.device
    proposals: list[str] = []

    mem_cur = memory.mem
    for _ in range(rounds):
        for aid in range(n_agents):
            with torch.no_grad():
                ctx = memory.read_context(mem_cur, aid)[:16].cpu().tolist()
            ctx_s = ", ".join(f"{x:+.2f}" for x in ctx)
            sys = (
                f"Eres el sub-agente {aid + 1} de {n_agents}. "
                "Responde en español. Aporta código Python breve, ideas o pasos "
                "útiles para la tarea grupal. Máximo 14 líneas. Sin saludos largos."
            )
            user = (
                f"TAREA:\n{task}\n\n"
                f"LECTURA_MEMORIA_COMPARTIDA (vector parcial): {ctx_s}\n"
                "Escribe solo tu aporte."
            )
            text = _ollama(ollama_chat, model, sys, user)
            proposals.append(text.strip())

            emb = text_hash_embed(text, memory.msg_dim, device)
            mem_cur, _ = memory.write_step(mem_cur, aid, emb)

    blob = "\n---\n".join(f"[Agente {i+1}]\n{p}" for i, p in enumerate(proposals))
    merge_sys = (
        "Fusiona las propuestas en UNA sola respuesta útil (código o lista de pasos). "
        "Español. Sin repetir encabezados de agentes."
    )
    merge_user = f"TAREA:\n{task}\n\nPROPUESTAS:\n{blob[:10000]}"
    unified = _ollama(ollama_chat, model, merge_sys, merge_user)

    uemb = text_hash_embed(unified, memory.msg_dim, device)
    glob = mem_cur.mean(dim=0)
    g = glob[: memory.msg_dim]
    cos = torch.nn.functional.cosine_similarity(
        g.unsqueeze(0), uemb.unsqueeze(0), dim=1
    ).squeeze(0)
    syn = float(syntax_score("\n".join(proposals) + "\n" + unified))
    loss = (
        0.0015 * mem_cur.pow(2).mean()
        - 0.18 * syn * cos
        - 0.06 * syn * torch.tanh(mem_cur.norm())
    )
    memory.learn(loss)
    return unified, proposals, float(loss.detach().cpu().item())
