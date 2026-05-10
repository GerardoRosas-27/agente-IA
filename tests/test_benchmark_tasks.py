from __future__ import annotations

import subprocess
from pathlib import Path

from harness.benchmark_tasks import CodeBenchmarkTask, load_benchmark_tasks, run_code_benchmark_tasks, save_benchmark_task


def test_benchmark_task_roundtrip_and_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("harness.trajectories.TRAJECTORY_DIR", tmp_path / "trajectories")
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    tasks_dir = tmp_path / "tasks"
    patch = """diff --git a/demo.py b/demo.py
new file mode 100644
--- /dev/null
+++ b/demo.py
@@ -0,0 +1 @@
+VALUE = 1
"""
    task = CodeBenchmarkTask("demo", "crear demo", patch)

    save_benchmark_task(task, tasks_dir=tasks_dir)
    loaded = load_benchmark_tasks(tasks_dir=tasks_dir)
    results = run_code_benchmark_tasks(root=tmp_path, tasks_dir=tasks_dir)

    assert loaded[0].task_id == "demo"
    assert results[0].passed
