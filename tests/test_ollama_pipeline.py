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


if __name__ == "__main__":
    unittest.main()
