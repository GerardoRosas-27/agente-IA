"""
Regiones cognitivas complementarias inspiradas en un cerebro modular.

Estas regiones no reemplazan al ciclo multiagente ni a SharedFlyMemory. Aportan
memoria, control, prediccion, valoracion, atencion y reposo como subsistemas
persistentes y entrenables.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import torch
import torch.nn as nn
import torch.optim as optim

from multi_agent_orchestrator import text_hash_embed
from persistent_memory import PersistentMemoryStore
from plastic_swarm_state import DEFAULT_DB
from skill_manager import SkillManager
from task_runtime import TaskRuntime


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _clip(v: float) -> float:
    return max(-1.0, min(1.0, float(v)))


def _terms(text: str) -> set[str]:
    import re

    return {
        t
        for t in re.findall(r"[a-záéíóúüñ0-9_./-]{3,}", str(text).lower())
        if t not in {"para", "como", "con", "que", "los", "las", "una", "uno", "por"}
    }


class _TinyRegionNet(nn.Module):
    def __init__(self, in_dim: int, out_dim: int, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden, hidden // 2),
            nn.LeakyReLU(0.1),
            nn.Linear(hidden // 2, out_dim),
            nn.Tanh(),
        )
        self.opt = optim.Adam(self.parameters(), lr=0.002)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim == 1:
            x = x.unsqueeze(0)
        return self.net(x)

    def train_one(self, x: torch.Tensor, y: torch.Tensor, *, steps: int = 5) -> dict[str, float]:
        stats = {"free_energy": 0.0, "prediction_error": 0.0, "complexity": 0.0, "entropy": 0.0}
        for _ in range(max(1, int(steps))):
            self.opt.zero_grad()
            pred = self.forward(x).squeeze(0)
            prediction_error = torch.nn.functional.mse_loss(pred, y)
            complexity = torch.zeros((), dtype=torch.float32)
            for param in self.parameters():
                complexity = complexity + param.pow(2).mean()
            entropy = torch.log1p(pred.var(unbiased=False) + x.var(unbiased=False))
            free_energy = prediction_error + 0.0005 * complexity - 0.02 * entropy
            free_energy.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters(), 2.5)
            self.opt.step()
            stats = {
                "free_energy": float(free_energy.detach().cpu()),
                "prediction_error": float(prediction_error.detach().cpu()),
                "complexity": float((0.0005 * complexity).detach().cpu()),
                "entropy": float(entropy.detach().cpu()),
            }
        return stats


@dataclass(frozen=True)
class CognitiveContext:
    text: str
    homeostasis: dict[str, float]
    suggested_action: str
    confidence: float


class CognitiveSystem:
    def __init__(self, db_path: Path | None = None, *, embed_dim: int = 40) -> None:
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embed_dim = max(8, int(embed_dim))
        self.input_dim = self.embed_dim * 2 + 8
        self.action_net = _TinyRegionNet(self.input_dim, 6)
        self.prediction_net = _TinyRegionNet(self.input_dim, 4)
        self.value_net = _TinyRegionNet(self.input_dim, 3)
        self.meta_net = _TinyRegionNet(self.input_dim, 4)
        self._init_db()
        self._load_nets()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS episodic_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                objective TEXT,
                actors TEXT,
                outcome TEXT,
                value REAL,
                summary TEXT,
                metadata TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS semantic_edges (
                subject TEXT,
                relation TEXT,
                object TEXT,
                weight REAL DEFAULT 0.5,
                updated TEXT,
                PRIMARY KEY(subject, relation, object)
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS executive_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                updated TEXT,
                status TEXT,
                goal TEXT,
                priority REAL DEFAULT 0.5,
                metadata TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_model (
                key TEXT PRIMARY KEY,
                value TEXT,
                confidence REAL DEFAULT 0.5,
                updated TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS perception_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                source TEXT,
                kind TEXT,
                summary TEXT,
                salience REAL DEFAULT 0.5
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cognitive_region_state (
                region TEXT PRIMARY KEY,
                updated TEXT,
                state_blob BLOB
            )"""
            )
            conn.commit()
        finally:
            conn.close()

    def _encode(self, objective: str, context: str, metrics: dict[str, float] | None = None) -> torch.Tensor:
        metrics = metrics or {}
        obj = text_hash_embed(objective, self.embed_dim, "cpu").float()
        ctx = text_hash_embed(context, self.embed_dim, "cpu").float()
        feats = torch.tensor(
            [
                _clip(metrics.get("reached", 0.0)),
                _clip(metrics.get("risk", 0.0)),
                _clip(metrics.get("tool_success", 0.0)),
                _clip(metrics.get("tool_fail", 0.0)),
                _clip(metrics.get("cycles", 0.0)),
                _clip(metrics.get("confidence", 0.0)),
                _clip(metrics.get("cost", 0.0)),
                _clip(metrics.get("novelty", 0.0)),
            ],
            dtype=torch.float32,
        )
        return torch.cat([obj, ctx, feats], dim=0)

    def record_episode(
        self,
        *,
        objective: str,
        actors: list[str],
        outcome: str,
        value: float,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO episodic_memory
                   (created, objective, actors, outcome, value, summary, metadata)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _now(),
                    objective[:1000],
                    json.dumps(actors[:40], ensure_ascii=False),
                    outcome[:120],
                    float(_clip(value)),
                    summary[:1800],
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def episodic_context(self, query: str, *, limit: int = 5) -> str:
        q_terms = _terms(query)
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT created, objective, actors, outcome, value, summary
                   FROM episodic_memory ORDER BY id DESC LIMIT 80"""
            ).fetchall()
        finally:
            conn.close()
        ranked = []
        for row in rows:
            text = " ".join(str(x) for x in row)
            d_terms = _terms(text)
            score = len(q_terms & d_terms) / max(1.0, (len(q_terms) * len(d_terms)) ** 0.5)
            ranked.append((score, row))
        ranked.sort(reverse=True, key=lambda item: item[0])
        lines = ["Memoria episodica jerarquica:"]
        for score, row in ranked[: max(1, int(limit))]:
            if score <= 0 and q_terms:
                continue
            created, objective, actors, outcome, value, summary = row
            lines.append(
                f"- {created} outcome={outcome} value={float(value):+.2f} actors={actors}: {objective[:160]} | {summary[:260]}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""

    def upsert_semantic_edge(self, subject: str, relation: str, obj: str, *, delta: float = 0.08) -> None:
        subject = subject.strip()[:160]
        relation = relation.strip()[:80]
        obj = obj.strip()[:160]
        if not subject or not relation or not obj:
            return
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO semantic_edges (subject, relation, object, weight, updated)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(subject, relation, object) DO UPDATE SET
                       weight=min(1.0, weight + ?),
                       updated=excluded.updated""",
                (subject, relation, obj, 0.5, _now(), float(delta)),
            )
            conn.commit()
        finally:
            conn.close()

    def semantic_context(self, query: str, *, limit: int = 8) -> str:
        q_terms = _terms(query)
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT subject, relation, object, weight
                   FROM semantic_edges ORDER BY weight DESC, updated DESC LIMIT 120"""
            ).fetchall()
        finally:
            conn.close()
        ranked = []
        for subject, relation, obj, weight in rows:
            text = f"{subject} {relation} {obj}"
            score = len(q_terms & _terms(text)) + float(weight)
            ranked.append((score, subject, relation, obj, weight))
        ranked.sort(reverse=True, key=lambda item: item[0])
        lines = ["Corteza semantica:"]
        for _score, subject, relation, obj, weight in ranked[: max(1, int(limit))]:
            lines.append(f"- {subject} --{relation}/{float(weight):.2f}--> {obj}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def set_user_fact(self, key: str, value: str, *, confidence: float = 0.5) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO user_model (key, value, confidence, updated)
                   VALUES (?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value,
                       confidence=max(confidence, excluded.confidence),
                       updated=excluded.updated""",
                (key[:120], value[:1000], float(max(0.0, min(1.0, confidence))), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def user_context(self, *, limit: int = 8) -> str:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT key, value, confidence FROM user_model
                   ORDER BY confidence DESC, updated DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return ""
        lines = ["Modelo estructurado del usuario:"]
        for key, value, confidence in rows:
            lines.append(f"- {key} conf={float(confidence):.2f}: {str(value)[:240]}")
        return "\n".join(lines)

    def update_goal(self, goal: str, *, status: str = "active", priority: float = 0.5) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO executive_goals (created, updated, status, goal, priority, metadata)
                   VALUES (?,?,?,?,?,?)""",
                (_now(), _now(), status[:40], goal[:1000], float(max(0.0, min(1.0, priority))), "{}"),
            )
            conn.commit()
        finally:
            conn.close()

    def executive_context(self, *, limit: int = 5) -> str:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT status, goal, priority FROM executive_goals
                   WHERE status!='done' ORDER BY priority DESC, updated DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return ""
        lines = ["Control ejecutivo / metas persistentes:"]
        for status, goal, priority in rows:
            lines.append(f"- {status} p={float(priority):.2f}: {str(goal)[:260]}")
        return "\n".join(lines)

    def record_perception(self, source: str, kind: str, summary: str, *, salience: float = 0.5) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO perception_events (created, source, kind, summary, salience)
                   VALUES (?,?,?,?,?)""",
                (_now(), source[:120], kind[:80], summary[:1000], float(max(0.0, min(1.0, salience)))),
            )
            conn.commit()
        finally:
            conn.close()

    def homeostasis(self, *, busy: bool = False, recent_errors: int = 0) -> dict[str, float]:
        fatigue = min(1.0, recent_errors / 8.0 + (0.2 if busy else 0.0))
        risk_budget = max(0.1, 0.8 - fatigue)
        exploration = max(0.05, 0.45 - fatigue * 0.25)
        token_budget = max(0.35, 1.0 - fatigue * 0.4)
        return {
            "fatigue": fatigue,
            "risk_budget": risk_budget,
            "exploration": exploration,
            "token_budget": token_budget,
        }

    def neural_context(self, objective: str, context: str, metrics: dict[str, float] | None = None) -> tuple[str, str, float]:
        x = self._encode(objective, context, metrics)
        labels = {
            "action": ["razonar", "buscar", "ejecutar", "preguntar", "crear_skill", "pausar"],
            "prediction": ["duracion", "fallo", "ciclos", "formato"],
            "value": ["utilidad", "riesgo", "saliencia"],
            "meta": ["no_se", "debil", "mas_evidencia", "repeticion_error"],
        }
        nets = {
            "action": self.action_net,
            "prediction": self.prediction_net,
            "value": self.value_net,
            "meta": self.meta_net,
        }
        lines = ["Regiones cognitivas aprendidas:"]
        suggested_action = "razonar"
        confidence = 0.0
        for name, net in nets.items():
            net.eval()
            with torch.no_grad():
                values = net(x).squeeze(0).detach().cpu().tolist()
            ranked = sorted(zip(labels[name], values), key=lambda item: item[1], reverse=True)
            if name == "action":
                suggested_action = ranked[0][0]
                confidence = float(ranked[0][1])
            lines.append(f"- {name}: " + ", ".join(f"{label}={float(value):+.2f}" for label, value in ranked))
        return "\n".join(lines), suggested_action, confidence

    def context(self, objective: str, *, busy: bool = False, recent_errors: int = 0) -> CognitiveContext:
        homeo = self.homeostasis(busy=busy, recent_errors=recent_errors)
        base_context = "\n".join(
            part
            for part in [
                self.episodic_context(objective),
                self.semantic_context(objective),
                self.user_context(),
                self.executive_context(),
            ]
            if part
        )
        neural, suggested_action, confidence = self.neural_context(objective, base_context, homeo)
        text = "\n\n".join([part for part in [base_context, neural, f"Homeostasis: {homeo}"] if part])
        return CognitiveContext(text=text[:3200], homeostasis=homeo, suggested_action=suggested_action, confidence=confidence)

    def learn_from_cycle(
        self,
        *,
        objective: str,
        actors: list[str],
        reached: bool,
        cycles_used: int,
        tool_calls: list[dict[str, Any]],
        final_answer: str,
        created_skill: bool,
    ) -> dict[str, dict[str, float]]:
        value = 1.0 if reached else -0.35
        if any(call.get("error") or not call.get("allowed") for call in tool_calls):
            value -= 0.15
        summary = f"reached={reached}; cycles={cycles_used}; final={final_answer[:600]}"
        self.record_episode(
            objective=objective,
            actors=actors,
            outcome="succeeded" if reached else "not_certified",
            value=value,
            summary=summary,
            metadata={"created_skill": created_skill, "tool_calls": len(tool_calls)},
        )
        for call in tool_calls:
            tool = str(call.get("tool_name") or "")
            if tool:
                self.upsert_semantic_edge(tool, "used_for", objective, delta=0.05 if reached else -0.02)
                if call.get("error") or not call.get("allowed"):
                    self.upsert_semantic_edge(tool, "risk_on", objective, delta=0.06)
        if "prefiere" in objective.lower() or "quiero" in objective.lower():
            self.set_user_fact("latest_preference_signal", objective[:400], confidence=0.45)
        self.update_goal(objective, status="done" if reached else "active", priority=0.7 if not reached else 0.3)
        metrics = {
            "reached": 1.0 if reached else -1.0,
            "risk": 1.0 if any(str(c.get("risk", "")).lower() in {"high", "dangerous"} for c in tool_calls) else 0.0,
            "tool_success": min(1.0, len([c for c in tool_calls if c.get("allowed") and not c.get("error")]) / max(1, len(tool_calls))),
            "tool_fail": min(1.0, len([c for c in tool_calls if c.get("error") or not c.get("allowed")]) / max(1, len(tool_calls))),
            "cycles": min(1.0, cycles_used / 10.0),
            "confidence": 0.8 if reached else -0.2,
            "cost": min(1.0, sum(float(c.get("duration_s") or 0.0) for c in tool_calls) / 60.0),
            "novelty": 0.7 if created_skill else 0.2,
        }
        x = self._encode(objective, summary, metrics)
        targets = {
            "action": torch.tensor([0.6, 0.4, 0.5 if tool_calls else -0.2, -0.1, 0.7 if created_skill else -0.1, -0.3 if reached else 0.4]),
            "prediction": torch.tensor([metrics["cost"], metrics["tool_fail"], metrics["cycles"], 0.4 if reached else -0.2]),
            "value": torch.tensor([value, metrics["risk"], 0.8 if created_skill else 0.3]),
            "meta": torch.tensor([-0.5 if reached else 0.5, -0.4 if reached else 0.4, -0.5 if reached else 0.6, metrics["tool_fail"]]),
        }
        stats = {
            "action": self.action_net.train_one(x, targets["action"]),
            "prediction": self.prediction_net.train_one(x, targets["prediction"]),
            "value": self.value_net.train_one(x, targets["value"]),
            "meta": self.meta_net.train_one(x, targets["meta"]),
        }
        self._save_nets()
        return stats

    def consolidate_rest(
        self,
        *,
        runtime: TaskRuntime | None = None,
        memory_store: PersistentMemoryStore | None = None,
        skill_manager: SkillManager | None = None,
    ) -> str:
        runtime = runtime or TaskRuntime(self.db_path)
        memory_store = memory_store or PersistentMemoryStore(db_path=self.db_path)
        skill_manager = skill_manager or SkillManager()
        tasks = runtime.list_tasks(limit=12)
        lines = ["Rest cycle consolidation:"]
        for task in tasks:
            status = str(task.get("status") or "")
            objective = str(task.get("objective") or "")
            calls = runtime.tool_calls_for_task(str(task["task_id"]), limit=30)
            if status in {"succeeded", "max_cycles"}:
                self.learn_from_cycle(
                    objective=objective,
                    actors=[str(c.get("tool_name")) for c in calls],
                    reached=status == "succeeded",
                    cycles_used=int((task.get("metadata") or {}).get("cycles", 1) or 1),
                    tool_calls=calls,
                    final_answer=status,
                    created_skill=False,
                )
                memory_store.record_session(objective=objective, summary=f"rest_cycle status={status}", outcome=status)
        repaired = skill_manager.repair_manifests()
        lines.append(f"tasks={len(tasks)} repaired_manifests={len(repaired)}")
        return "\n".join(lines)

    def _save_nets(self) -> None:
        import io

        conn = self._connect()
        try:
            for name, net in {
                "action": self.action_net,
                "prediction": self.prediction_net,
                "value": self.value_net,
                "meta": self.meta_net,
            }.items():
                bio = io.BytesIO()
                torch.save({"model": net.state_dict(), "optimizer": net.opt.state_dict()}, bio)
                conn.execute(
                    """INSERT OR REPLACE INTO cognitive_region_state (region, updated, state_blob)
                       VALUES (?,?,?)""",
                    (name, _now(), bio.getvalue()),
                )
            conn.commit()
        finally:
            conn.close()

    def _load_nets(self) -> None:
        import io

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT region, state_blob FROM cognitive_region_state"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        finally:
            conn.close()
        nets = {
            "action": self.action_net,
            "prediction": self.prediction_net,
            "value": self.value_net,
            "meta": self.meta_net,
        }
        for region, blob in rows:
            net = nets.get(region)
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


def start_rest_cycle_worker(
    *,
    is_idle: Callable[[], bool],
    on_log: Callable[[str, str], None],
    interval_s: int,
    cognitive_system: CognitiveSystem,
    runtime: TaskRuntime,
    memory_store: PersistentMemoryStore,
    skill_manager: SkillManager,
) -> threading.Thread:
    def _job() -> None:
        on_log("RestCycle", "Worker de reposo iniciado.")
        while True:
            time.sleep(max(60, int(interval_s)))
            if not is_idle():
                continue
            try:
                out = cognitive_system.consolidate_rest(
                    runtime=runtime,
                    memory_store=memory_store,
                    skill_manager=skill_manager,
                )
                on_log("RestCycle", out)
            except Exception as exc:
                on_log("RestCycle", f"Fallo en reposo: {exc}")

    thread = threading.Thread(target=_job, daemon=True)
    thread.start()
    return thread
