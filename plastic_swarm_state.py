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

# Un solo checkpoint en BD: evita INSERT ilimitados y el crecimiento del fichero.
_CHECKPOINT_ROW_ID = 1


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

    def free_energy_loss(self, x: torch.Tensor, y: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
        z = self.enc(x)
        reconstruction = torch.nn.functional.mse_loss(y, x)
        latent_prediction = torch.nn.functional.mse_loss(torch.tanh(z.mean(dim=0)), torch.zeros_like(z.mean(dim=0)))
        weight_complexity = torch.zeros((), device=x.device, dtype=x.dtype)
        for p in self.parameters():
            weight_complexity = weight_complexity + p.pow(2).mean()
        activation_complexity = 0.001 * z.pow(2).mean()
        entropy = torch.log1p(z.var(dim=0, unbiased=False).mean() + y.var(dim=0, unbiased=False).mean())
        free_energy = (
            reconstruction
            + 0.08 * latent_prediction
            + 0.0002 * weight_complexity
            + activation_complexity
            - 0.025 * entropy
        )
        stats = {
            "free_energy": float(free_energy.detach().cpu().item()),
            "reconstruction": float(reconstruction.detach().cpu().item()),
            "latent_prediction": float(latent_prediction.detach().cpu().item()),
            "complexity": float((0.0002 * weight_complexity + activation_complexity).detach().cpu().item()),
            "entropy": float(entropy.detach().cpu().item()),
        }
        return free_energy, stats

    def train_on_lines(
        self,
        lines: list[str],
        device: torch.device,
        steps: int = 6,
    ) -> tuple[float, dict[str, float]]:
        if len(lines) < 2:
            return 0.0, {
                "free_energy": 0.0,
                "reconstruction": 0.0,
                "latent_prediction": 0.0,
                "complexity": 0.0,
                "entropy": 0.0,
            }
        self.train()
        embs = []
        for ln in lines[:80]:
            emb = text_hash_embed(ln, self.dim, device).detach()
            embs.append(emb)
        x = torch.stack(embs, dim=0)
        tot = 0.0
        last_stats = {
            "free_energy": 0.0,
            "reconstruction": 0.0,
            "latent_prediction": 0.0,
            "complexity": 0.0,
            "entropy": 0.0,
        }
        for _ in range(max(1, steps)):
            self.opt.zero_grad()
            y = self.forward(x)
            loss, last_stats = self.free_energy_loss(x, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters(), 2.5)
            self.opt.step()
            tot += float(loss.detach().cpu())
        last_stats["free_energy"] = tot / max(1, steps)
        return last_stats["free_energy"], last_stats


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

    def _pack_memory(self, memory: SharedFlyMemory) -> dict:
        return {
            "version": 2,
            "model": memory.state_dict(),
            "optimizer": memory.optimizer.state_dict(),
        }

    def _pack_aux(self, aux: BufferPlasticNet | None) -> dict:
        if aux is None:
            return {"version": 2, "model": None, "optimizer": None}
        return {
            "version": 2,
            "model": aux.state_dict(),
            "optimizer": aux.opt.state_dict(),
        }

    def save(self, memory: SharedFlyMemory, aux: BufferPlasticNet | None) -> None:
        bio_m = io.BytesIO()
        torch.save(self._pack_memory(memory), bio_m)
        bio_a = io.BytesIO()
        torch.save(self._pack_aux(aux), bio_a)
        created = time.strftime("%Y-%m-%dT%H:%M:%S")
        mem_bytes = bio_m.getvalue()
        aux_bytes = bio_a.getvalue()

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO plastic_state (id, created, mem_blob, aux_blob, meta)
                   VALUES (?,?,?,?,?)""",
                (_CHECKPOINT_ROW_ID, created, mem_bytes, aux_bytes, "{}"),
            )
            cur = conn.execute(
                "DELETE FROM plastic_state WHERE id != ?",
                (_CHECKPOINT_ROW_ID,),
            )
            pruned = int(cur.rowcount or 0)
            conn.commit()
        finally:
            conn.close()

        if pruned > 0:
            vac = sqlite3.connect(str(self.db_path))
            try:
                vac.isolation_level = None
                vac.execute("VACUUM")
            finally:
                vac.close()

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
        mem_state = torch.load(io.BytesIO(mem_blob), map_location=dev, weights_only=True)
        if isinstance(mem_state, dict) and "model" in mem_state:
            memory.load_state_dict(mem_state["model"], strict=False)
            opt_state = mem_state.get("optimizer")
            if opt_state:
                try:
                    memory.optimizer.load_state_dict(opt_state)
                except ValueError:
                    pass
        else:
            memory.load_state_dict(mem_state, strict=False)
        if aux is not None and aux_blob:
            adev = next(aux.parameters()).device
            aux_state = torch.load(io.BytesIO(aux_blob), map_location=adev, weights_only=True)
            if isinstance(aux_state, dict) and "model" in aux_state:
                model_state = aux_state.get("model")
                if model_state:
                    aux.load_state_dict(model_state)
                opt_state = aux_state.get("optimizer")
                if opt_state:
                    try:
                        aux.opt.load_state_dict(opt_state)
                    except ValueError:
                        pass
            else:
                aux.load_state_dict(aux_state)
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
