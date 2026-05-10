from __future__ import annotations

from pathlib import Path

from harness.repo_index import build_repo_index, repo_context_for_goal, search_repo_index


def test_repo_index_extracts_python_symbols(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    src.mkdir()
    (src / "demo_tool.py").write_text(
        "import json\n\nclass DemoTool:\n    def run(self):\n        return json.dumps({})\n",
        encoding="utf-8",
    )

    entries = build_repo_index(root=tmp_path)

    assert entries
    entry = entries[0]
    assert entry.path == "skills/demo_tool.py"
    assert "DemoTool" in entry.symbols
    assert "run" in entry.symbols
    assert "json" in entry.imports


def test_repo_index_search_returns_related_files(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def webhook_handler():\n    pass\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def calculator():\n    pass\n", encoding="utf-8")

    matches = search_repo_index("webhook messages", root=tmp_path)
    context = repo_context_for_goal("webhook messages", root=tmp_path)

    assert matches[0].path == "alpha.py"
    assert "alpha.py" in context
