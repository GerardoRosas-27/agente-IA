"""Tests del cliente HTTP (sin servidor)."""
from __future__ import annotations

import unittest

from llm_api_client import parse_assistant_message


class TestParseAssistantMessage(unittest.TestCase):
    def test_dict_content(self) -> None:
        r = {"message": {"role": "assistant", "content": "hola mundo"}}
        self.assertEqual(parse_assistant_message(r), "hola mundo")

    def test_none(self) -> None:
        self.assertIsNone(parse_assistant_message(None))

    def test_reasoning_fallback(self) -> None:
        r = {"message": {"role": "assistant", "content": "", "reasoning_content": "razon"}}
        self.assertEqual(parse_assistant_message(r), "razon")


if __name__ == "__main__":
    unittest.main()
