"""
Estado plástico persistente: buffer de ciclo + red auxiliar pequeña + SQLite.
Reutiliza text_hash_embed (sin sentence-transformers).
"""
from __future__ import annotations

import io
import sqlite3
import threading
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim

from multi_agent_orchestrator import text_hash_embed
from unified_fly_memory import SharedFlyMemory

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "plastic_swarm.sqlite"


class CycleBuffer:
    """Buffer corto por ciclo: se vacía al cerrar el ciclo tras entrenar."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, role: str, text: str) -> None:
        t = text.strip().replace("\n", " ")[:1800]
        self._lines.append(f"{role}|{t}")

    def lines(self) -> list[str]:
        return list(self._lines)

    def clear(self) -> None:
        self._lines.clear()

    def __len__(self) -> int:
        return len(self._lines)


class BufferPlasticNet(nn.Module):
    """Autoencoder ligero sobre embeddings del buffer (misma dim que msg_dim)."""

    def __init__(self, dim: int = 40, hidden: int = 96):
        super().__init__()
        self.dim = dim
        self.enc = nn.Sequential(
            nn.Linear(dim, hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden, hidden // 2),
        )
        self.dec = nn.Sequential(
            nn.Linear(hidden // 2, hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden, dim),
        )
        self.opt = optim.Adam(self.parameters(), lr=0.002)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dec(self.enc(x))

    def train_on_lines(self, lines: list[str], device: torch.device, steps: int = 6) -> float:
        if len(lines) < 2:
            return 0.0
        self.train()
        embs = []
        for ln in lines[:80]:
            emb = text_hash_embed(ln, self.dim, device).detach()
            embs.append(emb)
        x = torch.stack(embs, dim=0)
        tot = 0.0
        for _ in range(max(1, steps)):
            self.opt.zero_grad()
            y = self.forward(x)
            loss = torch.nn.functional.mse_loss(y, x)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters(), 2.5)
            self.opt.step()
            tot += float(loss.detach().cpu())
        return tot / max(1, steps)


class SwarmPlasticStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS plastic_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT,
            mem_blob BLOB,
            aux_blob BLOB,
            meta TEXT
        )"""
        )
        conn.commit()
        conn.close()

    def save(self, memory: SharedFlyMemory, aux: BufferPlasticNet | None) -> None:
        bio_m = io.BytesIO()
        torch.save(memory.state_dict(), bio_m)
        bio_a = io.BytesIO()
        if aux is not None:
            torch.save(aux.state_dict(), bio_a)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO plastic_state (created, mem_blob, aux_blob, meta) VALUES (?,?,?,?)",
            (
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                bio_m.getvalue(),
                bio_a.getvalue(),
                "{}",
            ),
        )
        conn.commit()
        conn.close()

    def load_latest_into(
        self,
        memory: SharedFlyMemory,
        aux: BufferPlasticNet | None,
    ) -> bool:
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT mem_blob, aux_blob FROM plastic_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return False
        mem_blob, aux_blob = row
        dev = memory.mem.device
        memory.load_state_dict(
            torch.load(io.BytesIO(mem_blob), map_location=dev, weights_only=True)
        )
        if aux is not None and aux_blob:
            adev = next(aux.parameters()).device
            aux.load_state_dict(
                torch.load(io.BytesIO(aux_blob), map_location=adev, weights_only=True)
            )
        return True


def start_background_weights_load(
    store: SwarmPlasticStore,
    memory: SharedFlyMemory,
    aux: BufferPlasticNet | None,
    ready: threading.Event,
) -> threading.Thread:
    def _job() -> None:
        try:
            store.load_latest_into(memory, aux)
        except Exception:
            pass
        ready.set()

    th = threading.Thread(target=_job, daemon=True)
    th.start()
    return th
