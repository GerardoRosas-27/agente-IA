import os
from typing import Optional, Dict, Any
import requests
from datetime import timedelta

# --- Custom Exceptions for robust error handling ---

class WhatsAppAPIError(Exception):
    """Base exception for all WhatsApp API related errors."""
    pass

class APIAuthenticationError(WhatsAppAPIError):
    """Raised when authentication credentials (token/ID) are invalid or missing."""
    pass

class RecipientNotFoundError(WhatsAppAPIError):
    """Raised when the target recipient number is invalid or blocked by WhatsApp."""
    pass

# --- Client Implementation ---

class WaBusinessApiClient:
    """
    Cliente especializado para interactuar con la API de WhatsApp Business Cloud.
    Encapsula la lógica de autenticación y envío de mensajes.
    """
    BASE_URL = "https://graph.facebook.com/v1.0/" # Usar v1.0 o el más actual

    def __init__(self, token: str, account_id: str):
        """Inicializa el cliente con las credenciales necesarias."""
        if not token or not account_id:
            raise APIAuthenticationError("Token y Account ID son obligatorios para inicializar la conexión.")
            
        self.token = token
        self.account_id = account_id

    def _make_api_request(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Método interno genérico para realizar peticiones a la API."""
        url = f"{self.BASE_URL}{self.account_id}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_details = e.response.json().get('error', {})
            code = error_details.get('code')
            message = error_details.get('message', str(e))

            message_lower = message.lower()

            if code in [10047, 18]: # Códigos comunes de token/permiso inválido
                raise APIAuthenticationError(f"Autenticación fallida (Código {code}): {message}")
            elif (
                'invalid recipient' in message_lower
                or 'recipient not found' in message_lower
                or 'recipient number is invalid' in message_lower
            ):
                 raise RecipientNotFoundError(f"El número de destinatario es inválido o no puede recibir mensajes: {message}")
            else:
                # Lanzar la excepción original con contexto para otros errores API.
                raise WhatsAppAPIError(f"Error API al conectar/enviar ({e.response.status_code}): {message}")

    def initialize_connection(self) -> bool:
        """
        Verifica la conexión inicial enviando una llamada de verificación simple 
        o simplemente validando que el token esté activo.
        Para este ejemplo, solo verificaremos que podamos acceder al endpoint base.
        """
        print("INFO: Intentando inicializar conexión con WhatsApp API...")
        try:
            # Endpoint dummy para verificar autenticación sin enviar mensaje real.
            # Esto podría ser un simple 'me' o el status del negocio en la API de Meta.
            self._make_api_request(endpoint="profile", data={})
            return True
        except WhatsAppAPIError as e:
            print(f"ERROR: Falló la inicialización de conexión: {e}")
            raise

    def send_test_message(self, to_number: str, message_text: str) -> Dict[str, Any]:
        """
        Envía un mensaje de prueba al número de destino. 
        Debe manejar la lógica del cuerpo JSON requerido por WhatsApp.
        """
        print(f"INFO: Enviando mensaje de prueba a {to_number}...")
        data = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message_text}
        }
        try:
            # Endpoint /messages para enviar el mensaje
            response = self._make_api_request(endpoint="messages", data=data)
            return response

        except (APIAuthenticationError, RecipientNotFoundError) as e:
            raise e # Re-lanzar excepciones controladas.
        except WhatsAppAPIError as e:
            print(f"ERROR al enviar mensaje: {e}")
            raise


# NOTA SOBRE WEBHOOKS: 
# Para recibir mensajes entrantes, la lógica debe residir en un endpoint HTTP de nuestro servidor (Webhook)
# que reciba y valide el payload JSON del webhook de Meta. Este cliente solo maneja la acción saliente.
