"""CLI del harness: validar lista de features, ejecutar un ciclo, expandir spec inicial."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from llm_api_client import resolve_llm_chat_for_pipeline

from harness.agent_loop import load_agent_session, run_agent_loop, save_agent_session
from harness.benchmark_tasks import run_code_benchmark_tasks
from harness.benchmarks import benchmark_summary, run_benchmarks
from harness.best_of_n import choose_best_patch
from harness.coding_pipeline import run_localize_patch_validate
from harness.evaluator import evaluate_changes
from harness.auto_training import consolidate_runtime_learning, latest_training_context, run_training_cycle
from harness.feature_store import (
    load_feature_list,
    validate_feature_list,
)
from harness.orchestrator import expand_features_from_goal, run_one_feature_cycle
from harness.llm import invoke_llm
from harness.multiagent_contracts import contracts_context
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


def _cmd_learn(args: argparse.Namespace) -> int:
    print(consolidate_runtime_learning(limit=args.limit))
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


def _cmd_code_benchmark(args: argparse.Namespace) -> int:
    results = run_code_benchmark_tasks(root=REPO_ROOT, tasks_dir=Path(args.tasks_dir))
    if not results:
        print("No hay benchmark tasks.")
        return 0
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.task_id}\n{result.report[: args.max_output]}\n")
    return 0 if all(result.passed for result in results) else 1


def _cmd_pipeline(args: argparse.Namespace) -> int:
    patch_text = Path(args.patch).read_text(encoding="utf-8") if args.patch else None
    result = run_localize_patch_validate(
        args.goal,
        root=REPO_ROOT,
        patch_text=patch_text,
        extra_commands=args.command or [],
        trajectory_id=args.trajectory or "",
    )
    print(result.localization.rationale)
    if result.validation:
        print(result.validation.report[: args.max_output])
    print("Veredicto:", "PASS" if result.ok else result.trajectory.verdict)
    return 0 if result.ok or not patch_text else 1


def _cmd_best_patch(args: argparse.Namespace) -> int:
    patches = [Path(path).read_text(encoding="utf-8") for path in args.patches]
    best = choose_best_patch(patches, root=REPO_ROOT, extra_commands=args.command or [])
    if best is None:
        print("No hay patches candidatos.")
        return 1
    print(f"Mejor patch: #{best.index} ok={best.ok} score={best.score}")
    print(best.report[: args.max_output])
    return 0 if best.ok else 1


def _cmd_contracts(_args: argparse.Namespace) -> int:
    print(contracts_context())
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "", args.llm_profile or "")
    print(f"LLM: {label} - modelo {model}")

    initial_state = None
    goal = args.goal or ""
    if args.resume:
        initial_state = load_agent_session(args.resume)
        goal = goal or initial_state.goal
        print(f"Reanudando sesión: {initial_state.session_id}")
    if not goal:
        print("ERROR: indica un objetivo o usa --resume")
        return 1

    session_id = args.session or (initial_state.session_id if initial_state else "")

    def llm_call(system: str, prompt: str) -> str:
        return invoke_llm(
            model,
            system,
            prompt,
            llm_chat=llm_chat,
            num_predict=args.num_predict,
            temperature=args.temperature,
            role_hint="agent_loop",
        )

    def on_event(event) -> None:
        status = "OK" if event.ok else "FAIL"
        print(f"\n[{status}] {event.action}\n{event.content[: args.max_output]}")

    state = run_agent_loop(
        goal,
        llm_call=llm_call,
        max_steps=args.max_steps,
        on_event=on_event,
        initial_state=initial_state,
        session_id=session_id,
        autosave=True,
    )
    path = save_agent_session(state)
    print(f"\nSesión: {path}")
    print("Estado:", "done" if state.done else "incomplete")
    return 0 if state.done or args.allow_incomplete else 1


def _cmd_run(args: argparse.Namespace) -> int:
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "", args.llm_profile or "")
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
    llm_chat, model, label = resolve_llm_chat_for_pipeline(args.llm_model or "", args.llm_profile or "")
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

    s_learn = sub.add_parser("learn", help="Consolida aprendizaje desde uso real del agente")
    s_learn.add_argument("--limit", type=int, default=500)
    s_learn.set_defaults(func=_cmd_learn)

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

    s_code_bench = sub.add_parser("code-benchmark", help="Ejecuta benchmark tasks estilo SWE-bench")
    s_code_bench.add_argument("--tasks-dir", default=str(PROGRESS_DIR / "benchmark_tasks"))
    s_code_bench.add_argument("--max-output", type=int, default=3000)
    s_code_bench.set_defaults(func=_cmd_code_benchmark)

    s_pipeline = sub.add_parser("pipeline", help="Ejecuta localize -> patch -> validate")
    s_pipeline.add_argument("goal", help="Objetivo o issue")
    s_pipeline.add_argument("--patch", default="", help="Archivo con diff unificado para aplicar y validar")
    s_pipeline.add_argument("--command", action="append", default=[], help="Comando adicional permitido")
    s_pipeline.add_argument("--trajectory", default="", help="ID de trayectoria")
    s_pipeline.add_argument("--max-output", type=int, default=5000)
    s_pipeline.set_defaults(func=_cmd_pipeline)

    s_best = sub.add_parser("best-patch", help="Evalúa patches candidatos en worktrees aislados")
    s_best.add_argument("patches", nargs="+", help="Archivos .patch candidatos")
    s_best.add_argument("--command", action="append", default=[], help="Comando adicional permitido")
    s_best.add_argument("--max-output", type=int, default=5000)
    s_best.set_defaults(func=_cmd_best_patch)

    s_contracts = sub.add_parser("contracts", help="Muestra contratos SOP multiagente")
    s_contracts.set_defaults(func=_cmd_contracts)

    s_agent = sub.add_parser("agent", help="Ejecuta un agente interactivo con herramientas controladas")
    s_agent.add_argument("goal", nargs="?", default="", help="Objetivo de trabajo del agente")
    s_agent.add_argument("--resume", default="", help="ID de sesión para reanudar")
    s_agent.add_argument("--session", default="", help="ID de sesión para guardar progreso")
    s_agent.add_argument("--llm-model", default="", help="Sobrescribe modelo del perfil LLM")
    s_agent.add_argument("--llm-profile", default="", help="Perfil LLM: default, deepseek, deepseek_v4 u otro")
    s_agent.add_argument("--max-steps", type=int, default=8)
    s_agent.add_argument("--num-predict", type=int, default=1800)
    s_agent.add_argument("--temperature", type=float, default=0.2)
    s_agent.add_argument("--max-output", type=int, default=4000)
    s_agent.add_argument("--allow-incomplete", action="store_true", help="Exit 0 aunque no llegue a done")
    s_agent.set_defaults(func=_cmd_agent)

    s_run = sub.add_parser("run", help="Un ciclo sobre la siguiente feature (o la in_progress)")
    s_run.add_argument(
        "--llm-model",
        default="",
        help="Sobrescribe LLM_MODEL del .env",
    )
    s_run.add_argument("--llm-profile", default="", help="Perfil LLM: default, deepseek, deepseek_v4 u otro")
    s_run.add_argument("--num-predict-leader", type=int, default=1200)
    s_run.add_argument("--num-predict-worker", type=int, default=2800)
    s_run.set_defaults(func=_cmd_run)

    s_ex = sub.add_parser(
        "expand",
        help="Inicializador: anade features JSON desde un objetivo en lenguaje natural",
    )
    s_ex.add_argument("goal", help="Texto del producto / alcance")
    s_ex.add_argument("--llm-model", default="", help="Sobrescribe LLM_MODEL del .env")
    s_ex.add_argument("--llm-profile", default="", help="Perfil LLM: default, deepseek, deepseek_v4 u otro")
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
