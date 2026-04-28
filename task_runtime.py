"""
Runtime persistente para una plataforma agentica controlable.

Convierte cada objetivo y llamada de herramienta en registros auditables en
SQLite: que se pidio, si se permitio, cuanto duro y que devolvio.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from plastic_swarm_state import DEFAULT_DB


@dataclass(frozen=True)
class ToolCallRecord:
    task_id: str
    tool_name: str
    risk: str
    allowed: bool
    decision: str
    request: dict[str, Any]
    output: str
    error: str
    duration_s: float


class TaskRuntime:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS agent_tasks (
                task_id TEXT PRIMARY KEY,
                created TEXT,
                updated TEXT,
                status TEXT,
                objective TEXT,
                metadata TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tool_call_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                created TEXT,
                tool_name TEXT,
                risk TEXT,
                allowed INTEGER,
                decision TEXT,
                request TEXT,
                output TEXT,
                error TEXT,
                duration_s REAL
            )"""
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _compact(text: str, limit: int = 6000) -> str:
        return str(text or "").replace("\x00", "")[: max(200, int(limit))]

    def start_task(self, objective: str, *, metadata: dict[str, Any] | None = None) -> str:
        task_id = uuid.uuid4().hex
        now = self._now()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO agent_tasks
                   (task_id, created, updated, status, objective, metadata)
                   VALUES (?,?,?,?,?,?)""",
                (
                    task_id,
                    now,
                    now,
                    "running",
                    self._compact(objective, 4000),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return task_id

    def finish_task(self, task_id: str, *, status: str, metadata: dict[str, Any] | None = None) -> None:
        now = self._now()
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE agent_tasks
                   SET updated=?, status=?, metadata=?
                   WHERE task_id=?""",
                (now, status[:40], json.dumps(metadata or {}, ensure_ascii=False), task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def record_tool_call(self, record: ToolCallRecord) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tool_call_records
                   (task_id, created, tool_name, risk, allowed, decision,
                    request, output, error, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.task_id,
                    self._now(),
                    record.tool_name[:120],
                    record.risk[:30],
                    1 if record.allowed else 0,
                    self._compact(record.decision, 800),
                    self._compact(json.dumps(record.request, ensure_ascii=False), 4000),
                    self._compact(record.output),
                    self._compact(record.error, 3000),
                    float(record.duration_s),
                ),
            )
            conn.execute(
                "UPDATE agent_tasks SET updated=? WHERE task_id=?",
                (self._now(), record.task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def run_recorded(
        self,
        *,
        task_id: str,
        tool_name: str,
        risk: str,
        request: dict[str, Any],
        allowed: bool,
        decision: str,
        fn: Callable[[], Any] | None,
    ) -> Any:
        started = time.monotonic()
        output = ""
        error = ""
        result: Any = None
        if allowed and fn is not None:
            try:
                result = fn()
                output = str(result)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        self.record_tool_call(
            ToolCallRecord(
                task_id=task_id,
                tool_name=tool_name,
                risk=risk,
                allowed=allowed,
                decision=decision,
                request=request,
                output=output,
                error=error,
                duration_s=time.monotonic() - started,
            )
        )
        if error:
            raise RuntimeError(error)
        return result

    def recent_tool_calls(self, task_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, created, tool_name, risk, allowed, decision, request, output, error, duration_s
                   FROM tool_call_records
                   WHERE task_id=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (task_id, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": int(row[0]),
                "created": row[1],
                "tool_name": row[2],
                "risk": row[3],
                "allowed": bool(row[4]),
                "decision": row[5],
                "request": row[6],
                "output": row[7],
                "error": row[8],
                "duration_s": float(row[9] or 0.0),
            }
            for row in rows
        ]

    def list_tasks(self, *, limit: int = 30) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT task_id, created, updated, status, objective, metadata
                   FROM agent_tasks
                   ORDER BY updated DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        tasks = []
        for task_id, created, updated, status, objective, metadata in rows:
            try:
                meta = json.loads(metadata or "{}")
            except json.JSONDecodeError:
                meta = {}
            tasks.append(
                {
                    "task_id": task_id,
                    "created": created,
                    "updated": updated,
                    "status": status,
                    "objective": objective,
                    "metadata": meta,
                }
            )
        return tasks

    def tool_calls_for_task(self, task_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
        return self.recent_tool_calls(task_id, limit=limit)
