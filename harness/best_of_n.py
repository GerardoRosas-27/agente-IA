"""Evaluación best-of-N de patches en espacios aislados.

Mejora inspirada en BRT Agent (Google, arXiv:2502.01821) y Otter
(arXiv:2502.05368): si se proveen tests reproductores (Bug Reproduction Tests)
generados desde el issue, el ranking de candidatos pasa a usar el **Ensemble
Pass Rate (EPR)**: fracción de tests del BRT que el candidato hace pasar. Esto
mostró 70% de top-1 acierto sobre 20 candidatos en el paper original.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness.coding_pipeline import PatchValidationResult, validate_patch_candidate
from harness.test_generator import TestRunResult, evaluate_brt_against_candidate


@dataclass(frozen=True)
class PatchCandidateResult:
    index: int
    ok: bool
    score: float
    changed_files: tuple[str, ...]
    report: str
    brt_pass_rate: float = 0.0
    brt_total: int = 0


def _copy_repo(root: Path, destination: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {".git", "__pycache__", ".pytest_cache"}}

    shutil.copytree(root, destination, ignore=ignore)
    subprocess.run(["git", "init"], cwd=str(destination), capture_output=True, text=True, timeout=30)


def _isolated_root(root: Path, parent: Path, index: int) -> Path:
    target = parent / f"candidate_{index}"
    worktree = subprocess.run(
        ["git", "worktree", "add", "--detach", str(target), "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=45,
    )
    if worktree.returncode == 0:
        return target
    _copy_repo(root, target)
    return target


def evaluate_patch_candidates(
    patches: list[str],
    *,
    root: Path,
    extra_commands: list[str] | None = None,
    brt_paths: list[Path] | None = None,
) -> list[PatchCandidateResult]:
    """Evalúa N patches en worktrees aislados.

    Si se pasa `brt_paths`, cada candidato se puntúa también por su Ensemble
    Pass Rate (EPR) sobre los Bug Reproduction Tests, y el ranking final
    prioriza:
      1. patch aplicable + tests existentes ok + EPR=1.0
      2. EPR alto, aunque la suite global tenga otras fallas
      3. score base (mínimo número de líneas tocadas como tiebreaker)
    """
    results: list[PatchCandidateResult] = []
    brt_paths = brt_paths or []
    with tempfile.TemporaryDirectory(prefix="harness_best_of_n_") as tmp:
        parent = Path(tmp)
        for index, patch in enumerate(patches):
            candidate_root = _isolated_root(root, parent, index)
            validation: PatchValidationResult = validate_patch_candidate(
                patch,
                root=candidate_root,
                extra_commands=extra_commands,
            )
            score = 0.0
            if validation.applied:
                score += 0.4
            if validation.evaluation.ok:
                score += 0.6
            score -= 0.01 * len(validation.changed_files)

            brt_pass_rate = 0.0
            brt_total = 0
            brt_reports: list[str] = []
            if validation.applied:
                brt_runs: list[TestRunResult] = []
                for brt_path in brt_paths:
                    if not brt_path.is_file():
                        continue
                    run = evaluate_brt_against_candidate(brt_path, candidate_root=candidate_root)
                    brt_runs.append(run)
                    brt_reports.append(
                        f"### BRT {brt_path.name}\n"
                        f"passed={run.passed} failed={run.failed} errors={run.errors}\n"
                        f"{run.output[-1500:]}"
                    )
                if brt_runs:
                    total_passed = sum(r.passed for r in brt_runs)
                    total_tests = sum(r.total for r in brt_runs)
                    brt_total = total_tests
                    brt_pass_rate = (total_passed / total_tests) if total_tests else 0.0
                    # EPR domina el score: un patch que hace pasar todos los BRT
                    # gana incluso si toca más líneas que otro que solo arregla
                    # tests preexistentes.
                    score += brt_pass_rate

            report = validation.report
            if brt_reports:
                report = report + "\n\n## Bug Reproduction Tests\n" + "\n\n".join(brt_reports)

            results.append(
                PatchCandidateResult(
                    index=index,
                    ok=validation.applied and validation.evaluation.ok,
                    score=round(score, 4),
                    changed_files=validation.changed_files,
                    report=report,
                    brt_pass_rate=round(brt_pass_rate, 4),
                    brt_total=brt_total,
                )
            )
    # EPR primero (si hay BRTs), luego ok global, luego score base.
    results.sort(
        key=lambda item: (item.brt_pass_rate, item.ok, item.score),
        reverse=True,
    )
    return results


def choose_best_patch(
    patches: list[str],
    *,
    root: Path,
    extra_commands: list[str] | None = None,
    brt_paths: list[Path] | None = None,
) -> PatchCandidateResult | None:
    results = evaluate_patch_candidates(
        patches,
        root=root,
        extra_commands=extra_commands,
        brt_paths=brt_paths,
    )
    return results[0] if results else None
