... (Se mantiene el contenido existente hasta aquí) ...

## Componentes Python

| Módulo | Rol |
|--------|-----|
| `llm_api_client.py` | Cliente HTTP OpenAI-compatible → LM Studio |
| `harness/orchestrator.py` | Secuencia líder → implementador → revisor; actualiza `feature_list.json` |
| `harness/feature_store.py` | Carga/guardado atómico del JSON de features |
| `harness/cli.py` | Comandos `init`, `validate`, `status`, `run`, `expand` |
| `harness/tool_learning.py` | Recomendador aprendido de skills existentes; evita recrear herramientas ya disponibles |
| `harness/repo_index.py` | Índice ligero por símbolos, imports y tokens para contexto de código |
| `harness/agent_loop.py` | Loop observar → actuar → observar con herramientas controladas, sesiones y checkpoints |
| `harness/evaluator.py` | Checks objetivos para compilar, testear y bloquear comandos riesgosos |
| `harness/benchmarks.py` | Benchmarks locales para medir capacidades del harness |
| `harness/coding_pipeline.py` | Pipeline localizar → reparar → validar con trayectorias |
| `harness/best_of_n.py` | Evaluación de patches candidatos en worktrees/copies aisladas |
| `harness/trajectories.py` | Registro persistente de episodios completos de agente |
| `harness/reflection.py` | Reflexiones accionables después de fallos |
| `harness/skill_promotion.py` | Promoción de soluciones exitosas a skills reutilizables |
| `harness/benchmark_tasks.py` | Benchmarks propios estilo SWE-bench en JSON |
| `harness/multiagent_contracts.py` | SOPs y validación de artefactos por rol |
| `harness/test_generator.py` | Generador de Bug Reproduction Tests desde una feature (Otter / BRT Agent) |
| `harness/verifier.py` | Verificador determinista basado en evidencia ejecutable (pytest output, py_compile, BRTs, ruff); hard-fails sobre secretos / imports / tests |
| `harness/static_analysis.py` | Lint con `ruff` sobre archivos modificados (B1) |
| `harness/embeddings.py` | Cliente para `/v1/embeddings` con caché SQLite (F1); fallback automático a TF-IDF |
| `harness/events.py` | Logging estructurado JSONL en `progress/events.jsonl` (D1) |
| `harness/mutation_test.py` | Mutation testing por AST para detectar tests fantasma (E2) |
| **`api_endpoints/whatsapp_hook.py`** | **Maneja la recepción, verificación y parsing de payloads Webhook externos (ej. WhatsApp).** |

## Flags opt-in del orquestador

`run_one_feature_cycle` acepta tres banderas para activar prácticas inspiradas
en literatura reciente sin romper compatibilidad con flujos existentes:

| Flag | Descripción | Paper de referencia |
|------|-------------|---------------------|
| `enable_brt` | Antes del implementador, pide al LLM 1-3 tests pytest fail-to-pass desde la feature; los BRTs se ejecutan al final como evidencia | Otter (arXiv:2502.05368), BRT Agent (arXiv:2502.01821) |
| `enable_verifier` | Tras el reviewer LLM, ejecuta `harness.verifier.verify_cycle` sobre evidencia ejecutable; sobreescribe a FAIL si encuentra contradicciones | MAR (arXiv:2512.20845), Generator/Critic/Verifier |
| `enable_replan` | Si el reviewer da FAIL, antes de reintentar pide al Líder un **plan nuevo** basado en el feedback (no reusa el plan original) | AdaCoder (arXiv:2504.04220), CodePlan (Microsoft) |
| `enable_adversarial_review` | Tras el reviewer principal lanza un segundo reviewer "red team"; si los dos no concuerdan, el ciclo se marca FAIL | MAR (arXiv:2512.20845) |

Best-of-N también acepta `brt_paths`: cuando se proveen, el ranking de
candidatos se hace por **Ensemble Pass Rate** (EPR) sobre los BRTs, no solo
por tests preexistentes.

## Búsqueda estructural por símbolos (AutoCodeRover-style)

`harness.repo_index` expone APIs para localización precisa que se usan tanto
en el `coding_pipeline` como en el `agent_loop` (acciones JSON
`search_class`, `search_method`, `search_callers`):

```python
from harness.repo_index import search_class, search_method, search_method_in_class, search_callers

search_class("HttpClient")                    # → SymbolMatch con path/líneas
search_method("fetch", root=...)              # → todas las funciones/métodos `fetch`
search_method_in_class("fetch", "HttpClient") # → solo métodos en esa clase
search_callers("HttpClient")                  # → archivos que importan/referencian
```

## Memoria con TF-IDF (resistente a distractores) y embeddings opcionales

`harness.shared_memory.semantic_recall_v2` recupera memorias en dos modos:

1. **Embeddings reales** vía `/v1/embeddings` (LM Studio, vLLM…), si la env
   var `LLM_EMBEDDING_MODEL` apunta a un modelo embedding cargado. Los
   vectores se cachean en SQLite por (texto, modelo).
2. **TF-IDF + cosine** en Python puro como fallback. Cero dependencias
   externas, funciona offline.

En benchmarks con muchos distractores ambos modos recuperan el item
relevante en el top-3 (la versión LIKE/regex previa lo perdía).

## Robustez del cliente LLM

`llm_api_client._post_chat_completions` envuelve cada llamada con:

- **3 reintentos** con backoff exponencial (1s, 3s, 9s) sobre `ConnectionError`,
  `Timeout` y status retryables (`408/425/429/500/502/503/504`).
- **Circuit breaker** por backend: si se acumulan 5 fallos seguidos, se abre
  durante 30 segundos (las llamadas siguientes devuelven `None` inmediatamente
  sin tocar el servidor) y se cierra al recibir el primer éxito.

`reset_llm_circuit()` permite reiniciar el estado en tests y al cambiar de
perfil.

## Caché en memoria del repo index (A4)

`build_repo_index` cachea las entries por proceso con TTL=5s
(`use_memory_cache=True` por defecto). Esto evita 3-4 escaneos completos por
ciclo del orquestador (`localize_issue` + `repo_context_for_goal` +
`evaluate_changes`). Para invalidar manualmente:

```python
from harness.repo_index import clear_repo_index_memory_cache
clear_repo_index_memory_cache()
```

## Observabilidad: eventos JSONL (D1)

`harness.events.emit_event(event, **fields)` appendea una línea JSON a
`progress/events.jsonl`. Eventos emitidos hoy:

- `cycle.started`, `cycle.finished` (outcome=pass/fail/ambiguous)
- `leader.planned`
- `verifier.verdict`, `verifier.override`
- `adversarial_reviewer.verdict`

`read_events()` y `event_counts_by_outcome()` permiten construir dashboards o
medir tendencias (¿la tasa de PASS está cayendo? ¿el verifier hace override
con frecuencia? = el reviewer LLM está mal calibrado).

## Mutation testing (E2)

`harness.mutation_test.run_mutation_test(file, test_paths, root)` aplica
mutaciones AST simples (boolean flips, operator flips, return → None) a un
archivo y mide el `mutation_score = killed / total`. Si los tests son
fantasma (sin asserts reales), las mutaciones sobreviven y el reporte lo
señala. Útil como check adicional en auto-mejora (feature 19) y para validar
que un BRT realmente discrimina.

## Dependencias entre features (H1)

`feature_list.json` ahora soporta `depends_on: [id1, id2, ...]`. La función
`pick_next_pending` ignora features cuyas dependencias no estén `done`. Si
todas las pending están bloqueadas, devuelve `None` (no falla en silencio).
`blocked_by_dependencies(features)` lista cuáles están bloqueadas y por qué.

## Backends LLM

El backend por defecto sigue siendo LM Studio local mediante `LLM_API_BASE_URL`
y `LLM_MODEL`. `llm_api_client.py` también soporta perfiles OpenAI-compatible.
Para DeepSeek oficial se puede usar `--llm-profile deepseek` o
`LLM_PROFILE=deepseek` con `DEEPSEEK_API_KEY`. El alias `deepseek_v4` queda listo
para usar un modelo futuro `deepseek-v4` o el valor explícito de `DEEPSEEK_MODEL`.

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

## Modo agente

`python -m harness.cli agent "objetivo"` ejecuta un loop de herramientas con
estado persistente en `progress/agent_sessions/`. Cada observación se guarda
para poder reanudar con `--resume`. Los patches se previsualizan por defecto y
solo se aplican con `apply=true`; antes de aplicar se guarda un checkpoint con
el diff previo y el patch solicitado. El agente también puede ejecutar
`rollback` sobre un checkpoint para revertir el patch aplicado.

El índice de repositorio mantiene caché incremental en
`progress/repo_index_cache/` y extrae símbolos, imports y referencias de Python.
El evaluador usa este grafo para encontrar tests relacionados aunque no sigan
solo la convención `tests/test_<modulo>.py`.

La localización es jerárquica: primero archivos, luego símbolos y rangos de
líneas. Si un patch aplicado falla evaluación, el pipeline intenta rollback
automático. Las reflexiones se categorizan para que la siguiente iteración use
estrategias distintas según el tipo de fallo. Antes de actuar, el pipeline puede
recuperar trayectorias similares previas. Los SOPs multiagente se validan antes
de aceptar handoffs incompletos.

## Autoaprendizaje por uso real

Cada observación del modo agente puede generar un evento en `usage_events`:
acción, objetivo, éxito/fallo, evidencia y puntaje. `harness.auto_training`
consolida esos eventos en patrones aprendidos (`learned_pattern`) y en feedback
para `skill_usage`, de modo que el router aprende de acciones reales que pasaron
tests o fallaron, no de datasets sintéticos versionados.

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
