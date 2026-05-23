"""Registro persistente de skills disponibles para el harness."""
from __future__ import annotations

import sqlite3
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harness.paths import REPO_ROOT, STATE_DB_PATH
from harness.shared_memory import skill_memory_context
from harness.skill_runtime import start_skill_runtime, stop_skill_runtime


SKILLS_DIR = REPO_ROOT / "skills"


@dataclass(frozen=True)
class SkillRecord:
    name: str
    path: str
    enabled: bool
    instructions: str


def _connect(db_path: Path = STATE_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_skill_db(db_path: Path = STATE_DB_PATH) -> None:
    """Crea la tabla de skills si no existe."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                instructions TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )


def _default_instructions(skill_path: Path) -> str:
    return (
        f"# {skill_path.stem}\n\n"
        "## Uso\n"
        f"- Código: `{skill_path.as_posix()}`\n"
        "- Importa este módulo desde el harness solo cuando la skill esté habilitada.\n"
        "- Mantén aquí instrucciones concretas para que el sistema sepa cuándo usarla.\n"
    )


def _ensure_instruction_file(skill_path: Path) -> str:
    instruction_path = skill_path.with_suffix(".md")
    if not instruction_path.exists():
        instruction_path.write_text(_default_instructions(skill_path), encoding="utf-8")
    return instruction_path.read_text(encoding="utf-8", errors="ignore")


def _skill_record_values(skill_path: Path, skills_dir: Path) -> tuple[str, str, str]:
    """Devuelve (name, rel_path, instructions) para skills Python o SKILL.md."""
    if skill_path.name == "SKILL.md":
        name = skill_path.parent.name
        instructions = skill_path.read_text(encoding="utf-8", errors="ignore")
    else:
        name = skill_path.stem
        instructions = _ensure_instruction_file(skill_path)
    try:
        rel_path = skill_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        try:
            rel_path = skill_path.relative_to(skills_dir.parent).as_posix()
        except ValueError:
            rel_path = skill_path.as_posix()
    return name, rel_path, instructions


def _iter_skill_entries(skills_dir: Path) -> list[Path]:
    """Lista skills ejecutables locales y skills Markdown tipo upstream/SKILL.md."""
    entries: list[Path] = []
    for skill_path in sorted(skills_dir.glob("*.py")):
        if skill_path.name != "__init__.py":
            entries.append(skill_path)
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        entries.append(skill_md)
    return entries


def sync_skills(
    skills_dir: Path = SKILLS_DIR,
    db_path: Path = STATE_DB_PATH,
) -> list[SkillRecord]:
    """Sincroniza `skills/*.py` con SQLite y asegura instrucciones Markdown."""
    init_skill_db(db_path)
    skills_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    with _connect(db_path) as conn:
        for skill_path in _iter_skill_entries(skills_dir):
            skill_name, rel_path, instructions = _skill_record_values(skill_path, skills_dir)
            row = conn.execute(
                "SELECT name FROM skills WHERE name = ?",
                (skill_name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO skills (name, path, enabled, instructions, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (skill_name, rel_path, instructions, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE skills
                    SET path = ?, instructions = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (rel_path, instructions, now, skill_name),
                )

    return list_skills(db_path)


def import_markdown_skills(
    source_dir: Path,
    *,
    skills_dir: Path = SKILLS_DIR,
    names: list[str] | None = None,
    db_path: Path = STATE_DB_PATH,
) -> list[SkillRecord]:
    """Importa carpetas `*/SKILL.md` de un pack externo al directorio local de skills."""
    selected = set(names or [])
    imported: list[str] = []
    for source_skill in sorted(source_dir.glob("*/SKILL.md")):
        name = source_skill.parent.name
        if selected and name not in selected:
            continue
        target_dir = skills_dir / name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_skill, target_dir / "SKILL.md")
        imported.append(name)
    records = sync_skills(skills_dir=skills_dir, db_path=db_path)
    if not imported:
        return []
    imported_set = set(imported)
    return [record for record in records if record.name in imported_set]


def list_skills(db_path: Path = STATE_DB_PATH) -> list[SkillRecord]:
    init_skill_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, path, enabled, instructions FROM skills ORDER BY name"
        ).fetchall()
    return [
        SkillRecord(
            name=str(row["name"]),
            path=str(row["path"]),
            enabled=bool(row["enabled"]),
            instructions=str(row["instructions"] or ""),
        )
        for row in rows
    ]


def set_skill_enabled(name: str, enabled: bool, db_path: Path = STATE_DB_PATH) -> None:
    init_skill_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE skills SET enabled = ?, updated_at = ? WHERE name = ?",
            (1 if enabled else 0, now, name),
        )
    if enabled:
        start_skill_runtime(name, db_path=db_path)
    else:
        stop_skill_runtime(name, db_path=db_path)


def is_skill_enabled(name: str, db_path: Path = STATE_DB_PATH) -> bool:
    sync_skills(db_path=db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT enabled FROM skills WHERE name = ?",
            (name,),
        ).fetchone()
    return bool(row and row["enabled"])


def enabled_skills_context(db_path: Path = STATE_DB_PATH) -> str:
    """Devuelve un resumen breve de skills habilitadas para incluir en prompts."""
    records = [skill for skill in sync_skills(db_path=db_path) if skill.enabled]
    if not records:
        return "No hay skills habilitadas."

    chunks: list[str] = []
    for skill in records:
        instructions = skill.instructions.strip()
        if len(instructions) > 900:
            instructions = instructions[:900] + "\n... [truncado]"
        chunks.append(
            f"- {skill.name} ({skill.path})\n"
            f"  Instrucciones:\n{instructions}"
        )
    learned = skill_memory_context(db_path=db_path)
    return "\n\n".join(chunks) + "\n\n--- Aprendizajes previos de skills ---\n" + learned
