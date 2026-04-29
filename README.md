# Mosca hibrida: conectoma real + red plastica expansiva

Experimento que combina:

1. **El conectoma real de _Drosophila melanogaster_** publicado por el
   consorcio [FlyWire / Princeton Seung Lab (Nature 2024)](https://doi.org/10.1038/s41586-024-07558-y).
   Se usa el snapshot publico `v783` (~139k neuronas, ~55M sinapsis)
   como **red fija congelada**: los pesos sinapticos vienen de la
   biologia, no se entrenan. Es el "instinto" de la mosca.

2. **Una red expansiva en blanco** (`ExpansiveNetwork`) acoplada al
   conectoma. Empieza con pesos ~0 y aprende por **refuerzo** (REINFORCE
   con Adam) a asociar conceptos con acciones utiles. La pregunta del
   experimento es: **¿se puebla sola? ¿aparecen nuevas sinapsis
   funcionales a lo largo de la vida?**

3. (Opcional) **Ollama** como "maestro" externo en `experiment.py` que
   ocasionalmente sugiere una accion.

4. **Razonamiento interno con LLM pequeno (pesos congelados)** en
   `stats_simulation.py`: un modelo local (p. ej. Gemma en Ollama) emite
   intenciones abstractas (`buscar_comida`, `huir_peligro`, etc.) en JSON.
   Esas intenciones se codifican como vector extra de entrada de la red
   plastica y aplican un **sesgo fijo** sobre los logits de accion. El LLM
   **no se entrena** en linea; solo la capa plastica aprende a combinar
   instinto + razonamiento + mundo.

## Instalacion

```bash
pip install -r requirements.txt
```

Si quieres usar Ollama, instala [Ollama](https://ollama.com/) y baja modelos:

```bash
ollama pull phi3
ollama pull gemma3:270m
```

`gemma3:270m` es un Gemma pequeno adecuado para CPU; si no existe en tu
version de Ollama, prueba `gemma2:2b` o `gemma2:1b` y pasalo con
`--llm-model`.

## Uso

### Comandos principales

Instalar dependencias:

```bash
pip install -r requirements.txt
```

Crear configuracion local:

```bash
copy .env.example .env
```

Ejecutar la interfaz principal:

```bash
python multi_agent_app.py
```

Ejecutar con parametros del ciclo:

```bash
python multi_agent_app.py --max-cycles 5 --discuss 2 --execute 2 --test 2
python multi_agent_app.py --torch-threads 1 --replay-capacity 240
```

Abrir la app con modelo indicado por argumento:

```bash
python multi_agent_app.py --llm-model gemma3:270m
```

Arrancar el servidor LLM de prueba si LM Studio no responde:

```bash
python llm_test_api_server.py
```

Ejecutar pruebas del proyecto:

```bash
python -m unittest discover -s tests
```

Desde la UI principal puedes abrir `Configuración`, guardar `.env` y usar
`Crear nuevas herramientas` para generar, probar e instalar herramientas en
`skills/<nombre>/`.

Modo rapido (conectoma sintetico, sin internet, util para validar):

```bash
python multi_agent_app.py --llm-model gemma3:270m
python experiment.py --synthetic --no-llm
```

### Con el conectoma REAL de FlyWire (recomendado, ~72 MB en total)

El endpoint publico de FlyWire exige login con Google, asi que la descarga
100% automatica no funciona. Baja manualmente desde
[codex.flywire.ai/api/download](https://codex.flywire.ai/api/download)
**solo estos 3 archivos**:

| archivo de FlyWire | tamaño | para que sirve |
|---|---|---|
| Connections (Filtered)                     | 68 MB   | matriz sinaptica pre->post con `syn_count` |
| Neurotransmitter Type Predictions          | 1.7 MB  | signo excitatorio / inhibitorio (GABA, Gly, ACh, Glu, ...) |
| Classification / Hierarchical Annotations  | 934 KB  | region anatomica (lobulo optico, mushroom body, ...) |

Pasos:

1. Descarga los 3 archivos y ponlos en `./data/` (pueden quedar como `.gz`,
   el script se encarga).
2. Conviertelos al formato que usa el proyecto:

   ```bash
   python prepare_flywire_data.py
   ```

   Esto genera `data/neurons.csv` y `data/connections.csv` ya procesados
   (neurotransmisor + region anotados, aristas agrupadas).

3. Corre el experimento con el conectoma real:

   ```bash
   python experiment.py --episodes 300 --fly-neurons 5000
   ```

**NO descargues** Synapse Table (2.6 GB), Neuron Skeletons (13 GB),
Connections (Unfiltered, 277 MB), ni las versiones "Original Used Prior
To July 2025": no se usan en este experimento.

### Simulacion con mundo + estadisticas en terminal (`stats_simulation.py`)

```bash
python stats_simulation.py --max-steps 10000
python stats_simulation.py --llm --llm-model gemma3:270m --llm-every 30
```

Sin Ollama o sin el modelo instalado, el razonador cae en reglas heuristicas
equivalentes (mismo interfaz).

### PlasticSwarm / autonomía por objetivo (`multi_agent_app.py`)

**Sin conectoma ni mosca.** Entrada = **objetivo** (texto). Flujo fijo:
**Entiende** (único que ve el bruto) → **Planifica** → **Discuten** (N) →
**Ejecutan** (M) → **Prueban** (K) → **Revisor** (`OBJETIVO_ALCANZADO`,
`MOTIVO`, `RETROALIMENTACION`, `RESPUESTA_FINAL`). Si **NO**, nuevo ciclo con
retro inyectada al planificador. Por ciclo: escribe en `SharedFlyMemory` y al
cerrar el ciclo entrena una **red auxiliar** con el buffer y **vacía el buffer**.
Además mantiene una **replay compartida acotada** en SQLite: experiencias
episódicas, patrones procedimentales exitosos y memoria transactiva de qué rol
suele aportar mejor. Cada rol recupera solo un contexto corto antes de actuar,
para mejorar continuidad sin crecimiento indefinido de tokens/RAM.
El aprendizaje por ciclo minimiza **energía libre** (`sorpresa predictiva +
complejidad - entropía útil`) en memoria compartida, buffer auxiliar y red
plástica, para que el sistema aprenda sin colapsar a ceros ni crecer de forma
caótica. Incluye un disparo neuronal **cuántico-inspirado**: fase y posición
latentes detectan colisiones entre memoria y objetivo; cuando hay colapso, una
compuerta decide si dispara o no y esa lectura entra al loss.
Pesos y estado de optimizadores:
`data/plastic_swarm.sqlite` (guardado tras cada ciclo, tras cada run y al cerrar
ventana). Carga inicial en **hilo en segundo plano** (`weights_ready`).

```bash
python multi_agent_app.py
python multi_agent_app.py --llm-model gemma3:270m --max-cycles 5 --discuss 2 --execute 2 --test 2
python multi_agent_app.py --torch-threads 1 --replay-capacity 240
```

#### LLM Studio (u otro servidor OpenAI-compatible) por API

1. Copia `.env.example` a `.env` en la raíz del repo.
2. En LM Studio, arranca el servidor local y copia la URL base (suele ser `http://127.0.0.1:1234/v1`).
3. En `.env` define al menos:
   - `LLM_API_BASE_URL` — esa URL base (con `/v1`).
   - `LLM_MODEL` — el identificador del modelo que muestra LM Studio para la API.

Si `LLM_API_BASE_URL` está definido, **no** se usa el daemon Ollama para esta app; las llamadas van a `…/v1/chat/completions`. Opcional: `LLM_API_KEY`, `LLM_HTTP_TIMEOUT`.

**Si LM Studio no responde**, el cliente reintenta por defecto contra una API de prueba local (`LLM_TEST_API_BASE_URL`, por defecto `http://127.0.0.1:8765/v1`). Arranca el sustituto en otra terminal:

```bash
python llm_test_api_server.py
```

Desactivar el reintento: `LLM_TEST_FALLBACK=0` en `.env`.

`python chat_fly_app.py` redirige al mismo programa (compatibilidad).

Dependencias **opcionales** (LangGraph / LangChain, no usadas por esta app):

```bash
pip install -r requirements-optional.txt
```

El experimento con mosca (`experiment.py`, `chat_sim_session.py`, conectoma)
sigue en el repo pero **no** es la ventana principal de chat.

### Mas opciones

```bash
python experiment.py --episodes 500 --hidden 3000 --fly-neurons 3000
python experiment.py --no-llm                # sin Ollama
python experiment.py --synthetic             # forzar conectoma sintetico
```

## Archivos del proyecto

| archivo | descripcion |
|---|---|
| `download_connectome.py`   | Intenta bajar FlyWire o genera un conectoma sintetico de respaldo. |
| `prepare_flywire_data.py`  | Convierte los CSV crudos de FlyWire al formato del proyecto. |
| `fly_brain.py`             | `FlyConnectomeBrain`: conectoma fijo o modo `--blank-brain` (W aprendible). |
| `expansive_network.py`     | Red plastica en blanco (`ExpansiveNetwork`). |
| `experiment.py`            | Bucle de refuerzo + graficas. |
| `stats_simulation.py`      | Mundo simulado + dashboard terminal + opcion LLM interno. |
| `llm_reasoner.py`          | LLM local (Ollama) o heuristica; intenciones abstractas. |
| `chat_bridge.py`           | LLM clasifica texto → estímulos (no genera charla). |
| `chat_sim_session.py`      | Bucle simulacion + aprendizaje (mosca; no es la app principal). |
| `multi_agent_app.py`       | Chat Tk solo multi-agente + memoria en blanco (entrada principal). |
| `chat_fly_app.py`          | Delega en `multi_agent_app`. |
| `unified_fly_memory.py`    | Memoria compartida (`blank_init` opcional). |
| `multi_agent_orchestrator.py` | LLM local + heurística + utilidades (embed, loss). |
| `objective_agent_cycle.py`   | Pipeline + probadores + revisor + buffer→aux. |
| `plastic_swarm_state.py`     | Buffer de ciclo, red auxiliar, SQLite persistencia. |
| `llm_api_client.py`          | LM Studio / API OpenAI-compatible + fallback a API de prueba. |
| `llm_test_api_server.py`     | Servidor HTTP mínimo (stdlib) si LM Studio no responde. |
| `fly_voice.py`             | Frases del “cerebro” a partir de tensores (sin LLM). |
| `data/`                    | CSV del conectoma. |
| `results.png`              | Graficas generadas al final. |

## Metricas que miramos

- **Recompensa por episodio** (aprende o no).
- **% de sinapsis activas** en la red expansiva (|w| > 1e-3). Es la
  **"poblacion" de la red en blanco**.
- **Magnitud media y maxima de los pesos** en escala log, para ver
  crecimiento de conexiones.
- **Histograma final de pesos**: si parte gaussiana centrada en 0 y
  termina long-tail, es evidencia de que algunas sinapsis se volvieron
  dominantes, como en el cerebro real.

## Notas tecnicas importantes

- La version original del concepto inicializaba todos los pesos con
  **exactamente 0** y usaba `ReLU`. Eso produce gradiente cero y la red
  **no puede aprender nunca**. Aqui usamos un ruido gaussiano diminuto
  (`std=1e-4`) + `LeakyReLU`: funcionalmente esta en blanco pero los
  gradientes si fluyen, asi que podemos medir honestamente si
  aparecen sinapsis fuertes.
- Optimizador `Adam` con clipping (mas estable que SGD para RL policy
  gradient).
- La matriz sinaptica completa de FlyWire es grande; por defecto
  submuestreamos a ~2500 neuronas (`--fly-neurons`). Puedes subirlo si
  tienes RAM/VRAM.
- Las neuronas GABA / glicinergicas se inyectan con signo negativo
  (inhibitorias), las demas con signo positivo.

## Referencia cientifica

Dorkenwald, S., Matsliah, A., Sterling, A. R. et al. _Neuronal wiring
diagram of an adult brain._ **Nature** 634, 124-138 (2024).
