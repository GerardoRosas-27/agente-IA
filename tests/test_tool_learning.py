from __future__ import annotations

from harness.shared_memory import record_skill_usage
from harness.tool_learning import (
    build_tool_reuse_plan,
    internal_execution_context,
    learned_tools_context,
    recommend_tools_for_goal,
)


def test_recommends_existing_skill_from_instructions(tmp_path):
    db_path = tmp_path / "state.db"

    recommendations = recommend_tools_for_goal("necesito calcular 10 + 5", db_path=db_path)

    names = [item.skill_name for item in recommendations]
    assert "arithmetic_calculator" in names


def test_learns_from_successful_usage(tmp_path):
    db_path = tmp_path / "state.db"
    record_skill_usage(
        "whatsapp_connector",
        "mensajeria para clientes",
        "Usar handle_input_command('wa +521234567890 | hola') para enviar mensajes.",
        success=True,
        outcome="mensaje enviado",
        db_path=db_path,
    )

    recommendations = recommend_tools_for_goal("mandar mensaje a cliente por whatsapp", db_path=db_path)

    assert recommendations
    assert recommendations[0].skill_name == "whatsapp_connector"
    assert recommendations[0].known_use_cases == ("mensajeria para clientes",)


def test_learned_context_explains_how_to_use_tool(tmp_path):
    db_path = tmp_path / "state.db"
    record_skill_usage(
        "arithmetic_calculator",
        "calcular operaciones",
        "Usar handle_input_command('calc 10 + 5') o ArithmeticCalculatorSkill().run({...}).",
        success=True,
        outcome="skill completada",
        db_path=db_path,
    )

    context = learned_tools_context("hacer una suma", db_path=db_path)

    assert "arithmetic_calculator" in context
    assert "handle_input_command" in context
    assert "Antes de crear una herramienta nueva" in context


def test_build_tool_reuse_plan_prefers_existing_skill(tmp_path):
    db_path = tmp_path / "state.db"
    record_skill_usage(
        "internet_search_api",
        "buscar resultados en internet",
        "Usar run({'action': 'search', 'query': '...'}).",
        success=True,
        outcome="búsqueda completada",
        db_path=db_path,
    )

    plan = build_tool_reuse_plan("buscar información actualizada en internet", db_path=db_path)

    assert plan.decision == "REUSE_EXISTING_SKILL"
    assert "reutilizarla" in plan.rationale
    assert any("adaptador" in step for step in plan.steps)
    assert plan.recommendations[0].free_energy >= 0


def test_internal_execution_context_lists_steps_and_subtasks(tmp_path):
    db_path = tmp_path / "state.db"

    context = internal_execution_context("crear una herramienta totalmente nueva", db_path=db_path)

    assert "Contexto interno de ejecución del ciclo" in context
    assert "energía_libre" in context
    assert "Pasos internos obligatorios" in context
    assert "Tareas y subtareas internas" in context
