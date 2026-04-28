"""
Memoria persistente estilo Hermes para AgenteIA.

Capas:
- MEMORY.md: notas operativas y lecciones aprendidas por el agente.
- USER.md: preferencias persistentes del usuario.
- session_memory/session_memory_fts: archivo buscable de sesiones y resultados.
- CuradorMemoria: aplica acciones JSON seguras sobre esas capas.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plastic_swarm_state import DEFAULT_DB

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer)\b\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class MemoryAction:
    target: str
    action: str
    content: str
    reason: str = ""


def redact_sensitive(text: str) -> str:
    value = str(text or "").replace("\x00", "")
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)


def is_trivial_memory(text: str) -> bool:
    clean = str(text or "").strip()
    if len(clean) < 24:
        return True
    lowered = clean.lower()
    trivial = {
        "ok",
        "listo",
        "gracias",
        "hola",
        "sin aporte",
        "no hace falta",
    }
    return lowered in trivial


def parse_memory_curator_response(text: str) -> list[MemoryAction]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("acciones", data.get("actions", []))
    if not isinstance(items, list):
        return []
    actions: list[MemoryAction] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", item.get("destino", "")) or "").strip().lower()
        action = str(item.get("action", item.get("accion", "")) or "").strip().lower()
        content = redact_sensitive(str(item.get("content", item.get("contenido", "")) or "").strip())
        reason = str(item.get("reason", item.get("motivo", "")) or "").strip()[:400]
        if target not in {"memory", "user", "session"}:
            continue
        if action not in {"add", "replace", "remove"}:
            continue
        if action in {"add", "replace"} and is_trivial_memory(content):
            continue
        actions.append(MemoryAction(target=target, action=action, content=content[:1200], reason=reason))
    return actions[:12]


class PersistentMemoryStore:
    def __init__(
        self,
        root_dir: str | Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path(__file__).resolve().parent / "data" / "memories")
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.root_dir / "MEMORY.md"
        self.user_path = self.root_dir / "USER.md"
        self.db_path = db_path or DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file(self.memory_path, "# MEMORY\n\n")
        self._ensure_file(self.user_path, "# USER\n\n")
        self._init_db()

    @staticmethod
    def _ensure_file(path: Path, default: str) -> None:
        if not path.exists():
            path.write_text(default, encoding="utf-8")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT,
                objective TEXT,
                summary TEXT,
                outcome TEXT,
                metadata TEXT
            )"""
            )
            try:
                conn.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS session_memory_fts
                       USING fts5(objective, summary, outcome, content='session_memory', content_rowid='id')"""
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    def hot_context(self, *, max_chars: int = 2400) -> str:
        mem = self.memory_path.read_text(encoding="utf-8", errors="ignore")[: max_chars // 2]
        user = self.user_path.read_text(encoding="utf-8", errors="ignore")[: max_chars // 2]
        return (
            "--- MEMORY.md (memoria persistente del agente) ---\n"
            f"{mem.strip()}\n\n"
            "--- USER.md (preferencias persistentes del usuario) ---\n"
            f"{user.strip()}"
        )[:max_chars]

    def search_sessions(self, query: str, *, limit: int = 5, max_chars: int = 1400) -> str:
        q = str(query or "").strip()
        if not q:
            return ""
        conn = self._connect()
        try:
            try:
                rows = conn.execute(
                    """SELECT sm.created, sm.objective, sm.summary, sm.outcome
                       FROM session_memory_fts f
                       JOIN session_memory sm ON sm.id = f.rowid
                       WHERE session_memory_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (q.replace('"', " "), max(1, int(limit))),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{q[:80]}%"
                rows = conn.execute(
                    """SELECT created, objective, summary, outcome
                       FROM session_memory
                       WHERE objective LIKE ? OR summary LIKE ? OR outcome LIKE ?
                       ORDER BY id DESC LIMIT ?""",
                    (like, like, like, max(1, int(limit))),
                ).fetchall()
        finally:
            conn.close()
        if not rows:
            return ""
        lines = ["Sesiones anteriores relevantes:"]
        for created, objective, summary, outcome in rows:
            lines.append(
                f"- {created}: {str(objective)[:180]} | {str(summary)[:360]} | resultado={str(outcome)[:120]}"
            )
        return "\n".join(lines)[:max_chars]

    def record_session(
        self,
        *,
        objective: str,
        summary: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        summary = redact_sensitive(summary)[:1800]
        objective = redact_sensitive(objective)[:800]
        outcome = redact_sensitive(outcome)[:400]
        if is_trivial_memory(summary):
            return
        conn = self._connect()
        try:
            cur = conn.execute(
                """INSERT INTO session_memory (created, objective, summary, outcome, metadata)
                   VALUES (?,?,?,?,?)""",
                (
                    self._now(),
                    objective,
                    summary,
                    outcome,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            row_id = int(cur.lastrowid)
            try:
                conn.execute(
                    """INSERT INTO session_memory_fts (rowid, objective, summary, outcome)
                       VALUES (?,?,?,?)""",
                    (row_id, objective, summary, outcome),
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()

    def apply_actions(self, actions: list[MemoryAction]) -> list[str]:
        applied: list[str] = []
        for item in actions:
            path = self.memory_path if item.target == "memory" else self.user_path
            if item.target == "session":
                self.record_session(
                    objective=item.reason or "curated memory",
                    summary=item.content,
                    outcome="curated",
                )
                applied.append("session:add")
                continue
            current = path.read_text(encoding="utf-8", errors="ignore")
            if item.action == "add":
                if item.content not in current:
                    path.write_text(
                        current.rstrip() + f"\n- {item.content}\n",
                        encoding="utf-8",
                    )
                    applied.append(f"{item.target}:add")
            elif item.action == "replace":
                # Conservador: agrega la versión nueva y deja trazabilidad.
                path.write_text(
                    current.rstrip() + f"\n- ACTUALIZADO: {item.content}\n",
                    encoding="utf-8",
                )
                applied.append(f"{item.target}:replace")
            elif item.action == "remove" and item.content:
                updated = current.replace(item.content, "")
                path.write_text(updated, encoding="utf-8")
                applied.append(f"{item.target}:remove")
        return applied
