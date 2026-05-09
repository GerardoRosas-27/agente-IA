"""Generación de datasets sintéticos para entrenar el router de skills."""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from harness.paths import PROGRESS_DIR, STATE_DB_PATH
from harness.shared_memory import record_skill_usage, remember


DATASET_DIR = PROGRESS_DIR / "training_datasets"
DEFAULT_DATASET_PATH = DATASET_DIR / "tool_routing_dataset.jsonl"
DEFAULT_USER_TASK_DATASET_PATH = DATASET_DIR / "user_requested_tasks_dataset.jsonl"


@dataclass(frozen=True)
class DatasetStats:
    path: str
    examples: int
    approx_tokens: int
    trained_examples: int = 0
    trained_skills: tuple[str, ...] = ()


SKILL_PROFILES = {
    "internet_search_api": {
        "intents": [
            "buscar información actualizada",
            "consultar resultados web",
            "verificar conectividad a internet",
            "descargar texto de una URL",
            "obtener fuentes para una respuesta",
        ],
        "inputs": ["query", "url", "max_results", "max_chars"],
        "outputs": ["resultados estructurados", "texto formateado", "estado de conexión"],
        "api": "run({'action': 'search', 'query': consulta, 'max_results': 5})",
    },
    "python_execution_environment": {
        "intents": [
            "crear y ejecutar programa Python",
            "probar un script Python aislado",
            "leer logs de ejecución Python",
            "detener proceso Python en segundo plano",
            "validar código generado por el agente",
        ],
        "inputs": ["name", "source", "args", "timeout", "run_id"],
        "outputs": ["run_id", "log", "status", "returncode"],
        "api": "run({'action': 'run', 'name': 'demo.py', 'timeout': 10})",
    },
    "node_execution_environment": {
        "intents": [
            "crear y ejecutar script Node.js",
            "debuggear un script con inspector",
            "leer logs de proceso Node",
            "detener servicio Node",
            "probar automatización JavaScript",
        ],
        "inputs": ["name", "source", "args", "timeout", "break_on_start"],
        "outputs": ["run_id", "inspector_url", "log", "status"],
        "api": "run({'action': 'debug', 'name': 'server.js'})",
    },
    "whatsapp_connector": {
        "intents": [
            "enviar mensaje por WhatsApp",
            "responder un webhook de WhatsApp",
            "normalizar número telefónico",
            "extraer mensajes entrantes",
            "conectar el harness con mensajería",
        ],
        "inputs": ["phone_number", "message", "payload", "verify_token"],
        "outputs": ["resultado de envío", "mensajes entrantes", "respuesta webhook"],
        "api": "handle_input_command('wa +521234567890 | hola', dry_run=True)",
    },
    "wa_business_api_client": {
        "intents": [
            "autenticar WhatsApp Business API",
            "enviar mensaje de prueba Cloud API",
            "manejar error de token inválido",
            "detectar destinatario inválido",
            "verificar conexión a Meta Graph API",
        ],
        "inputs": ["token", "account_id", "to_number", "message_text"],
        "outputs": ["respuesta API", "APIAuthenticationError", "RecipientNotFoundError"],
        "api": "WaBusinessApiClient(token, account_id).send_test_message(to, text)",
    },
    "arithmetic_calculator": {
        "intents": [
            "calcular suma",
            "calcular resta",
            "calcular multiplicación",
            "calcular división",
            "validar entradas numéricas",
        ],
        "inputs": ["num1", "operator", "num2"],
        "outputs": ["resultado", "mensaje formateado", "error división por cero"],
        "api": "ArithmeticCalculatorSkill().run({'num1': 10, 'operator': '+', 'num2': 5})",
    },
    "qr_scan_handler": {
        "intents": [
            "procesar escaneo QR",
            "validar payload QR",
            "manejar timeout de escaneo",
            "notificar estado de conexión por QR",
            "rechazar datos QR inválidos",
        ],
        "inputs": ["raw_data", "scan_timestamp", "timeout"],
        "outputs": ["ScanStatus", "ScannedData", "notificación"],
        "api": "QrScanHandlerSkill().handle_scan(raw_data)",
    },
}


CONTEXTS = [
    "en una sesión de usuario final",
    "durante un ciclo líder implementador revisor",
    "como paso previo a crear una herramienta nueva",
    "para conectar dos skills existentes",
    "en una prueba automatizada del harness",
    "como diagnóstico antes de ejecutar código",
    "para reducir energía libre y evitar duplicación",
    "como subtarea de una feature pendiente",
]


USER_REQUEST_PATTERNS = [
    "quiero que el sistema {intent}",
    "crea una solución para {intent}",
    "necesito automatizar cómo {intent}",
    "haz una herramienta que pueda {intent}",
    "revisa si ya existe algo para {intent}",
    "conecta las herramientas para {intent}",
    "diseña un flujo para {intent}",
    "implementa y prueba cómo {intent}",
]


STEP_BANK = [
    "leer documentación de la skill candidata",
    "comparar entradas requeridas con datos disponibles",
    "ejecutar una invocación mínima en dry-run o entorno aislado",
    "validar salida estructurada y registrar evidencia",
    "crear adaptador solo si falta conversión de formato",
    "agregar prueba enfocada para el flujo conectado",
    "registrar éxito o fallo en skill_usage",
    "recalcular recomendación con energía libre",
]


def _approx_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _make_example(index: int, skill_name: str, rng: random.Random) -> dict:
    profile = SKILL_PROFILES[skill_name]
    intent = rng.choice(profile["intents"])
    context = rng.choice(CONTEXTS)
    inputs = rng.sample(profile["inputs"], k=min(len(profile["inputs"]), rng.randint(1, 3)))
    outputs = rng.sample(profile["outputs"], k=min(len(profile["outputs"]), rng.randint(1, 2)))
    steps = rng.sample(STEP_BANK, k=5)
    subtasks = [
        f"identificar si `{skill_name}` cubre: {intent}",
        f"preparar entradas: {', '.join(inputs)}",
        f"esperar salidas: {', '.join(outputs)}",
        "si falta conexión, crear adaptador pequeño y testeado",
        "guardar aprendizaje en memoria compartida",
    ]
    create_if_missing = [
        "crear nueva skill solo si ninguna candidata supera el umbral de reutilización",
        "definir API `run(request)` con contrato mínimo",
        "crear documentación Markdown y pruebas antes de marcar done",
    ]
    task = f"{intent} {context}"
    return {
        "id": f"synthetic_{index:08d}",
        "task": task,
        "selected_skill": skill_name,
        "decision": "REUSE_EXISTING_SKILL",
        "candidate_tools": [skill_name],
        "inputs": inputs,
        "expected_outputs": outputs,
        "tasks": [
            "analizar objetivo",
            "consultar memoria y energía libre",
            "seleccionar skill existente",
            "ejecutar flujo mínimo",
            "registrar resultado del aprendizaje",
        ],
        "subtasks": subtasks,
        "steps": steps,
        "create_if_missing": create_if_missing,
        "code_solution": profile["api"],
        "training_text": (
            f"Tarea: {task}. Usar `{skill_name}` porque cubre {intent}. "
            f"Entradas: {', '.join(inputs)}. Salidas: {', '.join(outputs)}. "
            f"Pasos: {'; '.join(steps)}. Subtareas: {'; '.join(subtasks)}. "
            f"Solución sugerida: {profile['api']}. Si no existe, {create_if_missing[0]}."
        ),
    }


def generate_tool_routing_dataset(
    *,
    output_path: Path = DEFAULT_DATASET_PATH,
    target_tokens: int = 2_000_000,
    seed: int = 17,
) -> DatasetStats:
    """Genera un dataset JSONL grande con tareas, subtareas y pasos."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    skills = list(SKILL_PROFILES)
    approx_tokens = 0
    examples = 0
    with output_path.open("w", encoding="utf-8") as file:
        while approx_tokens < target_tokens:
            skill = skills[examples % len(skills)]
            if examples % 97 == 0:
                skill = rng.choice(skills)
            example = _make_example(examples, skill, rng)
            line = json.dumps(example, ensure_ascii=False)
            file.write(line + "\n")
            approx_tokens += _approx_tokens(example["training_text"])
            examples += 1
    stats = DatasetStats(path=str(output_path), examples=examples, approx_tokens=approx_tokens)
    _write_stats(stats, output_path.with_suffix(".stats.json"))
    return stats


def _make_user_task_example(index: int, skill_name: str, rng: random.Random) -> dict:
    base = _make_example(index, skill_name, rng)
    profile = SKILL_PROFILES[skill_name]
    intent = rng.choice(profile["intents"])
    user_request = rng.choice(USER_REQUEST_PATTERNS).format(intent=intent)
    user_request = f"{user_request} {rng.choice(CONTEXTS)}"
    task_breakdown = [
        "confirmar intención del usuario y resultado esperado",
        "consultar red neuronal de skills y energía libre antes de crear código",
        f"evaluar si `{skill_name}` cubre el caso solicitado",
        "usar la skill existente si cubre el caso",
        "crear adaptador mínimo si faltan entradas/salidas",
        "ejecutar prueba o simulación del flujo",
        "guardar la sesión como entrenamiento",
    ]
    subtasks = [
        f"extraer entidades necesarias: {', '.join(base['inputs'])}",
        f"validar salida esperada: {', '.join(base['expected_outputs'])}",
        "documentar evidencia de reutilización o creación",
        "actualizar skill_usage con éxito/fallo",
    ]
    base.update(
        {
            "id": f"user_task_{index:08d}",
            "source": "synthetic_user_request",
            "user_request": user_request,
            "task": user_request,
            "tasks": task_breakdown,
            "subtasks": subtasks,
            "training_text": (
                f"Solicitud de usuario: {user_request}. "
                f"Desglose: {'; '.join(task_breakdown)}. "
                f"Subtareas: {'; '.join(subtasks)}. "
                f"Herramienta recomendada: `{skill_name}`. "
                f"API sugerida: {profile['api']}. "
                "Regla: consultar red neuronal entrenada antes de trabajar y guardar la sesión al terminar."
            ),
        }
    )
    return base


def generate_user_task_dataset(
    *,
    output_path: Path = DEFAULT_USER_TASK_DATASET_PATH,
    examples: int = 20_000,
    seed: int = 41,
) -> DatasetStats:
    """Genera dataset JSONL de solicitudes de usuarios con tareas y subtareas."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    skills = list(SKILL_PROFILES)
    approx_tokens = 0
    with output_path.open("w", encoding="utf-8") as file:
        for index in range(examples):
            skill = skills[index % len(skills)]
            if index % 89 == 0:
                skill = rng.choice(skills)
            example = _make_user_task_example(index, skill, rng)
            file.write(json.dumps(example, ensure_ascii=False) + "\n")
            approx_tokens += _approx_tokens(example["training_text"])
    stats = DatasetStats(path=str(output_path), examples=examples, approx_tokens=approx_tokens)
    _write_stats(stats, output_path.with_suffix(".stats.json"))
    return stats


def iter_dataset(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def train_from_dataset(
    *,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    max_examples: int | None = None,
    db_path: Path = STATE_DB_PATH,
) -> DatasetStats:
    """Entrena memoria/red desde el dataset, agregando ejemplos por skill."""
    trained = 0
    approx_tokens = 0
    trained_skills: set[str] = set()
    for example in iter_dataset(dataset_path):
        if max_examples is not None and trained >= max_examples:
            break
        skill = str(example["selected_skill"])
        use_case = str(example["task"])
        instructions = (
            f"Dataset sintético: {example['training_text']} "
            f"Pasos internos: {'; '.join(example.get('steps', []))}."
        )
        record_skill_usage(
            skill,
            use_case,
            instructions,
            success=True,
            outcome="dataset_training_success",
            db_path=db_path,
        )
        trained += 1
        trained_skills.add(skill)
        approx_tokens += _approx_tokens(str(example.get("training_text", "")))

    stats = DatasetStats(
        path=str(dataset_path),
        examples=sum(1 for _ in iter_dataset(dataset_path)),
        approx_tokens=_dataset_token_count(dataset_path),
        trained_examples=trained,
        trained_skills=tuple(sorted(trained_skills)),
    )
    remember(
        "auto_training_dataset",
        f"dataset_train_{datetime.now().isoformat(timespec='seconds')}",
        json.dumps(asdict(stats), ensure_ascii=False),
        tags=["dataset", "runtime_learning", "tool_router"],
        confidence=0.98,
        db_path=db_path,
    )
    _write_stats(stats, dataset_path.with_suffix(".trained.stats.json"))
    return stats


def _dataset_token_count(path: Path) -> int:
    total = 0
    for example in iter_dataset(path):
        total += _approx_tokens(str(example.get("training_text", "")))
    return total


def _write_stats(stats: DatasetStats, path: Path) -> None:
    path.write_text(json.dumps(asdict(stats), ensure_ascii=False, indent=2), encoding="utf-8")
