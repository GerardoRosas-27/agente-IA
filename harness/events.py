"""Logging estructurado en JSONL para observabilidad.

Cada evento es una línea JSON appendeada a `progress/events.jsonl`. Sirve para:
- construir un dashboard mínimo sin parsear markdown.
- detectar regresiones (¿la tasa de PASS cae?, ¿el verifier override sube?).
- alimentar análisis offline sin tocar el harness.

Schema mínimo por evento:
    {
      "ts": "2026-05-10T05:30:00",   # ISO con segundos
      "event": "verifier.verdict",   # tipo (`role.action` por convención)
      "role": "verifier",            # opcional
      "feature_id": 5,               # opcional
      "feature_name": "...",         # opcional
      "outcome": "pass",             # opcional, libre
      "score": 0.87,                 # opcional, numérico
      "extra": {...}                 # campo libre para detalles
    }
"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.paths import PROGRESS_DIR


_EVENTS_LOG_LOCK = threading.Lock()


def _json_safe(value: Any) -> Any:
    """Convierte valores arbitrarios en algo serializable por JSON.

    `emit_event` se usa como observabilidad, por lo que nunca debe romper el
    flujo principal si le pasan un `Path`, una dataclass o un objeto dentro de
    un dict/list. Convertimos recursivamente a tipos JSON o `str(value)`.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _events_path() -> Path:
    """Permite que tests sustituyan PROGRESS_DIR sin invalidar el módulo."""
    return PROGRESS_DIR / "events.jsonl"


def emit_event(
    event: str,
    *,
    path: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Appendea un evento JSONL. Devuelve el dict serializado para inspección.

    Falla silenciosa: si el filesystem rechaza la escritura (e.g. tests sin
    PROGRESS_DIR), se loguea por stdout y se sigue. La intención es que la
    observabilidad nunca rompa el flujo de negocio.
    """
    payload: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": event,
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = _json_safe(value)
    target = path or _events_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with _EVENTS_LOG_LOCK:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except (OSError, TypeError, ValueError) as exc:
        print(f"events.emit_event: no se pudo escribir {target}: {exc}")
    return payload


def read_events(*, path: Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Lee los últimos eventos. Útil para dashboards y tests."""
    target = path or _events_path()
    if not target.is_file():
        return []
    bounded = limit is not None and limit > 0
    out: list[dict[str, Any]] | deque[dict[str, Any]]
    out = deque(maxlen=int(limit)) if bounded else []
    try:
        with target.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    out.append(json.loads(stripped))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(out)


def event_counts_by_outcome(*, path: Path | None = None) -> dict[str, int]:
    """Resumen rápido outcome→count para ver tendencias."""
    counts: dict[str, int] = {}
    for evt in read_events(path=path):
        outcome = str(evt.get("outcome") or evt.get("event") or "")
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
