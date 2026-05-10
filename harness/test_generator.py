"""Bug Reproduction Test (BRT) generator.

Inspirado en:
- Otter (arXiv:2502.05368): generación TDD de tests desde el issue.
- BRT Agent / Google (arXiv:2502.01821): tests fail-to-pass como verificador.
- InfCode (arXiv:2511.16004): refinamiento adversarial test↔patch.

Idea: antes de pedir el patch, el harness pide al LLM que escriba 1-3 tests
pytest *que deben fallar en HEAD* y *pasar tras la implementación*. Esos tests
se usan como verificador objetivo y como criterio de selección entre patches
(EPR — Ensemble Pass Rate).
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness.paths import PROGRESS_DIR


REPRO_TEST_DIR = PROGRESS_DIR / "repro_tests"


@dataclass(frozen=True)
class GeneratedTestSuite:
    feature_name: str
    test_path: Path
    test_code: str
    initial_status: str
    raw_output: str

    @property
    def fails_in_head(self) -> bool:
        """`True` si los tests fallan en HEAD (lo deseable para un BRT)."""
        return self.initial_status == "fails_in_head"


@dataclass(frozen=True)
class TestRunResult:
    """Resumen de una corrida de pytest. `__test__ = False` evita que pytest lo
    intente colectar como clase de test (el prefijo `Test` lo dispararía)."""

    __test__ = False

    passed: int
    failed: int
    errors: int
    total: int
    output: str
    return_code: int

    @property
    def pass_rate(self) -> float:
        """Ensemble Pass Rate (EPR): qué fracción de los tests pasaron."""
        if self.total <= 0:
            return 0.0
        return self.passed / self.total

    @property
    def all_passed(self) -> bool:
        return self.return_code == 0 and self.total > 0 and self.failed == 0 and self.errors == 0


def _slug_for(feature_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", feature_name.strip())[:80].strip("_")
    return safe or "feature"


def _extract_python_block(text: str) -> str:
    """Extrae el primer bloque ```python ...``` o devuelve el texto crudo si parece código."""
    fenced = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("python"):
            cleaned = cleaned[6:]
    return cleaned.strip()


REPRO_TEST_SYSTEM = """Eres un generador de tests de regresión estilo TDD (Bug Reproduction Tests).
Recibes la descripción de una feature pendiente con sus criterios de aceptación.
Tu tarea es escribir entre 1 y 3 tests `pytest` que:
1. FALLAN si la feature todavía NO está implementada (HEAD actual).
2. PASAN una vez la feature esté implementada correctamente.

Reglas:
- Devuelve SOLO un bloque ```python con el archivo de tests completo.
- Importa SOLO desde el repositorio bajo prueba (no instales librerías nuevas).
- Si la feature crea una skill nueva en `skills/<nombre>.py`, importa de ahí.
- Si no puedes determinar el módulo exacto, usa `pytest.importorskip("ruta")` y luego asserts simples.
- Cada test debe tener nombre descriptivo `test_<lo_que_verifica>`.
- Si un criterio de aceptación menciona valores concretos (ej. "5+3 debe ser 8"), aserción exacta.
- No uses mocks de la propia feature (eso falsea PASS); mockea solo IO externo.
- No incluyas decoradores @skip; los tests deben ejecutarse.
"""


def _user_prompt(feature_block: str, repo_context: str) -> str:
    return (
        "--- Feature ---\n"
        f"{feature_block}\n\n"
        "--- Contexto del repo ---\n"
        f"{repo_context or '(sin contexto adicional)'}\n\n"
        "Escribe el archivo pytest completo. Solo devuelve el bloque ```python."
    )


def run_pytest_on_file(test_path: Path, *, root: Path, timeout: float = 60) -> TestRunResult:
    """Corre pytest sobre un único archivo y resume resultados (passed/failed/errors)."""
    if not test_path.is_file():
        return TestRunResult(0, 0, 0, 0, f"No existe: {test_path}", 2)
    cmd = [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=short", "--no-header"]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return TestRunResult(0, 0, 1, 1, f"Timeout: {exc}", 124)

    output = (completed.stdout + "\n" + completed.stderr).strip()
    passed = _count_match(r"(\d+)\s+passed", output)
    failed = _count_match(r"(\d+)\s+failed", output)
    errors = _count_match(r"(\d+)\s+error", output)
    total = passed + failed + errors
    return TestRunResult(
        passed=passed,
        failed=failed,
        errors=errors,
        total=total,
        output=output,
        return_code=completed.returncode,
    )


def _count_match(pattern: str, text: str) -> int:
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    if not matches:
        return 0
    try:
        return sum(int(m) for m in matches)
    except ValueError:
        return 0


def generate_repro_tests(
    feature: dict[str, Any],
    *,
    invoke_llm: Callable[..., str],
    model: str,
    repo_context: str = "",
    base_dir: Path = REPRO_TEST_DIR,
    root: Path | None = None,
    run_after_generation: bool = True,
) -> GeneratedTestSuite:
    """Genera y guarda tests reproductores. Si `run_after_generation`, los corre en HEAD.

    `invoke_llm` debe respetar la firma de `harness.llm.invoke_llm`:
        invoke_llm(model, system, user, *, num_predict, role_hint, ...) -> str
    """
    feature_name = str(feature.get("name") or feature.get("title") or "feature")
    slug = _slug_for(feature_name)
    base_dir.mkdir(parents=True, exist_ok=True)
    test_path = base_dir / f"test_repro_{slug}.py"

    feature_block = _format_feature_block(feature)
    user_text = _user_prompt(feature_block, repo_context)

    raw = invoke_llm(
        model,
        REPRO_TEST_SYSTEM,
        user_text,
        num_predict=1500,
        role_hint=f"repro_test_generator_{slug}",
    )
    test_code = _extract_python_block(raw)
    if not test_code or "def test_" not in test_code:
        test_code = (
            "import pytest\n\n"
            "def test_repro_placeholder():\n"
            "    pytest.fail('No se pudo generar BRT desde el issue. Revisar prompt.')\n"
        )
    test_path.write_text(test_code, encoding="utf-8")

    initial_status = "not_run"
    if run_after_generation and root is not None:
        result = run_pytest_on_file(test_path, root=root)
        if result.failed > 0 or result.errors > 0:
            initial_status = "fails_in_head"
        elif result.all_passed:
            initial_status = "passes_in_head"
        else:
            initial_status = "error"

    return GeneratedTestSuite(
        feature_name=feature_name,
        test_path=test_path,
        test_code=test_code,
        initial_status=initial_status,
        raw_output=raw,
    )


def _format_feature_block(feature: dict[str, Any]) -> str:
    acc = feature.get("acceptance") or []
    acc_txt = "\n".join(f"- {a}" for a in acc) if isinstance(acc, list) else str(acc)
    return (
        f"id: {feature.get('id')}\n"
        f"name: {feature.get('name')}\n"
        f"title: {feature.get('title')}\n"
        f"description: {feature.get('description')}\n"
        "acceptance:\n"
        f"{acc_txt or '- (sin criterios explícitos)'}"
    )


@dataclass(frozen=True)
class EnsemblePassResult:
    """Resultado agregado de correr un suite reproducido contra varios candidatos."""

    test_path: str
    pass_rate: float
    runs: tuple[TestRunResult, ...] = field(default_factory=tuple)


def evaluate_brt_against_candidate(
    test_path: Path,
    *,
    candidate_root: Path,
    timeout: float = 60,
) -> TestRunResult:
    """Corre los BRT en un worktree/copy aislado donde se aplicó el patch candidato."""
    candidate_test_dir = candidate_root / "progress" / "repro_tests"
    candidate_test_dir.mkdir(parents=True, exist_ok=True)
    target = candidate_test_dir / test_path.name
    target.write_text(test_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    return run_pytest_on_file(target, root=candidate_root, timeout=timeout)
