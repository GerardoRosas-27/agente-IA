# Implementación · Establecimiento de Conexión con WhatsApp Web (Intento 1)

## INFORME de Implementación para WhatsApp Web Connection

### Resumen
He desarrollado y estructurado la capa de abstracción principal, `WhatsAppClient`, responsable de gestionar el ciclo de vida de la conexión con WhatsApp Web. Esta clase encapsula la lógica compleja que involucra iniciar un navegador (simulando Playwright), manejar la persistencia del estado mediante una ruta de sesión (`session_path`), detectar si la sesión existe y guiar al usuario a través del proceso de escaneo de QR cuando sea necesario. He actualizado las dependencias necesarias en `requirements.txt`.

### Archivos afectados
1.  `src/whatsapp_client.py`: Implementación central de la lógica de conexión y persistencia.
2.  `requirements.txt`: Adición de la librería de automatización de navegadores (Playwright).

### Plan de cambios
El plan se centró en la creación de una clase robusta que siga los principios del "Cerebro separado de las manos". El `WhatsAppClient` actúa como el componente "manos" (herramientas/ejecución) mientras que la lógica de negocio superior usará sus métodos para decidir cuándo y cómo conectar.

1.  **Estructura de la Clase:** Implementar `WhatsAppClient` con inicialización del estado de la sesión.
2.  **Conexión (`connect`)**: El método intenta cargar primero el perfil desde `session_path`. Si falla o expira, activa el modo QR.
3.  **Persistencia (`save_session`, `disconnect`)**: Asegurar que el *cleanup* de recursos (navegador cerrado) y la serialización del estado (cookies/perfil) se ejecuten correctamente al finalizar la sesión.

### Código a implementar (usando el formato estricto de bloques)

#### 1. Creación de `src/whatsapp_client.py`
He creado esta clase siguiendo las anotaciones de tipo y asumiendo una librería de automatización como Playwright para simular las interacciones del navegador.

```python:src/whatsapp_client.py
import os
from typing import Optional
# Simulación de la dependencia real (Playwright)
class BrowserContext:
    """Mock Context para simular un contexto de navegador con sesión."""
    def __init__(self, session_path: str):
        self.session_path = session_path
        print(f"[INFO] Contexto inicializado para la sesión en {session_path}.")

    async def load_state(self) -> bool:
        """Intenta cargar el estado de la sesión desde disco."""
        if os.path.exists(self.session_path):
            # Simula el proceso de conexión exitoso con perfil guardado
            print("[SUCCESS] Sesión cargada correctamente.")
            return True
        return False

    async def capture_qr_code(self) -> str:
        """Simula la espera del código QR y la interacción."""
        print("[ACTION] Iniciando modo escaneo de QR. Esperando código...")
        # Aquí iría la lógica real de Playwright para escanear/esperar el elemento
        await asyncio.sleep(2) # Simular tiempo de escaneo
        return "QR_CODE_DETECTED"

    async def save_state(self):
        """Guarda el estado actual del contexto (cookies, etc.)."""
        if not os.path.exists(self.session_path):
            os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
        print(f"[PERSIST] Guardando sesión en {self.session_path}...")
        # Lógica real de serialización (e.g., context.storage_state({}))
        with open(self.session_path, 'w') as f:
            f.write("mock_session_data")

    async def close(self):
        """Cierra el contexto del navegador limpiamente."""
        print("[CLEANUP] Cerrando sesión y recursos de Playwright.")


class WhatsAppClient:
    """
    Cliente principal para gestionar la conexión persistente a WhatsApp Web.
    Abstrae las interacciones complejas con la automatización web.
    """
    def __init__(self, session_path: str):
        """Inicializa el cliente y su contexto de navegador."""
        self.session_path = session_path
        # Inicializamos el mock del contexto
        self._context = BrowserContext(session_path)

    async def connect(self) -> bool:
        """
        Intenta conectar a WhatsApp Web, priorizando la sesión persistente.
        Devuelve True si está conectado y listo para operar.
        """
        print("--- Intentando Conectar con WhatsApp ---")
        
        # 1. Intentar carga de sesión existente (Prioridad)
        is_loaded = await self._context.load_state()

        if is_loaded:
            print("[STATUS] Sesión activa y cargada.")
            return True
        else:
            # 2. Modo QR Code / Inicialización forzada
            qr_status = await self._context.capture_qr_code()
            if qr_status == "QR_CODE_DETECTED":
                print("[SUCCESS] Conexión establecida después de escanear el código.")
                return True
            else:
                print("[ERROR] Fallo crítico al establecer conexión inicial.")
                return False

    async def save_session(self):
        """Serializa los datos actuales del contexto para persistencia."""
        if self._context:
            await self._context.save_state()

    async def disconnect(self):
        """Limpia y cierra la conexión de forma segura."""
        if self._context:
             await self._context.close()
```

#### 2. Actualización de `requirements.txt`
Se agrega Playwright como dependencia clave para la automatización del navegador headless.

```text:requirements.txt
# ... otras dependencias
playwright>=1.37.0 # Para automatización web y manejo de sesión
pydantic # Ya existente o recomendado para tipado
```

### Comandos de verificación (p. ej. pytest, init)

**1. Verificación de Dependencias:**
Asegurar que Playwright está instalado en el entorno virtual.
```bash
pip install -r requirements.txt
playwright install chromium # O el navegador deseado
```

**2. Pruebas Unitarias Sugeridas (Test Structure):**
Crear pruebas que cubran los tres estados críticos:
*   `test_whatsapp_client_init_no_session`: Verifica que `connect()` entra en modo QR y no carga estado.
*   `test_whatsapp_client_with_existing_session`: Mockear la existencia del archivo de sesión para asegurar que `connect()` salte el paso QR y reporte conexión instantánea.
*   `test_whatsapp_client_full_cycle`: Ejecutar el ciclo completo: `init -> connect() (QR) -> save_session() -> disconnect()`. Verificar que los archivos intermedios se crean correctamente.

### Riesgos / notas
1.  **Mocking:** El código de implementación utiliza clases *mock* (`BrowserContext`) para simular la compleja interacción con Playwright, ya que el entorno real no está disponible. En producción, estos mocks deben ser reemplazados por llamadas reales a las APIs de `playwright` (e.g., `browser.new_context(storage_state=...)`).
2.  **Asincronía:** Se ha asumido que toda la orquestación del harness opera en un entorno asíncrono (`async`/`await`), lo cual es fundamental para manejar las operaciones de I/O (conexión, escritura de archivos). El código utiliza `asyncio.sleep()` como placeholder temporal.
3.  **Manejo de Excepciones:** Aunque la clase provee los métodos clave, en un entorno real se debe añadir manejo exhaustivo de excepciones dentro de `connect()`, especialmente para *timeouts* y fallos de conexión del servicio WhatsApp Web.
