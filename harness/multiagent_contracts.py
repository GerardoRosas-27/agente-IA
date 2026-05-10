"""Contratos SOP para roles multiagente del harness."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleContract:
    role: str
    required_sections: tuple[str, ...]
    handoff_artifact: str
    pass_criteria: tuple[str, ...]


DEFAULT_CONTRACTS = (
    RoleContract(
        "localizer",
        ("Objetivo", "Archivos candidatos", "Razón"),
        "LocalizationResult",
        ("lista de archivos no vacía o bloqueo explícito",),
    ),
    RoleContract(
        "patch_designer",
        ("Cambios propuestos", "Patch", "Riesgos"),
        "unified diff",
        ("patch aplica con git apply --check",),
    ),
    RoleContract(
        "implementer",
        ("Patch aplicado", "Archivos modificados", "Validación"),
        "PatchValidationResult",
        ("py_compile y tests relacionados pasan",),
    ),
    RoleContract(
        "tester",
        ("Comandos", "Salida", "Veredicto"),
        "EvaluationResult",
        ("fallos reproducibles incluyen traceback o comando exacto",),
    ),
    RoleContract(
        "reviewer",
        ("VERDICT", "Evidencia", "Riesgos"),
        "review markdown",
        ("primera línea VERDICT: PASS o VERDICT: FAIL",),
    ),
    RoleContract(
        "memory_curator",
        ("Evento", "Reflexión", "Patrón aprendido"),
        "learned_pattern",
        ("solo consolida evidencia de uso real",),
    ),
)


def contract_for_role(role: str) -> RoleContract | None:
    normalized = role.strip().lower()
    for contract in DEFAULT_CONTRACTS:
        if contract.role == normalized:
            return contract
    return None


def validate_contract_artifact(role: str, artifact: str) -> list[str]:
    contract = contract_for_role(role)
    if contract is None:
        return [f"rol desconocido: {role}"]
    artifact_lower = artifact.lower()
    return [
        f"falta sección requerida: {section}"
        for section in contract.required_sections
        if section.lower() not in artifact_lower
    ]


def assert_contract_handoff(role: str, artifact: str) -> None:
    errors = validate_contract_artifact(role, artifact)
    if errors:
        raise ValueError(f"Handoff inválido para `{role}`: " + "; ".join(errors))


def validate_contract_sequence(artifacts: dict[str, str]) -> dict[str, list[str]]:
    """Valida varios handoffs y devuelve errores por rol."""
    result: dict[str, list[str]] = {}
    for role, artifact in artifacts.items():
        errors = validate_contract_artifact(role, artifact)
        if errors:
            result[role] = errors
    return result


def contracts_context() -> str:
    lines = ["## Contratos SOP multiagente"]
    for contract in DEFAULT_CONTRACTS:
        lines.append(
            f"- `{contract.role}` -> {contract.handoff_artifact}. "
            f"Secciones: {', '.join(contract.required_sections)}. "
            f"Criterios: {', '.join(contract.pass_criteria)}."
        )
    return "\n".join(lines)
