import time
from typing import Optional
from progress.qr_scanner_interface import ScannedData, ScanStatus, ScannerState

TIMEOUT_SECONDS = 30  # Tiempo máximo para intentar un escaneo en segundos

class QRProcessor:
    """Procesa los datos brutos del escaneo y actualiza el estado interno."""

    def __init__(self):
        self.state = ScannerState()

    def process_scan(self, scan_data: Optional[str], current_timestamp: float) -> ScannedData:
        """
        Maneja la entrada del escaneo. 
        Retorna un objeto con los datos parseados y el estado resultante.
        """
        if not scan_data or not isinstance(scan_data, str):
            # AC2 - Manejo de inputs nulos/malformados
            print("Warning: Received null or malformed scan data.")
            return ScannedData(qr_content="", scan_timestamp=current_timestamp, status=ScanStatus.FAILED_INPUT)

        try:
            # Simulación de lógica compleja de parsing y validación del QR
            if "INVALID" in scan_data.upper():
                raise ValueError("El contenido QR detectado no es un formato esperado.")

            parsed_content = self._parse_qr(scan_data)
            
            scanned_data = ScannedData(
                qr_content=parsed_content, 
                scan_timestamp=current_timestamp, 
                status=ScanStatus.SUCCESS
            )
            self.state.current_status = ScanStatus.SUCCESS
            return scanned_data

        except ValueError as e:
            print(f"Error de procesamiento QR: {e}")
            scanned_data = ScannedData(qr_content="", scan_timestamp=current_timestamp, status=ScanStatus.FAILED_INPUT)
            self.state.current_status = ScanStatus.FAILED_INPUT
            return scanned_data

    def _parse_qr(self, raw_data: str) -> str:
        """Simula la extracción y normalización del contenido útil."""
        # Aquí iría lógica real de parsing (e.g., regex, base64 decode)
        return raw_data.upper().strip()

    def check_timeout(self) -> Optional[str]:
        """Verifica si el tiempo transcurrido desde la última actualización excede el límite."""
        time_elapsed = time.time() - self.state.last_update_time
        if time_elapsed > TIMEOUT_SECONDS:
            # AC2 - Detección de expiración
            self.state.current_status = ScanStatus.TIMEOUT
            return "Timeout detected: Exceeded maximum scanning period."
        return None

    def update_timestamp(self, timestamp: float):
        """Actualiza el tiempo de la última interacción."""
        self.state.last_update_time = timestamp


# Función side-effect simulada para notificación al usuario
def notify_user(status: ScanStatus, message: str):
    """Notifica al sistema principal sobre el resultado del escaneo."""
    if status == ScanStatus.SUCCESS:
        print(f"[NOTIFICATION SUCCESS] Conexión exitosa. Datos procesados: {message}")
    elif status == ScanStatus.TIMEOUT:
        print(f"[NOTIFICATION FAILURE] Fallo de conexión por tiempo expirado.")
    else:
        print(f"[NOTIFICATION ERROR] Error en el escaneo o datos inválidos.")
