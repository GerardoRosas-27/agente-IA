"""Orquestación Líder → Implementador → Revisor; estado en disco (anti teléfono descompuesto)."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from harness.auto_training import record_user_session_training
from harness.events import emit_event
from harness.adr import create_adr_for_change
from harness.quality_gates import evaluate_quality_gates, gates_markdown
from harness.review_score import aggregate_review_score, review_score_markdown, score_review_axes
from harness.test_generator import (
    GeneratedTestSuite,
    generate_repro_tests,
    run_pytest_on_file,
)
from harness.verifier import VerifierVerdict, verify_cycle
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
from harness.repo_index import clear_repo_index_memory_cache, repo_context_for_goal
from harness.reflection import debugging_recovery_checklist
from harness.tool_learning import internal_execution_context, learned_tools_context
from harness.workflow_router import route_workflows_for_feature, workflow_context
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


@dataclass(frozen=True)
class ApplyResult:
    saved_files: list[str]
    patch_files: list[str]
    rejected: list[str]
    report: str

    @property
    def changed_files(self) -> list[str]:
        ordered = dict.fromkeys([*self.saved_files, *self.patch_files])
        return list(ordered)


@dataclass(frozen=True)
class CommandPolicyResult:
    allowed: bool
    reason: str = ""


BLOCKED_COMMAND_PATTERNS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/.+\s", re.IGNORECASE),
    re.compile(r"\brmdir\s+/(?:s|q)\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b.*\b-Recurse\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[A-Za-z]*f", re.IGNORECASE),
    re.compile(r"\bgit\s+checkout\s+--\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b|\breboot\b", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*(?:sh|bash|powershell)", re.IGNORECASE),
)


def _get_in_progress(features: list[dict[str, Any]]) -> dict[str, Any] | None:
    for f in features:
        if (
            isinstance(f, dict)
            and is_user_task(f)
            and f.get("status") == "in_progress"
        ):
            return f
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_repo_path(rel_path: str, root: Path) -> tuple[Path | None, str]:
    normalized = rel_path.replace("\\", "/").strip()
    if not normalized:
        return None, "ruta vacía"
    candidate = Path(normalized)
    if candidate.is_absolute() or normalized.startswith("../") or "/../" in normalized:
        return None, "ruta fuera del repositorio"
    if normalized in {".env", ".env.local"} or normalized.startswith(".env."):
        return None, "archivo de secretos protegido"
    full_path = (root / candidate).resolve()
    root_resolved = root.resolve()
    if full_path != root_resolved and root_resolved not in full_path.parents:
        return None, "ruta fuera del repositorio"
    return full_path, ""


def _extract_patch_files(patch_text: str, root: Path) -> tuple[list[str], list[str]]:
    files: list[str] = []
    rejected: list[str] = []
    for line in patch_text.splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        path_token = line[4:].strip().split("\t", 1)[0]
        if path_token == "/dev/null":
            continue
        if path_token.startswith(("a/", "b/")):
            path_token = path_token[2:]
        full_path, reason = _resolve_repo_path(path_token, root)
        if full_path is None:
            rejected.append(f"{path_token}: {reason}")
            continue
        files.append(full_path.relative_to(root).as_posix())
    return list(dict.fromkeys(files)), rejected


def _apply_patch_blocks(text: str, root: Path) -> tuple[list[str], list[str]]:
    patch_files: list[str] = []
    reports: list[str] = []
    pattern = r"```(?:patch|diff)\n(.*?)```"
    for index, match in enumerate(re.finditer(pattern, text, re.DOTALL | re.IGNORECASE), start=1):
        patch_text = match.group(1).strip("\r\n") + "\n"
        if not patch_text:
            continue
        candidate_files, rejected = _extract_patch_files(patch_text, root)
        if rejected:
            reports.append(f"Patch {index} rechazado por rutas inválidas: {'; '.join(rejected)}")
            continue
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn"],
            cwd=str(root),
            input=patch_text,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode != 0:
            reports.append(f"Patch {index} no aplica:\n{check.stdout}{check.stderr}".strip())
            continue
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=str(root),
            input=patch_text,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if applied.returncode != 0:
            reports.append(f"Patch {index} falló al aplicar:\n{applied.stdout}{applied.stderr}".strip())
            continue
        patch_files.extend(candidate_files)
        reports.append(f"Patch {index} aplicado: {', '.join(candidate_files) or '(sin archivos detectados)'}")
    return list(dict.fromkeys(patch_files)), reports


def _build_repo_context(feature: dict[str, Any], *, max_files: int = 12, max_chars: int = 6000) -> str:
    """Selecciona archivos probablemente relevantes sin indexar todo el repo."""
    query = " ".join(
        str(feature.get(key) or "")
        for key in ("name", "title", "description")
    )
    indexed = repo_context_for_goal(query, root=_repo_root(), limit=max_files)
    if indexed and not indexed.startswith("No se encontraron"):
        return indexed[:max_chars]

    root = _repo_root()
    query = query.lower()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9_áéíóúñ]+", query, flags=re.IGNORECASE)
        if len(token) >= 4
    }
    if not tokens:
        return "No se detectaron tokens suficientes para seleccionar contexto."

    candidates: list[tuple[int, Path]] = []
    ignored_parts = {".git", "__pycache__", ".pytest_cache"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".txt"}:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel.startswith(("logs/", "progress/history", "progress/current")):
            continue
        haystack = rel.lower()
        score = sum(3 for token in tokens if token in haystack)
        if score == 0:
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:1200].lower()
            except OSError:
                head = ""
            score = sum(1 for token in tokens if token in head)
        if score > 0:
            candidates.append((score, path))

    if not candidates:
        return "No se encontraron archivos claramente relacionados por nombre o contenido inicial."

    chunks: list[str] = []
    used_chars = 0
    for score, path in sorted(candidates, key=lambda item: (-item[0], item[1].as_posix()))[:max_files]:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        snippet = text[:900].strip()
        block = f"### {rel} (score={score})\n```text\n{snippet}\n```\n"
        if used_chars + len(block) > max_chars:
            break
        chunks.append(block)
        used_chars += len(block)
    return "\n".join(chunks) if chunks else "No se pudo leer contexto relevante."


def _apply_code_blocks(text: str, *, root: Path | None = None) -> ApplyResult:
    # Busca bloques tipo ```python:ruta/archivo.py o ```ruta/archivo.py.
    # También acepta bloques unified diff como ```patch para cambios incrementales.
    saved_files: list[str] = []
    rejected: list[str] = []
    root = root or _repo_root()
    patch_files, patch_reports = _apply_patch_blocks(text, root)
    
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
            full_path, reason = _resolve_repo_path(rel_path, root)
            if full_path is None:
                rejected.append(f"{rel_path}: {reason}")
                continue
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(code, encoding="utf-8")
            saved_files.append(full_path.relative_to(root).as_posix())
        except Exception as exc:
            rejected.append(f"{rel_path}: {exc}")

    report_lines = []
    if saved_files:
        report_lines.append(f"Archivos escritos: {', '.join(saved_files)}")
    if patch_reports:
        report_lines.extend(patch_reports)
    if rejected:
        report_lines.append("Bloques rechazados: " + "; ".join(rejected))
    if not report_lines:
        report_lines.append("No se aplicaron cambios de código.")
    return ApplyResult(
        saved_files=list(dict.fromkeys(saved_files)),
        patch_files=patch_files,
        rejected=rejected,
        report="\n".join(report_lines),
    )


def _check_command_policy(command: str) -> CommandPolicyResult:
    stripped = command.strip()
    if not stripped:
        return CommandPolicyResult(False, "comando vacío")
    for pattern in BLOCKED_COMMAND_PATTERNS:
        if pattern.search(stripped):
            return CommandPolicyResult(False, "comando bloqueado por política de seguridad")
    return CommandPolicyResult(True)


def _normalize_shell_command(command: str) -> list[str]:
    stripped = command.strip()
    parts = shlex.split(stripped, posix=sys.platform != "win32")
    if stripped.startswith("pytest "):
        return [sys.executable, "-m", "pytest", *parts[1:]]
    if stripped == "pytest":
        return [sys.executable, "-m", "pytest"]
    if stripped.startswith("python "):
        return [sys.executable, *parts[1:]]
    if stripped.startswith("pip install "):
        return [sys.executable, "-m", "pip", "install", *parts[2:]]
    return ["cmd", "/c", stripped] if sys.platform == "win32" else ["bash", "-c", stripped]


def _run_bash_blocks(text: str, log: Callable[[str], None]) -> tuple[bool, str]:
    import re
    root = Path(__file__).resolve().parent.parent
    pattern = r"```bash\n(.*?)```"
    matches = re.finditer(pattern, text, re.DOTALL)
    ok = True
    reports: list[str] = []
    for m in matches:
        raw_block = m.group(1).strip()
        if not raw_block:
            continue
        commands = [
            line.strip()
            for line in raw_block.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        for cmd_str in commands:
            policy = _check_command_policy(cmd_str)
            if not policy.allowed:
                ok = False
                report = f"$ {cmd_str}\nBLOQUEADO: {policy.reason}"
                reports.append(report)
                log(report)
                continue
            log(f"Ejecutando dependencias/comandos bash:\n{cmd_str}")
            try:
                cmd = _normalize_shell_command(cmd_str)
                r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=180)
                report = f"$ {cmd_str}\nExit code: {r.returncode}\n{r.stdout}\n{r.stderr}".strip()
                reports.append(report)
                log(f"Salida bash (Exit {r.returncode}):\n{r.stdout}\n{r.stderr}")
                if r.returncode != 0:
                    ok = False
            except Exception as e:
                ok = False
                reports.append(f"$ {cmd_str}\nError ejecutando bash: {e}")
                log(f"Error ejecutando bash: {e}")
    if not reports:
        return True, "No hubo bloques bash para ejecutar."
    return ok, "\n\n".join(reports)


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
    """Compila e importa archivos Python creados para detectar errores temprano.

    Se hace primero un `py_compile` agrupado (mucho más rápido que un subproceso
    por archivo). Si la compilación agrupada falla, se cae a uno-por-archivo para
    aislar qué archivo está roto sin sacrificar el modo común (todos compilan).
    """
    root = Path(__file__).resolve().parent.parent
    reports: list[str] = []
    python_files = [path for path in saved_files if path.endswith(".py")]
    if not python_files:
        return "No hubo archivos Python nuevos/modificados para validar."

    full_paths = [str(root / rel) for rel in python_files]
    grouped_cmd = [sys.executable, "-m", "py_compile", *full_paths]
    grouped_ok = False
    try:
        r = subprocess.run(
            grouped_cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=max(20, 5 * len(python_files)),
        )
        grouped_ok = r.returncode == 0
        files_label = ", ".join(python_files)
        reports.append(
            f"## py_compile (agrupado: {files_label})\n"
            f"exit={r.returncode}\n{r.stdout}{r.stderr}".strip()
        )
    except Exception as exc:
        reports.append(f"py_compile agrupado error: {exc}")

    if not grouped_ok:
        for rel_path in python_files:
            full_path = root / rel_path
            reports.append(f"## Validando {rel_path}")
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(full_path)],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                reports.append(f"py_compile exit={r.returncode}\n{r.stdout}{r.stderr}".strip())
            except Exception as exc:
                reports.append(f"py_compile error: {exc}")

    for rel_path in python_files:
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


def _related_test_paths(saved_files: list[str]) -> list[str]:
    related: list[str] = []
    for rel_path in saved_files:
        path = rel_path.replace("\\", "/")
        if path.startswith("tests/") and path.endswith(".py"):
            related.append(path)
            continue
        stem = Path(path).stem
        if not stem:
            continue
        for candidate in (
            f"tests/test_{stem}.py",
            f"tests/{stem}_test.py",
        ):
            if (_repo_root() / candidate).is_file():
                related.append(candidate)
    return list(dict.fromkeys(related))


def _run_tests(saved_files: list[str] | None = None) -> str:
    """Ejecuta los tests del proyecto y devuelve el output."""
    root = _repo_root()
    reports: list[str] = []
    related = _related_test_paths(saved_files or [])
    commands = []
    if related:
        commands.append(("tests relacionados", [sys.executable, "-m", "pytest", *related, "-q", "--tb=short"]))
    commands.append(("suite completa", [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"]))
    overall_ok = True
    for label, cmd in commands:
        try:
            r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=90)
            out = (r.stdout + "\n" + r.stderr).strip()
            reports.append(f"## {label}\nComando: {' '.join(cmd)}\nExit code: {r.returncode}\n{out}")
            if r.returncode != 0:
                overall_ok = False
        except Exception as e:
            overall_ok = False
            reports.append(f"## {label}\nError ejecutando tests: {e}")
    header = "Exit code: 0" if overall_ok else "Exit code: 1"
    return header + "\n" + "\n\n".join(reports)


def run_one_feature_cycle(
    *,
    model: str,
    llm_chat: Callable[..., Any] | None = None,
    num_predict_leader: int = 1200,
    num_predict_worker: int = 2800,
    max_retries: int = 2,
    on_log: Callable[[str], None] | None = None,
    enable_brt: bool = False,
    enable_verifier: bool = False,
    enable_replan: bool = False,
    enable_adversarial_review: bool = False,
) -> HarnessCycleResult | None:
    """
    Ejecuta un ciclo completo sobre la feature `in_progress`, o reclama la siguiente `pending`.

    Flags opt-in (todos False por defecto para mantener compatibilidad):
      - `enable_brt`: genera Bug Reproduction Tests desde la feature antes de
        pedir el patch (Otter / BRT Agent). Los BRTs se ejecutan al final como
        evidencia adicional.
      - `enable_verifier`: usa `harness.verifier.verify_cycle` como filtro
        determinista por encima del veredicto del Reviewer LLM. Si el verifier
        encuentra evidencia contraria, sobreescribe a FAIL.
      - `enable_replan`: si el revisor da FAIL, antes de reintentar pide al
        Líder un plan nuevo basado en el feedback (en lugar de reusar el plan
        original). Inspirado en AdaCoder / CodePlan.
      - `enable_adversarial_review`: tras el reviewer principal, llama a un
        segundo reviewer "red team" cuyo prompt lo orienta a buscar fallos.
        Si discrepan (uno PASS, otro FAIL), el ciclo se marca FAIL.
        Inspirado en MAR (Multi-Agent Reflexion, arXiv:2512.20845).
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
    feature_query = str(feat.get("name") or feat.get("title") or "")
    workflow_recommendations = route_workflows_for_feature(feat)
    workflows_x = workflow_context(workflow_recommendations)
    internal_x = internal_execution_context(feature_query)
    skills_x = (
        enabled_skills_context()
        + "\n\n--- Workflows recomendados ---\n"
        + workflows_x
        + "\n\n--- Recomendador aprendido de herramientas ---\n"
        + learned_tools_context(feature_query)
    )
    memory_x = shared_memory_context(query=feature_query, limit=10)
    improvements_x = self_improvement_context(limit=6)
    repo_context_x = _build_repo_context(feat)
    arch_x = _read_head(DOCS_DIR / "architecture.md", 3500)
    conv_x = _read_head(DOCS_DIR / "conventions.md", 2500)
    ver_x = _read_head(DOCS_DIR / "verification.md", 2500)

    def _ask_leader(*, replan_feedback: str = "") -> str:
        user_msg = prompts.leader_user_message(
            feature_block=fblock,
            agents_excerpt=agents_x,
            checkpoints_excerpt=cp_x,
            skills_excerpt=skills_x,
            memory_excerpt=memory_x,
            improvements_excerpt=improvements_x,
            internal_context=internal_x,
        )
        if replan_feedback:
            user_msg += (
                "\n\n--- Feedback de intento anterior ---\n"
                f"{replan_feedback}\n\n"
                "Replanifica desde cero: cambia archivos candidatos o estrategia "
                "si el plan anterior no llevó a un PASS."
            )
        return invoke_llm(
            model,
            prompts.LEADER_SYSTEM,
            user_msg,
            llm_chat=llm_chat,
            num_predict=num_predict_leader,
            role_hint="leader_replan" if replan_feedback else "leader",
        )

    log("Líder: planificando…")
    emit_event("cycle.started", role="leader", feature_id=fid, feature_name=slug)
    leader_out = _ask_leader()
    _append_markdown(PROGRESS_DIR / "current.md", f"Líder · feature {fid}", leader_out)
    emit_event("leader.planned", feature_id=fid, feature_name=slug, length=len(leader_out))

    brt_suite: GeneratedTestSuite | None = None
    if enable_brt:
        log("Generando Bug Reproduction Tests desde la feature…")
        try:
            brt_suite = generate_repro_tests(
                feat,
                invoke_llm=invoke_llm,
                model=model,
                repo_context=repo_context_x,
                root=_repo_root(),
                run_after_generation=True,
            )
            log(
                f"BRTs en {brt_suite.test_path.name}; estado inicial: "
                f"{brt_suite.initial_status}"
            )
        except Exception as exc:
            log(f"BRT generation falló (continuamos sin BRT): {exc}")
            brt_suite = None

    verdict = None
    previous_feedback = None
    impl_path = PROGRESS_DIR / f"impl_{slug}.md"
    review_path = PROGRESS_DIR / f"review_{slug}.md"
    verify_path = PROGRESS_DIR / f"verify_{slug}.md"
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
                internal_context=internal_x,
                repo_context=repo_context_x,
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
        apply_result = _apply_code_blocks(impl_body)
        if apply_result.changed_files:
            clear_repo_index_memory_cache()
        saved_files = apply_result.changed_files
        if saved_files:
            log(f"Archivos creados/modificados: {', '.join(saved_files)}")
        else:
            log("No se detectaron bloques de código para guardar.")
        if apply_result.rejected:
            log("Bloques rechazados: " + "; ".join(apply_result.rejected))

        bash_ok, bash_output = _run_bash_blocks(impl_body, log)

        log("Validando archivos creados/modificados…")
        validation_output = _validate_saved_files(saved_files)
        change_evidence = (
            "## Aplicación de cambios\n"
            f"{apply_result.report}\n\n"
            "## Archivos modificados por el harness\n"
            f"{', '.join(saved_files) if saved_files else '(ninguno)'}\n\n"
            "## Bloques rechazados\n"
            f"{'; '.join(apply_result.rejected) if apply_result.rejected else '(ninguno)'}"
        )
        debug_path = PROGRESS_DIR / f"debug_{slug}_attempt_{attempt + 1}.md"
        debug_path.write_text(
            f"# Debug · {feat.get('title')} (Intento {attempt + 1})\n\n"
            f"{change_evidence}\n\n"
            f"## Validación\n```text\n{validation_output}\n```\n",
            encoding="utf-8",
        )
        log(f"Debug guardado: {debug_path.name}")

        log("Ejecutando tests automatizados…")
        test_output = _run_tests(saved_files)
        test_output = (
            "## Comandos bash del implementador\n"
            f"{bash_output}\n\n"
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
                change_evidence=change_evidence,
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
        workflow_names = [item.name for item in workflow_recommendations]
        quality_gates = evaluate_quality_gates(
            workflows=workflow_names,
            changed_files=saved_files,
            implementation_text=impl_body,
            review_text=review_body,
            test_output=test_output,
            validation_output=validation_output,
        )
        failed_gates = [gate.name for gate in quality_gates if not gate.passed]
        hard_gate_fail = any(gate.hard and not gate.passed for gate in quality_gates)
        review_scores = score_review_axes(
            review_text=review_body,
            test_output=test_output,
            changed_files=saved_files,
            quality_gate_failures=failed_gates,
        )
        review_score_passed, review_score = aggregate_review_score(review_scores)
        review_body += "\n\n" + gates_markdown(quality_gates)
        review_body += "\n\n" + review_score_markdown(review_scores)
        review_path.write_text(
            f"# Revisión · {feat.get('title')} (Intento {attempt + 1})\n\n{review_body}\n\n## Output de Tests\n```text\n{test_output}\n```\n",
            encoding="utf-8",
        )
        emit_event(
            "quality_gates.evaluated",
            feature_id=fid,
            feature_name=slug,
            attempt=attempt + 1,
            failed=failed_gates,
            review_score=review_score,
        )
        if verdict is True and (hard_gate_fail or not review_score_passed):
            verdict = False
            review_body += (
                "\n\nVEREDICTO SOBRESCRITO POR QUALITY GATES: "
                f"failed={failed_gates or '(ninguno)'} review_score={review_score:.3f}."
            )
            review_path.write_text(
                f"# Revisión · {feat.get('title')} (Intento {attempt + 1})\n\n{review_body}\n\n## Output de Tests\n```text\n{test_output}\n```\n",
                encoding="utf-8",
            )
        if verdict is True and (not bash_ok or apply_result.rejected):
            verdict = False
            review_body += (
                "\n\nVEREDICTO SOBRESCRITO POR HARNESS: fallaron comandos bash "
                "o se rechazaron bloques de cambio."
            )

        if enable_adversarial_review and verdict is True:
            log("Adversarial reviewer (red team): buscando fallos…")
            try:
                adversarial_body = invoke_llm(
                    model,
                    prompts.ADVERSARIAL_REVIEWER_SYSTEM,
                    prompts.adversarial_reviewer_user_message(
                        feature_block=fblock,
                        impl_report=impl_body,
                        test_output=test_output,
                        primary_review=review_body,
                        change_evidence=change_evidence,
                    ),
                    llm_chat=llm_chat,
                    num_predict=num_predict_leader,
                    role_hint=f"adversarial_reviewer_attempt_{attempt + 1}",
                )
            except Exception as exc:
                log(f"Adversarial reviewer falló (continuamos sin él): {exc}")
                adversarial_body = ""
            if adversarial_body:
                adversarial_path = PROGRESS_DIR / f"red_review_{slug}.md"
                adversarial_path.write_text(
                    f"# Red-team Review · {feat.get('title')} (Intento {attempt + 1})\n\n"
                    f"{adversarial_body}\n",
                    encoding="utf-8",
                )
                adversarial_verdict = parse_verdict(adversarial_body)
                emit_event(
                    "adversarial_reviewer.verdict",
                    role="adversarial_reviewer",
                    feature_id=fid,
                    feature_name=slug,
                    attempt=attempt + 1,
                    outcome="pass" if adversarial_verdict is True else (
                        "fail" if adversarial_verdict is False else "ambiguous"
                    ),
                )
                # Si el red team encuentra fallos (FAIL) o es ambiguo, anulamos
                # el PASS del reviewer principal: dos veredictos en desacuerdo
                # = no hay consenso.
                if adversarial_verdict is not True:
                    verdict = False
                    review_body += (
                        f"\n\nVEREDICTO SOBRESCRITO POR ADVERSARIAL REVIEWER. "
                        f"Ver `{adversarial_path.name}`."
                    )

        if enable_verifier:
            brt_runs = []
            if brt_suite is not None and brt_suite.test_path.is_file():
                log("Verifier: corriendo BRTs sobre HEAD post-implementación…")
                try:
                    brt_runs = [run_pytest_on_file(brt_suite.test_path, root=_repo_root())]
                except Exception as exc:
                    log(f"BRT post-run falló: {exc}")
            try:
                determ_verdict: VerifierVerdict = verify_cycle(
                    test_output=test_output,
                    validation_output=validation_output,
                    changed_files=saved_files,
                    rejected=apply_result.rejected,
                    bash_ok=bash_ok,
                    bug_reproduction_runs=brt_runs,
                    quality_gates=quality_gates if "quality_gates" in locals() else (),
                    root=_repo_root(),
                )
            except Exception as exc:
                log(f"Verifier determinista falló (continuamos con LLM-only): {exc}")
                determ_verdict = None
            if determ_verdict is not None:
                verify_path.write_text(
                    f"# Verifier · {feat.get('title')} (Intento {attempt + 1})\n\n"
                    f"{determ_verdict.report_markdown}\n",
                    encoding="utf-8",
                )
                emit_event(
                    "verifier.verdict",
                    role="verifier",
                    feature_id=fid,
                    feature_name=slug,
                    attempt=attempt + 1,
                    outcome="pass" if determ_verdict.passed else "fail",
                    score=determ_verdict.score,
                )
                if verdict is True and not determ_verdict.passed:
                    verdict = False
                    review_body += (
                        "\n\nVEREDICTO SOBRESCRITO POR VERIFIER DETERMINISTA: "
                        f"score={determ_verdict.score:.3f}. Ver `{verify_path.name}`."
                    )
                    emit_event(
                        "verifier.override",
                        role="verifier",
                        feature_id=fid,
                        feature_name=slug,
                        attempt=attempt + 1,
                        score=determ_verdict.score,
                    )

        if verdict is True:
            break
        else:
            previous_feedback = review_body
            if attempt < max_retries:
                log("El revisor rechazó la implementación. Reintentando…")
                if enable_replan:
                    log("Replan: pidiendo al Líder un plan nuevo basado en el feedback…")
                    try:
                        leader_out = _ask_leader(replan_feedback=review_body[-3000:])
                        _append_markdown(
                            PROGRESS_DIR / "current.md",
                            f"Líder (replan) · feature {fid}",
                            leader_out,
                        )
                    except Exception as exc:
                        log(f"Replan falló (sigo con plan original): {exc}")

    data = load_feature_list(FEATURE_LIST_PATH)
    if verdict is True:
        adr_result = create_adr_for_change(
            title=str(feat.get("title") or slug),
            changed_files=saved_files if "saved_files" in locals() else [],
            implementation_text=impl_body,
            docs_dir=DOCS_DIR,
        )
        if adr_result.created:
            emit_event(
                "adr.created",
                feature_id=fid,
                feature_name=slug,
                path=adr_result.path,
            )
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
        adr_note = f", ADR: {adr_result.path.name}" if adr_result.created else ""
        msg = f"Feature {fid} marcada done. Artefactos: {impl_path.name}, {review_path.name}{adr_note}"
        _append_history_line(
            f"- **{stamp_summary()}** feature `{slug}` (id={fid}) → **DONE**. "
            f"Ver `{impl_path.name}` y `{review_path.name}`."
        )
    elif verdict is False:
        recovery = "\n".join(f"- {item}" for item in debugging_recovery_checklist(str(feat.get("title") or slug), review_body))
        _append_markdown(
            PROGRESS_DIR / f"debugging_recovery_{slug}.md",
            "Checklist de recuperación",
            recovery,
        )
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
    emit_event(
        "cycle.finished",
        feature_id=fid,
        feature_name=slug,
        outcome=("pass" if verdict is True else "fail" if verdict is False else "ambiguous"),
    )
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

    internal_x = internal_execution_context(user_goal)
    remember(
        "preflight_neural_context",
        f"preflight_{stamp_summary()}",
        internal_x,
        tags=["preflight", "neural_router", "user_goal"],
        confidence=0.96,
    )

    new_items = None
    last_error = None
    
    for attempt in range(3):
        raw = invoke_llm(
            model,
            prompts.INIT_EXPAND_SYSTEM,
            prompts.initializer_user_message(
                user_goal=user_goal,
                max_existing_id=max_id,
                internal_context=internal_x,
            ),
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
    record_user_session_training(
        user_goal,
        outcome=f"expanded_into_{added}_features",
    )
    _append_history_line(
        f"- **{stamp_summary()}** inicializador: +{added} features desde objetivo de usuario."
    )
    return added
