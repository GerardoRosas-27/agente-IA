"""Reflexión automática después de fallos de agente."""
from __future__ import annotations

from pathlib import Path

from harness.paths import STATE_DB_PATH
from harness.shared_memory import remember
from harness.trajectories import AgentTrajectory, TrajectoryStep


def classify_failure(failure_report: str) -> str:
    lowered = failure_report.lower()
    if "modulenotfound" in lowered or "importerror" in lowered or " import " in lowered:
        return "import_error"
    if "assert" in lowered or "failed" in lowered:
        return "test_assertion"
    if "patch" in lowered and ("does not apply" in lowered or "no aplica" in lowered or "corrupt" in lowered):
        return "patch_conflict"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "no se encontraron archivos" in lowered or "wrong file" in lowered:
        return "wrong_file"
    return "unknown_failure"


def build_failure_reflection(goal: str, failure_report: str, *, last_action: str = "") -> str:
    """Crea una reflexión accionable a partir de evidencia de fallo."""
    report = failure_report.strip()
    category = classify_failure(report)
    lines = [
        f"Objetivo: {goal}",
        f"Acción que falló: {last_action or '(desconocida)'}",
        f"Categoría: {category}",
        "Qué salió mal:",
        report[:1200] or "No hubo evidencia suficiente.",
        "Regla para la próxima vez:",
    ]
    if category == "import_error":
        lines.append("verificar imports y ejecutar py_compile antes de proponer PASS.")
    elif category == "test_assertion":
        lines.append("leer el test fallido, localizar la expectativa exacta y crear un patch mínimo.")
    elif category == "patch_conflict":
        lines.append("previsualizar el patch y confirmar que aplica antes de modificar archivos.")
    elif category == "timeout":
        lines.append("reducir el alcance, correr tests relacionados y evitar comandos largos sin progreso.")
    elif category == "wrong_file":
        lines.append("rehacer localización jerárquica archivo→símbolo→líneas antes de editar.")
    else:
        lines.append("buscar más contexto y validar con tests relacionados antes de continuar.")
    return "\n".join(lines)


def debugging_recovery_checklist(goal: str, failure_report: str) -> list[str]:
    """Checklist sistemática inspirada en debugging-and-error-recovery."""
    category = classify_failure(failure_report)
    return [
        f"Objetivo: {goal}",
        "Reproducir el fallo con el comando o test más pequeño posible.",
        f"Clasificar el fallo actual: {category}.",
        "Localizar la capa responsable (test, import, patch, runtime, servicio externo).",
        "Reducir el caso hasta la entrada mínima que falla.",
        "Corregir la causa raíz, no solo el síntoma.",
        "Agregar una prueba, gate o evidencia que prevenga recurrencia.",
        "Reanudar solo cuando la verificación relevante pase.",
    ]


def reflect_on_trajectory(
    trajectory: AgentTrajectory,
    *,
    db_path: Path = STATE_DB_PATH,
) -> str:
    last: TrajectoryStep | None = None
    for step in trajectory.steps:
        if not step.success:
            last = step
    if last is None:
        return "No hay fallos que reflexionar."
    reflection = build_failure_reflection(trajectory.goal, last.observation, last_action=last.action)
    trajectory.reflection = reflection
    category = classify_failure(last.observation)
    remember(
        "failure_reflection",
        f"{trajectory.trajectory_id}:{last.action}",
        reflection,
        tags=["reflection", "failure", category, last.action],
        confidence=0.9,
        db_path=db_path,
    )
    return reflection
