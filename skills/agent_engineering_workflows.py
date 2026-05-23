from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Workflow:
    name: str
    phase: str
    summary: str
    use_when: tuple[str, ...]
    gates: tuple[str, ...]


WORKFLOWS: dict[str, Workflow] = {
    "spec-driven-development": Workflow(
        name="spec-driven-development",
        phase="define",
        summary="Define objetivos, alcance, criterios de aceptacion y limites antes de escribir codigo.",
        use_when=("feature", "nuevo", "requisito", "alcance", "prd", "spec", "especificacion"),
        gates=("La aceptacion esta escrita", "Los limites de scope estan claros", "No hay ambiguedades bloqueantes"),
    ),
    "planning-and-task-breakdown": Workflow(
        name="planning-and-task-breakdown",
        phase="plan",
        summary="Divide una especificacion en tareas pequenas, verificables y ordenadas por dependencia.",
        use_when=("plan", "descomponer", "tareas", "fases", "roadmap", "dependencias"),
        gates=("Cada tarea tiene salida verificable", "Las dependencias estan ordenadas", "Hay una sola tarea activa"),
    ),
    "incremental-implementation": Workflow(
        name="incremental-implementation",
        phase="build",
        summary="Implementa en rebanadas pequenas: cambiar, probar, verificar y continuar.",
        use_when=("implementar", "multiarchivo", "refactor", "cambio", "desarrolla", "build"),
        gates=("El repo queda compilable por rebanada", "Los cambios son revertibles", "No se mezclan concerns"),
    ),
    "test-driven-development": Workflow(
        name="test-driven-development",
        phase="verify",
        summary="Prueba primero el comportamiento o bug, luego implementa lo minimo para hacerlo pasar.",
        use_when=("test", "pytest", "bug", "regresion", "validar", "probar", "coverage"),
        gates=("Existe prueba que falla o valida el caso", "La prueba pasa con el fix", "La suite relevante pasa"),
    ),
    "debugging-and-error-recovery": Workflow(
        name="debugging-and-error-recovery",
        phase="verify",
        summary="Reproduce, localiza, reduce, corrige la causa raiz y agrega una guarda contra recurrencia.",
        use_when=("error", "fallo", "debug", "rompio", "traceback", "failing", "diagnosticar"),
        gates=("El fallo se reproduce", "La causa raiz esta identificada", "Hay una prueba o evidencia preventiva"),
    ),
    "code-review-and-quality": Workflow(
        name="code-review-and-quality",
        phase="review",
        summary="Revisa por cinco ejes: correctitud, legibilidad, arquitectura, seguridad y performance.",
        use_when=("review", "revisar", "merge", "calidad", "diff", "auditar"),
        gates=("Tests revisados primero", "Hallazgos clasificados por severidad", "Verificacion citada"),
    ),
    "security-and-hardening": Workflow(
        name="security-and-hardening",
        phase="review",
        summary="Trata entradas externas como no confiables, protege secretos y exige aprobacion para riesgos altos.",
        use_when=("seguridad", "secretos", "auth", "webhook", "externo", "api", "token", "permisos"),
        gates=("No hay secretos en codigo/logs", "Entradas externas se validan", "Acciones riesgosas requieren aprobacion"),
    ),
    "performance-optimization": Workflow(
        name="performance-optimization",
        phase="review",
        summary="Mide primero, identifica cuellos de botella reales y optimiza solo lo que importa.",
        use_when=("performance", "lento", "optimizar", "latencia", "memoria", "benchmark"),
        gates=("Hay medicion base", "La mejora se mide despues", "No se sacrifico correctitud"),
    ),
    "documentation-and-adrs": Workflow(
        name="documentation-and-adrs",
        phase="ship",
        summary="Documenta decisiones, tradeoffs y razones cuando cambian APIs, arquitectura o flujos.",
        use_when=("documentar", "adr", "arquitectura", "decision", "docs", "por que"),
        gates=("La decision y tradeoffs estan escritos", "El doc apunta a codigo real", "No duplica detalles obvios"),
    ),
    "shipping-and-launch": Workflow(
        name="shipping-and-launch",
        phase="ship",
        summary="Antes de lanzar, verifica rollback, monitoreo, flags y checklist final.",
        use_when=("ship", "deploy", "lanzar", "produccion", "release", "rollback"),
        gates=("Rollback definido", "Monitoreo o evidencia disponible", "Checklist pre-lanzamiento completo"),
    ),
}


def list_workflows() -> list[dict[str, object]]:
    """Devuelve los workflows disponibles en formato serializable."""
    return [
        {
            "name": workflow.name,
            "phase": workflow.phase,
            "summary": workflow.summary,
            "use_when": list(workflow.use_when),
            "gates": list(workflow.gates),
        }
        for workflow in WORKFLOWS.values()
    ]


def get_workflow(name: str) -> dict[str, object]:
    """Obtiene un workflow por nombre exacto."""
    workflow = WORKFLOWS[name]
    return {
        "name": workflow.name,
        "phase": workflow.phase,
        "summary": workflow.summary,
        "use_when": list(workflow.use_when),
        "gates": list(workflow.gates),
    }


def recommend_workflows(task: str, *, limit: int = 3) -> list[dict[str, object]]:
    """Recomienda workflows por coincidencia lexical simple contra la tarea."""
    normalized = task.casefold()
    scored: list[tuple[int, Workflow]] = []
    for workflow in WORKFLOWS.values():
        score = sum(1 for trigger in workflow.use_when if trigger in normalized)
        if workflow.name in normalized or workflow.phase in normalized:
            score += 2
        if score > 0:
            scored.append((score, workflow))

    scored.sort(key=lambda item: (-item[0], item[1].name))
    selected = [workflow for _score, workflow in scored[: max(1, limit)]]
    if not selected:
        selected = [WORKFLOWS["spec-driven-development"]]

    return [
        {
            "name": workflow.name,
            "phase": workflow.phase,
            "summary": workflow.summary,
            "gates": list(workflow.gates),
        }
        for workflow in selected
    ]


def build_execution_checklist(task: str) -> list[str]:
    """Construye una checklist minima de ejecucion para una tarea concreta."""
    recommendations = recommend_workflows(task, limit=3)
    checklist: list[str] = []
    for recommendation in recommendations:
        checklist.append(f"Aplicar workflow `{recommendation['name']}` ({recommendation['phase']}).")
        checklist.extend(str(gate) for gate in recommendation["gates"])
    checklist.append("Guardar evidencia ejecutable: tests, lint, logs o verificacion manual relevante.")
    return checklist
