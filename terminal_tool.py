"""
Herramienta de terminal segura para agentes.

El LLM solo propone una accion estructurada; este modulo valida politica,
constrine el directorio de trabajo y ejecuta con timeout/salida acotada.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_TRUE_VALUES = {"1", "true", "yes", "si", "sí", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n"}


@dataclass(frozen=True)
class TerminalCommandRequest:
    needed: bool
    reason: str
    command: str
    cwd: str = "."


@dataclass(frozen=True)
class TerminalCommandResult:
    allowed: bool
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    reason: str = ""

    def render(self) -> str:
        status = "permitido" if self.allowed else "bloqueado"
        exit_text = "n/a" if self.exit_code is None else str(self.exit_code)
        return "\n".join(
            [
                f"status={status}",
                f"command={self.command}",
                f"cwd={self.cwd}",
                f"exit_code={exit_text}",
                f"duration_s={self.duration_s:.2f}",
                f"reason={self.reason or '-'}",
                f"stdout:\n{self.stdout or '(vacio)'}",
                f"stderr:\n{self.stderr or '(vacio)'}",
            ]
        )


_SAFE_COMMANDS_DEFAULT = ("python", "py", "pytest", "git", "rg", "dir", "ls", "where")
_MODERATE_COMMANDS_DEFAULT = ("pip", "node", "npm")
_DANGEROUS_COMMANDS_DEFAULT = (
    "rm",
    "rmdir",
    "del",
    "erase",
    "remove-item",
    "rd",
    "format",
    "shutdown",
    "reboot",
    "restart-computer",
    "stop-computer",
    "setx",
    "reg",
    "schtasks",
    "sc",
)

_BLOCK_PATTERNS = (
    r"\b(rm|rmdir|del|erase|remove-item|rd)\b",
    r"\b(format|shutdown|reboot|restart-computer|stop-computer)\b",
    r"\bgit\s+(reset|clean|checkout|restore|switch|rebase|push|commit)\b",
    r"\bpip\s+(install|uninstall)\b",
    r"\bpython\s+-c\b",
    r"\bpy\s+-c\b",
    r"\b(setx|reg|schtasks|sc)\b",
    r"\.env\b",
)

_SHELL_META_RE = re.compile(r"[&|;<>`]")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def _env_command_list(name: str, default: tuple[str, ...]) -> set[str]:
    raw = (os.getenv(name) or "").strip()
    values = raw.split(",") if raw else list(default)
    commands: set[str] = set()
    for item in values:
        cmd = item.strip().lower()
        if not cmd or not re.fullmatch(r"[a-z0-9_.+-]+", cmd):
            continue
        commands.add(cmd)
    return commands


def configured_allowed_commands() -> set[str]:
    """
    Lista efectiva de comandos base permitidos.

    Por defecto: seguros y moderados activos; peligrosos inactivos. Se configura
    desde .env mediante TERMINAL_AGENT_*_COMMANDS y TERMINAL_AGENT_ENABLE_*.
    """
    allowed: set[str] = set()
    if _env_bool("TERMINAL_AGENT_ENABLE_SAFE_COMMANDS", True):
        allowed.update(
            _env_command_list("TERMINAL_AGENT_SAFE_COMMANDS", _SAFE_COMMANDS_DEFAULT)
        )
    if _env_bool("TERMINAL_AGENT_ENABLE_MODERATE_COMMANDS", True):
        allowed.update(
            _env_command_list(
                "TERMINAL_AGENT_MODERATE_COMMANDS", _MODERATE_COMMANDS_DEFAULT
            )
        )
    if _env_bool("TERMINAL_AGENT_ENABLE_DANGEROUS_COMMANDS", False):
        allowed.update(
            _env_command_list(
                "TERMINAL_AGENT_DANGEROUS_COMMANDS", _DANGEROUS_COMMANDS_DEFAULT
            )
        )
    return allowed


def parse_terminal_command_request(text: str) -> TerminalCommandRequest:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return TerminalCommandRequest(False, "El agente no devolvio JSON valido.", "")

    needed_raw = data.get("necesario", data.get("needed", False))
    needed = bool(needed_raw)
    if isinstance(needed_raw, str):
        needed = needed_raw.strip().lower() in _TRUE_VALUES
    reason = str(data.get("motivo", data.get("reason", "")) or "").strip()[:500]
    command = str(data.get("command", data.get("comando", "")) or "").strip()
    cwd = str(data.get("cwd", ".") or ".").strip()
    return TerminalCommandRequest(needed, reason, command, cwd)


def _first_token(command: str) -> str:
    match = re.match(r"\s*([^\s\"']+)", command)
    return (match.group(1) if match else "").lower()


def validate_terminal_command(command: str) -> tuple[bool, str]:
    cmd = command.strip()
    if not cmd:
        return False, "comando vacio"
    if len(cmd) > 260:
        return False, "comando demasiado largo"
    if "\n" in cmd or "\r" in cmd:
        return False, "solo se permite un comando de una linea"
    if _SHELL_META_RE.search(cmd):
        return False, "metacaracteres de shell bloqueados"
    first = _first_token(cmd)
    if first not in configured_allowed_commands():
        return False, f"comando base no permitido: {first or '(desconocido)'}"
    for pattern in _BLOCK_PATTERNS:
        if re.search(pattern, cmd, flags=re.I):
            return False, "comando bloqueado por politica de seguridad"
    return True, "permitido"


def _safe_cwd(project_root: Path, cwd: str) -> Path:
    root = project_root.resolve()
    candidate = (root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("cwd fuera de la raiz del proyecto") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("cwd no existe o no es directorio")
    return candidate


def _safe_env() -> dict[str, str]:
    keep = {
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_terminal_command(
    command: str,
    *,
    project_root: str | Path | None = None,
    cwd: str = ".",
    timeout: float = 10.0,
    max_output_chars: int = 4000,
) -> TerminalCommandResult:
    root = Path(project_root or Path(__file__).resolve().parent)
    timeout = max(1.0, min(60.0, float(timeout)))
    max_output_chars = max(500, min(12000, int(max_output_chars)))
    started = time.monotonic()

    try:
        safe_dir = _safe_cwd(root, cwd)
    except ValueError as exc:
        return TerminalCommandResult(
            False,
            command.strip(),
            cwd,
            None,
            "",
            "",
            time.monotonic() - started,
            str(exc),
        )

    allowed, reason = validate_terminal_command(command)
    if not allowed:
        return TerminalCommandResult(
            False,
            command.strip(),
            str(safe_dir),
            None,
            "",
            "",
            time.monotonic() - started,
            reason,
        )

    try:
        proc = subprocess.run(
            command,
            cwd=str(safe_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_safe_env(),
            shell=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return TerminalCommandResult(
            True,
            command.strip(),
            str(safe_dir),
            None,
            (exc.stdout or "")[:max_output_chars],
            (exc.stderr or "")[:max_output_chars],
            time.monotonic() - started,
            f"timeout ({timeout:.1f}s)",
        )
    except OSError as exc:
        return TerminalCommandResult(
            True,
            command.strip(),
            str(safe_dir),
            None,
            "",
            str(exc)[:max_output_chars],
            time.monotonic() - started,
            "error al ejecutar",
        )

    stdout = (proc.stdout or "").strip()[:max_output_chars]
    stderr = (proc.stderr or "").strip()[:max_output_chars]
    return TerminalCommandResult(
        True,
        command.strip(),
        str(safe_dir),
        proc.returncode,
        stdout,
        stderr,
        time.monotonic() - started,
        reason,
    )
