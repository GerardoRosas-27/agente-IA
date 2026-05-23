from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QualityGate:
    name: str
    passed: bool
    detail: str
    hard: bool = False


ANTI_RATIONALIZATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(test(s|ear|eo)?\s+despu[eé]s|lo pruebo despu[eé]s)\b", "pospone pruebas"),
    (r"\b(no hace falta|no es necesario)\s+(test|prueba|validaci[oó]n)", "minimiza verificación"),
    (r"\b(es obvio|deber[ií]a funcionar|se ve bien)\b", "asume sin evidencia"),
    (r"\b(solo es|es un cambio pequeñ[oa])\b.*\b(sin test|sin prueba)\b", "usa tamaño como excusa"),
)

SECRET_NAME_RE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|passwd|bearer\s+[a-z0-9._-]+)")
WEBHOOK_RE = re.compile(r"(?i)(webhook|callback|hub\.challenge|x-hub-signature|whatsapp)")


def find_anti_rationalizations(text: str) -> list[str]:
    found: list[str] = []
    for pattern, label in ANTI_RATIONALIZATION_PATTERNS:
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            found.append(label)
    return list(dict.fromkeys(found))


def security_gate_details(
    *,
    changed_files: Iterable[str],
    implementation_text: str = "",
    test_output: str = "",
) -> QualityGate:
    files = list(changed_files)
    risky_files = [
        path for path in files
        if path.startswith(".env") or path.endswith("/.env") or path.endswith(".key") or path.endswith(".pem")
    ]
    evidence = f"{implementation_text}\n{test_output}"
    secret_mentions = bool(SECRET_NAME_RE.search(evidence))
    external_boundary = bool(WEBHOOK_RE.search(evidence) or any("webhook" in path.lower() for path in files))
    if risky_files:
        return QualityGate("security_boundary", False, f"archivos sensibles modificados: {', '.join(risky_files)}", hard=True)
    if secret_mentions:
        return QualityGate("security_boundary", False, "posible secreto/token mencionado en evidencia de implementación", hard=True)
    if external_boundary:
        return QualityGate("security_boundary", True, "flujo externo detectado; requiere validación de payloads y secretos")
    return QualityGate("security_boundary", True, "sin señales de secretos ni límites externos críticos")


def evaluate_quality_gates(
    *,
    workflows: Iterable[str],
    changed_files: Iterable[str],
    implementation_text: str,
    review_text: str,
    test_output: str,
    validation_output: str = "",
) -> tuple[QualityGate, ...]:
    workflow_set = set(workflows)
    files = list(changed_files)
    combined = "\n".join([implementation_text, review_text, test_output, validation_output])
    gates: list[QualityGate] = []

    anti = find_anti_rationalizations(combined)
    gates.append(
        QualityGate(
            "anti_rationalization",
            not anti,
            "sin excusas de omisión" if not anti else f"detectado: {', '.join(anti)}",
            hard=bool(anti),
        )
    )

    tests_passed = (
        "Exit code: 0" in test_output
        or "tests ok" in (test_output or "").casefold()
        or re.search(r"\b\d+\s+passed\b", test_output or "")
    )
    needs_tests = bool({"test-driven-development", "debugging-and-error-recovery"} & workflow_set)
    gates.append(
        QualityGate(
            "test_evidence",
            bool(tests_passed),
            "tests ejecutados con evidencia" if tests_passed else "no hay evidencia clara de tests pasando",
            hard=needs_tests,
        )
    )

    gates.append(security_gate_details(changed_files=files, implementation_text=implementation_text, test_output=test_output))

    if "documentation-and-adrs" in workflow_set:
        has_doc = any(path.startswith("docs/") or path.startswith("progress/") for path in files)
        gates.append(QualityGate("documentation_evidence", has_doc, "documentación/ADR presente" if has_doc else "falta evidencia documental"))

    if "debugging-and-error-recovery" in workflow_set:
        debug_words = ("reproduce", "causa raiz", "root cause", "regression", "regresión")
        has_debug = any(word in combined.casefold() for word in debug_words)
        gates.append(QualityGate("debugging_discipline", has_debug, "incluye reproducción/causa raíz" if has_debug else "falta reproducción o causa raíz"))

    return tuple(gates)


def gates_markdown(gates: Iterable[QualityGate]) -> str:
    lines = ["## Quality Gates"]
    for gate in gates:
        status = "PASS" if gate.passed else "FAIL"
        hard = " hard" if gate.hard else ""
        lines.append(f"- {status} `{gate.name}`{hard}: {gate.detail}")
    return "\n".join(lines)
