... (Se mantiene el contenido existente hasta aquí) ...

## Componentes Python

| Módulo | Rol |
|--------|-----|
| `llm_api_client.py` | Cliente HTTP OpenAI-compatible → LM Studio |
| `harness/orchestrator.py` | Secuencia líder → implementador → revisor; actualiza `feature_list.json` |
| `harness/feature_store.py` | Carga/guardado atómico del JSON de features |
| `harness/cli.py` | Comandos `init`, `validate`, `status`, `run`, `expand` |
| `harness/tool_learning.py` | Recomendador aprendido de skills existentes; evita recrear herramientas ya disponibles |
| **`api_endpoints/whatsapp_hook.py`** | **Maneja la recepción, verificación y parsing de payloads Webhook externos (ej. WhatsApp).** |

## Flujo de un ciclo `run`

1. Reclamar la siguiente `pending` (o continuar `in_progress`).
2. **Líder**: plan breve → `progress/current.md`.
3. **Implementador**: informe detallado → `progress/impl_<name>.md`.
4. **Harness**: aplica cambios con guardrails, ejecuta validaciones y guarda evidencia.
5. **Revisor**: checklist + `VERDICT` → `progress/review_<name>.md`.
6. Si `PASS` → `done`; si `FAIL` o ambiguo → `pending` para corrección.

## Guardrails de ejecución agentica

El orquestador incluye siete mejoras para acercar el flujo a un agente de código
más potente y seguro:

1. **Contexto automático del repositorio:** antes de llamar al implementador,
   selecciona archivos probables por nombre/contenido inicial de la feature.
2. **Cambios incrementales:** acepta bloques `patch`/`diff` y los valida con
   `git apply --check` antes de aplicarlos.
3. **Rutas seguras:** los bloques de archivo completo se resuelven contra la raíz
   del repo; se rechazan rutas absolutas, `..` y archivos `.env`.
4. **Reporte de rechazos:** todo bloque rechazado queda en la evidencia de debug
   y puede forzar `FAIL` aunque el revisor apruebe.
5. **Política de comandos:** comandos destructivos como `git reset --hard`,
   `git clean -f`, `rm -rf` o `curl | sh` se bloquean antes de ejecutarse.
6. **Validación enfocada:** si se tocan tests o módulos con tests asociados,
   el harness ejecuta esos tests antes de la suite completa.
7. **Evidencia para revisión:** el revisor recibe archivos modificados, patches
   aplicados, bloques rechazados, comandos bash, validación de imports y pytest.

## Flujo de Datos Externos: Recepción de Mensajes (WhatsApp Webhook)

Cuando el sistema necesita interactuar con plataformas externas que envían eventos (ej. WhatsApp, Telegram), se debe utilizar un *endpoint* dedicado (`api_endpoints/whatsapp_hook.py`).

1. **Handshake (GET):** La plataforma Meta inicia la conexión enviando una solicitud GET. El `harness` intercepta esta llamada y devuelve el valor del `hub.challenge` para completar la verificación, manteniendo abierto el canal de Webhooks.
2. **Evento (POST):** Cuando ocurre un evento real (ej. mensaje), Meta envía un payload JSON complejo vía POST al *endpoint*.
3. **Parsing:** El módulo `whatsapp_hook.py` se encarga de desestructurar el JSON anidado, identificando el tipo de contenido (`text`, `image`, etc.) y normalizándolo en un objeto Python/JSON estandarizado que contenga: `sender_id`, `content`, y `media_detected`.
4. **Consumo Central:** Este evento estructurado es emitido internamente para ser procesado por los servicios centrales del *harness*, desacoplando el mecanismo de recepción de la lógica de negocio.

## Aprendizaje de herramientas

El harness mantiene memoria local de cómo se usan las skills en `skill_usage`.
`harness/tool_learning.py` reconstruye un selector ligero en Python puro desde
`progress/harness_state.db`: una neurona por skill con pesos por token, más
coincidencia lexical contra las instrucciones Markdown. No se versionan datasets
de entrenamiento; el runtime conserva solo la memoria aprendida necesaria. Antes de
crear herramientas nuevas, el líder recibe recomendaciones de skills ya existentes con:

1. qué herramienta parece aplicar;
2. por qué fue recomendada;
3. cómo se ha usado antes;
4. casos previos exitosos o fallidos.

Este aprendizaje se actualiza mediante `record_skill_usage` o el comando:

```bash
python -m harness.cli tools --learn --skill arithmetic_calculator --use-case "calculadora" --instructions "handle_input_command('calc 10 + 5')"
```

... (Resto del documento) ...
