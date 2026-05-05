"""Llamadas al modelo vía `llm_api_client` (LM Studio / API OpenAI-compatible)."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable

from llm_api_client import parse_assistant_message, remote_openai_chat
from harness.paths import LOGS_DIR

_LLM_CACHE_MAX = 64
_LLM_CACHE: OrderedDict[str, str] = OrderedDict()
_LLM_CACHE_LOCK = threading.Lock()


def _log_interaction(model: str, system: str, user: str, response: str, role_hint: str = "llm") -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = LOGS_DIR / f"{stamp}_{role_hint}.md"
    content = f"# LLM Interaction Log\n\n**Model**: {model}\n**Date**: {stamp}\n\n"
    content += f"## System Prompt\n\n```text\n{system}\n```\n\n"
    content += f"## User Prompt\n\n```text\n{user}\n```\n\n"
    content += f"## Response\n\n```text\n{response}\n```\n"
    log_file.write_text(content, encoding="utf-8")


def invoke_llm(
    model: str,
    system: str,
    user: str,
    *,
    llm_chat: Callable[..., Any] | None = None,
    num_predict: int = 2048,
    temperature: float = 0.35,
    use_cache: bool = False,
    role_hint: str = "llm",
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
    
    _log_interaction(model, system, user, out, role_hint)
    return out
