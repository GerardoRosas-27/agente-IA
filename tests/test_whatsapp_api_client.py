import pytest
from unittest import mock
import requests
from skills.wa_business_api_client import (
    WaBusinessApiClient, APIAuthenticationError, RecipientNotFoundError, WhatsAppAPIError
)

# --- Fixtures and Mocks Setup ---

@pytest.fixture
def client():
    """Fixture para crear un cliente de prueba con credenciales simuladas."""
    return WaBusinessApiClient(token="valid_test_token", account_id="mock_account_id")


# --- Helper Mocking Functionality for HTTP requests ---

def mock_http_response(status_code: int, json_data: dict, error_message: str = None):
    """Genera un objeto de respuesta mockado para el módulo requests."""
    mock_resp = mock.Mock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    
    # Configurar la excepción HTTP que se lanzaría al verificar errores
    if status_code >= 400:
        error_detail = {"error": {"code": error_message}} if error_message else {}
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} Client Error", response=mock_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp

# --- Test Cases ---

@mock.patch("skills.wa_business_api_client.requests.post")
def test_successful_initialization(mock_post, client):
    """Prueba 1: Happy Path - La inicialización del cliente debe pasar exitosamente."""
    SUCCESS_RESPONSE = {
        "status": "success", 
        "message": "API connection verified."
    }
    # Simular respuesta HTTP 200 OK
    mock_post.return_value = mock_http_response(200, SUCCESS_RESPONSE)

    try:
        result = client.initialize_connection()
        assert result is True
    except WhatsAppAPIError as e:
        pytest.fail(f"La inicialización falló inesperadamente con un error de API: {e}")


@mock.patch("skills.wa_business_api_client.requests.post")
def test_initialization_failure_auth(mock_post, client):
    """Prueba 2a: Fallo de autenticación durante la inicialización (Token inválido)."""
    # Simular un error HTTP 400 con mensaje de token inválido
    FAILURE_AUTH = {"error": {"code": 10047, "message": "Invalid access token."}}
    mock_post.return_value = mock_http_response(400, FAILURE_AUTH, "Token inválido")

    with pytest.raises(APIAuthenticationError) as excinfo:
        client.initialize_connection()
    assert "Autenticación fallida" in str(excinfo.value)


@mock.patch("skills.wa_business_api_client.requests.post")
def test_send_test_message_success(mock_post, client):
    """Prueba 3: Happy Path - El envío de mensaje debe funcionar y devolver el resultado esperado."""
    MESSAGE_RESPONSE = {
        "messaging_product": "whatsapp",
        "messages": [{"id": "msg-12345"}],
        "status": "sent"
    }
    # Simular respuesta HTTP 200 OK para envío exitoso
    mock_post.return_value = mock_http_response(200, MESSAGE_RESPONSE)

    result = client.send_test_message("whatsapp:+123456789", "Hola mundo")
    assert result["status"] == "sent"


@mock.patch("skills.wa_business_api_client.requests.post")
def test_send_test_message_auth_failure(mock_post, client):
    """Prueba 2b: Fallo de credenciales durante el envío."""
    FAILURE_AUTH = {"error": {"code": 10047, "message": "Token expirado."}}
    # Simular error HTTP 400 (Auth)
    mock_post.return_value = mock_http_response(400, FAILURE_AUTH, "Token inválido")

    with pytest.raises(APIAuthenticationError) as excinfo:
        client.send_test_message("whatsapp:+123456789", "Prueba")
    assert "Autenticación fallida" in str(excinfo.value)


@mock.patch("skills.wa_business_api_client.requests.post")
def test_send_test_message_recipient_failure(mock_post, client):
    """Prueba 3b: Fallo de destinatario no encontrado."""
    # Simular error HTTP 400 con mensaje específico de receptor inválido
    FAILURE_RECIPIENT = {"error": {"code": 162, "message": "The recipient number is invalid or blocked."}}
    mock_post.return_value = mock_http_response(400, FAILURE_RECIPIENT, None)

    with pytest.raises(RecipientNotFoundError) as excinfo:
        client.send_test_message("whatsapp:+1999999", "Prueba")
    assert "número de destinatario es inválido" in str(excinfo.value)

