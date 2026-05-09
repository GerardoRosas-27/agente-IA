from __future__ import annotations

import shutil
import time

import pytest

from skills.node_execution_environment import NodeExecutionEnvironment, run


NODE = shutil.which("node")


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")


def test_write_run_and_read_log(tmp_path):
    assert NODE is not None
    env = NodeExecutionEnvironment(
        workspace=tmp_path / "scripts",
        log_dir=tmp_path / "logs",
        node_executable=NODE,
    )
    env.write_script("hello.js", "console.log('hola desde node');\n")

    finished = env.run_script("hello.js", timeout=10)

    assert finished.status == "completed"
    assert finished.returncode == 0
    assert "hola desde node" in env.read_log(finished.run_id)


def test_debug_session_exposes_inspector_and_can_stop(tmp_path):
    assert NODE is not None
    env = NodeExecutionEnvironment(
        workspace=tmp_path / "scripts",
        log_dir=tmp_path / "logs",
        node_executable=NODE,
    )
    env.write_script(
        "debug_loop.js",
        "console.log('debug script listo');\n"
        "setInterval(() => console.log('tick'), 1000);\n",
    )

    started = env.start_debug_session("debug_loop.js")
    time.sleep(0.5)
    status = env.get_status(started.run_id)
    stopped = env.stop_script(started.run_id)
    log_text = env.read_log(started.run_id)

    assert status.status == "running"
    assert status.debug is True
    assert "Debugger listening" in log_text
    assert status.inspector_url.startswith("ws://")
    assert stopped.status == "stopped"


def test_structured_run_api_uses_default_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("skills.node_execution_environment.DEFAULT_WORKSPACE", tmp_path / "scripts")
    monkeypatch.setattr("skills.node_execution_environment.DEFAULT_LOG_DIR", tmp_path / "logs")

    write_result = run({"action": "write", "name": "api_demo.js", "source": "console.log('node api ok')"})
    run_result = run({"action": "run", "name": "api_demo.js", "timeout": 10})
    log_result = run({"action": "log", "run_id": run_result["run"]["run_id"]})

    assert write_result["ok"] is True
    assert run_result["ok"] is True
    assert "node api ok" in log_result["log"]
