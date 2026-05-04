"""Llamadas al modelo vía `llm_api_client` (LM Studio / API OpenAI-compatible)."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable

from llm_api_client import parse_assistant_message, remote_openai_chat

_LLM_CACHE_MAX = 64
_LLM_CACHE: OrderedDict[str, str] = OrderedDict()
_LLM_CACHE_LOCK = threading.Lock()


def invoke_llm(
    model: str,
    system: str,
    user: str,
    *,
    llm_chat: Callable[..., Any] | None = None,
    num_predict: int = 2048,
    temperature: float = 0.35,
    use_cache: bool = False,
) -> str:
    """
    Una vuelta chat completions. Por defecto `remote_openai_chat` (LM Studio).
    """
    chat = llm_chat or remote_openai_chat
    clean_user = user[:24000]
    cache_key = hashlib.sha256(
        f"{model}\n{num_predict}\n{temperature}\n{system}\n{clean_user}".encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()
    if use_cache:
        with _LLM_CACHE_LOCK:
            hit = _LLM_CACHE.get(cache_key)
            if hit is not None:
                _LLM_CACHE.move_to_end(cache_key)
                return hit

    r = chat(
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": clean_user},
        ],
        {"temperature": temperature, "num_predict": int(num_predict)},
    )
    out = parse_assistant_message(r)
    if not out:
        raise RuntimeError(
            "El servidor LLM no devolvió texto. Revisa LM Studio, LLM_API_BASE_URL y LLM_MODEL."
        )
    if use_cache:
        with _LLM_CACHE_LOCK:
            _LLM_CACHE[cache_key] = out
            _LLM_CACHE.move_to_end(cache_key)
            while len(_LLM_CACHE) > _LLM_CACHE_MAX:
                _LLM_CACHE.popitem(last=False)
    return out
