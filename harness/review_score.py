from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


REVIEW_AXES = ("correctness", "readability", "architecture", "security", "performance")


@dataclass(frozen=True)
class ReviewAxisScore:
    axis: str
    score: float
    detail: str
    hard: bool = False


def score_review_axes(
    *,
    review_text: str,
    test_output: str,
    changed_files: Iterable[str],
    quality_gate_failures: Iterable[str] = (),
) -> tuple[ReviewAxisScore, ...]:
    """Puntúa una revisión por 5 ejes con heurísticas deterministas."""
    text = f"{review_text}\n{test_output}".casefold()
    files = list(changed_files)
    failures = set(quality_gate_failures)
    tests_ok = ("exit code: 0" in text or "tests ok" in text) and "failed" not in text
    secret_file = any(path.startswith(".env") or path.endswith("/.env") for path in files)
    large_diff = len(files) > 12
    external = any(token in text for token in ("webhook", "token", "auth", "secret", "whatsapp"))
    perf_terms = any(token in text for token in ("benchmark", "latencia", "performance", "memoria", "tiempo"))

    return (
        ReviewAxisScore(
            "correctness",
            1.0 if tests_ok and "test_evidence" not in failures else 0.35,
            "tests pasan" if tests_ok else "sin evidencia limpia de tests",
            hard="test_evidence" in failures,
        ),
        ReviewAxisScore(
            "readability",
            0.75 if not large_diff else 0.45,
            f"{len(files)} archivos cambiados",
        ),
        ReviewAxisScore(
            "architecture",
            0.85 if not large_diff else 0.55,
            "cambio acotado" if not large_diff else "diff amplio; revisar separación de concerns",
        ),
        ReviewAxisScore(
            "security",
            0.2 if secret_file or "security_boundary" in failures else (0.75 if external else 0.9),
            "riesgo de secreto/límite externo" if external or secret_file else "sin señales de riesgo alto",
            hard=secret_file or "security_boundary" in failures,
        ),
        ReviewAxisScore(
            "performance",
            0.8 if perf_terms else 0.7,
            "evidencia/perfil mencionado" if perf_terms else "sin señales de cambio en hot path",
        ),
    )


def aggregate_review_score(scores: Iterable[ReviewAxisScore]) -> tuple[bool, float]:
    items = list(scores)
    if not items:
        return False, 0.0
    average = sum(item.score for item in items) / len(items)
    hard_fail = any(item.hard and item.score < 0.65 for item in items)
    return (not hard_fail) and average >= 0.65, round(average, 4)


def review_score_markdown(scores: Iterable[ReviewAxisScore]) -> str:
    items = list(scores)
    passed, average = aggregate_review_score(items)
    lines = [
        "## Review Score 5 Ejes",
        f"Resultado: {'PASS' if passed else 'FAIL'} (score={average:.3f})",
        "",
        "| Eje | Score | Hard | Detalle |",
        "|---|---:|---|---|",
    ]
    for item in items:
        lines.append(f"| {item.axis} | {item.score:.2f} | {'yes' if item.hard else 'no'} | {item.detail} |")
    return "\n".join(lines)
