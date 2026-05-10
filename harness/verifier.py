"""Verificador determinista basado en evidencia ejecutable.

Inspirado en:
- MAR / Multi-Agent Reflexion (arXiv:2512.20845): separar Acting/Diagnosing/
  Critiquing/Aggregating en lugar de un único Reviewer LLM.
- Generator/Critic/Verifier con memoria episódica (OpenReview T97MrGUNkZ):
  el Verifier no opina, solo certifica con evidencia.
- CodePRM (ACL 2025): Process Reward Model con feedback de ejecución por paso.

Este módulo NO llama al LLM. Toma artefactos producidos por el ciclo
(implementación, salida de pytest, BRTs, archivos rechazados) y emite un
veredicto objetivo. El Reviewer LLM sigue ejerciendo de Judge sobre estilo y
checklists; pero un PASS del Reviewer queda anulado si el Verifier ve
evidencia contraria.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from harness.static_analysis import LintResult, lint_changed_files
from harness.test_generator import TestRunResult


@dataclass(frozen=True)
class VerifierCheck:
    """Resultado de un criterio atómico (estilo MCTS-Judge)."""

    name: str
    passed: bool
    weight: float
    detail: str
    is_hard: bool = False


@dataclass(frozen=True)
class VerifierVerdict:
    passed: bool
    score: float
    checks: tuple[VerifierCheck, ...] = field(default_factory=tuple)
    summary: str = ""

    @property
    def report_markdown(self) -> str:
        lines = [
            f"VERDICT: {'PASS' if self.passed else 'FAIL'}",
            f"Score determinista: {self.score:.3f}",
            "",
            "## Checks",
        ]
        for check in self.checks:
            mark = "✔" if check.passed else "✘"
            lines.append(f"- {mark} **{check.name}** (peso {check.weight:.2f}): {check.detail}")
        if self.summary:
            lines.extend(["", "## Resumen", self.summary])
        return "\n".join(lines)


def _aggregate(checks: list[VerifierCheck], summary: str) -> VerifierVerdict:
    if not checks:
        return VerifierVerdict(False, 0.0, (), "No hubo evidencia para verificar.")
    total_weight = sum(c.weight for c in checks)
    if total_weight <= 0:
        return VerifierVerdict(False, 0.0, tuple(checks), "Pesos inválidos.")
    earned = sum(c.weight for c in checks if c.passed)
    score = earned / total_weight
    # Hard fails: por nombre fijo o porque el check se marcó dinámicamente como
    # hard (ej: un BRT que sí se ejecutó y falló).
    has_hard_fail = any(
        not c.passed and (c.is_hard or c.name in _HARD_FAIL_CHECKS) for c in checks
    )
    passed = (not has_hard_fail) and score >= _PASS_THRESHOLD
    return VerifierVerdict(passed=passed, score=round(score, 4), checks=tuple(checks), summary=summary)


_HARD_FAIL_CHECKS = {
    "tests_existentes_pasan",
    "patch_sin_rejects",
    "imports_no_rotos",
    "sin_secretos_modificados",
    "ruff_lint",
}

_PASS_THRESHOLD = 0.65


_PYTEST_EXIT_RE = re.compile(r"Exit code:\s*(\d+)", re.IGNORECASE)
_PYTEST_FAILED_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_PYTEST_PASSED_RE = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_PY_COMPILE_FAIL_RE = re.compile(r"py_compile\s+exit=([1-9]\d*)|SyntaxError|IndentationError", re.IGNORECASE)
_IMPORT_FAIL_RE = re.compile(r"import\s+\S+\s+exit=([1-9]\d*)|ImportError|ModuleNotFoundError", re.IGNORECASE)


def _check_tests_pass(test_output: str) -> VerifierCheck:
    failed_matches = _PYTEST_FAILED_RE.findall(test_output or "")
    failed = sum(int(x) for x in failed_matches) if failed_matches else 0
    exit_codes = _PYTEST_EXIT_RE.findall(test_output or "")
    bad_exit = any(code != "0" for code in exit_codes)
    passed = failed == 0 and not bad_exit
    detail = (
        f"failed={failed}, exit_codes={exit_codes or '(ninguno)'}"
        if (failed or bad_exit)
        else "todos los pytest del ciclo pasaron"
    )
    return VerifierCheck("tests_existentes_pasan", passed, weight=2.0, detail=detail)


def _check_patch_no_rejects(rejected: Iterable[str]) -> VerifierCheck:
    rejected_list = list(rejected)
    passed = not rejected_list
    detail = "sin bloques rechazados" if passed else f"rechazados: {'; '.join(rejected_list)}"
    return VerifierCheck("patch_sin_rejects", passed, weight=1.0, detail=detail)


def _check_imports(validation_output: str) -> VerifierCheck:
    if not validation_output:
        return VerifierCheck("imports_no_rotos", True, 1.0, "sin archivos nuevos a importar")
    has_failure = bool(_IMPORT_FAIL_RE.search(validation_output))
    passed = not has_failure
    detail = "imports OK" if passed else "se detectó ImportError/ModuleNotFoundError en los archivos modificados"
    return VerifierCheck("imports_no_rotos", passed, weight=1.0, detail=detail)


def _check_compile(validation_output: str) -> VerifierCheck:
    if not validation_output:
        return VerifierCheck("py_compile_ok", True, 1.0, "no hubo archivos Python a compilar")
    has_failure = bool(_PY_COMPILE_FAIL_RE.search(validation_output))
    passed = not has_failure
    detail = "py_compile OK" if passed else "errores de sintaxis o exit≠0 en py_compile"
    return VerifierCheck("py_compile_ok", passed, weight=1.5, detail=detail)


def _check_secrets(changed_files: Iterable[str]) -> VerifierCheck:
    secrets = [path for path in changed_files if path.startswith(".env") or path.endswith("/.env")]
    passed = not secrets
    detail = "no se tocaron archivos .env" if passed else f"detectados: {', '.join(secrets)}"
    return VerifierCheck("sin_secretos_modificados", passed, weight=2.0, detail=detail)


def _check_diff_size(changed_files: Iterable[str]) -> VerifierCheck:
    files = list(changed_files)
    n = len(files)
    if n <= 5:
        return VerifierCheck("diff_minimo", True, 0.5, f"{n} archivos cambiados")
    if n <= 12:
        return VerifierCheck("diff_minimo", True, 0.3, f"{n} archivos cambiados (aceptable)")
    return VerifierCheck("diff_minimo", False, 0.5, f"{n} archivos cambiados parece excesivo")


def _check_lint(lint: LintResult | None) -> VerifierCheck:
    """Check basado en ruff. No bloquea si ruff no está disponible."""
    if lint is None or not lint.available:
        return VerifierCheck(
            "ruff_lint",
            True,
            weight=0.0,
            detail="ruff no disponible (skip)",
        )
    if not lint.files_checked:
        return VerifierCheck("ruff_lint", True, weight=0.0, detail="sin archivos a lintear")
    return VerifierCheck(
        "ruff_lint",
        passed=lint.ok,
        weight=1.0,
        detail=lint.report_line,
    )


def _check_brts(brts: Iterable[TestRunResult]) -> VerifierCheck:
    runs = list(brts)
    if not runs:
        return VerifierCheck("bug_reproduction_tests", True, 0.0, "no se generaron BRTs para esta feature")
    total = sum(r.total for r in runs)
    passed = sum(r.passed for r in runs)
    if total <= 0:
        return VerifierCheck(
            "bug_reproduction_tests",
            False,
            weight=1.0,
            detail="BRTs presentes pero sin asserts ejecutados",
            is_hard=True,
        )
    rate = passed / total
    passed_check = rate >= 0.99
    detail = f"EPR={rate:.2f} ({passed}/{total} tests reproducidos)"
    return VerifierCheck(
        "bug_reproduction_tests",
        passed_check,
        weight=2.0,
        detail=detail,
        is_hard=True,
    )


def verify_cycle(
    *,
    test_output: str,
    validation_output: str = "",
    changed_files: Iterable[str] = (),
    rejected: Iterable[str] = (),
    bash_ok: bool = True,
    bug_reproduction_runs: Iterable[TestRunResult] = (),
    root: Path | None = None,
    run_lint: bool = True,
) -> VerifierVerdict:
    """Combina toda la evidencia ejecutable del ciclo en un veredicto objetivo.

    Si `run_lint` y se pasa `root`, ejecuta `ruff` sobre los archivos cambiados
    como check adicional con peso medio.
    """
    changed_list = list(changed_files)
    lint: LintResult | None = None
    if run_lint and root is not None:
        try:
            lint = lint_changed_files(changed_list, root=root)
        except Exception:
            lint = None
    checks: list[VerifierCheck] = [
        _check_tests_pass(test_output),
        _check_patch_no_rejects(rejected),
        _check_compile(validation_output),
        _check_imports(validation_output),
        _check_secrets(changed_list),
        _check_diff_size(changed_list),
        _check_lint(lint),
        _check_brts(bug_reproduction_runs),
        VerifierCheck(
            "bash_ok",
            bash_ok,
            weight=0.5,
            detail="comandos bash del implementador exit=0" if bash_ok else "fallaron comandos bash",
        ),
    ]
    summary_lines = [
        f"{len(changed_list)} archivos cambiados, {sum(1 for c in checks if c.passed)}/{len(checks)} checks OK."
    ]
    return _aggregate(checks, summary="\n".join(summary_lines))
