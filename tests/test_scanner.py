import pytest
from unittest.mock import AsyncMock

# Importaciones de las clases que vamos a testear
from src.scanner_service import QRScannerListener
from src.state_manager import StateManager, ConnectionState

# ===============================================
# FIXTURES REESTABILIZADOS PARA ASYNC/PYTEST
# ===============================================

@pytest.fixture()
def state_manager():
    """Fixture para inicializar un StateManager limpio antes de cada test."""
    return StateManager()

@pytest.fixture()
def scanner_listener():
    """Fixture para inicializar un QRScannerListener limpio antes de cada test."""
    return QRScannerListener()

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
    mock_state_update = AsyncMock()

    # 1. Registrar el mock en el listener (Simulando suscripción)
    register_mock_callback(scanner_listener, mock_state_update)

    # 2. Ejecutar la acción: Simular escaneo exitoso
    await scanner_listener.process_scan("QR-CODE-PAYLOAD-123", success=True)

    # 3. Verificación: Verificar que el callback fue llamado con éxito
    mock_state_update.assert_awaited_once_with(
        "Scan received: QR-CODE-PAYLOAD-123",
        {"raw": "QR-CODE-PAYLOAD-123"},
    )

@pytest.mark.asyncio
async def test_scan_failure(scanner_listener: QRScannerListener, state_manager: StateManager):
    """Test de fallo: El listener recibe un dato mal formado/inválido."""
    mock_state_update = AsyncMock()

    # 1. Registrar el mock
    register_mock_callback(scanner_listener, mock_state_update)

    # 2. Ejecutar la acción: Simular escaneo fallido (ej: código ilegible)
    await scanner_listener.process_scan("INVALID-DATA", success=False)

    # 3. Verificación: Verificar que el callback fue llamado con datos de error
    mock_state_update.assert_awaited_once_with(
        "Scan received: INVALID-DATA",
        {"error": "Invalid QR data"},
    )


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

    async def update_state(_message: str, payload: dict):
        await state_manager.handle_scan_success(payload["raw"])

    scanner_listener.register_listener(update_state)

    # Simular escaneo exitoso
    payload = "FINAL-PAYLOAD-XYZ"
    await scanner_listener.process_scan(payload, success=True) # Transiciona el estado
    
    # Verificación: El estado final debe ser CONNECTED
    assert state_manager.current_state == ConnectionState.CONNECTED
