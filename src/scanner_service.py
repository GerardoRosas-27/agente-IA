from typing import Callable, Any

class QRScannerListener:
    """
    Listener asíncrono responsable de recibir entradas del escáner QR.
    Emite eventos al ser llamado con un payload exitoso o fallido.
    """
    def __init__(self):
        # Lista de callbacks/event handlers para notificar el resultado
        self._callbacks: list[Callable[[str, dict], None]] = []

    def register_listener(self, callback: Callable[[str, dict], None]):
        """Registra una función de callback que procesará los eventos."""
        self._callbacks.append(callback)

    async def process_scan(self, raw_data: str, success: bool = True):
        """
        Método llamado por el sistema externo (ej: dispositivo lector). 
        Procesa la entrada y notifica a todos los listeners registrados.
        """
        payload: dict[str, Any] = {"raw": raw_data} if success else {"error": "Invalid QR data"}
        message = f"Scan received: {raw_data}"

        # Notificar asíncronamente a todos los componentes suscritos
        await self._notify(success, message, payload)

    async def _notify(self, success: bool, message: str, payload: dict[str, Any]):
        """Notifica a los callbacks registrados."""
        for callback in self._callbacks:
            # Ejecutar el callback de forma asíncrona para no bloquearse.
            try:
                await callback(message, payload)
            except Exception as e:
                print(f"Warning: Callback failed to execute for scan event: {e}")

    @property
    def is_listening(self) -> bool:
        return True # Simplificación para el propósito del harness
