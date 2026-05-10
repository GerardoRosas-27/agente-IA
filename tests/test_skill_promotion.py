from __future__ import annotations

from pathlib import Path

from harness.shared_memory import recall
from harness.skill_promotion import promote_solution_to_skill


def test_promote_solution_to_skill_writes_files_and_memory(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    result = promote_solution_to_skill(
        "Demo Skill",
        "def run(request: dict) -> dict:\n    return {'ok': True}\n",
        instructions="Llamar run({}) para probar la skill.",
        root=tmp_path,
        db_path=tmp_path / "state.db",
    )

    assert result.promoted
    assert result.skill_path.is_file()
    assert result.doc_path.is_file()
    assert recall(scope="promoted_skill", query="demo", db_path=tmp_path / "state.db")
