"""Benchmarks locales y deterministas para medir capacidades del harness."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.agent_loop import execute_tool_action
from harness.evaluator import evaluate_changes
from harness.repo_index import search_repo_index


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    goal: str
    kind: str


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    passed: bool
    detail: str


DEFAULT_BENCHMARKS = (
    BenchmarkCase("repo_index_symbols", "encontrar función webhook", "repo_index"),
    BenchmarkCase("tool_read_guard", "bloquear lectura fuera del repo", "tool_guard"),
    BenchmarkCase("objective_evaluator", "bloquear comando destructivo", "evaluator"),
)


def run_benchmarks(*, root: Path, output_path: Path | None = None) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for case in DEFAULT_BENCHMARKS:
        if case.kind == "repo_index":
            matches = search_repo_index(case.goal, root=root, limit=5)
            passed = bool(matches)
            detail = ", ".join(match.path for match in matches) or "sin matches"
        elif case.kind == "tool_guard":
            observation = execute_tool_action({"action": "read", "path": "../secret.txt"}, root=root)
            passed = not observation.ok
            detail = observation.content
        elif case.kind == "evaluator":
            evaluation = evaluate_changes([], commands=["git reset --hard"], root=root)
            passed = not evaluation.ok and "BLOQUEADO" in evaluation.report
            detail = evaluation.report
        else:
            passed = False
            detail = f"tipo desconocido: {case.kind}"
        results.append(BenchmarkResult(case.name, passed, detail[:1000]))

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return results


def benchmark_summary(results: list[BenchmarkResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    lines = [f"Benchmarks: {passed}/{len(results)} passed"]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"- {status} {result.name}: {result.detail}")
    return "\n".join(lines)
