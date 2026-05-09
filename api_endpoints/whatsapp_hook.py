import json
from typing import Dict, Any, Optional

# Utility function for processing the incoming payload structure
def process_incoming_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extrae y normaliza los datos de un mensaje entrante complejo de WhatsApp.
    Determina si el contenido es texto o multimedia.

    Args:
        payload: El diccionario JSON parseado del payload del webhook.

    Returns:
        Un dict estandarizado con información del evento, o None si no hay mensaje.
    """
    try:
        # Navegar por la estructura anidada típica de WhatsApp Webhook
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        if "messages" not in value:
            return None  # No es un mensaje, podría ser otro tipo de evento (status, etc.)

        messages = value["messages"]
        if not isinstance(messages, list) or not messages:
            return None
        message_data = messages[0]
    except (KeyError, IndexError):
        print("WARNING: Payload structure is unexpected.")
        return None

    event: Dict[str, Any] = {
        "event_type": "message_received",
        "sender_id": message_data.get("from"),
        "timestamp": message_data.get("timestamp"),
        "content": "",
        "media_detected": False,
        "metadata": {}
    }

    # Manejo específico por tipo de mensaje
    message_type = message_data.get("type")

    if message_type == "text":
        text_data = message_data.get("text", "")
        if isinstance(text_data, dict):
            event["content"] = text_data.get("body", "")
        else:
            event["content"] = text_data
    elif message_type == "image":
        media_id = message_data.get("image", {}).get("id")
        event["content"] = f"[IMAGE: {media_id}]"  # Placeholder para referencia media
        event["media_detected"] = True
        event["metadata"]["media_type"] = "image"
    elif message_type == "video":
        media_id = message_data.get("video", {}).get("id")
        event["content"] = f"[VIDEO: {media_id}]"
        event["media_detected"] = True
        event["metadata"]["media_type"] = "video"
    else:
        # Manejo de otros tipos (document, audio) si es necesario en el futuro.
        return None

    return event

def handle_whatsapp_hook(request: Dict[str, Any], method: str) -> tuple[int, str]:
    """
    Maneja la petición entrante del Webhook de WhatsApp/Meta.
    """
    if method == "GET":
        # Fase 1.2: Manejo de Verificación (Handshake)
        challenge = request.get("query", {}).get("hub.challenge")
        if challenge:
            print(f"DEBUG: Handling webhook verification challenge.")
            return 200, str(challenge)
        return 400, "Missing challenge parameter."

    elif method == "POST":
        # Fase 2 & 3: Parsing y Normalización de Payload
        try:
            payload = request.get("body", {})
        except Exception as e:
             print(f"ERROR processing body: {e}")
             return 400, "Invalid payload format."

        structured_event = process_incoming_payload(payload)

        if structured_event:
            # En un sistema real, esto se enviaría a una cola (Kafka/RabbitMQ)
            print("\n--- EVENTO NORMALIZADO Y LISTO PARA CONSUMO CENTRAL ---\n")
            print(json.dumps(structured_event, indent=2))
            return 200, "Message processed successfully."
        else:
            # El payload fue recibido pero no contenía un mensaje válido o era otro evento ignorado.
            return 200, "Payload received but no message event detected."

    return 405, "Method not allowed."
