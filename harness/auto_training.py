"""Ciclos de autoaprendizaje para entrenar la red de selección de skills."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from harness.paths import PROGRESS_DIR, STATE_DB_PATH
from harness.shared_memory import recall, record_skill_usage, remember
from harness.tool_learning import ToolRecommendation, build_tool_reuse_plan


@dataclass(frozen=True)
class TrainingTask:
    task_id: str
    title: str
    goal: str
    created_at: str


@dataclass(frozen=True)
class ActionPlan:
    decision: str
    rationale: str
    tools_to_use: tuple[str, ...]
    tasks: tuple[str, ...]
    subtasks: tuple[str, ...]
    create_if_missing: tuple[str, ...]
    code_solution: str


@dataclass(frozen=True)
class TrainingCycleResult:
    task: TrainingTask
    plan: ActionPlan
    learned_skills: tuple[str, ...]
    memory_key: str
    report_path: str


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    return slug[:80] or "training_task"


def _task_from_goal(goal: str) -> TrainingTask:
    stamp = datetime.now().isoformat(timespec="seconds")
    return TrainingTask(
        task_id=f"train_{int(time.time() * 1000)}",
        title=f"Entrenamiento: {goal[:80]}",
        goal=goal,
        created_at=stamp,
    )


def _adapter_code_for_reuse(goal: str, top: ToolRecommendation) -> str:
    module_name = top.path[:-3].replace("/", ".").replace("\\", ".")
    return (
        "```python\n"
        "# Adaptador sugerido por autoaprendizaje; ajustar nombres/argumentos al caso real.\n"
        "from importlib import import_module\n\n"
        f"skill = import_module('{module_name}')\n\n"
        "def execute(request: dict):\n"
        "    if hasattr(skill, 'run'):\n"
        "        return skill.run(request)\n"
        "    raise RuntimeError('La skill recomendada no expone run(request).')\n\n"
        f"# Objetivo entrenado: {goal}\n"
        "```\n"
    )


def _new_skill_code_suggestion(goal: str) -> str:
    skill_name = _slug(goal)
    return (
        "```python\n"
        f"# skills/{skill_name}.py\n"
        "from __future__ import annotations\n\n"
        "def run(request: dict) -> dict:\n"
        "    \"\"\"Implementar contrato mínimo cuando no exista una skill reutilizable.\"\"\"\n"
        "    raise NotImplementedError('Completar esta skill con pruebas antes de usarla.')\n"
        "```\n"
    )


def build_training_plan(goal: str, *, db_path: Path = STATE_DB_PATH) -> ActionPlan:
    reuse_plan = build_tool_reuse_plan(goal, db_path=db_path)
    top = reuse_plan.recommendations[0] if reuse_plan.recommendations else None

    if reuse_plan.decision == "REUSE_EXISTING_SKILL" and top:
        tools = tuple(item.skill_name for item in reuse_plan.recommendations)
        create_if_missing = (
            "Crear solo adaptador de entrada/salida si la skill no encaja directamente.",
            "Crear pruebas del adaptador; no duplicar la lógica de la skill base.",
        )
        code_solution = _adapter_code_for_reuse(goal, top)
    else:
        tools = tuple(item.skill_name for item in reuse_plan.recommendations)
        create_if_missing = (
            "Crear una nueva skill con API `run(request)`.",
            "Agregar `skills/<nombre>.md` con cuándo usarla y ejemplos.",
            "Registrar el primer uso en memoria para entrenar la red.",
        )
        code_solution = _new_skill_code_suggestion(goal)

    tasks = (
        "Analizar el objetivo y extraer intención, entradas y salida esperada.",
        "Consultar memoria compartida y recomendaciones con energía libre.",
        "Elegir reutilizar, extender o crear según menor energía libre y cobertura.",
        "Ejecutar o simular el flujo mínimo y guardar resultado del aprendizaje.",
    )

    return ActionPlan(
        decision=reuse_plan.decision,
        rationale=reuse_plan.rationale,
        tools_to_use=tools,
        tasks=tasks,
        subtasks=reuse_plan.subtasks,
        create_if_missing=create_if_missing,
        code_solution=code_solution,
    )


def _plan_to_markdown(result: TrainingCycleResult) -> str:
    plan = result.plan
    lines = [
        f"# Autoentrenamiento · {result.task.title}",
        "",
        f"- task_id: `{result.task.task_id}`",
        f"- objetivo: {result.task.goal}",
        f"- decisión: `{plan.decision}`",
        f"- memoria: `{result.memory_key}`",
        "",
        "## Razonamiento",
        plan.rationale,
        "",
        "## Herramientas candidatas",
    ]
    if plan.tools_to_use:
        lines.extend(f"- `{tool}`" for tool in plan.tools_to_use)
    else:
        lines.append("- Ninguna herramienta existente con confianza suficiente.")

    lines.extend(["", "## Tareas"])
    lines.extend(f"- {task}" for task in plan.tasks)
    lines.extend(["", "## Subtareas"])
    lines.extend(f"- {subtask}" for subtask in plan.subtasks)
    lines.extend(["", "## Si falta capacidad, crear"])
    lines.extend(f"- {step}" for step in plan.create_if_missing)
    lines.extend(["", "## Solución de código sugerida", plan.code_solution])
    return "\n".join(lines)


def run_training_cycle(
    goal: str,
    *,
    db_path: Path = STATE_DB_PATH,
    progress_dir: Path = PROGRESS_DIR,
) -> TrainingCycleResult:
    """Crea una tarea de entrenamiento, genera plan y lo aprende en runtime."""
    task = _task_from_goal(goal)
    plan = build_training_plan(goal, db_path=db_path)

    learned_skills: list[str] = []
    primary_skill = plan.tools_to_use[0] if plan.tools_to_use else ""
    if primary_skill:
        record_skill_usage(
            primary_skill,
            goal,
            (
                f"Autoentrenamiento `{task.task_id}`: usar `{primary_skill}` primero. "
                "Crear solo adaptador si falta conexión entre entradas/salidas."
            ),
            success=plan.decision == "REUSE_EXISTING_SKILL",
            outcome=f"plan={plan.decision}",
            db_path=db_path,
        )
        learned_skills.append(primary_skill)

    memory_key = task.task_id
    remember(
        "auto_training",
        memory_key,
        json.dumps(
            {
                "task": asdict(task),
                "plan": asdict(plan),
                "learned_skills": learned_skills,
            },
            ensure_ascii=False,
        ),
        tags=["auto_training", "runtime_learning", plan.decision],
        confidence=0.95,
        db_path=db_path,
    )

    progress_dir.mkdir(parents=True, exist_ok=True)
    report_path = progress_dir / f"auto_training_{_slug(task.task_id)}.md"
    result = TrainingCycleResult(
        task=task,
        plan=plan,
        learned_skills=tuple(learned_skills),
        memory_key=memory_key,
        report_path=str(report_path),
    )
    report_path.write_text(_plan_to_markdown(result), encoding="utf-8")
    return result


def latest_training_context(*, limit: int = 5, db_path: Path = STATE_DB_PATH) -> str:
    memories = recall(scope="auto_training", limit=limit, db_path=db_path)
    if not memories:
        return "No hay ciclos de autoentrenamiento registrados."
    chunks = []
    for item in memories:
        chunks.append(f"- `{item.key}`: {item.value[:500]}")
    return "\n".join(chunks)


def record_user_session_training(
    user_goal: str,
    *,
    outcome: str,
    db_path: Path = STATE_DB_PATH,
) -> None:
    """Guarda una sesión real del usuario como entrenamiento incremental."""
    plan = build_training_plan(user_goal, db_path=db_path)
    primary_skill = plan.tools_to_use[0] if plan.tools_to_use else ""
    if primary_skill:
        record_skill_usage(
            primary_skill,
            user_goal,
            (
                "Sesión real de usuario: consultar la red entrenada antes de trabajar; "
                f"usar `{primary_skill}` si cubre la tarea; crear solo adaptador si falta conexión. "
                f"Tareas: {'; '.join(plan.tasks)}. Subtareas: {'; '.join(plan.subtasks)}."
            ),
            success=plan.decision == "REUSE_EXISTING_SKILL",
            outcome=outcome,
            db_path=db_path,
        )

    remember(
        "user_session_training",
        f"session_{int(time.time() * 1000)}",
        json.dumps(
            {
                "goal": user_goal,
                "outcome": outcome,
                "decision": plan.decision,
                "tools_to_use": plan.tools_to_use,
                "tasks": plan.tasks,
                "subtasks": plan.subtasks,
            },
            ensure_ascii=False,
        ),
        tags=["user_session", "runtime_learning", plan.decision],
        confidence=0.97,
        db_path=db_path,
    )
