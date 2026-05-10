"""Loop de herramientas para agentes de código con estado observable."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness.orchestrator import _apply_code_blocks, _check_command_policy, _repo_root, _resolve_repo_path, _run_tests
from harness.repo_index import repo_context_for_goal


@dataclass(frozen=True)
class ToolObservation:
    action: str
    ok: bool
    content: str


@dataclass
class AgentLoopState:
    goal: str
    observations: list[ToolObservation] = field(default_factory=list)
    done: bool = False

    def add(self, action: str, ok: bool, content: str) -> ToolObservation:
        observation = ToolObservation(action=action, ok=ok, content=content[-8000:])
        self.observations.append(observation)
        return observation

    def context(self, *, max_items: int = 8) -> str:
        recent = self.observations[-max_items:]
        if not recent:
            return "Sin observaciones previas."
        return "\n\n".join(
            f"## {item.action} ok={item.ok}\n{item.content}"
            for item in recent
        )


def _json_action(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("La acción del agente debe ser un objeto JSON.")
    return data


def execute_tool_action(action: dict[str, Any], *, root: Path | None = None) -> ToolObservation:
    root = root or _repo_root()
    kind = str(action.get("action") or "").strip()
    if kind == "read":
        rel_path = str(action.get("path") or "")
        full_path, reason = _resolve_repo_path(rel_path, root)
        if full_path is None:
            return ToolObservation(kind, False, reason)
        if not full_path.is_file():
            return ToolObservation(kind, False, f"No existe: {rel_path}")
        return ToolObservation(kind, True, full_path.read_text(encoding="utf-8", errors="ignore")[:12000])

    if kind == "search":
        query = str(action.get("query") or "")
        return ToolObservation(kind, True, repo_context_for_goal(query, root=root, limit=int(action.get("limit") or 8)))

    if kind == "patch":
        body = str(action.get("patch") or "")
        result = _apply_code_blocks(f"```patch\n{body.strip()}\n```")
        return ToolObservation(kind, not result.rejected and bool(result.changed_files), result.report)

    if kind == "test":
        files = action.get("files") or []
        changed_files = [str(item) for item in files] if isinstance(files, list) else []
        return ToolObservation(kind, True, _run_tests(changed_files))

    if kind == "command":
        command = str(action.get("command") or "")
        policy = _check_command_policy(command)
        if not policy.allowed:
            return ToolObservation(kind, False, f"BLOQUEADO: {policy.reason}")
        completed = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=float(action.get("timeout") or 60),
        )
        ok = completed.returncode == 0
        return ToolObservation(
            kind,
            ok,
            f"Exit code: {completed.returncode}\n{completed.stdout}\n{completed.stderr}".strip(),
        )

    if kind == "done":
        return ToolObservation(kind, True, str(action.get("summary") or "Tarea terminada."))

    return ToolObservation(kind or "(sin acción)", False, "Acción no soportada.")


AGENT_LOOP_SYSTEM = """Eres un agente de código con herramientas. Responde SOLO JSON.
Acciones disponibles:
{"action":"search","query":"...","limit":5}
{"action":"read","path":"ruta"}
{"action":"patch","patch":"diff unificado"}
{"action":"test","files":["ruta.py"]}
{"action":"command","command":"python -m pytest ...","timeout":60}
{"action":"done","summary":"..."}
No uses comandos destructivos. Prefiere patch y tests enfocados."""


def run_agent_loop(
    goal: str,
    *,
    llm_call: Callable[[str, str], str],
    max_steps: int = 8,
    on_event: Callable[[ToolObservation], None] | None = None,
) -> AgentLoopState:
    """Ejecuta un loop observar -> actuar -> observar con herramientas controladas."""
    state = AgentLoopState(goal=goal)
    for _step in range(max_steps):
        prompt = (
            f"Objetivo:\n{goal}\n\n"
            f"Observaciones recientes:\n{state.context()}\n\n"
            "Elige la siguiente acción JSON."
        )
        raw_action = llm_call(AGENT_LOOP_SYSTEM, prompt)
        try:
            action = _json_action(raw_action)
        except (json.JSONDecodeError, ValueError) as exc:
            observation = state.add("parse", False, f"JSON inválido: {exc}\n{raw_action[:1000]}")
            if on_event:
                on_event(observation)
            continue
        observation = execute_tool_action(action)
        state.observations.append(observation)
        if on_event:
            on_event(observation)
        if observation.action == "done" and observation.ok:
            state.done = True
            break
    return state
