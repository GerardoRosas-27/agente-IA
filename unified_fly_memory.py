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
import torch.nn.functional as F


class SharedFlyMemory(nn.Module):
    def __init__(
        self,
        n_slots: int = 8,
        mem_dim: int = 96,
        msg_dim: int = 40,
        n_agents_max: int = 5,
        writer_hidden: int = 192,
        lr: float = 0.002,
        blank_init: bool = False,
    ):
        super().__init__()
        self.n_slots = n_slots
        self.mem_dim = mem_dim
        self.msg_dim = msg_dim
        self.n_agents_max = n_agents_max
        self.write_decay = 0.88
        self.write_mix = 0.12
        self.global_mix = 0.02

        if blank_init:
            self.mem = nn.Parameter(torch.zeros(n_slots, mem_dim))
            self.rept_W = nn.Parameter(torch.zeros(mem_dim, mem_dim))
            self.rept_b = nn.Parameter(torch.zeros(mem_dim))
        else:
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

    def _global_code(self, mem_tensor: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        glob = mem_tensor.mean(dim=0)
        rep = torch.tanh(glob @ self.rept_W + self.rept_b)
        return glob, rep

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

        glob, rep = self._global_code(mem_cur)
        inp = torch.cat([msg_emb.to(dev), oh, glob, rep], dim=0)
        delta = self.writer(inp)

        row = self._slot(agent_id)
        mem_next = self.write_decay * mem_cur + self.global_mix * rep
        mem_next = mem_next.clone()
        mem_next[row] = mem_next[row] + self.write_mix * torch.tanh(delta)
        read = self._read_from_mem(mem_next, agent_id)
        return mem_next, read

    def _read_from_parts(
        self,
        mem_tensor: torch.Tensor,
        agent_id: int,
        rep: torch.Tensor,
    ) -> torch.Tensor:
        row = self._slot(agent_id)
        row_v = mem_tensor[row]
        return torch.tanh(self.read_proj(torch.cat([row_v, rep], dim=0)))

    def _read_from_mem(self, mem_tensor: torch.Tensor, agent_id: int) -> torch.Tensor:
        _, rep = self._global_code(mem_tensor)
        return self._read_from_parts(mem_tensor, agent_id, rep)

    def read_context(self, mem_cur: torch.Tensor, agent_id: int) -> torch.Tensor:
        """Lectura O(1) relativa al estado mem_cur (misma API que write_step)."""
        return self._read_from_mem(mem_cur, agent_id)

    def free_energy_loss(
        self,
        mem_cur: torch.Tensor,
        target_emb: torch.Tensor,
        *,
        signal: float = 1.0,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        """
        Energia libre variacional simple:
        sorpresa predictiva + complejidad - entropia util.

        Se minimiza en cada ciclo para que la memoria en blanco alinee su estado
        global con el resultado revisado sin colapsar a ceros.
        """
        glob, rep = self._global_code(mem_cur)
        d = min(int(self.mem_dim), int(target_emb.numel()))
        g = F.normalize(glob[:d], dim=0, eps=1e-6)
        target = F.normalize(target_emb[:d].to(mem_cur.device), dim=0, eps=1e-6)
        prediction_error = 1.0 - F.cosine_similarity(
            g.unsqueeze(0), target.unsqueeze(0), dim=1
        ).squeeze(0)
        complexity = 0.0015 * mem_cur.pow(2).mean() + 0.0005 * rep.pow(2).mean()
        entropy = torch.log1p(mem_cur.var(dim=0, unbiased=False).mean())
        free_energy = prediction_error + complexity - 0.035 * entropy
        learning_signal = torch.as_tensor(
            max(0.05, float(signal)),
            device=mem_cur.device,
            dtype=free_energy.dtype,
        )
        loss = learning_signal * free_energy
        stats = {
            "free_energy": float(free_energy.detach().cpu().item()),
            "prediction_error": float(prediction_error.detach().cpu().item()),
            "complexity": float(complexity.detach().cpu().item()),
            "entropy": float(entropy.detach().cpu().item()),
            "signal": float(learning_signal.detach().cpu().item()),
        }
        return loss, stats

    def learn(self, loss: torch.Tensor) -> None:
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=3.0)
        self.optimizer.step()

    @torch.no_grad()
    def snapshot_text(self) -> str:
        g = self.mem.mean(dim=0)[:12].cpu().numpy()
        return "[" + ", ".join(f"{x:+.2f}" for x in g) + ", …]"
