"""
Biblioteca persistente de herramientas reutilizables.

Guarda scripts, conectores o procedimientos que ya funcionaron para que el
ciclo agentico pueda recordarlos antes de volver a construirlos.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from multi_agent_orchestrator import text_hash_embed
from plastic_swarm_state import DEFAULT_DB


@dataclass(frozen=True)
class ToolMemory:
    name: str
    kind: str
    objective: str
    trigger_terms: str
    entrypoint: str
    instructions: str
    evidence: str
    success_count: int = 1
    failure_count: int = 0
    confidence: float = 0.5


def _clean(text: str, limit: int) -> str:
    value = str(text or "").strip()
    value = re.sub(r"(?i)\b(api[_-]?key|token|secret|password)\s*=\s*\S+", r"\1=<redacted>", value)
    value = value.replace("\x00", "")
    return value[: max(1, int(limit))]


def parse_tool_memory_request(text: str) -> tuple[bool, ToolMemory | None, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, None, "JSON no valido"

    reusable_raw = data.get("reutilizable", data.get("reusable", False))
    reusable = bool(reusable_raw)
    if isinstance(reusable_raw, str):
        reusable = reusable_raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    if not reusable:
        return False, None, str(data.get("motivo", data.get("reason", "no reutilizable")) or "")

    name = _clean(data.get("nombre", data.get("name", "")), 90)
    entrypoint = _clean(data.get("entrada", data.get("entrypoint", "")), 240)
    instructions = _clean(data.get("instrucciones", data.get("instructions", "")), 1200)
    if not name or not instructions:
        return False, None, "faltan nombre o instrucciones"

    memory = ToolMemory(
        name=name,
        kind=_clean(data.get("tipo", data.get("kind", "procedimiento")), 50),
        objective=_clean(data.get("objetivo", data.get("objective", "")), 500),
        trigger_terms=_clean(data.get("activadores", data.get("trigger_terms", "")), 350),
        entrypoint=entrypoint,
        instructions=instructions,
        evidence=_clean(data.get("evidencia", data.get("evidence", "")), 700),
    )
    return True, memory, "ok"


class ToolLibrary:
    def __init__(
        self,
        db_path: Path | None = None,
        *,
        capacity: int = 160,
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
                """CREATE TABLE IF NOT EXISTS tool_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT,
                objective TEXT,
                trigger_terms TEXT,
                entrypoint TEXT,
                instructions TEXT,
                evidence TEXT,
                success_count INTEGER DEFAULT 1,
                failure_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.5,
                created TEXT,
                updated TEXT,
                embedding TEXT,
                UNIQUE(name, entrypoint)
            )"""
            )
            for sql in (
                "ALTER TABLE tool_library ADD COLUMN confidence REAL DEFAULT 0.5",
            ):
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
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

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            t
            for t in re.findall(r"[a-záéíóúüñ0-9_./-]{3,}", text.lower())
            if t not in {"para", "como", "con", "que", "los", "las", "una", "uno", "por"}
        }

    @classmethod
    def _lexical_score(cls, query: str, text: str) -> float:
        q = cls._terms(query)
        d = cls._terms(text)
        if not q or not d:
            return 0.0
        overlap = len(q & d)
        if overlap <= 0:
            return 0.0
        return float(overlap / math.sqrt(len(q) * len(d)))

    def upsert(self, tool: ToolMemory) -> None:
        created = time.strftime("%Y-%m-%dT%H:%M:%S")
        search_text = "\n".join(
            [
                tool.name,
                tool.kind,
                tool.objective,
                tool.trigger_terms,
                tool.entrypoint,
                tool.instructions,
            ]
        )
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tool_library
                   (name, kind, objective, trigger_terms, entrypoint, instructions, evidence,
                    success_count, failure_count, confidence, created, updated, embedding)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(name, entrypoint) DO UPDATE SET
                       kind=excluded.kind,
                       objective=excluded.objective,
                       trigger_terms=excluded.trigger_terms,
                       instructions=excluded.instructions,
                       evidence=excluded.evidence,
                       success_count=success_count + 1,
                       confidence=min(1.0, confidence + 0.08),
                       updated=excluded.updated,
                       embedding=excluded.embedding""",
                (
                    tool.name,
                    tool.kind,
                    tool.objective,
                    tool.trigger_terms,
                    tool.entrypoint,
                    tool.instructions,
                    tool.evidence,
                    int(tool.success_count),
                    int(tool.failure_count),
                    float(max(0.0, min(1.0, tool.confidence))),
                    created,
                    created,
                    self._embed_json(search_text),
                ),
            )
            conn.execute(
                """DELETE FROM tool_library
                   WHERE id NOT IN (
                       SELECT id FROM tool_library
                       ORDER BY (success_count - failure_count) DESC, updated DESC
                       LIMIT ?
                   )""",
                (self.capacity,),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failure(self, name: str, entrypoint: str = "") -> None:
        if not name.strip():
            return
        updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn = self._connect()
        try:
            if entrypoint.strip():
                conn.execute(
                    """UPDATE tool_library
                       SET failure_count=failure_count + 1,
                           confidence=max(0.0, confidence - 0.12),
                           updated=?
                       WHERE name=? AND entrypoint=?""",
                    (updated, name.strip(), entrypoint.strip()),
                )
            else:
                conn.execute(
                    """UPDATE tool_library
                       SET failure_count=failure_count + 1,
                           confidence=max(0.0, confidence - 0.12),
                           updated=?
                       WHERE name=?""",
                    (updated, name.strip()),
                )
            conn.commit()
        finally:
            conn.close()

    def delete(self, name: str, entrypoint: str = "") -> int:
        if not name.strip():
            return 0
        conn = self._connect()
        try:
            if entrypoint.strip():
                cur = conn.execute(
                    "DELETE FROM tool_library WHERE name=? AND entrypoint=?",
                    (name.strip(), entrypoint.strip()),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM tool_library WHERE name=?",
                    (name.strip(),),
                )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    def list_entries(self, *, limit: int = 80) -> list[ToolMemory]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT name, kind, objective, trigger_terms, entrypoint, instructions,
                          evidence, success_count, failure_count, confidence
                   FROM tool_library
                   ORDER BY (success_count - failure_count) DESC, updated DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
        return [
            ToolMemory(
                name=str(row[0]),
                kind=str(row[1] or "procedimiento"),
                objective=str(row[2] or ""),
                trigger_terms=str(row[3] or ""),
                entrypoint=str(row[4] or ""),
                instructions=str(row[5] or ""),
                evidence=str(row[6] or ""),
                success_count=int(row[7] or 0),
                failure_count=int(row[8] or 0),
                confidence=float(row[9] if row[9] is not None else 0.5),
            )
            for row in rows
        ]

    def search(self, query: str, *, limit: int = 4) -> list[tuple[float, ToolMemory]]:
        q = query.strip()
        if not q:
            return []
        qv = json.loads(self._embed_json(q))
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT name, kind, objective, trigger_terms, entrypoint, instructions,
                          evidence, success_count, failure_count, confidence, embedding
                   FROM tool_library
                   ORDER BY updated DESC
                   LIMIT ?""",
                (self.capacity,),
            ).fetchall()
        finally:
            conn.close()

        ranked: list[tuple[float, ToolMemory]] = []
        for row in rows:
            (
                name,
                kind,
                objective,
                trigger_terms,
                entrypoint,
                instructions,
                evidence,
                success_count,
                failure_count,
                confidence,
                emb_json,
            ) = row
            text = "\n".join(
                [
                    str(name),
                    str(kind),
                    str(objective),
                    str(trigger_terms),
                    str(entrypoint),
                    str(instructions),
                    str(evidence),
                ]
            )
            try:
                ev = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                ev = []
            reliability = max(
                -1.0,
                min(
                    1.0,
                    (int(success_count) - int(failure_count))
                    / max(1, int(success_count) + int(failure_count)),
                ),
            )
            score = (
                0.45 * self._cosine(qv, ev)
                + 0.35 * self._lexical_score(q, text)
                + 0.12 * reliability
                + 0.08 * float(confidence or 0.5)
            )
            ranked.append(
                (
                    score,
                    ToolMemory(
                        name=str(name),
                        kind=str(kind or "procedimiento"),
                        objective=str(objective or ""),
                        trigger_terms=str(trigger_terms or ""),
                        entrypoint=str(entrypoint or ""),
                        instructions=str(instructions or ""),
                        evidence=str(evidence or ""),
                        success_count=int(success_count or 0),
                        failure_count=int(failure_count or 0),
                        confidence=float(confidence if confidence is not None else 0.5),
                    ),
                )
            )
        ranked.sort(reverse=True, key=lambda x: x[0])
        return ranked[: max(1, int(limit))]

    def context(self, query: str, *, limit: int = 4, max_chars: int = 1800) -> str:
        matches = self.search(query, limit=limit)
        if not matches:
            return ""
        lines = ["Biblioteca de herramientas reutilizables:"]
        for score, tool in matches:
            lines.append(
                "- "
                f"{tool.name} [{tool.kind}] score={score:.2f} ok={tool.success_count} fail={tool.failure_count}; "
                f"conf={tool.confidence:.2f}; "
                f"uso={tool.entrypoint or 'ver instrucciones'}; "
                f"activadores={tool.trigger_terms or '-'}; "
                f"instrucciones={tool.instructions[:420]}"
            )
        return "\n".join(lines)[: max(300, int(max_chars))]
