from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def _write_h5ad(path: Path) -> None:
    import anndata as ad

    rng = np.random.default_rng(7)
    matrix = np.zeros((50, 12), dtype=float)
    matrix[:20, :6] = rng.poisson(5, size=(20, 6))
    matrix[20:40, 6:] = rng.poisson(5, size=(20, 6))
    matrix[40:, :] = rng.poisson(2, size=(10, 12))
    obs = pd.DataFrame({"condition": ["control"] * 40 + ["target"] * 10}, index=[f"cell_{i}" for i in range(50)])
    var = pd.DataFrame(index=[f"G{i}" for i in range(12)])
    ad.AnnData(matrix, obs=obs, var=var).write_h5ad(path)


def test_gmt_import_validates_namespace_duplicates_and_coverage(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    source.mkdir()
    (source / "modules.gmt").write_text("M1\tdesc\tG1\tG2\tG2\nM2\tdesc\tX1\tX2\n", encoding="utf-8")
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="gmt")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        result = runtime.run_analysis(
            "import gene modules",
            capability_id="module.import.gmt.v1",
            contract_id=contract["contract_id"],
            parameters={"gmt_path": "modules.gmt", "species": "human", "identifier_namespace": "HGNC", "gene_universe": ["G1", "G2", "G3"]},
        )
        assert result["status"] == "completed_with_caution"
        assert any("duplicate genes" in item for item in result["cautions"])
    finally:
        runtime.close()


def test_nmf_modules_are_fit_on_confirmed_controls_only(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    source.mkdir()
    _write_h5ad(source / "screen.h5ad")
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="nmf")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "control"})
        result = runtime.run_analysis(
            "learn NMF modules",
            capability_id="module.learn.nmf.v1",
            contract_id=contract["contract_id"],
            parameters={"h5ad_path": "screen.h5ad", "control_column": "condition", "control_values": ["control"], "ranks": [2, 3], "seeds": [0, 1, 2]},
        )
        assert result["status"] in {"completed", "completed_with_caution"}
        assert len(result["output_paths"]) == 2
    finally:
        runtime.close()


def test_state_reference_blocks_when_leiden_runtime_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    source.mkdir()
    _write_h5ad(source / "screen.h5ad")
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="state")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "control"})
        result = runtime.run_analysis(
            "build state reference",
            capability_id="reference.state.control_pca_leiden.v1",
            contract_id=contract["contract_id"],
            parameters={"h5ad_path": "screen.h5ad", "control_column": "condition", "control_values": ["control"]},
        )
        assert result["status"] == "blocked"
        assert any("dependency is missing" in item for item in result["blockers"])
    finally:
        runtime.close()
