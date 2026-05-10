"""Mutation testing local sobre archivos modificados.

Idea: tras aceptar un patch, aplicar mutaciones simples (cambiar `==` por
`!=`, `True` por `False`, `+` por `-`, etc.) en las líneas modificadas y
verificar que **al menos un test pasa de PASS a FAIL** por cada mutación.
Si una mutación sobrevive (todos los tests siguen pasando) → los tests no
ejercen esa lógica = tests fantasma.

Esta es de las pocas señales objetivas para distinguir "los tests pasan"
de "los tests realmente cubren la feature". Útil como check adicional en
auto-mejora (id 19) y para detectar BRTs que el LLM falsamente marca como
fail-to-pass pero en realidad son triviales.

Inspirado en `mutpy` y la línea de papers de mutation analysis aplicada a
LLM-generated tests (e.g., "Test the Tester", PLDI/ASE 2023+).

Limitaciones conscientes:
- Solo Python (basado en AST).
- Mutaciones simples (operadores binarios, constantes booleanas, return).
- Una mutación a la vez; no exhaustivo, sino muestral con `max_mutations`.
- No paraleliza tests; con suite grande conviene apuntarlo a un subset.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Mutation:
    file: str
    line: int
    description: str


@dataclass(frozen=True)
class MutationOutcome:
    mutation: Mutation
    killed: bool
    detail: str


@dataclass
class MutationReport:
    file: str
    total: int = 0
    killed: int = 0
    survivors: list[MutationOutcome] = field(default_factory=list)
    outcomes: list[MutationOutcome] = field(default_factory=list)

    @property
    def mutation_score(self) -> float:
        if self.total <= 0:
            return 1.0
        return self.killed / self.total

    @property
    def has_survivors(self) -> bool:
        return bool(self.survivors)


_BOOL_FLIP = {True: False, False: True}
_OP_FLIPS = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.And: ast.Or,
    ast.Or: ast.And,
}


def _enumerate_mutations(source: str) -> list[tuple[Mutation, str]]:
    """Devuelve [(metadata, source mutado)] para cada mutación enumerada.

    Estrategia: parsear AST, recorrer nodos mutables, regenerar el fuente con
    `ast.unparse`. Mantenemos un solo cambio por mutación.
    """
    mutations: list[tuple[Mutation, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return mutations

    def _try_mutation(make_mutated: object, description: str, line: int) -> None:
        # Reparsear desde cero por cada mutación para evitar contaminar nodos.
        try:
            local_tree = ast.parse(source)
        except SyntaxError:
            return
        if not make_mutated(local_tree):  # type: ignore[misc]
            return
        try:
            mutated_source = ast.unparse(local_tree)
        except (AttributeError, ValueError):
            return
        if mutated_source == source:
            return
        mutations.append(
            (
                Mutation(
                    file="<filled>",
                    line=line,
                    description=description,
                ),
                mutated_source,
            )
        )

    walk_targets: list[ast.AST] = list(ast.walk(tree))
    for target_idx, node in enumerate(walk_targets):
        line = getattr(node, "lineno", 0) or 0
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            target_index = target_idx

            def flip_bool(local_tree: ast.AST, _idx: int = target_index) -> bool:
                local_nodes = list(ast.walk(local_tree))
                if _idx >= len(local_nodes):
                    return False
                local = local_nodes[_idx]
                if isinstance(local, ast.Constant) and isinstance(local.value, bool):
                    local.value = _BOOL_FLIP[local.value]
                    return True
                return False

            _try_mutation(flip_bool, f"flip bool {node.value} ↔ {_BOOL_FLIP[node.value]}", line)
        if isinstance(node, ast.Compare) and node.ops:
            for op_index, op in enumerate(node.ops):
                op_cls = type(op)
                if op_cls not in _OP_FLIPS:
                    continue
                target_index = target_idx
                slot = op_index
                replacement_cls = _OP_FLIPS[op_cls]

                def flip_compare(local_tree: ast.AST, _idx: int = target_index, _slot: int = slot, _new: object = replacement_cls) -> bool:
                    local_nodes = list(ast.walk(local_tree))
                    if _idx >= len(local_nodes):
                        return False
                    local = local_nodes[_idx]
                    if isinstance(local, ast.Compare) and _slot < len(local.ops):
                        local.ops[_slot] = _new()  # type: ignore[operator]
                        return True
                    return False

                _try_mutation(flip_compare, f"flip compare op {op_cls.__name__}", line)
        if isinstance(node, ast.BinOp):
            op_cls = type(node.op)
            if op_cls in _OP_FLIPS and not issubclass(op_cls, (ast.And, ast.Or)):
                target_index = target_idx
                replacement_cls = _OP_FLIPS[op_cls]

                def flip_binop(local_tree: ast.AST, _idx: int = target_index, _new: object = replacement_cls) -> bool:
                    local_nodes = list(ast.walk(local_tree))
                    if _idx >= len(local_nodes):
                        return False
                    local = local_nodes[_idx]
                    if isinstance(local, ast.BinOp):
                        local.op = _new()  # type: ignore[operator]
                        return True
                    return False

                _try_mutation(flip_binop, f"flip binop {op_cls.__name__}", line)
        if isinstance(node, ast.Return) and node.value is not None:
            target_index = target_idx

            def to_none(local_tree: ast.AST, _idx: int = target_index) -> bool:
                local_nodes = list(ast.walk(local_tree))
                if _idx >= len(local_nodes):
                    return False
                local = local_nodes[_idx]
                if isinstance(local, ast.Return):
                    local.value = ast.Constant(value=None)
                    return True
                return False

            _try_mutation(to_none, "return X → return None", line)
    return mutations


def _run_pytest_focus(test_paths: list[str], *, root: Path, timeout: float = 60) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", *test_paths, "-q", "--tb=line", "--no-header"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"timeout: {exc}"
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, output


def run_mutation_test(
    target_file: str,
    *,
    test_paths: list[str],
    root: Path,
    max_mutations: int = 8,
    timeout_per_run: float = 30,
) -> MutationReport:
    """Ejecuta mutaciones contra `target_file` y mide cuántas matan los tests.

    `max_mutations`: límite muestral (un archivo grande puede tener decenas de
    mutaciones; con 8 ya se detecta razonablemente si los tests son fantasma).
    """
    report = MutationReport(file=target_file)
    full_path = root / target_file
    if not full_path.is_file() or not target_file.endswith(".py"):
        return report
    if not test_paths:
        return report

    original_source = full_path.read_text(encoding="utf-8", errors="ignore")
    mutations = _enumerate_mutations(original_source)
    if not mutations:
        return report

    sample = mutations[:max_mutations]
    backup_path = full_path.with_suffix(full_path.suffix + ".mut_bak")
    shutil.copyfile(full_path, backup_path)

    try:
        for meta, mutated_source in sample:
            full_path.write_text(mutated_source, encoding="utf-8")
            tests_pass, output = _run_pytest_focus(test_paths, root=root, timeout=timeout_per_run)
            killed = not tests_pass
            mutation = Mutation(
                file=target_file,
                line=meta.line,
                description=meta.description,
            )
            outcome = MutationOutcome(
                mutation=mutation,
                killed=killed,
                detail=output[-1500:] if output else "",
            )
            report.outcomes.append(outcome)
            if killed:
                report.killed += 1
            else:
                report.survivors.append(outcome)
            report.total += 1
    finally:
        # Restaurar siempre el archivo original.
        shutil.copyfile(backup_path, full_path)
        try:
            backup_path.unlink()
        except OSError:
            pass

    return report
