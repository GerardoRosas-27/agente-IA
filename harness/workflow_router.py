from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.events import emit_event


@dataclass(frozen=True)
class WorkflowRecommendation:
    name: str
    phase: str
    summary: str
    gates: tuple[str, ...]


def _feature_text(feature: dict[str, Any]) -> str:
    acceptance = feature.get("acceptance") or []
    acceptance_text = " ".join(str(item) for item in acceptance) if isinstance(acceptance, list) else str(acceptance)
    return " ".join(
        str(feature.get(key) or "")
        for key in ("name", "title", "description")
    ) + " " + acceptance_text


def route_workflows_for_feature(
    feature: dict[str, Any],
    *,
    limit: int = 3,
    emit: bool = True,
) -> tuple[WorkflowRecommendation, ...]:
    """Selecciona workflows de ingeniería aplicables a una feature."""
    task = _feature_text(feature)
    try:
        from skills.agent_engineering_workflows import recommend_workflows

        raw = recommend_workflows(task, limit=limit)
    except Exception:
        raw = [
            {
                "name": "spec-driven-development",
                "phase": "define",
                "summary": "Definir alcance y aceptación antes de implementar.",
                "gates": ["Aceptación explícita", "Scope claro", "Verificación planeada"],
            }
        ]
    recommendations = tuple(
        WorkflowRecommendation(
            name=str(item.get("name") or ""),
            phase=str(item.get("phase") or ""),
            summary=str(item.get("summary") or ""),
            gates=tuple(str(gate) for gate in (item.get("gates") or [])),
        )
        for item in raw
        if isinstance(item, dict)
    )
    if emit:
        emit_event(
            "workflow.routed",
            feature_id=feature.get("id"),
            feature_name=feature.get("name"),
            workflows=[item.name for item in recommendations],
        )
    return recommendations


def workflow_context(recommendations: tuple[WorkflowRecommendation, ...]) -> str:
    """Renderiza workflows recomendados para incluirlos en prompts del líder/reviewer."""
    if not recommendations:
        return "No se seleccionaron workflows de ingeniería."
    lines = ["## Workflows de ingeniería sugeridos"]
    for item in recommendations:
        lines.append(f"- `{item.name}` ({item.phase}): {item.summary}")
        for gate in item.gates:
            lines.append(f"  - Gate: {gate}")
    return "\n".join(lines)
