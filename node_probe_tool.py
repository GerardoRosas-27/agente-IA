"""
Agente auxiliar para ejecutar pruebas pequeñas en Node.js.

No reemplaza al agente Terminal: este modulo ejecuta scripts JavaScript
controlados en una carpeta temporal y devuelve evidencia al ciclo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_TRUE_VALUES = {"1", "true", "yes", "si", "sí", "on", "y"}

_NODE_PROBE_BLOCKLIST = (
    r"\brequire\s*\(\s*['\"](?:fs|child_process|net|tls|http|https|dgram|readline|worker_threads|cluster|os)['\"]\s*\)",
    r"\bimport\s+.*\s+from\s+['\"](?:fs|child_process|net|tls|http|https|dgram|readline|worker_threads|cluster|os)['\"]",
    r"\bprocess\s*\.\s*env\b",
    r"\bprocess\s*\.\s*(exit|kill|chdir)\s*\(",
    r"\b(eval|Function)\s*\(",
    r"\b(WebSocket|fetch|XMLHttpRequest)\s*\(",
    r"\bDeno\b",
)


def parse_node_probe_request(text: str) -> tuple[bool, str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|javascript|js)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, "El agente no solicitó un script Node.js ejecutable.", ""

    needed_raw = data.get("necesario", data.get("needed", False))
    needed = bool(needed_raw)
    if isinstance(needed_raw, str):
        needed = needed_raw.strip().lower() in _TRUE_VALUES
    reason = str(data.get("motivo", data.get("reason", "")) or "").strip()[:500]
    script = str(data.get("script", data.get("code", "")) or "").strip()
    return needed, reason, script


def validate_node_probe_script(script: str, *, max_chars: int = 3000) -> tuple[bool, str]:
    code = script.strip()
    max_chars = max(200, min(10000, int(max_chars)))
    if not code:
        return False, "No se ejecutó script: el agente no entregó código."
    if len(code) > max_chars:
        return False, f"No se ejecutó script: excede el límite configurado ({max_chars} caracteres)."
    for pattern in _NODE_PROBE_BLOCKLIST:
        if re.search(pattern, code, flags=re.I | re.M):
            return False, "No se ejecutó script: contiene operaciones bloqueadas para Node.js."
    return True, "permitido"


def run_node_probe_script(
    script: str,
    *,
    timeout: float = 5.0,
    max_chars: int = 3000,
) -> str:
    ok, reason = validate_node_probe_script(script, max_chars=max_chars)
    if not ok:
        return reason

    node_bin = shutil.which("node")
    if not node_bin:
        return "No se ejecutó script: Node.js no está instalado o no está en PATH."

    timeout = max(1.0, min(20.0, float(timeout)))
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC"}
    }
    child_env["NODE_DISABLE_COLORS"] = "1"

    with tempfile.TemporaryDirectory(prefix="plastic_node_probe_") as tmp:
        script_path = Path(tmp) / "probe.js"
        script_path.write_text(script.strip(), encoding="utf-8")
        try:
            proc = subprocess.run(
                [node_bin, str(script_path)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Script Node.js cancelado por timeout ({timeout:.1f}s)."

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    out = [
        f"exit_code={proc.returncode}",
        f"stdout:\n{stdout[:1200] or '(vacío)'}",
    ]
    if stderr:
        out.append(f"stderr:\n{stderr[:1000]}")
    return "\n".join(out)
