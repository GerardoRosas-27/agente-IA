"""Pipeline determinista: localizar -> reparar -> validar."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from harness.evaluator import EvaluationResult, evaluate_changes
from harness.orchestrator import _apply_code_blocks
from harness.reflection import build_failure_reflection
from harness.repo_index import SymbolLocation, localize_symbols, search_repo_index
from harness.trajectories import AgentTrajectory, find_similar_trajectories, save_trajectory, start_trajectory


@dataclass(frozen=True)
class LocalizationResult:
    files: tuple[str, ...]
    rationale: str
    symbols: tuple[SymbolLocation, ...] = ()


@dataclass(frozen=True)
class PatchValidationResult:
    applied: bool
    changed_files: tuple[str, ...]
    evaluation: EvaluationResult
    report: str


@dataclass
class CodingPipelineResult:
    localization: LocalizationResult
    validation: PatchValidationResult | None
    trajectory: AgentTrajectory

    @property
    def ok(self) -> bool:
        return bool(self.validation and self.validation.applied and self.validation.evaluation.ok)


def localize_issue(goal: str, *, root: Path, limit: int = 6) -> LocalizationResult:
    matches = search_repo_index(goal, root=root, limit=limit)
    symbol_matches = tuple(localize_symbols(goal, root=root, limit=limit))
    files = tuple(match.path for match in matches)
    file_rationale = "\n".join(
        f"- {match.path}: symbols={', '.join(match.symbols[:6]) or '-'}"
        for match in matches
    ) or "No se encontraron archivos candidatos."
    symbol_rationale = "\n".join(
        f"- {item.path}:{item.start_line}-{item.end_line} {item.kind} `{item.symbol}` score={item.score}"
        for item in symbol_matches
    ) or "No se encontraron símbolos candidatos."
    rationale = f"## Archivos candidatos\n{file_rationale}\n\n## Símbolos/líneas candidatos\n{symbol_rationale}"
    return LocalizationResult(files=files, rationale=rationale, symbols=symbol_matches)


def validate_patch_candidate(
    patch_text: str,
    *,
    root: Path,
    extra_commands: list[str] | None = None,
    auto_rollback: bool = True,
) -> PatchValidationResult:
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=str(root),
        input=patch_text.strip("\r\n") + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode != 0:
        evaluation = EvaluationResult(False, ("patch_check",), check.stdout + check.stderr)
        return PatchValidationResult(False, (), evaluation, "Patch no aplicable.")

    apply_result = _apply_code_blocks(f"```patch\n{patch_text.strip()}\n```", root=root)
    changed = apply_result.changed_files
    evaluation = evaluate_changes(changed, commands=extra_commands or [], root=root)
    if auto_rollback and (not evaluation.ok or apply_result.rejected):
        rollback = subprocess.run(
            ["git", "apply", "-R", "--whitespace=nowarn"],
            cwd=str(root),
            input=patch_text.strip("\r\n") + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        rollback_report = (
            "\n\n## Rollback automático\n"
            f"Exit code: {rollback.returncode}\n{rollback.stdout}\n{rollback.stderr}"
        )
    else:
        rollback_report = ""
    return PatchValidationResult(
        applied=bool(changed) and not apply_result.rejected,
        changed_files=tuple(changed),
        evaluation=evaluation,
        report=f"{apply_result.report}\n\n{evaluation.report}{rollback_report}",
    )


def run_localize_patch_validate(
    goal: str,
    *,
    root: Path,
    patch_text: str | None = None,
    extra_commands: list[str] | None = None,
    trajectory_id: str = "",
    auto_rollback: bool = True,
) -> CodingPipelineResult:
    trajectory = start_trajectory(goal, trajectory_id)
    similar = find_similar_trajectories(goal)
    if similar:
        trajectory.add_step(
            "retrieve_similar_trajectories",
            "\n".join(f"- {item.trajectory_id}: {item.verdict} {item.reflection[:240]}" for item in similar),
            success=True,
            score=0.5,
        )
    localization = localize_issue(goal, root=root)
    trajectory.add_step(
        "localize",
        localization.rationale,
        success=bool(localization.files),
        score=1.0 if localization.files else -0.5,
    )

    validation: PatchValidationResult | None = None
    if patch_text:
        validation = validate_patch_candidate(
            patch_text,
            root=root,
            extra_commands=extra_commands,
            auto_rollback=auto_rollback,
        )
        trajectory.patches.append(patch_text)
        trajectory.tests.append(validation.evaluation.report)
        trajectory.add_step(
            "patch_validate",
            validation.report,
            success=validation.applied and validation.evaluation.ok,
            score=1.0 if validation.applied and validation.evaluation.ok else -1.0,
        )
        if not validation.evaluation.ok:
            trajectory.reflection = build_failure_reflection(goal, validation.report, last_action="patch_validate")
        trajectory.verdict = "pass" if validation.applied and validation.evaluation.ok else "fail"
    else:
        trajectory.verdict = "localized"

    save_trajectory(trajectory)
    return CodingPipelineResult(localization, validation, trajectory)
