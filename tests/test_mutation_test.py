from __future__ import annotations

from pathlib import Path

from harness.mutation_test import run_mutation_test


def test_strong_tests_kill_mutations(tmp_path: Path) -> None:
    """Si los tests son sólidos, las mutaciones deben morir (mutation_score alto)."""
    src = tmp_path / "calc.py"
    src.write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def is_positive(x):\n"
        "    return x > 0\n",
        encoding="utf-8",
    )
    tests = tmp_path / "test_calc.py"
    tests.write_text(
        "from calc import add, is_positive\n"
        "\n"
        "def test_add_basic():\n"
        "    assert add(2, 3) == 5\n"
        "    assert add(-1, 1) == 0\n"
        "\n"
        "def test_add_zero():\n"
        "    assert add(0, 0) == 0\n"
        "\n"
        "def test_is_positive():\n"
        "    assert is_positive(5)\n"
        "    assert not is_positive(0)\n"
        "    assert not is_positive(-3)\n",
        encoding="utf-8",
    )

    report = run_mutation_test(
        "calc.py",
        test_paths=["test_calc.py"],
        root=tmp_path,
        max_mutations=4,
    )

    assert report.total >= 1
    assert report.mutation_score >= 0.5, (
        f"mutation_score={report.mutation_score} demasiado bajo; "
        f"survivors={[(s.mutation.description, s.mutation.line) for s in report.survivors]}"
    )
    # Tras correr, el archivo debe quedar exactamente como antes.
    final = (tmp_path / "calc.py").read_text(encoding="utf-8")
    assert "return a + b" in final
    assert "return x > 0" in final


def test_weak_tests_let_mutations_survive(tmp_path: Path) -> None:
    """Tests fantasma que solo verifican que la función se puede llamar.
    Las mutaciones sobreviven y el reporte lo señala."""
    src = tmp_path / "calc.py"
    src.write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    tests = tmp_path / "test_calc_weak.py"
    tests.write_text(
        "from calc import add\n"
        "\n"
        "def test_add_runs_without_crash():\n"
        "    add(1, 2)  # no asserts: tests fantasma\n",
        encoding="utf-8",
    )

    report = run_mutation_test(
        "calc.py",
        test_paths=["test_calc_weak.py"],
        root=tmp_path,
        max_mutations=3,
    )

    assert report.total >= 1
    assert report.has_survivors, "tests sin asserts deberían dejar mutaciones vivas"
    assert report.mutation_score < 1.0


def test_no_mutations_for_constant_only_file(tmp_path: Path) -> None:
    src = tmp_path / "consts.py"
    src.write_text("VERSION = 'a'\n", encoding="utf-8")
    tests = tmp_path / "test_consts.py"
    tests.write_text("def test_truth():\n    assert True\n", encoding="utf-8")

    report = run_mutation_test(
        "consts.py",
        test_paths=["test_consts.py"],
        root=tmp_path,
        max_mutations=4,
    )

    # No hay operadores binarios ni booleanos: nada que mutar.
    assert report.total == 0
    assert report.mutation_score == 1.0


def test_run_mutation_test_handles_missing_file(tmp_path: Path) -> None:
    report = run_mutation_test(
        "ghost.py",
        test_paths=["test.py"],
        root=tmp_path,
    )
    assert report.total == 0
