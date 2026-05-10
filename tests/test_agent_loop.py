from __future__ import annotations

from pathlib import Path

from harness.agent_loop import execute_tool_action, run_agent_loop


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

    state = run_agent_loop("buscar contexto", llm_call=lambda _system, _prompt: next(actions), max_steps=3)

    assert state.done
    assert [item.action for item in state.observations] == ["search", "done"]
