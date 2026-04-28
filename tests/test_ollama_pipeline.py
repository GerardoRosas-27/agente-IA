"""
Regresión: extraer texto de respuestas (dict API) y `call_llm` con stub remoto.
"""
import unittest
from typing import Any


class TestAssistantMessageText(unittest.TestCase):
    def test_dict_content(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        r: dict[str, Any] = {"message": {"content": "hola mundo"}}
        self.assertEqual(_ollama_response_text(r), "hola mundo")

    def test_none(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        self.assertIsNone(_ollama_response_text(None))


class TestCallLlmWithStub(unittest.TestCase):
    def test_call_llm_success(self) -> None:
        from multi_agent_orchestrator import call_llm

        def fake_chat(
            model: str,
            messages: list,
            options: dict | None,
        ) -> dict:
            return {"message": {"content": "RESULTADO", "role": "assistant"}}

        out = call_llm(
            fake_chat, "m", "sys", "user", num_predict=50
        )
        self.assertEqual(out, "RESULTADO")

    def test_call_llm_fails_on_empty(self) -> None:
        from multi_agent_orchestrator import call_llm

        def empty(_m, _msg, _opt) -> None:
            return None

        with self.assertRaises(RuntimeError):
            call_llm(empty, "m", "s", "u", num_predict=20)

    def test_call_llm_uses_in_memory_cache(self) -> None:
        from multi_agent_orchestrator import call_llm

        calls = {"n": 0}

        def fake_chat(_model, _messages, _options) -> dict:
            calls["n"] += 1
            return {"message": {"content": "CACHEADO", "role": "assistant"}}

        first = call_llm(fake_chat, "cache-model-unique", "sys", "user", num_predict=51)
        second = call_llm(fake_chat, "cache-model-unique", "sys", "user", num_predict=51)

        self.assertEqual(first, "CACHEADO")
        self.assertEqual(second, "CACHEADO")
        self.assertEqual(calls["n"], 1)


class TestStructuredReviewerParsing(unittest.TestCase):
    def test_parse_final_verdict_accepts_json(self) -> None:
        from objective_agent_cycle import parse_final_verdict

        reached, motivo, response, retro = parse_final_verdict(
            '{"objetivo_alcanzado": true, "motivo": "-", '
            '"retroalimentacion": "-", "respuesta_final": "listo"}'
        )

        self.assertTrue(reached)
        self.assertEqual(motivo, "-")
        self.assertEqual(response, "listo")
        self.assertEqual(retro, "-")


class TestAuxiliaryAgents(unittest.TestCase):
    def test_python_probe_parses_json_and_executes_small_script(self) -> None:
        from objective_agent_cycle import parse_python_probe_request, run_python_probe_script

        needed, reason, script = parse_python_probe_request(
            '{"necesario": true, "motivo": "validar suma", '
            '"script": "print(2 + 2)"}'
        )
        result = run_python_probe_script(script, timeout=2, max_chars=200)

        self.assertTrue(needed)
        self.assertEqual(reason, "validar suma")
        self.assertIn("exit_code=0", result)
        self.assertIn("4", result)

    def test_python_probe_blocks_unsafe_operations(self) -> None:
        from objective_agent_cycle import run_python_probe_script

        result = run_python_probe_script("import os\nprint(os.listdir('.'))")

        self.assertIn("operaciones bloqueadas", result)

    def test_python_probe_relevance_can_skip_non_code_context(self) -> None:
        from objective_agent_cycle import is_node_probe_relevant, is_python_probe_relevant

        self.assertFalse(is_python_probe_relevant("redactar una carta breve y amable"))
        self.assertTrue(is_python_probe_relevant("validar una función Python con tests"))
        self.assertFalse(is_node_probe_relevant("redactar una carta breve y amable"))
        self.assertTrue(is_node_probe_relevant("validar un parser JavaScript con Node.js"))

    def test_node_probe_parses_json_and_blocks_unsafe_operations(self) -> None:
        from node_probe_tool import parse_node_probe_request, run_node_probe_script

        needed, reason, script = parse_node_probe_request(
            '{"necesario": true, "motivo": "validar JS", '
            '"script": "console.log(JSON.stringify({ok: 2 + 2}))"}'
        )
        blocked = run_node_probe_script("const fs = require('fs'); console.log(fs.readdirSync('.'))")

        self.assertTrue(needed)
        self.assertEqual(reason, "validar JS")
        self.assertIn("console.log", script)
        self.assertIn("operaciones bloqueadas", blocked)

    def test_node_probe_executes_small_script_when_node_is_available(self) -> None:
        import shutil

        from node_probe_tool import run_node_probe_script

        if not shutil.which("node"):
            self.skipTest("Node.js no está instalado")

        result = run_node_probe_script("console.log(2 + 2)", timeout=2, max_chars=200)

        self.assertIn("exit_code=0", result)
        self.assertIn("4", result)

    def test_terminal_tool_runs_allowed_command(self) -> None:
        import tempfile

        from terminal_tool import run_terminal_command

        with tempfile.TemporaryDirectory() as tmp:
            result = run_terminal_command(
                "python --version",
                project_root=tmp,
                timeout=5,
            )

        self.assertTrue(result.allowed)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Python", result.stdout or result.stderr)

    def test_terminal_tool_blocks_destructive_command(self) -> None:
        import tempfile

        from terminal_tool import run_terminal_command

        with tempfile.TemporaryDirectory() as tmp:
            result = run_terminal_command(
                "git reset --hard",
                project_root=tmp,
                timeout=5,
            )

        self.assertFalse(result.allowed)
        self.assertIn("bloqueado", result.reason)

    def test_terminal_request_parses_json(self) -> None:
        from terminal_tool import parse_terminal_command_request

        req = parse_terminal_command_request(
            '{"necesario": true, "motivo": "ver tests", "command": "pytest", "cwd": "."}'
        )

        self.assertTrue(req.needed)
        self.assertEqual(req.reason, "ver tests")
        self.assertEqual(req.command, "pytest")

    def test_terminal_policy_can_disable_moderate_commands(self) -> None:
        from unittest.mock import patch

        from terminal_tool import validate_terminal_command

        with patch.dict(
            "os.environ",
            {
                "TERMINAL_AGENT_ENABLE_SAFE_COMMANDS": "1",
                "TERMINAL_AGENT_ENABLE_MODERATE_COMMANDS": "0",
                "TERMINAL_AGENT_ENABLE_DANGEROUS_COMMANDS": "0",
            },
            clear=False,
        ):
            allowed, reason = validate_terminal_command("pip list")

        self.assertFalse(allowed)
        self.assertIn("no permitido", reason)

    def test_terminal_policy_keeps_hard_block_for_dangerous_commands(self) -> None:
        from unittest.mock import patch

        from terminal_tool import validate_terminal_command

        with patch.dict(
            "os.environ",
            {
                "TERMINAL_AGENT_ENABLE_DANGEROUS_COMMANDS": "1",
            },
            clear=False,
        ):
            allowed, reason = validate_terminal_command("del archivo.txt")

        self.assertFalse(allowed)
        self.assertIn("bloqueado", reason)

    def test_tool_library_stores_and_retrieves_reusable_tool(self) -> None:
        import tempfile
        from pathlib import Path

        from tool_library import ToolLibrary, ToolMemory

        with tempfile.TemporaryDirectory() as tmp:
            lib = ToolLibrary(Path(tmp) / "tools.sqlite", embed_dim=16)
            lib.upsert(
                ToolMemory(
                    name="Conector Arduino serial",
                    kind="conector",
                    objective="leer sensores por puerto serial",
                    trigger_terms="arduino serial sensores puerto",
                    entrypoint="scripts/arduino_serial.py",
                    instructions="Reutilizar el script para abrir el puerto y leer lineas JSON.",
                    evidence="Funciono en una prueba previa.",
                )
            )
            ctx = lib.context("necesito conectar arduino por puerto serial")

        self.assertIn("Biblioteca de herramientas", ctx)
        self.assertIn("Conector Arduino serial", ctx)
        self.assertIn("scripts/arduino_serial.py", ctx)

    def test_tool_memory_request_parses_json(self) -> None:
        from tool_library import parse_tool_memory_request

        reusable, memory, reason = parse_tool_memory_request(
            '{"reutilizable": true, "nombre": "pytest smoke", "tipo": "comando", '
            '"objetivo": "validar pruebas", "activadores": "pytest tests", '
            '"entrada": "python -m unittest discover -s tests", '
            '"instrucciones": "Ejecutar para validar regresiones.", '
            '"evidencia": "tests OK"}'
        )

        self.assertTrue(reusable)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(memory)
        self.assertEqual(memory.name if memory else "", "pytest smoke")


if __name__ == "__main__":
    unittest.main()
