# Arquitectura del harness

## Principios (alineados con Anthropic)

- **Cerebro separado de las manos** (Managed Agents): el modelo razona; las herramientas y el filesystem ejecutan. Aquí las “manos” son tus herramientas externas (IDE, terminal) y los informes en `progress/`.
- **Sesión = estado durable**: `feature_list.json` + `progress/*` permiten retomar trabajo tras reinicios o límites de contexto ([Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)).
- **Una feature a la vez**: reduce el fallo “hacer demasiado a la vez” y deja el repo en estado revisable.
- **Revisor separado** del implementador: mitiga autoevaluación demasiado optimista ([Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)).

## Componentes Python

| Módulo | Rol |
|--------|-----|
| `llm_api_client.py` | Cliente HTTP OpenAI-compatible → LM Studio |
| `harness/orchestrator.py` | Secuencia líder → implementador → revisor; actualiza `feature_list.json` |
| `harness/feature_store.py` | Carga/guardado atómico del JSON de features |
| `harness/cli.py` | Comandos `init`, `validate`, `status`, `run`, `expand` |

## Flujo de un ciclo `run`

1. Reclamar la siguiente `pending` (o continuar `in_progress`).
2. **Líder**: plan breve → `progress/current.md`.
3. **Implementador**: informe detallado → `progress/impl_<name>.md`.
4. **Revisor**: checklist + `VERDICT` → `progress/review_<name>.md`.
5. Si `PASS` → `done`; si `FAIL` o ambiguo → `pending` para corrección.
