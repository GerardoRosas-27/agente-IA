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
    USER_TASK_ORIGIN,
    is_user_task,
    load_feature_list,
    pick_next_pending,
    save_feature_list,
    set_feature_status,
    validate_feature_list,
)
from harness.llm import invoke_llm
from harness.skill_registry import enabled_skills_context
from harness.skill_registry import sync_skills
from harness.shared_memory import (
    add_self_improvement,
    record_skill_usage,
    remember,
    self_improvement_context,
    shared_memory_context,
)
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
        if (
            isinstance(f, dict)
            and is_user_task(f)
            and f.get("status") == "in_progress"
        ):
            return f
    return None


def _apply_code_blocks(text: str) -> list[str]:
    import re
    # Busca bloques tipo ```python:ruta/archivo.py o ```ruta/archivo.py
    # También busca bloques que solo tengan el nombre del archivo en la primera línea del bloque o justo antes
    saved_files = []
    root = Path(__file__).resolve().parent.parent
    
    # Intento 1: Formato estricto ```python:ruta/archivo.py
    pattern1 = r"```[a-zA-Z0-9]*:([^\s]+)\n(.*?)```"
    matches1 = re.finditer(pattern1, text, re.DOTALL)
    
    # Intento 2: Formato relajado donde el archivo se menciona justo antes del bloque
    # Ej: Archivo `src/whatsapp.py`:
    # ```python
    # ...
    # ```
    pattern2 = r"(?:Archivo|File|Crear|Modificar).*?`([a-zA-Z0-9_/\.\-]+)`.*?\n.*?```[a-zA-Z0-9]*\n(.*?)```"
    matches2 = re.finditer(pattern2, text, re.DOTALL | re.IGNORECASE)
    
    all_matches = []
    for m in matches1:
        all_matches.append((m.group(1).strip(), m.group(2)))
        
    if not all_matches:
        for m in matches2:
            all_matches.append((m.group(1).strip(), m.group(2)))
            
    for rel_path, code in all_matches:
        try:
            full_path = (root / rel_path).resolve()
            # Asegurar que está dentro del repo
            if root in full_path.parents:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(code, encoding="utf-8")
                saved_files.append(rel_path)
        except Exception:
            pass
            
    return saved_files


def _run_bash_blocks(text: str, log: Callable[[str], None]) -> None:
    import re
    root = Path(__file__).resolve().parent.parent
    pattern = r"```bash\n(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    for m in matches:
        cmd_str = m.group(1).strip()
        if not cmd_str:
            continue
        log(f"Ejecutando dependencias/comandos bash:\n{cmd_str}")
        try:
            cmd = ["cmd", "/c", cmd_str] if sys.platform == "win32" else ["bash", "-c", cmd_str]
            r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=180)
            log(f"Salida bash (Exit {r.returncode}):\n{r.stdout}\n{r.stderr}")
        except Exception as e:
            log(f"Error ejecutando bash: {e}")


def _module_name_from_path(rel_path: str) -> str | None:
    if not rel_path.endswith(".py"):
        return None
    rel = rel_path[:-3].replace("\\", "/").strip("/")
    parts = [part for part in rel.split("/") if part and part != "__init__"]
    if not parts:
        return None
    if any(not re.match(r"^[A-Za-z_]\w*$", part) for part in parts):
        return None
    return ".".join(parts)


def _validate_saved_files(saved_files: list[str]) -> str:
    """Compila e importa archivos Python creados para detectar errores temprano."""
    root = Path(__file__).resolve().parent.parent
    reports: list[str] = []
    python_files = [path for path in saved_files if path.endswith(".py")]
    if not python_files:
        return "No hubo archivos Python nuevos/modificados para validar."

    for rel_path in python_files:
        full_path = root / rel_path
        reports.append(f"## Validando {rel_path}")
        compile_cmd = [sys.executable, "-m", "py_compile", str(full_path)]
        try:
            r = subprocess.run(
                compile_cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
            reports.append(f"py_compile exit={r.returncode}\n{r.stdout}{r.stderr}".strip())
        except Exception as exc:
            reports.append(f"py_compile error: {exc}")
            continue

        module_name = _module_name_from_path(rel_path)
        if module_name is None:
            continue
        import_cmd = [sys.executable, "-c", f"import {module_name}; print('import ok')"]
        try:
            r = subprocess.run(
                import_cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
            reports.append(f"import {module_name} exit={r.returncode}\n{r.stdout}{r.stderr}".strip())
        except Exception as exc:
            reports.append(f"import {module_name} error: {exc}")

    return "\n\n".join(reports)


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
    skills_x = enabled_skills_context()
    memory_x = shared_memory_context(query=str(feat.get("name") or feat.get("title") or ""), limit=10)
    improvements_x = self_improvement_context(limit=6)
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
            skills_excerpt=skills_x,
            memory_excerpt=memory_x,
            improvements_excerpt=improvements_x,
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
                memory_excerpt=memory_x,
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

        log("Aplicando cambios al sistema de archivos...")
        saved_files = _apply_code_blocks(impl_body)
        if saved_files:
            log(f"Archivos creados/modificados: {', '.join(saved_files)}")
        else:
            log("No se detectaron bloques de código para guardar.")

        _run_bash_blocks(impl_body, log)

        log("Validando archivos creados/modificados…")
        validation_output = _validate_saved_files(saved_files)
        debug_path = PROGRESS_DIR / f"debug_{slug}_attempt_{attempt + 1}.md"
        debug_path.write_text(
            f"# Debug · {feat.get('title')} (Intento {attempt + 1})\n\n"
            f"## Archivos\n{', '.join(saved_files) if saved_files else '(ninguno)'}\n\n"
            f"## Validación\n```text\n{validation_output}\n```\n",
            encoding="utf-8",
        )
        log(f"Debug guardado: {debug_path.name}")

        log("Ejecutando tests automatizados…")
        test_output = _run_tests()
        test_output = (
            "## Validación de archivos creados\n"
            f"{validation_output}\n\n"
            "## Pytest\n"
            f"{test_output}"
        )
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
        sync_skills()
        remember(
            "feature",
            slug,
            f"Feature id={fid} completada. {feat.get('title')}. Artefactos: {impl_path.name}, {review_path.name}.",
            tags=["done", "feature", slug],
            confidence=1.0,
        )
        for saved_file in (saved_files if "saved_files" in locals() else []):
            if saved_file.startswith("skills/") and saved_file.endswith(".py"):
                skill_name = Path(saved_file).stem
                record_skill_usage(
                    skill_name,
                    str(feat.get("title") or slug),
                    f"Skill creada o actualizada por la feature {fid}; revisar `skills/{skill_name}.md` para uso.",
                    success=True,
                    outcome="feature aprobada",
                )
        msg = f"Feature {fid} marcada done. Artefactos: {impl_path.name}, {review_path.name}"
        _append_history_line(
            f"- **{stamp_summary()}** feature `{slug}` (id={fid}) → **DONE**. "
            f"Ver `{impl_path.name}` y `{review_path.name}`."
        )
    elif verdict is False:
        set_feature_status(data, fid, "pending")
        remember(
            "feature_failure",
            f"{slug}_last_failure",
            f"Feature id={fid} falló revisión. Revisión: {review_path.name}.",
            tags=["fail", "feature", slug],
            confidence=0.8,
        )
        add_self_improvement(
            f"feature:{slug}",
            "Revisar patrón de fallo y mejorar prompts/tests si el mismo tipo de error se repite.",
            evidence=f"Revisor FAIL en {review_path.name}",
        )
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
        add_self_improvement(
            f"feature:{slug}",
            "Hacer más estricto el prompt del revisor para evitar veredictos ambiguos.",
            evidence="El revisor no inició con VERDICT: PASS/FAIL.",
        )
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

    new_items = None
    last_error = None
    
    for attempt in range(3):
        raw = invoke_llm(
            model,
            prompts.INIT_EXPAND_SYSTEM,
            prompts.initializer_user_message(user_goal=user_goal, max_existing_id=max_id),
            llm_chat=llm_chat,
            num_predict=num_predict,
            temperature=0.4,
            role_hint=f"initializer_attempt_{attempt + 1}",
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I).strip()
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            
        try:
            new_items = json.loads(cleaned)
            if not isinstance(new_items, list):
                raise ValueError("El inicializador no devolvió un array JSON.")
            break  # Parseo exitoso
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"Advertencia: Fallo al parsear JSON del inicializador (intento {attempt + 1}/3): {e}")
            new_items = None

    if new_items is None or not isinstance(new_items, list):
        raise ValueError(f"El inicializador falló tras 3 intentos. Último error: {last_error}\nTexto devuelto:\n{cleaned[:200]}...")

    added = 0
    next_id = max_id + 1
    for item in new_items:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item["origin"] = USER_TASK_ORIGIN
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
    remember(
        "user_goal",
        f"goal_{stamp_summary()}",
        f"Objetivo expandido en {added} feature(s): {user_goal}",
        tags=["goal", "initializer"],
        confidence=0.9,
    )
    _append_history_line(
        f"- **{stamp_summary()}** inicializador: +{added} features desde objetivo de usuario."
    )
    return added
