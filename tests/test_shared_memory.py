from pathlib import Path

from harness.shared_memory import (
    add_self_improvement,
    list_self_improvements,
    list_usage_events,
    recall,
    record_usage_event,
    record_skill_usage,
    remember,
    self_improvement_context,
    semantic_recall,
    shared_memory_context,
    skill_memory_context,
)


def test_remember_and_recall_shared_memory(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"

    remember(
        "skill",
        "whatsapp_connector",
        "Usar webhook para entrada y Cloud API para respuesta.",
        tags=["whatsapp", "skill"],
        db_path=db_path,
    )

    items = recall(scope="skill", query="webhook", db_path=db_path)
    assert len(items) == 1
    assert items[0].key == "whatsapp_connector"
    assert "Cloud API" in shared_memory_context(query="whatsapp", db_path=db_path)


def test_skill_usage_context_accumulates_success_and_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"

    record_skill_usage(
        "web_search",
        "buscar documentación",
        "Llamar search_web(query)",
        success=True,
        outcome="ok",
        db_path=db_path,
    )
    record_skill_usage(
        "web_search",
        "buscar documentación",
        "Llamar search_web(query)",
        success=False,
        outcome="timeout",
        db_path=db_path,
    )

    context = skill_memory_context(db_path=db_path)
    assert "web_search" in context
    assert "éxitos=1" in context
    assert "fallos=1" in context


def test_self_improvement_context(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"

    add_self_improvement(
        "tests",
        "Crear tests antes de implementar nuevas skills.",
        evidence="fallo previo",
        db_path=db_path,
    )

    context = self_improvement_context(db_path=db_path)
    assert "Crear tests" in context
    assert "fallo previo" in context
    items = list_self_improvements(db_path=db_path)
    assert len(items) == 1
    assert items[0].status == "pending"


def test_semantic_recall_finds_token_overlap_without_exact_phrase(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    remember(
        "architecture",
        "repo_index",
        "El índice extrae símbolos de Python e imports para contexto del agente.",
        tags=["symbols", "imports"],
        db_path=db_path,
    )
    remember(
        "other",
        "whatsapp",
        "Conector de mensajes entrantes.",
        db_path=db_path,
    )

    items = semantic_recall(query="buscar imports y simbolos del codigo", db_path=db_path)

    assert items
    assert items[0].key == "repo_index"


def test_usage_events_are_recorded_and_consolidated(tmp_path: Path) -> None:
    from harness.shared_memory import consolidate_usage_events

    db_path = tmp_path / "memory.db"
    record_usage_event(
        "agent_observation",
        "repo_index",
        "buscar contexto",
        "search",
        success=True,
        score=1.0,
        evidence="encontró archivos relevantes",
        db_path=db_path,
    )

    events = list_usage_events(event_type="agent_observation", db_path=db_path)
    patterns = consolidate_usage_events(event_type="agent_observation", db_path=db_path)

    assert len(events) == 1
    assert patterns[0].subject == "repo_index"
    assert patterns[0].success_count == 1
    assert recall(scope="learned_pattern", query="repo_index", db_path=db_path)
