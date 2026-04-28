import tempfile
import unittest
from pathlib import Path


class TestAgentLifecycle(unittest.TestCase):
    def test_skill_agent_lifecycle_contributes_context(self) -> None:
        from agent_lifecycle import run_skill_agent_lifecycle
        from task_runtime import TaskRuntime
        from tool_registry import ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("usar especialista")
            result = run_skill_agent_lifecycle(
                spec={
                    "name": "AgenteSkill:demo",
                    "skill_name": "demo",
                    "tool_name": "skill.demo",
                    "executor": "",
                    "description": "demo",
                    "instructions": "aportar contexto",
                },
                objective="resolver demo",
                criteria="- aportar evidencia",
                llm_call=lambda _system, _user, _budget: (
                    '{"aporta": true, "ejecutar": false, '
                    '"motivo": "aplica", "nota": "usar procedimiento demo"}'
                ),
                registry=ToolRegistry(),
                runtime=runtime,
                task_id=task_id,
                num_predict=80,
            )

        self.assertTrue(result.contributed)
        self.assertEqual(result.role, "AgenteSkill:demo")
        self.assertIn("usar procedimiento demo", result.content)

    def test_skill_agent_lifecycle_executes_registered_tool(self) -> None:
        from agent_lifecycle import run_skill_agent_lifecycle
        from task_runtime import TaskRuntime
        from tool_registry import RegisteredTool, ToolRegistry

        with tempfile.TemporaryDirectory() as tmp:
            runtime = TaskRuntime(Path(tmp) / "runtime.sqlite")
            task_id = runtime.start_task("ejecutar especialista")
            registry = ToolRegistry()
            registry.register(
                RegisteredTool(
                    name="skill.demo",
                    description="demo",
                    risk="low",
                    input_schema={},
                    handler=lambda: "resultado herramienta",
                )
            )
            result = run_skill_agent_lifecycle(
                spec={
                    "name": "AgenteSkill:demo",
                    "skill_name": "demo",
                    "tool_name": "skill.demo",
                    "executor": "terminal_command",
                    "description": "demo",
                    "instructions": "ejecutar",
                },
                objective="resolver demo",
                criteria="- ejecutar herramienta",
                llm_call=lambda _system, _user, _budget: (
                    '{"aporta": true, "ejecutar": true, '
                    '"motivo": "necesita evidencia", "nota": ""}'
                ),
                registry=registry,
                runtime=runtime,
                task_id=task_id,
                num_predict=80,
            )
            calls = runtime.recent_tool_calls(task_id)

        self.assertTrue(result.contributed)
        self.assertEqual(result.executed_tool, "skill.demo")
        self.assertIn("resultado herramienta", result.content)
        self.assertEqual(calls[0]["tool_name"], "skill.demo")


if __name__ == "__main__":
    unittest.main()
