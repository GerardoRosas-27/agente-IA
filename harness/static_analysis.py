"""Análisis estático rápido sobre archivos modificados.

Inspirado en la práctica de SWE-agent v1+ y muchos sistemas SOTA: correr un
linter rápido (ruff) sobre los archivos cambiados antes de ejecutar la suite
completa de tests. Atrapa imports rotos, sintaxis y nombres no definidos en
~10× menos tiempo que pytest.

El módulo NO obliga a tener ruff instalado: si no está disponible, devuelve un
resultado neutral (no bloquea el ciclo) marcando `available=False`. Esto
permite a `verifier.verify_cycle` añadirlo como check con peso medio sin
romper entornos sin ruff.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Una línea de issue de ruff con formato `concise` se ve como:
#   path/to/file.py:LINE:COL: CODE message
# El "All checks passed!" o "Found N error(s)." NO encajan con este patrón.
# Ruff colorea con ANSI escapes incluso cuando capturamos pipe; los limpiamos
# antes de matchear.
_RUFF_ISSUE_LINE = re.compile(r"^\S+\.py:\d+:\d+:\s+\w+", re.MULTILINE)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RUFF_INVOCATION_CACHE: list[str] | None | bool = False


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


@dataclass(frozen=True)
class LintResult:
    available: bool
    ok: bool
    issues: int
    output: str
    files_checked: tuple[str, ...] = ()

    @property
    def report_line(self) -> str:
        if not self.available:
            return "ruff no disponible (skip)"
        if not self.files_checked:
            return "ruff: nada que revisar"
        if self.ok:
            return f"ruff OK ({len(self.files_checked)} archivos)"
        return f"ruff: {self.issues} issue(s) en {len(self.files_checked)} archivos"


def _ruff_invocation() -> list[str] | None:
    """Devuelve un argv que invoca ruff, o None si no se encuentra."""
    global _RUFF_INVOCATION_CACHE
    if _RUFF_INVOCATION_CACHE is not False:
        return _RUFF_INVOCATION_CACHE
    direct = shutil.which("ruff")
    if direct:
        _RUFF_INVOCATION_CACHE = [direct]
        return _RUFF_INVOCATION_CACHE
    # Fallback: muchos entornos tienen ruff instalado en el venv pero no en PATH.
    # `python -m ruff` funciona si el módulo está en el sys.path del intérprete.
    try:
        check = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check.returncode == 0:
            _RUFF_INVOCATION_CACHE = [sys.executable, "-m", "ruff"]
            return _RUFF_INVOCATION_CACHE
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _RUFF_INVOCATION_CACHE = None
        return None
    _RUFF_INVOCATION_CACHE = None
    return None


def lint_changed_files(
    files: list[str],
    *,
    root: Path,
    extra_select: tuple[str, ...] = ("E", "F"),
    timeout: float = 30,
) -> LintResult:
    """Corre `ruff check` sobre los archivos Python modificados.

    Solo activa los grupos `E` (pycodestyle errors) y `F` (pyflakes) por
    defecto: queremos detectar imports rotos / nombres no definidos / sintaxis
    sin meternos en estilo. El llamador puede pasar más con `extra_select`.
    """
    python_files = [f for f in files if f.endswith(".py")]
    invocation = _ruff_invocation()
    if invocation is None:
        return LintResult(available=False, ok=True, issues=0, output="ruff no instalado")
    if not python_files:
        return LintResult(available=True, ok=True, issues=0, output="sin archivos Python")

    existing = []
    for rel in python_files:
        full = root / rel
        if full.is_file():
            existing.append(rel)
    if not existing:
        return LintResult(
            available=True,
            ok=True,
            issues=0,
            output="archivos modificados ya no existen (rollback?)",
        )

    select = ",".join(extra_select)
    cmd = [
        *invocation,
        "check",
        "--select",
        select,
        "--no-cache",
        "--output-format",
        "concise",
        "--exit-zero",
        *existing,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        return LintResult(
            available=True,
            ok=False,
            issues=1,
            output=f"ruff timeout: {exc}",
            files_checked=tuple(existing),
        )

    output = _strip_ansi((proc.stdout or "").strip())
    issues = len(_RUFF_ISSUE_LINE.findall(output))
    return LintResult(
        available=True,
        ok=issues == 0,
        issues=issues,
        output=output or "sin issues",
        files_checked=tuple(existing),
    )
