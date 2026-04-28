"""
Enjambre por OBJETIVO: Entiende → Planifica → Discuten → Ejecutan → Prueban
→ Revisor. Memoria compartida (SharedFlyMemory) + buffer de ciclo que entrena
una red auxiliar al cerrar cada ciclo (buffer se vacía). Ciclos hasta SI o tope.
"""
from __future__ import annotations

import re
import json
import threading
from typing import Callable

import torch

from multi_agent_orchestrator import _ollama, syntax_score, text_hash_embed
from plastic_swarm_state import BufferPlasticNet, CycleBuffer, SharedExperienceReplay, WorkingMemoryPool
from unified_fly_memory import SharedFlyMemory


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
    return max(48, min(900, int(base * factor)))


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
    num_predict_final: int = 280,
    max_cycles: int = 4,
    n_discuss: int = 2,
    n_execute: int = 2,
    n_test: int = 1,
    on_log: Callable[[str, str], None] | None = None,
    on_cycle_checkpoint: Callable[[], None] | None = None,
) -> tuple[str, int, float, bool]:
    """
    weights_ready: si se pasa, se espera al inicio (carga de pesos en segundo plano).
    """
    log = on_log or (lambda _r, _c: None)

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
            "4-7 pasos numerados. Español, sin saludos.\n"
        )
        user_p = (
            f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n{extra}\n"
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
                f"{replay_ctx(obj_claro + chr(10) + execute_acc, 'Prueba', 900)}"
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

        log("Sistema", "── Revisor (veredicto + una RESPUESTA_FINAL al usuario) ──")
        sys_r = (
            "Eres el REVISOR FINAL. Con OBJETIVO_CLARO, criterios, plan, discusión, "
            "ejecución y pruebas, decide si el objetivo queda satisfecho.\n"
            "Devuelve SOLO JSON válido con estas claves:\n"
            "{\n"
            "  \"objetivo_alcanzado\": true,\n"
            "  \"motivo\": \"breve; si todo está bien usa '-'\",\n"
            "  \"retroalimentacion\": \"si false, qué debe cambiar el próximo ciclo; si true '-'\",\n"
            "  \"respuesta_final\": \"síntesis útil al usuario\"\n"
            "}\n"
        )
        user_r = (
            f"OBJETIVO_CLARO:\n{obj_claro}\n\nCRITERIOS:\n{crit_txt}\n\n"
            f"PLAN:\n{out_p[:1800]}\n\nDISCUSIÓN:\n{discuss_acc[:1800]}\n\n"
            f"EJECUCIÓN:\n{execute_acc[:1800]}\n\nPRUEBAS:\n{test_acc[:1800]}\n"
            f"{replay_ctx(obj_claro + chr(10) + execute_acc + chr(10) + test_acc, 'Revisor', 1000)}"
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

        corpus = "\n".join([out_e, out_p, discuss_acc, execute_acc, test_acc, out_r])
        reached, motivo_prev, final_txt, retro_prev = parse_final_verdict(out_r)
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
