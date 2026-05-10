"""Benchmarks propios estilo SWE-bench para el harness."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.coding_pipeline import run_localize_patch_validate
from harness.paths import PROGRESS_DIR, REPO_ROOT


TASKS_DIR = PROGRESS_DIR / "benchmark_tasks"


@dataclass(frozen=True)
class CodeBenchmarkTask:
    task_id: str
    goal: str
    patch: str
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodeBenchmarkResult:
    task_id: str
    passed: bool
    report: str


def save_benchmark_task(task: CodeBenchmarkTask, *, tasks_dir: Path = TASKS_DIR) -> Path:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / f"{task.task_id}.json"
    path.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_benchmark_tasks(*, tasks_dir: Path = TASKS_DIR) -> list[CodeBenchmarkTask]:
    if not tasks_dir.is_dir():
        return []
    tasks: list[CodeBenchmarkTask] = []
    for path in sorted(tasks_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(
            CodeBenchmarkTask(
                task_id=str(raw["task_id"]),
                goal=str(raw["goal"]),
                patch=str(raw["patch"]),
                commands=tuple(raw.get("commands") or ()),
            )
        )
    return tasks


def run_code_benchmark_tasks(
    *,
    root: Path = REPO_ROOT,
    tasks_dir: Path = TASKS_DIR,
) -> list[CodeBenchmarkResult]:
    results: list[CodeBenchmarkResult] = []
    for task in load_benchmark_tasks(tasks_dir=tasks_dir):
        result = run_localize_patch_validate(
            task.goal,
            root=root,
            patch_text=task.patch,
            extra_commands=list(task.commands),
            trajectory_id=f"benchmark_{task.task_id}",
        )
        results.append(
            CodeBenchmarkResult(
                task_id=task.task_id,
                passed=result.ok,
                report=(result.validation.report if result.validation else result.localization.rationale),
            )
        )
    return results
