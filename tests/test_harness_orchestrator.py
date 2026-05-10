from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import orchestrator
from harness.feature_store import USER_TASK_ORIGIN, save_feature_list


class TestParseVerdict(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertIs(orchestrator.parse_verdict("VERDICT: PASS\n\nok"), True)

    def test_fail(self) -> None:
        self.assertIs(orchestrator.parse_verdict("VERDICT: FAIL\n\nx"), False)

    def test_ambiguous(self) -> None:
        self.assertIsNone(orchestrator.parse_verdict("No verdict here"))


class TestHarnessSafetyHelpers(unittest.TestCase):
    def test_apply_code_blocks_rejects_paths_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = """```python:skills/demo.py
VALUE = 1
```

```python:../outside.py
VALUE = 2
```"""
            with patch.object(orchestrator, "_repo_root", return_value=root):
                result = orchestrator._apply_code_blocks(body)

            self.assertEqual(result.saved_files, ["skills/demo.py"])
            self.assertTrue((root / "skills" / "demo.py").is_file())
            self.assertIn("fuera del repositorio", result.rejected[0])

    def test_apply_code_blocks_supports_unified_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=str(root), capture_output=True, text=True, check=True)
            body = """```patch
diff --git a/demo.txt b/demo.txt
new file mode 100644
--- /dev/null
+++ b/demo.txt
@@ -0,0 +1 @@
+hello
```"""
            with patch.object(orchestrator, "_repo_root", return_value=root):
                result = orchestrator._apply_code_blocks(body)

            self.assertEqual(result.patch_files, ["demo.txt"])
            self.assertEqual((root / "demo.txt").read_text(encoding="utf-8"), "hello\n")

    def test_run_bash_blocks_blocks_dangerous_commands(self) -> None:
        logs: list[str] = []
        ok, report = orchestrator._run_bash_blocks(
            "```bash\ngit reset --hard\n```",
            logs.append,
        )

        self.assertFalse(ok)
        self.assertIn("BLOQUEADO", report)
        self.assertTrue(any("BLOQUEADO" in item for item in logs))

    def test_related_test_paths_prefers_changed_tests_and_module_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("def test_x(): pass\n", encoding="utf-8")
            with patch.object(orchestrator, "_repo_root", return_value=root):
                related = orchestrator._related_test_paths(["skills/demo.py", "tests/test_demo.py"])

            self.assertEqual(related, ["tests/test_demo.py"])


class TestRunOneFeatureCycle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fl = self.tmp / "feature_list.json"
        save_feature_list(
            self.fl,
            {
                "features": [
                    {
                        "id": 1,
                        "name": "demo_feature",
                        "title": "Demo",
                        "description": "test",
                        "acceptance": ["criterio"],
                        "origin": USER_TASK_ORIGIN,
                        "status": "pending",
                    }
                ]
            },
        )
        (self.tmp / "AGENTS.md").write_text("# A\n", encoding="utf-8")
        (self.tmp / "CHECKPOINTS.md").write_text("# C\n", encoding="utf-8")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "architecture.md").write_text("arch", encoding="utf-8")
        (self.tmp / "docs" / "conventions.md").write_text("conv", encoding="utf-8")
        (self.tmp / "docs" / "verification.md").write_text("ver", encoding="utf-8")
        (self.tmp / "progress").mkdir()
        (self.tmp / "progress" / "current.md").write_text("# x\n", encoding="utf-8")
        (self.tmp / "progress" / "history.md").write_text("# h\n", encoding="utf-8")

        self._calls = 0

        def fake_invoke(*_a, **_k):
            self._calls += 1
            if self._calls == 1:
                return "plan del líder"
            if self._calls == 2:
                return "## Resumen\nhecho"
            return "VERDICT: PASS\n\nlisto"

        self._fake_invoke = fake_invoke

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_marks_done_on_pass(self) -> None:
        prog = self.tmp / "progress"
        with (
            patch.object(orchestrator, "FEATURE_LIST_PATH", self.fl),
            patch.object(orchestrator, "PROGRESS_DIR", prog),
            patch.object(orchestrator, "AGENTS_MD", self.tmp / "AGENTS.md"),
            patch.object(orchestrator, "CHECKPOINTS_MD", self.tmp / "CHECKPOINTS.md"),
            patch.object(orchestrator, "DOCS_DIR", self.tmp / "docs"),
            patch.object(orchestrator, "invoke_llm", self._fake_invoke),
            patch.object(orchestrator, "_run_tests", return_value="tests ok"),
            patch.object(orchestrator, "_build_repo_context", return_value="repo ctx"),
        ):
            res = orchestrator.run_one_feature_cycle(model="m")

        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.verdict, True)
        data = json.loads(self.fl.read_text(encoding="utf-8"))
        self.assertEqual(data["features"][0]["status"], "done")
        self.assertTrue((prog / "impl_demo_feature.md").is_file())
        self.assertTrue((prog / "review_demo_feature.md").is_file())

    def test_adversarial_reviewer_overrides_pass_when_disagrees(self) -> None:
        """E1: si el reviewer principal dice PASS pero el red team dice FAIL,
        el ciclo debe terminar en FAIL (no hay consenso)."""
        prog = self.tmp / "progress"

        call_count = {"n": 0}

        def fake_invoke(*_a, **kwargs):
            call_count["n"] += 1
            role = str(kwargs.get("role_hint") or "")
            # Orden esperado: leader, implementer, reviewer, adversarial
            if "leader" in role:
                return "plan del líder"
            if "implementer" in role:
                return "## Resumen\nhecho"
            if "adversarial" in role:
                return "VERDICT: FAIL\n- el patch no cubre el criterio X."
            # reviewer principal
            return "VERDICT: PASS\n\nlisto"

        with (
            patch.object(orchestrator, "FEATURE_LIST_PATH", self.fl),
            patch.object(orchestrator, "PROGRESS_DIR", prog),
            patch.object(orchestrator, "AGENTS_MD", self.tmp / "AGENTS.md"),
            patch.object(orchestrator, "CHECKPOINTS_MD", self.tmp / "CHECKPOINTS.md"),
            patch.object(orchestrator, "DOCS_DIR", self.tmp / "docs"),
            patch.object(orchestrator, "invoke_llm", fake_invoke),
            patch.object(orchestrator, "_run_tests", return_value="tests ok"),
            patch.object(orchestrator, "_build_repo_context", return_value="repo ctx"),
        ):
            res = orchestrator.run_one_feature_cycle(
                model="m",
                max_retries=0,
                enable_adversarial_review=True,
            )

        assert res is not None
        self.assertEqual(res.verdict, False)
        self.assertTrue((prog / "red_review_demo_feature.md").is_file())

    def test_verifier_overrides_llm_pass_when_tests_fail(self) -> None:
        prog = self.tmp / "progress"
        with (
            patch.object(orchestrator, "FEATURE_LIST_PATH", self.fl),
            patch.object(orchestrator, "PROGRESS_DIR", prog),
            patch.object(orchestrator, "AGENTS_MD", self.tmp / "AGENTS.md"),
            patch.object(orchestrator, "CHECKPOINTS_MD", self.tmp / "CHECKPOINTS.md"),
            patch.object(orchestrator, "DOCS_DIR", self.tmp / "docs"),
            patch.object(orchestrator, "invoke_llm", self._fake_invoke),
            patch.object(
                orchestrator,
                "_run_tests",
                return_value="Exit code: 1\n3 passed, 5 failed in 0.5s",
            ),
            patch.object(orchestrator, "_build_repo_context", return_value="repo ctx"),
        ):
            res = orchestrator.run_one_feature_cycle(
                model="m",
                max_retries=0,
                enable_verifier=True,
            )

        assert res is not None
        # El LLM fake siempre devuelve VERDICT: PASS, pero el verifier ve
        # 5 failed y exit=1 → debe sobreescribir a FAIL.
        self.assertEqual(res.verdict, False)
        self.assertTrue((prog / "verify_demo_feature.md").is_file())


if __name__ == "__main__":
    unittest.main()
