# Skills del sistema

Cada skill vive como un archivo Python dentro de esta carpeta.

Para que el sistema pueda usarla correctamente:

- El archivo `*.py` contiene el código ejecutable o importable.
- El archivo `*.md` con el mismo nombre contiene instrucciones de uso para el planificador y el implementador.
- El estado habilitada/deshabilitada se guarda en `progress/harness_state.db`.
- La interfaz "Administrar Skills" permite activar o desactivar skills sin borrar código.

Si agregas una nueva skill manualmente, crea también su archivo de instrucciones o abre la ventana de administración para que el sistema genere uno básico.

## Skills importadas/adaptadas

- `agent_engineering_workflows`: wrapper local inspirado en `addyosmani/agent-skills` para seleccionar workflows de especificación, planificación, implementación incremental, TDD, debugging, review, seguridad, performance, documentación y lanzamiento.
