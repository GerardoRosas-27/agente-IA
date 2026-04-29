"""
Runtime determinista para construir herramientas.

Separa el flujo de "crear herramienta" del ciclo conversacional general:
extrae una especificacion, instala codigo, ejecuta pruebas, reintenta con
errores concretos y registra la herramienta solo cuando queda validada.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from tool_creator import (
    GeneratedToolSpec,
    InstalledToolResult,
    ToolCreator,
    _same_spec,
    _spec_matches_objective,
    fallback_tool_spec,
    parse_generated_tool_spec,
    parse_markdown_tool_spec,
)

RepairCallback = Callable[[str], str]


def _compact(text: str, limit: int = 5000) -> str:
    return str(text or "").replace("\x00", "")[: max(200, int(limit))]


class ToolBuildRuntime:
    def __init__(
        self,
        *,
        creator: ToolCreator,
        project_root: str | Path | None = None,
        max_repair_attempts: int = 3,
    ) -> None:
        self.creator = creator
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self.logs_dir = self.project_root / "data" / "tool_build_logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _new_log_path(self, user_objective: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in user_objective[:36]).strip("-")
        return self.logs_dir / f"tool-build-{stamp}-{safe or 'tool'}.md"

    def _write(self, path: Path, title: str, body: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"## {time.strftime('%H:%M:%S')} {title}\n\n{_compact(body, 8000)}\n\n")

    def _extract_spec(self, text: str, user_objective: str, log_path: Path) -> GeneratedToolSpec:
        try:
            spec = parse_generated_tool_spec(text)
            self._write(log_path, "Spec JSON", spec.name)
        except Exception as json_exc:
            self._write(log_path, "Spec JSON Fallo", str(json_exc))
            try:
                spec = parse_markdown_tool_spec(text)
                self._write(log_path, "Spec Markdown", spec.name)
            except Exception as md_exc:
                self._write(log_path, "Spec Markdown Fallo", str(md_exc))
                fallback = fallback_tool_spec(user_objective)
                if fallback is None:
                    raise ValueError(
                        "El ciclo no produjo una herramienta instalable. "
                        f"JSON: {json_exc}; Markdown: {md_exc}"
                    ) from md_exc
                self._write(log_path, "Spec Recuperada", fallback.name)
                spec = fallback

        if not _spec_matches_objective(spec, user_objective):
            fallback = fallback_tool_spec(user_objective)
            if fallback is None:
                raise ValueError(f"La herramienta generada ({spec.name}) no corresponde al objetivo.")
            self._write(
                log_path,
                "Spec Reemplazada",
                f"{spec.name} no corresponde al objetivo; se usa {fallback.name}.",
            )
            spec = fallback
        return spec

    @staticmethod
    def _repair_prompt(
        *,
        user_objective: str,
        spec: GeneratedToolSpec,
        failure: str,
        attempt: int,
    ) -> str:
        payload = {
            "objetivo": user_objective,
            "intento": attempt,
            "fallo": _compact(failure, 3000),
            "spec_actual": {
                "name": spec.name,
                "description": spec.description,
                "triggers": spec.triggers,
                "code": spec.code,
                "test_code": spec.test_code,
                "instructions": spec.instructions,
                "risk": spec.risk,
                "callable": spec.callable_name,
                "input_schema": spec.input_schema or {},
                "output_schema": spec.output_schema or {},
                "permissions": spec.permissions or [],
                "requirements": spec.requirements or [],
            },
        }
        return (
            "Corrige esta herramienta. Devuelve SOLO JSON valido con: "
            "name, description, triggers, code, test_code, instructions, risk, callable, "
            "input_schema, output_schema, permissions, requirements. "
            "El codigo debe importarse sin efectos secundarios. Las pruebas deben usar unittest. "
            "La herramienta debe exponer el callable indicado, preferentemente run(...). "
            "Si usa red/API, usa tokens por parametro y mocks en test_code; no pongas secretos reales. "
            "No uses subprocess, eval, exec ni input interactivo.\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    def build(
        self,
        *,
        user_objective: str,
        initial_text: str,
        cycles: int = 0,
        reached: bool = False,
        run_tests: bool = True,
        timeout: float = 120.0,
        repair_callback: RepairCallback | None = None,
    ) -> InstalledToolResult:
        log_path = self._new_log_path(user_objective)
        log_path.write_text(
            "\n".join(
                [
                    "# Tool build log",
                    "",
                    f"- Inicio: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                    f"- Objetivo: {user_objective}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self._write(log_path, "Salida Inicial Del Ciclo", initial_text)
        spec = self._extract_spec(initial_text, user_objective, log_path)

        last_failure = ""
        last_result: InstalledToolResult | None = None
        attempted_specs: list[GeneratedToolSpec] = []
        max_attempts = 1 + self.max_repair_attempts

        for attempt in range(1, max_attempts + 1):
            attempted_specs.append(spec)
            self.creator.on_log("ToolBuildRuntime", f"Intento {attempt}/{max_attempts}: instalando {spec.name}.")
            self._write(log_path, f"Intento {attempt} Spec", json.dumps(spec.__dict__, ensure_ascii=False, indent=2))
            try:
                result = self.creator.install(spec, run_tests=run_tests, timeout=timeout)
            except Exception as exc:
                last_failure = f"{type(exc).__name__}: {exc}"
                self._write(log_path, f"Intento {attempt} Excepcion", last_failure)
                result = None
            else:
                last_result = result
                self._write(log_path, f"Intento {attempt} Pruebas", result.test_output)
                if result.test_ok:
                    return InstalledToolResult(
                        name=result.name,
                        skill_dir=result.skill_dir,
                        manifest_path=result.manifest_path,
                        test_ok=True,
                        test_output=result.test_output,
                        cycles=cycles,
                        reached=reached,
                        attempts=attempt,
                        build_log_path=str(log_path),
                    )
                last_failure = result.test_output

            fallback = fallback_tool_spec(user_objective)
            if fallback is not None and all(not _same_spec(prev, fallback) for prev in attempted_specs):
                self.creator.on_log("ToolBuildRuntime", "Reintentando con implementacion recuperada.")
                self._write(log_path, "Reintento Recuperado", fallback.name)
                spec = fallback
                continue

            if repair_callback is None or attempt >= max_attempts:
                break
            prompt = self._repair_prompt(
                user_objective=user_objective,
                spec=spec,
                failure=last_failure,
                attempt=attempt + 1,
            )
            self.creator.on_log("ToolBuildRuntime", "Pidiendo reparacion al LLM con el error de pruebas.")
            repaired_text = repair_callback(prompt)
            self._write(log_path, f"Reparacion LLM {attempt + 1}", repaired_text)
            spec = self._extract_spec(repaired_text, user_objective, log_path)

        if last_result is not None:
            return InstalledToolResult(
                name=last_result.name,
                skill_dir=last_result.skill_dir,
                manifest_path=last_result.manifest_path,
                test_ok=False,
                test_output=last_result.test_output,
                cycles=cycles,
                reached=reached,
                attempts=max_attempts,
                build_log_path=str(log_path),
            )
        raise RuntimeError(f"No se pudo instalar la herramienta. Ultimo error: {last_failure}")
