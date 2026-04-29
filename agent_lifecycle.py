"""
Ciclo de vida para subagentes especialistas.

El orquestador global delega una mision a cada agente modular; el agente decide
si aporta contexto, si ejecuta su herramienta y devuelve evidencia resumida.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from task_runtime import TaskRuntime
from tool_registry import ToolRegistry


@dataclass(frozen=True)
class AgentLifecycleResult:
    role: str
    content: str
    executed_tool: str = ""
    contributed: bool = False


def _strip_json_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


def _json_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    if value is None:
        return default
    return bool(value)


def run_skill_agent_lifecycle(
    *,
    spec: dict[str, Any],
    objective: str,
    criteria: str,
    llm_call: Callable[[str, str, int], str],
    registry: ToolRegistry,
    runtime: TaskRuntime,
    task_id: str,
    num_predict: int,
) -> AgentLifecycleResult:
    role_name = str(spec.get("name") or f"AgenteSkill:{spec.get('skill_name', 'skill')}")
    tool_name = str(spec.get("tool_name") or "")
    sys_agent = (
        f"Eres {role_name}. Gestionas la integración/skill '{spec.get('skill_name')}'.\n"
        "Tu trabajo es decidir si tu integración aporta al objetivo actual. "
        "Si solo debes aportar contexto, responde con una nota breve. "
        "Si tu skill tiene herramienta ejecutable y aporta evidencia real, solicita ejecutarla.\n"
        "Devuelve SOLO JSON válido:\n"
        "{\"aporta\": false, \"ejecutar\": false, \"args\": {}, \"motivo\": \"...\", \"nota\": \"...\"}\n"
    )
    user_agent = (
        f"OBJETIVO_CLARO:\n{objective}\n\nCRITERIOS:\n{criteria}\n\n"
        f"DESCRIPCION_SKILL:\n{spec.get('description')}\n\n"
        f"INSTRUCCIONES_SKILL:\n{spec.get('instructions')}\n\n"
        f"TOOL_NAME:\n{tool_name}\nEXECUTOR:\n{spec.get('executor') or '(sin executor)'}\n"
        f"CALLABLE:\n{spec.get('callable') or '-'}\nINPUT_SCHEMA:\n{spec.get('input_schema') or {}}\n\n"
        "Decide tu aporte. JSON:"
    )
    out_agent = llm_call(sys_agent, user_agent, num_predict)
    raw_agent = _strip_json_fence(out_agent)
    try:
        agent_data = json.loads(raw_agent)
    except (json.JSONDecodeError, TypeError):
        agent_data = {
            "aporta": True,
            "ejecutar": False,
            "nota": out_agent[:700],
            "motivo": "",
        }

    aporta = _json_bool(agent_data.get("aporta"), False)
    ejecutar = _json_bool(agent_data.get("ejecutar"), False)
    motivo = str(agent_data.get("motivo", "") or "").strip()
    nota = str(agent_data.get("nota", "") or "").strip()
    args = agent_data.get("args", {})
    if not isinstance(args, dict):
        args = {}
    parts = []
    if motivo:
        parts.append(f"Motivo: {motivo}")
    if nota:
        parts.append(f"Nota: {nota}")

    executed_tool = ""
    if ejecutar and tool_name and spec.get("executor"):
        result = registry.call(tool_name, args, runtime=runtime, task_id=task_id)
        rendered = result.render() if hasattr(result, "render") else str(result)
        executed_tool = tool_name
        parts.append(f"Ejecutó {tool_name}:\n{rendered}")

    content = "\n".join(parts).strip()
    contributed = bool(aporta or content)
    if contributed and not content:
        content = "Aporta contexto sin ejecución."
    return AgentLifecycleResult(
        role=role_name,
        content=content,
        executed_tool=executed_tool,
        contributed=contributed,
    )
