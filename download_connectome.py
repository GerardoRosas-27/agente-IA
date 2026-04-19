"""
Descarga el conectoma del cerebro de Drosophila melanogaster (mosca de la fruta)
publicado por el consorcio FlyWire / Princeton Seung Lab.

Fuente primaria:
    https://codex.flywire.ai/api/download  (snapshot publico v783)

Referencia:
    Dorkenwald et al. (2024) "Neuronal wiring diagram of an adult brain", Nature.
    https://doi.org/10.1038/s41586-024-07558-y

Si la descarga falla (sin internet, cambio de API, etc.) generamos un
conectoma SINTETICO con estadisticas similares a las reportadas por FlyWire
(escala libre, dispersa, E/I aprox. 70/30, long-tail weights).
Esto permite que el experimento corra siempre.
"""
from __future__ import annotations

import gzip
import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NEURONS_FILE = DATA_DIR / "neurons.csv"
CONNECTIONS_FILE = DATA_DIR / "connections.csv"

FLYWIRE_URLS = [
    "https://codex.flywire.ai/api/download?data_product=connections&data_version=783",
    "https://codex.flywire.ai/api/download?data_product=neurons&data_version=783",
]


def _download(url: str, dest: Path, timeout: int = 30) -> bool:
    """Intenta descargar un archivo mostrando progreso. Devuelve True si tuvo exito."""
    try:
        print(f"  -> GET {url}")
        with requests.get(url, stream=True, timeout=timeout) as r:
            if r.status_code != 200:
                print(f"     respuesta HTTP {r.status_code}, se omite.")
                return False
            total = int(r.headers.get("content-length", 0))
            buf = io.BytesIO()
            pbar = tqdm(total=total, unit="B", unit_scale=True, disable=total == 0)
            for chunk in r.iter_content(chunk_size=1 << 15):
                if chunk:
                    buf.write(chunk)
                    pbar.update(len(chunk))
            pbar.close()
            raw = buf.getvalue()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            dest.write_bytes(raw)
            return True
    except Exception as exc:
        print(f"     fallo: {exc}")
        return False


def try_download_flywire() -> bool:
    """Intenta bajar la version 783 publica del conectoma FlyWire."""
    print("Intentando descargar conectoma real FlyWire v783...")
    conn_ok = _download(FLYWIRE_URLS[0], CONNECTIONS_FILE)
    neur_ok = _download(FLYWIRE_URLS[1], NEURONS_FILE)
    return conn_ok and neur_ok


def generate_synthetic_fly_connectome(
    n_neurons: int = 5000,
    avg_out_degree: int = 40,
    excitatory_ratio: float = 0.7,
    seed: int = 1234,
) -> None:
    """
    Genera un conectoma biologicamente plausible con estadisticas similares
    a las reportadas para Drosophila:

      - neuronas tipicamente proyectan a decenas/centenas de otras neuronas
      - distribucion de pesos long-tail (lognormal)
      - mezcla de neuronas excitadoras e inhibidoras
      - conectividad dispersa (<<1%)
      - algunas neuronas "hub" con muchas proyecciones (scale-free)
    """
    print(
        f"Generando conectoma sintetico plausible: {n_neurons} neuronas, "
        f"grado medio ~{avg_out_degree}"
    )
    rng = np.random.default_rng(seed)

    types = rng.choice(
        ["excitatory", "inhibitory"],
        size=n_neurons,
        p=[excitatory_ratio, 1 - excitatory_ratio],
    )
    regions = rng.choice(
        ["optic_lobe", "mushroom_body", "central_complex", "antennal_lobe",
         "lateral_horn", "subesophageal", "ventral_cord"],
        size=n_neurons,
    )

    neurons = pd.DataFrame(
        {
            "root_id": np.arange(n_neurons, dtype=np.int64),
            "nt_type": types,
            "region": regions,
        }
    )
    neurons.to_csv(NEURONS_FILE, index=False)

    hub_scores = rng.pareto(1.5, size=n_neurons) + 1.0
    hub_scores = hub_scores / hub_scores.sum()

    out_degrees = rng.poisson(avg_out_degree, size=n_neurons).clip(min=1)

    pre_list, post_list, syn_list, nt_list = [], [], [], []
    for pre in tqdm(range(n_neurons), desc="  sinapsis"):
        k = int(out_degrees[pre])
        posts = rng.choice(n_neurons, size=k, replace=False, p=hub_scores)
        posts = posts[posts != pre]
        if len(posts) == 0:
            continue
        syn_counts = np.round(rng.lognormal(mean=1.0, sigma=0.9, size=len(posts))).astype(int)
        syn_counts = syn_counts.clip(min=1, max=200)
        pre_list.append(np.full(len(posts), pre, dtype=np.int64))
        post_list.append(posts.astype(np.int64))
        syn_list.append(syn_counts)
        nt_list.append(np.full(len(posts), types[pre]))

    connections = pd.DataFrame(
        {
            "pre_root_id": np.concatenate(pre_list),
            "post_root_id": np.concatenate(post_list),
            "syn_count": np.concatenate(syn_list),
            "nt_type": np.concatenate(nt_list),
        }
    )
    connections.to_csv(CONNECTIONS_FILE, index=False)
    print(
        f"  conectoma sintetico listo: {len(connections):,} sinapsis sobre "
        f"{n_neurons:,} neuronas"
    )


def ensure_connectome(force_synthetic: bool = False, n_neurons: int = 5000) -> None:
    """Asegura que `data/neurons.csv` y `data/connections.csv` existan."""
    if NEURONS_FILE.exists() and CONNECTIONS_FILE.exists():
        print("Conectoma ya presente en ./data, se omite descarga.")
        return

    if not force_synthetic and try_download_flywire():
        print("Descarga FlyWire completada.")
        return

    print("No se pudo descargar el conectoma real. Usando fallback sintetico.")
    generate_synthetic_fly_connectome(n_neurons=n_neurons)


if __name__ == "__main__":
    force_syn = "--synthetic" in sys.argv
    n = 5000
    for arg in sys.argv:
        if arg.startswith("--n="):
            n = int(arg.split("=")[1])
    ensure_connectome(force_synthetic=force_syn, n_neurons=n)
    print("\nListo. Archivos:")
    for f in (NEURONS_FILE, CONNECTIONS_FILE):
        if f.exists():
            print(f"  {f}  ({os.path.getsize(f)/1e6:.2f} MB)")
