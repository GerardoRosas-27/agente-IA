from __future__ import annotations

import sys
import types

import pytest

from skills.whatsapp_connector import (
    WhatsAppCloudConfig,
    extract_incoming_messages,
    handle_webhook_payload,
    handle_input_command,
    normalize_phone_number,
    parse_whatsapp_command,
    send_cloud_message,
    send_whatsapp_message,
    verify_webhook,
)


def test_normalize_phone_number_requires_country_code() -> None:
    assert normalize_phone_number("+52 123 456 7890") == "+521234567890"
    with pytest.raises(ValueError):
        normalize_phone_number("1234567890")


def test_parse_whatsapp_command() -> None:
    parsed = parse_whatsapp_command("whatsapp +521234567890 | hola mundo")
    assert parsed == ("+521234567890", "hola mundo")
    assert parse_whatsapp_command("crear una herramienta") is None


def test_handle_input_command_dry_run() -> None:
    result = handle_input_command("wa +521234567890 | mensaje", dry_run=True)
    assert result is not None
    assert result.success is True
    assert result.phone_number == "+521234567890"
    assert "Dry run" in result.detail


def test_send_whatsapp_message_uses_pywhatkit(monkeypatch) -> None:
    fake_module = types.SimpleNamespace()
    calls = []

    def fake_sendwhatmsg_instantly(**kwargs):
        calls.append(kwargs)

    fake_module.sendwhatmsg_instantly = fake_sendwhatmsg_instantly
    monkeypatch.setitem(sys.modules, "pywhatkit", fake_module)

    result = send_whatsapp_message("+521234567890", "hola", wait_time=1)

    assert result.success is True
    assert calls == [
        {
            "phone_no": "+521234567890",
            "message": "hola",
            "wait_time": 1,
            "tab_close": False,
            "close_time": 3,
        }
    ]


def test_verify_webhook() -> None:
    cfg = WhatsAppCloudConfig(
        access_token="token",
        phone_number_id="phone-id",
        verify_token="verify-me",
    )

    assert verify_webhook("subscribe", "verify-me", "123", config=cfg) == "123"
    assert verify_webhook("subscribe", "wrong", "123", config=cfg) is None


def test_extract_incoming_messages() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "521234567890",
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": "crea una tarea"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    messages = extract_incoming_messages(payload)

    assert len(messages) == 1
    assert messages[0].from_number == "521234567890"
    assert messages[0].text == "crea una tarea"


def test_send_cloud_message_dry_run() -> None:
    result = send_cloud_message("521234567890", "respuesta", dry_run=True)

    assert result.success is True
    assert result.phone_number == "521234567890"
    assert "Dry run Cloud API" in result.detail


def test_handle_webhook_payload_responds_to_sender() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "521234567890",
                                    "id": "wamid.2",
                                    "type": "text",
                                    "text": {"body": "hola"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    results = handle_webhook_payload(
        payload,
        responder=lambda message: f"respuesta a: {message.text}",
        dry_run=True,
    )

    assert len(results) == 1
    assert results[0].message == "respuesta a: hola"
