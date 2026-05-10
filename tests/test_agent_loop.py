from __future__ import annotations

import subprocess
from pathlib import Path

from harness.agent_loop import (
    AgentLoopState,
    execute_tool_action,
    load_agent_session,
    run_agent_loop,
    save_agent_session,
)


def test_agent_loop_reads_safe_file(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("hola", encoding="utf-8")

    observation = execute_tool_action({"action": "read", "path": "demo.txt"}, root=tmp_path)

    assert observation.ok
    assert observation.content == "hola"


def test_agent_loop_blocks_path_escape(tmp_path: Path) -> None:
    observation = execute_tool_action({"action": "read", "path": "../secret.txt"}, root=tmp_path)

    assert not observation.ok
    assert "fuera" in observation.content


def test_agent_loop_runs_until_done() -> None:
    actions = iter([
        '{"action":"search","query":"whatsapp"}',
        '{"action":"done","summary":"listo"}',
    ])

    state = run_agent_loop(
        "buscar contexto",
        llm_call=lambda _system, _prompt: next(actions),
        max_steps=3,
        autosave=False,
    )

    assert state.done
    assert [item.action for item in state.observations] == ["search", "done"]


def test_agent_loop_session_roundtrip(tmp_path: Path) -> None:
    state = AgentLoopState(goal="hacer algo", session_id="demo")
    state.add("search", True, "contexto")

    path = save_agent_session(state, base_dir=tmp_path)
    loaded = load_agent_session("demo", base_dir=tmp_path)

    assert path.is_file()
    assert loaded.goal == "hacer algo"
    assert loaded.observations[0].content == "contexto"


def test_patch_action_previews_before_apply(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    patch = """diff --git a/demo.txt b/demo.txt
new file mode 100644
--- /dev/null
+++ b/demo.txt
@@ -0,0 +1 @@
+hola
"""

    preview = execute_tool_action({"action": "patch", "patch": patch}, root=tmp_path)

    assert preview.action == "patch_preview"
    assert preview.ok
    assert not (tmp_path / "demo.txt").exists()


def test_patch_action_applies_with_checkpoint(tmp_path: Path, monkeypatch) -> None:
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    checkpoint_root = tmp_path / "progress"
    monkeypatch.setattr("harness.agent_loop.PROGRESS_DIR", checkpoint_root)
    patch = """diff --git a/demo.txt b/demo.txt
new file mode 100644
--- /dev/null
+++ b/demo.txt
@@ -0,0 +1 @@
+hola
"""

    applied = execute_tool_action({"action": "patch", "patch": patch, "apply": True}, root=tmp_path)

    assert applied.ok
    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "hola\n"
    assert "Checkpoint:" in applied.content
    assert list((checkpoint_root / "agent_sessions" / "checkpoints").glob("*.patch"))
