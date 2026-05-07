from src.scanner_service import QRScannerListener, ScannerEvent
# from src.state_manager import StateManager # Asumiendo que está disponible

class UIComponent:
    """Representa la capa de presentación que consume los eventos."""
    def __init__(self, scanner_listener: QRScannerListener):
        self.scanner = scanner_listener
        # Suscribirse al listener en el constructor (o en un método 'initialize')
        self.scanner.subscribe(self._handle_scan_event)

    async def _handle_scan_event(self, event: ScannerEvent):
        """Callback central para procesar cualquier evento de escaneo."""
        if event.success and event.payload:
            print(f"\n[UI] ✅ CONEXIÓN EXITOSA DETECTADA.")
            print(f"[UI] Payload QR recibido: {event.payload[:10]}...")
            # Aquí se llamaría al StateManager para transición a SUCCESS
        elif not event.success and event.error:
             print(f"\n[UI] ❌ FALLO DE ESCANEO: {event.error}")
             # Aquí se informaría al usuario y al StateManager sobre el fallo
        else:
            print("\n[UI] Evento de escaneo ambiguo o incompleto.")

    def display_status(self, state):
        """Muestra el estado actual del sistema."""
        if state == "WAITING_FOR_SCAN":
            print("--------------------------------------------")
            print("[STATUS] ⏳ Esperando conexión QR. Por favor escanee su código...")
            print("--------------------------------------------")
        elif state == "SUCCESS":
            print("\n[STATUS] ⭐ Conexión establecida con éxito.")
        else:
             print(f"\n[STATUS] Estado actual: {state}")

