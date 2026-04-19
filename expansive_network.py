"""
Red plastica "expansiva en blanco" que se acopla al cerebro fijo de la mosca.

Opcionalmente incluye cabezales para:
  - politica de PREGUNTAS (distribucion sobre un lexicon fijo; aprende CUAL tema)
  - vector de INSTRUCCION al LLM (matices continuos aprendidos, tanh)

El LLM sigue congelado; aprenden solo fc1, fc2, fc_q, fc_inst.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.optim as optim


class ExpansiveNetwork(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int = 2000,
        output_size: int = 8,
        blank_sigma: float = 1e-4,
        lr: float = 0.002,
        question_vocab_size: int = 0,
        instruction_dim: int = 0,
    ):
        super().__init__()
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, output_size)
        self.act = nn.LeakyReLU(0.1)

        self.question_vocab_size = int(question_vocab_size)
        self.instruction_dim = int(instruction_dim)
        self.fc_q: nn.Linear | None = None
        self.fc_inst: nn.Linear | None = None
        if self.question_vocab_size > 0 and self.instruction_dim > 0:
            self.fc_q = nn.Linear(hidden_size, self.question_vocab_size)
            self.fc_inst = nn.Linear(hidden_size, self.instruction_dim)
            with torch.no_grad():
                self.fc_q.weight.normal_(mean=0.0, std=blank_sigma)
                self.fc_q.bias.zero_()
                self.fc_inst.weight.normal_(mean=0.0, std=blank_sigma)
                self.fc_inst.bias.zero_()

        with torch.no_grad():
            self.fc1.weight.normal_(mean=0.0, std=blank_sigma)
            self.fc2.weight.normal_(mean=0.0, std=blank_sigma)
            self.fc1.bias.zero_()
            self.fc2.bias.zero_()

        self.optimizer = optim.Adam(self.parameters(), lr=lr)
        self.hidden_size = hidden_size
        self.output_size = output_size

    def forward_heads(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor | None, torch.Tensor | None]:
        if x.dim() == 1:
            x = x.unsqueeze(0)
        h = self.act(self.fc1(x))
        motor = self.fc2(h).squeeze(0)
        if self.fc_q is None or self.fc_inst is None:
            return motor, None, None
        q = self.fc_q(h).squeeze(0)
        inst = torch.tanh(self.fc_inst(h).squeeze(0))
        return motor, q, inst

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward_heads(x)[0]

    def learn(self, loss: torch.Tensor) -> None:
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=5.0)
        self.optimizer.step()

    def learn_question_policy(
        self,
        state: torch.Tensor,
        taken_idx: int,
        reward: float,
        entropy_coef: float = 0.045,
    ) -> None:
        """REINFORCE sobre la cabeza de preguntas cuando llega feedback del usuario."""
        if self.fc_q is None:
            return
        dev = next(self.parameters()).device
        x = state.to(dev).detach()
        if x.dim() > 1:
            x = x.view(-1)
        _, q_logits, _ = self.forward_heads(x)
        if q_logits is None:
            return
        log_probs = torch.log_softmax(q_logits, dim=0)
        probs = torch.softmax(q_logits, dim=0)
        entropy = -(probs * log_probs).sum()
        lp = log_probs[taken_idx]
        R = float(max(-1.0, min(1.0, reward)))
        loss = -torch.tensor(R, device=dev, dtype=lp.dtype) * lp - entropy_coef * entropy
        self.learn(loss)

    @torch.no_grad()
    def connection_stats(self, threshold: float = 1e-3) -> dict:
        """Metricas de cuanto se ha poblado la red."""
        chunks = [self.fc1.weight, self.fc2.weight]
        if self.fc_q is not None:
            chunks.append(self.fc_q.weight)
        if self.fc_inst is not None:
            chunks.append(self.fc_inst.weight)
        total = sum(w.numel() for w in chunks)
        active = sum((w.abs() > threshold).sum().item() for w in chunks)
        mean_abs = float(sum(w.abs().mean().item() for w in chunks) / len(chunks))
        max_abs = float(max(w.abs().max().item() for w in chunks))
        return {
            "mean_abs_weight": mean_abs,
            "max_abs_weight": max_abs,
            "active_pct": 100.0 * active / max(total, 1),
            "active_synapses": int(active),
            "total_synapses": int(total),
        }
