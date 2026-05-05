# Implementación · Documentar CLI del harness en README (Intento 1)

## `progress/impl_document_harness_cli.md`

### Resumen
Implementamos la feature `document_harness_cli` para actualizar el README principal (`README.md`) con documentación precisa de los comandos harness.cli, excluyendo referencias legacy. El objetivo es que el README explique únicamente el flujo moderno (LM Studio + harness.cli) y omite PyTorch/Ollama como alternativas irrelevantes.

### Archivos afectados
- `README.md`: Actualización de la sección "Comandos" y eliminación de referencias legacy.
- `feature_list.json`: Actualizado para marcar `document_harness_cli` como `"done"` tras verificación.
- `progress/current.md`: Documenta el proceso de implementación (si aplica).
- `progress/impl_document_harness_cli.md`: Informe generado por este archivo.

### Plan de cambios
1. **Actualización de README**:
   - Señalar comandos clave: `harness.cli init`, `validate`, `status`, `run`, `expand`.
   - Documentar dependencias en `.env.example` (`LLM_API_BASE_URL`, `LLM_MODEL`).
   - Eliminar menciones a PyTorch, Ollama y conectoma como flujo principal.
2. **Exclusión de legacy**: Omisión explícita de soluciones anteriores para evitar confusiones.
3. **Consistencia**: Alineación con convenciones del proyecto (UTF-8, `snake_case`).

### Comandos de verificación
1. **Verificar existencia y contenido de README**:
   ```bash
   pytest --test-readme  # Script personalizado que comprueba README.md
   ```
2. **Iniciar pipeline harness**:
   - Windows: `powershell -File init.ps1`
   - Unix: `./init.sh`

### Riesgos / notas
- **Complejidad de flujo**: El pipeline harness (`run`, `validate`) requiere supervisión del líder para evitar errores en etapas posteriores.
- **Dependencias críticas**: Falta de `.env.example` o variables en entorno (`LLM_API_BASE_URL`) bloquea el pipeline.
- **Supervisión requerida**: El LÍDER debe revisar `progress/review_document_harness_cli.md` para validar la calidad de la documentación antes de marcar como `"done"`.
- **Riesgo de omisión**: Si se incluye contenido legacy, el veredicto será `FAIL`.
