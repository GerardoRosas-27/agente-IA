from pathlib import Path

from harness.skill_registry import list_skills, set_skill_enabled, sync_skills


def test_sync_skills_creates_records_and_instructions(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "demo_tool.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    db_path = tmp_path / "state.db"

    records = sync_skills(skills_dir=skills_dir, db_path=db_path)

    assert len(records) == 1
    assert records[0].name == "demo_tool"
    assert records[0].enabled is True
    assert (skills_dir / "demo_tool.md").is_file()


def test_skill_enabled_state_persists(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "demo_tool.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    db_path = tmp_path / "state.db"

    sync_skills(skills_dir=skills_dir, db_path=db_path)
    set_skill_enabled("demo_tool", False, db_path=db_path)

    records = list_skills(db_path=db_path)
    assert records[0].enabled is False
