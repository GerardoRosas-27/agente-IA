"""
Creacion e instalacion de herramientas generadas por el ciclo principal.

El modulo espera que el ciclo agentico produzca una especificacion JSON con
codigo Python y pruebas. Luego instala ese codigo como skill local en
`skills/<nombre>/`, ejecuta sus pruebas y registra metadatos reutilizables.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from skill_manager import SkillManager
from tool_library import ToolLibrary, ToolMemory


_DANGEROUS_CODE_PATTERNS = (
    r"\bimport\s+(subprocess|ftplib)\b",
    r"\bfrom\s+(subprocess|ftplib)\b",
    r"\b(os\.system|os\.popen|shutil\.rmtree)\s*\(",
    r"\b(eval|exec|compile|input|__import__)\s*\(",
)

LogCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class GeneratedToolSpec:
    name: str
    description: str
    triggers: list[str]
    code: str
    test_code: str
    instructions: str
    risk: str = "moderate"
    callable_name: str = "run"
    input_schema: dict[str, str] | None = None
    output_schema: dict[str, str] | None = None
    permissions: list[str] | None = None
    requirements: list[str] | None = None


@dataclass(frozen=True)
class InstalledToolResult:
    name: str
    skill_dir: Path
    manifest_path: Path
    test_ok: bool
    test_output: str
    cycles: int = 0
    reached: bool = False
    attempts: int = 1
    build_log_path: str = ""


def tool_creation_objective(user_objective: str) -> str:
    objective = str(user_objective or "").strip()
    return (
        "Crea una nueva herramienta reutilizable para este sistema. "
        "Debes disenar codigo Python funcional, instalado como modulo y probado. "
        "NO sustituyas el pedido por una herramienta existente como project-tests. "
        "NO respondas que no hace falta crearla: debes crear un modulo nuevo para el objetivo solicitado. "
        "Ignora sesiones o memorias anteriores que impongan restricciones como 'sin red', "
        "'sin archivos externos' o 'solo enlaces locales' si el usuario ahora pide una integracion real. "
        "Puedes investigar por internet y puedes disenar integraciones reales cuando el objetivo lo requiera. "
        "No incluyas secretos reales; usa parametros para tokens, URLs y credenciales. "
        "Evita acciones destructivas y no uses subprocess, eval, exec ni input interactivo. "
        "La respuesta final debe ser SOLO JSON valido, sin markdown, con estas claves: "
        "name, description, triggers, code, test_code, instructions, risk, callable, "
        "input_schema, output_schema, permissions, requirements. "
        "El campo code debe definir funciones reutilizables sin ejecutar trabajo al importarse "
        "y DEBE exponer una funcion run(**kwargs) o run con argumentos nombrados. "
        "El campo test_code debe usar unittest y validar casos reales sin depender de servicios externos vivos; "
        "usa mocks cuando la herramienta sea de red. "
        "Si el objetivo requiere WhatsApp, crea una herramienta que soporte enlaces wa.me/deep links "
        "o WhatsApp Cloud API con token recibido por parametro y pruebas con mocks. "
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


def _as_schema(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in value.items():
        name = str(key).strip()
        if name:
            out[name] = str(val or "str").strip()[:80] or "str"
    return out


def parse_generated_tool_spec(text: str) -> GeneratedToolSpec:
    data = _extract_json_object(text)
    if not data.get("code") and not data.get("codigo"):
        nested = data.get("respuesta_final") or data.get("response") or data.get("final")
        if isinstance(nested, str) and nested.strip() != text.strip():
            return parse_generated_tool_spec(nested)
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
        callable_name=str(data.get("callable", data.get("callable_name", "run")) or "run").strip(),
        input_schema=_as_schema(data.get("input_schema", data.get("schema", {}))),
        output_schema=_as_schema(data.get("output_schema", {})),
        permissions=_as_list(data.get("permissions", data.get("permisos", []))),
        requirements=_as_list(data.get("requirements", data.get("dependencias", []))),
    )


def _extract_labeled_value(text: str, *labels: str) -> str:
    escaped = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?:\*\*)?(?:{escaped})(?:\*\*)?\s*:\s*(.+?)(?=\n\s*(?:\*\*)?[A-ZÁÉÍÓÚÑ_a-záéíóúñ -]+(?:\*\*)?\s*:|\Z)"
    match = re.search(pattern, text, re.I | re.S)
    return match.group(1).strip() if match else ""


def parse_markdown_tool_spec(text: str) -> GeneratedToolSpec:
    raw = str(text or "")
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", raw, flags=re.I | re.S)
    code = blocks[0].strip() if blocks else ""
    test_code = blocks[1].strip() if len(blocks) > 1 else ""
    name = _extract_labeled_value(raw, "name", "nombre").strip()
    description = _extract_labeled_value(raw, "description", "descripcion", "descripción").strip()
    triggers_raw = _extract_labeled_value(raw, "triggers", "activadores").strip()
    instructions = _extract_labeled_value(raw, "instructions", "instrucciones").strip()
    risk = _extract_labeled_value(raw, "risk", "riesgo").strip().lower() or "moderate"
    if not name or not code:
        raise ValueError("No se pudo extraer una especificacion Markdown instalable.")
    return GeneratedToolSpec(
        name=name,
        description=description,
        triggers=_as_list(triggers_raw),
        code=code,
        test_code=test_code,
        instructions=instructions,
        risk=risk,
        callable_name="run",
        input_schema={},
        output_schema={},
        permissions=[],
        requirements=[],
    )


def _is_whatsapp_request(text: str) -> bool:
    return bool(re.search(r"\b(whatsapp|whats\s*app|wapsat|waptsap|wasap|guasap)\b", text, re.I))


def _whatsapp_link_spec() -> GeneratedToolSpec:
    code = r'''"""
Herramienta local para preparar enlaces de WhatsApp.

No llama a internet ni abre aplicaciones; solo normaliza telefonos y genera
URLs/URI que otra capa puede abrir con aprobacion del usuario.
"""


def normalize_phone(phone, default_country_code=""):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    country = "".join(ch for ch in str(default_country_code or "") if ch.isdigit())
    if not digits:
        raise ValueError("telefono vacio")
    if country and len(digits) <= 10 and not digits.startswith(country):
        digits = country + digits
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("telefono fuera de rango E.164")
    return digits


def _quote_text(text):
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
    for byte in str(text or "").encode("utf-8"):
        ch = chr(byte)
        out.append(ch if ch in safe else "%{:02X}".format(byte))
    return "".join(out)


def build_wa_me_url(phone, message="", default_country_code=""):
    normalized = normalize_phone(phone, default_country_code)
    url = "https://wa.me/" + normalized
    if message:
        url += "?text=" + _quote_text(message)
    return url


def build_whatsapp_deep_link(phone, message="", default_country_code=""):
    normalized = normalize_phone(phone, default_country_code)
    link = "whatsapp://send?phone=" + normalized
    if message:
        link += "&text=" + _quote_text(message)
    return link


def contact_payload(phone, message="", default_country_code=""):
    return {
        "phone": normalize_phone(phone, default_country_code),
        "wa_me_url": build_wa_me_url(phone, message, default_country_code),
        "deep_link": build_whatsapp_deep_link(phone, message, default_country_code),
    }


def run(phone, message="", default_country_code=""):
    return contact_payload(phone, message, default_country_code)
'''
    test_code = r'''import unittest
from tool import build_wa_me_url, build_whatsapp_deep_link, contact_payload, normalize_phone, run


class TestWhatsAppLinkTool(unittest.TestCase):
    def test_build_wa_me_url_with_message(self):
        self.assertEqual(
            build_wa_me_url("+52 55 1234 5678", "hola mundo"),
            "https://wa.me/525512345678?text=hola%20mundo",
        )

    def test_default_country_code(self):
        self.assertEqual(normalize_phone("5512345678", "52"), "525512345678")

    def test_deep_link(self):
        self.assertEqual(
            build_whatsapp_deep_link("525512345678", "ok"),
            "whatsapp://send?phone=525512345678&text=ok",
        )

    def test_payload_and_invalid_phone(self):
        payload = contact_payload("525512345678")
        self.assertIn("wa_me_url", payload)
        with self.assertRaises(ValueError):
            normalize_phone("")

    def test_run_contract(self):
        payload = run("5512345678", "hola", "52")
        self.assertEqual(payload["phone"], "525512345678")


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedToolSpec(
        name="whatsapp-link-tool",
        description="Genera enlaces wa.me y whatsapp://send para iniciar conversaciones de WhatsApp sin usar red desde el modulo.",
        triggers=["whatsapp", "waptsap", "wasap", "wa.me", "mensaje"],
        code=code,
        test_code=test_code,
        instructions=(
            "Importa build_wa_me_url, build_whatsapp_deep_link o contact_payload desde tool.py. "
            "La herramienta no abre WhatsApp por si sola; devuelve enlaces listos para que la UI o el usuario los abra."
        ),
        risk="low",
        callable_name="run",
        input_schema={"phone": "str", "message": "str", "default_country_code": "str"},
        output_schema={"phone": "str", "wa_me_url": "str", "deep_link": "str"},
        permissions=[],
        requirements=[],
    )


def fallback_tool_spec(user_objective: str) -> GeneratedToolSpec | None:
    if _is_whatsapp_request(user_objective):
        return _whatsapp_link_spec()
    return None


def _same_spec(a: GeneratedToolSpec, b: GeneratedToolSpec) -> bool:
    return a.name == b.name and a.code == b.code and a.test_code == b.test_code


def _spec_matches_objective(spec: GeneratedToolSpec, user_objective: str) -> bool:
    if not _is_whatsapp_request(user_objective):
        return True
    haystack = "\n".join(
        [spec.name, spec.description, " ".join(spec.triggers), spec.code, spec.instructions]
    )
    return bool(re.search(r"\b(whatsapp|wa\.me|whatsapp://|wasap|waptsap)\b", haystack, re.I))


def _validate_python_code(code: str, *, label: str) -> None:
    try:
        compile(code, label, "exec")
    except SyntaxError as exc:
        raise ValueError(f"{label} tiene sintaxis invalida: {exc}") from exc
    for pattern in _DANGEROUS_CODE_PATTERNS:
        if re.search(pattern, code):
            raise ValueError(f"{label} contiene una operacion bloqueada por seguridad.")


def _validate_requirements(requirements: list[str] | None) -> list[str]:
    clean: list[str] = []
    for item in requirements or []:
        req = str(item or "").strip()
        if not req:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:[<>=!~]=?[A-Za-z0-9_.*+-]+)?", req):
            raise ValueError(f"requirement inseguro o invalido: {req}")
        clean.append(req)
    return clean[:24]


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
        on_log: LogCallback | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.skill_manager = skill_manager or SkillManager(self.project_root / "skills")
        self.tool_library = tool_library
        self.on_log = on_log or (lambda _role, _content: None)

    def install(self, spec: GeneratedToolSpec, *, run_tests: bool = True, timeout: float = 30.0) -> InstalledToolResult:
        self.on_log("CrearHerramienta", f"Validando codigo generado para {spec.name}...")
        _validate_python_code(spec.code, label="tool.py")
        test_code = spec.test_code or _default_test_code()
        _validate_python_code(test_code, label="test_tool.py")
        requirements = _validate_requirements(spec.requirements)
        callable_name = (spec.callable_name or "run").strip()
        if not callable_name.replace("_", "").isalnum() or callable_name.startswith("_"):
            raise ValueError("callable invalido para herramienta generada.")

        slug = self.skill_manager.slugify(spec.name)
        skill_dir = self.skill_manager.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(skill_dir / "__pycache__", ignore_errors=True)
        tool_path = skill_dir / "tool.py"
        test_path = skill_dir / "test_tool.py"
        manifest_path = skill_dir / "manifest.json"

        self.on_log("CrearHerramienta", f"Escribiendo modulo instalado en {skill_dir}...")
        tool_path.write_text(spec.code.rstrip() + "\n", encoding="utf-8")
        test_path.write_text(test_code.rstrip() + "\n", encoding="utf-8")
        (skill_dir / "__init__.py").write_text("", encoding="utf-8")
        (skill_dir / "requirements.txt").write_text(
            "\n".join(requirements) + ("\n" if requirements else ""),
            encoding="utf-8",
        )

        manifest = {
            "name": slug,
            "version": "0.1.0",
            "description": spec.description or f"Herramienta generada: {spec.name}",
            "risk": spec.risk if spec.risk in {"low", "moderate", "high", "dangerous"} else "moderate",
            "status": "validated" if run_tests else "experimental",
            "agent_enabled": True,
            "agent_role": f"AgenteSkill:{slug}",
            "triggers": spec.triggers[:24],
            "permissions": list(spec.permissions or [])[:24],
            "entrypoint": str(tool_path.relative_to(self.project_root)).replace("\\", "/"),
            "executor": "python_module",
            "callable": callable_name,
            "input_schema": spec.input_schema or {},
            "output_schema": spec.output_schema or {},
            "requirements": requirements,
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
                    "## Contrato",
                    f"Executor: `{manifest['executor']}`",
                    f"Callable: `{manifest['callable']}`",
                    f"Input schema: `{json.dumps(manifest['input_schema'], ensure_ascii=False)}`",
                    "",
                    "## Dependencias",
                    "Declaradas en `requirements.txt`. Instálalas manualmente en un entorno controlado si hacen falta.",
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
            self.on_log("CrearHerramienta", f"Ejecutando pruebas de {slug}...")
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
                self.on_log("CrearHerramienta", f"Pruebas fallidas; {slug} queda como experimental.\n{test_output[:1200]}")
            else:
                self.on_log("CrearHerramienta", f"Pruebas OK para {slug}.\n{test_output[:1200]}")

        if self.tool_library is not None and test_ok:
            self.on_log("CrearHerramienta", f"Registrando {slug} en ToolLibrary para uso futuro...")
            self.tool_library.upsert(
                ToolMemory(
                    name=slug,
                    kind="python_module",
                    objective=spec.description or spec.name,
                    trigger_terms=", ".join(spec.triggers),
                    entrypoint=manifest["entrypoint"],
                    instructions=(
                        f"Ejecutar tool skill.{slug} via executor=python_module, "
                        f"callable={callable_name}, input_schema={manifest['input_schema']}. "
                        f"{manifest['instructions']}"
                    ),
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
        self.on_log("CrearHerramienta", "Ejecutando ciclo principal para disenar la herramienta nueva...")
        final_text, cycles, _loss, reached = pipeline_runner(tool_creation_objective(user_objective))
        self.on_log("CrearHerramienta", f"Ciclo principal terminado: reached={reached}, ciclos={cycles}. Extrayendo especificacion...")
        try:
            spec = parse_generated_tool_spec(final_text)
        except Exception as exc:
            self.on_log("CrearHerramienta", f"El ciclo no entrego JSON instalable ({exc}); intentando extraer Markdown...")
            try:
                spec = parse_markdown_tool_spec(final_text)
            except Exception as md_exc:
                fallback = fallback_tool_spec(user_objective)
                if fallback is None:
                    raise ValueError(
                        "El ciclo principal no produjo una herramienta instalable. "
                        f"JSON: {exc}; Markdown: {md_exc}"
                    ) from md_exc
                self.on_log(
                    "CrearHerramienta",
                    "Usando plantilla segura de recuperacion para cumplir el objetivo solicitado.",
                )
                spec = fallback
        if not _spec_matches_objective(spec, user_objective):
            fallback = fallback_tool_spec(user_objective)
            if fallback is None:
                raise ValueError(
                    f"La herramienta generada ({spec.name}) no corresponde al objetivo pedido."
                )
            self.on_log(
                "CrearHerramienta",
                f"El ciclo genero {spec.name}, que no corresponde al objetivo; usando herramienta especifica recuperada.",
            )
            spec = fallback
        installed = self.install(spec, run_tests=run_tests, timeout=timeout)
        if run_tests and not installed.test_ok:
            fallback = fallback_tool_spec(user_objective)
            if fallback is not None and not _same_spec(spec, fallback):
                self.on_log(
                    "CrearHerramienta",
                    "La primera herramienta fallo pruebas; reintentando con implementacion recuperada.",
                )
                installed = self.install(fallback, run_tests=run_tests, timeout=timeout)
        return InstalledToolResult(
            name=installed.name,
            skill_dir=installed.skill_dir,
            manifest_path=installed.manifest_path,
            test_ok=installed.test_ok,
            test_output=installed.test_output,
            cycles=cycles,
            reached=reached,
            attempts=installed.attempts,
            build_log_path=installed.build_log_path,
        )
