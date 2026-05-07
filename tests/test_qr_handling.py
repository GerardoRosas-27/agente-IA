import pytest
from unittest.mock import patch, MagicMock
import time
# Importaciones de la implementación simulada
from progress.qr_scanner_interface import ScanStatus
from progress.qr_processor_v0 import QRProcessor, TIMEOUT_SECONDS
from skills.qr_scan_handler import QrScanHandlerSkill

# --- Fixtures y Mocks ---
@pytest.fixture
def qr_processor():
    return QRProcessor()

@pytest.fixture
def handler_skill():
    """Fixture para inicializar el skill con un estado limpio."""
    return QrScanHandlerSkill()

# --- Test Suite 1: Escaneo Exitoso (AC1) ---
def test_successful_scan(qr_processor):
    """Test 1: Verifica que la lectura exitosa se parsea correctamente y actualiza el estado a SUCCESS."""
    mock_time = time.time() + 5 # Simular un tiempo futuro
    
    # Mockear tiempo para asegurar determinismo en los tests de timeout/timestamp
    with patch('time.time', return_value=mock_time):
        scanned_data = qr_processor.process_scan("ABC-123-SCAN", mock_time)
        
        assert scanned_data.status == ScanStatus.SUCCESS
        assert scanned_data.qr_content == "ABC-123-SCAN"

# --- Test Suite 2: Manejo de Errores (AC2 - Input) ---
def test_handling_null_input(qr_processor):
    """Test 2: Verifica que el input nulo o malformado maneje la excepción sin fallar."""
    mock_time = time.time() + 1
    scanned_data = qr_processor.process_scan(None, mock_time)
    
    assert scanned_data.status == ScanStatus.FAILED_INPUT
    assert scanned_data.qr_content == ""

def test_handling_malformed_input_valueerror(qr_processor):
    """Test 2b: Verifica que el procesamiento falle si el contenido es inválido (ej. contiene 'INVALID')."""
    mock_time = time.time() + 1
    scanned_data = qr_processor.process_scan("XYZ-INVALID", mock_time)

    assert scanned_data.status == ScanStatus.FAILED_INPUT
    # El contenido parseado debe ser vacío si la excepción fue atrapada.
    assert scanned_data.qr_content == "" 


# --- Test Suite 3: Timeout (AC2 - Time) ---
def test_scan_timeout(handler_skill):
    """Test 3: Simula el paso del tiempo para forzar la detección de timeout."""
    
    # Mockear time.time() dos veces: inicial y luego después del timeout
    initial_time = time.time()
    timeout_time = initial_time + TIMEOUT_SECONDS + 10

    # Inicializa el handler en un estado temporal (simulando la última interacción)
    with patch('time.time', return_value=initial_time):
        handler_skill._processor.update_timestamp(initial_time)
    
    # Ejecutamos el check_timeout simulando que ha pasado más tiempo
    with patch('time.time', return_value=timeout_time):
        scanned_data = handler_skill.handle_scan("dummy data")
        
        assert scanned_data.status == ScanStatus.TIMEOUT

# --- Test Suite 4: Integración del Skill (Flujo Completo) ---
def test_full_integration_flow(handler_skill):
    """Verifica el flujo completo de manejo de escaneo usando la API pública del Skill."""
    initial_time = time.time()

    # 1. Primer intento (Éxito)
    with patch('time.time', return_value=initial_time):
        result1 = handler_skill.handle_scan("SCAN-SUCCESS-001")
        assert result1.status == ScanStatus.SUCCESS

    # Simular un tiempo de espera corto para resetear el contador de timeout
    # 2. Segundo intento (Fallo por mal dato)
    with patch('time.time', return_value=initial_time + 5):
        result2 = handler_skill.handle_scan("SCAN-INVALID")
        assert result2.status == ScanStatus.FAILED_INPUT

