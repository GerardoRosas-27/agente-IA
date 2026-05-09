"""CLI del harness: validar lista de features, ejecutar un ciclo, expandir spec inicial."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from llm_api_client import resolve_llm_chat_for_pipeline

from harness.auto_training import latest_training_context, run_training_cycle
from harness.feature_store import (
    load_feature_list,
    validate_feature_list,
)
from harness.orchestrator import expand_features_from_goal, run_one_feature_cycle
from harness.paths import FEATURE_LIST_PATH
from harness.shared_memory import (
    self_improvement_context,
    shared_memory_context,
    skill_memory_context,
)
from harness.tool_learning import internal_execution_context, learned_tools_context, learn_tool_outcome
from harness.training_dataset import generate_tool_routing_dataset, generate_user_task_dataset, train_from_dataset


def _cmd_init(_args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"]
    print(" ", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(root))
    return int(r.returncode)


def _cmd_validate(_args: argparse.Namespace) -> int:
    data = load_feature_list(FEATURE_LIST_PATH)
    errs = validate_feature_list(data)
    if errs:
        print("ERRORES:")
        for e in errs:
            print(" ", e)
        return 1
    print("feature_list.json OK")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    data = load_feature_list(FEATURE_LIST_PATH)
    feats = data.get("features") or []
    from collections import Counter

    c = Counter(str(f.get("status")) for f in feats if isinstance(f, dict))
    for k in ("pending", "in_progress", "done", "blocked"):
        print(f"  {k}: {c.get(k, 0)}")
    return 0


def _cmd_memory(args: argparse.Namespace) -> int:
    print("Memoria compartida:")
    print(shared_memory_context(query=args.query or "", limit=args.limit))
    print("\nAprendizajes de skills:")
    print(skill_memory_context(limit=args.limit))
    print("\nAuto-mejoras pendientes:")
    print(self_improvement_context(limit=args.limit))
    print("\nAutoentrenamientos recientes:")
    print(latest_training_context(limit=args.limit))
    return 0


def _cmd_tools(args: argparse.Namespace) -> int:
    if args.learn:
        if not args.skill or not args.use_case:
            print("ERROR: --skill y --use-case son obligatorios con --learn")
            return 1
        learn_tool_outcome(
            args.skill,
            args.use_case,
            args.instructions or "",
            success=not args.failure,
            outcome=args.outcome or ("éxito registrado" if not args.failure else "fallo registrado"),
        )
        print(f"Aprendizaje registrado para skill `{args.skill}`.")
        return 0

    if args.internal_context:
        print(internal_execution_context(args.goal or ""))
    else:
        print(learned_tools_context(args.goal or "", limit=args.limit))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    result = run_training_cycle(args.goal)
    print(f"Tarea creada: {result.task.task_id}")
    print(f"Decisión: {result.plan.decision}")
    print(f"Herramientas: {', '.join(result.plan.tools_to_use) if result.plan.tools_to_use else '(ninguna)'}")
    print(f"Reporte: {result.report_path}")
    print("\nTareas:")
    for task in result.plan.tasks:
        print(f"  - {task}")
    print("\nSubtareas:")
    for subtask in result.plan.subtasks:
        print(f"  - {subtask}")
    print("\nSolución de código sugerida:")
    print(result.plan.code_solution)
    return 0


def _cmd_dataset(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else None
    if args.action == "generate":
        if path is None:
            stats = generate_tool_routing_dataset(target_tokens=args.target_tokens, seed=args.seed)
        else:
            stats = generate_tool_routing_dataset(
                output_path=path,
                target_tokens=args.target_tokens,
                seed=args.seed,
            )
        print(f"Dataset: {stats.path}")
        print(f"Ejemplos: {stats.examples}")
        print(f"Tokens aproximados: {stats.approx_tokens}")
        return 0

    if args.action == "generate-user-tasks":
        if path is None:
            stats = generate_user_task_dataset(examples=args.examples, seed=args.seed)
        else:
            stats = generate_user_task_dataset(output_path=path, examples=args.examples, seed=args.seed)
        print(f"Dataset: {stats.path}")
        print(f"Ejemplos: {stats.examples}")
        print(f"Tokens aproximados: {stats.approx_tokens}")
        return 0

    if args.action == "train":
        if path is None:
            stats = train_from_dataset(max_examples=args.max_examples)
        else:
            stats = train_from_dataset(dataset_path=path, max_examples=args.max_examples)
        print(f"Dataset entrenado: {stats.path}")
        print(f"Ejemplos disponibles: {stats.examples}")
        print(f"Ejemplos entrenados: {stats.trained_examples}")
        print(f"Skills entrenadas: {', '.join(stats.trained_skills)}")
        return 0

    print("ERROR: acción inválida. Usa generate o train.")
    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "")
    print(f"LLM: {label} · modelo «{model}»")

    def log(m: str) -> None:
        print(m)

    try:
        res = run_one_feature_cycle(
            model=model,
            llm_chat=llm_chat,
            num_predict_leader=args.num_predict_leader,
            num_predict_worker=args.num_predict_worker,
            on_log=log,
        )
    except Exception as exc:
        print("ERROR:", exc)
        return 1
    if res is None:
        print("Nada que ejecutar.")
        return 0
    print(res.message)
    print("Impl:", res.impl_path)
    print("Review:", res.review_path)
    return 0


def _cmd_expand(args: argparse.Namespace) -> int:
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "")
    print(f"LLM: {label} · modelo «{model}»")
    try:
        n = expand_features_from_goal(
            args.goal,
            model=model,
            llm_chat=llm_chat,
            num_predict=args.num_predict,
        )
    except Exception as exc:
        print("ERROR:", exc)
        return 1
    print(f"Añadidas {n} features en feature_list.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Harness LM Studio: líder → implementador → revisor (artefactos en disco)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s_init = sub.add_parser("init", help="Ejecuta pytest (verificación del repo)")
    s_init.set_defaults(func=_cmd_init)

    s_val = sub.add_parser("validate", help="Valida reglas de feature_list.json")
    s_val.set_defaults(func=_cmd_validate)

    s_st = sub.add_parser("status", help="Resumen de estados en feature_list.json")
    s_st.set_defaults(func=_cmd_status)

    s_mem = sub.add_parser("memory", help="Muestra memoria compartida y aprendizajes")
    s_mem.add_argument("--query", default="", help="Filtro textual opcional")
    s_mem.add_argument("--limit", type=int, default=10)
    s_mem.set_defaults(func=_cmd_memory)

    s_tools = sub.add_parser("tools", help="Recomienda o registra uso aprendido de skills")
    s_tools.add_argument("goal", nargs="?", default="", help="Objetivo para recomendar herramientas existentes")
    s_tools.add_argument("--limit", type=int, default=3)
    s_tools.add_argument("--learn", action="store_true", help="Registra feedback de uso de una skill")
    s_tools.add_argument("--skill", default="", help="Nombre de la skill usada")
    s_tools.add_argument("--use-case", default="", help="Caso de uso aprendido")
    s_tools.add_argument("--instructions", default="", help="Cómo se invoca o usa la skill")
    s_tools.add_argument("--outcome", default="", help="Resultado observado")
    s_tools.add_argument("--failure", action="store_true", help="Marca el aprendizaje como fallo")
    s_tools.add_argument("--internal-context", action="store_true", help="Muestra plan interno de reutilización/creación")
    s_tools.set_defaults(func=_cmd_tools)

    s_train = sub.add_parser("auto-train", help="Ejecuta un ciclo de autoaprendizaje en runtime")
    s_train.add_argument("goal", help="Objetivo/tarea de entrenamiento")
    s_train.set_defaults(func=_cmd_train)

    s_dataset = sub.add_parser("training-dataset", help="Genera/entrena dataset sintético de tareas")
    s_dataset.add_argument("action", choices=["generate", "generate-user-tasks", "train"])
    s_dataset.add_argument("--path", default="", help="Ruta opcional del JSONL")
    s_dataset.add_argument("--target-tokens", type=int, default=2_000_000)
    s_dataset.add_argument("--examples", type=int, default=20_000)
    s_dataset.add_argument("--seed", type=int, default=17)
    s_dataset.add_argument("--max-examples", type=int, default=None)
    s_dataset.set_defaults(func=_cmd_dataset)

    s_run = sub.add_parser("run", help="Un ciclo sobre la siguiente feature (o la in_progress)")
    s_run.add_argument(
        "--llm-model",
        default="",
        help="Sobrescribe LLM_MODEL del .env",
    )
    s_run.add_argument("--num-predict-leader", type=int, default=1200)
    s_run.add_argument("--num-predict-worker", type=int, default=2800)
    s_run.set_defaults(func=_cmd_run)

    s_ex = sub.add_parser(
        "expand",
        help="Inicializador: añade features JSON desde un objetivo en lenguaje natural",
    )
    s_ex.add_argument("goal", help="Texto del producto / alcance")
    s_ex.add_argument("--llm-model", default="", help="Sobrescribe LLM_MODEL del .env")
    s_ex.add_argument("--num-predict", type=int, default=4000)
    s_ex.set_defaults(func=_cmd_expand)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
