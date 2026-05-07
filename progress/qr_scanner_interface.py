import time
from enum import Enum
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

# Definición del estado posible del scanner en cualquier momento dado
class ScanStatus(Enum):
    PENDING = "PENDING"        # Esperando escaneo.
    SUCCESS = "SUCCESS"        # Escaneo exitoso y procesado.
    FAILED_INPUT = "FAILED_INPUT" # Error debido a input nulo o malformado.
    TIMEOUT = "TIMEOUT"        # Expiró el tiempo máximo permitido para escanear.

@dataclass
class ScannedData:
    """Datos estructurados resultantes de un escaneo QR exitoso."""
    qr_content: str          # El dato extraído del código.
    scan_timestamp: float    # Tiempo exacto de la captura (unix time).
    status: ScanStatus       # Estado final de este intento.

@dataclass
class ScannerState:
    """Representa el estado completo y persistente del manejo del escaneo."""
    last_update_time: float = field(default_factory=time.time)
    current_status: ScanStatus = ScanStatus.PENDING
    is_active: bool = True
