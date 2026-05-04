# AGENTS.md — Mapa para agentes (divulgación progresiva)

Este archivo es el **punto de entrada** para cualquier agente (humano o IA) que trabaje en este repositorio. Es un **mapa**: lee solo lo necesario cuando lo necesites.

## 1. Antes de empezar (obligatorio)

1. Ejecuta la verificación del repo:
   - Windows: `powershell -File init.ps1`
   - Unix: `./init.sh`
2. Lee `progress/current.md` para el estado de la última sesión.
3. Abre `feature_list.json`: como máximo **una** feature en `in_progress`; prioriza `pending` por `id` ascendente.

## 2. Mapa del repositorio

| Ruta | Contenido | Cuándo leerlo |
|------|-------------|---------------|
| `feature_list.json` | Alcance y estado por feature | Siempre al planificar |
| `progress/current.md` | Plan / trazas recientes | Al arrancar sesión |
| `progress/history.md` | Bitácora append-only | Contexto histórico |
| `progress/impl_*.md` | Informe del implementador | Auditoría por feature |
| `progress/review_*.md` | Informe del revisor | Auditoría por feature |
| `docs/architecture.md` | Qué cuenta como buen diseño aquí | Antes de implementar |
| `docs/conventions.md` | Estilo y convenciones | Antes de editar código |
| `docs/verification.md` | Cómo verificar cambios | Antes de marcar `done` |
| `CHECKPOINTS.md` | Criterios objetivos de calidad | Revisión / QA |
| `harness/` | Orquestación Líder → Implementador → Revisor | Para extender el harness |

## 3. Reglas duras

- **Una feature a la vez** en progreso (`in_progress`).
- **Estado en disco**, no solo en el chat: los sub-roles dejan informes en `progress/`.
- **Separación de juicios** (patrón generator–evaluator): quien implementa no se auto-aprueba; el revisor emite `VERDICT: PASS` o `VERDICT: FAIL` en la primera línea.
- **LM Studio** es el único backend LLM previsto (`llm_api_client.py` + variables `LLM_*` en `.env`).

## 4. Cómo ejecutar el harness (humanos)

```bash
python -m harness.cli validate
python -m harness.cli status
python -m harness.cli run
```

Inicializador (descomponer un objetivo en nuevas filas `pending`):

```bash
python -m harness.cli expand "Tu especificación en lenguaje natural..."
```

## 5. Si te bloqueas

Documenta el bloqueo en `progress/current.md` y deja la feature en `blocked` o vuelve a `pending` con nota clara en `progress/history.md`.
