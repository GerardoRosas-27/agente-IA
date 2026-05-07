import pytest
from unittest.mock import MagicMock
from typing import Awaitable

# Importaciones de las clases que vamos a testear
from src.scanner_service import QRScannerListener
from src.state_manager import StateManager, ConnectionState

# ===============================================
# FIXTURES REESTABILIZADOS PARA ASYNC/PYTEST
# ===============================================

@pytest.fixture()
async def state_manager():
    """Fixture asíncrono para inicializar un StateManager limpio antes de cada test."""
    sm = StateManager()
    yield sm
    # Cleanup después del test si fuera necesario, aunque la instancia en pytest suele ser limpia.
    pass

@pytest.fixture()
async def scanner_listener():
    """Fixture asíncrono para inicializar un QRScannerListener limpio antes de cada test."""
    listener = QRScannerListener()
    yield listener
    # Desregistra callbacks o realiza cleanup si fuera necesario
    pass

# Helper function to simulate callback registration
def register_mock_callback(scanner_listener: QRScannerListener, mock_handler):
    """Asigna un mock de manejador al listener para simular la suscripción."""
    scanner_listener.register_listener(mock_handler)

# ===============================================
# PRUEBAS UNITARIAS CON ESTADO STABLE
# ===============================================

@pytest.mark.asyncio
async def test_scan_success(scanner_listener: QRScannerListener, state_manager: StateManager):
    """Test de éxito: El listener recibe y notifica un escaneo válido."""
    mock_state_update = MagicMock() # Mock para el callback que actualiza el estado

    # 1. Registrar el mock en el listener (Simulando suscripción)
    register_mock_callback(scanner_listener, mock_state_update)

    # 2. Ejecutar la acción: Simular escaneo exitoso
    await scanner_listener.process_scan("QR-CODE-PAYLOAD-123", success=True)

    # 3. Verificación: Verificar que el callback fue llamado con éxito
    mock_state_update.assert_awaited_with(pytest.approx("Scan received: QR-CODE-PAYLOAD-123"), pytest.approx({"raw": "QR-CODE-PAYLOAD-123"}))

@pytest.mark.asyncio
async def test_scan_failure(scanner_listener: QRScannerListener, state_manager: StateManager):
    """Test de fallo: El listener recibe un dato mal formado/inválido."""
    mock_state_update = MagicMock()

    # 1. Registrar el mock
    register_mock_callback(scanner_listener, mock_state_update)

    # 2. Ejecutar la acción: Simular escaneo fallido (ej: código ilegible)
    await scanner_listener.process_scan("INVALID-DATA", success=False)

    # 3. Verificación: Verificar que el callback fue llamado con datos de error
    mock_state_update.assert_awaited_with(pytest.approx("Scan received: INVALID-DATA"), pytest.approx({"error": "Invalid QR data"}))


@pytest.mark.asyncio
async def test_timeout_handling(state_manager: StateManager, scanner_listener: QRScannerListener):
    """Test de Timeout: Simula que el tiempo límite ha expirado y fuerza el cambio de estado."""

    # 1. Configurar el estado inicial (el sistema está esperando)
    await state_manager.initiate_scan_wait()
    assert state_manager.current_state == ConnectionState.WAITING_FOR_SCAN

    # 2. Ejecutar la acción: Simular el timeout que fuerza el cambio de estado
    await state_manager.handle_timeout()

    # 3. Verificación: El estado debe reflejar el timeout
    assert state_manager.current_state == ConnectionState.TIMEOUT

@pytest.mark.asyncio
async def test_successful_connection_flow(state_manager: StateManager, scanner_listener: QRScannerListener):
    """Flujo completo de éxito: Wait -> Scan Success -> Connected."""
    await state_manager.initiate_scan_wait() # Estado 1: Waiting

    # Simular escaneo exitoso
    payload = "FINAL-PAYLOAD-XYZ"
    await scanner_listener.process_scan(payload, success=True) # Transiciona el estado
    
    # Verificación: El estado final debe ser CONNECTED
    assert state_manager.current_state == ConnectionState.CONNECTED
