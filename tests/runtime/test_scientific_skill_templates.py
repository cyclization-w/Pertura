from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "src/pertura_runtime/agent_bundle/skills"
MATERIALIZER = (
    SKILLS / "run-replicate-aware-pseudobulk-de/scripts/materialize_pseudobulk.py"
)
EDGER = SKILLS / "run-replicate-aware-pseudobulk-de/scripts/run_edger_ql.R"
NULL = SKILLS / "run-design-preserving-null-calibration/scripts/run_paired_label_null.R"


def _load_materializer():
    spec = importlib.util.spec_from_file_location(
        "materialize_pseudobulk", MATERIALIZER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(tmp_path: Path, *, fractional: bool = False) -> tuple[Path, Path]:
    values = np.array(
        [
            [1, 0, 2],
            [3, 1, 0],
            [0, 4, 1],
            [2, 2, 2],
            [1, 1, 1],
            [5, 0, 0],
        ],
        dtype=float,
    )
    if fractional:
        values[0, 0] = 1.5
    cells = [f"c{index}" for index in range(6)]
    data = ad.AnnData(
        X=sparse.csr_matrix(values),
        obs=pd.DataFrame(
            {
                "donor": ["d1", "d1", "d1", "d1", "d2", "d2"],
                "condition": ["ctrl", "ctrl", "stim", "stim", "ctrl", "stim"],
            },
            index=cells,
        ),
        var=pd.DataFrame(index=["g1", "g2", "g3"]),
    )
    h5ad = tmp_path / "input.h5ad"
    data.write_h5ad(h5ad)
    selection = tmp_path / "selection.tsv"
    pd.DataFrame({"cell_id": cells}).to_csv(selection, sep="\t", index=False)
    return h5ad, selection


def test_materializer_aggregates_independent_units(tmp_path: Path) -> None:
    h5ad, selection = _write_fixture(tmp_path)
    config = {
        "input_h5ad": str(h5ad),
        "selection_tsv": str(selection),
        "unit_column": "donor",
        "condition_column": "condition",
        "output_counts": str(tmp_path / "counts.tsv"),
        "output_samples": str(tmp_path / "samples.tsv"),
        "output_accounting": str(tmp_path / "accounting.json"),
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    assert _load_materializer().main([str(config_path)]) == 0
    counts = pd.read_csv(tmp_path / "counts.tsv", sep="\t")
    samples = pd.read_csv(tmp_path / "samples.tsv", sep="\t")
    accounting = json.loads((tmp_path / "accounting.json").read_text())

    assert samples["sample_id"].tolist() == [
        "d1__ctrl",
        "d1__stim",
        "d2__ctrl",
        "d2__stim",
    ]
    assert counts.loc[counts["gene"] == "g1", "d1__ctrl"].item() == 4
    assert counts.loc[counts["gene"] == "g2", "d1__stim"].item() == 6
    assert accounting["selected_cells"] == 6
    assert accounting["cell_is_replicate"] is False


def test_materializer_rejects_cells_and_noninteger_counts(tmp_path: Path) -> None:
    h5ad, selection = _write_fixture(tmp_path, fractional=True)
    config = {
        "input_h5ad": str(h5ad),
        "selection_tsv": str(selection),
        "unit_column": "donor",
        "condition_column": "condition",
        "output_counts": str(tmp_path / "counts.tsv"),
        "output_samples": str(tmp_path / "samples.tsv"),
    }
    path = tmp_path / "fractional.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="nonnegative integers"):
        _load_materializer().main([str(path)])

    config["unit_column"] = "cell_id"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be biological replicates"):
        _load_materializer().main([str(path)])


def test_r_templates_encode_answer_free_frozen_methods() -> None:
    edger = EDGER.read_text(encoding="utf-8")
    null = NULL.read_text(encoding="utf-8")
    combined = (edger + null).lower()

    for token in (
        "filterByExpr",
        "calcNormFactors",
        "estimateDisp",
        "glmQLFit",
        "glmQLFTest",
    ):
        assert token in edger
        assert token in null
    assert "2^length(units) - 2L" in null
    assert "sort(unique" in null
    assert "full_gene_output" in edger
    for forbidden in ("papalexi", "kang18", "task_reference", "evaluator"):
        assert forbidden not in combined
