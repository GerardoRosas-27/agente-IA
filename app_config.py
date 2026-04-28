"""
Configuracion editable desde UI y persistida en `.env`.

Preserva comentarios y variables desconocidas del archivo, actualiza las claves
conocidas y sincroniza os.environ tras guardar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigSpec:
    key: str
    default: str
    kind: str = "str"
    section: str = "General"
    description: str = ""
    secret: bool = False


CONFIG_SPECS: tuple[ConfigSpec, ...] = (
    ConfigSpec("LLM_API_BASE_URL", "http://192.168.0.13:1234/v1", "str", "LLM", "URL base OpenAI-compatible."),
    ConfigSpec("LLM_MODEL", "xiaomi-mimo-vl-miloco-7b", "str", "LLM", "Modelo usado por LM Studio."),
    ConfigSpec("LLM_MAX_TOKENS", "", "int", "LLM", "Max tokens opcional; -1 sin tope."),
    ConfigSpec("LLM_API_KEY", "", "str", "LLM", "API key opcional.", secret=True),
    ConfigSpec("LLM_HTTP_TIMEOUT", "300", "int", "LLM", "Timeout HTTP del LLM."),
    ConfigSpec("INTERNET_AGENT_ENABLED", "1", "bool", "Agentes", "Activa agente Internet."),
    ConfigSpec("INTERNET_AGENT_TIMEOUT", "4", "int", "Agentes", "Timeout de busqueda web."),
    ConfigSpec("INTERNET_AGENT_MAX_RESULTS", "4", "int", "Agentes", "Resultados maximos de busqueda."),
    ConfigSpec("PYTHON_TEST_AGENT_ENABLED", "1", "bool", "Agentes", "Activa PruebaPython."),
    ConfigSpec("PYTHON_TEST_AGENT_TIMEOUT", "5", "int", "Agentes", "Timeout de scripts Python."),
    ConfigSpec("PYTHON_TEST_AGENT_MAX_CHARS", "2500", "int", "Agentes", "Tamano maximo de script Python."),
    ConfigSpec("PYTHON_TEST_AGENT_SKIP_NON_CODE", "1", "bool", "Agentes", "Evita Python si no hay contexto ejecutable."),
    ConfigSpec("NODE_TEST_AGENT_ENABLED", "1", "bool", "Agentes", "Activa PruebaNode."),
    ConfigSpec("NODE_TEST_AGENT_TIMEOUT", "5", "int", "Agentes", "Timeout de scripts Node."),
    ConfigSpec("NODE_TEST_AGENT_MAX_CHARS", "3000", "int", "Agentes", "Tamano maximo de script Node."),
    ConfigSpec("NODE_TEST_AGENT_SKIP_NON_CODE", "1", "bool", "Agentes", "Evita Node si no hay contexto JS."),
    ConfigSpec("TERMINAL_AGENT_ENABLED", "0", "bool", "Terminal", "Activa agente Terminal."),
    ConfigSpec("TERMINAL_AGENT_TIMEOUT", "10", "int", "Terminal", "Timeout de comando terminal."),
    ConfigSpec("TERMINAL_AGENT_MAX_OUTPUT_CHARS", "4000", "int", "Terminal", "Salida maxima terminal."),
    ConfigSpec("TERMINAL_AGENT_ENABLE_SAFE_COMMANDS", "1", "bool", "Terminal", "Comandos seguros activos."),
    ConfigSpec("TERMINAL_AGENT_SAFE_COMMANDS", "python,py,pytest,git,rg,dir,ls,where", "str", "Terminal", "Allowlist segura."),
    ConfigSpec("TERMINAL_AGENT_ENABLE_MODERATE_COMMANDS", "1", "bool", "Terminal", "Comandos moderados activos."),
    ConfigSpec("TERMINAL_AGENT_MODERATE_COMMANDS", "pip,node,npm", "str", "Terminal", "Allowlist moderada."),
    ConfigSpec("TERMINAL_AGENT_ENABLE_DANGEROUS_COMMANDS", "0", "bool", "Terminal", "Comandos peligrosos activos."),
    ConfigSpec("TERMINAL_AGENT_DANGEROUS_COMMANDS", "rm,rmdir,del,erase,remove-item,rd,format,shutdown,reboot,restart-computer,stop-computer,setx,reg,schtasks,sc", "str", "Terminal", "Comandos peligrosos."),
    ConfigSpec("TOOL_LIBRARY_AGENT_ENABLED", "1", "bool", "Memoria/Skills", "Activa ToolLibrary."),
    ConfigSpec("TOOL_LIBRARY_MAX_RESULTS", "4", "int", "Memoria/Skills", "Resultados de ToolLibrary."),
    ConfigSpec("AGENT_AUTONOMOUS_MODE", "0", "bool", "Control", "Modo autonomo global."),
    ConfigSpec("TOOL_REGISTRY_MAX_AUTO_RISK", "moderate", "str", "Control", "Riesgo maximo auto: low/moderate/high/dangerous."),
    ConfigSpec("TOOL_REGISTRY_ALLOWED_TOOLS", "", "str", "Control", "Si se define, solo estas herramientas."),
    ConfigSpec("TOOL_REGISTRY_DENIED_TOOLS", "terminal.run", "str", "Control", "Herramientas denegadas."),
    ConfigSpec("SKILL_MANAGER_ENABLED", "1", "bool", "Memoria/Skills", "Activa SkillManager."),
    ConfigSpec("SKILL_MANAGER_MAX_RESULTS", "5", "int", "Memoria/Skills", "Skills maximos recuperados."),
    ConfigSpec("SKILL_EXECUTION_AGENT_ENABLED", "1", "bool", "Memoria/Skills", "Permite ejecutar skills."),
    ConfigSpec("PERSISTENT_MEMORY_ENABLED", "1", "bool", "Memoria/Skills", "Activa memoria persistente."),
    ConfigSpec("TOOL_PREFERENCE_NET_ENABLED", "1", "bool", "Neural", "Activa ToolPreferenceNet."),
    ConfigSpec("TOOL_PREFERENCE_RANK_LIMIT", "8", "int", "Neural", "Ranking maximo de herramientas."),
    ConfigSpec("SPECIALIZED_REGIONS_ENABLED", "1", "bool", "Neural", "Activa regiones especializadas."),
    ConfigSpec("COGNITIVE_REGIONS_ENABLED", "1", "bool", "Neural", "Activa sistema cognitivo."),
    ConfigSpec("REST_CYCLE_ENABLED", "0", "bool", "Automejora", "Activa ciclo de reposo."),
    ConfigSpec("REST_CYCLE_INTERVAL_SECONDS", "1800", "int", "Automejora", "Intervalo reposo."),
    ConfigSpec("SELF_IMPROVEMENT_ENABLED", "0", "bool", "Automejora", "Activa worker de auto-mejora."),
    ConfigSpec("SELF_IMPROVEMENT_INTERVAL_SECONDS", "3600", "int", "Automejora", "Intervalo auto-mejora."),
    ConfigSpec("SELF_IMPROVEMENT_RUN_TESTS", "1", "bool", "Automejora", "Probar candidatos."),
    ConfigSpec("SELF_IMPROVEMENT_TEST_TIMEOUT", "120", "int", "Automejora", "Timeout tests candidato."),
    ConfigSpec("SELF_IMPROVEMENT_AUTO_PROMOTE", "0", "bool", "Automejora", "Promocion automatica al core."),
    ConfigSpec("SELF_IMPROVEMENT_PROMOTION_MIN_EVALUATIONS", "3", "int", "Automejora", "Evaluaciones positivas requeridas."),
    ConfigSpec("SELF_IMPROVEMENT_PROMOTION_MIN_DELTA", "0.05", "float", "Automejora", "Delta minimo sobre core."),
    ConfigSpec("AUXILIARY_AGENTS_FAIL_OPEN", "1", "bool", "Control", "Si un auxiliar falla, continuar."),
)


def env_path(project_root: str | Path | None = None) -> Path:
    return Path(project_root or Path(__file__).resolve().parent) / ".env"


def _parse_env_lines(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def load_config(project_root: str | Path | None = None) -> dict[str, str]:
    path = env_path(project_root)
    raw = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    file_values = _parse_env_lines(raw)
    values = {}
    for spec in CONFIG_SPECS:
        values[spec.key] = os.getenv(spec.key, file_values.get(spec.key, spec.default))
    return values


def _quote_if_needed(value: str) -> str:
    if value == "":
        return ""
    if any(ch.isspace() for ch in value) or "#" in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def save_config(values: dict[str, str], project_root: str | Path | None = None) -> Path:
    path = env_path(project_root)
    old_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    known = {spec.key for spec in CONFIG_SPECS}
    written: set[str] = set()
    new_lines: list[str] = []
    for line in old_lines:
        stripped = line.strip()
        candidate = stripped.lstrip("#").strip()
        if "=" in candidate:
            key, _, _old = candidate.partition("=")
            key = key.strip()
            if key in known and key in values:
                new_lines.append(f"{key}={_quote_if_needed(str(values[key]).strip())}")
                written.add(key)
                os.environ[key] = str(values[key]).strip()
                continue
        new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.append("# Configuracion gestionada por AgenteIA")
    for spec in CONFIG_SPECS:
        if spec.key in written:
            continue
        value = str(values.get(spec.key, spec.default)).strip()
        new_lines.append(f"{spec.key}={_quote_if_needed(value)}")
        os.environ[spec.key] = value
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return path


def grouped_specs() -> dict[str, list[ConfigSpec]]:
    groups: dict[str, list[ConfigSpec]] = {}
    for spec in CONFIG_SPECS:
        groups.setdefault(spec.section, []).append(spec)
    return groups
