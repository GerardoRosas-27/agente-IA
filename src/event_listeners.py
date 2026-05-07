from typing import Callable, List, Optional
import asyncio

# Define el tipo de callback para manejar datos de escaneo (data) y éxito (success)
QRScanCallback = Callable[[str], None]

class EventListener:
    """Gestor centralizado para escuchar eventos del sistema."""
    def __init__(self):
        self._handlers: dict[str, List[Callable]] = {}

    # ... (métodos existentes como on_system_ready, etc.)

    async def register_qr_scan_handler(self, callback: QRScanCallback) -> None:
        """Registra un manejador de eventos para el escaneo de códigos QR."""
        if "qr_scan" not in self._handlers:
            self._handlers["qr_scan"] = []
        
        # Añadimos la función callback proporcionada por el componente que implemente la lógica (el procesador)
        self._handlers["qr_scan"].append(callback)
        print("✅ QR Scan Handler registrado exitosamente.")

    async def simulate_qr_scan_event(self, data: str):
        """Simula la recepción de un evento de escaneo desde una fuente externa (ej. WebSocket)."""
        if "qr_scan" not in self._handlers or not self._handlers["qr_scan"]:
            print("⚠️ Advertencia: No hay handlers registrados para QR Scan.")
            return

        # Ejecutamos todos los callbacks registrados de manera asíncrona
        tasks = [callback(data) for callback in self._handlers["qr_scan"]]
        await asyncio.gather(*tasks)

