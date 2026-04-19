"""
Chat con la MOSCA: el LLM es solo PUENTE (clasifica tu texto en estímulos).
La respuesta en el chat la formula el CEREBRO (plantillas a partir de
actividad motora + sensores), no el modelo de lenguaje.

Simulacion en tiempo real en segundo plano (timer Tk).

Uso:
    python chat_fly_app.py
    python chat_fly_app.py --llm-model gemma2:2b
"""
from __future__ import annotations

import argparse
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

from chat_sim_session import FlyChatSession


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--fly-neurons", type=int, default=1200)
    p.add_argument("--hidden", type=int, default=3200,
                   help="tamano capa oculta plastica (motor + preguntas + instruccion LLM)")
    p.add_argument("--llm-model", default="gemma3:270m")
    p.add_argument("--synthetic", action="store_true")
    args = p.parse_args()

    root = tk.Tk()
    root.title("Chat con el cerebro de la mosca (LLM = puente)")
    root.geometry("820x640")
    root.configure(bg="#1a1d24")

    session = FlyChatSession(
        fly_neurons=args.fly_neurons,
        hidden=args.hidden,
        device="cpu",
        llm_bridge_model=args.llm_model,
        force_synthetic=args.synthetic,
    )

    header = tk.Label(
        root,
        text="Puente: el LLM solo etiqueta tu tono → estímulos. "
        "La mosca responde con lecturas del cerebro; las preguntas las elige la RED PLASTICA "
        "(tema + instrucción al LLM) y aprende de tu siguiente mensaje.",
        wraplength=780,
        justify="left",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10),
    )
    header.pack(fill="x", padx=10, pady=(8, 4))

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
    log.tag_configure("tu", foreground="#7ec8ff")
    log.tag_configure("puente", foreground="#a78bfa")
    log.tag_configure("cerebro", foreground="#6ee7b7")
    log.tag_configure("sys", foreground="#9ca3af")

    def append(tag: str, who: str, body: str) -> None:
        log.insert(tk.END, who + "\n", tag)
        log.insert(tk.END, body.rstrip() + "\n\n", tag)
        log.see(tk.END)

    append("sys", "[Sistema]", "Cargando conectoma y red plastica… listo. Escribe abajo.")

    row = tk.Frame(root, bg="#1a1d24")
    row.pack(fill="x", padx=10, pady=(0, 10))

    entry = tk.Entry(
        row,
        font=("Segoe UI", 11),
        bg="#252936",
        fg="#f3f4f6",
        insertbackground="#f3f4f6",
        relief="flat",
    )
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

    busy = {"v": False}

    def on_send(_ev=None) -> None:
        if busy["v"]:
            return
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, tk.END)
        append("tu", "[Tú]", text)
        busy["v"] = True

        def worker() -> None:
            try:
                br = session.classify_only(text)
            except Exception as exc:
                root.after(0, lambda: on_fail(exc))
                return
            root.after(0, lambda: on_done(br))

        def on_fail(exc: Exception) -> None:
            busy["v"] = False
            messagebox.showerror("Puente", str(exc))

        def on_done(br) -> None:
            src, snip = session.apply_bridge_result(br)
            sc = br.scores
            line = (
                f"Fuente={src}  hostil={sc['hostil']:.2f}  amable={sc['amable']:.2f}  "
                f"conversacion={sc['conversacion']:.2f}  curiosidad={sc['curiosidad']:.2f}"
            )
            if snip:
                line += f"\n(raw corto: {snip[:100]})"
            append("puente", "[Puente · lenguaje → estímulos]", line)
            busy["v"] = False

        threading.Thread(target=worker, daemon=True).start()

    entry.bind("<Return>", on_send)
    tk.Button(
        row,
        text="Enviar",
        command=on_send,
        bg="#3b82f6",
        fg="white",
        activebackground="#2563eb",
        relief="flat",
        padx=14,
    ).pack(side="right")

    def sim_tick() -> None:
        session.step(n=5)
        for qt in session.drain_question_queue():
            append("cerebro", "[Cerebro · pregunta]", qt)
        rep = session.pop_brain_reply_if_ready()
        if rep:
            append("cerebro", "[Cerebro]", rep)

        def qappend(t: str) -> None:
            root.after(0, lambda tt=t: append("cerebro", "[Cerebro · pregunta]", tt))

        session.try_enqueue_curiosity_question(qappend)
        root.after(75, sim_tick)

    root.after(200, sim_tick)
    root.mainloop()


if __name__ == "__main__":
    main()
