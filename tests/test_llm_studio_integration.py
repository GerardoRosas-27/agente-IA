"""
Prueba opcional contra LM Studio real en la red.

No se ejecuta en CI salvo que definas LLM_INTEGRATION_TEST=1 y una URL alcanzable
(LLM_API_BASE_URL o host/puerto por variables abajo).
"""
from __future__ import annotations

import os
import unittest

import requests


def _base_url() -> str | None:
    from llm_api_client import _load_env_file

    _load_env_file()
    explicit = (os.getenv("LLM_API_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.getenv("LLM_STUDIO_HOST") or "").strip()
    if host:
        port = (os.getenv("LLM_STUDIO_PORT") or "1234").strip()
        return f"http://{host}:{port}/v1"
    return None


@unittest.skipUnless(
    os.getenv("LLM_INTEGRATION_TEST") == "1",
    "define LLM_INTEGRATION_TEST=1 para probar contra LM Studio",
)
class TestLMStudioOpenAICompatible(unittest.TestCase):
    def test_chat_completions_returns_assistant_text(self) -> None:
        base = _base_url()
        self.assertIsNotNone(
            base,
            "configura LLM_API_BASE_URL o LLM_STUDIO_HOST (+ opcional LLM_STUDIO_PORT)",
        )
        model = (os.getenv("LLM_MODEL") or "xiaomi-mimo-vl-miloco-7b").strip()
        url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Reply with one short sentence."},
                {"role": "user", "content": "Say hello."},
            ],
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 64,
        }
        timeout = float(os.getenv("LLM_HTTP_TIMEOUT") or "120")
        r = requests.post(url, json=payload, timeout=timeout)
        self.assertEqual(r.status_code, 200, msg=r.text[:500])
        data = r.json()
        choices = data.get("choices") or []
        self.assertTrue(choices)
        content = (choices[0].get("message") or {}).get("content")
        self.assertIsInstance(content, str)
        self.assertTrue(content.strip())


if __name__ == "__main__":
    unittest.main()
