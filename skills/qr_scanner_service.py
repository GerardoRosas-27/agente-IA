from enum import Enum
from typing import Optional
from datetime import datetime
import asyncio

class ScanState(Enum):
    """Define los posibles estados del servicio de escaneo QR."""
    DISCONNECTED = "No conectado, esperando iniciar."
    PENDING_SCAN = "Esperando escanear un código QR válido..."
    CONNECTING = "Procesando la conexión establecida por el QR..."
    CONNECTED = "Conexión exitosa y activa."
    FAILED = "Fallo de lectura o error en la red. Intente de nuevo."
    EXPIRED = "Timeout. La sesión expiró sin completar el escaneo."


class QRScannerService:
    """
    Servicio que encapsula la lógica para detectar, procesar y manejar el estado 
    de una conexión establecida mediante escaneo de código QR externo.
    """
    SCAN_TIMEOUT_SECONDS = 30

    def __init__(self):
        self._state = ScanState.DISCONNECTED
        self.start_time: Optional[datetime] = None

    @property
    def state(self) -> ScanState:
        """Devuelve el estado actual del servicio."""
        return self._state

    async def start_scanning(self):
        """Inicia el ciclo de escaneo, estableciendo el temporizador de expiración."""
        if self.state != ScanState.DISCONNECTED:
            print("Advertencia: El servicio ya está en un estado activo. Debe resetearse primero.")
            return

        self._state = ScanState.PENDING_SCAN
        self.start_time = datetime.now()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Servicio QR iniciado. Tiempo límite: {self.SCAN_TIMEOUT_SECONDS} segundos.")

    async def _check_timeout_periodically(self):
        """Verifica una vez si la ventana de escaneo expiró."""
        if self.start_time is None:
            return

        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed > self.SCAN_TIMEOUT_SECONDS and self._state in {
            ScanState.PENDING_SCAN,
            ScanState.CONNECTING,
        }:
            print(
                f"\n[{datetime.now().strftime('%H:%M:%S')}] "
                f"!!! Timeout detectado después de {int(elapsed)} segundos."
            )
            self._state = ScanState.EXPIRED

    async def process_scan(self, qr_data: str) -> bool:
        """
        Procesa los datos recibidos del escáner QR externo.
        Retorna True si el procesamiento fue exitoso y cambió el estado a CONNECTED.
        """
        if self.state != ScanState.PENDING_SCAN and self.state != ScanState.DISCONNECTED:
            print(f"Error de proceso: No se puede escanear porque el servicio está en estado {self.state.value}.")
            return False

        await self._check_timeout_periodically()
        if self.state == ScanState.EXPIRED:
            return False

        # Validación 1: chequeo básico de datos.
        if not qr_data or len(qr_data) < 8:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR LECTURA QR: Los datos son ilegibles o inválidos.")
            self._state = ScanState.FAILED
            return False

        # Simulación de procesamiento y conexión (ejemplo: API call)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando conexión con datos QR: {qr_data[:10]}...")
        self._state = ScanState.CONNECTING
        await asyncio.sleep(0)  # Cede el control sin ralentizar los tests.

        # Validación 2: Lógica de negocio (simulación de éxito vs fracaso del contenido)
        if "VALID" in qr_data.upper():
            self._state = ScanState.CONNECTED
            print("=" * 50)
            print(f"✅ CONEXIÓN EXITOSA: La sesión ha sido establecida con el valor QR.")
            print(f"   Estado Actual: {self.state.value}")
            print("=" * 50)
            return True
        else:
            self._state = ScanState.FAILED
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR CONEXIÓN: El código QR no pudo establecer la conexión o es de un tipo no soportado.")
            return False

    def reset_service(self):
        """Reinicia el servicio a su estado inicial."""
        print("--- Servicio reiniciado ---")
        self._state = ScanState.DISCONNECTED
        self.start_time = None
if __name__ == "__main__":
    async def main():
        scanner = QRScannerService()

        # 1. Test de Flujo Exitoso
        print("\n--- PRUEBA DE FLUJO EXITOSO ---")
        await scanner.start_scanning()
        # Esperar un poco para asegurar el estado PENDING_SCAN
        await asyncio.sleep(0.5) 
        success = await scanner.process_scan("SECURE_QR_VALID_DATA_12345")
        print(f"Resultado de la prueba exitosa: {success}")

        scanner.reset_service()

        # 2. Test de Fallo por Timeout (Requiere simulación avanzada en tests, pero lo demostramos aquí)
        print("\n--- PRUEBA DE TIMEOUT (DEBE ESPERAR EL TIEMPO CONFIGURADO EN TESTS) ---")
        scanner = QRScannerService() # Reiniciar para el test 2
        await scanner.start_scanning()
        # En un entorno real, se dejaría pasar el tiempo y el _check_timeout_periodically lo cambiaría a EXPIRED.
        print("Simulando espera de timeout (Se recomienda usar tests/test_qr_scanner_service.py para verificar esto).")
        await asyncio.sleep(1) 

        # 3. Test de Fallo en la Lectura
        print("\n--- PRUEBA DE FALLO EN LECTURA ---")
        await scanner.reset_service()
        await scanner.start_scanning()
        await asyncio.sleep(0.5) # Asegurar estado PENDING
        await scanner.process_scan("datos cortos") # Datos inválidos o ilegibles

    # Ejecutar main para demostración (aunque el orquestador usará los tests)
    # asyncio.run(main())
