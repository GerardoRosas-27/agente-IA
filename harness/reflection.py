"""Reflexión automática después de fallos de agente."""
from __future__ import annotations

from pathlib import Path

from harness.paths import STATE_DB_PATH
from harness.shared_memory import remember
from harness.trajectories import AgentTrajectory


def build_failure_reflection(goal: str, failure_report: str, *, last_action: str = "") -> str:
    """Crea una reflexión accionable a partir de evidencia de fallo."""
    report = failure_report.strip()
    lines = [
        f"Objetivo: {goal}",
        f"Acción que falló: {last_action or '(desconocida)'}",
        "Qué salió mal:",
        report[:1200] or "No hubo evidencia suficiente.",
        "Regla para la próxima vez:",
    ]
    lowered = report.lower()
    if "import" in lowered or "modulenotfound" in lowered:
        lines.append("verificar imports y ejecutar py_compile antes de proponer PASS.")
    elif "assert" in lowered or "failed" in lowered:
        lines.append("leer el test fallido, localizar la expectativa exacta y crear un patch mínimo.")
    elif "patch" in lowered:
        lines.append("previsualizar el patch y confirmar que aplica antes de modificar archivos.")
    else:
        lines.append("buscar más contexto y validar con tests relacionados antes de continuar.")
    return "\n".join(lines)


def reflect_on_trajectory(
    trajectory: AgentTrajectory,
    *,
    db_path: Path = STATE_DB_PATH,
) -> str:
    failures = [step for step in trajectory.steps if not step.success]
    if not failures:
        return "No hay fallos que reflexionar."
    last = failures[-1]
    reflection = build_failure_reflection(trajectory.goal, last.observation, last_action=last.action)
    trajectory.reflection = reflection
    remember(
        "failure_reflection",
        f"{trajectory.trajectory_id}:{last.action}",
        reflection,
        tags=["reflection", "failure", last.action],
        confidence=0.9,
        db_path=db_path,
    )
    return reflection
