"""
App de escritorio mínima sobre el harness (LM Studio).

- Sin argumentos: ventana Tk con «Ejecutar un ciclo».
- Con argumentos: delega en `python -m harness.cli` (misma semántica que `harness.cli`).
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import ttk

from harness.feature_store import USER_TASK_ORIGIN, load_feature_list
from harness.paths import FEATURE_LIST_PATH
from harness.shared_memory import list_self_improvements
from harness.skill_registry import is_skill_enabled, set_skill_enabled, sync_skills
from harness.skill_runtime import get_runtime_status
from skills.whatsapp_connector import handle_input_command, parse_whatsapp_command


def main() -> None:
    if len(sys.argv) > 1:
        from harness.cli import main as cli_main

        cli_main(sys.argv[1:])
        return

    from llm_api_client import resolve_llm_chat_for_pipeline

    from harness.orchestrator import run_one_feature_cycle, expand_features_from_goal

    llm_chat, model_id, label = resolve_llm_chat_for_pipeline("")

    root = tk.Tk()
    root.title("Harness LM Studio · líder → implementador → revisor")
    root.geometry("900x700")
    root.configure(bg="#1a1d24")

    tk.Label(
        root,
        text=f"{label} · modelo «{model_id}»\nEscribe un objetivo para añadirlo y ejecutarlo, o déjalo en blanco para procesar la siguiente feature pendiente.",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10),
        justify="left",
    ).pack(fill="x", padx=10, pady=8)

    input_frame = tk.Frame(root, bg="#1a1d24")
    input_frame.pack(fill="x", padx=10, pady=(0, 8))
    
    tk.Label(input_frame, text="Objetivo:", bg="#1a1d24", fg="#9ca3af", font=("Segoe UI", 10)).pack(side="left", padx=(0, 6))
    goal_entry = tk.Entry(
        input_frame,
        font=("Segoe UI", 10),
        bg="#252936",
        fg="#f3f4f6",
        insertbackground="#f3f4f6",
        relief="flat",
    )
    goal_entry.pack(side="left", fill="x", expand=True)

    # --- LISTA DE TAREAS ---
    tk.Label(root, text="Tareas en el sistema:", bg="#1a1d24", fg="#c8d0e0", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
    progress_var = tk.StringVar(value="Estado: listo para trabajar.")
    tk.Label(
        root,
        textvariable=progress_var,
        bg="#1a1d24",
        fg="#9ca3af",
        font=("Segoe UI", 9),
    ).pack(anchor="w", padx=10, pady=(2, 0))
    
    tree_frame = tk.Frame(root, bg="#1a1d24")
    tree_frame.pack(fill="x", padx=10, pady=4)
    
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Treeview", background="#252936", foreground="#f3f4f6", fieldbackground="#252936", borderwidth=0)
    style.configure("Treeview.Heading", background="#374151", foreground="white", relief="flat")
    style.map("Treeview", background=[("selected", "#6366f1")])

    columns = ("id", "name", "status")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
    tree.heading("id", text="ID")
    tree.heading("name", text="Feature")
    tree.heading("status", text="Estado")
    tree.column("id", width=50, anchor="center")
    tree.column("name", width=600)
    tree.column("status", width=120, anchor="center")
    
    tree.pack(side="left", fill="x", expand=True)
    
    scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscroll=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    
    def refresh_tasks() -> None:
        for item in tree.get_children():
            tree.delete(item)
        try:
            data = load_feature_list(FEATURE_LIST_PATH)
            for f in data.get("features", []):
                if f.get("origin") != USER_TASK_ORIGIN:
                    continue
                if f.get("status") == "done":
                    continue
                tree.insert("", tk.END, values=(f.get("id"), f.get("title") or f.get("name"), f.get("status")))
        except Exception:
            pass

    refresh_tasks()
    # -----------------------

    def open_skills_window() -> None:
        """Abre una ventana separada para activar/desactivar skills."""
        sync_skills()
        win = tk.Toplevel(root)
        win.title("Skills disponibles")
        win.geometry("820x520")
        win.configure(bg="#1a1d24")

        tk.Label(
            win,
            text="Skills: activa o desactiva las herramientas que puede usar el sistema.",
            bg="#1a1d24",
            fg="#c8d0e0",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=8)

        skills_frame = tk.Frame(win, bg="#1a1d24")
        skills_frame.pack(fill="both", expand=True, padx=10, pady=4)

        skills_tree = ttk.Treeview(
            skills_frame,
            columns=("name", "enabled", "runtime", "path"),
            show="headings",
            height=10,
        )
        skills_tree.heading("name", text="Skill")
        skills_tree.heading("enabled", text="Habilitada")
        skills_tree.heading("runtime", text="Runtime")
        skills_tree.heading("path", text="Archivo")
        skills_tree.column("name", width=180)
        skills_tree.column("enabled", width=90, anchor="center")
        skills_tree.column("runtime", width=120, anchor="center")
        skills_tree.column("path", width=360)
        skills_tree.pack(side="left", fill="both", expand=True)

        skills_scroll = ttk.Scrollbar(skills_frame, orient=tk.VERTICAL, command=skills_tree.yview)
        skills_tree.configure(yscroll=skills_scroll.set)
        skills_scroll.pack(side="right", fill="y")

        instructions = scrolledtext.ScrolledText(
            win,
            wrap=tk.WORD,
            height=8,
            font=("Consolas", 9),
            bg="#0f1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
        )
        instructions.pack(fill="both", expand=False, padx=10, pady=6)

        skill_cache = {}

        def refresh_skills() -> None:
            skill_cache.clear()
            for item in skills_tree.get_children():
                skills_tree.delete(item)
            for skill in sync_skills():
                runtime = get_runtime_status(skill.name)
                skill_cache[skill.name] = skill
                skills_tree.insert(
                    "",
                    tk.END,
                    iid=skill.name,
                    values=(
                        skill.name,
                        "sí" if skill.enabled else "no",
                        "corriendo" if runtime.running else runtime.status,
                        skill.path,
                    ),
                )

        def selected_skill_name() -> str | None:
            selection = skills_tree.selection()
            if not selection:
                return None
            return str(selection[0])

        def show_selected_instructions(_event=None) -> None:
            name = selected_skill_name()
            instructions.delete("1.0", tk.END)
            if not name:
                return
            skill = skill_cache.get(name)
            if skill:
                instructions.insert(tk.END, skill.instructions)

        def set_selected(enabled: bool) -> None:
            name = selected_skill_name()
            if not name:
                messagebox.showinfo("Skills", "Selecciona una skill primero.")
                return
            set_skill_enabled(name, enabled)
            refresh_skills()
            skills_tree.selection_set(name)
            show_selected_instructions()

        skills_tree.bind("<<TreeviewSelect>>", show_selected_instructions)

        skill_buttons = tk.Frame(win, bg="#1a1d24")
        skill_buttons.pack(fill="x", padx=10, pady=8)
        tk.Button(
            skill_buttons,
            text="Activar",
            command=lambda: set_selected(True),
            bg="#059669",
            fg="white",
            relief="flat",
            padx=12,
        ).pack(side="left")
        tk.Button(
            skill_buttons,
            text="Desactivar",
            command=lambda: set_selected(False),
            bg="#ef4444",
            fg="white",
            relief="flat",
            padx=12,
        ).pack(side="left", padx=(10, 0))
        tk.Button(
            skill_buttons,
            text="Refrescar",
            command=refresh_skills,
            bg="#374151",
            fg="white",
            relief="flat",
            padx=12,
        ).pack(side="left", padx=(10, 0))

        refresh_skills()

    def open_self_improvements_window() -> None:
        """Muestra auto-mejoras separadas de la cola de tareas del usuario."""
        win = tk.Toplevel(root)
        win.title("Auto-mejoras del sistema")
        win.geometry("900x460")
        win.configure(bg="#1a1d24")

        tk.Label(
            win,
            text="Auto-mejoras internas: esta cola no se mezcla con tareas del usuario.",
            bg="#1a1d24",
            fg="#c8d0e0",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=8)

        frame = tk.Frame(win, bg="#1a1d24")
        frame.pack(fill="both", expand=True, padx=10, pady=4)

        columns = ("id", "source", "status", "proposal")
        improvements_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        improvements_tree.heading("id", text="ID")
        improvements_tree.heading("source", text="Origen")
        improvements_tree.heading("status", text="Estado")
        improvements_tree.heading("proposal", text="Propuesta")
        improvements_tree.column("id", width=50, anchor="center")
        improvements_tree.column("source", width=180)
        improvements_tree.column("status", width=100, anchor="center")
        improvements_tree.column("proposal", width=520)
        improvements_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=improvements_tree.yview)
        improvements_tree.configure(yscroll=scroll.set)
        scroll.pack(side="right", fill="y")

        def refresh_improvements() -> None:
            for item in improvements_tree.get_children():
                improvements_tree.delete(item)
            for item in list_self_improvements():
                improvements_tree.insert(
                    "",
                    tk.END,
                    values=(item.id, item.source, item.status, item.proposal),
                )

        row = tk.Frame(win, bg="#1a1d24")
        row.pack(fill="x", padx=10, pady=8)
        tk.Button(
            row,
            text="Refrescar",
            command=refresh_improvements,
            bg="#374151",
            fg="white",
            relief="flat",
            padx=12,
        ).pack(side="left")
        refresh_improvements()

    log = scrolledtext.ScrolledText(
        root,
        wrap=tk.WORD,
        height=22,
        font=("Consolas", 10),
        bg="#0f1117",
        fg="#e6edf3",
        insertbackground="#e6edf3",
    )
    log.pack(fill="both", expand=True, padx=10, pady=4)

    def append(msg: str) -> None:
        log.insert(tk.END, msg.rstrip() + "\n")
        log.see(tk.END)

    busy = {"v": False, "continuous": False}

    def on_run(continuous: bool = False) -> None:
        if busy["v"]:
            return
        busy["v"] = True
        busy["continuous"] = continuous
        append("--- Inicio de ciclo ---")

        def worker() -> None:
            try:
                def emit(m: str) -> None:
                    def update_ui() -> None:
                        append(m)
                        progress_var.set(f"Estado: {m[:140]}")
                        refresh_tasks()

                    root.after(0, update_ui)

                goal = goal_entry.get().strip()
                res = None
                if goal:
                    if parse_whatsapp_command(goal) is not None:
                        if not is_skill_enabled("whatsapp_connector"):
                            raise RuntimeError("La skill whatsapp_connector está desactivada.")
                        try:
                            whatsapp_result = handle_input_command(goal)
                        except Exception as exc:
                            raise RuntimeError(f"Error ejecutando skill WhatsApp: {exc}") from exc

                        assert whatsapp_result is not None
                        emit(whatsapp_result.detail)
                        root.after(0, lambda: goal_entry.delete(0, tk.END))
                    else:
                        # Una solicitud nueva del usuario se divide en subtareas y debe
                        # continuar hasta cerrar la cola completa o encontrar un fallo.
                        busy["continuous"] = True
                        emit(f"Expandiendo objetivo: {goal}")
                        added = expand_features_from_goal(
                            user_goal=goal,
                            model=model_id,
                            llm_chat=llm_chat,
                        )
                        emit(f"Se añadieron {added} nuevas features a la lista.")
                        # Limpiar el input para que el próximo clic solo avance el ciclo
                        root.after(0, lambda: goal_entry.delete(0, tk.END))
                        root.after(0, refresh_tasks)

                        res = run_one_feature_cycle(
                            model=model_id,
                            llm_chat=llm_chat,
                            on_log=emit,
                        )
                else:
                    res = run_one_feature_cycle(
                        model=model_id,
                        llm_chat=llm_chat,
                        on_log=emit,
                    )
            except Exception as exc:
                err_msg = str(exc)

                def show_err(e: str = err_msg) -> None:
                    append(f"ERROR: {e}")
                    messagebox.showerror("Harness", e)

                root.after(0, show_err)
                busy["v"] = False
                busy["continuous"] = False
                return

            def done() -> None:
                refresh_tasks()
                if res is None:
                    append("No hay features pendientes o en progreso.")
                    progress_var.set("Estado: no hay tareas pendientes.")
                    busy["continuous"] = False
                else:
                    append(res.message)
                    append(f"Impl: {res.impl_path}")
                    append(f"Review: {res.review_path}")
                    progress_var.set(f"Estado: tarea {res.feature_id} cerrada con veredicto {res.verdict}.")
                append("--- Fin ---\n")
                
                busy["v"] = False
                
                if busy["continuous"] and res is not None and res.verdict is True:
                    append(">>> Iniciando siguiente tarea en 2 segundos... <<<")
                    root.after(2000, lambda: on_run(continuous=True))
                elif busy["continuous"] and res is not None:
                    append(">>> Modo continuo detenido: la tarea no fue aprobada. Revisa el informe antes de continuar. <<<")
                    busy["continuous"] = False

            root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def stop_run() -> None:
        if busy["continuous"]:
            append(">>> Señal de parada enviada. El sistema se detendrá al terminar el ciclo actual. <<<")
            busy["continuous"] = False

    row = tk.Frame(root, bg="#1a1d24")
    row.pack(fill="x", padx=10, pady=8)
    tk.Button(
        row,
        text="Ejecutar un ciclo",
        command=lambda: on_run(continuous=False),
        bg="#3b82f6",
        fg="white",
        relief="flat",
        padx=14,
    ).pack(side="left")
    tk.Button(
        row,
        text="Ejecutar TODO (Continuo)",
        command=lambda: on_run(continuous=True),
        bg="#6366f1",
        fg="white",
        relief="flat",
        padx=14,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        row,
        text="Detener",
        command=stop_run,
        bg="#ef4444",
        fg="white",
        relief="flat",
        padx=12,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        row,
        text="Refrescar Lista",
        command=refresh_tasks,
        bg="#059669",
        fg="white",
        relief="flat",
        padx=12,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        row,
        text="Administrar Skills",
        command=open_skills_window,
        bg="#8b5cf6",
        fg="white",
        relief="flat",
        padx=12,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        row,
        text="Auto-mejoras",
        command=open_self_improvements_window,
        bg="#a16207",
        fg="white",
        relief="flat",
        padx=12,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        row,
        text="Salir",
        command=root.destroy,
        bg="#374151",
        fg="white",
        relief="flat",
        padx=12,
    ).pack(side="left", padx=(10, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
