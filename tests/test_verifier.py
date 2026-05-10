from __future__ import annotations

from harness.test_generator import TestRunResult
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
