"""Evaluación objetiva de cambios producidos por el harness."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from harness.orchestrator import _check_command_policy, _related_test_paths, _repo_root


@dataclass(frozen=True)
class EvaluationResult:
    ok: bool
    checks: tuple[str, ...]
    report: str


def _run(command: list[str], *, root: Path, timeout: float = 60) -> tuple[bool, str]:
    completed = subprocess.run(command, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    output = f"$ {' '.join(command)}\nExit code: {completed.returncode}\n{completed.stdout}\n{completed.stderr}".strip()
    return completed.returncode == 0, output


def evaluate_changes(
    changed_files: list[str],
    *,
    commands: list[str] | None = None,
    root: Path | None = None,
) -> EvaluationResult:
    root = root or _repo_root()
    checks: list[str] = []
    reports: list[str] = []
    ok = True

    python_files = [item for item in changed_files if item.endswith(".py")]
    if python_files:
        compile_cmd = [sys.executable, "-m", "py_compile", *python_files]
        check_ok, output = _run(compile_cmd, root=root, timeout=30)
        ok = ok and check_ok
        checks.append("py_compile")
        reports.append(output)

    related_tests = _related_test_paths(changed_files)
    if related_tests:
        test_cmd = [sys.executable, "-m", "pytest", *related_tests, "-q", "--tb=short"]
        check_ok, output = _run(test_cmd, root=root, timeout=90)
        ok = ok and check_ok
        checks.append("related_tests")
        reports.append(output)

    for command in commands or []:
        policy = _check_command_policy(command)
        checks.append(f"command:{command}")
        if not policy.allowed:
            ok = False
            reports.append(f"$ {command}\nBLOQUEADO: {policy.reason}")
            continue
        completed = subprocess.run(command, cwd=str(root), shell=True, capture_output=True, text=True, timeout=90)
        command_ok = completed.returncode == 0
        ok = ok and command_ok
        reports.append(f"$ {command}\nExit code: {completed.returncode}\n{completed.stdout}\n{completed.stderr}".strip())

    if not checks:
        checks.append("no_changed_files")
        reports.append("No hubo archivos o comandos para evaluar.")

    return EvaluationResult(ok=ok, checks=tuple(checks), report="\n\n".join(reports))
