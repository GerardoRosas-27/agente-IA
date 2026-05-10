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

## Prueba opcional contra LM Studio real

Con el servidor en marcha:

```bash
set LLM_INTEGRATION_TEST=1
python -m pytest tests/test_llm_studio_integration.py -v
```
