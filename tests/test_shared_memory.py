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
    semantic_recall_v2,
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


def test_semantic_recall_v2_resists_distractors(tmp_path: Path) -> None:
    """Reproduce el escenario del paper de Episodic Memory: muchos distractores y la
    consulta debe recuperar el item relevante en el top-3 vía TF-IDF."""
    db_path = tmp_path / "memory.db"
    remember(
        "trajectory",
        "webhook_fix",
        "Reparado el parser de webhook de WhatsApp para mensajes multimedia con sender_id correcto.",
        tags=["whatsapp", "webhook", "media"],
        db_path=db_path,
    )
    distractor_topics = [
        ("config_csv", "Cómo exportar configuraciones a CSV en disco."),
        ("logs_rotation", "Rotación automática de archivos de log por tamaño."),
        ("date_parser", "Parser de fechas en distintos formatos ISO y locales."),
        ("queue_dispatch", "Despachador de cola con prioridad para tareas batch."),
        ("pdf_render", "Render de plantillas a PDF con tipografías personalizadas."),
        ("retry_policy", "Política de reintento exponencial para clientes HTTP."),
        ("session_pool", "Pool de sesiones con reciclaje al exceder N requests."),
        ("crypto_keys", "Carga segura de claves de cifrado desde Vault."),
        ("graph_layout", "Algoritmos de layout para grafos dirigidos."),
        ("sql_migration", "Migraciones SQL idempotentes con bandera DOWN."),
    ]
    for key, value in distractor_topics:
        remember("trajectory", key, value, tags=["distractor"], db_path=db_path)

    results = semantic_recall_v2(
        query="error recibiendo media en webhook de WhatsApp con remitente",
        db_path=db_path,
        limit=3,
    )

    assert results, "semantic_recall_v2 no devolvió resultados"
    keys_top3 = [item.key for item in results]
    assert "webhook_fix" in keys_top3, f"se esperaba webhook_fix en top-3, obtuvo {keys_top3}"
    assert keys_top3[0] == "webhook_fix", f"se esperaba webhook_fix en posición 1, obtuvo {keys_top3}"


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
