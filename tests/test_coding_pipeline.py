from __future__ import annotations

import subprocess
from pathlib import Path

from harness.coding_pipeline import localize_issue, run_localize_patch_validate, validate_patch_candidate
from harness.repo_index import build_repo_index, search_repo_index


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


def test_patch_validation_invalidates_repo_index_memory_cache(tmp_path: Path) -> None:
    """Después de aplicar un patch, búsquedas posteriores no deben ver índice stale."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    (tmp_path / "seed.py").write_text("def seed_symbol():\n    return 1\n", encoding="utf-8")
    # Poblar la caché en memoria antes de aplicar el patch.
    assert [entry.path for entry in build_repo_index(root=tmp_path)]

    patch = """diff --git a/new_module.py b/new_module.py
new file mode 100644
--- /dev/null
+++ b/new_module.py
@@ -0,0 +1,2 @@
+def unique_new_symbol():
+    return 42
"""

    result = validate_patch_candidate(patch, root=tmp_path, auto_rollback=False)

    assert result.applied
    matches = search_repo_index("unique_new_symbol", root=tmp_path)
    assert matches
    assert matches[0].path == "new_module.py"
