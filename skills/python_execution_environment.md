# python_execution_environment

## Uso

Skill de entorno de ejecucion Python controlado por el sistema agentico.

Permite:

- escribir programas Python dentro de `progress/python_execution_environment/programs`;
- ejecutarlos en primer plano o segundo plano;
- consultar estado por `run_id`;
- detener ejecuciones;
- leer logs desde `progress/python_execution_environment/logs`;
- abrir una ventana Tk secundaria de solo visualizacion desde la app.

## API estructurada

```python
from skills.python_execution_environment import run

run({
    "action": "write",
    "name": "demo.py",
    "source": "print('hola')",
})

result = run({
    "action": "run",
    "name": "demo.py",
    "timeout": 10,
})
```

Acciones soportadas:

- `write`: requiere `name` y `source`.
- `start`: arranca en segundo plano y devuelve `run_id`.
- `run`: ejecuta y espera finalizacion.
- `status`: requiere `run_id`.
- `stop`: requiere `run_id`.
- `log`: requiere `run_id`.
- `list`: lista ejecuciones recientes.

## Visualizacion

La app principal muestra una ventana secundaria con los runs y logs. Por ahora es
solo lectura: el usuario puede observar, pero no intervenir ni ejecutar comandos.

## Nota de seguridad

Este entorno no es una sandbox fuerte. Ejecuta Python con los permisos del proceso
actual, pero evita `shell=True`, limita los archivos al workspace del entorno y
centraliza logs para auditoria.
