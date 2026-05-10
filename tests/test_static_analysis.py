from __future__ import annotations

from pathlib import Path

from harness.static_analysis import LintResult, lint_changed_files


def test_lint_clean_file_passes(tmp_path: Path) -> None:
    src = tmp_path / "good.py"
    src.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    result = lint_changed_files(["good.py"], root=tmp_path)

    if not result.available:
        # entorno sin ruff: ok pero marcado no-disponible.
        assert result.ok and result.issues == 0
        return
    assert result.ok
    assert result.issues == 0
    assert "good.py" in result.files_checked


def test_lint_detects_undefined_name(tmp_path: Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("def f():\n    return undefined_symbol\n", encoding="utf-8")

    result = lint_changed_files(["bad.py"], root=tmp_path)

    if not result.available:
        return
    assert not result.ok
    assert result.issues >= 1
    assert "undefined_symbol" in result.output or "F821" in result.output


def test_lint_skips_non_python_files(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")

    result = lint_changed_files(["data.json"], root=tmp_path)

    assert isinstance(result, LintResult)
    assert result.ok
    assert result.issues == 0


def test_lint_handles_missing_files_gracefully(tmp_path: Path) -> None:
    result = lint_changed_files(["does_not_exist.py"], root=tmp_path)
    assert result.ok
