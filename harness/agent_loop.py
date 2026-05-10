"""Loop de herramientas para agentes de código con estado observable."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from harness.paths import PROGRESS_DIR
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
    session_id: str = ""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "done": self.done,
            "session_id": self.session_id,
            "observations": [
                {"action": item.action, "ok": item.ok, "content": item.content}
                for item in self.observations
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentLoopState":
        observations = [
            ToolObservation(
                action=str(item.get("action") or ""),
                ok=bool(item.get("ok")),
                content=str(item.get("content") or ""),
            )
            for item in data.get("observations", [])
            if isinstance(item, dict)
        ]
        return cls(
            goal=str(data.get("goal") or ""),
            observations=observations,
            done=bool(data.get("done")),
            session_id=str(data.get("session_id") or ""),
        )


def _session_slug(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:80].strip("_")
    return slug or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def session_path(session_id: str, *, base_dir: Path = PROGRESS_DIR / "agent_sessions") -> Path:
    return base_dir / f"{_session_slug(session_id)}.json"


def save_agent_session(state: AgentLoopState, *, base_dir: Path = PROGRESS_DIR / "agent_sessions") -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    sid = state.session_id or _session_slug(state.goal)
    state.session_id = sid
    path = session_path(sid, base_dir=base_dir)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_agent_session(session_id: str, *, base_dir: Path = PROGRESS_DIR / "agent_sessions") -> AgentLoopState:
    path = session_path(session_id, base_dir=base_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    return AgentLoopState.from_dict(data)


def _checkpoint_patch(action_patch: str, *, root: Path) -> Path:
    checkpoint_dir = PROGRESS_DIR / "agent_sessions" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    before = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    path = checkpoint_dir / f"{stamp}.patch"
    path.write_text(
        "# Checkpoint antes de aplicar patch del agente\n"
        "## Git diff previo\n"
        "```patch\n"
        f"{before.stdout}\n"
        "```\n\n"
        "## Patch solicitado\n"
        "```patch\n"
        f"{action_patch.strip()}\n"
        "```\n",
        encoding="utf-8",
    )
    return path


def _requested_patch_from_checkpoint(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    marker = "## Patch solicitado"
    if marker not in text:
        return text
    section = text.split(marker, 1)[1]
    if "```patch" in section:
        section = section.split("```patch", 1)[1]
        section = section.split("```", 1)[0]
    return section.strip("\r\n") + "\n"


def rollback_checkpoint(checkpoint_path: str, *, root: Path | None = None) -> ToolObservation:
    root = root or _repo_root()
    full_path, reason = _resolve_repo_path(checkpoint_path, root)
    if full_path is None:
        # Checkpoints normally live under progress and may be passed as absolute-ish UI text.
        candidate = Path(checkpoint_path)
        if candidate.is_absolute() and candidate.is_file():
            full_path = candidate
        else:
            return ToolObservation("rollback", False, reason)
    if not full_path.is_file():
        return ToolObservation("rollback", False, f"No existe checkpoint: {checkpoint_path}")
    patch_text = _requested_patch_from_checkpoint(full_path)
    process = subprocess.run(
        ["git", "apply", "-R", "--whitespace=nowarn"],
        cwd=str(root),
        input=patch_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return ToolObservation(
        "rollback",
        process.returncode == 0,
        f"Exit code: {process.returncode}\n{process.stdout}\n{process.stderr}".strip(),
    )


def _preview_patch(patch_text: str, *, root: Path) -> ToolObservation:
    process = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=str(root),
        input=patch_text.strip("\r\n") + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    ok = process.returncode == 0
    status = "Patch aplicable" if ok else "Patch no aplicable"
    return ToolObservation(
        "patch_preview",
        ok,
        f"{status}. No se aplicaron cambios. Para aplicar, repite la acción con apply=true.\n"
        f"{process.stdout}{process.stderr}".strip(),
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
        if not body.strip():
            return ToolObservation(kind, False, "Patch vacío.")
        if not bool(action.get("apply")):
            return _preview_patch(body, root=root)
        checkpoint = _checkpoint_patch(body, root=root)
        result = _apply_code_blocks(f"```patch\n{body.strip()}\n```", root=root)
        return ToolObservation(
            kind,
            not result.rejected and bool(result.changed_files),
            f"Checkpoint: {checkpoint}\n{result.report}",
        )

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

    if kind == "rollback":
        return rollback_checkpoint(str(action.get("checkpoint") or ""), root=root)

    if kind == "done":
        return ToolObservation(kind, True, str(action.get("summary") or "Tarea terminada."))

    return ToolObservation(kind or "(sin acción)", False, "Acción no soportada.")


AGENT_LOOP_SYSTEM = """Eres un agente de código con herramientas. Responde SOLO JSON.
Acciones disponibles:
{"action":"search","query":"...","limit":5}
{"action":"read","path":"ruta"}
{"action":"patch","patch":"diff unificado"}  // preview, no aplica
{"action":"patch","patch":"diff unificado","apply":true}  // aplica con checkpoint
{"action":"test","files":["ruta.py"]}
{"action":"command","command":"python -m pytest ...","timeout":60}
{"action":"rollback","checkpoint":"progress/agent_sessions/checkpoints/archivo.patch"}
{"action":"done","summary":"..."}
No uses comandos destructivos. Primero previsualiza patches; aplica solo cuando estés seguro."""


def run_agent_loop(
    goal: str,
    *,
    llm_call: Callable[[str, str], str],
    max_steps: int = 8,
    on_event: Callable[[ToolObservation], None] | None = None,
    initial_state: AgentLoopState | None = None,
    session_id: str = "",
    autosave: bool = True,
    learn_from_usage: bool = True,
) -> AgentLoopState:
    """Ejecuta un loop observar -> actuar -> observar con herramientas controladas."""
    state = initial_state or AgentLoopState(goal=goal, session_id=session_id)
    if session_id and not state.session_id:
        state.session_id = session_id
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
        if learn_from_usage:
            try:
                from harness.auto_training import record_agent_observation

                record_agent_observation(goal, observation.action, ok=observation.ok, content=observation.content)
            except Exception:
                pass
        if on_event:
            on_event(observation)
        if autosave:
            save_agent_session(state)
        if observation.action == "done" and observation.ok:
            state.done = True
            if autosave:
                save_agent_session(state)
            break
    return state
