from __future__ import annotations

import json
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
        ):
            res = orchestrator.run_one_feature_cycle(model="m")

        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.verdict, True)
        data = json.loads(self.fl.read_text(encoding="utf-8"))
        self.assertEqual(data["features"][0]["status"], "done")
        self.assertTrue((prog / "impl_demo_feature.md").is_file())
        self.assertTrue((prog / "review_demo_feature.md").is_file())


if __name__ == "__main__":
    unittest.main()
