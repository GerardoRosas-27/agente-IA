from __future__ import annotations

from harness.adr import create_adr_for_change, should_create_adr
from harness.quality_gates import evaluate_quality_gates, find_anti_rationalizations
from harness.review_score import aggregate_review_score, score_review_axes
from harness.workflow_router import route_workflows_for_feature, workflow_context


def test_workflow_router_selects_security_for_webhook() -> None:
    feature = {
        "id": 1,
        "name": "whatsapp_webhook_security",
        "title": "Validar webhook con token",
        "description": "Asegurar webhook externo de WhatsApp",
    }

    recommendations = route_workflows_for_feature(feature, emit=False)
    names = {item.name for item in recommendations}

    assert "security-and-hardening" in names
    assert "Workflows" in workflow_context(recommendations)


def test_quality_gates_detect_anti_rationalization_and_secrets() -> None:
    gates = evaluate_quality_gates(
        workflows=["test-driven-development", "security-and-hardening"],
        changed_files=["skills/demo.py"],
        implementation_text="No hace falta test porque es obvio. ACCESS_TOKEN=abc",
        review_text="VERDICT: PASS",
        test_output="Exit code: 0\n1 passed",
    )

    failed = {gate.name for gate in gates if not gate.passed}
    assert "anti_rationalization" in failed
    assert "security_boundary" in failed
    assert find_anti_rationalizations("lo pruebo despues")


def test_review_score_blocks_hard_security_failure() -> None:
    scores = score_review_axes(
        review_text="VERDICT: PASS",
        test_output="Exit code: 0\n1 passed",
        changed_files=[".env", "skills/demo.py"],
        quality_gate_failures=["security_boundary"],
    )

    passed, score = aggregate_review_score(scores)

    assert passed is False
    assert score < 0.8


def test_adr_created_for_architectural_change(tmp_path) -> None:
    assert should_create_adr(["harness/orchestrator.py"], "workflow router")

    result = create_adr_for_change(
        title="Workflow router",
        changed_files=["harness/orchestrator.py"],
        implementation_text="Se agrega workflow router al harness.",
        docs_dir=tmp_path,
    )

    assert result.created is True
    assert result.path.is_file()
    assert "Workflow router" in result.path.read_text(encoding="utf-8")
