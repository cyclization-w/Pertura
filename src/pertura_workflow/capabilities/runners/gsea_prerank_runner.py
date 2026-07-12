from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import gseapy
import numpy as np


def main(config_path: str) -> None:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    data = np.load(config["effect_matrix_path"], allow_pickle=False)
    effects = np.asarray(data["effects"], float)
    observed = np.asarray(data["observed_mask"], bool)
    perturbations = np.asarray(data["perturbations"], str)
    features = np.asarray(data["features"], str)
    rows = []
    for index, perturbation in enumerate(perturbations):
        ranking = [
            (str(features[column]), float(effects[index, column]))
            for column in range(len(features))
            if observed[index, column]
        ]
        ranking.sort(key=lambda item: (-item[1], item[0]))
        if len(ranking) < config["min_size"]:
            continue
        result = gseapy.prerank(
            rnk=ranking,
            gene_sets=config["gene_sets_path"],
            permutation_num=config["permutation_num"],
            min_size=config["min_size"],
            max_size=config["max_size"],
            seed=config["seed"],
            threads=config["threads"],
            outdir=None,
            no_plot=True,
            verbose=False,
        )
        table = result.res2d.reset_index()
        for record in table.to_dict(orient="records"):
            rows.append({
                "perturbation_id": str(perturbation),
                "gene_set": record.get("Term") or record.get("Name") or record.get("index"),
                "ES": record.get("ES"),
                "NES": record.get("NES"),
                "PValue": record.get("NOM p-val"),
                "FDR": record.get("FDR q-val"),
            })
    fields = ("perturbation_id", "gene_set", "ES", "NES", "PValue", "FDR")
    with Path(config["output_path"]).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1])
