# Sesión actual

_Plantilla: el harness añade aquí bloques con marca de tiempo al ejecutar `python -m harness.cli run`._


## [2026-05-05T09:02:44] Líder · feature 1

### Plan de Sesión para Feature `document_harness_cli`

#### **Preparación (LÍDER)**  
1. Ejecutar verificación inicial:  
   ```bash
   powershell -File init.ps1  # Windows
   ./init.sh                  # Unix
   ```  
2. Leer `progress/current.md` y `feature_list.json` para confirmar estado actual.  
3. Actualizar `feature_list.json`:  
   - Mover `document_harness_cli` a `"in_progress": "document_harness_cli"`, limpiar otros estados.  

#### **Implementador (LÍDER)**  
1. Documentar en `README.md`:  
   - **Comandos clave**:  
     ```markdown
     - `validate`: Verifica consistencia de repositorio y LLM config.
     - `status`: Muestra estado de features.
     - `run`: Ejecuta pipeline de implementación/revisión.
     - `expand [texto]`: Genera nuevas features desde descripción natural.
     ```  
   - **Dependencias**:  
     - `.env.example` con `LLM_API_BASE_URL`, `LLM_MODEL`.  
     - Omitir referencias legacy (PyTorch, conectoma).  

#### **Revisor**  
1. Verificar `README.md`:  
   - Cumple con aceptación en `feature_list.json`?  
   - Incluir veredicto en `progress/review_document_harness_cli.md`:  
     ```markdown
     VERDICT: PASS  # O FAIL si hay errores
     [Justificación breve]
     ```  

#### **Finalización**  
- Marcar feature como `"done"` en `feature_list.json` tras veredicto positivo.  
- Documentar bloqueos (si los hubiera) en `progress/current.md`.  

> **Nota**: El LÍDER debe supervisar todo y generar informes en `progress/impl_document_harness_cli.md` y `progress/review_document_harness_cli.md`.


## [2026-05-05T09:14:54] Líder · feature 2

### Plan de Sesión para Feature `verdict_parser_tests` (id: 2)

1. **Revisión Inicial**  
   - Lee `feature_list.json`: Confirma que `id: 2` está en `in_progress` y no hay otras features activas.  
   - Consulta `progress/current.md`: Verifica la descripción de la feature y el estado actual.  

2. **Preparación del Implementador**  
   - Crea archivo `impl_verdict_parser_tests.md` en `progress/`.  
   - Define los pasos:  
     ```markdown
     # impl_verdict_parser_tests.md
     1. **Parseo de VERDICT**:  
        - Usa `test_harness_feature_store.py` para validar parseo de `VERDICT: PASS/FAIL`.  
     2. **Reglas de Estado**:  
        - Implementa en `tests/test_harness_feature_store.py`:  
          ```python
          def test_single_in_progress():
              assert len(feature_list["in_progress"]) == 1
          ```
     3. **Mock LLM**:  
        - Usa `mock.py` para simular respuestas de LM Studio (evita llamadas reales).  
     4. **Verificación Final**:  
        - Ejecuta `pytest tests/verdict_parser_tests/`.  
     ```

3. **Revisión por Implementador**  
   - El implementador ejecuta los tests y documenta resultados en `impl_verdict_parser_tests.md` (ej: "TESTS PASSED").  

4. **Revisión por Revisor**  
   - El revisor lee `impl_verdict_parser_tests.md`, ejecuta tests, y emite `VERDICT: PASS` o `FAIL` en la primera línea de `review_verdict_parser_tests.md`.  

5. **Cierre de Sesión**  
   - Si el revisor aprobó, marca feature como `done` en `feature_list.json`.  
   - Actualiza `progress/history.md` con enlace al archivo de implementación.  

### Archivos Críticos Referenciados
- `feature_list.json`: Estado global de features.  
- `impl_verdict_parser_tests.md`: Informe del implementador (en `progress/`).  
- `review_verdict_parser_tests.md`: Verificación por revisor (en `progress/`).  
- `tests/test_harness_feature_store.py`: Implementación de tests unitarios.  

### Reglas Cumplidas
- **Una feature activa**: Solo `id: 2` en `in_progress`.  
- **Separación implementador-revisor**: Documento de implementación y revisión separados.  
- **Estado en disco**: Todos los informes guardados en `progress/`.


## [2026-05-05T16:47:38] Líder · feature 3

Como Líder del harness, mi objetivo es proveer un plan de ataque conciso para el implementador. Debemos asegurar que la implementación siga los patrones definidos en `docs/architecture.md` y resulte en artefactos trazables (`progress/impl_*.md`).

**Feature activa:** 3: Generación de Código QR de WhatsApp
**Rol:** Implementador (Delegar al siguiente agente)

### Plan de Sesión para el Implementador

El objetivo es crear la capa de servicio que genera el código QR y exponerlo a la interfaz gráfica. El resultado esperado será un borrador en `progress/impl_whatsapp_qr_code_generation.md`.

**Fase 1: Preparación del Entorno y Lógica Central (Backend)**
*   **Dependencias:** Verificar si se requiere añadir `qrcode` al entorno. Actualizar la lista de dependencias necesaria (`requirements.txt`).
*   **Creación de Servicio:** Crear un nuevo módulo o servicio, preferentemente en `services/whatsapp_service.py`. Este será el *único lugar* que contenga la lógica de negocio para generar el QR.
    *   *Tarea:* Implementar la función principal `generate_qr(number: str, message: str) -> bytes | str` que utilice la librería 'qrcode' y devuelva el código en un formato utilizable por el frontend (ej. un archivo binario o una URL de datos).

**Fase 2: Integración con la UI (Frontend)**
*   **Modificación:** Localizar el componente/página donde se debe mostrar este resultado (`frontend/WhatsappDisplayComponent.tsx` o similar, según la estructura real del proyecto).
*   **Conexión:** Modificar el componente para que llame al nuevo servicio de backend en un punto de ejecución clave (ej. `onGenerateClick`).
*   **Renderizado:** Implementar el manejo del estado de carga y, una vez obtenido los datos del QR, renderizar el código de barras como elemento visual visible (`<img src={qr_data} />` o equivalente Canvas/SVG).

**Artefactos Clave a Modificar/Crear:**
1.  `requirements.txt` (Actualización de dependencias).
2.  `services/whatsapp_service.py` (Nueva capa de servicio, debe contener la lógica pura).
3.  Archivos en `frontend/` (Lógica de consumo y renderizado).

**Recordatorio:** Tras completar las modificaciones, el implementador debe escribir un resumen técnico detallado en `progress/impl_whatsapp_qr_code_generation.md` para que el revisor pueda auditar la implementación sin necesidad de leer los diffs brutos del código fuente directamente (el *por qué* se hizo y cómo interactúan las partes).


## [2026-05-05T16:52:34] Líder · feature 4

Como Agente LÍDER, mi función es establecer el mapa de ruta para que el Implementador pueda proceder con la tarea. Me centraré en desglosar las funcionalidades de conexión y persistencia requeridas por `whatsapp_web_connection` en pasos atómicos.

A continuación, presento el plan detallado para esta sesión. El objetivo es crear una capa de abstracción sólida (un *Client*) que gestione tanto la inicialización como la persistencia del estado.

---
### 🛠️ Plan de Sesión: whatsapp\_web\_connection

**Objetivo:** Implementar `WhatsAppClient` capaz de conectar, gestionar el escaneo QR y asegurar la persistencia de la sesión.

#### **1. Preparación y Dependencias (Delegado al entorno/setup)**

*   **Acción:** Verificar que las dependencias necesarias para la automatización de navegadores estén listadas.
*   **Archivos a revisar:** `requirements.txt` (Añadir Playwright o similar, si es necesario).
*   **Artefacto clave:** La configuración inicial del entorno debe apuntar a la lógica central en un nuevo módulo.

#### **2. Creación de la Capa de Abstracción (Implementador)**

Se requiere una clase principal que encapsule toda la lógica de interacción con WhatsApp Web, separándola de la capa de negocio superior.

*   **Ubicación:** Crear o actualizar `src/whatsapp_client.py`.
*   **Funcionalidades a implementar en `WhatsAppClient`:**
    1.  **`__init__(self, session_path: str)`:** Constructor que acepte una ruta para guardar el estado de la sesión. Debe inicializar la conexión al navegador (Playwright/Puppeteer).
    2.  **`connect(self) -> bool`:** Este método es crucial. Debería intentar cargar la sesión desde `session_path`. Si falla o si no existe, debe iniciar el modo "escaneo QR" y devolver un indicador de espera (`await self.qr_code`). Debe gestionar los timeouts apropiados para la conexión inicial.
    3.  **`save_session(self)`:** Método que serializa los datos de la sesión del navegador/servicio (e.g., cookies, perfil) en el disco especificado por `session_path`. Esto garantiza la persistencia después de cerrar y reabrir el proceso.
    4.  **`disconnect(self)`:** Limpia recursos y cierra la instancia del navegador de forma limpia.

#### **3. Integración con la Lógica de Negocio (Implementador)**

La función de conexión debe ser invocable desde el punto de entrada principal (`src/main.py`) y debe manejar el flujo completo:

1.  **Verificación:** El sistema debe primero intentar cargar una sesión existente.
2.  **Flujo de Conexión:** Si la sesión existe, llamar a `client.connect()`. Si falla (ej. expirado), pasar al modo escaneo QR hasta que se complete.
3.  **Manejo del Ciclo de Vida:** Al finalizar cualquier operación o al cerrar el sistema, **obligatoriamente** se debe ejecutar `client.save_session()` antes de llamar a `client.disconnect()`.

#### **4. Validación y Pruebas (Revisor/QA)**

*   **Prueba Unitarias:** Crear pruebas específicas en `tests/test_whatsapp_connection.py` para:
    1.  Inicialización exitosa con sesión preexistente.
    2.  Flujo completo de QR Code -> Escaneo exitoso -> Persistencia (verificar que el archivo generado permite una reconexión posterior).
*   **Artefacto a validar:** El comportamiento debe ser auditable en `progress/impl_whatsapp_web_connection.md` con trazas claras de logs de conexión y desconexión para verificar los estados (`CONNECTED`, `SCANNING`, `DISCONNECTED`).

---
### 📋 Resumen de Tareas (Para el Implementador)

1.  **Estructurar:** Definir `WhatsAppClient` en `src/whatsapp_client.py`.
2.  **Funcionalidad Central:** Implementar los métodos `connect()`, `save_session()` y `disconnect()`.
3.  **Orquestación:** Actualizar la capa de servicio principal para consumir esta clase, respetando el ciclo de vida (cargar -> usar -> guardar).

*(Este plan deja claro que la complejidad está en el manejo del estado del proceso (`session_path`) más que en la simple llamada a una librería.)*


## [2026-05-07T09:23:27] Líder · feature 5

Como Agente LÍDER, mi rol es establecer la hoja de ruta para el Implementador. Dado que estamos en el punto de partida para esta *feature*, este plan está diseñado para guiar los cambios iniciales y asegurar que la lógica sea modular y testeable.

A continuación, detallo un plan de sesión breve para el implementador. El enfoque será crear una capa de abstracción para la entrada del escáner y manejar el estado asíncrono resultante.

### 🛠️ Plan de Sesión: `qr_scan_handling` (Implementación)

El objetivo es escribir el código que escucha el evento externo sin bloquear el flujo principal, y gestiona los estados de conexión/error.

**Tareas para el Implementador:**

1.  **Crear Servicio de Escaneo (`src/scanner_service.py`):**
    *   Implementar una clase o módulo dedicado (ej: `QRScannerListener`) responsable únicamente de recibir la entrada del escáner QR y empaquetarla en un evento.
    *   Este servicio debe ser capaz de operar como *listener* asíncrono, desacoplado del componente principal que muestra el estado de conexión.

2.  **Actualizar Manejo de Estado (`src/state_manager.py`):**
    *   Modificar la lógica de gestión de estados para incluir un nuevo flujo: `WAITING_FOR_SCAN`.
    *   Cuando se entra en este estado, debe iniciar un temporizador de expiración (timeout) y registrar el último intento de escaneo exitoso/fallido.

3.  **Integrar la Notificación (`src/ui_logic.py`):**
    *   Añadir métodos dentro del componente de interfaz que consuman los eventos de éxito o fallo emitidos por `QRScannerListener`.
    *   El flujo debe manejar dos estados finales: A) Conexión exitosa (con el payload del QR) y B) Expiración del tiempo límite.

4.  **Pruebas Unitarias (`tests/test_scanner.py`):**
    *   Escribir mocks para simular la recepción de escaneos válidos, escaneos inválidos o la ausencia total de eventos (simulando timeout).

**Artefactos Esperados al finalizar:**

*   **Archivos Modificados/Creados:** `src/scanner_service.py`, `src/state_manager.py`, `src/ui_logic.py`, y nuevas pruebas en `tests/test_scanner.py`.
*   **Documentación de Progreso:** Actualizar `progress/impl_5.md` con la estructura del código base desarrollado para esta sesión, incluyendo un resumen de la lógica implementada.

---
***(Nota para el equipo: Recuerden que después de esta fase, se requerirá la revisión por parte de un Revisor experto en arquitectura asíncrona antes de marcar el avance.)***


## [2026-05-07T09:47:09] Líder · feature 5

Este ciclo es de tipo **CREACIÓN/HERRAMIENTA**. Estamos desarrollando una funcionalidad clave que debe residir en el *skillset* del sistema y, por lo tanto, su progreso debe rastrearse meticulosamente.

---
### 🛠️ Plan de Sesión para Implementador: `qr_scan_handling` (ID 5)

**Objetivo:** Desarrollar la lógica de recepción, procesamiento y manejo de estado tras un escaneo QR externo exitoso o fallido.

#### Fase I: Análisis e Interfase (Delegado al Implementador)
1. **Lectura Obligatoria:** Revisar `docs/architecture.md` para comprender los puntos de entrada de datos externos.
2. **Definición de Contrato:** Identificar la clase o módulo responsable de recibir el *stream* de escaneo QR (e.g., un endpoint HTTP, un manejador de eventos). Se debe esbozar la firma del método que recibirá los datos brutos (`scan_data: str`) y el estado asociado (`status: ScanStatus`).
3. **Archivado Intermedio:** Los esquemas de datos definidos para este manejo deben guardarse en `progress/qr_scanner_interface.py`.

#### Fase II: Desarrollo Core (Implementación)
1. **Manejo del Evento de Escaneo:** Implementar la función principal que actúa como *listener*. Esta función debe ser robusta ante inputs nulos o malformados. Se trabajará en un nuevo módulo bajo `src/input_handlers/`.
2. **Validación y Parsing (AC1):** Desarrollar la lógica para parsear el contenido del QR. Si la lectura es exitosa, se debe empaquetar el dato extraído junto con un *timestamp* de captura. Esto va en `progress/qr_processor_v0.py`.
3. **Manejo de Estado y Feedback (AC2):**
    *   Implementar una capa de estado que maneje la conexión activa vs. expirada. Si el proceso no detecta un escaneo dentro de $T$ segundos, debe emitir un evento de expiración o fallo (`ConnectionTimeoutError`).
    *   La notificación al usuario (éxito/fallo) debe ser manejada por una función *side-effect* que se llama tras procesar los datos.

#### Fase III: Pruebas y Validación (Obligatorio)
**Antes de considerar este ciclo cerrado, deben ejecutarse las siguientes pruebas:**

1. **Pruebas Unitarias (`pytest`):**
    *   Crear `test_qr_handling.py` en `progress/`.
    *   Test 1: Escaneo exitoso con dato válido (Debe retornar un objeto de datos parseados).
    *   Test 2: Entrada nula o malformada (Debe manejar la excepción sin colapsar el proceso).
    *   Test 3: Simulación de expiración de tiempo (Debe disparar la lógica de fallo/timeout).
2. **Pruebas de Integración:**
    *   Simular el flujo completo desde que se recibe un *payload* externo hasta que se actualiza el estado central del sistema. Esto verifica que el módulo `qr_processor` interactúa correctamente con los módulos de notificación y estado existentes.

#### Artefactos Esperados al Finalizar la Feature:
*   El código fuente funcional debe vivir en una nueva carpeta de *skills*: **`skills/qr_scan_handler.py`**.
*   Todos los esquemas, clases intermedias y versiones iniciales deben estar guardadas en `progress/`.
*   Un informe detallado del implementador (`progress/impl_qr_scan_handling.md`) debe narrar las decisiones arquitectónicas tomadas.

**Recordatorio:** La implementación es secuencial: no se puede avanzar a la Fase II sin haber definido el contrato en la Fase I, y no se cierra hasta que todas las pruebas de la Fase III pasan localmente.


## [2026-05-07T12:33:01] Líder · feature 5

Este ciclo es de tipo **CREACIÓN/HERRAMIENTA**.

Mi plan se centrará en guiar al Implementador para que desarrolle la funcionalidad requerida, manteniendo el estado en disco y definiendo los artefactos de prueba desde el inicio.

---

### 🛠️ Plan de Sesión: `qr_scan_handling` (ID 5)

**Objetivo Principal:** Integrar la lógica de detección del escaneo QR externo para establecer una conexión segura con un dispositivo, manejando tanto el éxito como los estados de fallo/expiración.
**Rol Delegado:** Implementador (Write Code).
**Artefacto Esperado Final:** Un módulo o clase en `skills/` que encapsule la lógica completa del escaneo y notificación de estado.

#### 📋 Fases de Desarrollo

1.  **Análisis Preliminar y Diseño de Interfaces (Implementador):**
    *   Revisar `progress/current.md` para entender el flujo actual de conexión antes de un QR scan.
    *   Leer `docs/architecture.md` para asegurar que la nueva lógica respeta los límites arquitectónicos del sistema.
    *   Definir una interfaz (stub) para recibir datos del escáner y un *handler* de estados de conexión (`connected`, `failed`, `expired`). Este diseño se documentará en `progress/design_qr_scan.md`.

2.  **Implementación Core - Detección de Escaneo (Implementador):**
    *   Escribir el código que escucha o procesa los *inputs* del escáner QR. Debe ser robusto al ruido y a la detección inicial versus la validación final.
    *   El proceso debe escribir su lógica paso a paso en `progress/impl_qr_scan_handling.md`.

3.  **Implementación Core - Manejo de Conexión (Implementador):**
    *   Integrar el *handler* que toma los datos del QR y realiza la conexión real (simulación o código de red).
    *   Asegurar que la lógica maneje explícitamente:
        a) Escaneo exitoso $\rightarrow$ Conexión establecida.
        b) Tiempo transcurrido sin acción $\rightarrow$ Notificación de Expiración (`expired`).
        c) QR inválido/fallo en la red $\rightarrow$ Notificación de Fallo (`failed`).

4.  **Refinamiento y Consolidación (Implementador):**
    *   Una vez que el código funciona, debe ser refactorizado para estar aislado como una herramienta reutilizable. Este módulo final residirá en `skills/qr_scanner_service.py` (ejemplo).

#### 🧪 Pruebas de Validación Requeridas

El ciclo no puede terminar sin pasar estas pruebas de validación automatizadas:

1.  **Test de Flujo Exitoso:** Se debe simular un escaneo QR válido que resulta en una conexión exitosa. **Resultado esperado:** La aplicación emite la notificación "Conexión Exitosa" y el estado pasa a `connected`.
2.  **Test de Fallo por Timeout:** Se debe iniciar el flujo de escanear pero dejar pasar un período de tiempo predefinido (ej., 30 segundos) sin que se escanee nada. **Resultado esperado:** La aplicación emite la notificación "Conexión Expirada/Timeout" y el estado pasa a `expired`.
3.  **Test de Fallo en la Lectura:** Se simula un escanear con datos QR inválidos o ilegibles por el dispositivo externo. **Resultado esperado:** La aplicación emite la notificación "Error al procesar escaneo, revisar código QR" y permanece en estado `pending_scan`.

#### 💾 Rutas de Archivo Clave (Para Referencia)

*   **Inicio/Planificación:** `progress/current.md`
*   **Progreso Intermedio:** `progress/impl_qr_scan_handling.md`
*   **Diseño/Stub:** `progress/design_qr_scan.md`
*   **Artefacto Final:** `skills/qr_scanner_service.py` (o similar)


## [2026-05-07T12:44:30] Líder · feature 5

El ciclo de trabajo es de tipo **CREACIÓN/HERRAMIENTA**.

Como Líder del Harness, mi función aquí es delegar la construcción de la funcionalidad completa y asegurar que el proceso de desarrollo sea riguroso, paso a paso, desde la implementación hasta la validación automatizada.

---

### 🛠️ Plan de Sesión para Implementador: `qr_scan_handling` (Feature ID 5)

**Objetivo:** Integrar la lógica robusta para detectar y manejar el flujo de conexión que se origina por el escaneo exitoso o fallido de un Código QR mediante dispositivo externo.

**Flujo General:** El implementador debe trabajar en bloques funcionales, guardando cada artefacto de avance en `progress/`.

#### Fase 1: Preparación e Integración del Listener (Implementador)

1.  **Modificar la Entrada de Datos:** Localizar el módulo encargado de recibir las entradas externas o eventos de escaneo (ejemplo: un *listener* HTTP o WebSocket).
2.  **Captura Inicial:** Implementar una capa que capture el evento de escaneo QR, sin asumir aún su validez ni su impacto en el estado del sistema principal. Solo se debe registrar la recepción del dato (`progress/impl_scan_capture.md`).
3.  **Manejo Asíncrono:** El proceso de detección y manejo del QR debe correr de forma asíncrona para no bloquear la interfaz de usuario mientras espera una respuesta o valida el código.

#### Fase 2: Lógica Central de Negocio (Implementador)

1.  **Validación del Dato:** Crear un *handler* que tome el valor escaneado y lo valide contra esquemas esperados. Debe determinar si es un QR válido para la conexión requerida.
2.  **Flujo Exitoso:** Si el dato es válido, se debe emitir una señal de "Conexión Intentada". Esta señal dispara la secuencia de *handshake* con el servicio externo y establece un temporizador de éxito/fallo (`progress/impl_success_logic.md`).
3.  **Manejo de Fallos/Tiempos:** Implementar la lógica de expiración (Timeout) o fallido en el proceso de conexión (ej: credenciales inválidas). Debe haber un mecanismo claro para notificar al usuario que la acción ha fallado y por qué, sin colapsar la aplicación.

#### Fase 3: Notificación y Experiencia de Usuario (Implementador)

1.  **Feedback Visual:** Actualizar los componentes UI/UX para reflejar el estado en tiempo real (`Conectando...`, `Conexión Exitosa`, `Fallo: Vuelva a Intentarlo`).
2.  **Logs Detallados:** Asegurar que todos los estados transitorios (recepción de dato, inicio de *handshake*, timeout) queden registrados de manera legible y auditable en logs específicos del módulo.

---

### 🧪 Pruebas de Validación Requeridas (Automated Validation)

El ciclo NO puede terminar hasta que el Implementador haya proporcionado código que pase las siguientes pruebas unitarias/de integración, documentando los resultados en `progress/test_results_<name>.md`.

1.  **Test A: Escaneo Exitoso (`qr_success`)**:
    *   **Acción:** Simular la recepción de un QR perfectamente formado y válido para el sistema.
    *   **Resultado Esperado:** El sistema debe procesar el escaneo, iniciar la conexión en menos de $X$ ms y notificar al usuario con éxito (aceptación: "Conexión Exitosa").

2.  **Test B: Escaneo Inválido o Malformado (`qr_invalid`)**:
    *   **Acción:** Simular el escaneo de un QR que contiene datos fuera del formato esperado, o datos nulos/vacíos.
    *   **Resultado Esperado:** El sistema debe rechazar inmediatamente la conexión y mostrar un mensaje claro de "Código QR no reconocido". (No debe intentar conectar).

3.  **Test C: Timeout Control (`qr_timeout`)**:
    *   **Acción:** Simular un escaneo válido, pero bloquear o retrasar intencionalmente el proceso de *handshake* externo por más tiempo que el límite definido ($Y$ segundos).
    *   **Resultado Esperado:** El sistema debe detectar la expiración automáticamente y notificar al usuario con claridad: "Tiempo de conexión agotado. Verifique la red."

Al completar las tres fases e incorporar estos artefactos, se procederá a pasar el control conceptualmente al Revisor para su revisión formal en `progress/review_qr_scan_handling.md`.


## [2026-05-07T12:50:20] Líder · feature 5

Como Agente Líder, he revisado la configuración del repositorio y el estado de la *feature* activa.

**Tipo de Ciclo Identificado:** CREACIÓN/HERRAMIENTA
**Feature Activa:** `qr_scan_handling` (ID 5)

---

### Plan de Sesión para el Implementador: Manejo de Escaneo QR

El objetivo es desarrollar la lógica robusta para capturar y manejar eventos de escaneo QR externos, asegurando una notificación clara al usuario sobre el estado de conexión.

**Delegación:** Este plan está dirigido al **Implementador**. Por favor, documenta tu progreso en `progress/impl_qr_scan_handling.md` a medida que trabajas.

#### ⚙️ Fase I: Implementación del Detector (Event Hook)
*   **Intención:** Identificar el punto de entrada donde se puede "escuchar" la señal de un dispositivo externo (smartphone escaneando). Esto podría requerir un *hook* o una API stub temporal si no existe aún.
*   **Tarea:** Modificar los archivos centrales de conexión (asumamos `src/connection_manager.py` y `src/event_listeners.py`) para añadir un método `register_qr_scan_handler(callback)` que reciba el payload del escaneo.
*   **Output esperado:** Un mecanismo que detecte la finalización del escaneo QR de manera asíncrona (ej. vía WebSocket o polling controlado).

#### 🧪 Fase II: Lógica de Procesamiento y Estado
*   **Intención:** Implementar la lógica dentro del *handler* para procesar el payload recibido, validar si es un dato esperado, e interactuar con el estado global de la conexión.
*   **Tarea A (Éxito):** Cuando se recibe un QR válido:
    1.  Desencadenar la función `handle_successful_scan(data)` en la clase principal del componente de UI/Lógica.
    2.  Actualizar el estado interno a `CONNECTED` y emitir una notificación visual de éxito (`progress/impl_qr_scan_handling.md` debe documentar este flujo).
*   **Tarea B (Fallo/Expiración):** Asegurar que si la conexión no se establece tras un tiempo prudencial, el sistema marque automáticamente el escaneo como fallido o caducado, notificando al usuario de manera clara.

#### 🛠️ Fase III: Refactorización y Creación de Herramienta
*   **Intención:** Consolidar el nuevo flujo de manejo QR en una clase/módulo reutilizable.
*   **Tarea:** Crear la herramienta `qr_scan_processor.py` dentro de la carpeta `skills/`. Esta herramienta encapsulará toda la lógica del *handler*, permitiendo que otras partes del sistema la consuman sin modificar el *core*.

#### 🎯 Fase IV: Pruebas Automatizadas y Validación (Obligatorio)
Antes de considerar esta feature como completada, debes ejecutar las siguientes validaciones unitarias/de integración. **El ciclo no terminará si estas pruebas fallan.**

1.  **Test de Éxito:** Crear un test que simule la recepción exitosa de datos QR válidos y verifique que el estado del sistema pase a `CONNECTED` y se emita la notificación correcta. (Añadir prueba a `tests/test_qr_handling.py`).
2.  **Test de Timeout/Fallo:** Crear un test que simule una conexión sin respuesta o con datos inválidos, verificando que el estado final sea `DISCONNECTED` y el mensaje de error sea apropiado. (Añadir prueba a `tests/test_qr_handling.py`).
3.  **Test de Integración de Hook:** Verificar que la herramienta en `skills/qr_scan_processor.py` pueda ser inicializada y llamada desde un módulo dummy sin romper las dependencias existentes.

---
*Por favor, comienza por implementar el detector (`progress/impl_qr_scan_handling.md`) y luego avísame cuando estés listo para que yo, como Líder, revise la implementación antes de pasar a las pruebas.*
