from __future__ import annotations

from pathlib import Path

from harness import skill_runtime


def test_non_process_skill_runtime_records_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"

    status = skill_runtime.start_skill_runtime("demo_tool", db_path=db_path)

    assert status.skill_name == "demo_tool"
    assert status.running is False
    assert status.status == "enabled"


def test_whatsapp_runtime_starts_process(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(skill_runtime, "PROGRESS_DIR", tmp_path / "progress")
    opened_urls = []
    monkeypatch.setattr(skill_runtime.webbrowser, "open", opened_urls.append)

    class FakeProcess:
        pid = 12345

    monkeypatch.setattr(
        skill_runtime.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(skill_runtime.os, "kill", lambda *_args, **_kwargs: None)

    status = skill_runtime.start_skill_runtime("whatsapp_connector", db_path=db_path)

    assert opened_urls == ["https://web.whatsapp.com"]
    assert status.pid == 12345
    assert status.status == "running"
    assert skill_runtime.get_runtime_status("whatsapp_connector", db_path=db_path).running


def test_stop_skill_runtime_marks_stopped(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(skill_runtime.os, "kill", lambda *_args, **_kwargs: None)
    skill_runtime._save_runtime_status(
        "whatsapp_connector",
        pid=12345,
        status="running",
        detail="test",
        db_path=db_path,
    )

    stopped = skill_runtime.stop_skill_runtime("whatsapp_connector", db_path=db_path)

    assert stopped.running is False
    assert stopped.status == "stopped"
