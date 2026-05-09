from __future__ import annotations

from harness import prompts


def test_leader_prompt_includes_internal_execution_context():
    message = prompts.leader_user_message(
        feature_block="title: Buscar en internet",
        agents_excerpt="AGENTS",
        checkpoints_excerpt="CHECKPOINTS",
        internal_context="Decisión previa a crear herramientas: `REUSE_EXISTING_SKILL`\nenergía_libre=0.12",
    )

    assert "Contexto interno de ejecución y reutilización de skills" in message
    assert "REUSE_EXISTING_SKILL" in message
    assert "energía_libre" in message
    assert "reutilizar una skill existente" in message


def test_implementer_prompt_tells_agent_not_to_duplicate_skills():
    message = prompts.implementer_user_message(
        feature_block="title: Buscar en internet",
        leader_plan="Usar internet_search_api",
        architecture_excerpt="architecture",
        conventions_excerpt="conventions",
        internal_context="Usar skill existente `internet_search_api` con energía_libre=0.10.",
    )

    assert "reutilización de skills" in message
    assert "evita crear una skill duplicada" in message
    assert "energía_libre" in message
    assert "internet_search_api" in message


def test_initializer_prompt_receives_neural_context():
    message = prompts.initializer_user_message(
        user_goal="crear buscador web",
        max_existing_id=10,
        internal_context="REUSE_EXISTING_SKILL internet_search_api",
    )

    assert "Contexto interno de red neuronal" in message
    assert "internet_search_api" in message
    assert "crear buscador web" in message
