from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from harness.paths import PROGRESS_DIR


DEFAULT_WORKSPACE = PROGRESS_DIR / "python_execution_environment" / "programs"
DEFAULT_LOG_DIR = PROGRESS_DIR / "python_execution_environment" / "logs"
MANIFEST_NAME = "runs.jsonl"


@dataclass(frozen=True)
class ProgramRun:
    run_id: str
    program_path: str
    log_path: str
    pid: int | None
    status: str
    started_at: float
    ended_at: float | None = None
    returncode: int | None = None


class PythonExecutionEnvironment:
    """Entorno controlado por el sistema agentico para ejecutar programas Python.

    No es una sandbox de seguridad completa. Aisla archivos y logs por carpeta,
    evita `shell=True` y restringe nombres de programa, pero el codigo Python
    ejecutado conserva los permisos del proceso actual.
    """

    def __init__(
        self,
        *,
        workspace: Path | str = DEFAULT_WORKSPACE,
        log_dir: Path | str = DEFAULT_LOG_DIR,
        python_executable: str = sys.executable,
    ) -> None:
        self.workspace = Path(workspace)
        self.log_dir = Path(log_dir)
        self.python_executable = python_executable
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, subprocess.Popen[str]] = {}

    @property
    def manifest_path(self) -> Path:
        return self.log_dir / MANIFEST_NAME

    def write_program(self, name: str, source: str) -> Path:
        """Crea o reemplaza un programa dentro del workspace del entorno."""
        program_path = self._program_path(name)
        program_path.parent.mkdir(parents=True, exist_ok=True)
        program_path.write_text(source, encoding="utf-8")
        return program_path

    def start_program(
        self,
        name: str,
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> ProgramRun:
        """Arranca un programa en segundo plano y devuelve su identificador."""
        program_path = self._program_path(name)
        if not program_path.is_file():
            raise FileNotFoundError(f"No existe el programa: {program_path}")

        run_id = f"{program_path.stem}_{int(time.time() * 1000)}"
        log_path = self.log_dir / f"{run_id}.log"
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        if env:
            child_env.update(env)

        log_file = log_path.open("w", encoding="utf-8")
        log_file.write(f"[runtime] starting {program_path.name} run_id={run_id}\n")
        log_file.flush()
        process = subprocess.Popen(
            [self.python_executable, str(program_path), *(args or [])],
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
        run = ProgramRun(
            run_id=run_id,
            program_path=str(program_path),
            log_path=str(log_path),
            pid=int(process.pid),
            status="running",
            started_at=time.time(),
        )
        self._append_manifest(run)
        return run

    def run_program(
        self,
        name: str,
        *,
        args: list[str] | None = None,
        timeout: float = 30,
        env: dict[str, str] | None = None,
    ) -> ProgramRun:
        """Ejecuta un programa y espera su finalizacion."""
        run = self.start_program(name, args=args, env=env)
        process = self._processes[run.run_id]
        try:
            returncode = process.wait(timeout=timeout)
            status = "completed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            self.stop_program(run.run_id)
            return ProgramRun(
                **{
                    **asdict(run),
                    "status": "timeout",
                    "ended_at": time.time(),
                    "returncode": None,
                }
            )

        finished = ProgramRun(
            **{
                **asdict(run),
                "status": status,
                "ended_at": time.time(),
                "returncode": int(returncode),
            }
        )
        self._append_manifest(finished)
        return finished

    def get_status(self, run_id: str) -> ProgramRun:
        """Consulta el estado actual de un run conocido."""
        run = self._latest_manifest_run(run_id)
        process = self._processes.get(run_id)
        if process is None:
            return run

        returncode = process.poll()
        if returncode is None:
            return ProgramRun(**{**asdict(run), "status": "running", "pid": int(process.pid)})

        status = "completed" if returncode == 0 else "failed"
        finished = ProgramRun(
            **{
                **asdict(run),
                "status": status,
                "ended_at": time.time(),
                "returncode": int(returncode),
            }
        )
        self._append_manifest(finished)
        self._processes.pop(run_id, None)
        return finished

    def stop_program(self, run_id: str) -> ProgramRun:
        """Detiene un programa en ejecucion."""
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
        stopped = ProgramRun(
            **{
                **asdict(run),
                "status": "stopped",
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

    def list_runs(self, *, limit: int = 25) -> list[ProgramRun]:
        runs = self._read_manifest()
        deduped: dict[str, ProgramRun] = {}
        for run in runs:
            deduped[run.run_id] = run
        ordered = sorted(deduped.values(), key=lambda item: item.started_at, reverse=True)
        return ordered[:limit]

    def _program_path(self, name: str) -> Path:
        clean_name = name.replace("\\", "/").strip("/")
        if not clean_name or clean_name.startswith("../") or "/../" in clean_name:
            raise ValueError("Nombre de programa invalido.")
        if not clean_name.endswith(".py"):
            clean_name += ".py"
        path = (self.workspace / clean_name).resolve()
        workspace = self.workspace.resolve()
        if workspace != path.parent and workspace not in path.parents:
            raise ValueError("El programa debe vivir dentro del workspace del entorno.")
        return path

    def _append_manifest(self, run: ProgramRun) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")

    def _read_manifest(self) -> list[ProgramRun]:
        if not self.manifest_path.is_file():
            return []
        runs: list[ProgramRun] = []
        for line in self.manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                data: dict[str, Any] = json.loads(line)
                runs.append(ProgramRun(**data))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return runs

    def _latest_manifest_run(self, run_id: str) -> ProgramRun:
        for run in reversed(self._read_manifest()):
            if run.run_id == run_id:
                return run
        raise KeyError(f"Run no encontrado: {run_id}")


def create_viewer_window(parent) -> None:
    """Abre una ventana Tk read-only para observar runs y logs."""
    import tkinter as tk
    from tkinter import scrolledtext
    from tkinter import ttk

    env = PythonExecutionEnvironment()
    win = tk.Toplevel(parent)
    win.title("Entorno Python · visualizacion")
    win.geometry("900x560")
    win.configure(bg="#1a1d24")

    tk.Label(
        win,
        text="Entorno de ejecucion Python (solo visualizacion)",
        bg="#1a1d24",
        fg="#c8d0e0",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", padx=10, pady=8)

    frame = tk.Frame(win, bg="#1a1d24")
    frame.pack(fill="x", padx=10, pady=4)
    tree = ttk.Treeview(frame, columns=("run", "status", "pid", "program"), show="headings", height=7)
    tree.heading("run", text="Run")
    tree.heading("status", text="Estado")
    tree.heading("pid", text="PID")
    tree.heading("program", text="Programa")
    tree.column("run", width=220)
    tree.column("status", width=90, anchor="center")
    tree.column("pid", width=80, anchor="center")
    tree.column("program", width=470)
    tree.pack(fill="x", expand=True)

    log = scrolledtext.ScrolledText(
        win,
        wrap=tk.WORD,
        height=20,
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
                values=(run.run_id, run.status, run.pid or "", run.program_path),
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
            log.insert(tk.END, env.read_log(selected["run_id"], max_chars=20000))
        else:
            log.insert(tk.END, "Sin ejecuciones registradas todavia.")
        log.configure(state="disabled")

    tree.bind("<<TreeviewSelect>>", refresh_log)
    refresh_runs()


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Entrada estructurada para agentes.

    Acciones soportadas: write, start, run, status, stop, log, list.
    """
    env = PythonExecutionEnvironment()
    action = str(request.get("action", "")).strip().lower()
    if action == "write":
        path = env.write_program(str(request["name"]), str(request.get("source", "")))
        return {"ok": True, "path": str(path)}
    if action == "start":
        started = env.start_program(str(request["name"]), args=list(request.get("args", [])))
        return {"ok": True, "run": asdict(started)}
    if action == "run":
        finished = env.run_program(
            str(request["name"]),
            args=list(request.get("args", [])),
            timeout=float(request.get("timeout", 30)),
        )
        return {"ok": finished.status == "completed", "run": asdict(finished)}
    if action == "status":
        return {"ok": True, "run": asdict(env.get_status(str(request["run_id"])))}
    if action == "stop":
        return {"ok": True, "run": asdict(env.stop_program(str(request["run_id"])))}
    if action == "log":
        return {"ok": True, "log": env.read_log(str(request["run_id"]))}
    if action == "list":
        return {"ok": True, "runs": [asdict(item) for item in env.list_runs()]}
    raise ValueError(f"Accion no soportada: {action}")
