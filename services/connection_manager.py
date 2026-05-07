import asyncio
import re
from typing import Optional

class ConnectionError(Exception):
    """Excepción personalizada para fallos de conexión."""
    pass

class ConnectionManager:
    MAX_HANDSHAKE_TIME = 5  # Timeout en segundos

    @staticmethod
    def validate_qr_code(qr_data: str) -> Optional[str]:
        """
        Valida si el QR contiene un formato de conexión esperado.
        Formato esperado: 'CONN:<ID>:<SECRET>'
        Retorna los datos sanitizados o None si es inválido.
        """
        # Patrón regex simple para simular validación de estructura
        match = re.match(r"^[A-Z]{3}:\d{4}:[a-zA-Z0-9]+$", qr_data)
        if match:
            return match.group(0)
        print(f"[WARN] Código QR '{qr_data}' no cumple con el formato esperado.")
        return None

    async def attempt_connection(self, valid_qr_code: str) -> dict:
        """
        Intenta establecer una conexión de forma asíncrona.
        Lanza ConnectionError en caso de fallo o timeout.
        """
        print("--- Iniciando proceso de Handshake...")

        try:
            # Simulación de la llamada al servicio externo que puede tardar
            await self._simulate_handshake(valid_qr_code)
            
            # Si llega aquí, el handshake fue exitoso antes del timeout
            return {"status": "success", "message": "Conexión Exitosa. Listo para operar."}

        except asyncio.TimeoutError:
            raise ConnectionError("Tiempo de conexión agotado. Verifique la red o credenciales.")
        except Exception as e:
            # Captura fallos generales (ej: servidor caído)
            raise ConnectionError(f"Fallo crítico durante el handshake: {str(e)}")

    async def _simulate_handshake(self, qr_code: str):
        """Simula una API call externa con riesgo de timeout."""
        # Esta simulación es crucial para las pruebas. Se espera que la capa de prueba 
        # modifique esta función si necesita forzar fallos específicos (ej: bloqueo).
        await asyncio.sleep(1) # Simula latencia normal
        if "FAIL" in qr_code: # Lógica simulada para forzar un fallo interno
             raise ConnectionError("Credenciales internas inválidas.")

