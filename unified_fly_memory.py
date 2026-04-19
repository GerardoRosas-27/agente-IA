"""
Memoria compartida aprendible: reptil (mezcla global W) + plastico (MLP writer).

Acceso O(1) por agente: escribe principalmente en una fila dedicada; todas
las filas reciben un arrastre suave del estado reptiliano (difusion leve).

Uso tipico en un solo hilo (orquestador); encadenar write_step(mem, ...)
para un grafo unico y luego loss.backward().
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.optim as optim


class SharedFlyMemory(nn.Module):
    def __init__(
        self,
        n_slots: int = 8,
        mem_dim: int = 96,
        msg_dim: int = 40,
        n_agents_max: int = 5,
        writer_hidden: int = 192,
        lr: float = 0.002,
    ):
        super().__init__()
        self.n_slots = n_slots
        self.mem_dim = mem_dim
        self.msg_dim = msg_dim
        self.n_agents_max = n_agents_max

        self.mem = nn.Parameter(torch.randn(n_slots, mem_dim) * 0.02)
        self.rept_W = nn.Parameter(torch.randn(mem_dim, mem_dim) * 0.02)
        self.rept_b = nn.Parameter(torch.zeros(mem_dim))

        in_w = msg_dim + n_agents_max + mem_dim + mem_dim
        self.writer = nn.Sequential(
            nn.Linear(in_w, writer_hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(writer_hidden, mem_dim),
        )
        self.read_proj = nn.Linear(mem_dim + mem_dim, mem_dim)
        self.optimizer = optim.Adam(self.parameters(), lr=lr)

    def _slot(self, agent_id: int) -> int:
        return int(agent_id % self.n_slots)

    def write_step(
        self,
        mem_cur: torch.Tensor,
        agent_id: int,
        msg_emb: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if msg_emb.dim() == 0:
            msg_emb = msg_emb.view(-1)
        dev = mem_cur.device
        oh = torch.zeros(self.n_agents_max, device=dev, dtype=mem_cur.dtype)
        oh[agent_id % self.n_agents_max] = 1.0

        glob = mem_cur.mean(dim=0)
        rep = torch.tanh(glob @ self.rept_W + self.rept_b)
        inp = torch.cat([msg_emb.to(dev), oh, glob, rep], dim=0)
        delta = self.writer(inp)

        row = self._slot(agent_id)
        mask = torch.zeros_like(mem_cur)
        mask[row] = torch.tanh(delta)
        mem_next = (
            0.88 * mem_cur
            + 0.12 * mask
            + 0.02 * rep.unsqueeze(0).expand_as(mem_cur)
        )
        read = self._read_from_mem(mem_next, agent_id)
        return mem_next, read

    def _read_from_mem(self, mem_tensor: torch.Tensor, agent_id: int) -> torch.Tensor:
        row = self._slot(agent_id)
        row_v = mem_tensor[row]
        glob = mem_tensor.mean(dim=0)
        rep = torch.tanh(glob @ self.rept_W + self.rept_b)
        return torch.tanh(self.read_proj(torch.cat([row_v, rep], dim=0)))

    def read_context(self, mem_cur: torch.Tensor, agent_id: int) -> torch.Tensor:
        """Lectura O(1) relativa al estado mem_cur (misma API que write_step)."""
        return self._read_from_mem(mem_cur, agent_id)

    def learn(self, loss: torch.Tensor) -> None:
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=3.0)
        self.optimizer.step()

    @torch.no_grad()
    def snapshot_text(self) -> str:
        g = self.mem.mean(dim=0)[:12].cpu().numpy()
        return "[" + ", ".join(f"{x:+.2f}" for x in g) + ", …]"
