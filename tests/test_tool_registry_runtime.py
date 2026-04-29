import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestTaskRuntimeAndToolRegistry(unittest.TestCase):
    def test_registry_executes_allowed_tool_and_records_call(self) -> None:
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("sumar numeros")
            registry = ToolRegistry()
            registry.register(
                RegisteredTool(
                    name="math.add",
                    description="Suma dos enteros",
                    risk="low",
                    input_schema={"a": "int", "b": "int"},
                    handler=lambda a, b: a + b,
                )
            )

            result = registry.call(
                "math.add",
                {"a": 2, "b": 3},
                runtime=runtime,
                task_id=task_id,
            )
            calls = runtime.recent_tool_calls(task_id)

        self.assertEqual(result, 5)
        self.assertEqual(calls[0]["tool_name"], "math.add")
        self.assertTrue(calls[0]["allowed"])
        self.assertIn("5", calls[0]["output"])

    def test_registry_blocks_high_risk_by_default_and_records_decision(self) -> None:
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("accion riesgosa")
            registry = ToolRegistry()
            registry.register(
                RegisteredTool(
                    name="danger.demo",
                    description="Herramienta riesgosa",
                    risk="high",
                    input_schema={},
                    handler=lambda: "no debe ejecutarse",
                )
            )

            with patch.dict("os.environ", {"TOOL_REGISTRY_MAX_AUTO_RISK": "moderate"}, clear=False):
                result = registry.call("danger.demo", {}, runtime=runtime, task_id=task_id)
            calls = runtime.recent_tool_calls(task_id)

        self.assertIsNone(result)
        self.assertFalse(calls[0]["allowed"])
        self.assertIn("requiere aprobacion", calls[0]["decision"])

    def test_registry_autonomous_mode_allows_high_risk_tool(self) -> None:
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("modo autonomo")
            registry = ToolRegistry(autonomous=True)
            registry.register(
                RegisteredTool(
                    name="high.demo",
                    description="Herramienta high",
                    risk="high",
                    input_schema={},
                    handler=lambda: "ejecutada",
                )
            )

            result = registry.call("high.demo", {}, runtime=runtime, task_id=task_id)
            calls = runtime.recent_tool_calls(task_id)

        self.assertEqual(result, "ejecutada")
        self.assertTrue(calls[0]["allowed"])
        self.assertIn("modo autonomo", calls[0]["decision"])

    def test_registry_uses_approval_callback_when_not_autonomous(self) -> None:
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        approvals = {"n": 0}

        def approve(_tool, _request, _reason):
            approvals["n"] += 1
            return True

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("aprobar riesgo")
            registry = ToolRegistry(approval_callback=approve)
            registry.register(
                RegisteredTool(
                    name="approval.demo",
                    description="Herramienta high",
                    risk="high",
                    input_schema={},
                    handler=lambda: "aprobada",
                )
            )
            with patch.dict("os.environ", {"TOOL_REGISTRY_MAX_AUTO_RISK": "moderate"}, clear=False):
                result = registry.call("approval.demo", {}, runtime=runtime, task_id=task_id)
            calls = runtime.recent_tool_calls(task_id)

        self.assertEqual(result, "aprobada")
        self.assertEqual(approvals["n"], 1)
        self.assertIn("aprobada por usuario", calls[0]["decision"])

    def test_runtime_finishes_task(self) -> None:
        from task_runtime import TaskRuntime

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("objetivo")
            runtime.finish_task(task_id, status="succeeded", metadata={"cycles": 1})
            calls = runtime.recent_tool_calls(task_id)

        self.assertEqual(calls, [])

    def test_runtime_lists_tasks_and_tool_calls_for_audit_ui(self) -> None:
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("auditar llamada")
            registry = ToolRegistry()
            registry.register(
                RegisteredTool(
                    name="echo.demo",
                    description="Eco",
                    risk="low",
                    input_schema={"text": "str"},
                    handler=lambda text: f"eco:{text}",
                )
            )
            registry.call(
                "echo.demo",
                {"text": "hola"},
                runtime=runtime,
                task_id=task_id,
            )
            tasks = runtime.list_tasks(limit=5)
            calls = runtime.tool_calls_for_task(task_id)

        self.assertEqual(tasks[0]["task_id"], task_id)
        self.assertEqual(calls[0]["tool_name"], "echo.demo")
        self.assertIn('"text": "hola"', calls[0]["request"])
        self.assertIn("eco:hola", calls[0]["output"])

    def test_skill_manager_loads_manifest_context(self) -> None:
        import json

        from skill_manager import SkillManager

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "serial-device"
            skill_dir.mkdir(parents=True)
            (skill_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "serial-device",
                        "version": "0.1.0",
                        "description": "Conecta dispositivos por puerto serial",
                        "risk": "moderate",
                        "status": "experimental",
                        "triggers": ["serial", "arduino"],
                        "permissions": ["serial:read"],
                        "entrypoint": "skills/serial/run.py",
                        "instructions": "Usar para leer sensores seriales.",
                    }
                ),
                encoding="utf-8",
            )
            ctx = SkillManager(Path(tmp) / "skills").context("leer arduino serial")

        self.assertIn("serial-device", ctx)
        self.assertIn("serial:read", ctx)

    def test_skill_manager_exposes_modular_agent_specs(self) -> None:
        import json

        from skill_manager import SkillManager

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "github"
            skill_dir.mkdir(parents=True)
            (skill_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "github",
                        "description": "Gestiona issues y repositorios GitHub",
                        "risk": "moderate",
                        "status": "experimental",
                        "agent_enabled": True,
                        "agent_role": "AgenteSkill:github",
                        "triggers": ["github", "issue", "repo"],
                        "permissions": ["github:read"],
                        "entrypoint": "skills/github/run.py",
                        "executor": "terminal_command",
                        "command": "python --version",
                        "instructions": "Usar para tareas relacionadas con GitHub.",
                    }
                ),
                encoding="utf-8",
            )
            manager = SkillManager(Path(tmp) / "skills")
            specs = manager.agent_specs("revisar issue github")
            ctx = manager.agents_context("revisar issue github")

        self.assertEqual(specs[0]["name"], "AgenteSkill:github")
        self.assertEqual(specs[0]["tool_name"], "skill.github")
        self.assertIn("AgenteSkill:github", ctx)

    def test_skill_manager_repairs_incomplete_manifest_and_caches(self) -> None:
        import json

        from skill_manager import SkillManager

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "broken"
            skill_dir.mkdir(parents=True)
            (skill_dir / "manifest.json").write_text(
                json.dumps({"risk": "invalid-risk"}),
                encoding="utf-8",
            )
            manager = SkillManager(Path(tmp) / "skills")
            repaired = manager.repair_manifests()
            first = manager.load_skills()
            second = manager.load_skills()
            data = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
            readme_exists = (skill_dir / "README.md").exists()

        self.assertTrue(repaired)
        self.assertEqual(first[0].name, "broken")
        self.assertEqual(second[0].risk, "moderate")
        self.assertEqual(data["status"], "experimental")
        self.assertTrue(readme_exists)

    def test_tool_library_lists_and_deletes_entries(self) -> None:
        from tool_library import ToolLibrary, ToolMemory

        with tempfile.TemporaryDirectory() as tmp:
            lib = ToolLibrary(Path(tmp) / "tools.sqlite", embed_dim=16)
            lib.upsert(
                ToolMemory(
                    name="demo-tool",
                    kind="procedimiento",
                    objective="demo",
                    trigger_terms="demo",
                    entrypoint="demo.py",
                    instructions="usar demo",
                    evidence="ok",
                )
            )
            before = lib.list_entries()
            lib.mark_failure("demo-tool", "demo.py")
            marked = lib.list_entries()
            deleted = lib.delete("demo-tool", "demo.py")
            after = lib.list_entries()

        self.assertEqual(before[0].name, "demo-tool")
        self.assertEqual(marked[0].failure_count, 1)
        self.assertEqual(deleted, 1)
        self.assertEqual(after, [])

    def test_skill_manager_registers_executable_terminal_skill(self) -> None:
        import json

        from skill_manager import SkillManager
        from task_runtime import TaskRuntime
        from tool_registry import ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "echo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "echo-skill",
                        "description": "Skill de prueba",
                        "risk": "low",
                        "status": "validated",
                        "executor": "terminal_command",
                        "command": "python --version",
                        "cwd": ".",
                    }
                ),
                encoding="utf-8",
            )
            registry = ToolRegistry()
            calls = {"n": 0}

            def fake_terminal(command, project_root, cwd, timeout, max_output_chars):
                calls["n"] += 1
                return f"{command}|{project_root}|{cwd}|{timeout}|{max_output_chars}"

            SkillManager(Path(tmp) / "skills").register_executable_tools(
                registry,
                run_terminal_fn=fake_terminal,
                project_root=tmp,
            )
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("run skill")
            result = registry.call("skill.echo-skill", {}, runtime=runtime, task_id=task_id)

        self.assertEqual(calls["n"], 1)
        self.assertIn("python --version", result)

    def test_tool_creator_installs_and_tests_python_module(self) -> None:
        from skill_manager import SkillManager
        from tool_creator import ToolCreator, parse_generated_tool_spec
        from tool_library import ToolLibrary

        response = """
        {
          "name": "sum-helper",
          "description": "Suma listas de numeros",
          "triggers": ["sumar", "lista"],
          "code": "def sum_numbers(values):\\n    return sum(values)\\n",
          "test_code": "import unittest\\nfrom tool import sum_numbers\\n\\nclass TestSumNumbers(unittest.TestCase):\\n    def test_normal(self):\\n        self.assertEqual(sum_numbers([1, 2, 3]), 6)\\n    def test_empty(self):\\n        self.assertEqual(sum_numbers([]), 0)\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n",
          "instructions": "Importar sum_numbers desde tool.py.",
          "risk": "low"
        }
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = SkillManager(root / "skills")
            library = ToolLibrary(root / "tools.sqlite", embed_dim=16)
            creator = ToolCreator(
                project_root=root,
                skill_manager=manager,
                tool_library=library,
            )
            result = creator.install(parse_generated_tool_spec(response), timeout=10)
            entries = library.list_entries()

        self.assertTrue(result.test_ok, result.test_output)
        self.assertEqual(result.name, "sum-helper")
        self.assertTrue(entries)
        self.assertEqual(entries[0].entrypoint, "skills/sum-helper/tool.py")

    def test_tool_creator_rejects_dangerous_generated_code(self) -> None:
        from tool_creator import parse_generated_tool_spec

        response = '{"name": "bad", "code": "import os\\ndef run():\\n    return os.getcwd()"}'

        with self.assertRaises(ValueError):
            from tool_creator import ToolCreator

            with tempfile.TemporaryDirectory() as tmp:
                ToolCreator(project_root=Path(tmp)).install(parse_generated_tool_spec(response))


if __name__ == "__main__":
    unittest.main()
