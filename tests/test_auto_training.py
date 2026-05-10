from __future__ import annotations

from pathlib import Path

from harness.auto_training import (
    build_training_plan,
    consolidate_runtime_learning,
    latest_training_context,
    record_agent_observation,
    record_user_session_training,
    run_training_cycle,
)
from harness.shared_memory import recall
from harness.tool_learning import recommend_tools_for_goal


def test_build_training_plan_reuses_existing_skill(tmp_path):
    db_path = tmp_path / "state.db"

    plan = build_training_plan("buscar información actualizada en internet", db_path=db_path)

    assert plan.decision == "REUSE_EXISTING_SKILL"
    assert "internet_search_api" in plan.tools_to_use
    assert "Solución" not in plan.code_solution
    assert "import_module('skills.internet_search_api')" in plan.code_solution


def test_run_training_cycle_persists_memory_and_report(tmp_path):
    db_path = tmp_path / "state.db"
    progress_dir = tmp_path / "progress"

    result = run_training_cycle(
        "buscar resultados web para una tarea",
        db_path=db_path,
        progress_dir=progress_dir,
    )

    assert result.task.task_id.startswith("train_")
    assert Path(result.report_path).is_file()
    memories = recall(scope="auto_training", db_path=db_path)
    assert len(memories) == 1
    assert result.memory_key == memories[0].key


def test_training_cycle_updates_runtime_skill_learning(tmp_path):
    db_path = tmp_path / "state.db"

    run_training_cycle("buscar información en internet", db_path=db_path, progress_dir=tmp_path)
    recommendations = recommend_tools_for_goal("buscar información en internet", db_path=db_path)

    assert recommendations
    assert recommendations[0].skill_name == "internet_search_api"
    assert "buscar información en internet" in recommendations[0].known_use_cases


def test_latest_training_context_lists_recent_cycles(tmp_path):
    db_path = tmp_path / "state.db"
    run_training_cycle("calcular una suma simple", db_path=db_path, progress_dir=tmp_path)

    context = latest_training_context(db_path=db_path)

    assert "train_" in context
    assert "calcular una suma simple" in context


def test_record_user_session_training_persists_session_and_skill_usage(tmp_path):
    db_path = tmp_path / "state.db"

    record_user_session_training(
        "buscar resultados web para un usuario",
        outcome="session_completed",
        db_path=db_path,
    )

    memories = recall(scope="user_session_training", db_path=db_path)
    recommendations = recommend_tools_for_goal("buscar resultados web para un usuario", db_path=db_path)

    assert memories
    assert recommendations
    assert recommendations[0].skill_name == "internet_search_api"


def test_record_agent_observation_and_consolidate_runtime_learning(tmp_path):
    db_path = tmp_path / "state.db"

    record_agent_observation(
        "arreglar tests",
        "test",
        ok=True,
        content="Exit code: 0\n2 passed",
        db_path=db_path,
    )
    summary = consolidate_runtime_learning(db_path=db_path)
    memories = recall(scope="runtime_learning_summary", db_path=db_path)

    assert "Patrones consolidados" in summary
    assert "test_runner/test" in summary
    assert memories
