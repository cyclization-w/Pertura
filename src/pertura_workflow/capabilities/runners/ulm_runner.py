from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import decoupler as dc
import numpy as np
import pandas as pd


def main(config_path: str) -> None:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    data = np.load(config["effect_matrix_path"], allow_pickle=False)
    effects = np.asarray(data["effects"], float)
    observed = np.asarray(data["observed_mask"], bool)
    perturbations = np.asarray(data["perturbations"], str)
    features = np.asarray(data["features"], str)
    if effects.shape != observed.shape or effects.shape != (len(perturbations), len(features)):
        raise ValueError("effect matrix dimensions are inconsistent")
    matrix = pd.DataFrame(
        np.where(observed, effects, np.nan),
        index=perturbations,
        columns=features,
    )
    network_path = Path(config["network_path"])
    network = (
        pd.read_parquet(network_path)
        if network_path.suffix.lower() == ".parquet"
        else pd.read_csv(network_path)
    )
    required = {"source", "target", "weight"}
    if not required.issubset(network.columns):
        raise ValueError("CollecTRI table requires source, target and weight")
    network = network.loc[:, ["source", "target", "weight"]].copy()
    network["source"] = network["source"].astype(str)
    network["target"] = network["target"].astype(str)
    network["weight"] = pd.to_numeric(network["weight"], errors="raise")
    if not np.isfinite(network["weight"].to_numpy(float)).all():
        raise ValueError("CollecTRI weights must be finite")
    if network.duplicated(["source", "target"]).any():
        raise ValueError("CollecTRI contains duplicate source-target edges")
    minimum = int(config["minimum_targets"])
    result = dc.mt.ulm(
        data=matrix,
        net=network,
        tmin=minimum,
        tval=True,
        verbose=False,
    )
    if not isinstance(result, tuple) or len(result) != 2:
        raise RuntimeError("decoupler.mt.ulm did not return score and adjusted-pvalue tables")
    scores, adjusted = result
    scores = pd.DataFrame(scores)
    adjusted = pd.DataFrame(adjusted)
    if not scores.index.equals(matrix.index):
        scores = scores.reindex(matrix.index)
    adjusted = adjusted.reindex(index=scores.index, columns=scores.columns)
    if scores.empty or scores.shape != adjusted.shape:
        raise RuntimeError("decoupler.mt.ulm returned inconsistent result tables")
    target_counts = (
        network[network["target"].isin(matrix.columns)]
        .groupby("source", sort=True)["target"].nunique()
        .to_dict()
    )
    rows = []
    for perturbation in scores.index:
        for regulator in scores.columns:
            activity = float(scores.loc[perturbation, regulator])
            fdr = float(adjusted.loc[perturbation, regulator])
            if not np.isfinite(activity) or not np.isfinite(fdr):
                continue
            rows.append({
                "perturbation_id": str(perturbation),
                "regulator": str(regulator),
                "activity": activity,
                "statistic": activity,
                "PValue": "",
                "FDR": fdr,
                "n_targets": int(target_counts.get(str(regulator), 0)),
            })
    fields = (
        "perturbation_id", "regulator", "activity", "statistic",
        "PValue", "FDR", "n_targets",
    )
    with Path(config["output_path"]).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1])
