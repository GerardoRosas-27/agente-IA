from pathlib import Path

from harness.shared_memory import (
    add_self_improvement,
    recall,
    record_skill_usage,
    remember,
    self_improvement_context,
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
