"""
LLM vía API compatible con OpenAI (LM Studio, vLLM, etc.).

Si LM Studio no responde, se puede reintentar contra una API de prueba local
(`llm_test_api_server.py`) — ver `LLM_TEST_API_BASE_URL` y `LLM_TEST_FALLBACK`.

Variables de entorno (típicamente en `.env` en la raíz del proyecto):
  LLM_API_BASE_URL   — LM Studio, p.ej. http://127.0.0.1:1234/v1
  LLM_MODEL          — Modelo expuesto por el servidor
  LLM_API_KEY        — Opcional; Bearer si no está vacío
  LLM_HTTP_TIMEOUT   — Segundos (por defecto 300)
  LLM_MAX_TOKENS     — Por defecto 512; -1 pasa "sin tope" (p.ej. LM Studio en /v1/chat/completions)
  LLM_TEST_API_BASE_URL — Base del API de prueba (por defecto http://127.0.0.1:8765/v1)
  LLM_TEST_FALLBACK  — 1 (defecto) intentar API de prueba si falla el principal; 0 desactiva
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import requests

_ENV_LOADED = False


def _load_env_file() -> None:
    """Carga `.env` sin dependencias externas (no sobrescribe variables ya definidas)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


def is_remote_llm_configured() -> bool:
    _load_env_file()
    return bool(os.getenv("LLM_API_BASE_URL", "").strip())


def _test_fallback_enabled() -> bool:
    v = (os.getenv("LLM_TEST_FALLBACK") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _fallback_base() -> str:
    return (os.getenv("LLM_TEST_API_BASE_URL") or "http://127.0.0.1:8765/v1").strip().rstrip("/")


def _post_chat_completions(
    base: str,
    model: str,
    messages: list,
    options: dict | None,
    *,
    timeout: float,
    api_key: str,
) -> dict | None:
    url = f"{base.rstrip('/')}/chat/completions"
    opts = options or {}
    env_default = (os.getenv("LLM_MAX_TOKENS") or "").strip()

    def _parse_tokens(v: int | str) -> int:
        n = int(v)
        if n < 0:
            return -1
        return n

    def _max_tokens_value() -> int:
        if "num_predict" in opts:
            return _parse_tokens(opts["num_predict"])
        if "max_tokens" in opts:
            return _parse_tokens(opts["max_tokens"])
        if not env_default:
            return 512
        if env_default.strip() == "-1":
            return -1
        try:
            v = int(env_default)
            if v < 0:
                return -1
            return v
        except (ValueError, TypeError):
            return 512

    mt = _max_tokens_value()
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": float(opts.get("temperature", 0.45)),
    }
    if mt < 0:
        payload["max_tokens"] = -1
    else:
        payload["max_tokens"] = max(1, mt)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None

    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if content is None:
            return None
        text = str(content).strip()
        if not text:
            return None
        return {"message": {"role": "assistant", "content": text}}
    except (TypeError, AttributeError, KeyError, IndexError):
        return None


def remote_openai_chat(
    model: str,
    messages: list,
    options: dict | None = None,
) -> dict | None:
    """
    POST /chat/completions al servidor principal; si falla, al API de prueba (si está activo).
    Devuelve dict compatible con `_ollama_response_text`.
    """
    _load_env_file()
    primary = os.getenv("LLM_API_BASE_URL", "").strip().rstrip("/")
    if not primary:
        return None

    main_timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "300") or "300")
    api_key = (os.getenv("LLM_API_KEY") or "").strip()

    out = _post_chat_completions(
        primary, model, messages, options, timeout=main_timeout, api_key=api_key
    )
    if out is not None:
        return out

    if not _test_fallback_enabled():
        return None

    fb = _fallback_base()
    if fb.rstrip("/") == primary.rstrip("/"):
        return None

    fb_timeout = float(os.getenv("LLM_TEST_HTTP_TIMEOUT", "12") or "12")
    # La API de prueba no requiere la misma clave que LM Studio
    return _post_chat_completions(
        fb,
        model,
        messages,
        options,
        timeout=fb_timeout,
        api_key="",
    )


def resolve_llm_chat_for_pipeline(cli_model: str) -> tuple[Callable[..., Any], str, str]:
    """
    Retorna (callable_chat, model_id, etiqueta_backend).

    Si `LLM_API_BASE_URL` está definido, usa la API remota y el modelo
    `LLM_MODEL` (o `--llm-model` como respaldo).
    Si no, usa Ollama local con `cli_model`.
    """
    _load_env_file()
    if is_remote_llm_configured():
        env_model = (os.getenv("LLM_MODEL") or "").strip()
        model_id = env_model or (cli_model or "").strip()
        if not model_id:
            raise ValueError(
                "Con LLM_API_BASE_URL definido hace falta LLM_MODEL en el entorno "
                "(o pasa --llm-model como respaldo)."
            )
        return (
            remote_openai_chat,
            model_id,
            "API (LM Studio / OpenAI; si falla → API de prueba local si está activa)",
        )
    from chat_bridge import local_llm_chat_call

    m = (cli_model or "gemma3:270m").strip()
    return local_llm_chat_call, m, "Ollama (local)"
