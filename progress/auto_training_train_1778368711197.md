# Autoentrenamiento · Entrenamiento: buscar información actualizada en internet y entregar resultados estructurados

- task_id: `train_1778368711197`
- objetivo: buscar información actualizada en internet y entregar resultados estructurados
- decisión: `REUSE_EXISTING_SKILL`
- memoria: `train_1778368711197`

## Razonamiento
La skill `internet_search_api` parece cubrir la tarea con score=0.39. El ciclo debe reutilizarla y crear solo pegamento/adaptadores si falta conexión.

## Herramientas candidatas
- `internet_search_api`
- `whatsapp_connector`
- `arithmetic_calculator`
- `wa_business_api_client`

## Tareas
- Analizar el objetivo y extraer intención, entradas y salida esperada.
- Consultar memoria compartida y recomendaciones con energía libre.
- Elegir reutilizar, extender o crear según menor energía libre y cobertura.
- Ejecutar o simular el flujo mínimo y guardar resultado del aprendizaje.

## Subtareas
- Leer `skills/internet_search_api.md` y ubicar su punto de entrada.
- Probar invocación mínima de `internet_search_api` con datos representativos.
- Diseñar el adaptador más pequeño posible para conectar entradas/salidas.
- Verificar que no se creó una skill duplicada con responsabilidad equivalente.

## Si falta capacidad, crear
- Crear solo adaptador de entrada/salida si la skill no encaja directamente.
- Crear pruebas del adaptador; no duplicar la lógica de la skill base.

## Solución de código sugerida
```python
# Adaptador sugerido por autoaprendizaje; ajustar nombres/argumentos al caso real.
from importlib import import_module

skill = import_module('skills.internet_search_api')

def execute(request: dict):
    if hasattr(skill, 'run'):
        return skill.run(request)
    raise RuntimeError('La skill recomendada no expone run(request).')

# Objetivo entrenado: buscar información actualizada en internet y entregar resultados estructurados
```
