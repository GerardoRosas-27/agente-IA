import tempfile
import unittest
from pathlib import Path


class TestPersistentLearning(unittest.TestCase):
    def test_memory_store_applies_actions_and_searches_sessions(self) -> None:
        from persistent_memory import (
            PersistentMemoryStore,
            parse_memory_curator_response,
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(
                Path(tmp) / "memories",
                Path(tmp) / "state.sqlite",
            )
            actions = parse_memory_curator_response(
                '{"acciones": ['
                '{"target": "memory", "action": "add", "content": "Usar unittest para validar regresiones.", "reason": "tests"},'
                '{"target": "user", "action": "add", "content": "El usuario prefiere modo autonomo opcional.", "reason": "preferencia"}'
                "]}"
            )
            applied = store.apply_actions(actions)
            store.record_session(
                objective="validar regresiones",
                summary="Se ejecutaron pruebas unittest con resultado correcto.",
                outcome="succeeded",
            )
            hot = store.hot_context()
            found = store.search_sessions("regresiones unittest")

        self.assertIn("memory:add", applied)
        self.assertIn("user:add", applied)
        self.assertIn("unittest", hot)
        self.assertIn("regresiones", found)

    def test_curator_redacts_secret_like_content(self) -> None:
        from persistent_memory import parse_memory_curator_response

        actions = parse_memory_curator_response(
            '{"acciones": [{"target": "memory", "action": "add", '
            '"content": "api_key=SECRET123 usar servicio", "reason": "redactar"}]}'
        )

        self.assertEqual(len(actions), 1)
        self.assertIn("<redacted>", actions[0].content)
        self.assertNotIn("SECRET123", actions[0].content)

    def test_skill_manager_writes_playbook_and_examples(self) -> None:
        from skill_manager import SkillManager, parse_skill_learning_response

        with tempfile.TemporaryDirectory() as tmp:
            manager = SkillManager(Path(tmp) / "skills")
            items = parse_skill_learning_response(
                '{"skills": [{"name": "Validar Proyecto", '
                '"description": "Validar cambios Python con unittest", '
                '"triggers": ["tests", "unittest"], '
                '"instructions": "Ejecutar la suite y revisar errores antes de responder.", '
                '"evidence": "La suite pasó correctamente.", '
                '"risk": "high", "executor": "terminal_command", '
                '"command": "python -m unittest discover -s tests", "cwd": "."}]}'
            )
            path = manager.upsert_learned_skill(**items[0])
            manifest = (path / "manifest.json").read_text(encoding="utf-8")
            readme = (path / "README.md").read_text(encoding="utf-8")
            examples = (path / "examples.json").read_text(encoding="utf-8")

        self.assertIn("validar-proyecto", str(path))
        self.assertIn('"confidence"', manifest)
        self.assertIn("## Instrucciones", readme)
        self.assertIn("La suite pasó", examples)

    def test_tool_library_confidence_changes_with_success_and_failure(self) -> None:
        from tool_library import ToolLibrary, ToolMemory

        with tempfile.TemporaryDirectory() as tmp:
            lib = ToolLibrary(Path(tmp) / "tools.sqlite", embed_dim=16)
            tool = ToolMemory(
                name="demo",
                kind="procedimiento",
                objective="demo",
                trigger_terms="demo",
                entrypoint="demo",
                instructions="usar demo",
                evidence="ok",
                confidence=0.5,
            )
            lib.upsert(tool)
            first = lib.list_entries()[0].confidence
            lib.upsert(tool)
            second = lib.list_entries()[0].confidence
            lib.mark_failure("demo", "demo")
            third = lib.list_entries()[0].confidence

        self.assertGreaterEqual(second, first)
        self.assertLess(third, second)


if __name__ == "__main__":
    unittest.main()
