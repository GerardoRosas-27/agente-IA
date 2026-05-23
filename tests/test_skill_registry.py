from pathlib import Path

from harness.skill_registry import import_markdown_skills, list_skills, set_skill_enabled, sync_skills


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


def test_sync_skills_discovers_folder_skill_md(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    folder = skills_dir / "demo-markdown"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: demo-markdown\n---\n# Demo\n", encoding="utf-8")
    db_path = tmp_path / "state.db"

    records = sync_skills(skills_dir=skills_dir, db_path=db_path)

    assert len(records) == 1
    assert records[0].name == "demo-markdown"
    assert records[0].path.endswith("demo-markdown/SKILL.md")
    assert "# Demo" in records[0].instructions


def test_import_markdown_skills_copies_selected_skill(tmp_path: Path) -> None:
    source = tmp_path / "upstream" / "skills"
    (source / "keep").mkdir(parents=True)
    (source / "skip").mkdir()
    (source / "keep" / "SKILL.md").write_text("# Keep\n", encoding="utf-8")
    (source / "skip" / "SKILL.md").write_text("# Skip\n", encoding="utf-8")
    target = tmp_path / "local_skills"
    db_path = tmp_path / "state.db"

    records = import_markdown_skills(source, skills_dir=target, names=["keep"], db_path=db_path)

    assert [record.name for record in records] == ["keep"]
    assert (target / "keep" / "SKILL.md").read_text(encoding="utf-8") == "# Keep\n"
    assert not (target / "skip").exists()
