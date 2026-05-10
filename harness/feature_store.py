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
    seen_ids: set[int] = set()
    feature_ids: set[int] = set()
    normalized_features: list[tuple[int, dict[str, Any]]] = []
    for i, f in enumerate(feats):
        if not isinstance(f, dict):
            errs.append(f"features[{i}] no es un objeto")
            continue
        try:
            fid = int(f.get("id"))
            if fid in seen_ids:
                errs.append(f"features[{i}].id duplicado: {fid}")
            seen_ids.add(fid)
            feature_ids.add(fid)
            normalized_features.append((fid, f))
        except (TypeError, ValueError):
            errs.append(f"features[{i}].id inválido: {f.get('id')!r}")
        st = f.get("status")
        if st not in VALID_STATUS:
            errs.append(f"features[{i}].status inválido: {st!r}")
        if st == "in_progress" and is_user_task(f):
            in_prog += 1
    if in_prog > 1:
        errs.append("Solo puede haber una tarea de usuario con status «in_progress»")
    for fid, feature in normalized_features:
        deps = _feature_dependencies(feature)
        for dep in deps:
            if dep == fid:
                errs.append(f"feature id={fid} depende de sí misma")
            elif dep not in feature_ids:
                errs.append(f"feature id={fid} depends_on desconocido: {dep}")
    return errs


def _feature_dependencies(feature: dict[str, Any]) -> list[int]:
    """Lee `depends_on` como lista de ids (acepta lista o entero suelto)."""
    raw = feature.get("depends_on")
    if raw is None:
        return []
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, (list, tuple)):
        out: list[int] = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    return []


def _ids_with_status(features: list[dict[str, Any]], status: str) -> set[int]:
    out: set[int] = set()
    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != status:
            continue
        try:
            out.add(int(f.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return out


def feature_dependencies_satisfied(
    feature: dict[str, Any],
    *,
    all_features: list[dict[str, Any]],
) -> bool:
    """Una feature está lista si todas sus `depends_on` están en `done`."""
    deps = _feature_dependencies(feature)
    if not deps:
        return True
    done_ids = _ids_with_status(all_features, "done")
    return all(dep in done_ids for dep in deps)


def pick_next_pending(features: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Elige la siguiente feature pending respetando `depends_on`.

    Una feature solo se elige si **todas** sus `depends_on` están en `done`.
    Si ninguna pending tiene sus dependencias satisfechas, devuelve `None`
    aunque haya pending; eso fuerza al usuario/CLI a destrabar la cadena.
    """
    pending = [
        f
        for f in features
        if isinstance(f, dict)
        and is_user_task(f)
        and f.get("status") == "pending"
    ]
    if not pending:
        return None

    eligible = [
        f for f in pending
        if feature_dependencies_satisfied(f, all_features=features)
    ]
    if not eligible:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        try:
            val = item.get("id")
            if val is None:
                return (1, 0)
            return (0, int(val))
        except (TypeError, ValueError):
            return (1, 0)

    return min(eligible, key=sort_key)


def blocked_by_dependencies(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lista features pending cuyas dependencias aún no están satisfechas."""
    pending = [
        f
        for f in features
        if isinstance(f, dict)
        and is_user_task(f)
        and f.get("status") == "pending"
    ]
    return [
        f for f in pending
        if not feature_dependencies_satisfied(f, all_features=features)
    ]


def feature_by_id(features: list[dict[str, Any]], fid: int) -> dict[str, Any] | None:
    for f in features:
        if isinstance(f, dict):
            val = f.get("id")
            try:
                if val is not None and int(val) == fid:
                    return f
            except (TypeError, ValueError):
                continue
    return None


def set_feature_status(
    data: dict[str, Any], fid: int, status: str
) -> bool:
    if status not in VALID_STATUS:
        return False
    for f in data.get("features") or []:
        if isinstance(f, dict):
            val = f.get("id")
            try:
                if val is not None and int(val) == fid:
                    f["status"] = status
                    return True
            except (TypeError, ValueError):
                continue
    return False
