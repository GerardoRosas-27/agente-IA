"""
Chat multi-agente únicamente: mismo LLM local (Ollama en localhost) para cada
sub-agente y para la fusión. Sin conectoma, sin simulación de mosca, sin
descarga de datos. Memoria compartida sintética en blanco (rápida).
"""
from __future__ import annotations

import argparse
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import Callable

from chat_bridge import local_llm_chat_call

from multi_agent_orchestrator import run_collaborative_program
from unified_fly_memory import SharedFlyMemory


def main() -> None:
    p = argparse.ArgumentParser(
        description="Multi-agente + memoria compartida en blanco (sin mosca / sin conectoma)."
    )
    p.add_argument(
        "--llm-model",
        default="gemma3:270m",
        help="Mismo nombre de modelo que en el puente del chat (daemon local).",
    )
    p.add_argument("--mem-slots", type=int, default=6, help="Filas de memoria compartida.")
    p.add_argument("--mem-dim", type=int, default=64, help="Dimensión por fila (más bajo = más rápido).")
    p.add_argument(
        "--num-predict",
        type=int,
        default=220,
        help="Tokens máximos por llamada al LLM (sub-agente / fusión).",
    )
    args = p.parse_args()

    # Misma llamada que `LanguageBridge`: ollama.chat; si falla → None → heurística.
    ollama_chat = local_llm_chat_call
    shared_mem = SharedFlyMemory(
        n_slots=max(4, args.mem_slots),
        mem_dim=max(32, args.mem_dim),
        msg_dim=40,
        n_agents_max=5,
        writer_hidden=128,
        lr=0.002,
        blank_init=True,
    )

    root = tk.Tk()
    root.title("Multi-agente · memoria en blanco (LLM local)")
    root.geometry("780x620")
    root.configure(bg="#1a1d24")

    hdr = tk.Label(
        root,
        text=(
            f"Mismo LLM local que el puente del chat: modelo {args.llm_model!r} "
            "para cada sub-agente y la fusión. Si no hay respuesta, se usa borrador "
            "heurístico (sin mensajes de error). Sin conectoma ni mosca."
        ),
        wraplength=740,
        justify="left",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10),
    )
    hdr.pack(fill="x", padx=10, pady=(8, 4))

    log = scrolledtext.ScrolledText(
        root,
        wrap=tk.WORD,
        height=24,
        font=("Consolas", 10),
        bg="#0f1117",
        fg="#e6edf3",
        insertbackground="#e6edf3",
    )
    log.pack(fill="both", expand=True, padx=10, pady=6)
    for tag, fg in (
        ("a", "#a78bfa"),
        ("u", "#6ee7b7"),
        ("s", "#9ca3af"),
    ):
        log.tag_configure(tag, foreground=fg)

    def append(tag: str, who: str, body: str) -> None:
        log.insert(tk.END, who + "\n", tag)
        log.insert(tk.END, body.rstrip() + "\n\n", tag)
        log.see(tk.END)

    append(
        "s",
        "[Sistema]",
        "Listo. Escribe una tarea y pulsa Ejecutar. "
        "Se intenta el mismo cliente local que el chat antiguo; si no hay modelo "
        "o daemon, verás respuestas [borrador local].",
    )

    row = tk.Frame(root, bg="#1a1d24")
    row.pack(fill="x", padx=10, pady=(0, 4))
    tk.Label(row, text="Tarea:", bg="#1a1d24", fg="#9ca3af", font=("Segoe UI", 9)).pack(
        side="left", padx=(0, 6)
    )
    task_entry = tk.Entry(
        row,
        font=("Segoe UI", 10),
        bg="#252936",
        fg="#f3f4f6",
        insertbackground="#f3f4f6",
        relief="flat",
    )
    task_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    tk.Label(row, text="N:", bg="#1a1d24", fg="#9ca3af").pack(side="left", padx=(0, 2))
    n_spin = tk.Spinbox(row, from_=1, to=5, width=3, font=("Segoe UI", 10), bg="#252936", fg="#f3f4f6")
    n_spin.delete(0, tk.END)
    n_spin.insert(0, "5")
    n_spin.pack(side="left", padx=(0, 8))

    busy = {"v": False}

    def on_run(_ev=None) -> None:
        if busy["v"]:
            return
        task = task_entry.get().strip()
        if not task:
            messagebox.showinfo("Multi-agente", "Escribe una tarea.")
            return
        try:
            n_ag = int(n_spin.get())
        except ValueError:
            n_ag = 5
        n_ag = max(1, min(5, n_ag))
        busy["v"] = True
        append("s", "[Orquestador]", f"Ejecutando con N={n_ag}…")

        def worker() -> None:
            try:
                unified, proposals, loss_v = run_collaborative_program(
                    task=task,
                    n_agents=n_ag,
                    memory=shared_mem,
                    model=args.llm_model,
                    ollama_chat=ollama_chat,
                    rounds=1,
                    num_predict=args.num_predict,
                )
            except Exception as exc:
                root.after(0, lambda: on_fail(exc))
                return
            snap = shared_mem.snapshot_text()
            root.after(0, lambda: on_done(unified, proposals, loss_v, snap))

        def on_fail(exc: Exception) -> None:
            busy["v"] = False
            messagebox.showerror("Multi-agente", str(exc))

        def on_done(
            unified: str,
            proposals: list[str],
            loss_v: float,
            snap: str,
        ) -> None:
            for i, p in enumerate(proposals):
                append("a", f"[Sub-agente {i + 1}]", p)
            append("u", "[Unificado]", unified)
            append("s", "[Memoria]", f"loss={loss_v:.4f}  {snap}")
            busy["v"] = False

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
    task_entry.bind("<Return>", on_run)

    root.mainloop()


if __name__ == "__main__":
    main()
