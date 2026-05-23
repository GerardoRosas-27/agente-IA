from __future__ import annotations

from harness.test_generator import TestRunResult
from harness.quality_gates import QualityGate
from harness.verifier import VerifierVerdict, verify_cycle


def test_verify_cycle_pass_when_all_evidence_clean() -> None:
    verdict = verify_cycle(
        test_output="Exit code: 0\n12 passed in 1.0s",
        validation_output="py_compile exit=0\nimport demo exit=0\nimport ok",
        changed_files=["skills/demo.py"],
        rejected=[],
        bash_ok=True,
        bug_reproduction_runs=[
            TestRunResult(passed=2, failed=0, errors=0, total=2, output="2 passed", return_code=0)
        ],
    )

    assert isinstance(verdict, VerifierVerdict)
    assert verdict.passed is True
    assert verdict.score >= 0.9
    assert "VERDICT: PASS" in verdict.report_markdown


def test_verify_cycle_fails_on_pytest_failures() -> None:
    verdict = verify_cycle(
        test_output="Exit code: 1\n3 passed, 2 failed in 1.0s",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=["skills/demo.py"],
        bug_reproduction_runs=[],
    )

    assert verdict.passed is False
    assert any(c.name == "tests_existentes_pasan" and not c.passed for c in verdict.checks)


def test_verify_cycle_blocks_secret_changes() -> None:
    verdict = verify_cycle(
        test_output="Exit code: 0\n10 passed",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=[".env", "skills/demo.py"],
    )

    assert verdict.passed is False
    secrets_check = next(c for c in verdict.checks if c.name == "sin_secretos_modificados")
    assert not secrets_check.passed


def test_verify_cycle_fails_when_brts_dont_pass() -> None:
    verdict = verify_cycle(
        test_output="Exit code: 0\n10 passed",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=["skills/demo.py"],
        bug_reproduction_runs=[
            TestRunResult(passed=1, failed=2, errors=0, total=3, output="...", return_code=1)
        ],
    )

    assert verdict.passed is False
    brt = next(c for c in verdict.checks if c.name == "bug_reproduction_tests")
    assert "EPR=" in brt.detail


def test_verify_cycle_no_brts_does_not_block_pass() -> None:
    """Si no se generaron BRTs (peso 0), no debe bloquear PASS por sí solo."""
    verdict = verify_cycle(
        test_output="Exit code: 0\n10 passed",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=["skills/demo.py"],
        bug_reproduction_runs=(),
    )

    assert verdict.passed is True


def test_verify_cycle_runs_ruff_when_root_is_provided(tmp_path) -> None:
    """El verifier debe bloquear PASS si ruff detecta un error F/E."""
    bad = tmp_path / "bad.py"
    bad.write_text("def f():\n    return missing_name\n", encoding="utf-8")

    verdict = verify_cycle(
        test_output="Exit code: 0\n1 passed",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=["bad.py"],
        root=tmp_path,
        run_lint=True,
    )

    lint_check = next(c for c in verdict.checks if c.name == "ruff_lint")
    if lint_check.weight == 0.0:
        # Entorno sin ruff: el check se salta por compatibilidad.
        assert verdict.passed is True
    else:
        assert not lint_check.passed
        assert verdict.passed is False


def test_verify_cycle_blocks_hard_quality_gate() -> None:
    verdict = verify_cycle(
        test_output="Exit code: 0\n10 passed",
        validation_output="py_compile exit=0\nimport ok",
        changed_files=["skills/demo.py"],
        quality_gates=[
            QualityGate("anti_rationalization", False, "detectado: no hace falta test", hard=True)
        ],
    )

    assert verdict.passed is False
    gate = next(c for c in verdict.checks if c.name == "quality_gate:anti_rationalization")
    assert gate.is_hard is True
