from __future__ import annotations

import pytest

from harness.multiagent_contracts import (
    assert_contract_handoff,
    contract_for_role,
    contracts_context,
    validate_contract_artifact,
    validate_contract_sequence,
)


def test_contract_lookup_and_context() -> None:
    contract = contract_for_role("localizer")

    assert contract is not None
    assert "Archivos candidatos" in contract.required_sections
    assert "localizer" in contracts_context()


def test_contract_validation_reports_missing_sections() -> None:
    errors = validate_contract_artifact("reviewer", "VERDICT: PASS\nEvidencia: ok")

    assert errors == ["falta sección requerida: Riesgos"]


def test_contract_sequence_and_assertion() -> None:
    artifacts = {
        "localizer": "Objetivo: x\nArchivos candidatos: a.py\nRazón: match",
        "reviewer": "VERDICT: PASS\nEvidencia: ok",
    }

    errors = validate_contract_sequence(artifacts)

    assert "localizer" not in errors
    assert errors["reviewer"] == ["falta sección requerida: Riesgos"]
    with pytest.raises(ValueError):
        assert_contract_handoff("reviewer", artifacts["reviewer"])
