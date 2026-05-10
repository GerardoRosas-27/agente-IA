from __future__ import annotations

from pathlib import Path

from harness.test_generator import (
    GeneratedTestSuite,
    evaluate_brt_against_candidate,
    generate_repro_tests,
    run_pytest_on_file,
)


def _fake_llm_passing_brt(*_args, **_kwargs) -> str:
    return (
        "```python\n"
        "def test_brt_one_plus_one():\n"
        "    assert 1 + 1 == 2\n"
        "```\n"
    )


def _fake_llm_failing_brt(*_args, **_kwargs) -> str:
    return (
        "```python\n"
        "def test_brt_module_exists():\n"
        "    import pkg.future_module  # noqa: F401\n"
        "```\n"
    )


def _fake_llm_no_python_block(*_args, **_kwargs) -> str:
    return "Lo siento, no puedo escribir tests sin más contexto."


def test_generate_repro_tests_writes_file_and_runs_in_head(tmp_path: Path) -> None:
    feature = {
        "id": 99,
        "name": "demo",
        "title": "Demo BRT",
        "description": "Suma simple",
        "acceptance": ["1+1 debe ser 2"],
    }

    suite = generate_repro_tests(
        feature,
        invoke_llm=_fake_llm_passing_brt,
        model="dummy",
        base_dir=tmp_path / "repro",
        root=tmp_path,
        run_after_generation=True,
    )

    assert isinstance(suite, GeneratedTestSuite)
    assert suite.test_path.is_file()
    assert "test_brt_one_plus_one" in suite.test_code
    # 1+1 ya pasa en HEAD: el BRT entonces NO falla, lo cual es señal de que
    # los criterios escogidos por el LLM no diferencian la feature.
    assert suite.initial_status == "passes_in_head"


def test_generate_repro_tests_detects_fail_in_head(tmp_path: Path) -> None:
    feature = {
        "id": 100,
        "name": "missing_module",
        "title": "Importa módulo inexistente",
        "description": "...",
        "acceptance": ["Debe existir pkg.future_module"],
    }

    suite = generate_repro_tests(
        feature,
        invoke_llm=_fake_llm_failing_brt,
        model="dummy",
        base_dir=tmp_path / "repro",
        root=tmp_path,
        run_after_generation=True,
    )

    assert suite.initial_status == "fails_in_head"


def test_generate_repro_tests_falls_back_when_llm_returns_garbage(tmp_path: Path) -> None:
    feature = {"id": 101, "name": "broken", "title": "x", "description": "y", "acceptance": []}

    suite = generate_repro_tests(
        feature,
        invoke_llm=_fake_llm_no_python_block,
        model="dummy",
        base_dir=tmp_path / "repro",
        root=tmp_path,
        run_after_generation=False,
    )

    assert suite.test_path.is_file()
    assert "pytest.fail" in suite.test_code


def test_run_pytest_on_file_counts_passed_and_failed(tmp_path: Path) -> None:
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(
        "def test_ok():\n"
        "    assert True\n"
        "\n"
        "def test_fail():\n"
        "    assert False\n",
        encoding="utf-8",
    )

    result = run_pytest_on_file(test_file, root=tmp_path)

    assert result.passed == 1
    assert result.failed == 1
    assert result.total == 2
    assert 0 < result.pass_rate < 1
    assert not result.all_passed


def test_evaluate_brt_against_candidate_runs_in_isolated_root(tmp_path: Path) -> None:
    test_file = tmp_path / "brt.py"
    test_file.write_text("def test_pass():\n    assert 2 == 2\n", encoding="utf-8")
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()

    result = evaluate_brt_against_candidate(test_file, candidate_root=candidate_root)

    assert result.passed == 1
    assert result.all_passed
    assert (candidate_root / "progress" / "repro_tests" / "brt.py").is_file()
