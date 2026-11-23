"""Synthetic blinded screen for the service and server tests.

Default: 10 groups, two hidden calibration decoys. Effect groups have 30 strong effects (p = 1e-8) in both tables;
every other p-value is uniform on [0.05, 1], so no null group can pass BH. Expected for the default: 60 Robust
claims on P01/P02, nothing elsewhere, FDR(2) = 8 * 1 / 2 / 60.
Keys: keys/server_key.json (calibration decoys only; the config points here) and keys/key.json (the scorer's key,
with holdout decoys and identities).
"""
import json
import os

import numpy as np
import pandas as pd

GROUPS = [f"P{i:02d}" for i in range(1, 11)]
DECOYS = ["P03", "P07"]
EFFECT = ["P01", "P02"]


def make_screen(root, groups=GROUPS, decoys=DECOYS, effect=EFFECT, holdout=(), sizes=None, n_features=200, seed=0,
                budget=2, vectors=False, hot=()):
    """Workspace with outputs/tables/spec_a.tsv and spec_b.tsv, the two keys and a config. Returns the config.
    vectors: the tables also carry per-vector rows (construct <group>_v1, <group>_v2). Effect groups replicate in both
    vectors; 'hot' groups (single-vector artefacts, like a clonal expansion) are significant pooled and in v2 only."""
    rng = np.random.default_rng(seed)
    ws, key_dir = f"{root}/ws", f"{root}/keys"
    os.makedirs(f"{ws}/outputs/tables")
    os.makedirs(key_dir)
    genes = [f"g{j}" for j in range(n_features)]
    for name in ("spec_a", "spec_b"):
        parts = []
        for g in groups:
            for con in (("all", "v1", "v2") if vectors else ("all",)):
                p, eff = rng.uniform(0.05, 1.0, n_features), rng.normal(0.0, 0.2, n_features)
                if g in effect or (g in hot and con in ("all", "v2")):
                    p[:30], eff[:30] = (1e-8 if con == "all" else 1e-6), 1.0
                part = pd.DataFrame({"perturbation_id": g, "gene": genes, "logFC": eff, "p": p})
                if vectors:
                    part["construct"] = con if con == "all" else f"{g}_{con}"
                parts.append(part)
        pd.concat(parts).to_csv(f"{ws}/outputs/tables/{name}.tsv", sep="\t", index=False)
    hidden = set(decoys) | set(holdout)
    with open(f"{key_dir}/key.json", "w") as fh:
        json.dump({"decoys": {d: "control guides" for d in decoys},
                   "holdout_decoys": {h: "control guides" for h in holdout},
                   "perturbations": {g: f"Gene{g}" for g in groups if g not in hidden}}, fh)
    with open(f"{key_dir}/server_key.json", "w") as fh:
        json.dump({"decoys": {d: "control guides" for d in decoys}}, fh)
    return {"run": "synthetic", "workspace": ws, "state_dir": f"{root}/state", "key_path": f"{key_dir}/server_key.json",
            "groups": list(groups), "budget": budget, "n_boot": 200,
            "group_sizes": sizes or {g: 200 for g in groups}}


def make_cell_screen(folder, seed=0, n_units=6, n_null=10, genes=80):
    """A cell-level screen for the robustness engine (pertura_research.retired.robustness): mice as independent units, counts drawn
    gamma-Poisson with a mouse effect on every gene. The targets:
    - EVERY: gene gE doubled in every mouse (two guides);
    - ONE: gene gO up 4-fold in the first mouse only;
    - NOGUIDE: no effect; the cells without a guide carry half as much of gene gN, so it looks raised against them;
    - N01..: no effect at all (the nulls), each read on its own gene gX01..;
    and 12 control guides, and cells without a guide. Returns (Screen, design, findings): findings maps a name to the
    spec the engine takes, the nulls' direction left to the caller."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    names = ["gE", "gO", "gN"] + [f"gX{i:02d}" for i in range(1, n_null + 1)]
    names += [f"g{j}" for j in range(genes - len(names))]
    gi = {g: j for j, g in enumerate(names)}
    base = rng.gamma(4, 5, len(names))
    units = [f"m{k + 1}" for k in range(n_units)]
    targets = ["EVERY", "ONE", "NOGUIDE"] + [f"N{i:02d}" for i in range(1, n_null + 1)]
    rows = []
    for u in units:
        for t in targets:
            rows += [(u, f"{t}_{k}", t) for k in (1, 2) for _ in range(20)]
        rows += [(u, f"CTRL_{k}", "control") for k in range(1, 13) for _ in range(8)]
        rows += [(u, "", "none")] * 60
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    mouse = {u: np.exp(rng.normal(0, 0.1, len(names))) for u in units}
    mu = base[None, :] * np.stack([mouse[u] for u in obs["unit"]])
    lab = obs["label"].to_numpy()
    mu[lab == "EVERY", gi["gE"]] *= 2.0
    mu[(lab == "ONE") & (obs["unit"].to_numpy() == units[0]), gi["gO"]] *= 4.0
    mu[lab == "none", gi["gN"]] *= 0.5
    size = np.exp(rng.normal(0, 0.3, len(obs)))[:, None]
    lam = rng.gamma(5.0, mu * size / 5.0)  # gamma-Poisson, dispersion 0.2
    X = sp.csr_matrix(rng.poisson(lam).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(3, 30)}" if g else "" for g in obs["guide"]]
    obs["control_group"] = np.where(lab == "control", "CTRL", "")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=names))
    gl = [f"{t}_{k}" for t in targets for k in (1, 2)] + [f"CTRL_{k}" for k in range(1, 13)]
    pd.DataFrame({"guide": gl, "target": [g.rsplit("_", 1)[0] for g in gl],
                  "kind": ["targeting"] * (2 * len(targets)) + ["non_targeting"] * 12}).to_csv(
        os.path.join(folder, "guides.tsv"), sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": []}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": n_units}}}
    findings = {"every unit": {"target": "EVERY", "kind": "gene", "feature": "gE", "direction": 1},
                "one unit": {"target": "ONE", "kind": "gene", "feature": "gO", "direction": 1},
                "against no-guide cells": {"target": "NOGUIDE", "kind": "gene", "feature": "gN", "direction": 1,
                                           "choices": {"reference": "no_guide"}}}
    for i in range(1, n_null + 1):
        findings[f"null N{i:02d}"] = {"target": f"N{i:02d}", "kind": "gene", "feature": f"gX{i:02d}"}
    return Screen(folder, design, adata=a), design, findings


def make_condition_screen(folder, seed=0, wells=8, genes=60, effect=1.6):
    """A screen in two conditions, half the wells each (wells as units, as one aggregated library of separate
    wells): target INT raises the set s0-s4 only in the second condition ("stim"); target NUL raises it alike in
    both; target OTHER does nothing; control guides. Counts gamma-Poisson with a well effect on every gene. Returns
    (Screen, design, the set)."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    names = [f"s{i}" for i in range(5)] + [f"g{j}" for j in range(genes - 5)]
    base = rng.gamma(4, 5, len(names))
    units = [f"w{k + 1}" for k in range(wells)]
    cond = {u: ("rest" if k < wells // 2 else "stim") for k, u in enumerate(units)}
    rows = []
    for u in units:
        for t in ("INT", "NUL", "OTHER"):
            rows += [(u, f"{t}_{k}", t) for k in (1, 2) for _ in range(30)]
        rows += [(u, f"CTRL_{k}", "control") for k in (1, 2, 3) for _ in range(20)]
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    obs["condition"] = obs["unit"].map(cond)
    well = {u: np.exp(rng.normal(0, 0.1, len(names))) for u in units}
    mu = base[None, :] * np.stack([well[u] for u in obs["unit"]])
    lab, cnd = obs["label"].to_numpy(), obs["condition"].to_numpy()
    s = slice(0, 5)
    mu[(lab == "INT") & (cnd == "stim"), s] *= effect
    mu[lab == "NUL", s] *= effect
    lam = rng.gamma(5.0, mu * np.exp(rng.normal(0, 0.3, len(obs)))[:, None] / 5.0)
    X = sp.csr_matrix(rng.poisson(lam).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(3, 30)}" for g in obs["guide"]]
    obs["control_group"] = np.where(lab == "control", "CTRL", "")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group", "condition"]].copy(),
                   var=pd.DataFrame(index=names))
    gl = [f"{t}_{k}" for t in ("INT", "NUL", "OTHER") for k in (1, 2)] + [f"CTRL_{k}" for k in (1, 2, 3)]
    pd.DataFrame({"guide": gl, "target": [g.rsplit("_", 1)[0] for g in gl],
                  "kind": ["targeting"] * 6 + ["non_targeting"] * 3}).to_csv(os.path.join(folder, "guides.tsv"),
                                                                             sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "lanes", "flags": []}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRa"}},
              "experiment": {"conditions": {"value": cond}, "organism": {"value": "human"}},
              "units": {"independent_unit": {"value": "none"}}}
    return Screen(folder, design, adata=a), design, {"S": [f"s{i}" for i in range(5)]}
