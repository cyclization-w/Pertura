"""Outside references (docs/adr/0051, D9): published Perturb-seq screens and protein complexes, kept offline on the
analysis server and read as prior knowledge, never as this screen's evidence.

- Replogle et al. 2022 (Cell 185:2559; Figshare+ 20029387): the normalized pseudo-bulk profiles of the K562
  essential-scale, K562 genome-wide and RPE1 essential-scale CRISPRi screens (gemgroup Z-normalized expression per
  perturbation, genes over 0.01 UMI per cell), with each perturbation's own-gene knockdown and cells.
- CORUM (Helmholtz Munich, Zenodo 17419058): mammalian protein complexes from single-protein experiments.

For a target, a reference gives its profile, the perturbations whose profiles are nearest (Pearson, the field's
measure of similar knockdowns in Replogle 2022), its knockdown there and its cells; CORUM gives the complexes that
hold it. A mouse target is read through its human orthologs (HCOP, as gene_sets translates)."""
import os

import numpy as np
import pandas as pd

FOLDER = os.environ.get("PERTURA_PRIOR_SCREENS", os.path.join(os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura")), "resources/prior_screens/replogle_2022"))
CORUM = os.environ.get("PERTURA_CORUM", os.path.join(os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura")),
                                                   "resources/protein_complexes/corum_zenodo_17419058"))
REFERENCES = {"K562_essential": "K562_essential_normalized_bulk_01.h5ad",
              "K562_genome_wide": "K562_gwps_normalized_bulk_01.h5ad",
              "RPE1_essential": "rpe1_normalized_bulk_01.h5ad"}
SOURCE = "Replogle et al. 2022, Cell 185:2559 (Figshare+ 20029387)"
_cache = {}


def reference(name):
    """(profiles: perturbations x genes, Z-normalized, rows named by the target's symbol; knockdown and cells per
    row) of one reference, read once."""
    if name not in REFERENCES:
        raise ValueError(f"reference is one of {', '.join(REFERENCES)}")
    if name not in _cache:
        import anndata as ad
        a = ad.read_h5ad(os.path.join(FOLDER, REFERENCES[name]))
        symbols = [str(x).split("_")[1] if "_" in str(x) else str(x) for x in a.obs_names]
        genes = a.var["gene_name"].astype(str).to_numpy() if "gene_name" in a.var else np.asarray(a.var_names)
        x = np.asarray(a.X.toarray() if hasattr(a.X, "toarray") else a.X, dtype=np.float32)
        info = pd.DataFrame({"knockdown_fold": a.obs.get("fold_expr"), "cells": a.obs.get("num_cells_filtered")},
                            index=a.obs_names).set_axis(symbols)
        keep = ~pd.Index(symbols).duplicated()  # a target with two guide pairs: its first row
        _cache[name] = (pd.DataFrame(x[keep], index=np.asarray(symbols)[keep], columns=genes), info[keep])
    return _cache[name]


def nearest(profiles, target, k=5):
    """The k perturbations whose profiles correlate best with the target's (Pearson over the shared genes)."""
    x = profiles.to_numpy()
    x = np.where(np.isfinite(x), x, 0.0)  # a Z the reference could not compute (its controls' spread 0): no change
    x = (x - x.mean(axis=1, keepdims=True)) / (x.std(axis=1, keepdims=True) + 1e-9)
    t = x[profiles.index.get_loc(target)]
    r = pd.Series(x @ t / x.shape[1], index=profiles.index).drop(target)
    return [{"target": i, "r": round(float(v), 3)} for i, v in r.sort_values(ascending=False).head(k).items()]


def complexes(organism="human"):
    """{complex name: [subunit symbols]} from CORUM for the organism: human complexes, and for mouse the human ones
    translated through HCOP with CORUM's own mouse complexes."""
    key = ("corum", organism)
    if key not in _cache:
        t = pd.read_csv(os.path.join(CORUM, "corum_allComplexes.txt"), sep="\t",
                        usecols=["complex_name", "organism", "subunits_gene_name"])
        sets = lambda d: {n: sorted({g for g in str(s).split(";") if g and g != "None"})  # noqa: E731
                          for n, s in zip(d["complex_name"], d["subunits_gene_name"])}
        out = sets(t[t["organism"] == "Human"])
        if organism == "mouse":
            from . import gene_sets as gs
            out = {**gs.human_to_mouse(out), **sets(t[t["organism"] == "Mouse"])}
        _cache[key] = out
    return _cache[key]


def to_human(genes):
    """Each mouse symbol's human orthologs (HCOP, gene_sets' criteria)."""
    from . import gene_sets as gs
    t = pd.read_csv(os.path.join(gs.folder(), *gs.HCOP), sep="\t", compression="gzip", low_memory=False,
                    usecols=["human_symbol", "mouse_symbol", "support"])
    t = t[t["support"].astype(str).str.split(",").str.len() >= gs.MIN_EVIDENCE]
    orth = t.groupby("mouse_symbol")["human_symbol"].apply(lambda s: sorted(set(s.astype(str)))).to_dict()
    return {g: orth.get(g, []) for g in genes}


def look_up(targets, references=None, k=5, organism="human"):
    """For each target and reference: whether the reference holds it, its knockdown and cells there, its nearest
    perturbations, and the CORUM complexes that hold it. A row per target and reference; prior knowledge."""
    names = list(references or REFERENCES)
    human = to_human(targets) if organism == "mouse" else {t: [t] for t in targets}
    held = complexes(organism)
    rows = []
    for t in targets:
        cx = sorted(n for n, genes in held.items() if t in genes)
        for name in names:
            profiles, info = reference(name)
            hit = next((h for h in human.get(t, []) if h in profiles.index), None)
            row = {"target": t, "reference": name, "in_reference": hit is not None, "complexes": cx}
            if hit is not None:
                fold = float(info.loc[hit, "knockdown_fold"])
                row.update(as_human=hit, knockdown_fold=round(fold, 3) if np.isfinite(fold) else None,
                           cells=int(info.loc[hit, "cells"]), nearest=nearest(profiles, hit, k))
            rows.append(row)
    return rows
