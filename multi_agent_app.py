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

from chat_bridge import local_llm_chat_call

from objective_agent_cycle import run_objective_pipeline
from plastic_swarm_state import (
    BufferPlasticNet,
    CycleBuffer,
    SwarmPlasticStore,
    start_background_weights_load,
)
from unified_fly_memory import SharedFlyMemory


def _role_tag(role: str) -> str:
    if role.startswith("Discute"):
        return "d"
    if role.startswith("Ejecuta"):
        return "x"
    if role.startswith("Prueba"):
        return "t"
    return {
        "Entiende": "e",
        "Planifica": "p",
        "Revisor": "r",
        "Ciclo": "c",
        "Memoria": "m",
        "BufferNet": "b",
        "Sistema": "s",
    }.get(role, "s")


def main() -> None:
    p = argparse.ArgumentParser(description="PlasticSwarm — objetivo + memoria plástica.")
    p.add_argument("--llm-model", default="gemma3:270m")
    p.add_argument("--mem-slots", type=int, default=6)
    p.add_argument("--mem-dim", type=int, default=64)
    p.add_argument("--num-predict", type=int, default=180)
    p.add_argument("--num-predict-final", type=int, default=280)
    p.add_argument("--max-cycles", type=int, default=4)
    p.add_argument("--discuss", type=int, default=2)
    p.add_argument("--execute", type=int, default=2)
    p.add_argument("--test", type=int, default=1, help="Agentes probadores por ciclo.")
    args = p.parse_args()

    device = torch.device("cpu")

    shared_mem = SharedFlyMemory(
        n_slots=max(4, args.mem_slots),
        mem_dim=max(32, args.mem_dim),
        msg_dim=40,
        n_agents_max=8,
        writer_hidden=128,
        lr=0.002,
        blank_init=True,
    ).to(device)

    plastic_aux = BufferPlasticNet(dim=40, hidden=96).to(device)
    store = SwarmPlasticStore()
    weights_ready = threading.Event()
    start_background_weights_load(store, shared_mem, plastic_aux, weights_ready)

    root = tk.Tk()
    root.title("PlasticSwarm · objetivo + memoria")
    root.geometry("840x700")
    root.configure(bg="#1a1d24")

    hdr = tk.Label(
        root,
        text=(
            "Entrada: un OBJETIVO (texto). Flujo: Entiende → Planifica → Discuten → "
            "Ejecutan → Prueban → Revisor (SI/NO + retro). Cada ciclo: memoria compartida "
            "aprende; el buffer del ciclo entrena una red auxiliar y se vacía. "
            "Al cerrar la ventana se guardan pesos en data/plastic_swarm.sqlite."
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
        ("d", "#a78bfa"),
        ("x", "#34d399"),
        ("t", "#fb7185"),
        ("r", "#6ee7b7"),
        ("c", "#94a3b8"),
        ("m", "#9ca3af"),
        ("b", "#818cf8"),
        ("s", "#64748b"),
    ):
        log.tag_configure(tag, foreground=fg)

    def append(tag: str, who: str, body: str) -> None:
        log.insert(tk.END, who + "\n", tag)
        log.insert(tk.END, body.rstrip() + "\n\n", tag)
        log.see(tk.END)

    append("s", "[Sistema]", "Cargando pesos previos en segundo plano… Ejecutar cuando quieras.")

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
                    args.llm_model,
                    local_llm_chat_call,
                    cycle_buffer=cycle_buf,
                    plastic_aux=plastic_aux,
                    weights_ready=weights_ready,
                    num_predict=args.num_predict,
                    num_predict_final=args.num_predict_final,
                    max_cycles=max_c,
                    n_discuss=n_d,
                    n_execute=n_x,
                    n_test=n_t,
                    on_log=emit,
                )
            except Exception as exc:
                root.after(0, lambda: on_fail(exc))
                return

            def done() -> None:
                st = "certificado" if ok else "fin por límite de ciclos"
                append(
                    "r",
                    "[Respuesta final]",
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
    goal_entry.bind("<Return>", on_run)

    root.mainloop()


if __name__ == "__main__":
    main()
