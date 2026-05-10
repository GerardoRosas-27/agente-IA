"""Promoción de soluciones exitosas a skills reutilizables."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harness.evaluator import evaluate_changes
from harness.paths import REPO_ROOT, STATE_DB_PATH
from harness.shared_memory import record_skill_usage, remember


@dataclass(frozen=True)
class SkillPromotionResult:
    promoted: bool
    skill_path: Path
    doc_path: Path
    report: str


def _skill_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not name:
        name = "promoted_skill"
    if not name[0].isalpha():
        name = f"skill_{name}"
    return name


def promote_solution_to_skill(
    name: str,
    source: str,
    *,
    instructions: str,
    tests: list[str] | None = None,
    root: Path = REPO_ROOT,
    db_path: Path = STATE_DB_PATH,
) -> SkillPromotionResult:
    skill_name = _skill_name(name)
    skill_path = root / "skills" / f"{skill_name}.py"
    doc_path = root / "skills" / f"{skill_name}.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(source, encoding="utf-8")
    doc_path.write_text(
        f"# {skill_name}\n\n## Uso\n{instructions.strip()}\n",
        encoding="utf-8",
    )
    changed = [skill_path.relative_to(root).as_posix(), doc_path.relative_to(root).as_posix()]
    changed.extend(tests or [])
    evaluation = evaluate_changes(changed, root=root)
    promoted = evaluation.ok
    if promoted:
        record_skill_usage(
            skill_name,
            instructions[:200],
            f"Skill promovida desde solución exitosa. Archivo: {skill_path.relative_to(root).as_posix()}",
            success=True,
            outcome="promoted_from_successful_solution",
            db_path=db_path,
        )
        remember(
            "promoted_skill",
            skill_name,
            f"Skill `{skill_name}` promovida. Uso: {instructions[:500]}",
            tags=["skill", "promoted", skill_name],
            confidence=0.95,
            db_path=db_path,
        )
    return SkillPromotionResult(promoted, skill_path, doc_path, evaluation.report)
