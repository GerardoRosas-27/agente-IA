from __future__ import annotations

import subprocess
from pathlib import Path

from harness.coding_pipeline import localize_issue, run_localize_patch_validate, validate_patch_candidate


def test_localize_issue_finds_related_file(tmp_path: Path) -> None:
    (tmp_path / "webhook.py").write_text("def handle_webhook():\n    pass\n", encoding="utf-8")

    result = localize_issue("arreglar webhook", root=tmp_path)

    assert result.files == ("webhook.py",)
    assert result.symbols
    assert "Símbolos/líneas" in result.rationale


def test_pipeline_applies_patch_and_validates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("harness.trajectories.TRAJECTORY_DIR", tmp_path / "trajectories")
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    patch = """diff --git a/demo.py b/demo.py
new file mode 100644
--- /dev/null
+++ b/demo.py
@@ -0,0 +1 @@
+VALUE = 1
"""

    result = run_localize_patch_validate("crear demo", root=tmp_path, patch_text=patch)

    assert result.ok
    assert result.validation is not None
    assert result.validation.changed_files == ("demo.py",)
    assert (tmp_path / "demo.py").is_file()


def test_validate_patch_candidate_rolls_back_on_failed_evaluation(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    patch = """diff --git a/broken.py b/broken.py
new file mode 100644
--- /dev/null
+++ b/broken.py
@@ -0,0 +1 @@
+def broken(:
"""

    result = validate_patch_candidate(patch, root=tmp_path, auto_rollback=True)

    assert result.applied
    assert not result.evaluation.ok
    assert "Rollback automático" in result.report
    assert not (tmp_path / "broken.py").exists()
