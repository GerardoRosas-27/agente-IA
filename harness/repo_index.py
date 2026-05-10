"""Índice ligero del repositorio para contexto de agentes."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from harness.paths import REPO_ROOT


TOKEN_RE = re.compile(r"[a-z0-9_áéíóúñ]+", re.IGNORECASE)
INDEXABLE_SUFFIXES = {".py", ".md", ".json", ".txt"}
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}


@dataclass(frozen=True)
class FileIndexEntry:
    path: str
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    tokens: tuple[str, ...]
    summary: str


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in TOKEN_RE.findall(text):
        lowered = raw.lower()
        parts = [lowered, *lowered.split("_")]
        tokens.update(part for part in parts if len(part) >= 3)
    return tokens


def _python_symbols_and_imports(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return (), ()
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])
    return tuple(dict.fromkeys(symbols)), tuple(dict.fromkeys(imports))


def build_repo_index(
    *,
    root: Path = REPO_ROOT,
    max_file_chars: int = 2000,
) -> list[FileIndexEntry]:
    entries: list[FileIndexEntry] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in INDEXABLE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel.startswith(("logs/", "progress/history", "progress/current")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        symbols, imports = _python_symbols_and_imports(path) if path.suffix == ".py" else ((), ())
        token_source = f"{rel}\n{' '.join(symbols)}\n{' '.join(imports)}\n{text[:max_file_chars]}"
        summary = " ".join(line.strip() for line in text.splitlines()[:6] if line.strip())
        entries.append(
            FileIndexEntry(
                path=rel,
                symbols=symbols,
                imports=imports,
                tokens=tuple(sorted(tokenize(token_source))),
                summary=summary[:500],
            )
        )
    return entries


def search_repo_index(
    query: str,
    *,
    root: Path = REPO_ROOT,
    limit: int = 8,
) -> list[FileIndexEntry]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, FileIndexEntry]] = []
    for entry in build_repo_index(root=root):
        entry_tokens = set(entry.tokens)
        overlap = len(query_tokens & entry_tokens)
        if overlap <= 0:
            continue
        symbol_bonus = 0.4 * sum(1 for symbol in entry.symbols if symbol.lower() in query.lower())
        path_bonus = 0.3 * sum(1 for token in query_tokens if token in entry.path.lower())
        score = overlap + symbol_bonus + path_bonus
        scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1].path))
    return [entry for _score, entry in scored[: max(limit, 0)]]


def repo_context_for_goal(query: str, *, root: Path = REPO_ROOT, limit: int = 6) -> str:
    matches = search_repo_index(query, root=root, limit=limit)
    if not matches:
        return "No se encontraron archivos relacionados en el índice del repo."
    chunks: list[str] = []
    for entry in matches:
        symbols = ", ".join(entry.symbols[:12]) or "(sin símbolos)"
        imports = ", ".join(entry.imports[:8]) or "(sin imports)"
        chunks.append(
            f"- `{entry.path}`\n"
            f"  símbolos: {symbols}\n"
            f"  imports: {imports}\n"
            f"  resumen: {entry.summary or '(sin resumen)'}"
        )
    return "\n".join(chunks)
