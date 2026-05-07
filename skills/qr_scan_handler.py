import time
from typing import Optional
from progress.qr_scanner_interface import ScannedData, ScanStatus, ScannerState
# Importamos el motor de procesamiento interno
from progress.qr_processor_v0 import QRProcessor, notify_user

class QrScanHandlerSkill:
    """
    Skill principal para gestionar y procesar la entrada de escaneo QR 
    desde fuentes externas. Debe ser invocado por el Orchestrator.
    """
    def __init__(self):
        self._processor = QRProcessor()
        # Inicializamos un estado base al cargar el skill
        self.state: ScannerState = self._processor.state

    def handle_scan(self, raw_data: Optional[str]) -> ScannedData:
        """
        Método público para recibir los datos brutos del escaneo y procesarlos.
        Retorna el objeto de datos parseado con el estado final.
        """
        current_time = time.time()

        # 1. Verificar Timeout antes de cualquier procesamiento (AC2)
        timeout_message = self._processor.check_timeout()
        if timeout_message:
            print(f"Timeout check triggered: {timeout_message}")
            notify_user(ScanStatus.TIMEOUT, "El tiempo límite ha expirado.")
            # Retornamos un objeto de fallo explícito por timeout
            return ScannedData(qr_content="", scan_timestamp=current_time, status=ScanStatus.TIMEOUT)

        # 2. Procesar el escaneo
        scanned_data = self._processor.process_scan(raw_data, current_time)
        self._processor.update_timestamp(current_time)
        
        # 3. Notificación (side-effect)
        notify_user(scanned_data.status, scanned_data.qr_content)

        return scanned_data

    def check_and_reset_state(self):
        """Verifica el tiempo y reinicia el estado si ha pasado mucho tiempo."""
        # Llamar a check_timeout es suficiente para actualizar el estado interno de manera segura
        self._processor.check_timeout() 
        print(f"State checked. Current status: {self.state.current_status}")

