"""
Red plastica "expansiva en blanco" que se acopla al cerebro fijo de la mosca.

Idea del experimento:
    El conectoma de la mosca (fly_brain) representa su INSTINTO congelado.
    Encima le conectamos una red con muchas neuronas inicialmente vacias
    (pesos ~0). Mediante refuerzo observamos si la red "se puebla sola",
    es decir si aparecen conexiones fuertes que mejoran la recompensa.

Nota tecnica importante:
    Inicializar con ceros exactos + ReLU mata el gradiente. Usamos una
    inicializacion casi-cero (sigma muy pequena) para que la red sea
    funcionalmente "en blanco" pero matematicamente viable. Asi podemos
    medir honestamente si surgen sinapsis significativas (|w| > umbral).
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
    ):
        super().__init__()
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, output_size)
        self.act = nn.LeakyReLU(0.1)

        with torch.no_grad():
            self.fc1.weight.normal_(mean=0.0, std=blank_sigma)
            self.fc2.weight.normal_(mean=0.0, std=blank_sigma)
            self.fc1.bias.zero_()
            self.fc2.bias.zero_()

        self.optimizer = optim.Adam(self.parameters(), lr=lr)
        self.hidden_size = hidden_size
        self.output_size = output_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.act(self.fc1(x))
        return self.fc2(h)

    def learn(self, loss: torch.Tensor) -> None:
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=5.0)
        self.optimizer.step()

    @torch.no_grad()
    def connection_stats(self, threshold: float = 1e-3) -> dict:
        """Metricas de cuanto se ha poblado la red."""
        w1 = self.fc1.weight
        w2 = self.fc2.weight
        total = w1.numel() + w2.numel()
        active = ((w1.abs() > threshold).sum() + (w2.abs() > threshold).sum()).item()
        return {
            "mean_abs_weight": (w1.abs().mean().item() + w2.abs().mean().item()) / 2,
            "max_abs_weight": max(w1.abs().max().item(), w2.abs().max().item()),
            "active_pct": 100.0 * active / total,
            "active_synapses": active,
            "total_synapses": total,
        }
