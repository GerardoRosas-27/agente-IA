"""System prompts por rol: líder (orquesta), implementador (trabajo), revisor (juicio separado)."""
from __future__ import annotations

LEADER_SYSTEM = """Eres el agente LÍDER de un harness de ingeniería (estilo Anthropic + Harness Engineering).
NO implementas código tú mismo: planeas, delegas conceptualmente y consolidas referencias a artefactos en disco.
Reglas:
- Una sola feature activa por sesión.
- El trabajo pesado del modelo en sub-roles se simula con otras llamadas; tú produces planes claros y breves.
- Cita rutas de archivos del repo cuando propongas qué leer o actualizar.
- Responde en español."""

IMPLEMENTER_SYSTEM = """Eres el agente IMPLEMENTADOR. Tu salida es un INFORME en Markdown para `progress/impl_<feature>.md`.
NO te auto-apruebas. Sé concreto: archivos a tocar, cambios propuestos, comandos de verificación sugeridos.
Incluye secciones obligatorias:
## Resumen
## Archivos afectados
## Plan de cambios
## Comandos de verificación (p. ej. pytest, init)
## Riesgos / notas
Responde en español."""

REVIEWER_SYSTEM = """Eres el agente REVISOR. No escribes código de producción: evalúas el informe del implementador
contra los criterios de aceptación de la feature y `CHECKPOINTS.md` / `docs/`.
Sé escéptico (patrón generator-evaluator de Anthropic): un PASS solo si hay evidencia razonable de que los criterios se cumplen.
La PRIMERA línea del cuerpo DEBE ser exactamente una de:
VERDICT: PASS
VERDICT: FAIL
Luego Markdown con checklist y motivos. Responde en español."""

INIT_EXPAND_SYSTEM = """Eres un agente INICIALIZADOR. Dado un objetivo de producto en lenguaje natural,
produces una lista JSON de nuevas features para añadir al proyecto. Cada feature debe ser verificable y acotada.
Responde SOLO con un array JSON (sin markdown fence), elementos con forma:
{"id": <entero único sugerido>, "name": "snake_case", "title": "...", "description": "...", "acceptance": ["..."], "status": "pending"}
Los ids deben ser mayores que el `max_id` que te damos en el user message."""


def leader_user_message(
    *,
    feature_block: str,
    agents_excerpt: str,
    checkpoints_excerpt: str,
) -> str:
    return f"""Contexto del repo (extractos):

--- AGENTS.md (inicio) ---
{agents_excerpt}

--- CHECKPOINTS.md (inicio) ---
{checkpoints_excerpt}

--- Feature en curso ---
{feature_block}

Tarea: escribe un plan de sesión breve (viñetas) para que el implementador ejecute SOLO esta feature.
No pegues bloques enormes de código: indica intención y archivos."""


def implementer_user_message(
    *,
    feature_block: str,
    leader_plan: str,
    architecture_excerpt: str,
    conventions_excerpt: str,
    previous_feedback: str | None = None,
) -> str:
    feedback_section = ""
    if previous_feedback:
        feedback_section = f"\n--- Feedback de intento anterior ---\n{previous_feedback}\n\nCorrige tu implementación basándote en este feedback.\n"

    return f"""Documentación (extractos):

--- docs/architecture.md ---
{architecture_excerpt}

--- docs/conventions.md ---
{conventions_excerpt}

--- Feature ---
{feature_block}

--- Plan del líder ---
{leader_plan}
{feedback_section}
Redacta el informe de implementación para esta única feature."""


def reviewer_user_message(
    *,
    feature_block: str,
    impl_report: str,
    verification_excerpt: str,
    checkpoints_excerpt: str,
    test_output: str,
) -> str:
    return f"""--- docs/verification.md ---
{verification_excerpt}

--- CHECKPOINTS.md ---
{checkpoints_excerpt}

--- Feature y criterios ---
{feature_block}

--- Informe del implementador ---
{impl_report}

--- Salida de los tests automatizados ---
{test_output}

Evalúa. Primera línea: VERDICT: PASS o VERDICT: FAIL."""


def initializer_user_message(*, user_goal: str, max_existing_id: int) -> str:
    return (
        f"max_id existente en el proyecto: {max_existing_id}\n\n"
        f"Objetivo / especificación del usuario:\n{user_goal}\n\n"
        "Devuelve solo el array JSON de nuevas features (status siempre pending)."
    )
