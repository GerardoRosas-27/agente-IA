"""CLI del harness: validar lista de features, ejecutar un ciclo, expandir spec inicial."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from llm_api_client import resolve_llm_chat_for_pipeline

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
    return 0


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
