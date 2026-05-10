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
class SymbolLocation:
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    score: float = 0.0


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
    locations: tuple[SymbolLocation, ...] = ()


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


def _parse_python_facts(
    text: str, rel_path: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[SymbolLocation, ...]]:
    """Un único pase AST: extrae símbolos, imports, referencias y ubicaciones.

    Antes había dos funciones separadas (`_python_static_facts` y
    `_python_symbol_locations`) que parseaban el mismo archivo dos veces. Esta
    fusión reduce a la mitad el coste de indexación de cada archivo Python.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return (), (), (), ()
    symbols: list[str] = []
    imports: list[str] = []
    references: list[str] = []
    locations: list[SymbolLocation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
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
            symbols.append(node.name)
            locations.append(
                SymbolLocation(
                    path=rel_path,
                    symbol=node.name,
                    kind="function",
                    start_line=int(getattr(node, "lineno", 1)),
                    end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                )
            )
        elif isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Name):
            references.append(node.id)
        elif isinstance(node, ast.Attribute):
            references.append(node.attr)
    return (
        tuple(dict.fromkeys(symbols)),
        tuple(dict.fromkeys(imports)),
        tuple(dict.fromkeys(references)),
        tuple(locations),
    )


def _python_symbol_locations_for_entry(entry: FileIndexEntry, root: Path) -> tuple[SymbolLocation, ...]:
    """Obtiene locations para un entry, reparseando solo si no hay cache."""
    if entry.locations:
        return entry.locations
    if not entry.path.endswith(".py"):
        return ()
    full_path = root / entry.path
    try:
        text = full_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    _symbols, _imports, _references, locations = _parse_python_facts(text, entry.path)
    return locations


def _cache_path(root: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(root.resolve()))[-120:]
    return PROGRESS_DIR / "repo_index_cache" / f"{safe}.json"


def _location_from_dict(data: dict, fallback_path: str) -> SymbolLocation | None:
    try:
        return SymbolLocation(
            path=str(data.get("path") or fallback_path),
            symbol=str(data.get("symbol") or ""),
            kind=str(data.get("kind") or "function"),
            start_line=int(data.get("start_line") or 1),
            end_line=int(data.get("end_line") or data.get("start_line") or 1),
            score=float(data.get("score") or 0.0),
        )
    except (TypeError, ValueError):
        return None


def _entry_from_dict(data: dict) -> FileIndexEntry:
    rel_path = str(data.get("path") or "")
    raw_locations = data.get("locations") or ()
    if isinstance(raw_locations, (list, tuple)):
        parsed_locations = tuple(
            loc
            for item in raw_locations
            if isinstance(item, dict)
            for loc in [_location_from_dict(item, rel_path)]
            if loc is not None and loc.symbol
        )
    else:
        parsed_locations = ()
    return FileIndexEntry(
        path=rel_path,
        symbols=tuple(data.get("symbols") or ()),
        imports=tuple(data.get("imports") or ()),
        references=tuple(data.get("references") or ()),
        tokens=tuple(data.get("tokens") or ()),
        summary=str(data.get("summary") or ""),
        mtime_ns=int(data.get("mtime_ns") or 0),
        size=int(data.get("size") or 0),
        locations=parsed_locations,
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
        if path.suffix == ".py":
            symbols, imports, references, locations = _parse_python_facts(text, rel)
        else:
            symbols, imports, references, locations = (), (), (), ()
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
                locations=locations,
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
    query_lower = query.lower()
    scored: list[tuple[float, FileIndexEntry]] = []
    for entry in build_repo_index(root=root):
        entry_tokens = set(entry.tokens)
        overlap = len(query_tokens & entry_tokens)
        if overlap <= 0:
            continue
        symbol_bonus = 0.4 * sum(1 for symbol in entry.symbols if symbol.lower() in query_lower)
        path_lower = entry.path.lower()
        path_bonus = 0.3 * sum(1 for token in query_tokens if token in path_lower)
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
    """Localiza clases/funciones/líneas candidatas para una tarea.

    Reutiliza las locations ya capturadas en el índice cuando están disponibles
    para evitar reparsear cada archivo Python una segunda vez.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    results: list[SymbolLocation] = []
    for entry in search_repo_index(query, root=root, limit=max(limit * 2, 10)):
        entry_path_lower = entry.path.lower()
        path_bonus = 0.25 * sum(1 for token in query_tokens if token in entry_path_lower)
        token_bonus = 0.1 * len(query_tokens & set(entry.tokens))
        for location in _python_symbol_locations_for_entry(entry, root):
            symbol_tokens = tokenize(location.symbol)
            score = float(len(query_tokens & symbol_tokens)) + path_bonus + token_bonus
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


@dataclass(frozen=True)
class SymbolMatch:
    """Resultado de búsqueda estructural por nombre de símbolo (AutoCodeRover-style)."""

    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    parent: str = ""


def _iter_locations(root: Path) -> list[tuple[FileIndexEntry, SymbolLocation]]:
    """Aplana (entry, location) para iteración rápida."""
    pairs: list[tuple[FileIndexEntry, SymbolLocation]] = []
    for entry in build_repo_index(root=root):
        for location in _python_symbol_locations_for_entry(entry, root):
            pairs.append((entry, location))
    return pairs


def _class_ranges_for_entry(entry: FileIndexEntry, root: Path) -> list[tuple[str, int, int]]:
    """Devuelve (nombre_clase, start, end) para reconstruir parentesco método→clase."""
    locations = _python_symbol_locations_for_entry(entry, root)
    return [(loc.symbol, loc.start_line, loc.end_line) for loc in locations if loc.kind == "class"]


def search_class(class_name: str, *, root: Path = REPO_ROOT, limit: int = 20) -> list[SymbolMatch]:
    """Encuentra clases con nombre exacto (case-insensitive)."""
    target = class_name.strip()
    if not target:
        return []
    target_lower = target.lower()
    results: list[SymbolMatch] = []
    for entry, location in _iter_locations(root):
        if location.kind != "class":
            continue
        if location.symbol == target or location.symbol.lower() == target_lower:
            results.append(
                SymbolMatch(
                    path=entry.path,
                    symbol=location.symbol,
                    kind="class",
                    start_line=location.start_line,
                    end_line=location.end_line,
                )
            )
    return results[: max(limit, 0)]


def search_method(method_name: str, *, root: Path = REPO_ROOT, limit: int = 50) -> list[SymbolMatch]:
    """Encuentra funciones/métodos con nombre exacto. Anota la clase contenedora si aplica."""
    target = method_name.strip()
    if not target:
        return []
    target_lower = target.lower()
    results: list[SymbolMatch] = []
    for entry in build_repo_index(root=root):
        locations = _python_symbol_locations_for_entry(entry, root)
        if not locations:
            continue
        class_ranges = [
            (loc.symbol, loc.start_line, loc.end_line) for loc in locations if loc.kind == "class"
        ]
        for location in locations:
            if location.kind not in {"function"}:
                continue
            if location.symbol != target and location.symbol.lower() != target_lower:
                continue
            parent = ""
            for class_name, start, end in class_ranges:
                if start <= location.start_line <= end:
                    parent = class_name
                    break
            results.append(
                SymbolMatch(
                    path=entry.path,
                    symbol=location.symbol,
                    kind="method" if parent else "function",
                    start_line=location.start_line,
                    end_line=location.end_line,
                    parent=parent,
                )
            )
    return results[: max(limit, 0)]


def search_method_in_class(
    method_name: str,
    class_name: str,
    *,
    root: Path = REPO_ROOT,
    limit: int = 20,
) -> list[SymbolMatch]:
    """Encuentra un método dentro de una clase específica."""
    class_target = class_name.strip().lower()
    if not class_target:
        return []
    matches = search_method(method_name, root=root, limit=max(limit * 4, 50))
    return [m for m in matches if m.parent.lower() == class_target][: max(limit, 0)]


def search_callers(symbol: str, *, root: Path = REPO_ROOT, limit: int = 50) -> list[FileIndexEntry]:
    """Lista archivos que referencian un símbolo (heurístico: aparece en references)."""
    target = symbol.strip()
    if not target:
        return []
    results: list[FileIndexEntry] = []
    for entry in build_repo_index(root=root):
        if target in entry.references or target in entry.imports:
            results.append(entry)
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

    # Precomputa información de los archivos cambiados una sola vez para evitar
    # rehacer `set(...)`/`tokenize(...)` por cada test del repositorio.
    changed_info: list[tuple[str, str, set[str], set[str]]] = []
    for changed_path in changed:
        changed_entry = entries_by_path.get(changed_path)
        if not changed_entry:
            continue
        module_stem = Path(changed_path).stem
        module_tokens = tokenize(module_stem)
        symbol_set = set(changed_entry.symbols)
        changed_info.append((changed_path, module_stem, module_tokens, symbol_set))

    for entry in graph.entries:
        if not entry.path.startswith("tests/") or not entry.path.endswith(".py"):
            continue
        test_tokens = set(entry.tokens)
        if changed_stems & test_tokens:
            tests.append(entry.path)
            continue
        test_imports = set(entry.imports)
        test_references = set(entry.references)
        for _changed_path, module_stem, module_tokens, symbol_set in changed_info:
            if module_stem in test_imports or module_tokens & test_tokens:
                tests.append(entry.path)
                break
            if symbol_set & test_references:
                tests.append(entry.path)
                break
    return list(dict.fromkeys(tests))
