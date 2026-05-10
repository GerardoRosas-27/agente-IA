"""Tests del cliente HTTP (sin servidor)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

import llm_api_client
from llm_api_client import (
    _post_chat_completions,
    parse_assistant_message,
    reset_llm_circuit,
    resolve_llm_chat_for_pipeline,
    resolve_llm_profile,
)


class TestParseAssistantMessage(unittest.TestCase):
    def test_dict_content(self) -> None:
        r = {"message": {"role": "assistant", "content": "hola mundo"}}
        self.assertEqual(parse_assistant_message(r), "hola mundo")

    def test_none(self) -> None:
        self.assertIsNone(parse_assistant_message(None))

    def test_reasoning_fallback(self) -> None:
        r = {"message": {"role": "assistant", "content": "", "reasoning_content": "razon"}}
        self.assertEqual(parse_assistant_message(r), "razon")

    def test_resolve_named_llm_profile(self) -> None:
        env = {
            "LLM_PROFILE": "hermes",
            "LLM_HERMES_API_BASE_URL": "http://localhost:1234/v1",
            "LLM_HERMES_MODEL": "hermes-local",
            "LLM_HERMES_API_KEY": "token",
            "LLM_HERMES_HTTP_TIMEOUT": "12",
        }
        with patch.dict("os.environ", env, clear=True):
            profile = resolve_llm_profile()

        self.assertEqual(profile.name, "hermes")
        self.assertEqual(profile.base_url, "http://localhost:1234/v1")
        self.assertEqual(profile.model, "hermes-local")
        self.assertEqual(profile.api_key, "token")
        self.assertEqual(profile.timeout, 12)

    def test_resolve_deepseek_profile_uses_official_api_defaults(self) -> None:
        env = {"DEEPSEEK_API_KEY": "deepseek-token"}
        with patch.dict("os.environ", env, clear=True):
            profile = resolve_llm_profile("deepseek")

        self.assertEqual(profile.name, "deepseek")
        self.assertEqual(profile.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(profile.model, "deepseek-chat")
        self.assertEqual(profile.api_key, "deepseek-token")

    def test_resolve_deepseek_v4_profile_can_select_future_model(self) -> None:
        env = {"DEEPSEEK_API_KEY": "deepseek-token"}
        with patch.dict("os.environ", env, clear=True):
            profile = resolve_llm_profile("deepseek_v4")

        self.assertEqual(profile.name, "deepseek_v4")
        self.assertEqual(profile.model, "deepseek-v4")

    def test_pipeline_returns_chat_bound_to_profile(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deepseek-token",
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
        with patch.dict("os.environ", env, clear=True):
            chat, model, label = resolve_llm_chat_for_pipeline("", "deepseek")

        self.assertTrue(callable(chat))
        self.assertEqual(model, "deepseek-chat")
        self.assertIn("deepseek", label)


class TestRetriesAndCircuit(unittest.TestCase):
    def setUp(self) -> None:
        reset_llm_circuit()
        # Acelerar tests: backoff inmediato.
        self._patches = [
            patch.object(llm_api_client, "_RETRY_BASE_DELAY", 0.0),
            patch.object(llm_api_client, "_RETRY_MAX_DELAY", 0.0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        reset_llm_circuit()

    def _success_response(self) -> MagicMock:
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]
        }
        return r

    def _http_error_response(self, status: int) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.raise_for_status.side_effect = requests.HTTPError(f"status={status}")
        return r

    def test_retries_on_transient_5xx_then_succeeds(self) -> None:
        responses = [self._http_error_response(503), self._http_error_response(503), self._success_response()]
        with patch.object(llm_api_client.requests, "post", side_effect=responses):
            result = _post_chat_completions(
                "http://x/v1",
                "model",
                [{"role": "user", "content": "hi"}],
                None,
                timeout=5,
                api_key="",
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["message"]["content"], "ok")

    def test_retries_on_connection_error(self) -> None:
        side = [requests.ConnectionError("boom"), self._success_response()]
        with patch.object(llm_api_client.requests, "post", side_effect=side):
            result = _post_chat_completions(
                "http://x/v1",
                "model",
                [{"role": "user", "content": "hi"}],
                None,
                timeout=5,
                api_key="",
            )
        self.assertIsNotNone(result)

    def test_circuit_breaker_opens_after_repeated_failures(self) -> None:
        responses = [
            requests.ConnectionError("boom"),
            requests.ConnectionError("boom"),
            requests.ConnectionError("boom"),
        ]

        def _all_fail(*_a, **_kw):
            raise requests.ConnectionError("boom")

        # Primer intento agota retries y registra fallos.
        with patch.object(llm_api_client.requests, "post", side_effect=_all_fail):
            for _ in range(2):  # 2 invocaciones × 3 retries = 6 fallos > 5 threshold
                _post_chat_completions(
                    "http://broken/v1",
                    "m",
                    [{"role": "user", "content": "hi"}],
                    None,
                    timeout=1,
                    api_key="",
                )

        # Tercera invocación: el circuit breaker está abierto y NO debe llamar
        # a requests.post.
        post_mock = MagicMock(side_effect=_all_fail)
        with patch.object(llm_api_client.requests, "post", post_mock):
            result = _post_chat_completions(
                "http://broken/v1",
                "m",
                [{"role": "user", "content": "hi"}],
                None,
                timeout=1,
                api_key="",
            )
        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 0, "el circuit abierto debió saltar la llamada")
        _ = responses  # silenciar warning

    def test_non_retryable_4xx_does_not_open_circuit(self) -> None:
        bad_request = self._http_error_response(400)

        with patch.object(llm_api_client.requests, "post", return_value=bad_request) as post:
            for _ in range(8):
                result = _post_chat_completions(
                    "http://bad-request/v1",
                    "m",
                    [{"role": "user", "content": "hi"}],
                    None,
                    timeout=1,
                    api_key="",
                )
                self.assertIsNone(result)

        # Si 400 abriera circuito, post.call_count sería menor que 8.
        self.assertEqual(post.call_count, 8)


if __name__ == "__main__":
    unittest.main()
