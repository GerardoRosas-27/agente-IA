from enum import Enum
import asyncio
from datetime import datetime

class ConnectionState(Enum):
    DISCONNECTED = "Disconnected"
    WAITING_FOR_SCAN = "Waiting for QR Scan..."
    CONNECTING = "Connecting..."
    CONNECTED = "Connected"
    TIMEOUT = "Connection Timeout"
    ERROR = "Error State"


class StateManager:
    """Gestiona el estado de la conexión a través del ciclo de vida."""
    def __init__(self):
        self._current_state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_scan_time: datetime | None = None

    @property
    def current_state(self) -> ConnectionState:
        return self._current_state

    def set_state(self, new_state: ConnectionState):
        """Establece el estado actual del sistema."""
        print(f"STATE CHANGE: {self._current_state.name} -> {new_state.name}")
        self._current_state = new_state

    async def initiate_scan_wait(self):
        """Transiciona a WAITING y establece la lógica de timeout (simulado)."""
        if self._current_state != ConnectionState.DISCONNECTED:
            print("Warning: Cannot start scan wait from current state.")
            return

        self.set_state(ConnectionState.WAITING_FOR_SCAN)
        # En un sistema real, se lanzaría un task de asyncio aquí para el timeout.
        pass # Lógica de temporizador delegada al Orchestrator por ahora.

    async def handle_scan_success(self, qr_data: str):
        """Procesa un escaneo exitoso y transiciona a CONNECTED."""
        print(f"Handling successful scan with data: {qr_data}")
        self._last_scan_time = datetime.now()
        self.set_state(ConnectionState.CONNECTED)

    async def handle_timeout(self):
        """Se llama cuando transcurre el tiempo límite de escaneo."""
        print("Timeout detected: QR scan expected but not received.")
        self._last_scan_time = None
        self.set_state(ConnectionState.TIMEOUT)
