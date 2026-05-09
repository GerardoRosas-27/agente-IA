from __future__ import annotations

import sys
import time

from skills.python_execution_environment import PythonExecutionEnvironment, run


def test_write_run_and_read_log(tmp_path):
    env = PythonExecutionEnvironment(
        workspace=tmp_path / "programs",
        log_dir=tmp_path / "logs",
        python_executable=sys.executable,
    )
    env.write_program("hello.py", "print('hola desde entorno')\n")

    finished = env.run_program("hello.py", timeout=10)

    assert finished.status == "completed"
    assert finished.returncode == 0
    assert "hola desde entorno" in env.read_log(finished.run_id)


def test_start_status_and_stop_long_running_program(tmp_path):
    env = PythonExecutionEnvironment(
        workspace=tmp_path / "programs",
        log_dir=tmp_path / "logs",
        python_executable=sys.executable,
    )
    env.write_program(
        "loop.py",
        "import time\n"
        "print('inicio', flush=True)\n"
        "while True:\n"
        "    time.sleep(0.1)\n",
    )

    started = env.start_program("loop.py")
    time.sleep(0.25)
    status = env.get_status(started.run_id)
    stopped = env.stop_program(started.run_id)

    assert status.status == "running"
    assert stopped.status == "stopped"
    assert "inicio" in env.read_log(started.run_id)


def test_structured_run_api_uses_default_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("skills.python_execution_environment.DEFAULT_WORKSPACE", tmp_path / "programs")
    monkeypatch.setattr("skills.python_execution_environment.DEFAULT_LOG_DIR", tmp_path / "logs")

    write_result = run({"action": "write", "name": "api_demo.py", "source": "print('api ok')"})
    run_result = run({"action": "run", "name": "api_demo.py", "timeout": 10})
    log_result = run({"action": "log", "run_id": run_result["run"]["run_id"]})

    assert write_result["ok"] is True
    assert run_result["ok"] is True
    assert "api ok" in log_result["log"]
