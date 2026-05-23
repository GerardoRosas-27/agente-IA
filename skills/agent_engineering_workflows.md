# agent_engineering_workflows

Skill local adaptada desde [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills), repo clonado y revisado en el commit `250ffaa`.

## Uso

- Codigo: `skills/agent_engineering_workflows.py`
- Usa esta skill cuando el sistema necesite escoger un flujo de trabajo de ingenieria antes de planear, implementar, probar, revisar o lanzar cambios.
- Esta es una version compacta y compatible con el harness local: el upstream contiene skills Markdown para varios agentes; este sistema descubre skills por archivo Python y usa este `.md` como instrucciones.

## Workflows Instalados

La skill empaqueta los flujos mas utiles del repo externo para este harness:

- `spec-driven-development`: definir objetivo, alcance, criterios de aceptacion y limites antes de codigo.
- `planning-and-task-breakdown`: convertir una especificacion en tareas pequenas, verificables y ordenadas.
- `incremental-implementation`: construir en rebanadas pequenas, dejando el repo compilable/testeable tras cada cambio.
- `test-driven-development`: escribir prueba o reproduccion antes de cambiar comportamiento.
- `debugging-and-error-recovery`: reproducir, localizar, reducir, corregir causa raiz y guardar contra recurrencia.
- `code-review-and-quality`: revisar por correctitud, legibilidad, arquitectura, seguridad y performance.
- `security-and-hardening`: validar entradas externas, proteger secretos y exigir aprobacion para acciones riesgosas.
- `performance-optimization`: medir antes y despues; optimizar solo cuellos reales.
- `documentation-and-adrs`: documentar decisiones y tradeoffs cuando cambia arquitectura/API.
- `shipping-and-launch`: validar rollback, monitoreo y checklist antes de produccion.

## Reglas Operativas

1. Antes de iniciar un cambio no trivial, llama conceptualmente a `recommend_workflows(task)` para escoger 1-3 workflows aplicables.
2. Si hay ambiguedad de requisitos, empieza con `spec-driven-development` o pregunta al usuario.
3. Si el cambio toca varios archivos, aplica `incremental-implementation`: una rebanada, prueba, evidencia, siguiente rebanada.
4. Si se cambia comportamiento, usa `test-driven-development`: prueba/reproduccion antes del fix cuando sea razonable.
5. Si algo falla, detente y usa `debugging-and-error-recovery`; no sigas agregando features sobre una base rota.
6. Antes de cerrar, usa `code-review-and-quality` y cita la verificacion ejecutada.
7. Para webhooks, tokens, `.env`, APIs externas o comandos peligrosos, combina con `security-and-hardening`.

## API Publica

```python
from skills.agent_engineering_workflows import (
    build_execution_checklist,
    get_workflow,
    list_workflows,
    recommend_workflows,
)

recommend_workflows("arregla el webhook y agrega tests")
build_execution_checklist("implementar daemon con rollback")
```

## Mejoras Recomendadas Para El Harness

- Soportar skills Markdown nativas ademas de `skills/*.py`, para importar repos como `agent-skills` sin crear wrappers.
- Agregar un comando `python -m harness.cli skills import <repo> --select ...` que descargue `SKILL.md`, preserve atribucion y genere wrappers automaticamente.
- Conectar `agent_engineering_workflows.recommend_workflows()` con el lider/orquestador para incluir el workflow sugerido en `progress/current.md`.
- Guardar en `progress/events.jsonl` que workflow se uso para cada ciclo; esto permitiria medir que flujos producen mas `PASS`.
