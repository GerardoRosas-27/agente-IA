# Verificación

## Automatizada

```bash
python -m harness.cli init
# equivalente: pytest tests/ -q
python -m harness.cli validate
```

## Manual antes de dar por cerrada una feature

1. Releer criterios de aceptación en `feature_list.json`.
2. Abrir `progress/review_<name>.md` y comprobar que el veredicto coincide con la realidad del código.
3. Ejecutar las pruebas o comandos que el implementador indicó en `progress/impl_<name>.md`.
4. Si el revisor usó criterios incorrectos, ajusta prompts en `harness/prompts.py` o documentación en `docs/` y vuelve a lanzar `run`.

## Integración LM Studio (opcional)

Con servidor en marcha:

```bash
set LLM_INTEGRATION_TEST=1
python -m pytest tests/test_llm_studio_integration.py -v
```
