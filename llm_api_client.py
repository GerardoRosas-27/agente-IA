"""
LLM vía API compatible con OpenAI (LM Studio en la red local).

Solo conexión HTTP a `…/v1/chat/completions`. Valores por defecto si faltan en .env:
  base:  http://192.168.0.13:1234/v1
  modelo: xiaomi-mimo-vl-miloco-7b (ajusta en .env: LLM_API_BASE_URL, LLM_MODEL)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import requests

# LM Studio (misma red que el PC con el servidor; cambia en .env si aplica)
DEFAULT_LLM_API_BASE_URL = "http://192.168.0.4:1234/v1"
DEFAULT_LLM_MODEL = "xiaomi-mimo-vl-miloco-7b"

_ENV_LOADED = False


def _load_env_file() -> None:
    """Carga `.env` sin dependencias externas (no sobrescribe variables ya definidas)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        _ensure_llm_defaults()
        return
    _ENV_LOADED = True
    path = Path(__file__).resolve().parent / ".env"
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw = ""
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
    _ensure_llm_defaults()


def _ensure_llm_defaults() -> None:
    if not (os.getenv("LLM_API_BASE_URL", "").strip()):
        os.environ["LLM_API_BASE_URL"] = DEFAULT_LLM_API_BASE_URL
    if not (os.getenv("LLM_MODEL", "").strip()):
        os.environ["LLM_MODEL"] = DEFAULT_LLM_MODEL


def is_remote_llm_configured() -> bool:
    _load_env_file()
    return bool((os.getenv("LLM_API_BASE_URL") or "").strip())


def get_resolved_model(cli_fallback: str = "") -> str:
    """Modelo: env LLM_MODEL, luego `cli_fallback`, luego el por defecto de LM Studio."""
    _load_env_file()
    m = (os.getenv("LLM_MODEL") or "").strip()
    if m:
        return m
    c = (cli_fallback or "").strip()
    if c:
        return c
    return DEFAULT_LLM_MODEL


def parse_assistant_message(r: Any) -> str | None:
    """
    Extrae texto de {"message": {"content": "..."}} (OpenAI) u objetos tipo respuesta
    con .message. Algunos modelos rellenan "thinking" si content está vacío.
    """
    if r is None:
        return None
    if isinstance(r, dict):
        try:
            msg = r["message"]
        except (KeyError, TypeError):
            return None
    else:
        msg = getattr(r, "message", None)
    if msg is None:
        return None
    if isinstance(msg, dict):
        raw = msg.get("content")
        think = msg.get("thinking") or msg.get("reasoning_content")
    else:
        try:
            raw = msg["content"]  # type: ignore[index]
        except (KeyError, TypeError, AttributeError):
            raw = getattr(msg, "content", None)
        think = getattr(msg, "thinking", None) or getattr(
            msg, "reasoning_content", None
        )
    if raw is not None:
        t = str(raw).strip()
        if t:
            return t
    if think is not None:
        t2 = str(think).strip()
        if t2:
            return t2
    return None


def _post_chat_completions(
    base: str,
    model: str,
    messages: list,
    options: dict | None,
    *,
    timeout: float,
    api_key: str,
) -> dict | None:
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
    
    # Soporte para el endpoint custom /api/v1/chat
    is_custom_endpoint = base.endswith("/api/v1")
    
    if is_custom_endpoint:
        url = f"{base}/chat"
        system_prompt = ""
        user_input = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt += m.get("content", "") + "\n"
            elif m.get("role") == "user":
                user_input += m.get("content", "") + "\n"
                
        payload: dict[str, Any] = {
            "model": model,
            "system_prompt": system_prompt.strip(),
            "input": user_input.strip()
        }
    else:
        url = f"{base.rstrip('/')}/chat/completions"
        payload = {
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
    except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
        print(f"Error de red o JSON: {e}")
        return None

    if is_custom_endpoint:
        # Extraer texto de respuestas custom comunes
        text = ""
        if isinstance(data, dict):
            # Formato específico de LM Studio /api/v1/chat
            if "output" in data and isinstance(data["output"], list):
                for item in data["output"]:
                    if isinstance(item, dict) and item.get("type") == "message":
                        text = item.get("content", "")
                        break
                if not text:
                    for item in data["output"]:
                        if isinstance(item, dict) and item.get("type") == "reasoning":
                            text = item.get("content", "")
                            break
            elif "response" in data and isinstance(data["response"], str):
                text = data["response"]
            elif "content" in data and isinstance(data["content"], str):
                text = data["content"]
            elif "message" in data:
                if isinstance(data["message"], str):
                    text = data["message"]
                elif isinstance(data["message"], dict):
                    text = data["message"].get("content", "")
            elif "choices" in data and len(data["choices"]) > 0:
                msg = (data["choices"][0] or {}).get("message") or {}
                text = msg.get("content", "")
        
        if text:
            return {"message": {"role": "assistant", "content": str(text).strip()}}
        return None

    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if content is None or not str(content).strip():
            content = msg.get("reasoning_content") or msg.get("thinking")
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
    POST /v1/chat/completions al único servidor configurado (LLM_API_BASE_URL).
    """
    _load_env_file()
    primary = (os.getenv("LLM_API_BASE_URL") or DEFAULT_LLM_API_BASE_URL).strip().rstrip("/")
    if not primary:
        return None

    main_timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "300") or "300")
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    return _post_chat_completions(
        primary, model, messages, options, timeout=main_timeout, api_key=api_key
    )


def resolve_llm_chat_for_pipeline(cli_model: str) -> tuple[Callable[..., Any], str, str]:
    """
    Retorna (remote_openai_chat, model_id, etiqueta).
    Siempre la API de LM Studio (URL y modelo vía .env; hay valores por defecto en código).
    """
    _load_env_file()
    model_id = get_resolved_model(cli_model)
    return (
        remote_openai_chat,
        model_id,
        "LM Studio (API OpenAI)",
    )
