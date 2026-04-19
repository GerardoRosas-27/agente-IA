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

Modo rapido (conectoma sintetico, sin internet, util para validar):

```bash
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

### Chat con el cerebro de la mosca (`chat_fly_app.py`)

Interfaz grafica (Tkinter): **tu texto** pasa por el LLM solo como **puente**
(JSON: hostil / amable / conversacion / curiosidad) y se inyecta en canales
sociales del mundo. **La linea tipo "murmullo"** la arma el cerebro con
plantillas a partir de tensores.

**Preguntas aprendidas:** la red plastica es mas grande (`--hidden` por
defecto 3200) y tiene dos cabezales extra: (1) una politica categorica sobre
un **lexicon fijo de temas** (49 semillas en español — lo aprendible es *cual*
tema se activa); (2) un **vector de instruccion** (28 numeros en tanh) que se
pasa al LLM para que *articule* una pregunta corta alrededor de ese tema. El
LLM **no inventa el tema**: lo elige la plasticidad. Cuando tu **siguiente**
mensaje llega, el tono del puente produce una recompensa `R` y se aplica
**REINFORCE** sobre la cabeza de preguntas (y el resto de pesos compartidos
via `fc1`).

```bash
python chat_fly_app.py
python chat_fly_app.py --hidden 4000 --llm-model gemma2:2b
```

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
| `fly_brain.py`             | Carga el conectoma como matriz de pesos fija (`FlyConnectomeBrain`). |
| `expansive_network.py`     | Red plastica en blanco (`ExpansiveNetwork`). |
| `experiment.py`            | Bucle de refuerzo + graficas. |
| `stats_simulation.py`      | Mundo simulado + dashboard terminal + opcion LLM interno. |
| `llm_reasoner.py`          | LLM local (Ollama) o heuristica; intenciones abstractas. |
| `chat_bridge.py`           | LLM clasifica texto → estímulos (no genera charla). |
| `chat_sim_session.py`      | Bucle simulacion + aprendizaje para el chat. |
| `chat_fly_app.py`          | Ventana de chat (Tkinter). |
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
