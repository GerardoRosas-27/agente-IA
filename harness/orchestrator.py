"""Orquestación Líder → Implementador → Revisor; estado en disco (anti teléfono descompuesto)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from harness.feature_store import (
    load_feature_list,
    pick_next_pending,
    save_feature_list,
    set_feature_status,
    validate_feature_list,
)
from harness.llm import invoke_llm
from harness.paths import (
    AGENTS_MD,
    CHECKPOINTS_MD,
    DOCS_DIR,
    FEATURE_LIST_PATH,
    PROGRESS_DIR,
)
from harness import prompts


def _read_head(path: Path, max_chars: int = 4500) -> str:
    if not path.is_file():
        return "(archivo ausente)\n"
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n… [truncado]"


def _safe_slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (name or "feature").strip(), flags=re.ASCII)
    s = s.strip("_")[:80] or "feature"
    return s


def _append_markdown(path: Path, heading: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    block = f"\n\n## [{stamp}] {heading}\n\n{body.strip()}\n"
    if path.is_file():
        path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    else:
        path.write_text(f"# {path.stem}\n" + block, encoding="utf-8")


def _append_history_line(line: str) -> None:
    h = PROGRESS_DIR / "history.md"
    _append_markdown(h, "Entrada", line)


def parse_verdict(reviewer_text: str) -> bool | None:
    for line in reviewer_text.strip().splitlines():
        u = line.strip().upper().replace(" ", "")
        if u == "VERDICT:PASS":
            return True
        if u == "VERDICT:FAIL":
            return False
    return None


def _feature_markdown(feat: dict[str, Any]) -> str:
    acc = feat.get("acceptance") or []
    acc_txt = "\n".join(f"- {a}" for a in acc) if isinstance(acc, list) else str(acc)
    return "\n".join(
        [
            f"id: {feat.get('id')}",
            f"name: {feat.get('name')}",
            f"title: {feat.get('title')}",
            f"description: {feat.get('description')}",
            "acceptance:",
            acc_txt or "- (sin criterios explícitos)",
        ]
    )


@dataclass
class HarnessCycleResult:
    feature_id: int
    feature_name: str
    verdict: bool | None
    impl_path: Path
    review_path: Path
    message: str


def _get_in_progress(features: list[dict[str, Any]]) -> dict[str, Any] | None:
    for f in features:
        if isinstance(f, dict) and f.get("status") == "in_progress":
            return f
    return None


def _run_tests() -> str:
    """Ejecuta los tests del proyecto y devuelve el output."""
    root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"]
    try:
        r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=60)
        out = r.stdout + "\n" + r.stderr
        return f"Exit code: {r.returncode}\n{out.strip()}"
    except Exception as e:
        return f"Error ejecutando tests: {e}"


def run_one_feature_cycle(
    *,
    model: str,
    llm_chat: Callable[..., Any] | None = None,
    num_predict_leader: int = 1200,
    num_predict_worker: int = 2800,
    max_retries: int = 2,
    on_log: Callable[[str], None] | None = None,
) -> HarnessCycleResult | None:
    """
    Ejecuta un ciclo completo sobre la feature `in_progress`, o reclama la siguiente `pending`.
    """
    log = on_log or (lambda _m: None)

    data = load_feature_list(FEATURE_LIST_PATH)
    errs = validate_feature_list(data)
    if errs:
        raise ValueError("feature_list.json inválido:\n" + "\n".join(errs))

    features: list[dict[str, Any]] = list(data.get("features") or [])
    feat = _get_in_progress(features)
    if feat is None:
        feat = pick_next_pending(features)
        if feat is None:
            log("No hay features pendientes.")
            return None
        fid = int(feat["id"])
        set_feature_status(data, fid, "in_progress")
        save_feature_list(FEATURE_LIST_PATH, data)
        log(f"Feature {fid} marcada in_progress.")

    fid = int(feat["id"])
    slug = _safe_slug(str(feat.get("name") or f"feature_{fid}"))
    fblock = _feature_markdown(feat)

    agents_x = _read_head(AGENTS_MD, 3500)
    cp_x = _read_head(CHECKPOINTS_MD, 2500)
    arch_x = _read_head(DOCS_DIR / "architecture.md", 3500)
    conv_x = _read_head(DOCS_DIR / "conventions.md", 2500)
    ver_x = _read_head(DOCS_DIR / "verification.md", 2500)

    log("Líder: planificando…")
    leader_out = invoke_llm(
        model,
        prompts.LEADER_SYSTEM,
        prompts.leader_user_message(
            feature_block=fblock,
            agents_excerpt=agents_x,
            checkpoints_excerpt=cp_x,
        ),
        llm_chat=llm_chat,
        num_predict=num_predict_leader,
        role_hint="leader",
    )
    _append_markdown(PROGRESS_DIR / "current.md", f"Líder · feature {fid}", leader_out)

    verdict = None
    previous_feedback = None
    impl_path = PROGRESS_DIR / f"impl_{slug}.md"
    review_path = PROGRESS_DIR / f"review_{slug}.md"
    impl_body = ""
    review_body = ""

    for attempt in range(max_retries + 1):
        log(f"Implementador: redactando informe (intento {attempt + 1}/{max_retries + 1})…")
        impl_body = invoke_llm(
            model,
            prompts.IMPLEMENTER_SYSTEM,
            prompts.implementer_user_message(
                feature_block=fblock,
                leader_plan=leader_out,
                architecture_excerpt=arch_x,
                conventions_excerpt=conv_x,
                previous_feedback=previous_feedback,
            ),
            llm_chat=llm_chat,
            num_predict=num_predict_worker,
            role_hint=f"implementer_attempt_{attempt + 1}",
        )
        impl_path.write_text(
            f"# Implementación · {feat.get('title')} (Intento {attempt + 1})\n\n{impl_body}\n",
            encoding="utf-8",
        )

        log("Ejecutando tests automatizados…")
        test_output = _run_tests()
        log(f"Tests finalizados. Longitud del output: {len(test_output)} caracteres.")

        log("Revisor: evaluando…")
        review_body = invoke_llm(
            model,
            prompts.REVIEWER_SYSTEM,
            prompts.reviewer_user_message(
                feature_block=fblock,
                impl_report=impl_body,
                verification_excerpt=ver_x,
                checkpoints_excerpt=cp_x,
                test_output=test_output,
            ),
            llm_chat=llm_chat,
            num_predict=num_predict_leader,
            role_hint=f"reviewer_attempt_{attempt + 1}",
        )
        review_path.write_text(
            f"# Revisión · {feat.get('title')} (Intento {attempt + 1})\n\n{review_body}\n\n## Output de Tests\n```text\n{test_output}\n```\n",
            encoding="utf-8",
        )

        verdict = parse_verdict(review_body)
        if verdict is True:
            break
        else:
            previous_feedback = review_body
            if attempt < max_retries:
                log("El revisor rechazó la implementación. Reintentando…")

    data = load_feature_list(FEATURE_LIST_PATH)
    if verdict is True:
        set_feature_status(data, fid, "done")
        msg = f"Feature {fid} marcada done. Artefactos: {impl_path.name}, {review_path.name}"
        _append_history_line(
            f"- **{stamp_summary()}** feature `{slug}` (id={fid}) → **DONE**. "
            f"Ver `{impl_path.name}` y `{review_path.name}`."
        )
    elif verdict is False:
        set_feature_status(data, fid, "pending")
        msg = (
            f"Revisor FAIL: feature {fid} vuelve a pending. Revisa `{review_path.name}` "
            "y corrige antes de reintentar."
        )
        _append_history_line(
            f"- **{stamp_summary()}** feature `{slug}` (id={fid}) → **FAIL** (pending de nuevo). "
            f"Motivo en `{review_path.name}`."
        )
    else:
        set_feature_status(data, fid, "pending")
        msg = (
            f"Veredicto ambiguo: feature {fid} queda pending. "
            "El revisor debe empezar con VERDICT: PASS o VERDICT: FAIL."
        )
        _append_history_line(
            f"- **{stamp_summary()}** feature `{slug}` (id={fid}) → **AMBIGUO** (pending)."
        )

    save_feature_list(FEATURE_LIST_PATH, data)
    log(msg)
    return HarnessCycleResult(
        feature_id=fid,
        feature_name=slug,
        verdict=verdict,
        impl_path=impl_path,
        review_path=review_path,
        message=msg,
    )


def stamp_summary() -> str:
    return datetime.now().isoformat(timespec="seconds")


def expand_features_from_goal(
    user_goal: str,
    *,
    model: str,
    llm_chat: Callable[..., Any] | None = None,
    num_predict: int = 4000,
) -> int:
    """Añade features generadas por el modelo (inicializador). Devuelve cuántas se añadieron."""
    data = load_feature_list(FEATURE_LIST_PATH)
    errs = validate_feature_list(data)
    if errs:
        raise ValueError("\n".join(errs))
    features: list[dict[str, Any]] = list(data.get("features") or [])
    max_id = 0
    for f in features:
        if isinstance(f, dict):
            try:
                max_id = max(max_id, int(f.get("id") or 0))
            except (TypeError, ValueError):
                pass

    raw = invoke_llm(
        model,
        prompts.INIT_EXPAND_SYSTEM,
        prompts.initializer_user_message(user_goal=user_goal, max_existing_id=max_id),
        llm_chat=llm_chat,
        num_predict=num_predict,
        temperature=0.4,
        role_hint="initializer",
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    new_items = json.loads(cleaned)
    if not isinstance(new_items, list):
        raise ValueError("El inicializador no devolvió un array JSON.")

    added = 0
    next_id = max_id + 1
    for item in new_items:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item.setdefault("status", "pending")
        if item["status"] not in {"pending"}:
            item["status"] = "pending"
        try:
            oid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            oid = 0
        if oid <= max_id or any(
            isinstance(f, dict) and int(f.get("id") or -1) == oid for f in features
        ):
            item["id"] = next_id
            next_id += 1
        else:
            next_id = max(next_id, oid + 1)
        features.append(item)
        added += 1

    data["features"] = features
    save_feature_list(FEATURE_LIST_PATH, data)
    _append_history_line(
        f"- **{stamp_summary()}** inicializador: +{added} features desde objetivo de usuario."
    )
    return added
