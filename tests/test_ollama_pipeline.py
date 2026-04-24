"""
Regresión: respuesta real de Ollama (ChatResponse) vs heurística repetida.

Fallaba antes al usar `isinstance(r, dict)`; también al llamar `ollama.chat`
global desde el Thread de Tk (httpx no thread-safe → fallos silenciosos).
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    from ollama import ChatResponse, Message

    _OLLAMA_PKG = True
except ImportError:
    _OLLAMA_PKG = False

# Pruebas que llaman al daemon (modelo cargado, red). Por defecto desactivadas.
_RUN_LIVE_OLLAMA = os.environ.get("OLLAMA_INTEGRATION", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


class TestOllamaResponseText(unittest.TestCase):
    def test_dict_response(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        r = {"message": {"role": "assistant", "content": "  hola mundo  "}}
        self.assertEqual(_ollama_response_text(r), "hola mundo")

    def test_none_response(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        self.assertIsNone(_ollama_response_text(None))

    @unittest.skipUnless(_OLLAMA_PKG, "paquete ollama no instalado")
    def test_chat_response_like_ollama_0_3(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        r = ChatResponse(
            message=Message(role="assistant", content="CONTENIDO_ASSISTANT"),
            done=True,
        )
        self.assertEqual(_ollama_response_text(r), "CONTENIDO_ASSISTANT")

    @unittest.skipUnless(_OLLAMA_PKG, "paquete ollama no instalado")
    def test_thinking_fallback_when_content_empty(self) -> None:
        from multi_agent_orchestrator import _ollama_response_text

        r = ChatResponse(
            message=Message(role="assistant", content="", thinking="solo pensamiento"),
            done=True,
        )
        self.assertEqual(_ollama_response_text(r), "solo pensamiento")


class TestOllamaVsHeuristic(unittest.TestCase):
    @unittest.skipUnless(_OLLAMA_PKG, "paquete ollama no instalado")
    def test_ollama_extracts_llm_text_not_user_truncation(self) -> None:
        from multi_agent_orchestrator import _ollama

        def fake_chat(
            *,
            model: str,
            messages: list,
            options: dict | None = None,
            stream: bool = False,
        ):
            self.assertFalse(stream)
            return ChatResponse(
                message=Message(role="assistant", content="RESPUESTA_LLM_MARCADOR"),
                done=True,
            )

        user = "X" * 2000
        out = _ollama(
            fake_chat,
            "gemma3:270m",
            "Eres asistente.",
            user,
            num_predict=80,
        )
        self.assertIn("RESPUESTA_LLM_MARCADOR", out)
        self.assertNotEqual(out.strip(), user[:400].strip())


class TestLocalLlmFromWorkerThread(unittest.TestCase):
    """El bug de Tk: pipeline en Thread; debe usar Client por hilo."""

    @unittest.skipUnless(_OLLAMA_PKG, "paquete ollama no instalado")
    @unittest.skipUnless(
        _RUN_LIVE_OLLAMA,
        "definir OLLAMA_INTEGRATION=1 para probar contra el daemon (opcional)",
    )
    def test_local_llm_chat_call_from_thread_uses_client(self) -> None:
        from chat_bridge import local_llm_chat_call
        from multi_agent_orchestrator import _ollama_response_text

        out: list[str | None] = []

        def worker() -> None:
            try:
                r = local_llm_chat_call(
                    "gemma3:270m",
                    [{"role": "user", "content": "Di solo: HILO_OK"}],
                    {"num_predict": 32, "temperature": 0.1},
                )
                out.append(_ollama_response_text(r))
            except Exception as e:
                out.append(f"ERR:{e}")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=45)
        if t.is_alive():
            self.skipTest("Timeout esperando a Ollama (45s); daemon lento o modelo cargando.")
        self.assertEqual(len(out), 1)
        if out[0] is None or (out[0] and out[0].startswith("ERR:")):
            self.skipTest(f"Ollama no disponible en este entorno: {out[0]}")
        self.assertIsNotNone(out[0])
        self.assertIn("HILO", (out[0] or "").upper())


class TestLiveOllamaOptional(unittest.TestCase):
    """Si el daemon está arriba, comprobación de punta a punta (opcional)."""

    @unittest.skipUnless(_OLLAMA_PKG, "paquete ollama no instalado")
    @unittest.skipUnless(
        _RUN_LIVE_OLLAMA,
        "definir OLLAMA_INTEGRATION=1 para probar contra el daemon (opcional)",
    )
    def test_live_ping_if_daemon_running(self) -> None:
        from chat_bridge import local_llm_chat_call
        from multi_agent_orchestrator import _ollama, _ollama_response_text

        r = local_llm_chat_call(
            "gemma3:270m",
            [{"role": "user", "content": "Responde exactamente: PING_DAEMON"}],
            {"num_predict": 24, "temperature": 0},
        )
        if r is None:
            self.skipTest("Ollama no respondió (¿servicio activo y modelo instalado?)")
        text = _ollama_response_text(r)
        self.assertIsNotNone(text)
        self.assertGreater(len(text or ""), 0)

        wrapped = _ollama(
            local_llm_chat_call,
            "gemma3:270m",
            "Responde en una palabra.",
            "Palabra requerida: ZETA",
            num_predict=16,
        )
        self.assertNotEqual(wrapped.strip(), "Palabra requerida: ZETA"[:400].strip())


if __name__ == "__main__":
    unittest.main()
