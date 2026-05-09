from __future__ import annotations

from harness.shared_memory import recall, skill_memory_context
from harness.tool_learning import recommend_tools_for_goal
from harness.training_dataset import generate_tool_routing_dataset, generate_user_task_dataset, train_from_dataset


def test_generate_tool_routing_dataset_creates_jsonl(tmp_path):
    path = tmp_path / "dataset.jsonl"

    stats = generate_tool_routing_dataset(output_path=path, target_tokens=1200, seed=1)

    assert path.is_file()
    assert stats.examples > 0
    assert stats.approx_tokens >= 1200
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert "selected_skill" in first_line
    assert "subtasks" in first_line


def test_train_from_dataset_updates_skill_usage(tmp_path):
    path = tmp_path / "dataset.jsonl"
    db_path = tmp_path / "state.db"
    generate_tool_routing_dataset(output_path=path, target_tokens=1500, seed=2)

    stats = train_from_dataset(dataset_path=path, max_examples=10, db_path=db_path)

    assert stats.trained_examples == 10
    assert stats.trained_skills
    memory = skill_memory_context(limit=20, db_path=db_path)
    assert "dataset_training_success" in memory


def test_dataset_training_improves_recommendations(tmp_path):
    path = tmp_path / "dataset.jsonl"
    db_path = tmp_path / "state.db"
    generate_tool_routing_dataset(output_path=path, target_tokens=3000, seed=3)
    train_from_dataset(dataset_path=path, max_examples=40, db_path=db_path)

    recommendations = recommend_tools_for_goal("debuggear un script con inspector node", db_path=db_path)

    assert recommendations
    assert recommendations[0].skill_name == "node_execution_environment"


def test_generate_user_task_dataset_creates_requested_task_examples(tmp_path):
    path = tmp_path / "user_tasks.jsonl"

    stats = generate_user_task_dataset(output_path=path, examples=25, seed=4)

    assert stats.examples == 25
    text = path.read_text(encoding="utf-8")
    assert "user_request" in text
    assert "guardar la sesión como entrenamiento" in text


def test_user_task_dataset_can_train_router(tmp_path):
    path = tmp_path / "user_tasks.jsonl"
    db_path = tmp_path / "state.db"
    generate_user_task_dataset(output_path=path, examples=80, seed=5)

    stats = train_from_dataset(dataset_path=path, db_path=db_path)

    assert stats.trained_examples == 80
    memories = recall(scope="auto_training_dataset", db_path=db_path)
    assert memories
