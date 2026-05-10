"""Evaluación best-of-N de patches en espacios aislados."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness.coding_pipeline import PatchValidationResult, validate_patch_candidate


@dataclass(frozen=True)
class PatchCandidateResult:
    index: int
    ok: bool
    score: float
    changed_files: tuple[str, ...]
    report: str


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
) -> list[PatchCandidateResult]:
    results: list[PatchCandidateResult] = []
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
            results.append(
                PatchCandidateResult(
                    index=index,
                    ok=validation.applied and validation.evaluation.ok,
                    score=round(score, 4),
                    changed_files=validation.changed_files,
                    report=validation.report,
                )
            )
    results.sort(key=lambda item: (item.ok, item.score), reverse=True)
    return results


def choose_best_patch(
    patches: list[str],
    *,
    root: Path,
    extra_commands: list[str] | None = None,
) -> PatchCandidateResult | None:
    results = evaluate_patch_candidates(patches, root=root, extra_commands=extra_commands)
    return results[0] if results else None
