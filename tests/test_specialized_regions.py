import tempfile
import unittest
from pathlib import Path


class TestSpecializedRegions(unittest.TestCase):
    def test_predict_train_and_persist_regions(self) -> None:
        from specialized_regions import SpecializedRegionStore

        calls = [
            {
                "tool_name": "probe.python",
                "risk": "low",
                "allowed": True,
                "error": "",
                "duration_s": 0.2,
            },
            {
                "tool_name": "terminal.run",
                "risk": "high",
                "allowed": False,
                "error": "",
                "duration_s": 0.0,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "regions.sqlite"
            store = SpecializedRegionStore(db, embed_dim=16)
            ctx_before = store.context("validar proyecto", "usar pruebas")
            stats = store.train_from_cycle(
                objective="validar proyecto",
                context="se usaron pruebas y una herramienta bloqueada",
                reached=True,
                cycles_used=1,
                tool_calls=calls,
                created_skill=True,
                used_skill=True,
                steps=2,
            )
            reloaded = SpecializedRegionStore(db, embed_dim=16)
            ctx_after = reloaded.context("validar proyecto", "usar pruebas")

        self.assertIn("GoalCriticNet", ctx_before)
        self.assertIn("goal_critic", stats)
        self.assertIn("PlannerPolicyNet", ctx_after)
        self.assertIn("SafetyRiskNet", ctx_after)

    def test_region_targets_include_all_regions(self) -> None:
        from specialized_regions import SpecializedRegionStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SpecializedRegionStore(Path(tmp) / "regions.sqlite", embed_dim=16)
            targets = store.targets_from_cycle(
                reached=False,
                cycles_used=2,
                tool_calls=[],
                created_skill=False,
                used_skill=False,
            )

        self.assertEqual(
            set(targets),
            {
                "goal_critic",
                "planner_policy",
                "skill_composer",
                "safety_risk",
                "attention_router",
            },
        )


if __name__ == "__main__":
    unittest.main()
