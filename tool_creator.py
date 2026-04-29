"""
Creacion e instalacion de herramientas generadas por el ciclo principal.

El modulo espera que el ciclo agentico produzca una especificacion JSON con
codigo Python y pruebas. Luego instala ese codigo como skill local en
`skills/<nombre>/`, ejecuta sus pruebas y registra metadatos reutilizables.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from skill_manager import SkillManager
from tool_library import ToolLibrary, ToolMemory


_DANGEROUS_CODE_PATTERNS = (
    r"\bimport\s+(os|subprocess|socket|shutil|requests|urllib|http|ftplib|ssl)\b",
    r"\bfrom\s+(os|subprocess|socket|shutil|requests|urllib|http|ftplib|ssl)\b",
    r"\b(eval|exec|compile|input|__import__)\s*\(",
    r"\b(open)\s*\(",
)


@dataclass(frozen=True)
class GeneratedToolSpec:
    name: str
    description: str
    triggers: list[str]
    code: str
    test_code: str
    instructions: str
    risk: str = "moderate"


@dataclass(frozen=True)
class InstalledToolResult:
    name: str
    skill_dir: Path
    manifest_path: Path
    test_ok: bool
    test_output: str
    cycles: int = 0
    reached: bool = False


def tool_creation_objective(user_objective: str) -> str:
    objective = str(user_objective or "").strip()
    return (
        "Crea una nueva herramienta reutilizable para este sistema. "
        "Debes disenar codigo Python pequeno, autocontenido y probado. "
        "No uses red, archivos externos, secretos, subprocess, sockets, eval, exec ni input. "
        "La respuesta final debe ser SOLO JSON valido, sin markdown, con estas claves: "
        "name, description, triggers, code, test_code, instructions, risk. "
        "El campo code debe definir funciones reutilizables sin ejecutar trabajo al importarse. "
        "El campo test_code debe usar unittest y validar al menos un caso normal y un caso borde. "
        "Objetivo de la herramienta: "
        f"{objective}"
    )[:8000]


def _strip_json_fence(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


def _extract_json_object(text: str) -> dict:
    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("La respuesta del ciclo no contiene JSON de herramienta.")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("La especificacion de herramienta debe ser un objeto JSON.")
    return data


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def parse_generated_tool_spec(text: str) -> GeneratedToolSpec:
    data = _extract_json_object(text)
    name = str(data.get("name", data.get("nombre", "")) or "").strip()
    code = str(data.get("code", data.get("codigo", "")) or "").strip()
    if not name:
        raise ValueError("La herramienta generada no tiene name.")
    if not code:
        raise ValueError("La herramienta generada no tiene code.")
    test_code = str(data.get("test_code", data.get("tests", data.get("pruebas", ""))) or "").strip()
    return GeneratedToolSpec(
        name=name,
        description=str(data.get("description", data.get("descripcion", "")) or "").strip(),
        triggers=_as_list(data.get("triggers", data.get("activadores", []))),
        code=code,
        test_code=test_code,
        instructions=str(data.get("instructions", data.get("instrucciones", "")) or "").strip(),
        risk=str(data.get("risk", "moderate") or "moderate").strip().lower(),
    )


def _validate_python_code(code: str, *, label: str) -> None:
    try:
        compile(code, label, "exec")
    except SyntaxError as exc:
        raise ValueError(f"{label} tiene sintaxis invalida: {exc}") from exc
    for pattern in _DANGEROUS_CODE_PATTERNS:
        if re.search(pattern, code):
            raise ValueError(f"{label} contiene una operacion bloqueada por seguridad.")


def _default_test_code() -> str:
    return """import importlib.util
import pathlib
import unittest


class TestGeneratedTool(unittest.TestCase):
    def test_module_imports(self):
        path = pathlib.Path(__file__).with_name("tool.py")
        spec = importlib.util.spec_from_file_location("generated_tool", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        public = [name for name in dir(module) if not name.startswith("_")]
        self.assertTrue(public)


if __name__ == "__main__":
    unittest.main()
"""


class ToolCreator:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        skill_manager: SkillManager | None = None,
        tool_library: ToolLibrary | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.skill_manager = skill_manager or SkillManager(self.project_root / "skills")
        self.tool_library = tool_library

    def install(self, spec: GeneratedToolSpec, *, run_tests: bool = True, timeout: float = 30.0) -> InstalledToolResult:
        _validate_python_code(spec.code, label="tool.py")
        test_code = spec.test_code or _default_test_code()
        _validate_python_code(test_code, label="test_tool.py")

        slug = self.skill_manager.slugify(spec.name)
        skill_dir = self.skill_manager.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        tool_path = skill_dir / "tool.py"
        test_path = skill_dir / "test_tool.py"
        manifest_path = skill_dir / "manifest.json"

        tool_path.write_text(spec.code.rstrip() + "\n", encoding="utf-8")
        test_path.write_text(test_code.rstrip() + "\n", encoding="utf-8")
        (skill_dir / "__init__.py").write_text("", encoding="utf-8")

        manifest = {
            "name": slug,
            "version": "0.1.0",
            "description": spec.description or f"Herramienta generada: {spec.name}",
            "risk": spec.risk if spec.risk in {"low", "moderate", "high", "dangerous"} else "moderate",
            "status": "validated" if run_tests else "experimental",
            "agent_enabled": True,
            "agent_role": f"AgenteSkill:{slug}",
            "triggers": spec.triggers[:24],
            "permissions": [],
            "entrypoint": str(tool_path.relative_to(self.project_root)).replace("\\", "/"),
            "executor": "",
            "command": "",
            "cwd": ".",
            "instructions": spec.instructions or "Importa tool.py desde este skill y reutiliza sus funciones publicas.",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (skill_dir / "README.md").write_text(
            "\n".join(
                [
                    f"# {slug}",
                    "",
                    manifest["description"],
                    "",
                    "## Uso",
                    manifest["instructions"],
                    "",
                    "## Pruebas",
                    f"`python -m unittest discover -s {skill_dir.relative_to(self.project_root)} -p test*.py`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.skill_manager.invalidate_cache()

        test_ok = True
        test_output = "Pruebas no ejecutadas."
        if run_tests:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(skill_dir),
                    "-p",
                    "test*.py",
                ],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=max(1.0, float(timeout)),
                check=False,
            )
            test_ok = proc.returncode == 0
            test_output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
            if not test_output:
                test_output = f"exit_code={proc.returncode}"
            if not test_ok:
                manifest["status"] = "experimental"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if self.tool_library is not None and test_ok:
            self.tool_library.upsert(
                ToolMemory(
                    name=slug,
                    kind="python_module",
                    objective=spec.description or spec.name,
                    trigger_terms=", ".join(spec.triggers),
                    entrypoint=manifest["entrypoint"],
                    instructions=manifest["instructions"],
                    evidence=f"Pruebas ejecutadas al instalar:\n{test_output[:600]}",
                    confidence=0.72,
                )
            )

        return InstalledToolResult(
            name=slug,
            skill_dir=skill_dir,
            manifest_path=manifest_path,
            test_ok=test_ok,
            test_output=test_output[:4000],
        )

    def create_with_main_cycle(
        self,
        user_objective: str,
        *,
        pipeline_runner: Callable[[str], tuple[str, int, float, bool]],
        run_tests: bool = True,
        timeout: float = 30.0,
    ) -> InstalledToolResult:
        final_text, cycles, _loss, reached = pipeline_runner(tool_creation_objective(user_objective))
        spec = parse_generated_tool_spec(final_text)
        installed = self.install(spec, run_tests=run_tests, timeout=timeout)
        return InstalledToolResult(
            name=installed.name,
            skill_dir=installed.skill_dir,
            manifest_path=installed.manifest_path,
            test_ok=installed.test_ok,
            test_output=installed.test_output,
            cycles=cycles,
            reached=reached,
        )
