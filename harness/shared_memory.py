"""Memoria compartida y aprendizaje operativo del harness."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.paths import STATE_DB_PATH


TOKEN_RE = re.compile(r"[a-z0-9_áéíóúñ]+", re.IGNORECASE)


@dataclass(frozen=True)
class MemoryItem:
    id: int
    scope: str
    key: str
    value: str
    tags: tuple[str, ...]
    confidence: float
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SelfImprovementItem:
    id: int
    source: str
    proposal: str
    status: str
    evidence: str
    created_at: str


def _connect(db_path: Path = STATE_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db(db_path: Path = STATE_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                use_case TEXT NOT NULL,
                instructions TEXT NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_outcome TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(skill_name, use_case)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_improvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                proposal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                evidence TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )


def remember(
    scope: str,
    key: str,
    value: str,
    *,
    tags: list[str] | tuple[str, ...] | None = None,
    confidence: float = 1.0,
    db_path: Path = STATE_DB_PATH,
) -> None:
    """Guarda o actualiza una memoria compartida."""
    init_memory_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    tag_text = json.dumps(list(tags or []), ensure_ascii=False)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO shared_memory
                (scope, key, value, tags, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, key) DO UPDATE SET
                value = excluded.value,
                tags = excluded.tags,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (scope, key, value, tag_text, confidence, now, now),
        )


def _row_to_memory(row: sqlite3.Row) -> MemoryItem:
    try:
        tags = tuple(json.loads(row["tags"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        tags = ()
    return MemoryItem(
        id=int(row["id"]),
        scope=str(row["scope"]),
        key=str(row["key"]),
        value=str(row["value"]),
        tags=tags,
        confidence=float(row["confidence"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in TOKEN_RE.findall(text):
        lowered = raw.lower()
        parts = [lowered, *lowered.split("_")]
        tokens.update(part for part in parts if len(part) >= 3)
    return tokens


def recall(
    *,
    scope: str | None = None,
    query: str = "",
    limit: int = 12,
    db_path: Path = STATE_DB_PATH,
) -> list[MemoryItem]:
    """Recupera memorias por scope y búsqueda textual simple."""
    init_memory_db(db_path)
    sql = "SELECT * FROM shared_memory"
    params: list[Any] = []
    clauses: list[str] = []
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if query:
        like = f"%{query}%"
        clauses.append("(key LIKE ? OR value LIKE ? OR tags LIKE ?)")
        params.extend([like, like, like])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(int(limit))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(row) for row in rows]


def semantic_recall(
    *,
    query: str,
    scope: str | None = None,
    limit: int = 12,
    db_path: Path = STATE_DB_PATH,
) -> list[MemoryItem]:
    """Recupera memorias por similitud léxica ponderada sin depender de embeddings externos."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return recall(scope=scope, limit=limit, db_path=db_path)
    candidates = recall(scope=scope, limit=max(limit * 8, 50), db_path=db_path)
    scored: list[tuple[float, MemoryItem]] = []
    for item in candidates:
        haystack = f"{item.scope} {item.key} {item.value} {' '.join(item.tags)}"
        item_tokens = _tokens(haystack)
        overlap = query_tokens & item_tokens
        if not overlap:
            continue
        score = (len(overlap) / len(query_tokens)) + (0.15 * item.confidence)
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].updated_at), reverse=False)
    return [item for _score, item in scored[: max(limit, 0)]]


def record_skill_usage(
    skill_name: str,
    use_case: str,
    instructions: str,
    *,
    success: bool,
    outcome: str,
    db_path: Path = STATE_DB_PATH,
) -> None:
    """Aprende cómo se usa una skill y si funcionó."""
    init_memory_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skill_usage
                (skill_name, use_case, instructions, success_count, failure_count, last_outcome, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name, use_case) DO UPDATE SET
                instructions = excluded.instructions,
                success_count = success_count + excluded.success_count,
                failure_count = failure_count + excluded.failure_count,
                last_outcome = excluded.last_outcome,
                updated_at = excluded.updated_at
            """,
            (
                skill_name,
                use_case,
                instructions,
                1 if success else 0,
                0 if success else 1,
                outcome,
                now,
            ),
        )


def skill_memory_context(limit: int = 8, db_path: Path = STATE_DB_PATH) -> str:
    """Resumen de aprendizajes acumulados sobre uso de skills."""
    init_memory_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT skill_name, use_case, instructions, success_count, failure_count, last_outcome
            FROM skill_usage
            ORDER BY success_count DESC, updated_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    if not rows:
        return "No hay aprendizajes previos de skills."
    chunks = []
    for row in rows:
        chunks.append(
            f"- Skill `{row['skill_name']}` para `{row['use_case']}`: "
            f"{row['instructions']} "
            f"(éxitos={row['success_count']}, fallos={row['failure_count']}, "
            f"último={row['last_outcome']})"
        )
    return "\n".join(chunks)


def add_self_improvement(
    source: str,
    proposal: str,
    *,
    evidence: str = "",
    db_path: Path = STATE_DB_PATH,
) -> None:
    """Registra una propuesta de auto-mejora para revisión posterior."""
    init_memory_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO self_improvements (source, proposal, status, evidence, created_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (source, proposal, evidence, now),
        )


def self_improvement_context(limit: int = 6, db_path: Path = STATE_DB_PATH) -> str:
    init_memory_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source, proposal, evidence, created_at
            FROM self_improvements
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    if not rows:
        return "No hay propuestas de auto-mejora pendientes."
    return "\n".join(
        f"- [{row['created_at']}] {row['source']}: {row['proposal']} Evidencia: {row['evidence']}"
        for row in rows
    )


def list_self_improvements(
    *,
    status: str | None = None,
    limit: int = 100,
    db_path: Path = STATE_DB_PATH,
) -> list[SelfImprovementItem]:
    """Lista auto-mejoras en una cola separada de tareas de usuario."""
    init_memory_db(db_path)
    sql = "SELECT id, source, proposal, status, evidence, created_at FROM self_improvements"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        SelfImprovementItem(
            id=int(row["id"]),
            source=str(row["source"]),
            proposal=str(row["proposal"]),
            status=str(row["status"]),
            evidence=str(row["evidence"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def shared_memory_context(query: str = "", limit: int = 10, db_path: Path = STATE_DB_PATH) -> str:
    """Contexto compacto para el orquestador."""
    memories = semantic_recall(query=query, limit=limit, db_path=db_path) if query else recall(limit=limit, db_path=db_path)
    if not memories:
        return "No hay memoria compartida relevante todavía."
    return "\n".join(
        f"- ({item.scope}) {item.key}: {item.value}"
        for item in memories
    )
