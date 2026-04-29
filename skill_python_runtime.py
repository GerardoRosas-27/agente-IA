"""
Runtime aislado para ejecutar skills Python generados.

Los modulos creados por el agente se importan en un proceso hijo con entorno
reducido, timeout y comunicacion JSON. Esto evita efectos secundarios en el
proceso principal y deja una frontera clara para auditoria.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PythonSkillResult:
    ok: bool
    skill: str
    callable_name: str
    output: Any = None
    error: str = ""
    duration_s: float = 0.0

    def render(self) -> str:
        status = "ok" if self.ok else "error"
        rendered_output = (
            self.output
            if isinstance(self.output, str)
            else json.dumps(self.output, ensure_ascii=False, default=str)
        )
        return "\n".join(
            [
                f"status={status}",
                f"skill={self.skill}",
                f"callable={self.callable_name}",
                f"duration_s={self.duration_s:.2f}",
                f"output:\n{rendered_output if rendered_output else '(vacio)'}",
                f"error:\n{self.error or '(vacio)'}",
            ]
        )


def _safe_env() -> dict[str, str]:
    keep = {
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _safe_entrypoint(project_root: Path, entrypoint: str) -> Path:
    root = project_root.resolve()
    candidate = (root / entrypoint).resolve() if not Path(entrypoint).is_absolute() else Path(entrypoint).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("entrypoint fuera de la raiz del proyecto") from exc
    if not candidate.exists() or candidate.name != "tool.py":
        raise ValueError("entrypoint de skill Python invalido")
    return candidate


def run_python_skill(
    *,
    skill_name: str,
    entrypoint: str,
    callable_name: str = "run",
    kwargs: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
    timeout: float = 20.0,
    max_output_chars: int = 6000,
) -> PythonSkillResult:
    root = Path(project_root or Path(__file__).resolve().parent)
    tool_path = _safe_entrypoint(root, entrypoint)
    public_name = str(callable_name or "run").strip()
    if not public_name.replace("_", "").isalnum() or public_name.startswith("_"):
        raise ValueError("callable invalido para skill Python")

    runner = r"""
import importlib.util
import json
import sys
import traceback

payload = json.loads(sys.stdin.read() or "{}")
path = payload["path"]
callable_name = payload["callable"]
kwargs = payload.get("kwargs") or {}
try:
    spec = importlib.util.spec_from_file_location("agentia_generated_tool", path)
    module = importlib.util.module_from_spec(spec)
    if spec is None or spec.loader is None:
        raise RuntimeError("no se pudo cargar el modulo")
    spec.loader.exec_module(module)
    fn = getattr(module, callable_name)
    if not callable(fn):
        raise TypeError(f"{callable_name} no es callable")
    result = fn(**kwargs)
    print(json.dumps({"ok": True, "output": result}, ensure_ascii=False, default=str))
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(limit=4),
    }, ensure_ascii=False))
    sys.exit(1)
"""
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-I", "-c", runner],
        input=json.dumps(
            {"path": str(tool_path), "callable": public_name, "kwargs": kwargs or {}},
            ensure_ascii=False,
            default=str,
        ),
        cwd=str(root),
        env=_safe_env(),
        capture_output=True,
        text=True,
        timeout=max(1.0, float(timeout)),
        check=False,
    )
    duration = time.monotonic() - started
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    try:
        data = json.loads(stdout.splitlines()[-1]) if stdout else {}
    except (json.JSONDecodeError, IndexError):
        data = {}
    if proc.returncode == 0 and data.get("ok") is True:
        return PythonSkillResult(
            ok=True,
            skill=skill_name,
            callable_name=public_name,
            output=data.get("output"),
            duration_s=duration,
        )
    error = str(data.get("error") or stderr or stdout or f"exit_code={proc.returncode}")
    if data.get("traceback"):
        error = f"{error}\n{data['traceback']}"
    return PythonSkillResult(
        ok=False,
        skill=skill_name,
        callable_name=public_name,
        error=error[: max(500, int(max_output_chars))],
        duration_s=duration,
    )
