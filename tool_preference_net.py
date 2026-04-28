"""
Region neuronal especializada para preferencia de herramientas.

Aprende que herramientas/skills suelen aportar mejor a ciertos objetivos usando
eventos auditados por TaskRuntime. Es una capa auxiliar separada de
SharedFlyMemory: memoria cognitiva general vs. memoria de seleccion de herramientas.
"""
from __future__ import annotations

import io
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.optim as optim

from multi_agent_orchestrator import text_hash_embed
from plastic_swarm_state import DEFAULT_DB

_RISK_VALUE = {"low": 0.0, "moderate": 0.33, "high": 0.66, "dangerous": 1.0}


@dataclass(frozen=True)
class ToolPreferenceScore:
    tool_name: str
    score: float
    risk: str = ""


class ToolPreferenceNet(nn.Module):
    def __init__(self, embed_dim: int = 40, hidden: int = 96):
        super().__init__()
        self.embed_dim = max(8, int(embed_dim))
        self.feature_dim = 5
        self.net = nn.Sequential(
            nn.Linear(self.embed_dim * 2 + self.feature_dim, hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden // 2, 1),
            nn.Tanh(),
        )
        self.opt = optim.Adam(self.parameters(), lr=0.002)

    def encode(
        self,
        objective: str,
        tool_name: str,
        *,
        risk: str = "",
        allowed: bool = True,
        duration_s: float = 0.0,
        had_error: bool = False,
        was_blocked: bool = False,
    ) -> torch.Tensor:
        device = next(self.parameters()).device
        obj = text_hash_embed(objective, self.embed_dim, device).float()
        tool = text_hash_embed(tool_name, self.embed_dim, device).float()
        features = torch.tensor(
            [
                _RISK_VALUE.get(str(risk).lower(), 0.0),
                1.0 if allowed else 0.0,
                min(1.0, max(0.0, float(duration_s) / 30.0)),
                1.0 if had_error else 0.0,
                1.0 if was_blocked else 0.0,
            ],
            dtype=torch.float32,
            device=device,
        )
        return torch.cat([obj, tool, features], dim=0)

    def score_tensor(self, encoded: torch.Tensor) -> torch.Tensor:
        if encoded.ndim == 1:
            encoded = encoded.unsqueeze(0)
        return self.net(encoded).squeeze(-1)

    def train_batch(self, rows: list[tuple[torch.Tensor, float]], *, steps: int = 12) -> tuple[float, dict[str, float]]:
        if not rows:
            return 0.0, {"free_energy": 0.0, "prediction_error": 0.0, "complexity": 0.0, "entropy": 0.0}
        x = torch.stack([item[0] for item in rows], dim=0)
        y = torch.tensor([item[1] for item in rows], dtype=torch.float32, device=x.device)
        last_stats = {}
        total = 0.0
        self.train()
        for _ in range(max(1, int(steps))):
            self.opt.zero_grad()
            pred = self.score_tensor(x)
            prediction_error = torch.nn.functional.mse_loss(pred, y)
            complexity = torch.zeros((), dtype=torch.float32, device=x.device)
            for param in self.parameters():
                complexity = complexity + param.pow(2).mean()
            entropy = torch.log1p(pred.var(unbiased=False) + x.var(unbiased=False))
            free_energy = prediction_error + 0.0005 * complexity - 0.02 * entropy
            free_energy.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters(), 2.5)
            self.opt.step()
            total += float(free_energy.detach().cpu())
            last_stats = {
                "free_energy": float(free_energy.detach().cpu()),
                "prediction_error": float(prediction_error.detach().cpu()),
                "complexity": float((0.0005 * complexity).detach().cpu()),
                "entropy": float(entropy.detach().cpu()),
            }
        last_stats["free_energy"] = total / max(1, int(steps))
        return last_stats["free_energy"], last_stats


class ToolPreferenceStore:
    def __init__(self, db_path: Path | None = None, *, embed_dim: int = 40):
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.net = ToolPreferenceNet(embed_dim=embed_dim)
        self._init_db()
        self.load()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tool_preference_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                objective TEXT,
                tool_name TEXT,
                risk TEXT,
                allowed INTEGER,
                had_error INTEGER,
                was_blocked INTEGER,
                duration_s REAL,
                reward REAL,
                metadata TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tool_preference_state (
                id INTEGER PRIMARY KEY,
                updated TEXT,
                state_blob BLOB
            )"""
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def reward_for_call(call: dict[str, Any], *, reached: bool) -> float:
        allowed = bool(call.get("allowed"))
        had_error = bool(call.get("error"))
        was_blocked = not allowed
        duration = float(call.get("duration_s") or 0.0)
        risk = str(call.get("risk") or "").lower()
        reward = 0.15
        if reached:
            reward += 0.65
        if allowed:
            reward += 0.15
        if str(call.get("output") or "").strip():
            reward += 0.15
        if had_error:
            reward -= 0.55
        if was_blocked:
            reward -= 0.45
        if duration > 15.0:
            reward -= 0.15
        if risk in {"high", "dangerous"} and not reached:
            reward -= 0.25
        return max(-1.0, min(1.0, reward))

    def record_event(
        self,
        *,
        objective: str,
        call: dict[str, Any],
        reward: float,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tool_preference_events
                   (created, objective, tool_name, risk, allowed, had_error, was_blocked,
                    duration_s, reward, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    self._now(),
                    str(objective)[:1000],
                    str(call.get("tool_name") or "")[:160],
                    str(call.get("risk") or "")[:40],
                    1 if call.get("allowed") else 0,
                    1 if call.get("error") else 0,
                    0 if call.get("allowed") else 1,
                    float(call.get("duration_s") or 0.0),
                    float(max(-1.0, min(1.0, reward))),
                    json.dumps(
                        {
                            "decision": call.get("decision"),
                            "id": call.get("id"),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def train_from_calls(
        self,
        objective: str,
        calls: list[dict[str, Any]],
        *,
        reached: bool,
        steps: int = 12,
    ) -> tuple[float, dict[str, float]]:
        rows = []
        for call in calls:
            tool_name = str(call.get("tool_name") or "").strip()
            if not tool_name:
                continue
            reward = self.reward_for_call(call, reached=reached)
            self.record_event(objective=objective, call=call, reward=reward)
            encoded = self.net.encode(
                objective,
                tool_name,
                risk=str(call.get("risk") or ""),
                allowed=bool(call.get("allowed")),
                duration_s=float(call.get("duration_s") or 0.0),
                had_error=bool(call.get("error")),
                was_blocked=not bool(call.get("allowed")),
            )
            rows.append((encoded.detach(), reward))
        loss, stats = self.net.train_batch(rows, steps=steps)
        if rows:
            self.save()
        return loss, stats

    def rank_tools(
        self,
        objective: str,
        candidates: list[tuple[str, str]],
        *,
        limit: int = 8,
    ) -> list[ToolPreferenceScore]:
        self.net.eval()
        scored = []
        with torch.no_grad():
            for name, risk in candidates:
                encoded = self.net.encode(objective, name, risk=risk)
                score = float(self.net.score_tensor(encoded).detach().cpu().item())
                scored.append(ToolPreferenceScore(name, score, risk))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, int(limit))]

    def context(self, objective: str, candidates: list[tuple[str, str]], *, limit: int = 8) -> str:
        ranked = self.rank_tools(objective, candidates, limit=limit)
        if not ranked:
            return ""
        lines = ["Preferencias neuronales de herramientas:"]
        for idx, item in enumerate(ranked, start=1):
            lines.append(f"{idx}. {item.tool_name} risk={item.risk or '-'} score={item.score:+.3f}")
        return "\n".join(lines)

    def save(self) -> None:
        bio = io.BytesIO()
        torch.save({"version": 1, "model": self.net.state_dict(), "optimizer": self.net.opt.state_dict()}, bio)
        conn = self._connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO tool_preference_state (id, updated, state_blob)
                   VALUES (?,?,?)""",
                (1, self._now(), bio.getvalue()),
            )
            conn.commit()
        finally:
            conn.close()

    def load(self) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_blob FROM tool_preference_state WHERE id=1"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return False
        try:
            state = torch.load(io.BytesIO(row[0]), map_location="cpu", weights_only=True)
            self.net.load_state_dict(state.get("model", {}), strict=False)
            opt_state = state.get("optimizer")
            if opt_state:
                self.net.opt.load_state_dict(opt_state)
            return True
        except Exception:
            return False
