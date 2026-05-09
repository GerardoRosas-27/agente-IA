from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from harness.paths import PROGRESS_DIR


DEFAULT_WORKSPACE = PROGRESS_DIR / "node_execution_environment" / "scripts"
DEFAULT_LOG_DIR = PROGRESS_DIR / "node_execution_environment" / "logs"
MANIFEST_NAME = "runs.jsonl"
INSPECTOR_RE = re.compile(r"ws://[^\s]+")


@dataclass(frozen=True)
class NodeProgramRun:
    run_id: str
    script_path: str
    log_path: str
    pid: int | None
    status: str
    started_at: float
    debug: bool = False
    inspector_url: str = ""
    ended_at: float | None = None
    returncode: int | None = None


class NodeExecutionEnvironment:
    """Entorno Node.js controlado por el sistema agentico.

    Permite escribir scripts, ejecutarlos, arrancarlos con inspector de Node,
    consultar estado, detener procesos y leer logs. No usa `shell=True`.
    """

    def __init__(
        self,
        *,
        workspace: Path | str = DEFAULT_WORKSPACE,
        log_dir: Path | str = DEFAULT_LOG_DIR,
        node_executable: str = "node",
    ) -> None:
        self.workspace = Path(workspace)
        self.log_dir = Path(log_dir)
        self.node_executable = node_executable
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, subprocess.Popen[str]] = {}

    @property
    def manifest_path(self) -> Path:
        return self.log_dir / MANIFEST_NAME

    def write_script(self, name: str, source: str) -> Path:
        script_path = self._script_path(name)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(source, encoding="utf-8")
        return script_path

    def start_script(
        self,
        name: str,
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        debug: bool = False,
        break_on_start: bool = False,
    ) -> NodeProgramRun:
        script_path = self._script_path(name)
        if not script_path.is_file():
            raise FileNotFoundError(f"No existe el script: {script_path}")

        run_id = f"{script_path.stem}_{int(time.time() * 1000)}"
        log_path = self.log_dir / f"{run_id}.log"
        child_env = os.environ.copy()
        if env:
            child_env.update(env)

        command = [self.node_executable]
        if debug:
            command.append("--inspect-brk=127.0.0.1:0" if break_on_start else "--inspect=127.0.0.1:0")
        command.extend(["--enable-source-maps", str(script_path), *(args or [])])

        log_file = log_path.open("w", encoding="utf-8")
        log_file.write(f"[runtime] starting {script_path.name} run_id={run_id} debug={debug}\n")
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=str(self.workspace),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            env=child_env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        log_file.close()
        self._processes[run_id] = process
        run = NodeProgramRun(
            run_id=run_id,
            script_path=str(script_path),
            log_path=str(log_path),
            pid=int(process.pid),
            status="running",
            started_at=time.time(),
            debug=debug,
        )
        self._append_manifest(run)
        return run

    def run_script(
        self,
        name: str,
        *,
        args: list[str] | None = None,
        timeout: float = 30,
        env: dict[str, str] | None = None,
    ) -> NodeProgramRun:
        run = self.start_script(name, args=args, env=env)
        process = self._processes[run.run_id]
        try:
            returncode = process.wait(timeout=timeout)
            status = "completed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            self.stop_script(run.run_id)
            timed_out = NodeProgramRun(
                **{
                    **asdict(run),
                    "status": "timeout",
                    "ended_at": time.time(),
                    "returncode": None,
                }
            )
            self._append_manifest(timed_out)
            return timed_out

        finished = NodeProgramRun(
            **{
                **asdict(run),
                "status": status,
                "ended_at": time.time(),
                "returncode": int(returncode),
            }
        )
        self._append_manifest(finished)
        self._processes.pop(run.run_id, None)
        return finished

    def start_debug_session(
        self,
        name: str,
        *,
        args: list[str] | None = None,
        break_on_start: bool = False,
    ) -> NodeProgramRun:
        return self.start_script(name, args=args, debug=True, break_on_start=break_on_start)

    def get_status(self, run_id: str) -> NodeProgramRun:
        run = self._latest_manifest_run(run_id)
        process = self._processes.get(run_id)
        inspector_url = self._read_inspector_url(run)
        if process is None:
            return NodeProgramRun(**{**asdict(run), "inspector_url": inspector_url or run.inspector_url})

        returncode = process.poll()
        if returncode is None:
            return NodeProgramRun(
                **{
                    **asdict(run),
                    "status": "running",
                    "pid": int(process.pid),
                    "inspector_url": inspector_url or run.inspector_url,
                }
            )

        status = "completed" if returncode == 0 else "failed"
        finished = NodeProgramRun(
            **{
                **asdict(run),
                "status": status,
                "inspector_url": inspector_url or run.inspector_url,
                "ended_at": time.time(),
                "returncode": int(returncode),
            }
        )
        self._append_manifest(finished)
        self._processes.pop(run_id, None)
        return finished

    def stop_script(self, run_id: str) -> NodeProgramRun:
        run = self._latest_manifest_run(run_id)
        process = self._processes.get(run_id)
        if process is not None and process.poll() is None:
            if sys.platform == "win32":
                try:
                    os.kill(process.pid, signal.CTRL_BREAK_EVENT)
                except OSError:
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        stopped = NodeProgramRun(
            **{
                **asdict(run),
                "status": "stopped",
                "inspector_url": self._read_inspector_url(run) or run.inspector_url,
                "ended_at": time.time(),
                "returncode": process.returncode if process else None,
            }
        )
        self._append_manifest(stopped)
        self._processes.pop(run_id, None)
        return stopped

    def read_log(self, run_id: str, *, max_chars: int = 8000) -> str:
        run = self._latest_manifest_run(run_id)
        log_path = Path(run.log_path)
        if not log_path.is_file():
            return ""
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]

    def list_runs(self, *, limit: int = 25) -> list[NodeProgramRun]:
        runs = self._read_manifest()
        deduped: dict[str, NodeProgramRun] = {}
        for run in runs:
            deduped[run.run_id] = run
        ordered = sorted(deduped.values(), key=lambda item: item.started_at, reverse=True)
        return ordered[:limit]

    def _script_path(self, name: str) -> Path:
        clean_name = name.replace("\\", "/").strip("/")
        if not clean_name or clean_name.startswith("../") or "/../" in clean_name:
            raise ValueError("Nombre de script invalido.")
        if not clean_name.endswith((".js", ".mjs", ".cjs")):
            clean_name += ".js"
        path = (self.workspace / clean_name).resolve()
        workspace = self.workspace.resolve()
        if workspace != path.parent and workspace not in path.parents:
            raise ValueError("El script debe vivir dentro del workspace del entorno.")
        return path

    def _append_manifest(self, run: NodeProgramRun) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")

    def _read_manifest(self) -> list[NodeProgramRun]:
        if not self.manifest_path.is_file():
            return []
        runs: list[NodeProgramRun] = []
        for line in self.manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                data: dict[str, Any] = json.loads(line)
                runs.append(NodeProgramRun(**data))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return runs

    def _latest_manifest_run(self, run_id: str) -> NodeProgramRun:
        for run in reversed(self._read_manifest()):
            if run.run_id == run_id:
                return run
        raise KeyError(f"Run no encontrado: {run_id}")

    def _read_inspector_url(self, run: NodeProgramRun) -> str:
        log_path = Path(run.log_path)
        if not log_path.is_file():
            return ""
        match = INSPECTOR_RE.search(log_path.read_text(encoding="utf-8", errors="replace"))
        return match.group(0) if match else ""


def create_viewer_window(parent) -> None:
    """Abre una ventana Tk read-only para observar scripts Node y logs."""
    import tkinter as tk
    from tkinter import scrolledtext
    from tkinter import ttk

    env = NodeExecutionEnvironment()
    win = tk.Toplevel(parent)
    win.title("Entorno Node.js · visualizacion")
    win.geometry("980x580")
    win.configure(bg="#1a1d24")

    tk.Label(
        win,
        text="Entorno de ejecucion Node.js (solo visualizacion)",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", padx=10, pady=8)

    frame = tk.Frame(win, bg="#1a1d24")
    frame.pack(fill="x", padx=10, pady=4)
    tree = ttk.Treeview(
        frame,
        columns=("run", "status", "debug", "pid", "script"),
        show="headings",
        height=7,
    )
    tree.heading("run", text="Run")
    tree.heading("status", text="Estado")
    tree.heading("debug", text="Debug")
    tree.heading("pid", text="PID")
    tree.heading("script", text="Script")
    tree.column("run", width=220)
    tree.column("status", width=90, anchor="center")
    tree.column("debug", width=70, anchor="center")
    tree.column("pid", width=80, anchor="center")
    tree.column("script", width=500)
    tree.pack(fill="x", expand=True)

    log = scrolledtext.ScrolledText(
        win,
        wrap=tk.WORD,
        height=21,
        font=("Consolas", 10),
        bg="#0f1117",
        fg="#e6edf3",
        insertbackground="#e6edf3",
    )
    log.pack(fill="both", expand=True, padx=10, pady=8)
    log.configure(state="disabled")

    selected = {"run_id": ""}

    def refresh_runs() -> None:
        current_selection = selected["run_id"]
        for item in tree.get_children():
            tree.delete(item)
        for run in env.list_runs(limit=50):
            tree.insert(
                "",
                tk.END,
                iid=run.run_id,
                values=(run.run_id, run.status, "si" if run.debug else "no", run.pid or "", run.script_path),
            )
        if current_selection and tree.exists(current_selection):
            tree.selection_set(current_selection)
        refresh_log()
        if win.winfo_exists():
            win.after(1000, refresh_runs)

    def refresh_log(_event=None) -> None:
        selection = tree.selection()
        if selection:
            selected["run_id"] = str(selection[0])
        log.configure(state="normal")
        log.delete("1.0", tk.END)
        if selected["run_id"]:
            log.insert(tk.END, env.read_log(selected["run_id"], max_chars=24000))
        else:
            log.insert(tk.END, "Sin ejecuciones registradas todavia.")
        log.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", refresh_log)
    refresh_runs()


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Entrada estructurada para agentes.

    Acciones soportadas: write, start, run, debug, status, stop, log, list.
    """
    env = NodeExecutionEnvironment()
    action = str(request.get("action", "")).strip().lower()
    if action == "write":
        path = env.write_script(str(request["name"]), str(request.get("source", "")))
        return {"ok": True, "path": str(path)}
    if action == "start":
        started = env.start_script(str(request["name"]), args=list(request.get("args", [])))
        return {"ok": True, "run": asdict(started)}
    if action == "run":
        finished = env.run_script(
            str(request["name"]),
            args=list(request.get("args", [])),
            timeout=float(request.get("timeout", 30)),
        )
        return {"ok": finished.status == "completed", "run": asdict(finished)}
    if action == "debug":
        started = env.start_debug_session(
            str(request["name"]),
            args=list(request.get("args", [])),
            break_on_start=bool(request.get("break_on_start", False)),
        )
        return {"ok": True, "run": asdict(started)}
    if action == "status":
        return {"ok": True, "run": asdict(env.get_status(str(request["run_id"])))}
    if action == "stop":
        return {"ok": True, "run": asdict(env.stop_script(str(request["run_id"])))}
    if action == "log":
        return {"ok": True, "log": env.read_log(str(request["run_id"]))}
    if action == "list":
        return {"ok": True, "runs": [asdict(item) for item in env.list_runs()]}
    raise ValueError(f"Accion no soportada: {action}")
