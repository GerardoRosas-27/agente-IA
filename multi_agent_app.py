"""
Enjambre por objetivo: Entiende → Planifica → Discuten → Ejecutan → Prueban
→ Revisor; memoria compartida + buffer por ciclo que entrena red auxiliar.
Persistencia SQLite (pesos) y carga en segundo plano al arrancar.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

import torch
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog

from app_config import grouped_specs, load_config, save_config
from cognitive_regions import CognitiveSystem, start_rest_cycle_worker
from llm_api_client import parse_assistant_message, resolve_llm_chat_for_pipeline

from objective_agent_cycle import run_objective_pipeline
from plastic_swarm_state import (
    BufferPlasticNet,
    CycleBuffer,
    SharedExperienceReplay,
    SwarmPlasticStore,
    start_background_weights_load,
)
from persistent_memory import PersistentMemoryStore
from skill_manager import SkillManager
from task_runtime import TaskRuntime
from tool_library import ToolLibrary
from tool_build_runtime import ToolBuildRuntime
from tool_creator import ToolCreator, tool_creation_objective
from unified_fly_memory import SharedFlyMemory


def _new_chat_log_path() -> Path:
    logs_dir = Path(__file__).resolve().parent / "data" / "chat_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"chat-log-{stamp}.md"


def _role_tag(role: str) -> str:
    if role.startswith("Internet"):
        return "i"
    if role.startswith("Discute"):
        return "d"
    if role.startswith("Ejecuta"):
        return "x"
    if role.startswith("PruebaPython"):
        return "z"
    if role.startswith("PruebaNode"):
        return "n"
    if role.startswith("Terminal"):
        return "q"
    if role.startswith("ToolPreferenceNet"):
        return "v"
    if role.startswith("RegionesEspecializadas"):
        return "o"
    if role.startswith("CognitiveRegions") or role.startswith("RestCycle"):
        return "h"
    if role.startswith("GestorHerramientas"):
        return "g"
    if role.startswith("AgenteSkill") or role.startswith("AgentesModulares"):
        return "a"
    if role.startswith("Skills"):
        return "k"
    if role.startswith("Prueba"):
        return "t"
    return {
        "Entiende": "e",
        "Planifica": "p",
        "GestorHerramientas": "g",
        "ToolPreferenceNet": "v",
        "RegionesEspecializadas": "o",
        "CognitiveRegions": "h",
        "RestCycle": "h",
        "AgentesModulares": "a",
        "Skills": "k",
        "Runtime": "u",
        "Revisor": "r",
        "Ciclo": "c",
        "Memoria": "m",
        "BufferNet": "b",
        "Replay": "y",
        "Persistencia": "s",
        "Sistema": "s",
    }.get(role, "s")


def main() -> None:
    p = argparse.ArgumentParser(description="PlasticSwarm — objetivo + memoria plástica.")
    p.add_argument(
        "--llm-model",
        default="",
        help="Sobrescribe LLM_MODEL de .env si se indica (LM Studio, misma base URL).",
    )
    p.add_argument("--mem-slots", type=int, default=6)
    p.add_argument("--mem-dim", type=int, default=64)
    p.add_argument("--num-predict", type=int, default=180)
    p.add_argument("--num-predict-final", type=int, default=900)
    p.add_argument("--max-cycles", type=int, default=4)
    p.add_argument("--discuss", type=int, default=2)
    p.add_argument("--execute", type=int, default=2)
    p.add_argument("--test", type=int, default=1, help="Agentes probadores por ciclo.")
    p.add_argument(
        "--torch-threads",
        type=int,
        default=1,
        help="Hilos CPU de PyTorch; 1 reduce contencion en equipos pequenos.",
    )
    p.add_argument(
        "--replay-capacity",
        type=int,
        default=240,
        help="Experiencias compartidas persistentes a conservar.",
    )
    args = p.parse_args()

    llm_chat_fn, llm_model_id, llm_backend_label = resolve_llm_chat_for_pipeline(
        args.llm_model
    )

    device = torch.device("cpu")
    torch.set_num_threads(max(1, int(args.torch_threads)))

    shared_mem = SharedFlyMemory(
        n_slots=max(4, args.mem_slots),
        mem_dim=max(32, args.mem_dim),
        msg_dim=40,
        n_agents_max=12,
        writer_hidden=128,
        lr=0.002,
        blank_init=True,
    ).to(device)

    plastic_aux = BufferPlasticNet(dim=40, hidden=96).to(device)
    store = SwarmPlasticStore()
    experience_replay = SharedExperienceReplay(capacity=args.replay_capacity, embed_dim=40)
    tool_library = ToolLibrary(embed_dim=40)
    task_runtime = TaskRuntime()
    persistent_memory = PersistentMemoryStore()
    skill_manager = SkillManager()
    cognitive_system = CognitiveSystem()
    weights_ready = threading.Event()
    start_background_weights_load(store, shared_mem, plastic_aux, weights_ready)

    root = tk.Tk()
    root.title("PlasticSwarm · objetivo + memoria")
    root.geometry("840x700")
    root.configure(bg="#1a1d24")
    chat_log_path = _new_chat_log_path()
    chat_log_path.write_text(
        "\n".join(
            [
                "# PlasticSwarm chat log",
                "",
                f"- Inicio: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                f"- LLM: {llm_backend_label}",
                f"- Modelo: {llm_model_id}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    hdr = tk.Label(
        root,
        text=(
            "Entrada: un OBJETIVO (texto). Flujo: Entiende → GestorHerramientas → Planifica → Internet → "
            "Discuten → Ejecutan → Prueban → PruebaPython → PruebaNode → Terminal → Revisor (SI/NO + retro). Cada ciclo: memoria compartida "
            "aprende minimizando energía libre; el buffer del ciclo entrena una red auxiliar "
            "y se vacía. Replay compartido recupera experiencia episódica, procedimental "
            "y transactiva sin crecer indefinidamente. Se guardan pesos y optimizadores tras cada ciclo y al cerrar. "
            "Solo LLM vía API LM Studio (.env: LLM_API_BASE_URL, LLM_MODEL). "
            "Agentes Internet, PruebaPython, PruebaNode, Terminal y GestorHerramientas configurables en .env."
        ),
        wraplength=800,
        justify="left",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10),
    )
    hdr.pack(fill="x", padx=10, pady=(8, 4))

    log = scrolledtext.ScrolledText(
        root,
        wrap=tk.WORD,
        height=26,
        font=("Consolas", 10),
        bg="#0f1117",
        fg="#e6edf3",
        insertbackground="#e6edf3",
    )
    log.pack(fill="both", expand=True, padx=10, pady=6)
    for tag, fg in (
        ("e", "#fbbf24"),
        ("p", "#38bdf8"),
        ("i", "#2dd4bf"),
        ("g", "#c084fc"),
        ("a", "#f0abfc"),
        ("k", "#f9a8d4"),
        ("u", "#93c5fd"),
        ("d", "#a78bfa"),
        ("x", "#34d399"),
        ("t", "#fb7185"),
        ("z", "#f97316"),
        ("n", "#facc15"),
        ("q", "#60a5fa"),
        ("v", "#86efac"),
        ("o", "#67e8f9"),
        ("h", "#bef264"),
        ("r", "#6ee7b7"),
        ("c", "#94a3b8"),
        ("m", "#9ca3af"),
        ("b", "#818cf8"),
        ("y", "#22d3ee"),
        ("s", "#64748b"),
    ):
        log.tag_configure(tag, foreground=fg)

    def append(tag: str, who: str, body: str) -> None:
        text = body.rstrip()
        log.insert(tk.END, who + "\n", tag)
        log.insert(tk.END, text + "\n\n", tag)
        log.see(tk.END)
        try:
            with chat_log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"## {time.strftime('%H:%M:%S')} {who}\n\n{text}\n\n")
        except OSError:
            pass

    append(
        "s",
        "[Sistema]",
        f"Cargando pesos previos en segundo plano… Ejecutar cuando quieras.\n"
        f"LLM: {llm_backend_label} · modelo «{llm_model_id}»\n"
        f"Chat-log: {chat_log_path}",
    )

    row = tk.Frame(root, bg="#1a1d24")
    row.pack(fill="x", padx=10, pady=(0, 4))
    tk.Label(row, text="Objetivo:", bg="#1a1d24", fg="#9ca3af").pack(side="left", padx=(0, 6))
    goal_entry = tk.Entry(
        row,
        font=("Segoe UI", 10),
        bg="#252936",
        fg="#f3f4f6",
        insertbackground="#f3f4f6",
        relief="flat",
    )
    goal_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

    cf = tk.Frame(root, bg="#1a1d24")
    cf.pack(fill="x", padx=10, pady=(0, 8))

    def _spin(parent, label: str, default: int, lo: int, hi: int) -> tk.Spinbox:
        tk.Label(parent, text=label, bg="#1a1d24", fg="#9ca3af", font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 2)
        )
        sp = tk.Spinbox(
            parent,
            from_=lo,
            to=hi,
            width=3,
            font=("Segoe UI", 10),
            bg="#252936",
            fg="#f3f4f6",
        )
        sp.delete(0, tk.END)
        sp.insert(0, str(default))
        sp.pack(side="left", padx=(0, 12))
        return sp

    spin_cycles = _spin(cf, "Ciclos máx.:", args.max_cycles, 1, 10)
    spin_discuss = _spin(cf, "Discutir:", args.discuss, 1, 4)
    spin_execute = _spin(cf, "Ejecutar:", args.execute, 1, 4)
    spin_test = _spin(cf, "Probar:", args.test, 1, 3)
    autonomous_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        cf,
        text="Autónomo",
        variable=autonomous_var,
        bg="#1a1d24",
        fg="#f3f4f6",
        selectcolor="#252936",
        activebackground="#1a1d24",
        activeforeground="#f3f4f6",
        font=("Segoe UI", 9),
    ).pack(side="left", padx=(0, 12))

    busy = {"v": False}

    def emit(role: str, content: str) -> None:
        tag = _role_tag(role)
        who = f"[{role}]"

        def _do() -> None:
            append(tag, who, content)

        root.after(0, _do)

    def on_closing() -> None:
        try:
            store.save(shared_mem, plastic_aux)
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    if (os.getenv("REST_CYCLE_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "si", "sí", "on"}:
        try:
            rest_interval = int(os.getenv("REST_CYCLE_INTERVAL_SECONDS") or "1800")
        except ValueError:
            rest_interval = 1800
        start_rest_cycle_worker(
            is_idle=lambda: not busy["v"],
            on_log=emit,
            interval_s=rest_interval,
            cognitive_system=cognitive_system,
            runtime=task_runtime,
            memory_store=persistent_memory,
            skill_manager=skill_manager,
        )

    def approve_tool_call(tool: object, request: dict, reason: str) -> bool:
        result = {"approved": False}
        ready = threading.Event()
        name = getattr(tool, "name", "herramienta")
        risk = getattr(tool, "risk", "?")

        def ask() -> None:
            preview = str(request)
            if len(preview) > 1200:
                preview = preview[:1200] + "..."
            result["approved"] = messagebox.askyesno(
                "Aprobar herramienta",
                (
                    f"La herramienta '{name}' requiere aprobación.\n"
                    f"Riesgo: {risk}\n"
                    f"Motivo: {reason}\n\n"
                    f"Solicitud:\n{preview}\n\n"
                    "¿Permitir esta ejecución?"
                ),
                parent=root,
            )
            ready.set()

        root.after(0, ask)
        ready.wait()
        return bool(result["approved"])

    def open_audit_window() -> None:
        win = tk.Toplevel(root)
        win.title("Auditoría · runtime agentico")
        win.geometry("980x620")
        win.configure(bg="#1a1d24")

        top = tk.Frame(win, bg="#1a1d24")
        top.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(
            top,
            text="Objetivos recientes y llamadas de herramientas",
            bg="#1a1d24",
            fg="#e6edf3",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        body = tk.Frame(win, bg="#1a1d24")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tasks_box = tk.Listbox(
            body,
            width=42,
            bg="#0f1117",
            fg="#e6edf3",
            selectbackground="#374151",
            activestyle="none",
            font=("Consolas", 9),
        )
        tasks_box.pack(side="left", fill="y", padx=(0, 8))

        details = scrolledtext.ScrolledText(
            body,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#0f1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
        )
        details.pack(side="left", fill="both", expand=True)

        state: dict[str, list[dict]] = {"tasks": []}

        def _task_label(task: dict) -> str:
            objective = str(task.get("objective") or "").replace("\n", " ")[:34]
            status = str(task.get("status") or "?")
            updated = str(task.get("updated") or "")
            return f"{updated} | {status:<10} | {objective}"

        def render_task(index: int) -> None:
            details.delete("1.0", tk.END)
            tasks = state["tasks"]
            if not (0 <= index < len(tasks)):
                details.insert(tk.END, "Selecciona un objetivo para ver su auditoría.")
                return
            task = tasks[index]
            calls = task_runtime.tool_calls_for_task(str(task["task_id"]), limit=80)
            lines = [
                f"TASK_ID: {task['task_id']}",
                f"estado: {task.get('status')}",
                f"creado: {task.get('created')}",
                f"actualizado: {task.get('updated')}",
                f"objetivo:\n{task.get('objective')}",
                "",
                f"tool_calls: {len(calls)}",
                "═" * 80,
            ]
            for call in calls:
                status = "PERMITIDA" if call.get("allowed") else "BLOQUEADA"
                lines.extend(
                    [
                        f"#{call.get('id')} · {call.get('created')} · {call.get('tool_name')} [{call.get('risk')}] {status}",
                        f"decisión: {call.get('decision')}",
                        f"duración: {float(call.get('duration_s') or 0.0):.2f}s",
                        f"request:\n{call.get('request') or '{}'}",
                    ]
                )
                if call.get("output"):
                    lines.append(f"output:\n{str(call.get('output'))[:1800]}")
                if call.get("error"):
                    lines.append(f"error:\n{str(call.get('error'))[:1200]}")
                lines.append("-" * 80)
            details.insert(tk.END, "\n".join(lines))

        def refresh() -> None:
            state["tasks"] = task_runtime.list_tasks(limit=40)
            tasks_box.delete(0, tk.END)
            for task in state["tasks"]:
                tasks_box.insert(tk.END, _task_label(task))
            if state["tasks"]:
                tasks_box.selection_set(0)
                render_task(0)
            else:
                details.delete("1.0", tk.END)
                details.insert(tk.END, "Aún no hay objetivos auditados.")

        def on_select(_event=None) -> None:
            sel = tasks_box.curselection()
            if sel:
                render_task(int(sel[0]))

        tk.Button(
            top,
            text="Actualizar",
            command=refresh,
            bg="#374151",
            fg="white",
            relief="flat",
            padx=10,
        ).pack(side="right")
        tasks_box.bind("<<ListboxSelect>>", on_select)
        refresh()

    def open_memory_window() -> None:
        win = tk.Toplevel(root)
        win.title("Memoria · herramientas reutilizables")
        win.geometry("980x620")
        win.configure(bg="#1a1d24")

        top = tk.Frame(win, bg="#1a1d24")
        top.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(
            top,
            text="Biblioteca de herramientas reutilizables",
            bg="#1a1d24",
            fg="#e6edf3",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        body = tk.Frame(win, bg="#1a1d24")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tools_box = tk.Listbox(
            body,
            width=44,
            bg="#0f1117",
            fg="#e6edf3",
            selectbackground="#374151",
            activestyle="none",
            font=("Consolas", 9),
        )
        tools_box.pack(side="left", fill="y", padx=(0, 8))

        details = scrolledtext.ScrolledText(
            body,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#0f1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
        )
        details.pack(side="left", fill="both", expand=True)

        state: dict[str, list] = {"tools": []}

        def _label(tool) -> str:
            return (
                f"ok={tool.success_count:<2} fail={tool.failure_count:<2} "
                f"{tool.kind[:10]:<10} {tool.name[:28]}"
            )

        def selected_tool():
            sel = tools_box.curselection()
            if not sel:
                return None
            idx = int(sel[0])
            if 0 <= idx < len(state["tools"]):
                return state["tools"][idx]
            return None

        def render(index: int) -> None:
            details.delete("1.0", tk.END)
            if not (0 <= index < len(state["tools"])):
                details.insert(tk.END, "Selecciona una herramienta.")
                return
            tool = state["tools"][index]
            details.insert(
                tk.END,
                "\n".join(
                    [
                        f"NOMBRE: {tool.name}",
                        f"TIPO: {tool.kind}",
                        f"OK/FAIL: {tool.success_count}/{tool.failure_count}",
                        f"ENTRYPOINT: {tool.entrypoint or '-'}",
                        f"ACTIVADORES:\n{tool.trigger_terms or '-'}",
                        f"OBJETIVO:\n{tool.objective or '-'}",
                        f"INSTRUCCIONES:\n{tool.instructions or '-'}",
                        f"EVIDENCIA:\n{tool.evidence or '-'}",
                    ]
                ),
            )

        def refresh_tools() -> None:
            state["tools"] = tool_library.list_entries(limit=100)
            tools_box.delete(0, tk.END)
            for tool in state["tools"]:
                tools_box.insert(tk.END, _label(tool))
            details.delete("1.0", tk.END)
            if state["tools"]:
                tools_box.selection_set(0)
                render(0)
            else:
                details.insert(tk.END, "Aún no hay herramientas reutilizables guardadas.")

        def on_select(_event=None) -> None:
            sel = tools_box.curselection()
            if sel:
                render(int(sel[0]))

        def mark_bad() -> None:
            tool = selected_tool()
            if tool is None:
                return
            tool_library.mark_failure(tool.name, tool.entrypoint)
            refresh_tools()

        def delete_tool() -> None:
            tool = selected_tool()
            if tool is None:
                return
            if not messagebox.askyesno(
                "Borrar memoria",
                f"¿Borrar la herramienta reutilizable '{tool.name}'?",
                parent=win,
            ):
                return
            tool_library.delete(tool.name, tool.entrypoint)
            refresh_tools()

        tk.Button(
            top,
            text="Actualizar",
            command=refresh_tools,
            bg="#374151",
            fg="white",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            top,
            text="Borrar",
            command=delete_tool,
            bg="#7f1d1d",
            fg="white",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            top,
            text="Marcar fallida",
            command=mark_bad,
            bg="#92400e",
            fg="white",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(8, 0))
        tools_box.bind("<<ListboxSelect>>", on_select)
        refresh_tools()

    def create_new_tool() -> None:
        if busy["v"]:
            messagebox.showinfo(
                "Crear herramienta",
                "El ciclo principal ya está trabajando. Espera a que termine.",
                parent=root,
            )
            return
        objective = simpledialog.askstring(
            "Crear nueva herramienta",
            "Describe qué herramienta quieres crear y probar:",
            parent=root,
        )
        if not objective or not objective.strip():
            return

        try:
            run_tests = str(os.getenv("SELF_IMPROVEMENT_RUN_TESTS", "1")).strip().lower() in {
                "1",
                "true",
                "yes",
                "si",
                "sí",
                "on",
            }
            test_timeout = float(os.getenv("SELF_IMPROVEMENT_TEST_TIMEOUT", "120") or "120")
        except ValueError:
            run_tests, test_timeout = True, 120.0

        busy["v"] = True
        emit(
            "Sistema",
            "Creando nueva herramienta con el ciclo principal; se instalará como skill si las pruebas pasan.",
        )
        creator = ToolCreator(
            skill_manager=skill_manager,
            tool_library=tool_library,
            on_log=emit,
        )
        cycle_buf = CycleBuffer()

        def pipeline_runner(cycle_objective: str) -> tuple[str, int, float, bool]:
            forced_env = {
                "INTERNET_AGENT_ENABLED": "1",
                "PYTHON_TEST_AGENT_ENABLED": "1",
                "PYTHON_TEST_AGENT_SKIP_NON_CODE": "0",
                "NODE_TEST_AGENT_ENABLED": "1",
                "NODE_TEST_AGENT_SKIP_NON_CODE": "0",
            }
            previous_env = {key: os.environ.get(key) for key in forced_env}
            os.environ.update(forced_env)
            emit(
                "CrearHerramienta",
                "Agentes auxiliares forzados para este flujo: Internet=ON, PruebaPython=ON, PruebaNode=ON, skip_non_code=OFF.",
            )
            try:
                return run_objective_pipeline(
                    cycle_objective,
                    shared_mem,
                    llm_model_id,
                    llm_chat_fn,
                    cycle_buffer=cycle_buf,
                    plastic_aux=plastic_aux,
                    experience_replay=experience_replay,
                    tool_library=tool_library,
                    skill_manager=skill_manager,
                    persistent_memory=persistent_memory,
                    cognitive_system=cognitive_system,
                    task_runtime=task_runtime,
                    internet_agent_enabled=True,
                    python_test_agent_enabled=True,
                    node_test_agent_enabled=True,
                    terminal_agent_enabled=False,
                    autonomous_mode=bool(autonomous_var.get()),
                    approval_callback=approve_tool_call,
                    weights_ready=weights_ready,
                    num_predict=max(args.num_predict, 260),
                    num_predict_final=max(args.num_predict_final, 1400),
                    max_cycles=max(1, min(5, args.max_cycles)),
                    n_discuss=max(1, min(3, args.discuss)),
                    n_execute=max(1, min(3, args.execute)),
                    n_test=max(1, min(2, args.test)),
                    on_log=emit,
                    on_cycle_checkpoint=lambda: store.save(shared_mem, plastic_aux),
                )
            finally:
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        def worker() -> None:
            try:
                builder = ToolBuildRuntime(
                    creator=creator,
                    max_repair_attempts=3,
                )

                def repair_with_llm(prompt: str) -> str:
                    resp = llm_chat_fn(
                        llm_model_id,
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Eres un reparador de herramientas. Devuelve solo JSON valido "
                                    "con codigo y pruebas corregidas."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        {"num_predict": 1600, "temperature": 0.2},
                    )
                    return parse_assistant_message(resp) or ""

                final_txt, cycles, _loss_v, reached = pipeline_runner(
                    tool_creation_objective(objective.strip())
                )
                result = builder.build(
                    user_objective=objective.strip(),
                    initial_text=final_txt,
                    cycles=cycles,
                    reached=reached,
                    run_tests=run_tests,
                    timeout=test_timeout,
                    repair_callback=repair_with_llm,
                )
            except Exception as exc:
                root.after(0, lambda exc=exc: on_fail_create(exc))
                return

            def done() -> None:
                status = "validada" if result.test_ok else "instalada como experimental"
                append(
                    "g",
                    "[CrearHerramienta]",
                    (
                        f"Herramienta {status}: {result.name}\n"
                        f"Ruta: {result.skill_dir}\n"
                        f"Ciclos usados: {result.cycles}\n\n"
                        f"Intentos de build: {result.attempts}\n"
                        f"Build-log: {result.build_log_path or '-'}\n\n"
                        f"Pruebas:\n{result.test_output}"
                    ),
                )
                try:
                    store.save(shared_mem, plastic_aux)
                except Exception as exc:
                    append("s", "[Persistencia]", str(exc))
                busy["v"] = False
                if result.test_ok:
                    messagebox.showinfo(
                        "Crear herramienta",
                        f"Herramienta instalada y probada: {result.name}",
                        parent=root,
                    )
                else:
                    messagebox.showwarning(
                        "Crear herramienta",
                        f"La herramienta se instaló como experimental porque fallaron las pruebas: {result.name}",
                        parent=root,
                    )

            root.after(0, done)

        def on_fail_create(exc: Exception) -> None:
            busy["v"] = False
            messagebox.showerror("Crear herramienta", str(exc), parent=root)

        threading.Thread(target=worker, daemon=True).start()

    def open_config_window() -> None:
        win = tk.Toplevel(root)
        win.title("Configuración · .env")
        win.geometry("820x700")
        win.configure(bg="#1a1d24")

        header = tk.Frame(win, bg="#1a1d24")
        header.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(
            header,
            text="Configuración persistente (.env)",
            bg="#1a1d24",
            fg="#e6edf3",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        canvas = tk.Canvas(win, bg="#1a1d24", highlightthickness=0)
        scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
        form = tk.Frame(canvas, bg="#1a1d24")
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=(0, 10))

        loaded = load_config()
        widgets: dict[str, object] = {}
        row_idx = 0
        for section, specs in grouped_specs().items():
            tk.Label(
                form,
                text=section,
                bg="#1a1d24",
                fg="#f3f4f6",
                font=("Segoe UI", 10, "bold"),
            ).grid(row=row_idx, column=0, columnspan=3, sticky="w", pady=(12, 4))
            row_idx += 1
            for spec in specs:
                tk.Label(
                    form,
                    text=spec.key,
                    bg="#1a1d24",
                    fg="#c8d0e0",
                    font=("Consolas", 9),
                    anchor="w",
                    width=38,
                ).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=2)
                if spec.kind == "bool":
                    var = tk.BooleanVar(value=str(loaded.get(spec.key, spec.default)).strip().lower() in {"1", "true", "yes", "si", "sí", "on"})
                    widget = tk.Checkbutton(
                        form,
                        variable=var,
                        bg="#1a1d24",
                        selectcolor="#252936",
                        activebackground="#1a1d24",
                    )
                    widget.grid(row=row_idx, column=1, sticky="w", pady=2)
                    widgets[spec.key] = var
                else:
                    entry = tk.Entry(
                        form,
                        width=52,
                        bg="#252936",
                        fg="#f3f4f6",
                        insertbackground="#f3f4f6",
                        relief="flat",
                        show="*" if spec.secret else "",
                    )
                    entry.insert(0, loaded.get(spec.key, spec.default))
                    entry.grid(row=row_idx, column=1, sticky="we", pady=2)
                    widgets[spec.key] = entry
                tk.Label(
                    form,
                    text=spec.description,
                    bg="#1a1d24",
                    fg="#9ca3af",
                    font=("Segoe UI", 8),
                    anchor="w",
                    wraplength=260,
                    justify="left",
                ).grid(row=row_idx, column=2, sticky="w", padx=(8, 0), pady=2)
                row_idx += 1

        def collect_values() -> dict[str, str]:
            out = {}
            for section_specs in grouped_specs().values():
                for spec in section_specs:
                    widget = widgets[spec.key]
                    if isinstance(widget, tk.BooleanVar):
                        out[spec.key] = "1" if widget.get() else "0"
                    else:
                        out[spec.key] = widget.get().strip()  # type: ignore[attr-defined]
            return out

        def save() -> None:
            try:
                path = save_config(collect_values())
            except Exception as exc:
                messagebox.showerror("Configuración", str(exc), parent=win)
                return
            messagebox.showinfo(
                "Configuración",
                f"Guardado en {path}.\nAlgunos cambios de LLM pueden requerir reiniciar la app.",
                parent=win,
            )

        tk.Button(
            header,
            text="Ver chat-log",
            command=show_chat_log_path,
            bg="#374151",
            fg="white",
            activebackground="#4b5563",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(0, 6))
        tk.Button(
            header,
            text="Memoria",
            command=open_memory_window,
            bg="#374151",
            fg="white",
            activebackground="#4b5563",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(0, 6))
        tk.Button(
            header,
            text="Auditoría",
            command=open_audit_window,
            bg="#374151",
            fg="white",
            activebackground="#4b5563",
            relief="flat",
            padx=10,
        ).pack(side="right", padx=(0, 6))
        tk.Button(
            header,
            text="Guardar .env",
            command=save,
            bg="#6366f1",
            fg="white",
            activebackground="#4f46e5",
            relief="flat",
            padx=12,
        ).pack(side="right")

    def on_run(_ev=None) -> None:
        if busy["v"]:
            return
        goal = goal_entry.get().strip()
        if not goal:
            messagebox.showinfo("Objetivo", "Escribe qué quieres lograr.")
            return
        try:
            max_c = int(spin_cycles.get())
            n_d = int(spin_discuss.get())
            n_x = int(spin_execute.get())
            n_t = int(spin_test.get())
        except ValueError:
            max_c, n_d, n_x, n_t = 4, 2, 2, 1
        max_c = max(1, min(10, max_c))
        n_d = max(1, min(4, n_d))
        n_x = max(1, min(4, n_x))
        n_t = max(1, min(3, n_t))
        busy["v"] = True
        emit("Sistema", "Pipeline en marcha…")
        cycle_buf = CycleBuffer()

        def worker() -> None:
            try:
                final_txt, cycles, loss_v, ok = run_objective_pipeline(
                    goal,
                    shared_mem,
                    llm_model_id,
                    llm_chat_fn,
                    cycle_buffer=cycle_buf,
                    plastic_aux=plastic_aux,
                    experience_replay=experience_replay,
                    tool_library=tool_library,
                    skill_manager=skill_manager,
                    persistent_memory=persistent_memory,
                    cognitive_system=cognitive_system,
                    task_runtime=task_runtime,
                    autonomous_mode=bool(autonomous_var.get()),
                    approval_callback=approve_tool_call,
                    weights_ready=weights_ready,
                    num_predict=args.num_predict,
                    num_predict_final=args.num_predict_final,
                    max_cycles=max_c,
                    n_discuss=n_d,
                    n_execute=n_x,
                    n_test=n_t,
                    on_log=emit,
                    on_cycle_checkpoint=lambda: store.save(shared_mem, plastic_aux),
                )
            except Exception as exc:
                err = exc
                root.after(0, lambda err=err: on_fail(err))
                return

            def done() -> None:
                st = "certificado" if ok else "fin por límite de ciclos"
                append(
                    "r",
                    "[Respuesta al usuario]",
                    f"({st}, ciclos={cycles}, loss memoria compartida={loss_v:.4f})\n\n{final_txt}",
                )
                try:
                    store.save(shared_mem, plastic_aux)
                    append("s", "[Persistencia]", "Checkpoint tras objetivo guardado.")
                except Exception as exc:
                    append("s", "[Persistencia]", str(exc))
                busy["v"] = False

            root.after(0, done)

        def on_fail(exc: Exception) -> None:
            busy["v"] = False
            messagebox.showerror("Pipeline", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def show_chat_log_path() -> None:
        messagebox.showinfo(
            "Chat-log",
            f"La conversación visible se está guardando en:\n{chat_log_path}",
            parent=root,
        )

    action_bar = tk.Frame(root, bg="#1a1d24")
    action_bar.pack(fill="x", padx=10, pady=(0, 8))
    tk.Button(
        action_bar,
        text="Ejecutar",
        command=on_run,
        bg="#6366f1",
        fg="white",
        activebackground="#4f46e5",
        relief="flat",
        padx=12,
    ).pack(side="right")
    tk.Button(
        action_bar,
        text="Configurar",
        command=open_config_window,
        bg="#374151",
        fg="white",
        activebackground="#4b5563",
        relief="flat",
        padx=12,
    ).pack(side="right", padx=(0, 8))
    tk.Button(
        action_bar,
        text="Crear herramienta",
        command=create_new_tool,
        bg="#0f766e",
        fg="white",
        activebackground="#0d9488",
        relief="flat",
        padx=12,
    ).pack(side="right", padx=(0, 8))
    goal_entry.bind("<Return>", on_run)

    root.mainloop()


if __name__ == "__main__":
    main()
