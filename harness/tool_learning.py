"""Selector aprendido de herramientas/skills existentes.

La meta no es reemplazar al LLM: es darle una memoria operativa pequeña que
recuerde qué skills ya existen, para qué sirvieron y cómo invocarlas.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from harness.paths import STATE_DB_PATH
from harness.shared_memory import init_memory_db, record_skill_usage
from harness.skill_registry import SkillRecord, sync_skills


TOKEN_RE = re.compile(r"[a-z0-9_áéíóúñ]+", re.IGNORECASE)


@dataclass(frozen=True)
class ToolRecommendation:
    skill_name: str
    path: str
    score: float
    free_energy: float
    why: str
    how_to_use: str
    known_use_cases: tuple[str, ...]


@dataclass(frozen=True)
class ToolReusePlan:
    decision: str
    rationale: str
    recommendations: tuple[ToolRecommendation, ...]
    steps: tuple[str, ...]
    subtasks: tuple[str, ...]


@dataclass(frozen=True)
class _UsageExample:
    skill_name: str
    use_case: str
    instructions: str
    success_count: int
    failure_count: int
    last_outcome: str


@dataclass(frozen=True)
class FreeEnergyState:
    prediction: float
    target: float
    prediction_error: float
    complexity: float
    free_energy: float


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def _sigmoid(value: float) -> float:
    if value >= 40:
        return 1.0
    if value <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _free_energy(
    *,
    prediction: float,
    target: float,
    complexity: float,
) -> FreeEnergyState:
    """Calcula una energía libre simple: error esperado + complejidad.

    En este harness, `prediction` es qué tan probable cree la red que una skill
    sirve para el objetivo; `target` viene de éxitos/fallos aprendidos o de la
    coincidencia lexical; `complexity` penaliza skills con señales difusas o muy
    amplias. Menor energía libre implica mejor reutilización esperada.
    """
    prediction = _clamp(prediction)
    target = _clamp(target)
    complexity = max(complexity, 0.0)
    prediction_error = (target - prediction) ** 2
    return FreeEnergyState(
        prediction=prediction,
        target=target,
        prediction_error=prediction_error,
        complexity=complexity,
        free_energy=prediction_error + complexity,
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_usage(db_path: Path) -> list[_UsageExample]:
    init_memory_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT skill_name, use_case, instructions, success_count, failure_count, last_outcome
            FROM skill_usage
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [
        _UsageExample(
            skill_name=str(row["skill_name"]),
            use_case=str(row["use_case"]),
            instructions=str(row["instructions"]),
            success_count=int(row["success_count"]),
            failure_count=int(row["failure_count"]),
            last_outcome=str(row["last_outcome"]),
        )
        for row in rows
    ]


class TinyToolNeuralRouter:
    """Red pequeña de una neurona por skill, entrenada con memoria local.

    Usa tokens del objetivo como entrada y aprende pesos por skill a partir de
    éxitos/fallos guardados en `skill_usage`. También mezcla una señal lexical
    de las instrucciones Markdown para poder recomendar skills recién creadas.
    """

    def __init__(self, records: list[SkillRecord], usage: list[_UsageExample]):
        self.records = [record for record in records if record.enabled]
        self.usage = usage
        self.weights: dict[str, dict[str, float]] = {}
        self.bias: dict[str, float] = {}
        self._train()

    def _ensure_skill(self, skill_name: str) -> None:
        self.weights.setdefault(skill_name, {})
        # Sesgo negativo: una skill no debe recomendarse solo por existir.
        self.bias.setdefault(skill_name, -1.0)

    def _train(self) -> None:
        for record in self.records:
            self._ensure_skill(record.name)
            for token in _tokenize(f"{record.name} {record.instructions}"):
                self.weights[record.name][token] = self.weights[record.name].get(token, 0.0) + 0.05

        for example in self.usage:
            if not any(record.name == example.skill_name for record in self.records):
                continue
            self._ensure_skill(example.skill_name)
            tokens = _tokenize(f"{example.use_case} {example.instructions}")
            signal = max(example.success_count, 0) - max(example.failure_count, 0)
            if signal == 0:
                continue
            learning_rate = 0.25 if signal > 0 else 0.18
            bounded_signal = max(min(signal, 5), -5)
            for token in tokens:
                self.weights[example.skill_name][token] = (
                    self.weights[example.skill_name].get(token, 0.0)
                    + learning_rate * bounded_signal
                )
            self.bias[example.skill_name] += 0.08 * bounded_signal

    def _neural_score(self, skill_name: str, query_tokens: set[str]) -> float:
        self._ensure_skill(skill_name)
        raw = self.bias[skill_name]
        raw += sum(self.weights[skill_name].get(token, 0.0) for token in query_tokens)
        return _sigmoid(raw)

    @staticmethod
    def _lexical_score(record: SkillRecord, query_tokens: set[str]) -> float:
        skill_tokens = _tokenize(f"{record.name} {record.instructions}")
        if not query_tokens or not skill_tokens:
            return 0.0
        overlap = len(query_tokens & skill_tokens)
        return min(overlap / max(len(query_tokens), 1), 1.0)

    @staticmethod
    def _usage_target(examples: list[_UsageExample], lexical: float) -> float:
        if not examples:
            return lexical
        successes = sum(max(example.success_count, 0) for example in examples)
        failures = sum(max(example.failure_count, 0) for example in examples)
        total = successes + failures
        if total <= 0:
            return lexical
        learned_target = successes / total
        return _clamp((0.7 * learned_target) + (0.3 * lexical))

    @staticmethod
    def _complexity_penalty(record: SkillRecord, examples: list[_UsageExample], lexical: float) -> float:
        instruction_tokens = _tokenize(record.instructions)
        breadth_penalty = min(len(instruction_tokens) / 2000, 0.08)
        uncertainty_penalty = 0.07 if not examples else 0.0
        mismatch_penalty = 0.05 * (1.0 - lexical)
        return breadth_penalty + uncertainty_penalty + mismatch_penalty

    def recommend(self, goal: str, *, limit: int = 3) -> list[ToolRecommendation]:
        query_tokens = _tokenize(goal)
        usage_by_skill: dict[str, list[_UsageExample]] = {}
        for example in self.usage:
            usage_by_skill.setdefault(example.skill_name, []).append(example)

        scored: list[ToolRecommendation] = []
        for record in self.records:
            neural = self._neural_score(record.name, query_tokens)
            lexical = self._lexical_score(record, query_tokens)
            examples = usage_by_skill.get(record.name, [])
            target = self._usage_target(examples, lexical)
            energy = _free_energy(
                prediction=neural,
                target=target,
                complexity=self._complexity_penalty(record, examples, lexical),
            )
            base_score = (0.45 * neural) + (0.25 * lexical) + (0.30 * target)
            score = _clamp(base_score * (1.0 - min(energy.free_energy, 0.55)))
            known_cases = tuple(example.use_case for example in examples[:3])
            best_instructions = (
                examples[0].instructions
                if examples
                else _compact_instructions(record.instructions)
            )
            why_parts = []
            if lexical > 0:
                why_parts.append("coincide con el objetivo por nombre/instrucciones")
            if examples:
                why_parts.append("tiene uso aprendido previo")
            if not why_parts:
                why_parts.append("está habilitada y disponible")
            scored.append(
                ToolRecommendation(
                    skill_name=record.name,
                    path=record.path,
                    score=round(score, 4),
                    free_energy=round(energy.free_energy, 4),
                    why=", ".join(why_parts),
                    how_to_use=best_instructions,
                    known_use_cases=known_cases,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return [item for item in scored[: max(limit, 0)] if item.score >= 0.18]


def _compact_instructions(text: str, max_chars: int = 360) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def recommend_tools_for_goal(
    goal: str,
    *,
    limit: int = 3,
    db_path: Path = STATE_DB_PATH,
) -> list[ToolRecommendation]:
    records = sync_skills(db_path=db_path)
    usage = _load_usage(db_path)
    return TinyToolNeuralRouter(records, usage).recommend(goal, limit=limit)


def learned_tools_context(
    goal: str,
    *,
    limit: int = 3,
    db_path: Path = STATE_DB_PATH,
) -> str:
    recommendations = recommend_tools_for_goal(goal, limit=limit, db_path=db_path)
    if not recommendations:
        return "No se encontró una skill existente claramente relacionada; crear una nueva puede ser correcto."

    chunks = [
        "Antes de crear una herramienta nueva, revisa estas skills existentes recomendadas por memoria aprendida:"
    ]
    for item in recommendations:
        cases = ", ".join(item.known_use_cases) if item.known_use_cases else "sin casos previos registrados"
        chunks.append(
            f"- `{item.skill_name}` ({item.path}) score={item.score:.2f}, energía_libre={item.free_energy:.2f}: {item.why}. "
            f"Uso: {item.how_to_use} Casos: {cases}."
        )
    return "\n".join(chunks)


def build_tool_reuse_plan(
    goal: str,
    *,
    limit: int = 4,
    reuse_threshold: float = 0.40,
    db_path: Path = STATE_DB_PATH,
) -> ToolReusePlan:
    recommendations = recommend_tools_for_goal(goal, limit=limit, db_path=db_path)
    top = recommendations[0] if recommendations else None
    low_energy_reuse = bool(top and top.score >= 0.35 and top.free_energy <= 0.40)
    if top and (top.score >= reuse_threshold or low_energy_reuse):
        decision = "REUSE_EXISTING_SKILL"
        rationale = (
            f"La skill `{top.skill_name}` parece cubrir la tarea con score={top.score:.2f}. "
            "El ciclo debe reutilizarla y crear solo pegamento/adaptadores si falta conexión."
        )
        steps = (
            "1. Confirmar criterios de aceptación contra la documentación de la skill recomendada.",
            "2. Usar la API pública de la skill existente antes de crear una nueva.",
            "3. Si falta integración, crear solo un script/adaptador mínimo que conecte skills existentes.",
            "4. Añadir pruebas que validen el flujo conectado sin duplicar lógica ya disponible.",
            "5. Registrar el resultado en `skill_usage` para mejorar recomendaciones futuras.",
        )
        subtasks = (
            f"Leer `skills/{top.skill_name}.md` y ubicar su punto de entrada.",
            f"Probar invocación mínima de `{top.skill_name}` con datos representativos.",
            "Diseñar el adaptador más pequeño posible para conectar entradas/salidas.",
            "Verificar que no se creó una skill duplicada con responsabilidad equivalente.",
        )
    else:
        decision = "CREATE_OR_EXTEND_SKILL"
        rationale = (
            "No hay una skill existente con confianza suficiente. Se permite crear una nueva "
            "o extender una cercana, documentando por qué no bastaba reutilizar."
        )
        steps = (
            "1. Revisar lista completa de skills habilitadas e instrucciones Markdown.",
            "2. Declarar explícitamente por qué ninguna skill existente cubre la tarea.",
            "3. Crear o extender una skill en `skills/` con API pública pequeña.",
            "4. Añadir documentación de uso y pruebas automatizadas.",
            "5. Registrar el nuevo uso en memoria para ciclos posteriores.",
        )
        subtasks = (
            "Comparar la tarea contra skills existentes y memoria de usos previos.",
            "Definir contrato mínimo de entrada/salida antes de implementar.",
            "Agregar pruebas de uso directo y de integración con el harness.",
        )

    return ToolReusePlan(
        decision=decision,
        rationale=rationale,
        recommendations=tuple(recommendations),
        steps=steps,
        subtasks=subtasks,
    )


def internal_execution_context(
    goal: str,
    *,
    db_path: Path = STATE_DB_PATH,
) -> str:
    plan = build_tool_reuse_plan(goal, db_path=db_path)
    lines = [
        "## Contexto interno de ejecución del ciclo",
        "",
        f"Decisión previa a crear herramientas: `{plan.decision}`",
        f"Razón: {plan.rationale}",
        "",
        "### Capacidades existentes candidatas",
    ]
    if plan.recommendations:
        for item in plan.recommendations:
            cases = ", ".join(item.known_use_cases) if item.known_use_cases else "sin casos previos"
            lines.append(
                f"- `{item.skill_name}` ({item.path}) score={item.score:.2f}. "
                f"energía_libre={item.free_energy:.2f}. {item.why}. Uso: {item.how_to_use} Casos: {cases}."
            )
    else:
        lines.append("- Ninguna skill candidata con confianza suficiente.")

    lines.extend(["", "### Pasos internos obligatorios"])
    lines.extend(f"- {step}" for step in plan.steps)
    lines.extend(["", "### Tareas y subtareas internas"])
    lines.extend(f"- {subtask}" for subtask in plan.subtasks)
    return "\n".join(lines)


def learn_tool_outcome(
    skill_name: str,
    use_case: str,
    instructions: str,
    *,
    success: bool,
    outcome: str,
    db_path: Path = STATE_DB_PATH,
) -> None:
    """Registra feedback operacional para que el router mejore sus recomendaciones."""
    record_skill_usage(
        skill_name,
        use_case,
        instructions,
        success=success,
        outcome=outcome,
        db_path=db_path,
    )
