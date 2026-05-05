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


def main() -> None:
    if len(sys.argv) > 1:
        from harness.cli import main as cli_main

        cli_main(sys.argv[1:])
        return

    from llm_api_client import resolve_llm_chat_for_pipeline

    from harness.orchestrator import run_one_feature_cycle

    llm_chat, model_id, label = resolve_llm_chat_for_pipeline("")

    root = tk.Tk()
    root.title("Harness LM Studio · líder → implementador → revisor")
    root.geometry("820x560")
    root.configure(bg="#1a1d24")

    tk.Label(
        root,
        text=f"{label} · modelo «{model_id}»\nUn ciclo procesa la siguiente feature en feature_list.json (ver AGENTS.md).",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10),
        justify="left",
    ).pack(fill="x", padx=10, pady=8)

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

    busy = {"v": False}

    def on_run() -> None:
        if busy["v"]:
            return
        busy["v"] = True
        append("--- Inicio de ciclo ---")

        def worker() -> None:
            try:

                def emit(m: str) -> None:
                    root.after(0, lambda: append(m))

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
                return

            def done() -> None:
                if res is None:
                    append("No hay features pendientes o en progreso.")
                else:
                    append(res.message)
                    append(f"Impl: {res.impl_path}")
                    append(f"Review: {res.review_path}")
                append("--- Fin ---\n")
                busy["v"] = False

            root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    row = tk.Frame(root, bg="#1a1d24")
    row.pack(fill="x", padx=10, pady=8)
    tk.Button(
        row,
        text="Ejecutar un ciclo",
        command=on_run,
        bg="#6366f1",
        fg="white",
        relief="flat",
        padx=14,
    ).pack(side="left")
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
