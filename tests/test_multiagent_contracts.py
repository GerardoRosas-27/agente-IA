from __future__ import annotations

from harness.multiagent_contracts import contract_for_role, contracts_context, validate_contract_artifact


def test_contract_lookup_and_context() -> None:
    contract = contract_for_role("localizer")

    assert contract is not None
    assert "Archivos candidatos" in contract.required_sections
    assert "localizer" in contracts_context()


def test_contract_validation_reports_missing_sections() -> None:
    errors = validate_contract_artifact("reviewer", "VERDICT: PASS\nEvidencia: ok")

    assert errors == ["falta sección requerida: Riesgos"]
