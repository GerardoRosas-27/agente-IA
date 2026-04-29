"""
Registro tipado de herramientas para el sistema multiagente.

El LLM ya no deberia "llamar modulos" implicitamente: solicita una herramienta
registrada, el registry aplica politica y el TaskRuntime guarda auditoria.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from task_runtime import TaskRuntime

_TRUE_VALUES = {"1", "true", "yes", "si", "sí", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n"}
_RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "dangerous": 3}


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    risk: str
    input_schema: dict[str, str]
    handler: Callable[..., Any]
    default_enabled: bool = True
    enabled_env: str = ""


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    reason: str


ApprovalCallback = Callable[[RegisteredTool, dict[str, Any], str], bool]


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def _env_csv(name: str) -> set[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


class ToolRegistry:
    def __init__(
        self,
        *,
        autonomous: bool = False,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self.autonomous = bool(autonomous)
        self.approval_callback = approval_callback

    def register(self, tool: RegisteredTool) -> None:
        name = tool.name.strip()
        if not name:
            raise ValueError("tool.name vacio")
        if tool.risk not in _RISK_ORDER:
            raise ValueError(f"risk invalido para {name}: {tool.risk}")
        self._tools[name] = tool

    def list_tools(self) -> list[RegisteredTool]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"herramienta no registrada: {name}") from exc

    def decide(
        self,
        tool: RegisteredTool,
        request: dict[str, Any] | None = None,
        *,
        interactive: bool = False,
    ) -> ToolDecision:
        denied = _env_csv("TOOL_REGISTRY_DENIED_TOOLS")
        allowed_only = _env_csv("TOOL_REGISTRY_ALLOWED_TOOLS")
        if tool.name in denied:
            return ToolDecision(False, "bloqueada por TOOL_REGISTRY_DENIED_TOOLS")
        if allowed_only and tool.name not in allowed_only:
            return ToolDecision(False, "fuera de TOOL_REGISTRY_ALLOWED_TOOLS")

        enabled = tool.default_enabled
        if tool.enabled_env:
            enabled = _env_bool(tool.enabled_env, tool.default_enabled)
        if not enabled:
            return ToolDecision(False, f"desactivada por {tool.enabled_env or 'configuracion'}")

        if self.autonomous or _env_bool("AGENT_AUTONOMOUS_MODE", False):
            return ToolDecision(True, "permitida por modo autonomo")

        max_risk = (os.getenv("TOOL_REGISTRY_MAX_AUTO_RISK") or "moderate").strip().lower()
        max_value = _RISK_ORDER.get(max_risk, _RISK_ORDER["moderate"])
        if _RISK_ORDER[tool.risk] > max_value:
            reason = f"requiere aprobacion: riesgo {tool.risk}"
            if interactive and self.approval_callback is not None:
                approved = self.approval_callback(tool, request or {}, reason)
                if approved:
                    return ToolDecision(True, f"aprobada por usuario: riesgo {tool.risk}")
                return ToolDecision(False, f"rechazada por usuario: riesgo {tool.risk}")
            return ToolDecision(False, reason)
        return ToolDecision(True, "permitida por politica")

    def call(
        self,
        name: str,
        request: dict[str, Any],
        *,
        runtime: TaskRuntime,
        task_id: str,
    ) -> Any:
        tool = self.get(name)
        decision = self.decide(tool, request, interactive=True)

        def _invoke() -> Any:
            return tool.handler(**request)

        return runtime.run_recorded(
            task_id=task_id,
            tool_name=tool.name,
            risk=tool.risk,
            request=request,
            allowed=decision.allowed,
            decision=decision.reason,
            fn=_invoke if decision.allowed else None,
        )

    def context(self) -> str:
        lines = ["Herramientas registradas:"]
        for tool in self.list_tools():
            decision = self.decide(tool, interactive=False)
            status = "ON" if decision.allowed else "OFF"
            schema = ", ".join(f"{k}:{v}" for k, v in tool.input_schema.items())
            lines.append(
                f"- {tool.name} [{tool.risk}] {status}: {tool.description} | input={schema}"
            )
        return "\n".join(lines)


def build_default_tool_registry(
    *,
    search_fn: Callable[..., str],
    run_terminal_fn: Callable[..., object],
    tool_library: Any | None,
    project_root: str,
    skill_manager: Any | None = None,
    autonomous: bool = False,
    approval_callback: ApprovalCallback | None = None,
) -> ToolRegistry:
    from node_probe_tool import run_node_probe_script
    from objective_agent_cycle import run_python_probe_script

    registry = ToolRegistry(autonomous=autonomous, approval_callback=approval_callback)
    registry.register(
        RegisteredTool(
            name="internet.search",
            description="Busca contexto publico para el objetivo.",
            risk="moderate",
            input_schema={"query": "str", "max_results": "int", "timeout": "float"},
            handler=search_fn,
            default_enabled=True,
            enabled_env="INTERNET_AGENT_ENABLED",
        )
    )
    registry.register(
        RegisteredTool(
            name="probe.python",
            description="Ejecuta un script Python pequeno y autocontenido.",
            risk="low",
            input_schema={"script": "str", "timeout": "float", "max_chars": "int"},
            handler=run_python_probe_script,
            default_enabled=True,
            enabled_env="PYTHON_TEST_AGENT_ENABLED",
        )
    )
    registry.register(
        RegisteredTool(
            name="probe.node",
            description="Ejecuta un script JavaScript pequeno con Node.js.",
            risk="low",
            input_schema={"script": "str", "timeout": "float", "max_chars": "int"},
            handler=run_node_probe_script,
            default_enabled=True,
            enabled_env="NODE_TEST_AGENT_ENABLED",
        )
    )
    registry.register(
        RegisteredTool(
            name="terminal.run",
            description="Ejecuta un comando de terminal validado por politica.",
            risk="high",
            input_schema={
                "command": "str",
                "cwd": "str",
                "timeout": "float",
                "max_output_chars": "int",
            },
            handler=lambda command, cwd=".", timeout=10.0, max_output_chars=4000: run_terminal_fn(
                command,
                project_root=project_root,
                cwd=cwd,
                timeout=timeout,
                max_output_chars=max_output_chars,
            ),
            default_enabled=False,
            enabled_env="TERMINAL_AGENT_ENABLED",
        )
    )
    if tool_library is not None:
        registry.register(
            RegisteredTool(
                name="tool_library.context",
                description="Recupera herramientas reutilizables relevantes.",
                risk="low",
                input_schema={"query": "str", "limit": "int", "max_chars": "int"},
                handler=tool_library.context,
                default_enabled=True,
                enabled_env="TOOL_LIBRARY_AGENT_ENABLED",
            )
        )
        registry.register(
            RegisteredTool(
                name="tool_library.upsert",
                description="Guarda una herramienta reutilizable validada.",
                risk="moderate",
                input_schema={"tool": "ToolMemory"},
                handler=lambda tool: tool_library.upsert(tool),
                default_enabled=True,
                enabled_env="TOOL_LIBRARY_AGENT_ENABLED",
            )
        )
    if skill_manager is not None:
        try:
            skill_manager.register_executable_tools(
                registry,
                run_terminal_fn=run_terminal_fn,
                project_root=project_root,
                tool_library=tool_library,
            )
        except Exception:
            pass
        registry.register(
            RegisteredTool(
                name="skills.context",
                description="Recupera skills/plugins instalados relevantes.",
                risk="low",
                input_schema={"query": "str", "limit": "int", "max_chars": "int"},
                handler=skill_manager.context,
                default_enabled=True,
                enabled_env="SKILL_MANAGER_ENABLED",
            )
        )
        registry.register(
            RegisteredTool(
                name="skills.agents_context",
                description="Recupera agentes modulares instalados por integracion.",
                risk="low",
                input_schema={"query": "str", "limit": "int", "max_chars": "int"},
                handler=skill_manager.agents_context,
                default_enabled=True,
                enabled_env="SKILL_MANAGER_ENABLED",
            )
        )
    return registry
