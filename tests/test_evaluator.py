from __future__ import annotations

from pathlib import Path

from harness.evaluator import evaluate_changes


def test_evaluator_compiles_changed_python(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = evaluate_changes(["demo.py"], root=tmp_path)

    assert result.ok
    assert "py_compile" in result.checks


def test_evaluator_uses_graph_related_tests(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = evaluate_changes(["src/calc.py"], root=tmp_path)

    assert result.ok
    assert "related_tests" in result.checks


def test_evaluator_blocks_dangerous_command(tmp_path: Path) -> None:
    result = evaluate_changes([], commands=["git reset --hard"], root=tmp_path)

    assert not result.ok
    assert "BLOQUEADO" in result.report
