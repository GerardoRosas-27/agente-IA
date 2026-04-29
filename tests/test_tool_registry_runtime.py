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

        response = '{"name": "bad", "code": "import subprocess\\ndef run():\\n    return subprocess.run([\'python\', \'--version\'])"}'

        with self.assertRaises(ValueError):
            from tool_creator import ToolCreator

            with tempfile.TemporaryDirectory() as tmp:
                ToolCreator(project_root=Path(tmp)).install(parse_generated_tool_spec(response))

    def test_tool_creation_objective_allows_research_and_network_design(self) -> None:
        from tool_creator import tool_creation_objective

        objective = tool_creation_objective("conectar con WhatsApp Cloud API")

        self.assertIn("Puedes investigar por internet", objective)
        self.assertIn("Ignora sesiones o memorias anteriores", objective)
        self.assertIn("WhatsApp Cloud API", objective)
        self.assertNotIn("No uses red", objective)

    def test_tool_creator_recovers_whatsapp_request_when_cycle_only_plans(self) -> None:
        from skill_manager import SkillManager
        from tool_creator import ToolCreator
        from tool_library import ToolLibrary

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creator = ToolCreator(
                project_root=root,
                skill_manager=SkillManager(root / "skills"),
                tool_library=ToolLibrary(root / "tools.sqlite", embed_dim=16),
            )

            def fake_pipeline(_objective: str):
                return (
                    "No hace falta crear una herramienta nueva; usa project-tests.",
                    1,
                    0.0,
                    False,
                )

            result = creator.create_with_main_cycle(
                "crear herramienta para conectarme a waptsap",
                pipeline_runner=fake_pipeline,
                timeout=10,
            )
            tool_py = result.skill_dir / "tool.py"
            tool_source = tool_py.read_text(encoding="utf-8")
            entries = creator.tool_library.list_entries() if creator.tool_library else []

        self.assertTrue(result.test_ok, result.test_output)
        self.assertEqual(result.name, "whatsapp-link-tool")
        self.assertIn("build_wa_me_url", tool_source)
        self.assertTrue(entries)
        self.assertEqual(entries[0].name, "whatsapp-link-tool")

    def test_tool_creator_retries_recovered_tool_when_generated_tests_fail(self) -> None:
        import json

        from skill_manager import SkillManager
        from tool_creator import ToolCreator
        from tool_library import ToolLibrary

        generated = json.dumps(
            {
                "name": "whatsapp-broken",
                "description": "whatsapp roto",
                "triggers": ["whatsapp"],
                "code": "def build_wa_me_url(phone):\n    return 'bad'\n",
                "test_code": (
                    "import unittest\n"
                    "from tool import build_wa_me_url\n\n"
                    "class TestBroken(unittest.TestCase):\n"
                    "    def test_url(self):\n"
                    "        self.assertEqual(build_wa_me_url('1'), 'expected')\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
                "instructions": "usar",
                "risk": "low",
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creator = ToolCreator(
                project_root=root,
                skill_manager=SkillManager(root / "skills"),
                tool_library=ToolLibrary(root / "tools.sqlite", embed_dim=16),
            )

            def fake_pipeline(_objective: str):
                return generated, 1, 0.0, True

            result = creator.create_with_main_cycle(
                "crear herramienta para conectarme a whatsapp",
                pipeline_runner=fake_pipeline,
                timeout=10,
            )
            entries = creator.tool_library.list_entries() if creator.tool_library else []

        self.assertTrue(result.test_ok, result.test_output)
        self.assertEqual(result.name, "whatsapp-link-tool")
        self.assertTrue(entries)
        self.assertEqual(entries[0].name, "whatsapp-link-tool")

    def test_objective_cycle_extracts_candidate_code_block_for_probe(self) -> None:
        from objective_agent_cycle import first_code_block

        text = "Propuesta:\n```python\nprint('ok')\n```\nFin"

        self.assertEqual(first_code_block(text, ("python", "py")), "print('ok')")

    def test_tool_build_runtime_repairs_failed_tool_with_callback(self) -> None:
        import json

        from skill_manager import SkillManager
        from tool_build_runtime import ToolBuildRuntime
        from tool_creator import ToolCreator
        from tool_library import ToolLibrary

        broken = json.dumps(
            {
                "name": "sum-tool",
                "description": "Suma numeros",
                "triggers": ["sumar"],
                "code": "def add(a, b):\n    return a - b\n",
                "test_code": (
                    "import unittest\nfrom tool import add\n\n"
                    "class TestAdd(unittest.TestCase):\n"
                    "    def test_add(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
                "instructions": "Usar add(a, b).",
                "risk": "low",
            }
        )
        fixed = json.dumps(
            {
                "name": "sum-tool",
                "description": "Suma numeros",
                "triggers": ["sumar"],
                "code": "def add(a, b):\n    return a + b\n",
                "test_code": (
                    "import unittest\nfrom tool import add\n\n"
                    "class TestAdd(unittest.TestCase):\n"
                    "    def test_add(self):\n"
                    "        self.assertEqual(add(2, 3), 5)\n"
                    "    def test_zero(self):\n"
                    "        self.assertEqual(add(0, 0), 0)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
                "instructions": "Usar add(a, b).",
                "risk": "low",
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creator = ToolCreator(
                project_root=root,
                skill_manager=SkillManager(root / "skills"),
                tool_library=ToolLibrary(root / "tools.sqlite", embed_dim=16),
            )
            builder = ToolBuildRuntime(creator=creator, project_root=root, max_repair_attempts=2)
            repair_calls = {"n": 0}

            def repair(_prompt: str) -> str:
                repair_calls["n"] += 1
                return fixed

            result = builder.build(
                user_objective="crear herramienta para sumar numeros",
                initial_text=broken,
                cycles=1,
                reached=True,
                repair_callback=repair,
                timeout=10,
            )
            log_exists = Path(result.build_log_path).exists()
            entries = creator.tool_library.list_entries() if creator.tool_library else []

        self.assertTrue(result.test_ok, result.test_output)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(repair_calls["n"], 1)
        self.assertTrue(log_exists)
        self.assertTrue(entries)
        self.assertEqual(entries[0].name, "sum-tool")


if __name__ == "__main__":
    unittest.main()
