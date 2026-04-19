"""
Convierte los CSVs crudos que publica FlyWire Codex (snapshot v783)
al formato simple que usa el resto del proyecto:

    data/neurons.csv      columnas: root_id, nt_type, region
    data/connections.csv  columnas: pre_root_id, post_root_id, syn_count, nt_type

Archivos de entrada esperados (todos comprimidos .gz, que es como los
sirve el portal https://codex.flywire.ai/api/download):

    data/connections_princeton.csv.gz    (68 MB, "Connections (Filtered)")
    data/neurons.csv.gz                  (1.7 MB, "Neurotransmitter Type Predictions")
    data/classification.csv.gz           (934 KB, "Classification / Hierarchical Annotations")

Opcionales (se usan si estan, enriquecen el campo 'region'):
    data/consolidated_cell_types.csv.gz  (902 KB, "Cell Types")
    data/names.csv.gz                    (1.2 MB, "Proofread Cell Names And Groups")

Uso:
    python prepare_flywire_data.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"


SUPER_CLASS_TO_REGION = {
    "optic":              "optic_lobe",
    "visual_projection":  "optic_lobe",
    "visual_centrifugal": "optic_lobe",
    "olfactory":          "antennal_lobe",
    "mushroom_body":      "mushroom_body",
    "mb_kenyon_cell":     "mushroom_body",
    "kenyon":             "mushroom_body",
    "central":            "central_complex",
    "central_brain":      "central_complex",
    "lateral_horn":       "lateral_horn",
    "lh":                 "lateral_horn",
    "sez":                "subesophageal",
    "subesophageal":      "subesophageal",
    "descending":         "ventral_cord",
    "ascending":          "ventral_cord",
    "motor":              "ventral_cord",
    "sensory":            "antennal_lobe",
    "endocrine":          "central_complex",
}


def _region_from_row(row: pd.Series) -> str:
    """Mapea las columnas de clasificacion a una region anatomica gruesa."""
    for col in ("super_class", "class", "sub_class", "cell_type"):
        val = row.get(col)
        if isinstance(val, str):
            low = val.lower()
            for key, region in SUPER_CLASS_TO_REGION.items():
                if key in low:
                    return region
    return "central_complex"


def _find_gz(stems: list[str]) -> Path | None:
    """Busca archivos .csv.gz. Solo gz para evitar confundirse con los
    csv procesados que este script escribe."""
    for stem in stems:
        p = DATA_DIR / f"{stem}.csv.gz"
        if p.exists():
            return p
    return None


def _read(path: Path) -> pd.DataFrame:
    print(f"    leyendo {path.name} ({path.stat().st_size/1e6:.1f} MB) ...")
    return pd.read_csv(path, compression="gzip", low_memory=False)


def prepare() -> None:
    print("Buscando CSVs crudos de FlyWire en ./data ...\n")

    conn_path = _find_gz(["connections_princeton", "connections"])
    nt_path   = _find_gz(["neurons", "neurotransmitters",
                          "neurotransmitter_type_predictions"])
    cls_path  = _find_gz(["classification"])

    cell_types_path = _find_gz(["consolidated_cell_types", "cell_types"])
    names_path      = _find_gz(["names"])

    missing = []
    if conn_path is None: missing.append("Connections (Filtered)")
    if nt_path   is None: missing.append("Neurotransmitter Type Predictions")
    if cls_path  is None: missing.append("Classification / Hierarchical Annotations")
    if missing:
        print("FALTAN archivos (.csv.gz): " + ", ".join(missing))
        print("Descargalos desde https://codex.flywire.ai/api/download y")
        print("dejalos en ./data/ sin descomprimir.")
        return

    print(f"  connections   -> {conn_path.name}")
    print(f"  neurotrans.   -> {nt_path.name}")
    print(f"  classif.      -> {cls_path.name}")
    if cell_types_path:
        print(f"  cell_types    -> {cell_types_path.name}  (opcional)")
    if names_path:
        print(f"  names         -> {names_path.name}  (opcional)")
    print()

    print("Procesando neuronas + clasificacion + neurotransmisores:")
    cls = _read(cls_path)
    if "root_id" not in cls.columns:
        cls = cls.rename(columns={cls.columns[0]: "root_id"})

    nt = _read(nt_path)
    if "root_id" not in nt.columns:
        nt = nt.rename(columns={nt.columns[0]: "root_id"})

    nt_col = next(
        (c for c in ("top_nt", "nt_type", "predicted_nt", "nt")
         if c in nt.columns),
        None,
    )
    if nt_col is None:
        for c in ("ach_avg", "gaba_avg", "glut_avg", "oct_avg", "ser_avg", "da_avg"):
            if c not in nt.columns:
                break
        else:
            avg_cols = ["ach_avg", "gaba_avg", "glut_avg", "oct_avg", "ser_avg", "da_avg"]
            nt_names = ["ach", "gaba", "glut", "oct", "ser", "da"]
            nt["nt_type"] = [nt_names[i] for i in nt[avg_cols].values.argmax(axis=1)]
            nt_col = "nt_type"
    if nt_col is None:
        print(f"  AVISO: no puedo inferir neurotransmisor, uso 'ach'. Columnas: {nt.columns.tolist()}")
        nt["nt_type"] = "ach"
        nt_col = "nt_type"
    else:
        nt = nt.rename(columns={nt_col: "nt_type"})
        nt_col = "nt_type"

    if cell_types_path is not None:
        ct = _read(cell_types_path)
        if "root_id" not in ct.columns:
            ct = ct.rename(columns={ct.columns[0]: "root_id"})
        keep = [c for c in ("cell_type", "hemibrain_type") if c in ct.columns]
        if keep:
            cls = cls.merge(ct[["root_id"] + keep], on="root_id",
                            how="left", suffixes=("", "_ct"))

    cls["region"] = cls.apply(_region_from_row, axis=1)

    neurons = cls[["root_id", "region"]].merge(
        nt[["root_id", "nt_type"]], on="root_id", how="left"
    )
    neurons["nt_type"] = neurons["nt_type"].fillna("ach").astype(str)

    out_neurons = DATA_DIR / "neurons.csv"
    neurons.to_csv(out_neurons, index=False)
    print(f"  -> {out_neurons.name}  ({len(neurons):,} neuronas)")
    print("     distribucion de regiones:")
    for r, c in neurons["region"].value_counts().items():
        print(f"       {r:20s}  {c:>7,}")
    print("     distribucion NT:")
    for r, c in neurons["nt_type"].value_counts().head(10).items():
        print(f"       {r:20s}  {c:>7,}")
    print()

    print("Procesando conexiones (puede tardar ~1 min):")
    conn = _read(conn_path)
    rename = {}
    for cand in ("pre_root_id", "pre_pt_root_id", "presynaptic_root_id", "pre"):
        if cand in conn.columns:
            rename[cand] = "pre_root_id"; break
    for cand in ("post_root_id", "post_pt_root_id", "postsynaptic_root_id", "post"):
        if cand in conn.columns:
            rename[cand] = "post_root_id"; break
    for cand in ("syn_count", "synapse_count", "count", "n_syn"):
        if cand in conn.columns:
            rename[cand] = "syn_count"; break
    conn = conn.rename(columns=rename)

    needed = {"pre_root_id", "post_root_id", "syn_count"}
    if not needed.issubset(conn.columns):
        print(f"ERROR: columnas encontradas: {conn.columns.tolist()}")
        return

    print(f"    agregando {len(conn):,} filas por (pre,post) ...")
    conn = (
        conn.groupby(["pre_root_id", "post_root_id"], as_index=False)["syn_count"]
        .sum()
    )

    conn = conn.merge(
        neurons[["root_id", "nt_type"]].rename(columns={"root_id": "pre_root_id"}),
        on="pre_root_id",
        how="left",
    )
    conn["nt_type"] = conn["nt_type"].fillna("ach").astype(str)

    out_conn = DATA_DIR / "connections.csv"
    conn[["pre_root_id", "post_root_id", "syn_count", "nt_type"]].to_csv(
        out_conn, index=False
    )
    print(f"  -> {out_conn.name}  ({len(conn):,} aristas sinapticas)")

    print("\nListo. Ahora puedes correr:")
    print("    python experiment.py --episodes 300 --fly-neurons 5000")


if __name__ == "__main__":
    prepare()
