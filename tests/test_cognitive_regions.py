import tempfile
import unittest
from pathlib import Path


class TestCognitiveRegions(unittest.TestCase):
    def test_cognitive_system_context_and_learning(self) -> None:
        from cognitive_regions import CognitiveSystem

        with tempfile.TemporaryDirectory() as tmp:
            system = CognitiveSystem(Path(tmp) / "cognitive.sqlite", embed_dim=16)
            system.set_user_fact("riesgo", "prefiere aprobaciones para acciones high", confidence=0.8)
            system.update_goal("mejorar pruebas", priority=0.7)
            system.upsert_semantic_edge("probe.python", "sirve_para", "validar tests")
            before = system.context("validar tests")
            stats = system.learn_from_cycle(
                objective="validar tests",
                actors=["Entiende", "AgenteSkill:project-tests"],
                reached=True,
                cycles_used=1,
                tool_calls=[
                    {
                        "tool_name": "probe.python",
                        "allowed": True,
                        "error": "",
                        "risk": "low",
                        "duration_s": 0.2,
                    }
                ],
                final_answer="tests ok",
                created_skill=True,
            )
            after = system.context("validar tests")

        self.assertIn("Regiones cognitivas", before.text)
        self.assertIn("Control ejecutivo", before.text)
        self.assertIn("action", stats)
        self.assertIn("Memoria episodica", after.text)
        self.assertIn("Corteza semantica", after.text)

    def test_rest_cycle_worker_can_be_started_in_tests(self) -> None:
        from cognitive_regions import CognitiveSystem, start_rest_cycle_worker
        from persistent_memory import PersistentMemoryStore
        from skill_manager import SkillManager
        from task_runtime import TaskRuntime

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            logs = []
            thread = start_rest_cycle_worker(
                is_idle=lambda: False,
                on_log=lambda role, content: logs.append((role, content)),
                interval_s=60,
                cognitive_system=CognitiveSystem(db, embed_dim=16),
                runtime=TaskRuntime(db),
                memory_store=PersistentMemoryStore(Path(tmp) / "mem", db),
                skill_manager=SkillManager(Path(tmp) / "skills"),
            )

        self.assertTrue(thread.daemon)


if __name__ == "__main__":
    unittest.main()
