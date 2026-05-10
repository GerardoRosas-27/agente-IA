from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import harness.embeddings as embeddings
from harness.embeddings import cosine_similarity, get_embedding, is_embeddings_endpoint_configured


def _mock_response(vector: list[float]) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"data": [{"embedding": vector}]}
    return r


def test_cosine_similarity_basic() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_get_embedding_returns_none_without_model_env(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    with patch.dict(
        "os.environ",
        {"LLM_API_BASE_URL": "http://x/v1", "LLM_EMBEDDING_MODEL": ""},
        clear=True,
    ):
        assert get_embedding("hola", db_path=db) is None
        assert is_embeddings_endpoint_configured() is False


def test_get_embedding_calls_endpoint_and_caches(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    env = {
        "LLM_API_BASE_URL": "http://localhost:1234/v1",
        "LLM_EMBEDDING_MODEL": "bge-small",
    }
    with patch.dict("os.environ", env, clear=True):
        with patch.object(
            embeddings.requests, "post", return_value=_mock_response([0.1, 0.2, 0.3])
        ) as post:
            first = get_embedding("hola mundo", db_path=db)
            assert first is not None
            assert first.dim == 3
            assert first.vector == (0.1, 0.2, 0.3)
            assert first.model == "bge-small"
            assert post.call_count == 1

            # Segunda llamada con mismo texto: debe servirse del cache.
            second = get_embedding("hola mundo", db_path=db)
            assert second is not None
            # Tolerancia float32: el cache empaca como struct 'f' (4 bytes).
            for a, b in zip(second.vector, first.vector):
                assert abs(a - b) < 1e-6
            assert post.call_count == 1, "el cache no se respetó"


def test_get_embedding_returns_none_on_connection_error(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    env = {
        "LLM_API_BASE_URL": "http://localhost:1234/v1",
        "LLM_EMBEDDING_MODEL": "bge-small",
    }
    with patch.dict("os.environ", env, clear=True):
        with patch.object(
            embeddings.requests, "post", side_effect=requests.ConnectionError("boom")
        ):
            assert get_embedding("hola", db_path=db) is None


def test_semantic_recall_v2_falls_back_to_tfidf_without_endpoint(tmp_path: Path) -> None:
    """Si LLM_EMBEDDING_MODEL no está, semantic_recall_v2 debe usar TF-IDF (no fallar)."""
    from harness.shared_memory import remember, semantic_recall_v2

    db = tmp_path / "memory.db"
    remember(
        "trajectory",
        "webhook_fix",
        "Reparado parser webhook WhatsApp media",
        tags=["whatsapp", "webhook"],
        db_path=db,
    )

    with patch.dict("os.environ", {}, clear=True):
        results = semantic_recall_v2(query="error webhook whatsapp", db_path=db, limit=3)

    assert results
    assert results[0].key == "webhook_fix"
