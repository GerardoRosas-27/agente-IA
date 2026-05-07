"""Lista de features en JSON: una `in_progress` a la vez (regla del harness)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


VALID_STATUS = frozenset({"pending", "in_progress", "done", "blocked"})
USER_TASK_ORIGIN = "user"


def is_user_task(feature: dict[str, Any]) -> bool:
    """Indica si una feature pertenece a la cola visible del usuario."""
    return feature.get("origin") == USER_TASK_ORIGIN


def load_feature_list(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def save_feature_list(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(
        suffix=".json", prefix="feature_list_", dir=str(path.parent)
    )
    try:
        with open(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        Path(tmp).replace(path)
    except BaseException:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def validate_feature_list(data: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    feats = data.get("features")
    if not isinstance(feats, list):
        return ["feature_list.json: falta array «features»"]
    in_prog = 0
    for i, f in enumerate(feats):
        if not isinstance(f, dict):
            errs.append(f"features[{i}] no es un objeto")
            continue
        st = f.get("status")
        if st not in VALID_STATUS:
            errs.append(f"features[{i}].status inválido: {st!r}")
        if st == "in_progress" and is_user_task(f):
            in_prog += 1
    if in_prog > 1:
        errs.append("Solo puede haber una tarea de usuario con status «in_progress»")
    return errs


def pick_next_pending(features: list[dict[str, Any]]) -> dict[str, Any] | None:
    pending = [
        f
        for f in features
        if isinstance(f, dict)
        and is_user_task(f)
        and f.get("status") == "pending"
    ]
    if not pending:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        try:
            val = item.get("id")
            if val is None:
                return (1, 0)
            return (0, int(val))
        except (TypeError, ValueError):
            return (1, 0)

    return min(pending, key=sort_key)


def feature_by_id(features: list[dict[str, Any]], fid: int) -> dict[str, Any] | None:
    for f in features:
        if isinstance(f, dict):
            val = f.get("id")
            if val is not None and int(val) == fid:
                return f
    return None


def set_feature_status(
    data: dict[str, Any], fid: int, status: str
) -> bool:
    if status not in VALID_STATUS:
        return False
    for f in data.get("features") or []:
        if isinstance(f, dict):
            val = f.get("id")
            if val is not None and int(val) == fid:
                f["status"] = status
                return True
    return False
