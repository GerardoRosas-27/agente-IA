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
