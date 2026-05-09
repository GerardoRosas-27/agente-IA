# node_execution_environment

## Uso

Skill de entorno Node.js controlado por el sistema agentico.

Permite:

- escribir scripts Node.js dentro de `progress/node_execution_environment/scripts`;
- ejecutar scripts en primer plano o segundo plano;
- arrancar sesiones debug con `node --inspect`;
- consultar estado por `run_id`;
- detener procesos;
- leer logs desde `progress/node_execution_environment/logs`;
- abrir una ventana Tk secundaria de solo visualizacion desde la app.

## API estructurada

```python
from skills.node_execution_environment import run

run({
    "action": "write",
    "name": "demo.js",
    "source": "console.log('hola desde node')",
})

result = run({
    "action": "run",
    "name": "demo.js",
    "timeout": 10,
})
```

Acciones soportadas:

- `write`: requiere `name` y `source`.
- `start`: arranca en segundo plano y devuelve `run_id`.
- `run`: ejecuta y espera finalizacion.
- `debug`: arranca con inspector de Node (`--inspect=127.0.0.1:0`).
- `status`: requiere `run_id`.
- `stop`: requiere `run_id`.
- `log`: requiere `run_id`.
- `list`: lista ejecuciones recientes.

## Debug

```python
debug_run = run({
    "action": "debug",
    "name": "server.js",
    "break_on_start": False,
})
```

El log captura la URL del inspector (`ws://...`) emitida por Node. El sistema
agentico puede usar esa informacion para conectar herramientas de debug en el
futuro. Por ahora la ventana secundaria solo visualiza estado y logs.

## Nota de seguridad

Este entorno no es una sandbox fuerte. Ejecuta Node.js con los permisos del
proceso actual, pero evita `shell=True`, limita scripts al workspace del entorno
y centraliza logs para auditoria.
