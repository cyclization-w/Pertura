from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "generate_paper_ref05.py"
    spec = importlib.util.spec_from_file_location("generate_paper_ref05", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(donors: list[str]) -> list[dict[str, str]]:
    rows = []
    for donor in donors:
        rows.extend(
            [
                {"cell_id": f"{donor}-ctrl", "unit_id": donor, "is_control": "true"},
                {"cell_id": f"{donor}-stim", "unit_id": donor, "is_control": "false"},
            ]
        )
    return rows


def test_ref05_split_design_requires_paired_donors() -> None:
    module = _module()
    design = module._split_design(_rows(["d1", "d2", "d3", "d4"]), "evaluation")
    assert design["d1-ctrl"] == "d1\x1fctrl"
    assert design["d1-stim"] == "d1\x1fstim"

    incomplete = _rows(["d1", "d2", "d3"])
    incomplete.pop()
    with pytest.raises(ValueError, match="lack paired conditions"):
        module._split_design(incomplete, "evaluation")

    invalid = _rows(["d1", "d2", "d3"])
    invalid[0]["is_control"] = "unknown"
    with pytest.raises(ValueError, match="invalid control flag"):
        module._split_design(invalid, "evaluation")


def test_ref05_condition_matching_is_fail_closed() -> None:
    module = _module()
    assert module._condition_matches("ctrl", "ctrl")
    assert module._condition_matches("stim", "stim")
    assert not module._condition_matches("stim", "ctrl")
    assert not module._condition_matches("mystery", "stim")


def test_ref05_missing_state_is_explicitly_unavailable() -> None:
    module = _module()
    assert module._cell_state("CD14 Mono") == "CD14 Mono"
    assert module._cell_state("  CD4 T  ") == "CD4 T"
    assert module._cell_state(None) is None
    assert module._cell_state(float("nan")) is None


def test_ref05_is_independent_split_scoped_and_streaming() -> None:
    root = Path(__file__).resolve().parents[2]
    python = (root / "scripts" / "generate_paper_ref05.py").read_text(encoding="utf-8")
    runner = (root / "scripts" / "generate_paper_ref05.R").read_text(encoding="utf-8")

    assert "from pertura_" not in python
    assert '_selection_rows(splits_path, "calibration")' in python
    assert '_selection_rows(splits_path, "evaluation")' in python
    assert "source.X[start:stop, :]" in python
    assert "calibration_donors & evaluation_donors" in python
    assert '"cell_label_permutation": False' in python
    assert '"missing_cell_state"' in python
    assert '"propeller_included"' in python

    assert 'reformulate(c("donor", condition_column))' in runner
    assert 'reformulate(c(condition_column, "donor"), intercept = FALSE)' in runner
    assert "propeller.ttest(" in runner
    assert "prop_list$Proportions" in runner
    assert "prop_list$proportions" not in runner
    assert "contrast <- numeric(ncol(design))" in runner
    assert "contrast[group_columns[[1]]] <- -1" in runner
    assert "contrast[group_columns[[2]]] <- 1" in runner
    assert "contrasts = designed$contrast" in runner
    assert "contrast <- matrix(" not in runner
    assert "for (donor in swapped)" in runner
    assert "cell_label_permutation = FALSE" in runner
    assert "from pertura_" not in runner


def test_ref05_catalog_outputs_are_implemented() -> None:
    root = Path(__file__).resolve().parents[2]
    python = (root / "scripts" / "generate_paper_ref05.py").read_text(encoding="utf-8")
    for name in (
        "edger_reference.tsv",
        "edger_design_matrix.tsv",
        "edger_session_info.txt",
        "propeller_reference.tsv",
        "propeller_design_matrix.tsv",
        "propeller_session_info.txt",
        "method_null_reference.tsv",
        "replicate_null_reference.tsv",
    ):
        assert name in python


def test_product_propeller_uses_speckle_110_field_names() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = (
        root
        / "src"
        / "pertura_workflow"
        / "capabilities"
        / "runners"
        / "propeller_composition.R"
    ).read_text(encoding="utf-8")
    assert "prop_list$Proportions" in runner
    assert "prop_list$proportions" not in runner
    assert "contrast <- numeric(ncol(design))" in runner
    assert 'reformulate(c("condition", "subject_id"), intercept = FALSE)' in runner
    assert 'match(paste0("condition", contrast_levels), colnames(design))' in runner
    assert "model.matrix(~ subject_id + condition" not in runner
    assert "makeContrasts(" not in runner
    assert 'metadata_suffix %in% c("tsv", "txt")' in runner
    assert "metadata <- read.table(" in runner
    assert "metadata <- read.csv(" not in runner
    assert "baseline_proportion" in runner
    assert "target_proportion" in runner
    assert "effect = as.numeric(target_mean" in runner
    assert 'c("P.Value", "PValue", "p.value", "pvalue")' in runner

    reference_runner = (
        root
        / "src"
        / "pertura_bench"
        / "runners"
        / "propeller_reference.R"
    ).read_text(encoding="utf-8")
    assert "transformed$Proportions" in reference_runner
    assert "transformed$proportions" not in reference_runner
    assert "contrast <- numeric(ncol(design))" in reference_runner
    assert "makeContrasts(" not in reference_runner
