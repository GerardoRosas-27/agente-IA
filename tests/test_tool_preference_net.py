import tempfile
import unittest
from pathlib import Path


class TestToolPreferenceNet(unittest.TestCase):
    def test_reward_penalizes_blocked_or_error_calls(self) -> None:
        from tool_preference_net import ToolPreferenceStore

        good = ToolPreferenceStore.reward_for_call(
            {
                "allowed": True,
                "output": "ok",
                "error": "",
                "duration_s": 0.2,
                "risk": "low",
            },
            reached=True,
        )
        bad = ToolPreferenceStore.reward_for_call(
            {
                "allowed": False,
                "output": "",
                "error": "blocked",
                "duration_s": 0.2,
                "risk": "high",
            },
            reached=False,
        )

        self.assertGreater(good, bad)

    def test_train_rank_and_persist_tool_preferences(self) -> None:
        from tool_preference_net import ToolPreferenceStore

        calls = [
            {
                "id": 1,
                "tool_name": "probe.python",
                "risk": "low",
                "allowed": True,
                "decision": "permitida",
                "output": "tests ok",
                "error": "",
                "duration_s": 0.4,
            },
            {
                "id": 2,
                "tool_name": "terminal.run",
                "risk": "high",
                "allowed": False,
                "decision": "bloqueada",
                "output": "",
                "error": "",
                "duration_s": 0.0,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "pref.sqlite"
            store = ToolPreferenceStore(db, embed_dim=16)
            loss, stats = store.train_from_calls(
                "validar proyecto python",
                calls,
                reached=True,
                steps=2,
            )
            ranked = store.rank_tools(
                "validar proyecto python",
                [("probe.python", "low"), ("terminal.run", "high")],
            )
            reloaded = ToolPreferenceStore(db, embed_dim=16)
            ctx = reloaded.context(
                "validar proyecto python",
                [("probe.python", "low"), ("terminal.run", "high")],
            )

        self.assertIsInstance(loss, float)
        self.assertIn("free_energy", stats)
        self.assertEqual(len(ranked), 2)
        self.assertIn("Preferencias neuronales", ctx)
        self.assertIn("probe.python", ctx)


if __name__ == "__main__":
    unittest.main()
