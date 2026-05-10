"""Tests del cliente HTTP (sin servidor)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from llm_api_client import parse_assistant_message, resolve_llm_chat_for_pipeline, resolve_llm_profile


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


if __name__ == "__main__":
    unittest.main()
