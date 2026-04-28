"""
Enjambre por objetivo: Entiende → Planifica → Discuten → Ejecutan → Prueban
→ Revisor; memoria compartida + buffer por ciclo que entrena red auxiliar.
Persistencia SQLite (pesos) y carga en segundo plano al arrancar.
"""
from __future__ import annotations

import argparse
import threading

import torch
import tkinter as tk
from tkinter import messagebox, scrolledtext

from llm_api_client import resolve_llm_chat_for_pipeline

from objective_agent_cycle import run_objective_pipeline
from plastic_swarm_state import (
    BufferPlasticNet,
    CycleBuffer,
    SharedExperienceReplay,
    SwarmPlasticStore,
    start_background_weights_load,
)
from task_runtime import TaskRuntime
from tool_library import ToolLibrary
from unified_fly_memory import SharedFlyMemory


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
    weights_ready = threading.Event()
    start_background_weights_load(store, shared_mem, plastic_aux, weights_ready)

    root = tk.Tk()
    root.title("PlasticSwarm · objetivo + memoria")
    root.geometry("840x700")
    root.configure(bg="#1a1d24")

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
        ("r", "#6ee7b7"),
        ("c", "#94a3b8"),
        ("m", "#9ca3af"),
        ("b", "#818cf8"),
        ("y", "#22d3ee"),
        ("s", "#64748b"),
    ):
        log.tag_configure(tag, foreground=fg)

    def append(tag: str, who: str, body: str) -> None:
        log.insert(tk.END, who + "\n", tag)
        log.insert(tk.END, body.rstrip() + "\n\n", tag)
        log.see(tk.END)

    append(
        "s",
        "[Sistema]",
        f"Cargando pesos previos en segundo plano… Ejecutar cuando quieras.\n"
        f"LLM: {llm_backend_label} · modelo «{llm_model_id}»",
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

    tk.Button(
        row,
        text="Ejecutar",
        command=on_run,
        bg="#6366f1",
        fg="white",
        activebackground="#4f46e5",
        relief="flat",
        padx=12,
    ).pack(side="right")
    tk.Button(
        row,
        text="Auditoría",
        command=open_audit_window,
        bg="#374151",
        fg="white",
        activebackground="#4b5563",
        relief="flat",
        padx=12,
    ).pack(side="right", padx=(0, 8))
    tk.Button(
        row,
        text="Memoria",
        command=open_memory_window,
        bg="#374151",
        fg="white",
        activebackground="#4b5563",
        relief="flat",
        padx=12,
    ).pack(side="right", padx=(0, 8))
    goal_entry.bind("<Return>", on_run)

    root.mainloop()


if __name__ == "__main__":
    main()
