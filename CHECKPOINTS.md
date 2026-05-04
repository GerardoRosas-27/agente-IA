# Checkpoints — estado “correcto” del repo

El revisor del harness usa esta lista como referencia rápida.

1. **`python -m harness.cli validate`** — `feature_list.json` respeta reglas (una sola `in_progress`, estados válidos).
2. **`init.ps1` / `init.sh`** — `pytest` en verde para los tests incluidos en el repo.
3. **LM Studio** — `.env` con `LLM_API_BASE_URL` (termina en `/v1`) y `LLM_MODEL` coherentes con el servidor local.
4. **Trazabilidad** — cada ciclo deja `progress/impl_<name>.md` y `progress/review_<name>.md` enlazables desde `history.md`.
5. **Sin secretos en el diff** — claves y tokens solo en `.env` (no versionado).
