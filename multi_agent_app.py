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

from harness.feature_store import load_feature_list
from harness.paths import FEATURE_LIST_PATH


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
                tree.insert("", tk.END, values=(f.get("id"), f.get("title") or f.get("name"), f.get("status")))
        except Exception:
            pass

    refresh_tasks()
    # -----------------------

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
                    root.after(0, lambda: append(m))

                goal = goal_entry.get().strip()
                if goal:
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
                    busy["continuous"] = False
                else:
                    append(res.message)
                    append(f"Impl: {res.impl_path}")
                    append(f"Review: {res.review_path}")
                append("--- Fin ---\n")
                
                busy["v"] = False
                
                if busy["continuous"] and res is not None:
                    append(">>> Iniciando siguiente tarea en 2 segundos... <<<")
                    root.after(2000, lambda: on_run(continuous=True))

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
