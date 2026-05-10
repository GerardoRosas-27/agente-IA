from __future__ import annotations

from pathlib import Path

from harness.repo_index import (
    build_repo_graph,
    build_repo_index,
    localize_symbols,
    related_tests_for_files,
    repo_context_for_goal,
    search_callers,
    search_class,
    search_method,
    search_method_in_class,
    search_repo_index,
)


def test_repo_index_extracts_python_symbols(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    src.mkdir()
    (src / "demo_tool.py").write_text(
        "import json\n\nclass DemoTool:\n    def run(self):\n        return json.dumps({})\n",
        encoding="utf-8",
    )

    entries = build_repo_index(root=tmp_path)

    assert entries
    entry = entries[0]
    assert entry.path == "skills/demo_tool.py"
    assert "DemoTool" in entry.symbols
    assert "run" in entry.symbols
    assert "json" in entry.imports
    assert "dumps" in entry.references


def test_repo_index_search_returns_related_files(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def webhook_handler():\n    pass\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def calculator():\n    pass\n", encoding="utf-8")

    matches = search_repo_index("webhook messages", root=tmp_path)
    context = repo_context_for_goal("webhook messages", root=tmp_path)

    assert matches[0].path == "alpha.py"
    assert "alpha.py" in context


def test_repo_index_cache_reuses_unchanged_entries(tmp_path: Path, monkeypatch) -> None:
    cache_dir = tmp_path / "progress"
    monkeypatch.setattr("harness.repo_index.PROGRESS_DIR", cache_dir)
    source = tmp_path / "module.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    first = build_repo_index(root=tmp_path)
    second = build_repo_index(root=tmp_path)

    assert first == second
    assert list((cache_dir / "repo_index_cache").glob("*.json"))


def test_repo_graph_and_related_tests_use_symbols_and_imports(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    graph = build_repo_graph(root=tmp_path)
    related = related_tests_for_files(["src/calc.py"], root=tmp_path)

    assert "add" in graph.symbol_to_files
    assert related == ["tests/test_calc.py"]


def test_structural_search_apis(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "client.py").write_text(
        "class HttpClient:\n"
        "    def fetch(self):\n"
        "        return 1\n"
        "\n"
        "class StubClient:\n"
        "    def fetch(self):\n"
        "        return 2\n"
        "\n"
        "def standalone():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    (src / "consumer.py").write_text(
        "from pkg.client import HttpClient\n"
        "\n"
        "def use():\n"
        "    return HttpClient().fetch()\n",
        encoding="utf-8",
    )

    classes = search_class("HttpClient", root=tmp_path)
    methods = search_method("fetch", root=tmp_path)
    in_class = search_method_in_class("fetch", "StubClient", root=tmp_path)
    callers = search_callers("HttpClient", root=tmp_path)

    assert len(classes) == 1
    assert classes[0].path == "pkg/client.py"
    assert {m.symbol for m in methods} == {"fetch"}
    assert {(m.path, m.parent) for m in methods} == {
        ("pkg/client.py", "HttpClient"),
        ("pkg/client.py", "StubClient"),
    }
    assert len(in_class) == 1
    assert in_class[0].parent == "StubClient"
    assert any(c.path == "pkg/consumer.py" for c in callers)


def test_localize_symbols_returns_line_ranges(tmp_path: Path) -> None:
    (tmp_path / "webhook.py").write_text(
        "\n\nclass WebhookHandler:\n    def handle_webhook(self):\n        return True\n",
        encoding="utf-8",
    )

    matches = localize_symbols("handle webhook", root=tmp_path)

    assert matches
    assert matches[0].symbol == "handle_webhook"
    assert matches[0].start_line == 4
    assert matches[0].end_line >= matches[0].start_line
