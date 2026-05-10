"""Persistencia de trayectorias completas de agentes de código."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness.paths import PROGRESS_DIR


TRAJECTORY_DIR = PROGRESS_DIR / "trajectories"


@dataclass(frozen=True)
class TrajectoryStep:
    action: str
    observation: str
    success: bool
    score: float
    created_at: float


@dataclass
class AgentTrajectory:
    trajectory_id: str
    goal: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    patches: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    verdict: str = "in_progress"
    reflection: str = ""

    def add_step(
        self,
        action: str,
        observation: str,
        *,
        success: bool,
        score: float = 0.0,
    ) -> None:
        self.steps.append(
            TrajectoryStep(
                action=action,
                observation=observation[-12000:],
                success=success,
                score=float(score),
                created_at=time.time(),
            )
        )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:80].strip("_")
    return slug or f"trajectory_{int(time.time() * 1000)}"


def start_trajectory(goal: str, trajectory_id: str = "") -> AgentTrajectory:
    return AgentTrajectory(trajectory_id=_slug(trajectory_id or goal), goal=goal)


def trajectory_path(trajectory_id: str, *, base_dir: Path = TRAJECTORY_DIR) -> Path:
    return base_dir / f"{_slug(trajectory_id)}.json"


def save_trajectory(trajectory: AgentTrajectory, *, base_dir: Path = TRAJECTORY_DIR) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    path = trajectory_path(trajectory.trajectory_id, base_dir=base_dir)
    path.write_text(json.dumps(asdict(trajectory), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_trajectory(trajectory_id: str, *, base_dir: Path = TRAJECTORY_DIR) -> AgentTrajectory:
    raw = json.loads(trajectory_path(trajectory_id, base_dir=base_dir).read_text(encoding="utf-8"))
    trajectory = AgentTrajectory(
        trajectory_id=str(raw["trajectory_id"]),
        goal=str(raw["goal"]),
        patches=[str(item) for item in raw.get("patches", [])],
        tests=[str(item) for item in raw.get("tests", [])],
        verdict=str(raw.get("verdict") or "in_progress"),
        reflection=str(raw.get("reflection") or ""),
    )
    for item in raw.get("steps", []):
        if isinstance(item, dict):
            trajectory.steps.append(
                TrajectoryStep(
                    action=str(item.get("action") or ""),
                    observation=str(item.get("observation") or ""),
                    success=bool(item.get("success")),
                    score=float(item.get("score") or 0.0),
                    created_at=float(item.get("created_at") or 0.0),
                )
            )
    return trajectory


def trajectory_summary(trajectory: AgentTrajectory) -> str:
    successes = sum(1 for step in trajectory.steps if step.success)
    failures = len(trajectory.steps) - successes
    return (
        f"Trajectory `{trajectory.trajectory_id}` goal={trajectory.goal!r} "
        f"verdict={trajectory.verdict} steps={len(trajectory.steps)} "
        f"successes={successes} failures={failures}"
    )
