import pytest
from unittest.mock import patch, MagicMock
import asyncio
from datetime import datetime, timedelta
from skills.qr_scanner_service import QRScannerService, ScanState


@pytest.fixture
def scanner():
    """Fixture que proporciona una instancia limpia del servicio."""
    return QRScannerService()

# Función auxiliar para esperar en tests asíncronos sin bloquear el orquestador
async def wait(seconds=0.1):
    await asyncio.sleep(seconds)


@pytest.mark.asyncio
async def test_initialization_state(scanner: QRScannerService):
    """Verifica que el servicio comienza en estado DISCONNECTED."""
    assert scanner.state == ScanState.DISCONNECTED

@pytest.mark.asyncio
async def test_successful_scan_flow(scanner: QRScannerService):
    """Test 1: Simula un escaneo QR válido y exitoso (Success)."""
    await scanner.start_scanning()
    # Esperamos a que el estado se ponga en PENDING_SCAN (aunque el sleep no es perfecto, ayuda)
    await wait(0.5)

    success = await scanner.process_scan("SECURE_QR_VALID_DATA_12345")
    
    assert success is True
    # Verificar que el estado final sea CONNECTED
    assert scanner.state == ScanState.CONNECTED


@pytest.mark.asyncio
async def test_failed_scan_due_to_invalid_data(scanner: QRScannerService):
    """Test 3: Simula un escaneo con datos inválidos o ilegibles (Failure)."""
    await scanner.start_scanning()
    await wait(0.5)

    # Intentar escanear con datos muy cortos o vacíos
    success = await scanner.process_scan("data") 
    
    assert success is False
    # Verificar que el estado final sea FAILED
    assert scanner.state == ScanState.FAILED


@pytest.mark.asyncio
@patch('skills.qr_scanner_service.QRScannerService._check_timeout_periodically', new=MagicMock())
async def test_scan_timeout_flow(scanner: QRScannerService):
    """Test 2: Simula el transcurso del tiempo sin acción (Timeout)."""
    # Para este test, forzamos que la lógica de timeout se ejecute.
    # Mockeamos start_time para simular un periodo de espera muy largo.
    scanner.start_scanning()
    await wait(0.5)

    # 1. Forzar el estado PENDING y configurar start_time artificialmente en el pasado
    original_start_time = scanner.start_time
    if original_start_time:
        # Retrocedemos la hora inicial para simular un tiempo transcurrido
        scanner.start_time = datetime.now() - timedelta(seconds=35)

    # 2. Ejecutar el chequeo de timeout que debe detectar el estado EXPIRED
    await scanner._check_timeout_periodically()
    
    # Nota: Dado que _check_timeout_periodically usa un bucle infinito, lo forzamos a solo verificar la expiración
    # En un ambiente real esto fallaría por Timeout/RuntimeError si no se maneja el break. 
    # Aquí confiamos en que la lógica interna simule el cambio de estado al pasar del tiempo.

    await wait(0.5) # Dar tiempo a los prints y mockeos
    assert scanner.state == ScanState.EXPIRED


@pytest.mark.asyncio
async def test_scanner_cannot_scan_if_already_connected(scanner: QRScannerService):
    """Prueba de borde: Intenta escanear cuando ya está conectado."""
    await scanner.start_scanning()
    await wait(0.5)

    # Forzarlo a estado CONNECTED manualmente para la prueba
    scanner._state = ScanState.CONNECTED 

    success = await scanner.process_scan("VALID_DATA")
    assert success is False
