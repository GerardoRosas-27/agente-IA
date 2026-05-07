"""Skill bidireccional para WhatsApp.

Incluye dos modos:
- WhatsApp Web con pywhatkit: útil para enviar manualmente y mostrar QR/login.
- WhatsApp Cloud API: útil para recibir mensajes por webhook y responder.
"""
from __future__ import annotations

import json
import os
import re
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

import requests


WHATSAPP_WEB_URL = "https://web.whatsapp.com"
COMMAND_PREFIXES = ("whatsapp", "wa", "/whatsapp", "/wa")
DEFAULT_GRAPH_API_VERSION = "v20.0"


@dataclass(frozen=True)
class WhatsAppSendResult:
    success: bool
    phone_number: str
    message: str
    detail: str


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    """Mensaje entrante recibido desde el webhook de WhatsApp Cloud API."""

    from_number: str
    message_id: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WhatsAppCloudConfig:
    access_token: str
    phone_number_id: str
    verify_token: str
    graph_api_version: str = DEFAULT_GRAPH_API_VERSION


def _load_env_file() -> None:
    """Carga `.env` local sin sobrescribir variables ya definidas."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def load_cloud_config() -> WhatsAppCloudConfig:
    """Lee la configuración necesaria para WhatsApp Cloud API."""
    _load_env_file()
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", DEFAULT_GRAPH_API_VERSION).strip()
    missing = [
        name
        for name, value in {
            "WHATSAPP_ACCESS_TOKEN": access_token,
            "WHATSAPP_PHONE_NUMBER_ID": phone_number_id,
            "WHATSAPP_VERIFY_TOKEN": verify_token,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Faltan variables para WhatsApp Cloud API: " + ", ".join(missing)
        )
    return WhatsAppCloudConfig(
        access_token=access_token,
        phone_number_id=phone_number_id,
        verify_token=verify_token,
        graph_api_version=version,
    )


def normalize_phone_number(raw_number: str) -> str:
    """Normaliza un teléfono a formato internacional para WhatsApp."""
    cleaned = re.sub(r"[\s().-]+", "", raw_number.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        raise ValueError("El número debe incluir código de país, por ejemplo +521234567890.")
    digits = cleaned[1:]
    if not digits.isdigit() or len(digits) < 8:
        raise ValueError("El número de WhatsApp no tiene un formato válido.")
    return "+" + digits


def normalize_cloud_recipient(raw_number: str) -> str:
    """Cloud API espera el número sin `+`."""
    cleaned = re.sub(r"[\s().-]+", "", raw_number.strip())
    if cleaned.startswith("+"):
        return normalize_phone_number(cleaned).lstrip("+")
    if cleaned.startswith("00"):
        return normalize_phone_number(cleaned).lstrip("+")
    if cleaned.isdigit() and len(cleaned) >= 8:
        return cleaned
    raise ValueError("El número de WhatsApp Cloud API no tiene un formato válido.")


def parse_whatsapp_command(text: str) -> tuple[str, str] | None:
    """
    Extrae número y mensaje desde el input.

    Formato recomendado:
        whatsapp +521234567890 | Hola desde el sistema
    """
    stripped = text.strip()
    if not stripped:
        return None

    lower = stripped.lower()
    prefix = next((p for p in COMMAND_PREFIXES if lower.startswith(p + " ")), None)
    if prefix is None:
        return None

    body = stripped[len(prefix) :].strip()
    if "|" not in body:
        raise ValueError("Formato esperado: whatsapp +codigoPaisNumero | mensaje")

    number, message = (part.strip() for part in body.split("|", 1))
    if not message:
        raise ValueError("El mensaje de WhatsApp no puede estar vacío.")
    return normalize_phone_number(number), message


def open_whatsapp_login() -> WhatsAppSendResult:
    """Abre WhatsApp Web para que el usuario pueda escanear el QR si hace falta."""
    webbrowser.open(WHATSAPP_WEB_URL)
    return WhatsAppSendResult(
        success=True,
        phone_number="",
        message="",
        detail="WhatsApp Web abierto. Si no hay sesión activa, escanea el QR con tu teléfono.",
    )


def send_whatsapp_message(
    phone_number: str,
    message: str,
    *,
    wait_time: int = 15,
    tab_close: bool = False,
    close_time: int = 3,
    dry_run: bool = False,
) -> WhatsAppSendResult:
    """
    Envía un mensaje usando WhatsApp Web.

    pywhatkit abrirá el navegador. Si la cuenta no está conectada, WhatsApp Web
    mostrará el QR para iniciar sesión. El usuario debe completar ese paso.
    """
    normalized = normalize_phone_number(phone_number)
    if not message.strip():
        raise ValueError("El mensaje de WhatsApp no puede estar vacío.")

    if dry_run:
        return WhatsAppSendResult(
            success=True,
            phone_number=normalized,
            message=message,
            detail="Dry run: mensaje validado, no enviado.",
        )

    try:
        import pywhatkit
    except ImportError as exc:
        raise RuntimeError(
            "Falta la dependencia pywhatkit. Instala con: pip install pywhatkit"
        ) from exc

    pywhatkit.sendwhatmsg_instantly(
        phone_no=normalized,
        message=message,
        wait_time=wait_time,
        tab_close=tab_close,
        close_time=close_time,
    )
    return WhatsAppSendResult(
        success=True,
        phone_number=normalized,
        message=message,
        detail="Mensaje enviado o puesto en cola en WhatsApp Web.",
    )


def send_cloud_message(
    phone_number: str,
    message: str,
    *,
    config: WhatsAppCloudConfig | None = None,
    dry_run: bool = False,
    timeout: float = 20.0,
) -> WhatsAppSendResult:
    """Envía un mensaje usando WhatsApp Cloud API."""
    normalized = normalize_cloud_recipient(phone_number)
    if not message.strip():
        raise ValueError("El mensaje de WhatsApp no puede estar vacío.")

    if dry_run:
        return WhatsAppSendResult(
            success=True,
            phone_number=normalized,
            message=message,
            detail="Dry run Cloud API: mensaje validado, no enviado.",
        )

    cfg = config or load_cloud_config()
    url = (
        f"https://graph.facebook.com/{cfg.graph_api_version}/"
        f"{cfg.phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalized,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {cfg.access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return WhatsAppSendResult(
        success=True,
        phone_number=normalized,
        message=message,
        detail=f"Respuesta enviada por WhatsApp Cloud API: {response.status_code}",
    )


def handle_input_command(text: str, *, dry_run: bool = False) -> WhatsAppSendResult | None:
    """Ejecuta el comando si el input corresponde a esta skill."""
    parsed = parse_whatsapp_command(text)
    if parsed is None:
        return None
    phone_number, message = parsed
    return send_whatsapp_message(phone_number, message, dry_run=dry_run)


def verify_webhook(
    mode: str | None,
    token: str | None,
    challenge: str | None,
    *,
    config: WhatsAppCloudConfig | None = None,
) -> str | None:
    """Valida el GET inicial que Meta envía al configurar el webhook."""
    if config is None:
        _load_env_file()
        expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    else:
        expected_token = config.verify_token
    if mode == "subscribe" and token == expected_token and challenge:
        return challenge
    return None


def extract_incoming_messages(payload: dict[str, Any]) -> list[IncomingWhatsAppMessage]:
    """Extrae mensajes de texto desde el payload de WhatsApp Cloud API."""
    messages: list[IncomingWhatsAppMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for msg in value.get("messages", []) or []:
                if msg.get("type") != "text":
                    continue
                text = ((msg.get("text") or {}).get("body") or "").strip()
                sender = str(msg.get("from") or "").strip()
                message_id = str(msg.get("id") or "").strip()
                if sender and text:
                    messages.append(
                        IncomingWhatsAppMessage(
                            from_number=sender,
                            message_id=message_id,
                            text=text,
                            raw=msg,
                        )
                    )
    return messages


def default_responder(message: IncomingWhatsAppMessage) -> str:
    """Respuesta mínima cuando aún no se conecta el harness."""
    return (
        "Recibí tu solicitud por WhatsApp:\n"
        f"{message.text}\n\n"
        "El webhook está activo. Conecta este responder al harness para ejecutar tareas."
    )


def handle_webhook_payload(
    payload: dict[str, Any],
    *,
    responder: Callable[[IncomingWhatsAppMessage], str] = default_responder,
    dry_run: bool = False,
    config: WhatsAppCloudConfig | None = None,
) -> list[WhatsAppSendResult]:
    """
    Procesa mensajes entrantes y responde al mismo número.

    `responder` es donde se conecta la lógica del sistema: recibe el texto entrante,
    ejecuta tareas o consulta el LLM, y devuelve el texto final para WhatsApp.
    """
    results: list[WhatsAppSendResult] = []
    for message in extract_incoming_messages(payload):
        response_text = responder(message)
        results.append(
            send_cloud_message(
                message.from_number,
                response_text,
                config=config,
                dry_run=dry_run,
            )
        )
    return results


def create_webhook_app(
    *,
    responder: Callable[[IncomingWhatsAppMessage], str] = default_responder,
):
    """Crea una app Flask para exponer `/webhook`."""
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get("/webhook")
    def webhook_verify():
        challenge = verify_webhook(
            request.args.get("hub.mode"),
            request.args.get("hub.verify_token"),
            request.args.get("hub.challenge"),
        )
        if challenge is None:
            return "Forbidden", 403
        return challenge, 200

    @app.post("/webhook")
    def webhook_receive():
        payload = request.get_json(silent=True) or {}
        results = handle_webhook_payload(payload, responder=responder)
        return jsonify({"ok": True, "responses": len(results)}), 200

    return app


def harness_responder(message: IncomingWhatsAppMessage) -> str:
    """
    Ejecuta el harness desde un mensaje entrante y devuelve un resumen.

    Está pensado para webhooks; puede tardar porque corre el ciclo de tareas.
    """
    from llm_api_client import resolve_llm_chat_for_pipeline
    from harness.orchestrator import expand_features_from_goal, run_one_feature_cycle

    llm_chat, model_id, _label = resolve_llm_chat_for_pipeline("")
    added = expand_features_from_goal(
        message.text,
        model=model_id,
        llm_chat=llm_chat,
    )

    summaries: list[str] = [f"Creé {added} tarea(s) desde tu mensaje."]
    for _ in range(max(1, added)):
        result = run_one_feature_cycle(
            model=model_id,
            llm_chat=llm_chat,
            on_log=lambda _m: None,
        )
        if result is None:
            break
        summaries.append(f"Tarea {result.feature_id}: {result.message}")
        if result.verdict is not True:
            break

    return "\n".join(summaries)


if __name__ == "__main__":
    app = create_webhook_app(responder=harness_responder)
    app.run(host="0.0.0.0", port=int(os.getenv("WHATSAPP_WEBHOOK_PORT", "8080")))
