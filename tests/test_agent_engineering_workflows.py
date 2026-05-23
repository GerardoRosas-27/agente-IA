from __future__ import annotations

from skills.agent_engineering_workflows import (
    build_execution_checklist,
    get_workflow,
    list_workflows,
    recommend_workflows,
)


def test_list_workflows_includes_core_lifecycle() -> None:
    names = {workflow["name"] for workflow in list_workflows()}

    assert "spec-driven-development" in names
    assert "incremental-implementation" in names
    assert "test-driven-development" in names
    assert "code-review-and-quality" in names


def test_recommend_workflows_for_bug_and_tests() -> None:
    recommendations = recommend_workflows("debug este bug y agrega pytest de regresion", limit=2)
    names = [item["name"] for item in recommendations]

    assert "debugging-and-error-recovery" in names
    assert "test-driven-development" in names


def test_recommend_workflows_defaults_to_spec_for_unclear_task() -> None:
    recommendations = recommend_workflows("quiero una cosa nueva", limit=1)

    assert recommendations[0]["name"] == "spec-driven-development"


def test_get_workflow_returns_public_shape() -> None:
    workflow = get_workflow("security-and-hardening")

    assert workflow["phase"] == "review"
    assert "summary" in workflow
    assert "gates" in workflow


def test_build_execution_checklist_includes_evidence_gate() -> None:
    checklist = build_execution_checklist("implementar webhook externo con token")

    assert any("workflow" in item for item in checklist)
    assert checklist[-1].startswith("Guardar evidencia ejecutable")
