"""
Cerebro de la mosca a partir del conectoma FlyWire (o sintético).

Modo clásico: matriz sináptica **fija** (buffer) = instinto congelado.

Modo `blank_learnable=True`: misma **topología** (qué neuronas existen,
máscaras sensor/motor), pero **W = 0** al inicio y `W` es `nn.Parameter`
para aprendizaje a mediano plazo (plasticidad del núcleo recurrente),
combinado con el optimizador de la sesión de chat.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim

DATA_DIR = Path(__file__).parent / "data"


def _load_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    neurons = pd.read_csv(DATA_DIR / "neurons.csv")
    connections = pd.read_csv(DATA_DIR / "connections.csv")
    return neurons, connections


def _infer_sign(nt_type: str) -> float:
    """GABA / glicina -> inhibitorio (-1). El resto excitatorio (+1)."""
    if not isinstance(nt_type, str):
        return 1.0
    low = nt_type.lower()
    if "gaba" in low or "gly" in low or "inhib" in low:
        return -1.0
    return 1.0


class FlyConnectomeBrain(nn.Module):
    """
    Capa lineal con pesos = matriz sinaptica real de la mosca.
    Los pesos se registran como `buffer` (no entrenables).

    Forward: x (estado de N neuronas) -> activacion recurrente de un paso.
    """

    def __init__(
        self,
        max_neurons: int | None = 3000,
        normalize: bool = True,
        device: str | torch.device = "cpu",
        blank_learnable: bool = False,
        fly_lr: float = 3e-5,
    ):
        super().__init__()
        self.blank_learnable = bool(blank_learnable)
        neurons_df, conn_df = _load_tables()

        if max_neurons is not None and len(neurons_df) > max_neurons:
            keep_ids = set(
                neurons_df.sample(n=max_neurons, random_state=0)["root_id"].tolist()
            )
            neurons_df = neurons_df[neurons_df["root_id"].isin(keep_ids)].reset_index(drop=True)
            conn_df = conn_df[
                conn_df["pre_root_id"].isin(keep_ids)
                & conn_df["post_root_id"].isin(keep_ids)
            ].reset_index(drop=True)

        n = len(neurons_df)
        id_to_idx = {rid: i for i, rid in enumerate(neurons_df["root_id"].tolist())}

        pre_idx = conn_df["pre_root_id"].map(id_to_idx).to_numpy()
        post_idx = conn_df["post_root_id"].map(id_to_idx).to_numpy()
        syn = conn_df["syn_count"].to_numpy(dtype=np.float32)

        signs = np.ones(n, dtype=np.float32)
        for i, row in neurons_df.reset_index(drop=True).iterrows():
            signs[i] = _infer_sign(row.get("nt_type", ""))

        W = torch.zeros(n, n, dtype=torch.float32)
        pre_t = torch.from_numpy(pre_idx.astype(np.int64))
        post_t = torch.from_numpy(post_idx.astype(np.int64))
        vals = torch.from_numpy(syn * signs[pre_idx])
        W.index_put_((post_t, pre_t), vals, accumulate=True)

        if not self.blank_learnable:
            if normalize:
                max_abs = W.abs().max().item()
                if max_abs > 0:
                    W = W / max_abs
        else:
            W = torch.zeros(n, n, dtype=torch.float32)

        W = W.to(device)
        if self.blank_learnable:
            self.W = nn.Parameter(W)
            self.fly_optimizer = optim.Adam([self.W], lr=fly_lr)
        else:
            self.register_buffer("W", W)
            self.fly_optimizer = None

        self.n_neurons = n
        self.register_buffer(
            "signs", torch.from_numpy(signs).to(device)
        )

        self.regions = neurons_df["region"].tolist() if "region" in neurons_df.columns else None

        sensory_mask = np.zeros(n, dtype=np.float32)
        if self.regions is not None:
            for i, r in enumerate(self.regions):
                if r in ("optic_lobe", "antennal_lobe"):
                    sensory_mask[i] = 1.0
        if sensory_mask.sum() == 0:
            sensory_mask[: max(16, n // 20)] = 1.0
        self.register_buffer(
            "sensory_mask", torch.from_numpy(sensory_mask).to(device)
        )
        self.sensory_indices = np.where(sensory_mask > 0)[0]

        motor_mask = np.zeros(n, dtype=np.float32)
        if self.regions is not None:
            for i, r in enumerate(self.regions):
                if r in ("ventral_cord", "subesophageal"):
                    motor_mask[i] = 1.0
        if motor_mask.sum() == 0:
            motor_mask[-max(8, n // 50):] = 1.0
        self.register_buffer(
            "motor_mask", torch.from_numpy(motor_mask).to(device)
        )
        self.motor_indices = np.where(motor_mask > 0)[0]

        if not self.blank_learnable:
            for p in self.parameters():
                p.requires_grad_(False)

    def forward(
        self,
        sensory_input: torch.Tensor,
        steps: int = 2,
        enable_grad: bool = False,
    ) -> torch.Tensor:
        """
        sensory_input: (batch, n_sensory) o (n_sensory,)
        Retorna la activacion completa (batch, n_neurons) tras `steps`
        pasos de propagacion con la matriz sinaptica.

        `enable_grad=True` solo en modo blank_learnable para acoplar
        gradientes desde la red plástica (REINFORCE).
        """
        g = enable_grad and self.blank_learnable
        cm = contextlib.nullcontext() if g else torch.no_grad()
        with cm:
            if sensory_input.dim() == 1:
                sensory_input = sensory_input.unsqueeze(0)
            batch = sensory_input.shape[0]
            n_sensory = int(self.sensory_mask.sum().item())

            x = torch.zeros(
                batch, self.n_neurons, device=self.W.device, dtype=self.W.dtype
            )
            pad = torch.zeros(
                batch, n_sensory, device=self.W.device, dtype=self.W.dtype
            )
            if sensory_input.shape[1] < n_sensory:
                pad[:, : sensory_input.shape[1]] = sensory_input
            else:
                pad = sensory_input[:, :n_sensory]
            x[:, self.sensory_indices] = pad

            for _ in range(steps):
                y = torch.tanh(x @ self.W.T)
                # Con W=0 puro, y sería 0 y no habría gradiente útil hacia W;
                # el residual conserva la inyección sensorial y desbloquea el aprendizaje.
                if self.blank_learnable:
                    x = y + 0.12 * x
                else:
                    x = y

            return x

    def motor_output(self, activity: torch.Tensor) -> torch.Tensor:
        """Extrae la actividad de las neuronas motoras."""
        return activity[:, self.motor_indices]

    def describe(self) -> dict:
        W = self.W
        nnz = (W != 0).sum().item()
        inhib = (self.signs < 0).sum().item()
        return {
            "n_neurons": self.n_neurons,
            "n_synapses_nonzero": int(nnz),
            "density_pct": 100.0 * nnz / (self.n_neurons ** 2),
            "inhibitory_neurons": int(inhib),
            "excitatory_neurons": int(self.n_neurons - inhib),
            "n_sensory": int(self.sensory_mask.sum().item()),
            "n_motor": int(self.motor_mask.sum().item()),
            "blank_learnable": self.blank_learnable,
        }
