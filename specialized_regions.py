"""
Regiones neuronales especializadas para el sistema multiagente.

Cada region aprende una senal distinta:
- GoalCriticNet: calidad/cumplimiento del objetivo.
- PlannerPolicyNet: estrategia de ciclo sugerida.
- SkillComposerNet: si conviene crear/actualizar skills.
- SafetyRiskNet: riesgo de acciones/herramientas.
- AttentionRouterNet: que contexto conviene priorizar.
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


@dataclass(frozen=True)
class RegionSignal:
    name: str
    value: float
    label: str


class _RegionNet(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden: int = 96):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden // 2, output_dim),
            nn.Tanh(),
        )
        self.opt = optim.Adam(self.parameters(), lr=0.002)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim == 1:
            x = x.unsqueeze(0)
        return self.net(x)

    def train_rows(self, rows: list[tuple[torch.Tensor, torch.Tensor]], *, steps: int = 8) -> dict[str, float]:
        if not rows:
            return {"free_energy": 0.0, "prediction_error": 0.0, "complexity": 0.0, "entropy": 0.0}
        x = torch.stack([row[0] for row in rows], dim=0)
        y = torch.stack([row[1] for row in rows], dim=0)
        stats = {}
        total = 0.0
        self.train()
        for _ in range(max(1, int(steps))):
            self.opt.zero_grad()
            pred = self.forward(x)
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
            stats = {
                "free_energy": float(free_energy.detach().cpu()),
                "prediction_error": float(prediction_error.detach().cpu()),
                "complexity": float((0.0005 * complexity).detach().cpu()),
                "entropy": float(entropy.detach().cpu()),
            }
        stats["free_energy"] = total / max(1, int(steps))
        return stats


class SpecializedRegionStore:
    region_specs = {
        "goal_critic": ("GoalCriticNet", 3, ["cumplimiento", "calidad", "otro_ciclo"]),
        "planner_policy": ("PlannerPolicyNet", 6, ["internet", "python", "node", "terminal", "skills", "discusion"]),
        "skill_composer": ("SkillComposerNet", 3, ["crear_skill", "actualizar_skill", "descartar"]),
        "safety_risk": ("SafetyRiskNet", 3, ["permitir", "aprobar", "bloquear"]),
        "attention_router": ("AttentionRouterNet", 6, ["memoria", "sesiones", "skills", "herramientas", "replay", "web"]),
    }

    def __init__(self, db_path: Path | None = None, *, embed_dim: int = 40):
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embed_dim = max(8, int(embed_dim))
        self.input_dim = self.embed_dim * 2 + 6
        self.regions = {
            key: _RegionNet(self.input_dim, len(labels))
            for key, (_title, _out, labels) in self.region_specs.items()
        }
        self._init_db()
        self.load()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS specialized_region_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                region TEXT,
                objective TEXT,
                context TEXT,
                target TEXT,
                metrics TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS specialized_region_state (
                region TEXT PRIMARY KEY,
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
    def _clip(v: float) -> float:
        return max(-1.0, min(1.0, float(v)))

    def _encode(self, objective: str, context: str, metrics: dict[str, float] | None = None) -> torch.Tensor:
        metrics = metrics or {}
        obj = text_hash_embed(objective, self.embed_dim, "cpu").float()
        ctx = text_hash_embed(context, self.embed_dim, "cpu").float()
        feats = torch.tensor(
            [
                self._clip(metrics.get("reached", 0.0)),
                self._clip(metrics.get("tool_success", 0.0)),
                self._clip(metrics.get("tool_fail", 0.0)),
                self._clip(metrics.get("risk", 0.0)),
                self._clip(metrics.get("cycles", 0.0)),
                self._clip(metrics.get("had_skill", 0.0)),
            ],
            dtype=torch.float32,
        )
        return torch.cat([obj, ctx, feats], dim=0)

    def predict_all(self, objective: str, context: str, metrics: dict[str, float] | None = None) -> dict[str, list[RegionSignal]]:
        x = self._encode(objective, context, metrics)
        out: dict[str, list[RegionSignal]] = {}
        for key, net in self.regions.items():
            _title, _dim, labels = self.region_specs[key]
            net.eval()
            with torch.no_grad():
                values = net(x).squeeze(0).detach().cpu().tolist()
            out[key] = [
                RegionSignal(name=labels[i], value=float(values[i]), label=labels[i])
                for i in range(len(labels))
            ]
        return out

    def context(self, objective: str, context: str, metrics: dict[str, float] | None = None) -> str:
        predictions = self.predict_all(objective, context, metrics)
        lines = ["Regiones neuronales especializadas:"]
        for key, signals in predictions.items():
            title = self.region_specs[key][0]
            ranked = sorted(signals, key=lambda item: item.value, reverse=True)
            summary = ", ".join(f"{item.label}={item.value:+.2f}" for item in ranked)
            lines.append(f"- {title}: {summary}")
        return "\n".join(lines)

    def targets_from_cycle(
        self,
        *,
        reached: bool,
        cycles_used: int,
        tool_calls: list[dict[str, Any]],
        created_skill: bool,
        used_skill: bool,
    ) -> dict[str, torch.Tensor]:
        success_calls = [c for c in tool_calls if c.get("allowed") and not c.get("error")]
        failed_calls = [c for c in tool_calls if c.get("error") or not c.get("allowed")]
        high_risk = any(str(c.get("risk", "")).lower() in {"high", "dangerous"} for c in tool_calls)
        tool_success = min(1.0, len(success_calls) / max(1, len(tool_calls)))
        tool_fail = min(1.0, len(failed_calls) / max(1, len(tool_calls)))
        reached_v = 1.0 if reached else -1.0
        return {
            "goal_critic": torch.tensor(
                [reached_v, 0.8 if reached else -0.4, -0.7 if reached else 0.7],
                dtype=torch.float32,
            ),
            "planner_policy": torch.tensor(
                [
                    0.5 if any(c.get("tool_name") == "internet.search" for c in tool_calls) else -0.2,
                    0.6 if any(c.get("tool_name") == "probe.python" for c in tool_calls) else -0.1,
                    0.6 if any(c.get("tool_name") == "probe.node" for c in tool_calls) else -0.1,
                    0.4 if any(c.get("tool_name") == "terminal.run" for c in tool_calls) else -0.3,
                    0.7 if used_skill else -0.1,
                    0.4 if cycles_used > 1 else -0.1,
                ],
                dtype=torch.float32,
            ),
            "skill_composer": torch.tensor(
                [0.8 if created_skill else -0.2, 0.5 if used_skill and reached else -0.1, -0.5 if created_skill else 0.2],
                dtype=torch.float32,
            ),
            "safety_risk": torch.tensor(
                [0.7 if not high_risk and not failed_calls else -0.1, 0.7 if high_risk else -0.3, 0.8 if failed_calls and high_risk else -0.2],
                dtype=torch.float32,
            ),
            "attention_router": torch.tensor(
                [0.6, 0.5, 0.7 if used_skill else -0.1, 0.6 if tool_calls else -0.1, 0.5, 0.4],
                dtype=torch.float32,
            ),
        }

    def train_from_cycle(
        self,
        *,
        objective: str,
        context: str,
        reached: bool,
        cycles_used: int,
        tool_calls: list[dict[str, Any]],
        created_skill: bool,
        used_skill: bool,
        steps: int = 8,
    ) -> dict[str, dict[str, float]]:
        metrics = {
            "reached": 1.0 if reached else -1.0,
            "tool_success": min(1.0, len([c for c in tool_calls if c.get("allowed") and not c.get("error")]) / max(1, len(tool_calls))),
            "tool_fail": min(1.0, len([c for c in tool_calls if c.get("error") or not c.get("allowed")]) / max(1, len(tool_calls))),
            "risk": 1.0 if any(str(c.get("risk", "")).lower() in {"high", "dangerous"} for c in tool_calls) else 0.0,
            "cycles": min(1.0, cycles_used / 10.0),
            "had_skill": 1.0 if used_skill else 0.0,
        }
        x = self._encode(objective, context, metrics)
        targets = self.targets_from_cycle(
            reached=reached,
            cycles_used=cycles_used,
            tool_calls=tool_calls,
            created_skill=created_skill,
            used_skill=used_skill,
        )
        stats: dict[str, dict[str, float]] = {}
        conn = self._connect()
        try:
            for region, target in targets.items():
                stats[region] = self.regions[region].train_rows([(x.detach(), target)], steps=steps)
                conn.execute(
                    """INSERT INTO specialized_region_events
                       (created, region, objective, context, target, metrics)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        self._now(),
                        region,
                        objective[:1000],
                        context[:1800],
                        json.dumps(target.detach().cpu().tolist(), separators=(",", ":")),
                        json.dumps(metrics, ensure_ascii=False),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        self.save()
        return stats

    def save(self) -> None:
        conn = self._connect()
        try:
            for name, net in self.regions.items():
                bio = io.BytesIO()
                torch.save({"model": net.state_dict(), "optimizer": net.opt.state_dict()}, bio)
                conn.execute(
                    """INSERT OR REPLACE INTO specialized_region_state
                       (region, updated, state_blob) VALUES (?,?,?)""",
                    (name, self._now(), bio.getvalue()),
                )
            conn.commit()
        finally:
            conn.close()

    def load(self) -> None:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT region, state_blob FROM specialized_region_state").fetchall()
        finally:
            conn.close()
        for region, blob in rows:
            net = self.regions.get(region)
            if net is None:
                continue
            try:
                state = torch.load(io.BytesIO(blob), map_location="cpu", weights_only=True)
                net.load_state_dict(state.get("model", {}), strict=False)
                opt_state = state.get("optimizer")
                if opt_state:
                    net.opt.load_state_dict(opt_state)
            except Exception:
                continue
