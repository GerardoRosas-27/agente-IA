from __future__ import annotations

from pathlib import Path

from harness.reflection import build_failure_reflection, reflect_on_trajectory
from harness.shared_memory import recall
from harness.trajectories import load_trajectory, save_trajectory, start_trajectory, trajectory_summary


def test_trajectory_roundtrip_and_summary(tmp_path: Path) -> None:
    trajectory = start_trajectory("arreglar bug", "demo")
    trajectory.add_step("localize", "ok", success=True, score=1.0)

    save_trajectory(trajectory, base_dir=tmp_path)
    loaded = load_trajectory("demo", base_dir=tmp_path)

    assert loaded.goal == "arreglar bug"
    assert loaded.steps[0].action == "localize"
    assert "steps=1" in trajectory_summary(loaded)


def test_failure_reflection_is_actionable_and_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    trajectory = start_trajectory("arreglar imports", "imports")
    trajectory.add_step("test", "ModuleNotFoundError: missing package", success=False, score=-1)

    reflection = reflect_on_trajectory(trajectory, db_path=db_path)

    assert "verificar imports" in reflection
    assert recall(scope="failure_reflection", db_path=db_path)


def test_build_failure_reflection_handles_patch_failures() -> None:
    reflection = build_failure_reflection("aplicar cambio", "patch does not apply", last_action="patch")

    assert "previsualizar" in reflection
