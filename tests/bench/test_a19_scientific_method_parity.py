from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from pertura_core.hashing import path_sha256


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_script("qualify_a19_evaluators")
binding_qualification = _load_script("qualify_a19_capability_bindings")
method_qualification = _load_script("qualify_a19_scientific_methods")
scope_audit = _load_script("audit_a19_scientific_method_scope")


def _result(capability_id: str, paths: list[Path]):
    return SimpleNamespace(
        capability_id=capability_id,
        output_paths=tuple(str(path) for path in paths),
        output_hashes={path.name: path_sha256(path) for path in paths},
        result_id=f"result-{capability_id}",
        canonical_hash="sha256:" + "1" * 64,
        dependencies=(),
    )


def test_papa02_parity_adapter_uses_real_committed_outputs(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    ref03 = paper / "references" / "REF-03"
    ref03.mkdir(parents=True)
    versions = {
        "anndata": "0.11.4",
        "scanpy": "1.11.4",
        "scikit_learn": "1.6.1",
        "igraph": "0.11.8",
        "leidenalg": "0.10.2",
    }
    (ref03 / "manifest.json").write_text(
        json.dumps({"environment": versions}), encoding="utf-8"
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    model = staging / "state_reference_fit.npz"
    model.write_bytes(b"real-model")
    assignments = staging / "control_state_assignments.parquet"
    pd.DataFrame(
        [{"cell_id": "c1", "technical_state_id": "state_a", "is_control": True}]
    ).to_parquet(assignments, index=False)
    manifest = staging / "state_reference_fit.json"
    manifest.write_text(
        json.dumps(
            {
                "environment": {
                    "versions": versions | {"scikit-learn": versions["scikit_learn"]}
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"

    hashes = binding_qualification._materialize_real_scientific_outputs(
        task_id="PAPA-02",
        results={
            "state.reference.fit.v1": _result(
                "state.reference.fit.v1", [model, assignments, manifest]
            )
        },
        workspace_root=tmp_path,
        output=output,
        paper_root=paper,
    )

    observed = pd.read_csv(output / "reference_cell_manifest.tsv", sep="\t")
    assert observed.to_dict(orient="records") == [
        {"cell_id": "c1", "technical_state_id": "state_a"}
    ]
    assert (output / "state_reference_model" / "model.npz").read_bytes() == b"real-model"
    assert set(hashes) == {
        "reference_cell_manifest.tsv",
        "reference_provenance.json",
        "state_reference_model/model.npz",
    }


def test_reference_environment_drift_fails_closed(tmp_path: Path) -> None:
    ref04 = tmp_path / "references" / "REF-04"
    ref04.mkdir(parents=True)
    reference = {
        "anndata": "0.11.4",
        "scanpy": "1.12.1",
        "pertpy": "1.1.1",
        "scikit_learn": "1.8.0",
        "igraph": "0.11.8",
        "leidenalg": "0.10.2",
        "scipy": "1.15.2",
    }
    (ref04 / "manifest.json").write_text(
        json.dumps({"environment": reference}), encoding="utf-8"
    )
    actual = {
        "environment": {
            "versions": {
                **{key: value for key, value in reference.items() if key != "scikit_learn"},
                "scikit-learn": "0.0.0",
            }
        }
    }

    with pytest.raises(RuntimeError, match="REF-04 environment version drift"):
        binding_qualification._require_ref04_environment_parity(actual, tmp_path)


def test_method_parity_gate_has_exact_real_task_coverage() -> None:
    assert method_qualification._BOUND_PARITY_TASKS == {
        "PAPA-02",
        "PAPA-03",
        "PAPA-04",
        "PAPA-05",
        "KANG-02",
    }
    refresh = (ROOT / "scripts/refresh_sherlock_a19_checkpoint.sh").read_text(
        encoding="utf-8"
    )
    assert refresh.index("qualify_a19_scientific_methods.py") < refresh.index(
        "export-server-plan"
    )
    assert refresh.index("audit_a19_scientific_method_scope.py") < refresh.index(
        "export-server-plan"
    )


def test_papa06_independent_reference_and_skill_share_frozen_protocol() -> None:
    reference = (ROOT / "scripts/generate_paper_task_trans_de.R").read_text(
        encoding="utf-8"
    )
    skill = (
        ROOT
        / "src/pertura_runtime/agent_bundle/skills/"
        "run-replicate-aware-pseudobulk-de/scripts/run_edger_ql.R"
    ).read_text(encoding="utf-8")
    for source in (reference, skill):
        assert "filterByExpr" in source
        assert "calcNormFactors" in source
        assert "glmQLFit" in source
        assert "robust = TRUE" in source or "robust = robust" in source
        assert "tested = FALSE" in source


def test_scope_audit_freezes_runtime_and_authority_surfaces() -> None:
    frozen = set(scope_audit._EXACTLY_FROZEN_PATHS)
    assert {
        "src/pertura_runtime/product_tools",
        "src/pertura_runtime/product.py",
        "src/pertura_runtime/invocation_bindings.py",
        "src/pertura_runtime/project",
        "src/pertura_runtime/verifier",
        "src/pertura_core/promotion.py",
        "src/pertura_core/receipt_verification.py",
        "src/pertura_bench/paper_agent_execution.py",
        "src/pertura_bench/task_submission.py",
        "src/pertura_bench/resource_evidence.py",
        "src/pertura_workflow/environments",
        "benchmarks/paper_v1/task_references.v1.json",
    }.issubset(frozen)
