"""Embeddings locales vía un endpoint OpenAI-compatible (LM Studio, vLLM, etc).

Cuando LM Studio carga un modelo embedding (bge-small, all-MiniLM, e5-small...)
expone `/v1/embeddings`. Este módulo lo consume y cachea los vectores en
SQLite para reuso entre sesiones.

Si el endpoint no está disponible o devuelve error, el módulo retorna `None` y
los call sites deben hacer fallback a TF-IDF (`semantic_recall_v2` ya lo hace).
Esto mantiene el harness funcional offline incluso si solo está cargado un
modelo de chat.

El nombre del modelo embedding se resuelve por (en orden):
  1. argumento explícito,
  2. env var `LLM_EMBEDDING_MODEL`,
  3. None → fallback.

Inspirado en el paper "Episodic Memory is the Missing Piece for Long-Term LLM
Agents" (arXiv:2502.06975): vector memory mejora dramáticamente recall vs
sliding window/LIKE en presencia de distractores.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import requests

from harness.paths import STATE_DB_PATH


@dataclass(frozen=True)
class EmbeddingResult:
    text: str
    vector: tuple[float, ...]
    dim: int
    model: str


def _embedding_db_init(db_path: Path = STATE_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                text_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                vector BLOB NOT NULL,
                dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (text_hash, model)
            )
            """
        )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack_vector(blob: bytes, dim: int) -> tuple[float, ...]:
    return struct.unpack(f"{dim}f", blob)


def _cache_get(
    text: str, model: str, *, db_path: Path = STATE_DB_PATH
) -> EmbeddingResult | None:
    _embedding_db_init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT vector, dim FROM embedding_cache WHERE text_hash=? AND model=?",
            (_hash_text(text), model),
        ).fetchone()
    if not row:
        return None
    blob, dim = row
    try:
        vector = _unpack_vector(blob, int(dim))
    except struct.error:
        return None
    return EmbeddingResult(text=text, vector=vector, dim=int(dim), model=model)


def _cache_put(
    result: EmbeddingResult, *, db_path: Path = STATE_DB_PATH
) -> None:
    from datetime import datetime

    _embedding_db_init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO embedding_cache (text_hash, model, vector, dim, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _hash_text(result.text),
                result.model,
                _pack_vector(result.vector),
                result.dim,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def _resolve_embedding_endpoint(*, base_url: str | None = None) -> tuple[str, str]:
    """Devuelve (base_url, api_key) usando perfil default si no se especifica."""
    if base_url:
        return base_url.rstrip("/"), (os.getenv("LLM_API_KEY") or "").strip()
    try:
        # Reutiliza la resolución central del cliente LLM. Esto carga `.env`,
        # aplica defaults y respeta perfiles sin duplicar parsing de config.
        from llm_api_client import resolve_llm_profile

        profile = resolve_llm_profile()
        return profile.base_url, profile.api_key
    except Exception:
        pass
    base = (os.getenv("LLM_API_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    return base, api_key


def _resolve_embedding_model(explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    try:
        # Carga `.env` mediante la misma ruta que el cliente LLM principal.
        from llm_api_client import resolve_llm_profile

        resolve_llm_profile()
    except Exception:
        pass
    env_model = (os.getenv("LLM_EMBEDDING_MODEL") or "").strip()
    if env_model:
        return env_model
    return None


def get_embedding(
    text: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 30,
    use_cache: bool = True,
    db_path: Path = STATE_DB_PATH,
) -> EmbeddingResult | None:
    """Pide un embedding al endpoint OpenAI-compatible. Cachea por (texto, modelo).

    Devuelve `None` si no hay endpoint, no hay modelo embedding configurado o
    falla la llamada. El llamador debe hacer fallback a TF-IDF.
    """
    text = text.strip()
    if not text:
        return None
    resolved_model = _resolve_embedding_model(model)
    if not resolved_model:
        return None
    base, api_key = _resolve_embedding_endpoint(base_url=base_url)
    if not base:
        return None

    if use_cache:
        cached = _cache_get(text, resolved_model, db_path=db_path)
        if cached is not None:
            return cached

    url = f"{base}/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": resolved_model, "input": text}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None

    items = data.get("data") if isinstance(data, dict) else None
    if not items or not isinstance(items, list):
        return None
    first = items[0] if isinstance(items[0], dict) else None
    if not first:
        return None
    raw_vector = first.get("embedding")
    if not isinstance(raw_vector, list) or not raw_vector:
        return None
    try:
        vector = tuple(float(x) for x in raw_vector)
    except (TypeError, ValueError):
        return None
    result = EmbeddingResult(text=text, vector=vector, dim=len(vector), model=resolved_model)
    if use_cache:
        try:
            _cache_put(result, db_path=db_path)
        except (sqlite3.Error, OSError):
            pass
    return result


def embed_batch(
    texts: Iterable[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60,
    use_cache: bool = True,
    db_path: Path = STATE_DB_PATH,
) -> list[EmbeddingResult | None]:
    """Embeddings de varios textos. Hace una llamada por texto (algunos servidores
    no aceptan input batch); aprovecha el cache para evitar repetidos."""
    return [
        get_embedding(text, model=model, base_url=base_url, timeout=timeout, use_cache=use_cache, db_path=db_path)
        for text in texts
    ]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine entre dos vectores. Tolera longitudes distintas usando el mínimo."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    norm_a = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    norm_b = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


def is_embeddings_endpoint_configured() -> bool:
    """Indica si hay un modelo embedding configurado y un base_url."""
    return bool(_resolve_embedding_model(None) and _resolve_embedding_endpoint()[0])
