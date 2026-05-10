"""CLI del harness: validar lista de features, ejecutar un ciclo, expandir spec inicial."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from llm_api_client import resolve_llm_chat_for_pipeline

from harness.benchmarks import benchmark_summary, run_benchmarks
from harness.evaluator import evaluate_changes
from harness.auto_training import latest_training_context, run_training_cycle
from harness.feature_store import (
    load_feature_list,
    validate_feature_list,
)
from harness.orchestrator import expand_features_from_goal, run_one_feature_cycle
from harness.paths import FEATURE_LIST_PATH
from harness.paths import PROGRESS_DIR, REPO_ROOT
from harness.repo_index import repo_context_for_goal
from harness.shared_memory import (
    self_improvement_context,
    shared_memory_context,
    skill_memory_context,
)
from harness.tool_learning import internal_execution_context, learned_tools_context, learn_tool_outcome


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
            outcome=args.outcome or ("exito registrado" if not args.failure else "fallo registrado"),
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
    print(f"Decision: {result.plan.decision}")
    print(f"Herramientas: {', '.join(result.plan.tools_to_use) if result.plan.tools_to_use else '(ninguna)'}")
    print(f"Reporte: {result.report_path}")
    print("\nTareas:")
    for task in result.plan.tasks:
        print(f"  - {task}")
    print("\nSubtareas:")
    for subtask in result.plan.subtasks:
        print(f"  - {subtask}")
    print("\nSolucion de codigo sugerida:")
    print(result.plan.code_solution)
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    print(repo_context_for_goal(args.query, root=REPO_ROOT, limit=args.limit))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    result = evaluate_changes(args.files or [], commands=args.command or [], root=REPO_ROOT)
    print(result.report)
    return 0 if result.ok else 1


def _cmd_benchmark(args: argparse.Namespace) -> int:
    output = Path(args.output) if args.output else PROGRESS_DIR / "benchmarks" / "latest.json"
    results = run_benchmarks(root=REPO_ROOT, output_path=output)
    print(benchmark_summary(results))
    print(f"Reporte: {output}")
    return 0 if all(result.passed for result in results) else 1


def _cmd_run(args: argparse.Namespace) -> int:
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "")
    print(f"LLM: {label} - modelo {model}")

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
    print(f"LLM: {label} - modelo {model}")
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
    print(f"Anadidas {n} features en feature_list.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Harness LM Studio: lider -> implementador -> revisor (artefactos en disco)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s_init = sub.add_parser("init", help="Ejecuta pytest (verificacion del repo)")
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
    s_tools.add_argument("--instructions", default="", help="Como se invoca o usa la skill")
    s_tools.add_argument("--outcome", default="", help="Resultado observado")
    s_tools.add_argument("--failure", action="store_true", help="Marca el aprendizaje como fallo")
    s_tools.add_argument("--internal-context", action="store_true", help="Muestra plan interno de reutilizacion/creacion")
    s_tools.set_defaults(func=_cmd_tools)

    s_train = sub.add_parser("auto-train", help="Ejecuta un ciclo de autoaprendizaje en runtime")
    s_train.add_argument("goal", help="Objetivo/tarea de entrenamiento")
    s_train.set_defaults(func=_cmd_train)

    s_index = sub.add_parser("index", help="Busca contexto relevante en el indice del repo")
    s_index.add_argument("query", help="Consulta para buscar en simbolos, imports y texto")
    s_index.add_argument("--limit", type=int, default=8)
    s_index.set_defaults(func=_cmd_index)

    s_eval = sub.add_parser("evaluate", help="Evalua cambios con checks objetivos")
    s_eval.add_argument("files", nargs="*", help="Archivos cambiados para compilar/testear")
    s_eval.add_argument("--command", action="append", default=[], help="Comando adicional permitido")
    s_eval.set_defaults(func=_cmd_evaluate)

    s_bench = sub.add_parser("benchmark", help="Ejecuta benchmarks locales del harness")
    s_bench.add_argument("--output", default="", help="Ruta JSON para guardar resultados")
    s_bench.set_defaults(func=_cmd_benchmark)

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
        help="Inicializador: anade features JSON desde un objetivo en lenguaje natural",
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
