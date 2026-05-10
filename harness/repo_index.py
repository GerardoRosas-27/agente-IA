"""Índice ligero del repositorio para contexto de agentes."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.paths import PROGRESS_DIR, REPO_ROOT


TOKEN_RE = re.compile(r"[a-z0-9_áéíóúñ]+", re.IGNORECASE)
INDEXABLE_SUFFIXES = {".py", ".md", ".json", ".txt"}
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}


@dataclass(frozen=True)
class FileIndexEntry:
    path: str
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    references: tuple[str, ...]
    tokens: tuple[str, ...]
    summary: str
    mtime_ns: int = 0
    size: int = 0


@dataclass(frozen=True)
class SymbolLocation:
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    score: float = 0.0


@dataclass(frozen=True)
class RepoGraph:
    entries: tuple[FileIndexEntry, ...]
    symbol_to_files: dict[str, tuple[str, ...]]
    import_to_files: dict[str, tuple[str, ...]]


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in TOKEN_RE.findall(text):
        lowered = raw.lower()
        parts = [lowered, *lowered.split("_")]
        tokens.update(part for part in parts if len(part) >= 3)
    return tokens


def _python_static_facts(path: Path) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return (), (), ()
    symbols: list[str] = []
    imports: list[str] = []
    references: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Name):
            references.append(node.id)
        elif isinstance(node, ast.Attribute):
            references.append(node.attr)
    return tuple(dict.fromkeys(symbols)), tuple(dict.fromkeys(imports)), tuple(dict.fromkeys(references))


def _python_symbol_locations(path: Path, rel_path: str) -> tuple[SymbolLocation, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return ()
    locations: list[SymbolLocation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            locations.append(
                SymbolLocation(
                    path=rel_path,
                    symbol=node.name,
                    kind="class",
                    start_line=int(getattr(node, "lineno", 1)),
                    end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            locations.append(
                SymbolLocation(
                    path=rel_path,
                    symbol=node.name,
                    kind="function",
                    start_line=int(getattr(node, "lineno", 1)),
                    end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                )
            )
    return tuple(locations)


def _cache_path(root: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(root.resolve()))[-120:]
    return PROGRESS_DIR / "repo_index_cache" / f"{safe}.json"


def _entry_from_dict(data: dict) -> FileIndexEntry:
    return FileIndexEntry(
        path=str(data.get("path") or ""),
        symbols=tuple(data.get("symbols") or ()),
        imports=tuple(data.get("imports") or ()),
        references=tuple(data.get("references") or ()),
        tokens=tuple(data.get("tokens") or ()),
        summary=str(data.get("summary") or ""),
        mtime_ns=int(data.get("mtime_ns") or 0),
        size=int(data.get("size") or 0),
    )


def _load_cache(root: Path) -> dict[str, FileIndexEntry]:
    path = _cache_path(root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, list):
        return {}
    return {
        entry.path: entry
        for item in raw
        if isinstance(item, dict)
        for entry in [_entry_from_dict(item)]
        if entry.path
    }


def _save_cache(root: Path, entries: list[FileIndexEntry]) -> None:
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_repo_index(
    *,
    root: Path = REPO_ROOT,
    max_file_chars: int = 2000,
    use_cache: bool = True,
) -> list[FileIndexEntry]:
    entries: list[FileIndexEntry] = []
    cache = _load_cache(root) if use_cache else {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in INDEXABLE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel.startswith(("logs/", "progress/history", "progress/current", "progress/repo_index_cache/")):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = cache.get(rel)
        if cached and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            entries.append(cached)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        symbols, imports, references = _python_static_facts(path) if path.suffix == ".py" else ((), (), ())
        token_source = f"{rel}\n{' '.join(symbols)}\n{' '.join(imports)}\n{' '.join(references)}\n{text[:max_file_chars]}"
        summary = " ".join(line.strip() for line in text.splitlines()[:6] if line.strip())
        entries.append(
            FileIndexEntry(
                path=rel,
                symbols=symbols,
                imports=imports,
                references=references,
                tokens=tuple(sorted(tokenize(token_source))),
                summary=summary[:500],
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            )
        )
    if use_cache:
        _save_cache(root, entries)
    return entries


def build_repo_graph(*, root: Path = REPO_ROOT) -> RepoGraph:
    entries = tuple(build_repo_index(root=root))
    symbol_map: dict[str, list[str]] = {}
    import_map: dict[str, list[str]] = {}
    for entry in entries:
        for symbol in entry.symbols:
            symbol_map.setdefault(symbol, []).append(entry.path)
        for import_name in entry.imports:
            import_map.setdefault(import_name, []).append(entry.path)
    return RepoGraph(
        entries=entries,
        symbol_to_files={key: tuple(dict.fromkeys(value)) for key, value in symbol_map.items()},
        import_to_files={key: tuple(dict.fromkeys(value)) for key, value in import_map.items()},
    )


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
            f"  referencias: {', '.join(entry.references[:8]) or '(sin referencias)'}\n"
            f"  resumen: {entry.summary or '(sin resumen)'}"
        )
    return "\n".join(chunks)


def localize_symbols(query: str, *, root: Path = REPO_ROOT, limit: int = 10) -> list[SymbolLocation]:
    """Localiza clases/funciones/líneas candidatas para una tarea."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    results: list[SymbolLocation] = []
    for entry in search_repo_index(query, root=root, limit=max(limit * 2, 10)):
        path = root / entry.path
        for location in _python_symbol_locations(path, entry.path):
            symbol_tokens = tokenize(location.symbol)
            score = float(len(query_tokens & symbol_tokens))
            score += 0.25 * sum(1 for token in query_tokens if token in entry.path.lower())
            score += 0.1 * len(query_tokens & set(entry.tokens))
            if score > 0:
                results.append(
                    SymbolLocation(
                        path=location.path,
                        symbol=location.symbol,
                        kind=location.kind,
                        start_line=location.start_line,
                        end_line=location.end_line,
                        score=round(score, 4),
                    )
                )
    results.sort(key=lambda item: (-item.score, item.path, item.start_line))
    return results[: max(limit, 0)]


def related_tests_for_files(files: list[str], *, root: Path = REPO_ROOT) -> list[str]:
    """Relaciona módulos cambiados con tests por nombre, imports y referencias."""
    graph = build_repo_graph(root=root)
    entries_by_path = {entry.path: entry for entry in graph.entries}
    tests: list[str] = []
    changed = [path.replace("\\", "/") for path in files]
    changed_stems = {Path(path).stem for path in changed}

    for path in changed:
        if path.startswith("tests/") and path.endswith(".py"):
            tests.append(path)

    for entry in graph.entries:
        if not entry.path.startswith("tests/") or not entry.path.endswith(".py"):
            continue
        test_tokens = set(entry.tokens)
        if changed_stems & test_tokens:
            tests.append(entry.path)
            continue
        for changed_path in changed:
            changed_entry = entries_by_path.get(changed_path)
            if not changed_entry:
                continue
            module_stem = Path(changed_path).stem
            module_tokens = tokenize(module_stem)
            if module_stem in entry.imports or module_tokens & test_tokens:
                tests.append(entry.path)
                break
            if set(changed_entry.symbols) & set(entry.references):
                tests.append(entry.path)
                break
    return list(dict.fromkeys(tests))
