# Harness multi-agente + LM Studio

Sistema **ligero** inspirado en [Harness Engineering (repo ejemplo)](https://github.com/betta-tech/ejemplo-harness-subagentes) y en prácticas de Anthropic: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), [Harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps), [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [Managed Agents (cerebro / manos / sesión)](https://www.anthropic.com/engineering/managed-agents), [Multi-agent research](https://www.anthropic.com/engineering/built-multi-agent-research-system), [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) y [Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents).

## Ideas que incorpora

- **Lista de features en JSON** (`feature_list.json`), una `in_progress` a la vez.
- **Estado en disco** (`progress/`), no solo en el chat.
- **Tres roles en el modelo**: líder (plan), implementador (informe), revisor (veredicto explícito PASS/FAIL).
- **Cliente LLM único**: LM Studio u otro servidor compatible OpenAI vía `llm_api_client.py`.
- **Router de skills liviano**: usa la memoria persistida en `progress/harness_state.db`, sin datasets de entrenamiento versionados.
- **Guardrails agenticos**: contexto automático, patches validados, bloqueo de comandos peligrosos y evidencia para revisión.

## Requisitos

```bash
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y configura `LLM_API_BASE_URL` (debe terminar en `/v1`) y `LLM_MODEL`.

## Comandos

| Comando | Descripción |
|---------|-------------|
| `python -m harness.cli validate` | Comprueba reglas de `feature_list.json` |
| `python -m harness.cli status` | Cuenta features por estado |
| `python -m harness.cli run` | Un ciclo líder → implementador → revisor |
| `python -m harness.cli expand "…"` | Añade features `pending` desde texto natural |
| `python -m harness.cli init` | Equivale a `pytest tests/ -q` |
| `python -m harness.cli index "consulta"` | Busca contexto por símbolos/imports/texto |
| `python -m harness.cli evaluate archivo.py` | Ejecuta checks objetivos sobre cambios |
| `python -m harness.cli benchmark` | Mide capacidades locales del harness |
| `python -m harness.cli agent "objetivo"` | Ejecuta un agente con herramientas, sesiones y checkpoints |

También puedes usar `python -m harness` en lugar de `python -m harness.cli` (mismos subcomandos).

## Interfaz gráfica (opcional)

```bash
python multi_agent_app.py
```

Con argumentos se comporta como el CLI (`python multi_agent_app.py run`, etc.).

## Verificación del repo

```powershell
powershell -File init.ps1
```

## Punto de entrada para agentes

Abre [`AGENTS.md`](AGENTS.md) y sigue el mapa (`docs/`, `CHECKPOINTS.md`, `progress/`).

## Mejoras tipo Hermes/Claude Code

El harness ahora incluye diez piezas de infraestructura para acercarse a un agente de código avanzado:

1. Perfiles LLM (`LLM_PROFILE`, `LLM_<PERFIL>_MODEL`, `LLM_<PERFIL>_API_BASE_URL`).
2. Índice del repo por símbolos, imports y tokens.
3. Recuperación de memoria por similitud léxica ponderada.
4. Loop de herramientas `search/read/patch/test/command/done`.
5. Estado observable del loop para replanificar por pasos.
6. Evaluador objetivo de cambios (`py_compile`, tests relacionados, comandos permitidos).
7. Política de bloqueo para comandos destructivos.
8. Evidencia estructurada para el revisor.
9. Benchmarks locales para medir regresiones de capacidades.
10. CLI para indexar, evaluar y benchmarkear sin depender del chat.

### Agente de código

```bash
python -m harness.cli agent "arregla el parser de webhook" --session webhook_fix
python -m harness.cli agent --resume webhook_fix
```

El agente usa acciones JSON internas (`search`, `read`, `patch`, `test`,
`command`, `done`). Los patches primero se previsualizan; solo se aplican si el
modelo repite la acción con `apply=true`, y cada aplicación guarda un checkpoint
en `progress/agent_sessions/checkpoints/`.

## Prueba opcional contra LM Studio real

Con el servidor en marcha:

```bash
set LLM_INTEGRATION_TEST=1
python -m pytest tests/test_llm_studio_integration.py -v
```
