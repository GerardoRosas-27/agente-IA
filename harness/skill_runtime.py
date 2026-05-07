"""Runtime de skills activables en segundo plano."""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harness.paths import PROGRESS_DIR, REPO_ROOT, STATE_DB_PATH


@dataclass(frozen=True)
class RuntimeStatus:
    skill_name: str
    running: bool
    pid: int | None
    status: str
    detail: str


def _connect(db_path: Path = STATE_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_runtime_db(db_path: Path = STATE_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_runtime (
                skill_name TEXT PRIMARY KEY,
                pid INTEGER,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )


def _is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_runtime_status(
    skill_name: str,
    db_path: Path = STATE_DB_PATH,
) -> RuntimeStatus:
    init_runtime_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pid, status, detail FROM skill_runtime WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
    if row is None:
        return RuntimeStatus(skill_name, False, None, "stopped", "")
    pid = int(row["pid"]) if row["pid"] is not None else None
    running = _is_pid_running(pid)
    return RuntimeStatus(
        skill_name=skill_name,
        running=running,
        pid=pid if running else None,
        status=str(row["status"]),
        detail=str(row["detail"] or ""),
    )


def _save_runtime_status(
    skill_name: str,
    *,
    pid: int | None,
    status: str,
    detail: str,
    db_path: Path = STATE_DB_PATH,
) -> RuntimeStatus:
    init_runtime_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skill_runtime (skill_name, pid, status, detail, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(skill_name) DO UPDATE SET
                pid = excluded.pid,
                status = excluded.status,
                detail = excluded.detail,
                updated_at = excluded.updated_at
            """,
            (skill_name, pid, status, detail, now),
        )
    return RuntimeStatus(skill_name, bool(pid and _is_pid_running(pid)), pid, status, detail)


def start_skill_runtime(skill_name: str, db_path: Path = STATE_DB_PATH) -> RuntimeStatus:
    """Arranca procesos asociados a una skill activada."""
    if skill_name != "whatsapp_connector":
        return _save_runtime_status(
            skill_name,
            pid=None,
            status="enabled",
            detail="Skill habilitada; no requiere proceso en segundo plano.",
            db_path=db_path,
        )

    current = get_runtime_status(skill_name, db_path)
    if current.running:
        return current

    # Abrimos WhatsApp Web primero para que el usuario autentique por QR si no hay sesión.
    webbrowser.open("https://web.whatsapp.com")

    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PROGRESS_DIR / "whatsapp_webhook.log"
    log_file = log_path.open("a", encoding="utf-8")
    log_file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] starting webhook\n")
    log_file.flush()

    process = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "skills" / "whatsapp_connector.py")],
        cwd=str(REPO_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    detail = (
        "WhatsApp Web abierto para QR/login y webhook iniciado. "
        f"Log: {log_path}"
    )
    return _save_runtime_status(
        skill_name,
        pid=int(process.pid),
        status="running",
        detail=detail,
        db_path=db_path,
    )


def stop_skill_runtime(skill_name: str, db_path: Path = STATE_DB_PATH) -> RuntimeStatus:
    """Detiene el proceso en segundo plano de una skill."""
    current = get_runtime_status(skill_name, db_path)
    if current.pid and current.running:
        try:
            if sys.platform == "win32":
                os.kill(current.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(current.pid, signal.SIGTERM)
        except OSError:
            pass
    return _save_runtime_status(
        skill_name,
        pid=None,
        status="stopped",
        detail="Runtime detenido.",
        db_path=db_path,
    )
