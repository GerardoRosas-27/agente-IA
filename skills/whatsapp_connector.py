from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from skills.wa_business_api_client import (
    APIAuthenticationError,
    RecipientNotFoundError,
    WaBusinessApiClient,
    WhatsAppAPIError,
)


@dataclass(frozen=True)
class WhatsAppCloudConfig:
    access_token: str
    phone_number_id: str
    verify_token: str
    graph_api_version: str = "v20.0"

    @classmethod
    def from_env(cls) -> "WhatsAppCloudConfig":
        return cls(
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
            verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
            graph_api_version=os.getenv("WHATSAPP_GRAPH_API_VERSION", "v20.0"),
        )


@dataclass(frozen=True)
class WhatsAppSendResult:
    success: bool
    phone_number: str
    message: str
    detail: str


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    from_number: str
    message_id: str
    message_type: str
    text: str
    raw: dict


def normalize_phone_number(phone_number: str) -> str:
    normalized = re.sub(r"[\s().-]", "", phone_number)
    if not re.fullmatch(r"\+\d{8,15}", normalized):
        raise ValueError("El número debe incluir código de país, por ejemplo +521234567890.")
    return normalized


def parse_whatsapp_command(text: str) -> Optional[tuple[str, str]]:
    match = re.match(r"^\s*/?(?:whatsapp|wa)\s+(\+\d[\d\s().-]*)\s*\|\s*(.+?)\s*$", text)
    if not match:
        return None
    return normalize_phone_number(match.group(1)), match.group(2)


def send_whatsapp_message(
    phone_number: str,
    message: str,
    *,
    wait_time: int = 10,
    tab_close: bool = False,
    close_time: int = 3,
) -> WhatsAppSendResult:
    normalized = normalize_phone_number(phone_number)
    import pywhatkit

    pywhatkit.sendwhatmsg_instantly(
        phone_no=normalized,
        message=message,
        wait_time=wait_time,
        tab_close=tab_close,
        close_time=close_time,
    )
    return WhatsAppSendResult(True, normalized, message, "Mensaje enviado con WhatsApp Web.")


def handle_input_command(text: str, *, dry_run: bool = False) -> Optional[WhatsAppSendResult]:
    parsed = parse_whatsapp_command(text)
    if parsed is None:
        return None

    phone_number, message = parsed
    if dry_run:
        return WhatsAppSendResult(True, phone_number, message, "Dry run: mensaje no enviado.")
    return send_whatsapp_message(phone_number, message)


def verify_webhook(
    mode: str,
    verify_token: str,
    challenge: str,
    *,
    config: Optional[WhatsAppCloudConfig] = None,
) -> Optional[str]:
    cfg = config or WhatsAppCloudConfig.from_env()
    if mode == "subscribe" and verify_token == cfg.verify_token:
        return challenge
    return None


def send_cloud_message(
    phone_number: str,
    message: str,
    *,
    config: Optional[WhatsAppCloudConfig] = None,
    dry_run: bool = False,
) -> WhatsAppSendResult:
    if dry_run:
        return WhatsAppSendResult(True, phone_number, message, "Dry run Cloud API: mensaje no enviado.")

    cfg = config or WhatsAppCloudConfig.from_env()
    if not cfg.access_token or not cfg.phone_number_id:
        raise APIAuthenticationError("WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID son obligatorios.")

    url = f"https://graph.facebook.com/{cfg.graph_api_version}/{cfg.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": message},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return WhatsAppSendResult(True, phone_number, message, "Mensaje enviado con WhatsApp Cloud API.")


def extract_incoming_messages(payload: dict) -> list[IncomingWhatsAppMessage]:
    messages: list[IncomingWhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_type = message.get("type", "")
                text_data = message.get("text", {})
                text = text_data.get("body", "") if isinstance(text_data, dict) else str(text_data)
                messages.append(
                    IncomingWhatsAppMessage(
                        from_number=message.get("from", ""),
                        message_id=message.get("id", ""),
                        message_type=message_type,
                        text=text,
                        raw=message,
                    )
                )
    return messages


def harness_responder(message: IncomingWhatsAppMessage) -> str:
    return f"Mensaje recibido: {message.text}"


def handle_webhook_payload(
    payload: dict,
    *,
    responder: Callable[[IncomingWhatsAppMessage], str] = harness_responder,
    config: Optional[WhatsAppCloudConfig] = None,
    dry_run: bool = False,
) -> list[WhatsAppSendResult]:
    results: list[WhatsAppSendResult] = []
    for message in extract_incoming_messages(payload):
        response_text = responder(message)
        results.append(send_cloud_message(message.from_number, response_text, config=config, dry_run=dry_run))
    return results

class WhatsAppConnector:
    """
    Orquesta el uso del cliente de la API de WhatsApp Business.
    Actúa como capa de negocio para aislar la lógica del endpoint HTTP específico.
    """

    def __init__(self):
        # El conector no debe mantener las credenciales, solo la lógica de orquestación.
        pass

    def configure_and_test(self, access_token: str, account_id: str, test_recipient_number: str) -> bool:
        """
        Intenta inicializar la conexión y enviar un mensaje de prueba utilizando credenciales proporcionadas.

        Args:
            access_token: Token de acceso API.
            account_id: ID de la cuenta de WhatsApp Business.
            test_recipient_number: Número de destino para la prueba (ej: 'whatsapp:+123456789').

        Returns:
            True si el proceso es exitoso, False en caso contrario.

        Raises:
            APIAuthenticationError: Si las credenciales son inválidas.
            RecipientNotFoundError: Si el número de destino no es válido o está bloqueado.
            WhatsAppAPIError: Otros errores graves de conexión/servicio.
        """
        try:
            # 1. Inicializar el cliente con parámetros proporcionados (mejor que env vars)
            client = WaBusinessApiClient(token=access_token, account_id=account_id)

            # 2. Verificar Conexión (Fase de autenticación base)
            if not client.initialize_connection():
                raise WhatsAppAPIError("No se pudo verificar la conexión inicial con la API.")

            # 3. Enviar Mensaje de Prueba (Flujo completo de prueba de aceptación)
            message = "Hola, este es un mensaje de prueba exitoso del sistema."
            response = client.send_test_message(to_number=test_recipient_number, message_text=message)

            print("✅ Conexión y Test Exitosos.")
            return response

        except (APIAuthenticationError, RecipientNotFoundError, WhatsAppAPIError) as e:
            # Capturamos la excepción específica y notificamos el fallo.
            print(f"❌ FALLO DE CONEXIÓN O TEST: {e}")
            return False
        except Exception as e:
            print(f"🚨 ERROR NO CAPTURADO EN EL ORQUESTADOR: {e}")
            return False

