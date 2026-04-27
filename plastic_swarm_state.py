"""
Estado plástico persistente: buffer de ciclo + red auxiliar pequeña + SQLite.
Reutiliza text_hash_embed (sin sentence-transformers).
"""
from __future__ import annotations

import io
import json
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


class SharedExperienceReplay:
    """
    Memoria compartida acotada para aprendizaje de por vida:
    episodica (experiencias), procedimental (patrones exitosos) y transactiva
    (que rol suele saber/resolver que).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        capacity: int = 240,
        embed_dim: int = 40,
    ) -> None:
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.capacity = max(24, int(capacity))
        self.embed_dim = max(8, int(embed_dim))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS shared_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                cycle INTEGER,
                role TEXT,
                agent_key TEXT,
                objective TEXT,
                text TEXT,
                reward REAL,
                importance REAL,
                embedding TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS procedural_memory (
                title TEXT PRIMARY KEY,
                content TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                source_count INTEGER DEFAULT 0,
                updated TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS transactive_memory (
                agent_key TEXT PRIMARY KEY,
                last_role TEXT,
                interactions INTEGER DEFAULT 0,
                success_ema REAL DEFAULT 0.0,
                avg_reward REAL DEFAULT 0.0,
                last_seen TEXT
            )"""
            )
            conn.commit()
        finally:
            conn.close()

    def _embed_json(self, text: str) -> str:
        emb = text_hash_embed(text, self.embed_dim, "cpu").detach().cpu().tolist()
        return json.dumps([round(float(x), 6) for x in emb], separators=(",", ":"))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        dot = sum(a[i] * b[i] for i in range(n))
        na = sum(a[i] * a[i] for i in range(n)) ** 0.5
        nb = sum(b[i] * b[i] for i in range(n)) ** 0.5
        if na <= 1e-9 or nb <= 1e-9:
            return 0.0
        return float(dot / (na * nb))

    def _query_vec(self, query: str) -> list[float]:
        return json.loads(self._embed_json(query))

    def record_cycle(
        self,
        *,
        cycle: int,
        objective: str,
        events: list[tuple[str, str]],
        reached: bool,
    ) -> None:
        if not events:
            return
        reward = 1.0 if reached else -0.25
        created = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn = self._connect()
        try:
            for role, text in events:
                clean = text.strip().replace("\n", " ")[:1800]
                if not clean:
                    continue
                agent_key = role.rstrip("0123456789") or role
                importance = max(0.05, min(1.0, 0.55 + 0.45 * reward))
                conn.execute(
                    """INSERT INTO shared_experiences
                       (created, cycle, role, agent_key, objective, text, reward, importance, embedding)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        created,
                        int(cycle),
                        role[:64],
                        agent_key[:64],
                        objective[:800],
                        clean,
                        reward,
                        importance,
                        self._embed_json(f"{objective}\n{role}\n{clean}"),
                    ),
                )
                self._update_transactive(conn, agent_key, role, reward, reached, created)

            if reached:
                self._consolidate_success(conn, objective, events, created)
            else:
                self._mark_procedure_failures(conn, events, created)

            conn.execute(
                """DELETE FROM shared_experiences
                   WHERE id NOT IN (
                       SELECT id FROM shared_experiences ORDER BY id DESC LIMIT ?
                   )""",
                (self.capacity,),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_transactive(
        self,
        conn: sqlite3.Connection,
        agent_key: str,
        role: str,
        reward: float,
        reached: bool,
        created: str,
    ) -> None:
        row = conn.execute(
            "SELECT interactions, success_ema, avg_reward FROM transactive_memory WHERE agent_key=?",
            (agent_key,),
        ).fetchone()
        hit = 1.0 if reached else 0.0
        if row:
            interactions, success_ema, avg_reward = row
            interactions = int(interactions) + 1
            success_ema = 0.88 * float(success_ema) + 0.12 * hit
            avg_reward = 0.88 * float(avg_reward) + 0.12 * float(reward)
            conn.execute(
                """UPDATE transactive_memory
                   SET last_role=?, interactions=?, success_ema=?, avg_reward=?, last_seen=?
                   WHERE agent_key=?""",
                (role, interactions, success_ema, avg_reward, created, agent_key),
            )
        else:
            conn.execute(
                """INSERT INTO transactive_memory
                   (agent_key, last_role, interactions, success_ema, avg_reward, last_seen)
                   VALUES (?,?,?,?,?,?)""",
                (agent_key, role, 1, hit, reward, created),
            )

    def _consolidate_success(
        self,
        conn: sqlite3.Connection,
        objective: str,
        events: list[tuple[str, str]],
        created: str,
    ) -> None:
        for role, text in events:
            base = role.rstrip("0123456789") or role
            if base not in {"Planifica", "Ejecuta", "Prueba", "Revisor"}:
                continue
            title = f"{base}: patron exitoso"
            content = f"Objetivo: {objective[:280]}\nPatron: {text.strip()[:1000]}"
            conn.execute(
                """INSERT INTO procedural_memory
                   (title, content, success_count, failure_count, source_count, updated)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(title) DO UPDATE SET
                       content=excluded.content,
                       success_count=success_count + 1,
                       source_count=source_count + 1,
                       updated=excluded.updated""",
                (title, content, 1, 0, 1, created),
            )

    def _mark_procedure_failures(
        self,
        conn: sqlite3.Connection,
        events: list[tuple[str, str]],
        created: str,
    ) -> None:
        seen = {role.rstrip("0123456789") or role for role, _ in events}
        for base in seen:
            title = f"{base}: patron exitoso"
            conn.execute(
                "UPDATE procedural_memory SET failure_count=failure_count + 1, updated=? WHERE title=?",
                (created, title),
            )

    def retrieval_context(
        self,
        query: str,
        *,
        agent_key: str = "",
        limit: int = 4,
        max_chars: int = 1400,
    ) -> str:
        qv = self._query_vec(query)
        conn = self._connect()
        try:
            procedures = conn.execute(
                """SELECT title, content, success_count, failure_count
                   FROM procedural_memory
                   ORDER BY (success_count - failure_count) DESC, updated DESC
                   LIMIT 3"""
            ).fetchall()
            episodes = conn.execute(
                """SELECT role, text, reward, importance, embedding, id
                   FROM shared_experiences
                   ORDER BY id DESC LIMIT ?""",
                (self.capacity,),
            ).fetchall()
            transactive = conn.execute(
                """SELECT agent_key, interactions, success_ema, avg_reward
                   FROM transactive_memory
                   ORDER BY success_ema DESC, interactions DESC LIMIT 5"""
            ).fetchall()
        finally:
            conn.close()

        ranked = []
        for role, text, reward, importance, emb_json, row_id in episodes:
            try:
                ev = json.loads(emb_json)
            except json.JSONDecodeError:
                ev = []
            role_bonus = 0.08 if agent_key and str(role).startswith(agent_key) else 0.0
            score = 0.70 * self._cosine(qv, ev) + 0.22 * float(importance) + role_bonus
            score += min(0.08, int(row_id) / max(1, self.capacity * 1000))
            ranked.append((score, role, text, reward))
        ranked.sort(reverse=True, key=lambda x: x[0])

        chunks: list[str] = []
        if procedures:
            ptxt = []
            for title, content, ok, fail in procedures:
                ptxt.append(f"- {title} (ok={ok}, fail={fail}): {str(content).replace(chr(10), ' ')[:360]}")
            chunks.append("Procedimental:\n" + "\n".join(ptxt))
        if ranked:
            etxt = []
            for _score, role, text, reward in ranked[: max(1, int(limit))]:
                etxt.append(f"- {role} R={float(reward):+.2f}: {str(text)[:300]}")
            chunks.append("Episodica compartida:\n" + "\n".join(etxt))
        if transactive:
            ttxt = []
            for key, interactions, success_ema, avg_reward in transactive:
                ttxt.append(
                    f"- {key}: n={interactions}, exito_ema={float(success_ema):.2f}, recompensa={float(avg_reward):+.2f}"
                )
            chunks.append("Transactiva:\n" + "\n".join(ttxt))

        out = "\n\n".join(chunks).strip()
        return out[:max(120, int(max_chars))]
