from __future__ import annotations

import subprocess
from pathlib import Path

from harness.best_of_n import choose_best_patch, evaluate_patch_candidates


def test_best_of_n_prefers_applicable_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    bad_patch = "not a patch"
    good_patch = """diff --git a/demo.py b/demo.py
new file mode 100644
--- /dev/null
+++ b/demo.py
@@ -0,0 +1 @@
+VALUE = 1
"""

    results = evaluate_patch_candidates([bad_patch, good_patch], root=tmp_path)
    best = choose_best_patch([bad_patch, good_patch], root=tmp_path)

    assert results[0].index == 1
    assert best is not None
    assert best.ok


def test_best_of_n_uses_brt_to_break_ties(tmp_path: Path) -> None:
    """Cuando dos patches son aplicables y compilables, el que pasa los BRT gana."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, text=True, check=True)

    correct_patch = """diff --git a/answer.py b/answer.py
new file mode 100644
--- /dev/null
+++ b/answer.py
@@ -0,0 +1 @@
+ANSWER = 42
"""
    wrong_patch = """diff --git a/answer.py b/answer.py
new file mode 100644
--- /dev/null
+++ b/answer.py
@@ -0,0 +1 @@
+ANSWER = 7
"""

    brt_path = tmp_path / "brt_demo.py"
    brt_path.write_text(
        "def test_answer_is_42():\n"
        "    from answer import ANSWER\n"
        "    assert ANSWER == 42\n",
        encoding="utf-8",
    )

    results = evaluate_patch_candidates(
        [wrong_patch, correct_patch],
        root=tmp_path,
        brt_paths=[brt_path],
    )

    assert results, "no se evaluaron candidatos"
    assert results[0].brt_pass_rate == 1.0
    assert results[0].index == 1, f"se esperaba el patch correcto en top, results={results}"
    assert results[1].brt_pass_rate == 0.0
