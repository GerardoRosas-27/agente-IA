"""
Enjambre por OBJETIVO: Entiende → Planifica → Internet → Discuten → Ejecutan
→ Prueban → PruebaPython → Revisor. Memoria compartida (SharedFlyMemory)
y buffer de ciclo que entrena una red auxiliar al cerrar cada ciclo.
"""
from __future__ import annotations

import html
import os
import re
import json
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import torch

from multi_agent_orchestrator import _ollama, syntax_score, text_hash_embed
from node_probe_tool import parse_node_probe_request, run_node_probe_script
from plastic_swarm_state import (
    BufferPlasticNet,
    CycleBuffer,
    SharedExperienceReplay,
    WorkingMemoryPool,
)
from terminal_tool import parse_terminal_command_request, run_terminal_command
from tool_library import ToolLibrary, parse_tool_memory_request
from unified_fly_memory import SharedFlyMemory

_TRUE_ENV = {"1", "true", "yes", "si", "sí", "on", "y"}
_FALSE_ENV = {"0", "false", "no", "off", "n"}


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_ENV:
        return True
    if raw in _FALSE_ENV:
        return False
    return default


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, *, lo: float, hi: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _strip_json_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|python)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


def _ctx_snip(memory: SharedFlyMemory, mem_cur: torch.Tensor, aid: int, n: int = 12) -> str:
    with torch.no_grad():
        v = memory.read_context(mem_cur, aid)[:n].cpu().tolist()
    return ", ".join(f"{x:+.2f}" for x in v)


def parse_understanding(text: str, raw_fallback: str) -> tuple[str, list[str]]:
    obj = ""
    crit: list[str] = []
    for line in text.splitlines():
        u = line.upper().strip()
        if u.startswith("OBJETIVO_CLARO:") or u.startswith("OBJETIVO:"):
            obj = line.split(":", 1)[-1].strip()
        elif "CRITERIO" in u and ":" in line:
            crit.append(line.split(":", 1)[-1].strip())
    if not obj:
        obj = raw_fallback.strip().replace("\n", " ")[:400]
    if not crit:
        crit = [
            "La respuesta final resume cómo se satisface el objetivo.",
            "No quedan ambigüedades críticas sin resolver.",
        ]
    return obj, crit[:6]


def parse_final_verdict(text: str) -> tuple[bool, str, str, str]:
    """
    (alcanzado, motivo, respuesta_final, retroalimentacion_para_siguiente_ciclo).
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            reached_raw = data.get("objetivo_alcanzado", data.get("alcanzado", False))
            reached = bool(reached_raw)
            if isinstance(reached_raw, str):
                reached = reached_raw.strip().lower() in {"si", "sí", "yes", "true", "1"}
            motivo = str(data.get("motivo", "") or "").strip()[:800]
            resp = str(data.get("respuesta_final", "") or data.get("respuesta", "") or "").strip()
            retro = str(data.get("retroalimentacion", "") or data.get("retro", "") or "").strip()[:800]
            if resp:
                return reached, motivo, resp, retro
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    reached = False
    if re.search(
        r"OBJETIVO[_\s]*ALCANZADO\s*:\s*(NO|FALSE|0)\b",
        text,
        re.I | re.MULTILINE,
    ):
        reached = False
    elif re.search(
        r"OBJETIVO[_\s]*ALCANZADO\s*:\s*(SI|SÍ|YES|TRUE|1)\b",
        text,
        re.I | re.MULTILINE,
    ):
        reached = True

    motivo = ""
    m = re.search(r"MOTIVO\s*:\s*(.+?)(?=\n\s*(RESPUESTA|RETRO)|\Z)", text, re.I | re.S)
    if m:
        motivo = m.group(1).strip()[:800]

    retro = ""
    mr = re.search(
        r"RETROALIMENTACION\s*:\s*(.+?)(?=\n\s*RESPUESTA|\Z)", text, re.I | re.S
    )
    if mr:
        retro = mr.group(1).strip()[:800]
    elif not reached and motivo:
        retro = motivo

    resp = text.strip()
    m2 = re.search(r"RESPUESTA[_\s]*FINAL\s*:\s*(.+)\Z", text, re.I | re.S)
    if m2:
        resp = m2.group(1).strip()
    return reached, motivo, resp, retro


def web_research_agent_turn(
    query: str,
    *,
    max_results: int = 4,
    timeout: float = 4.0,
) -> str:
    """
    Busca contexto público sin depender de claves API. Si no hay conexión o
    no hay resultados útiles, devuelve cadena vacía para que el agente pase turno.
    """
    q = query.strip().replace("\n", " ")
    if not q:
        return ""

    max_results = max(1, min(8, int(max_results)))
    timeout = max(1.0, min(20.0, float(timeout)))
    results: list[str] = []

    try:
        from ddgs import DDGS  # type: ignore

        with DDGS(timeout=timeout) as ddgs:
            for item in ddgs.text(q[:240], max_results=max_results):
                title = str(item.get("title") or "Resultado web").strip()
                body = str(item.get("body") or "").strip()
                href = str(item.get("href") or "").strip()
                if not body and not href:
                    continue
                line = f"- {title}: {body[:560]}"
                if href:
                    line = f"{line} ({href})"
                results.append(line[:760])
                if len(results) >= max_results:
                    break
    except Exception:
        results = []

    ddg_url = (
        "https://api.duckduckgo.com/?"
        + urllib.parse.urlencode(
            {
                "q": q[:240],
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
        )
    )
    try:
        req = urllib.request.Request(
            ddg_url,
            headers={"User-Agent": "PlasticSwarm/1.0 (+learning-cycle)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(240_000).decode("utf-8", errors="ignore"))
        abstract = str(data.get("AbstractText") or "").strip()
        source = str(data.get("AbstractSource") or "DuckDuckGo").strip()
        if abstract:
            results.append(f"- {source}: {abstract[:650]}")
        for item in data.get("RelatedTopics") or []:
            if len(results) >= max_results:
                break
            if "Topics" in item:
                topics = item.get("Topics") or []
            else:
                topics = [item]
            for topic in topics:
                text = str(topic.get("Text") or "").strip()
                if text:
                    results.append(f"- DuckDuckGo: {text[:650]}")
                if len(results) >= max_results:
                    break
    except Exception:
        results = []

    if len(results) < max_results:
        wiki_url = (
            "https://en.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "opensearch",
                    "search": q[:120],
                    "limit": max_results,
                    "namespace": "0",
                    "format": "json",
                }
            )
        )
        try:
            req = urllib.request.Request(
                wiki_url,
                headers={"User-Agent": "PlasticSwarm/1.0 (+learning-cycle)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read(160_000).decode("utf-8", errors="ignore"))
            titles = data[1] if len(data) > 1 else []
            snippets = data[2] if len(data) > 2 else []
            urls = data[3] if len(data) > 3 else []
            for title, snippet, url in zip(titles, snippets, urls):
                if len(results) >= max_results:
                    break
                clean = html.unescape(str(snippet or title)).strip()
                link = str(url or "").strip()
                if clean:
                    results.append(f"- Wikipedia: {clean[:560]} ({link})")
        except Exception:
            pass

    if not results:
        return ""
    return "Aporte web para el ciclo de aprendizaje:\n" + "\n".join(results[:max_results])


def parse_python_probe_request(text: str) -> tuple[bool, str, str]:
    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, "El agente no solicitó un script ejecutable.", ""
    needed_raw = data.get("necesario", data.get("needed", False))
    needed = bool(needed_raw)
    if isinstance(needed_raw, str):
        needed = needed_raw.strip().lower() in _TRUE_ENV
    reason = str(data.get("motivo", data.get("reason", "")) or "").strip()[:500]
    script = str(data.get("script", "") or "").strip()
    return needed, reason, script


_PYTHON_PROBE_RELEVANCE_RE = re.compile(
    r"\b("
    r"python|codigo|código|script|programa|funcion|función|clase|api|bug|error|"
    r"test|prueba|pytest|unittest|validar|verificar|calculo|cálculo|formula|fórmula|"
    r"algoritmo|json|csv|regex|parse|simulacion|simulación|resultado|invariante"
    r")\b",
    re.I,
)

_NODE_PROBE_RELEVANCE_RE = re.compile(
    r"\b("
    r"node|nodejs|node\.js|javascript|js|typescript|ts|npm|package\.json|"
    r"frontend|backend js|express|react|vite|next|json|regex|webhook|api|"
    r"validar|verificar|parse|transformar|uuid|crypto|hash|base64"
    r")\b",
    re.I,
)


def is_python_probe_relevant(*parts: str) -> bool:
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return False
    return bool(_PYTHON_PROBE_RELEVANCE_RE.search(text))


def is_node_probe_relevant(*parts: str) -> bool:
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return False
    return bool(_NODE_PROBE_RELEVANCE_RE.search(text))


_PROBE_BLOCKLIST = (
    r"\bimport\s+(os|subprocess|socket|shutil|pathlib|requests|urllib|http|ftplib|ssl)\b",
    r"\bfrom\s+(os|subprocess|socket|shutil|pathlib|requests|urllib|http|ftplib|ssl)\b",
    r"\b(open|eval|exec|compile|input|__import__)\s*\(",
    r"\b(exit|quit)\s*\(",
)


def run_python_probe_script(
    script: str,
    *,
    timeout: float = 5.0,
    max_chars: int = 2500,
) -> str:
    code = script.strip()
    max_chars = max(200, min(8000, int(max_chars)))
    if not code:
        return "No se ejecutó script: el agente no entregó código."
    if len(code) > max_chars:
        return f"No se ejecutó script: excede el límite configurado ({max_chars} caracteres)."
    for pattern in _PROBE_BLOCKLIST:
        if re.search(pattern, code):
            return "No se ejecutó script: contiene operaciones bloqueadas para pruebas pequeñas."

    timeout = max(1.0, min(20.0, float(timeout)))
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH"}
    }
    child_env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="plastic_probe_") as tmp:
        script_path = Path(tmp) / "probe.py"
        script_path.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Script cancelado por timeout ({timeout:.1f}s)."
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    out = [
        f"exit_code={proc.returncode}",
        f"stdout:\n{stdout[:1200] or '(vacío)'}",
    ]
    if stderr:
        out.append(f"stderr:\n{stderr[:1000]}")
    return "\n".join(out)


def _role_budget(base: int, role: str, *, cycle: int, retry: bool = False) -> int:
    """
    Presupuesto adaptativo: roles de coordinación usan menos tokens; el revisor
    puede crecer cuando hay retro previa o ciclos posteriores.
    """
    base = max(64, int(base))
    factors = {
        "Entiende": 0.85,
        "Planifica": 1.05,
        "Discute": 0.70,
        "Ejecuta": 0.95,
        "Prueba": 0.75,
        "Revisor": 1.15,
    }
    key = next((k for k in factors if role.startswith(k)), role)
    factor = factors.get(key, 0.85)
    if cycle > 0 and key in {"Planifica", "Revisor"}:
        factor += 0.15
    if retry:
        factor += 0.35
    upper = 1800 if key == "Revisor" else 900
    return max(48, min(upper, int(base * factor)))


def _mem_step(
    memory: SharedFlyMemory,
    mem_cur: torch.Tensor,
    slot: int,
    text: str,
) -> torch.Tensor:
    emb = text_hash_embed(text, memory.msg_dim, memory.mem.device)
    mem_next, _ = memory.write_step(mem_cur, slot % max(1, memory.n_agents_max), emb)
    return mem_next


def _memory_learn(
    memory: SharedFlyMemory,
    mem_cur: torch.Tensor,
    corpus: str,
    final_answer: str,
) -> tuple[float, dict[str, float]]:
    device = memory.mem.device
    uemb = text_hash_embed(final_answer, memory.msg_dim, device)
    syn = float(syntax_score(corpus + "\n" + final_answer))
    signal = 0.35 + 0.65 * syn
    loss, stats = memory.free_energy_loss(mem_cur, uemb, signal=signal)
    memory.learn(loss)
    return float(loss.detach().cpu().item()), stats


def run_objective_pipeline(
    raw_user_objective: str,
    memory: SharedFlyMemory,
    model: str,
    llm_chat: Callable[..., object],
    *,
    cycle_buffer: CycleBuffer,
    plastic_aux: BufferPlasticNet | None = None,
    experience_replay: SharedExperienceReplay | None = None,
    weights_ready: threading.Event | None = None,
    num_predict: int = 180,
    num_predict_final: int = 900,
    max_cycles: int = 4,
    n_discuss: int = 2,
    n_execute: int = 2,
    n_test: int = 1,
    internet_agent_enabled: bool | None = None,
    python_test_agent_enabled: bool | None = None,
    node_test_agent_enabled: bool | None = None,
    terminal_agent_enabled: bool | None = None,
    internet_search_fn: Callable[..., str] | None = None,
    terminal_run_fn: Callable[..., object] | None = None,
    tool_library: ToolLibrary | None = None,
    on_log: Callable[[str, str], None] | None = None,
    on_cycle_checkpoint: Callable[[], None] | None = None,
) -> tuple[str, int, float, bool]:
    """
    weights_ready: si se pasa, se espera al inicio (carga de pesos en segundo plano).
    """
    log = on_log or (lambda _r, _c: None)
    internet_enabled = (
        _env_bool("INTERNET_AGENT_ENABLED", True)
        if internet_agent_enabled is None
        else bool(internet_agent_enabled)
    )
    python_probe_enabled = (
        _env_bool("PYTHON_TEST_AGENT_ENABLED", True)
        if python_test_agent_enabled is None
        else bool(python_test_agent_enabled)
    )
    node_probe_enabled = (
        _env_bool("NODE_TEST_AGENT_ENABLED", True)
        if node_test_agent_enabled is None
        else bool(node_test_agent_enabled)
    )
    terminal_enabled = (
        _env_bool("TERMINAL_AGENT_ENABLED", False)
        if terminal_agent_enabled is None
        else bool(terminal_agent_enabled)
    )
    web_max_results = _env_int("INTERNET_AGENT_MAX_RESULTS", 4, lo=1, hi=8)
    web_timeout = _env_float("INTERNET_AGENT_TIMEOUT", 4.0, lo=1.0, hi=20.0)
    probe_timeout = _env_float("PYTHON_TEST_AGENT_TIMEOUT", 5.0, lo=1.0, hi=20.0)
    probe_max_chars = _env_int("PYTHON_TEST_AGENT_MAX_CHARS", 2500, lo=200, hi=8000)
    probe_skip_non_code = _env_bool("PYTHON_TEST_AGENT_SKIP_NON_CODE", True)
    node_probe_timeout = _env_float("NODE_TEST_AGENT_TIMEOUT", 5.0, lo=1.0, hi=20.0)
    node_probe_max_chars = _env_int("NODE_TEST_AGENT_MAX_CHARS", 3000, lo=200, hi=10000)
    node_probe_skip_non_code = _env_bool("NODE_TEST_AGENT_SKIP_NON_CODE", True)
    terminal_timeout = _env_float("TERMINAL_AGENT_TIMEOUT", 10.0, lo=1.0, hi=60.0)
    terminal_max_output = _env_int("TERMINAL_AGENT_MAX_OUTPUT_CHARS", 4000, lo=500, hi=12000)
    tool_manager_enabled = _env_bool("TOOL_LIBRARY_AGENT_ENABLED", True)
    tool_library_max_results = _env_int("TOOL_LIBRARY_MAX_RESULTS", 4, lo=1, hi=8)
    auxiliary_fail_open = _env_bool("AUXILIARY_AGENTS_FAIL_OPEN", True)
    search_fn = internet_search_fn or web_research_agent_turn
    run_terminal_fn = terminal_run_fn or run_terminal_command
    tools = tool_library if tool_library is not None else (ToolLibrary() if tool_manager_enabled else None)

    def logb(role: str, content: str) -> None:
        cycle_buffer.add(role, content)
        log(role, content)

    def replay_ctx(query: str, agent_key: str, max_chars: int = 1200) -> str:
        if experience_replay is None:
            return ""
        try:
            ctx = experience_replay.retrieval_context(
                query,
                agent_key=agent_key,
                limit=3,
                max_chars=max_chars,
            )
        except Exception as exc:
            log("Replay", f"No se pudo recuperar memoria compartida: {exc}")
            return ""
        if not ctx:
            return ""
        return "\n--- Memoria compartida recuperada ---\n" + ctx + "\n"

    def work_ctx(pool: WorkingMemoryPool, max_chars: int = 900) -> str:
        ctx = pool.context(limit=5)
        if not ctx:
            return ""
        return "\n--- Pool temporal del ciclo ---\n" + ctx[:max_chars] + "\n"

    if weights_ready is not None:
        weights_ready.wait(timeout=180)

    log(
        "Sistema",
        f"LLM: modelo «{model}» (LM Studio). Cada rol escribe abajo; al final "
        "«Respuesta final» resume para el usuario. Si el pipeline falla, comprobar "
        "el servidor y .env (LLM_API_BASE_URL, LLM_MODEL).",
    )

    raw = raw_user_objective.strip()[:8000]
    slot = 0

    def step_slot() -> int:
        nonlocal slot
        s = slot
        slot += 1
        return s

    last_loss = 0.0
    reached = False
    cycles_used = 0
    motivo_prev = ""
    retro_prev = ""

    for cyc in range(max(1, int(max_cycles))):
        mem_cur = memory.mem
        cycle_events: list[tuple[str, str]] = []
        working_pool = WorkingMemoryPool()
        cycles_used = cyc + 1
        logb("Ciclo", f"═══ Ciclo {cyc + 1} / {max_cycles} ═══")

        log("Sistema", "── Intérprete (formaliza objetivo y criterios) ──")
        sys_e = (
            "Eres el agente INTERPRETE. El usuario define un OBJETIVO (puede ser un objeto "
            "textual: producto, problema, meta).\n"
            "Formalízalo; no repitas literal todo su texto como única salida.\n"
            "Formato:\nOBJETIVO_CLARO: ...\nCRITERIO_1: ...\nCRITERIO_2: ...\n"
        )
        user_e = f"Entrada bruta (solo para ti):\n{raw}"
        out_e = _ollama(
            llm_chat,
            model,
            sys_e,
            user_e,
            num_predict=_role_budget(num_predict + 40, "Entiende", cycle=cyc),
        )
        logb("Entiende", out_e)
        cycle_events.append(("Entiende", out_e))
        working_pool.add("Entiende", out_e, salience=0.70)
        mem_cur = _mem_step(memory, mem_cur, step_slot(), out_e)
        obj_claro, criterios = parse_understanding(out_e, raw)
        crit_txt = "\n".join(f"- {x}" for x in criterios)

        tool_ctx = ""
        if tool_manager_enabled and tools is not None:
            log("Sistema", "── GestorHerramientas (recupera herramientas previas) ──")
            try:
                tool_ctx = tools.context(
                    f"{obj_claro}\n{crit_txt}\n{raw[:1200]}",
                    limit=tool_library_max_results,
                    max_chars=1800,
                )
            except Exception as exc:
                tool_ctx = ""
                log("GestorHerramientas", f"No se pudo consultar biblioteca: {exc}")
            if tool_ctx:
                logb("GestorHerramientas", tool_ctx)
                cycle_events.append(("GestorHerramientas", tool_ctx))
                working_pool.add("GestorHerramientas", tool_ctx, salience=0.90)
                mem_cur = _mem_step(memory, mem_cur, step_slot(), tool_ctx)
            else:
                log("GestorHerramientas", "No hay herramientas reutilizables relevantes; continúa.")

        log("Sistema", "── Planificador (plan numerado) ──")
        ctx = _ctx_snip(memory, mem_cur, 0)
        extra = ""
        if motivo_prev or retro_prev:
            extra = (
                "\n--- Retro del ciclo anterior (Revisor) ---\n"
                f"MOTIVO: {motivo_prev}\nRETRO: {retro_prev}\n"
                "Ajusta el plan; no contradigas el OBJETIVO_CLARO.\n"
            )
        sys_p = (
            "Eres PLANIFICADOR. Objetivo formal + criterios. "
            "4-7 pasos numerados. Si la biblioteca trae una herramienta relevante, "
            "prioriza reutilizarla antes de rehacer trabajo. Español, sin saludos.\n"
        )
        user_p = (
            f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n{extra}\n"
            f"HERRAMIENTAS_REUTILIZABLES:\n{tool_ctx or '(sin coincidencias)'}\n\n"
            f"{replay_ctx(obj_claro + chr(10) + crit_txt, 'Planifica')}"
            f"{work_ctx(working_pool)}"
            f"Memoria (parcial): {ctx}\nPLAN:"
        )
        out_p = _ollama(
            llm_chat,
            model,
            sys_p,
            user_p,
            num_predict=_role_budget(num_predict + 60, "Planifica", cycle=cyc, retry=bool(extra)),
        )
        logb("Planifica", out_p)
        cycle_events.append(("Planifica", out_p))
        working_pool.add("Planifica", out_p, salience=0.86)
        mem_cur = _mem_step(memory, mem_cur, step_slot(), out_p)

        internet_acc = ""
        if internet_enabled:
            log("Sistema", "── Internet (busca contexto externo si hay conexión) ──")
            try:
                internet_acc = search_fn(
                    f"{obj_claro}\n{crit_txt}\n{out_p[:900]}",
                    max_results=web_max_results,
                    timeout=web_timeout,
                ).strip()
            except Exception:
                internet_acc = ""
            if internet_acc:
                logb("Internet", internet_acc)
                cycle_events.append(("Internet", internet_acc))
                working_pool.add("Internet", internet_acc, salience=0.82)
                mem_cur = _mem_step(memory, mem_cur, step_slot(), internet_acc)
            else:
                log("Internet", "Sin conexión o sin resultados útiles; pasa turno sin aportar.")

        log(
            "Sistema",
            f"── Debate entre agentes ({max(1, int(n_discuss))} intervención(es)) ──",
        )
        acc_d = []
        for i in range(max(1, int(n_discuss))):
            prev = "\n".join(acc_d[-2:]) if acc_d else "(nada aún)"
            sys_d = (
                f"DISCUSIÓN #{i + 1}: critica o mejora el plan (riesgos, alternativas). "
                "Máx. 10 líneas. Español.\n"
            )
            user_d = (
                f"OBJETIVO_CLARO:\n{obj_claro}\n\nPLAN:\n{out_p[:3500]}\n\n"
                f"HERRAMIENTAS_REUTILIZABLES:\n{tool_ctx[:1400] or '(sin coincidencias)'}\n\n"
                f"APORTE_WEB:\n{internet_acc[:1600] or '(sin aporte)'}\n\n"
                f"{replay_ctx(obj_claro + chr(10) + out_p + chr(10) + prev, 'Discute', 900)}"
                f"{work_ctx(working_pool, 700)}"
                f"Voces recientes:\n{prev}\n\nMemoria: {_ctx_snip(memory, mem_cur, i)}\nTu aporte:"
            )
            out_d = _ollama(
                llm_chat,
                model,
                sys_d,
                user_d,
                num_predict=_role_budget(num_predict, "Discute", cycle=cyc),
            )
            logb(f"Discute{i + 1}", out_d)
            cycle_events.append((f"Discute{i + 1}", out_d))
            working_pool.add(f"Discute{i + 1}", out_d, salience=0.55)
            mem_cur = _mem_step(memory, mem_cur, step_slot(), out_d)
            acc_d.append(out_d)
        discuss_acc = "\n---\n".join(acc_d)

        log(
            "Sistema",
            f"── Ejecución ({max(1, int(n_execute))} agente(s)) ──",
        )
        acc_x = []
        for j in range(max(1, int(n_execute))):
            sys_x = (
                f"EJECUCIÓN #{j + 1}: convierte plan+discusión en entregables "
                "(checklist, pseudocódigo, pasos). Máx. 12 líneas. Español.\n"
            )
            user_x = (
                f"OBJETIVO_CLARO:\n{obj_claro}\n\nPLAN:\n{out_p[:2500]}\n\n"
                f"HERRAMIENTAS_REUTILIZABLES:\n{tool_ctx[:1400] or '(sin coincidencias)'}\n\n"
                f"APORTE_WEB:\n{internet_acc[:1600] or '(sin aporte)'}\n\n"
                f"DISCUSIÓN:\n{discuss_acc[:2500]}\n\n"
                f"{replay_ctx(obj_claro + chr(10) + out_p + chr(10) + discuss_acc, 'Ejecuta', 1000)}"
                f"{work_ctx(working_pool)}"
                f"Memoria: {_ctx_snip(memory, mem_cur, j + 2)}\nTu entrega:"
            )
            out_x = _ollama(
                llm_chat,
                model,
                sys_x,
                user_x,
                num_predict=_role_budget(num_predict + 40, "Ejecuta", cycle=cyc),
            )
            logb(f"Ejecuta{j + 1}", out_x)
            cycle_events.append((f"Ejecuta{j + 1}", out_x))
            working_pool.add(f"Ejecuta{j + 1}", out_x, salience=0.78)
            mem_cur = _mem_step(memory, mem_cur, step_slot(), out_x)
            acc_x.append(out_x)
        execute_acc = "\n---\n".join(acc_x)

        log(
            "Sistema",
            f"── Pruebas ({max(1, int(n_test))} probador(es)) ──",
        )
        acc_t = []
        for k in range(max(1, int(n_test))):
            sys_t = (
                f"PROBADOR #{k + 1}: diseña pruebas, casos límite o checklist de verificación "
                "sobre la EJECUCIÓN (qué validar, qué podría fallar). Máx. 10 líneas. Español.\n"
            )
            user_t = (
                f"OBJETIVO_CLARO:\n{obj_claro}\n\nEJECUCIÓN:\n{execute_acc[:3500]}\n\n"
                f"HERRAMIENTAS_REUTILIZABLES:\n{tool_ctx[:1200] or '(sin coincidencias)'}\n\n"
                f"APORTE_WEB:\n{internet_acc[:1400] or '(sin aporte)'}\n\n"
                f"{replay_ctx(obj_claro + chr(10) + internet_acc + chr(10) + execute_acc, 'Prueba', 900)}"
                f"{work_ctx(working_pool, 800)}"
                f"Memoria: {_ctx_snip(memory, mem_cur, k + 4)}\nTu informe de prueba:"
            )
            out_t = _ollama(
                llm_chat,
                model,
                sys_t,
                user_t,
                num_predict=_role_budget(num_predict + 30, "Prueba", cycle=cyc),
            )
            logb(f"Prueba{k + 1}", out_t)
            cycle_events.append((f"Prueba{k + 1}", out_t))
            working_pool.add(f"Prueba{k + 1}", out_t, salience=0.72)
            mem_cur = _mem_step(memory, mem_cur, step_slot(), out_t)
            acc_t.append(out_t)
        test_acc = "\n---\n".join(acc_t)

        python_probe_acc = ""
        if python_probe_enabled:
            log("Sistema", "── PruebaPython (script pequeño solo si hace falta) ──")
            if probe_skip_non_code and not is_python_probe_relevant(
                obj_claro,
                crit_txt,
                out_p,
                execute_acc,
                test_acc,
            ):
                log(
                    "PruebaPython",
                    "Objetivo sin contexto de código/verificación ejecutable; pasa turno.",
                )
            else:
                sys_py = (
                    "Eres PRUEBA_PYTHON. Decide si hace falta ejecutar un script Python pequeño "
                    "para comprobar cálculos, ejemplos, invariantes o resultados concretos.\n"
                    "Si no aporta evidencia real, marca necesario=false.\n"
                    "Si es necesario, usa solo librería estándar segura (math, statistics, json, random), "
                    "sin red, sin leer/escribir archivos externos, sin input y en menos de 60 líneas.\n"
                    "Devuelve SOLO JSON válido:\n"
                    "{\"necesario\": false, \"motivo\": \"...\", \"script\": \"\"}\n"
                )
                user_py = (
                    f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n\n"
                    f"PLAN:\n{out_p[:1600]}\n\nEJECUCIÓN:\n{execute_acc[:2400]}\n\n"
                    f"PRUEBAS_LLM:\n{test_acc[:1800]}\n\n"
                    f"APORTE_WEB:\n{internet_acc[:1200] or '(sin aporte)'}\n\n"
                    "¿Hace falta comprobar algo con Python? JSON:"
                )
                try:
                    out_py_req = _ollama(
                        llm_chat,
                        model,
                        sys_py,
                        user_py,
                        num_predict=_role_budget(num_predict + 40, "Prueba", cycle=cyc),
                    )
                    needed, reason, script = parse_python_probe_request(out_py_req)
                    if needed:
                        run_result = run_python_probe_script(
                            script,
                            timeout=probe_timeout,
                            max_chars=probe_max_chars,
                        )
                        python_probe_acc = (
                            f"Motivo: {reason or 'verificación solicitada'}\n"
                            f"Script ejecutado:\n{script[:1200]}\n\nResultado:\n{run_result}"
                        )
                        logb("PruebaPython", python_probe_acc)
                        cycle_events.append(("PruebaPython", python_probe_acc))
                        working_pool.add("PruebaPython", python_probe_acc, salience=0.84)
                        mem_cur = _mem_step(memory, mem_cur, step_slot(), python_probe_acc)
                        test_acc = (
                            "\n---\n".join([test_acc, python_probe_acc])
                            if test_acc
                            else python_probe_acc
                        )
                    else:
                        log("PruebaPython", reason or "No hace falta script; pasa turno sin ejecutar.")
                except Exception as exc:
                    if not auxiliary_fail_open:
                        raise
                    log("PruebaPython", f"Falló ({exc}); pasa turno sin aportar.")

        node_probe_acc = ""
        if node_probe_enabled:
            log("Sistema", "── PruebaNode (script JavaScript pequeño solo si hace falta) ──")
            if node_probe_skip_non_code and not is_node_probe_relevant(
                obj_claro,
                crit_txt,
                out_p,
                execute_acc,
                test_acc,
            ):
                log(
                    "PruebaNode",
                    "Objetivo sin contexto Node.js/JavaScript ejecutable; pasa turno.",
                )
            else:
                sys_node = (
                    "Eres PRUEBA_NODE. Decide si hace falta ejecutar un script JavaScript pequeño "
                    "con Node.js para comprobar transformaciones JSON, regex, cálculos, hashing, "
                    "parsing o comportamiento específico de JavaScript/Node.\n"
                    "Si Python ya resolvió la evidencia o Node no aporta algo distinto, marca necesario=false.\n"
                    "Si es necesario, usa JavaScript autocontenido; puedes usar built-ins seguros como "
                    "assert, crypto, URL, URLSearchParams, util y path. No uses fs, red, child_process, "
                    "process.env, eval, Function, input ni escribas archivos. Menos de 80 líneas.\n"
                    "Devuelve SOLO JSON válido:\n"
                    "{\"necesario\": false, \"motivo\": \"...\", \"script\": \"\"}\n"
                )
                user_node = (
                    f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n\n"
                    f"PLAN:\n{out_p[:1600]}\n\nEJECUCIÓN:\n{execute_acc[:2400]}\n\n"
                    f"PRUEBAS_LLM:\n{test_acc[:1800]}\n\n"
                    f"PRUEBA_PYTHON:\n{python_probe_acc[:1400] or '(sin aporte)'}\n\n"
                    f"APORTE_WEB:\n{internet_acc[:1200] or '(sin aporte)'}\n\n"
                    "¿Hace falta comprobar algo con Node.js? JSON:"
                )
                try:
                    out_node_req = _ollama(
                        llm_chat,
                        model,
                        sys_node,
                        user_node,
                        num_predict=_role_budget(num_predict + 50, "Prueba", cycle=cyc),
                    )
                    needed, reason, script = parse_node_probe_request(out_node_req)
                    if needed:
                        run_result = run_node_probe_script(
                            script,
                            timeout=node_probe_timeout,
                            max_chars=node_probe_max_chars,
                        )
                        node_probe_acc = (
                            f"Motivo: {reason or 'verificación Node.js solicitada'}\n"
                            f"Script ejecutado:\n{script[:1200]}\n\nResultado:\n{run_result}"
                        )
                        logb("PruebaNode", node_probe_acc)
                        cycle_events.append(("PruebaNode", node_probe_acc))
                        working_pool.add("PruebaNode", node_probe_acc, salience=0.83)
                        mem_cur = _mem_step(memory, mem_cur, step_slot(), node_probe_acc)
                        test_acc = (
                            "\n---\n".join([test_acc, node_probe_acc])
                            if test_acc
                            else node_probe_acc
                        )
                    else:
                        log("PruebaNode", reason or "No hace falta Node.js; pasa turno sin ejecutar.")
                except Exception as exc:
                    if not auxiliary_fail_open:
                        raise
                    log("PruebaNode", f"Falló ({exc}); pasa turno sin aportar.")

        terminal_acc = ""
        if terminal_enabled:
            log("Sistema", "── Terminal (comando seguro solo si aporta evidencia) ──")
            sys_term = (
                "Eres TERMINAL_PLANNER. Decide si ejecutar UN comando de terminal aporta "
                "evidencia real para el objetivo actual.\n"
                "Solo puedes pedir comandos permitidos por la politica configurada en .env. "
                "Por defecto estan activos comandos seguros y moderados para lectura/verificacion "
                "(python, py, pytest, git status/log/diff, rg, dir/ls, where, pip list/freeze, node/npm de inspeccion). "
                "No pidas instalar, borrar, mover, escribir archivos, leer .env, cambiar git ni usar red.\n"
                "Devuelve SOLO JSON valido:\n"
                "{\"necesario\": false, \"motivo\": \"...\", \"command\": \"\", \"cwd\": \".\"}\n"
            )
            user_term = (
                f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n\n"
                f"PLAN:\n{out_p[:1600]}\n\nEJECUCIÓN:\n{execute_acc[:2200]}\n\n"
                f"PRUEBAS:\n{test_acc[:2200]}\n\n"
                "Si un comando seguro puede comprobar algo util, pidelo. JSON:"
            )
            try:
                out_term_req = _ollama(
                    llm_chat,
                    model,
                    sys_term,
                    user_term,
                    num_predict=_role_budget(num_predict + 30, "Prueba", cycle=cyc),
                )
                term_req = parse_terminal_command_request(out_term_req)
                if term_req.needed:
                    result_obj = run_terminal_fn(
                        term_req.command,
                        project_root=Path(__file__).resolve().parent,
                        cwd=term_req.cwd,
                        timeout=terminal_timeout,
                        max_output_chars=terminal_max_output,
                    )
                    rendered = (
                        result_obj.render()
                        if hasattr(result_obj, "render")
                        else str(result_obj)
                    )
                    terminal_acc = (
                        f"Motivo: {term_req.reason or 'verificacion solicitada'}\n"
                        f"Resultado terminal:\n{rendered}"
                    )
                    logb("Terminal", terminal_acc)
                    cycle_events.append(("Terminal", terminal_acc))
                    working_pool.add("Terminal", terminal_acc, salience=0.86)
                    mem_cur = _mem_step(memory, mem_cur, step_slot(), terminal_acc)
                    test_acc = (
                        "\n---\n".join([test_acc, terminal_acc])
                        if test_acc
                        else terminal_acc
                    )
                else:
                    log("Terminal", term_req.reason or "No hace falta comando; pasa turno.")
            except Exception as exc:
                if not auxiliary_fail_open:
                    raise
                log("Terminal", f"Falló ({exc}); pasa turno sin aportar.")

        log("Sistema", "── Revisor (veredicto + una RESPUESTA_FINAL al usuario) ──")
        sys_r = (
            "Eres el REVISOR FINAL. Con OBJETIVO_CLARO, criterios, plan, discusión, "
            "ejecución, aporte web y pruebas, decide si el objetivo queda satisfecho.\n"
            "La clave respuesta_final debe ser una respuesta dirigida al usuario final, "
            "no un resumen interno del pipeline. Debe contestar de forma ordenada la "
            "pregunta inicial: empieza con la respuesta directa, luego pasos o puntos "
            "importantes, después advertencias/limitaciones si aplican, y cierra con "
            "la recomendación práctica. No menciones nombres de agentes salvo que el "
            "usuario lo haya pedido. Usa suficiente detalle; evita respuestas de una sola frase.\n"
            "Devuelve SOLO JSON válido con estas claves:\n"
            "{\n"
            "  \"objetivo_alcanzado\": true,\n"
            "  \"motivo\": \"breve; si todo está bien usa '-'\",\n"
            "  \"retroalimentacion\": \"si false, qué debe cambiar el próximo ciclo; si true '-'\",\n"
            "  \"respuesta_final\": \"respuesta final clara, ordenada y completa para el usuario\"\n"
            "}\n"
        )
        user_r = (
            f"PREGUNTA_INICIAL_DEL_USUARIO:\n{raw[:4000]}\n\n"
            f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n\n"
            f"PLAN:\n{out_p[:2600]}\n\nDISCUSIÓN:\n{discuss_acc[:2400]}\n\n"
            f"HERRAMIENTAS_REUTILIZABLES:\n{tool_ctx[:1800] or '(sin coincidencias)'}\n\n"
            f"APORTE_WEB:\n{internet_acc[:2200] or '(sin aporte)'}\n\n"
            f"EJECUCIÓN:\n{execute_acc[:2800]}\n\nPRUEBAS:\n{test_acc[:3000]}\n\n"
            f"TERMINAL:\n{terminal_acc[:2200] or '(sin aporte)'}\n\n"
            "INSTRUCCION_RESPUESTA_FINAL:\n"
            "Redacta respuesta_final como si hablaras directamente con el usuario que hizo "
            "PREGUNTA_INICIAL_DEL_USUARIO. Ordena la respuesta con párrafos o viñetas claras "
            "cuando ayude. No cortes ideas a medias.\n"
            f"{replay_ctx(obj_claro + chr(10) + internet_acc + chr(10) + execute_acc + chr(10) + test_acc, 'Revisor', 1400)}"
            f"{work_ctx(working_pool)}"
        )
        out_r = _ollama(
            llm_chat,
            model,
            sys_r,
            user_r,
            num_predict=_role_budget(
                num_predict_final,
                "Revisor",
                cycle=cyc,
                retry=bool(motivo_prev or retro_prev),
            ),
        )
        logb("Revisor", out_r)
        cycle_events.append(("Revisor", out_r))
        working_pool.add("Revisor", out_r, salience=0.92)
        mem_cur = _mem_step(memory, mem_cur, step_slot(), out_r)

        corpus = "\n".join(
            [out_e, out_p, internet_acc, discuss_acc, execute_acc, test_acc, terminal_acc, out_r]
        )
        reached, motivo_prev, final_txt, retro_prev = parse_final_verdict(out_r)
        if tool_manager_enabled and reached and tools is not None:
            log("Sistema", "── GestorHerramientas (consolida reutilizable si aplica) ──")
            sys_tool_save = (
                "Eres GESTOR_HERRAMIENTAS. Analiza el ciclo exitoso y decide si se creo, "
                "descubrio o valido una herramienta reutilizable: script, comando, conector, "
                "procedimiento tecnico o integracion con dispositivo/API.\n"
                "No guardes secretos, tokens, valores de .env ni datos privados. "
                "Si solo hubo una respuesta general sin herramienta reusable, marca reutilizable=false.\n"
                "Devuelve SOLO JSON valido:\n"
                "{"
                "\"reutilizable\": false, "
                "\"motivo\": \"...\", "
                "\"nombre\": \"\", "
                "\"tipo\": \"script|comando|conector|procedimiento\", "
                "\"objetivo\": \"\", "
                "\"activadores\": \"palabras para reconocer objetivos similares\", "
                "\"entrada\": \"archivo/comando/endpoint si aplica\", "
                "\"instrucciones\": \"como reutilizarlo\", "
                "\"evidencia\": \"por que funciono\""
                "}\n"
            )
            user_tool_save = (
                f"OBJETIVO:\n{obj_claro}\n\nPLAN:\n{out_p[:1800]}\n\n"
                f"EJECUCION:\n{execute_acc[:2400]}\n\nPRUEBAS:\n{test_acc[:2200]}\n\n"
                f"TERMINAL:\n{terminal_acc[:1800] or '(sin terminal)'}\n\n"
                f"RESPUESTA_FINAL:\n{final_txt[:1600]}\n\n"
                "Decide si hay herramienta reutilizable que guardar. JSON:"
            )
            try:
                out_tool_save = _ollama(
                    llm_chat,
                    model,
                    sys_tool_save,
                    user_tool_save,
                    num_predict=_role_budget(num_predict + 120, "Revisor", cycle=cyc),
                )
                reusable, tool_memory, reason = parse_tool_memory_request(out_tool_save)
                if reusable and tool_memory is not None:
                    if not tool_memory.objective:
                        tool_memory = type(tool_memory)(
                            name=tool_memory.name,
                            kind=tool_memory.kind,
                            objective=obj_claro[:500],
                            trigger_terms=tool_memory.trigger_terms,
                            entrypoint=tool_memory.entrypoint,
                            instructions=tool_memory.instructions,
                            evidence=tool_memory.evidence,
                            success_count=tool_memory.success_count,
                            failure_count=tool_memory.failure_count,
                        )
                    tools.upsert(tool_memory)
                    saved_msg = (
                        f"Guardada herramienta reutilizable: {tool_memory.name} "
                        f"({tool_memory.kind}) -> {tool_memory.entrypoint or 'ver instrucciones'}"
                    )
                    logb("GestorHerramientas", saved_msg)
                    cycle_events.append(("GestorHerramientas", saved_msg))
                else:
                    log("GestorHerramientas", reason or "Nada reutilizable que guardar.")
            except Exception as exc:
                if not auxiliary_fail_open:
                    raise
                log("GestorHerramientas", f"No se pudo consolidar herramienta: {exc}")
        if experience_replay is not None:
            try:
                experience_replay.record_cycle(
                    cycle=cyc + 1,
                    objective=obj_claro,
                    events=cycle_events,
                    reached=reached,
                )
                log(
                    "Replay",
                    "experiencias compartidas registradas; memoria procedimental/transactiva actualizada.",
                )
            except Exception as exc:
                log("Replay", f"No se pudo registrar replay compartido: {exc}")
        last_loss, free_energy_stats = _memory_learn(memory, mem_cur, corpus, final_txt)

        lines = cycle_buffer.lines()
        if plastic_aux is not None and len(lines) >= 2:
            la, ba = plastic_aux.train_on_lines(lines, memory.mem.device, steps=6)
            log(
                "BufferNet",
                f"energia_libre={ba['free_energy']:.4f} "
                f"reconstruccion={ba['reconstruction']:.4f} "
                f"complejidad={ba['complexity']:.6f} "
                f"entropia={ba['entropy']:.6f} "
                f"loss={la:.4f}",
            )
        cycle_buffer.clear()

        log(
            "Memoria",
            "ciclo "
            f"{cyc + 1}: energia_libre={free_energy_stats['free_energy']:.4f} "
            f"loss={last_loss:.4f} "
            f"sorpresa={free_energy_stats['prediction_error']:.4f} "
            f"complejidad={free_energy_stats['complexity']:.6f} "
            f"entropia={free_energy_stats['entropy']:.6f} "
            f"colision_q={free_energy_stats['quantum_collision']:.4f} "
            f"colapso={free_energy_stats['collapse_prob']:.4f} "
            f"disparo={free_energy_stats['spike_rate']:.4f} "
            f"senal={free_energy_stats['signal']:.2f}  {memory.snapshot_text()}",
        )
        if on_cycle_checkpoint is not None:
            try:
                on_cycle_checkpoint()
                log("Persistencia", f"Checkpoint del ciclo {cyc + 1} guardado.")
            except Exception as exc:
                log("Persistencia", f"No se pudo guardar el ciclo {cyc + 1}: {exc}")

        if reached:
            log("Sistema", "Objetivo certificado por el Revisor.")
            return final_txt, cycles_used, last_loss, True

        if cyc + 1 >= max_cycles:
            log("Sistema", "Máximo de ciclos; última RESPUESTA_FINAL del Revisor.")
            return final_txt, cycles_used, last_loss, False

        log(
            "Sistema",
            f"Revisor: aún no certifica. Motivo: {motivo_prev or '—'} | "
            f"Retro: {retro_prev or '—'}. Nuevo ciclo…",
        )

    raise RuntimeError("objective_cycle: fin de bucle inesperado")
