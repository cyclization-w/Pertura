from __future__ import annotations

import csv
import json
from pathlib import Path

from pertura_core import CapabilityRunRequest, DependencyRef, ScopeKey
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.intake import inspect_dataset_path


def _contract_request(source: Path, capability_id: str, parameters: dict, dependencies=()):
    contract = inspect_dataset_path(source)
    spec = CapabilityRegistry.load_default(include_external=False).get(capability_id)
    request = CapabilityRunRequest(
        run_id="scientific-grounding",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters,
        dependencies=dependencies,
    )
    return contract, spec, request


def test_replicate_null_calibration_fails_poor_ntc_behavior(tmp_path: Path) -> None:
    source = tmp_path / "calibration"
    source.mkdir()
    cells: list[str] = []
    metadata: list[tuple[str, str, str]] = []
    for replicate in ("r1", "r2", "r3"):
        for condition in ("target", "baseline", "ntc_a", "ntc_b"):
            for index in range(3):
                cell = f"{replicate}_{condition}_{index}"
                cells.append(cell)
                metadata.append((cell, condition, replicate))
    with (source / "counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *cells])
        writer.writerow(
            [
                "G1",
                *(
                    100
                    if "_ntc_a_" in cell
                    else 1
                    if "_ntc_b_" in cell
                    else 12
                    if "_target_" in cell
                    else 3
                    for cell in cells
                ),
            ]
        )
        writer.writerow(["G2", *(5 for _ in cells)])
    with (source / "metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "condition", "replicate"])
        writer.writerows(metadata)

    contract, spec, request = _contract_request(
        source,
        "calibration.replicate_null.v1",
        {
            "counts_path": "counts.csv",
            "metadata_path": "metadata.csv",
            "target_condition": "target",
            "baseline_condition": "baseline",
            "negative_control_conditions": ["ntc_a", "ntc_b"],
            "permutations": 40,
        },
    )
    staging = tmp_path / "calibration-output"
    staging.mkdir()
    result = execute_capability(spec, request, contract, staging)

    assert result.status.value == "blocked"
    assert result.metrics["passed"] is False
    assert any("NTC" in blocker and "threshold" in blocker for blocker in result.blockers)
    payload = json.loads((staging / "replicate_null_calibration.json").read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["thresholds"]["profile"] == "replicate_null_v1"


def test_target_reliability_uses_retained_cells_not_all_caller_cells(tmp_path: Path) -> None:
    source = tmp_path / "target"
    source.mkdir()
    target_cells = [f"t{index}" for index in range(30)]
    control_cells = [f"c{index}" for index in range(30)]
    with (source / "expression.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "KLF1", "SIG1"])
        for cell in target_cells:
            writer.writerow([cell, 1, 1])
        for cell in control_cells:
            writer.writerow([cell, 8, 4])
    with (source / "metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "perturbation_uid", "guide", "batch", "replicate"])
        for index, cell in enumerate(target_cells):
            writer.writerow([cell, "target:KLF1", f"g{index % 3}", "b1", f"r{index % 3}"])
        for index, cell in enumerate(control_cells):
            writer.writerow([cell, "control:NTC", "NTC", "b1", f"r{index % 3}"])

    result_id = "result_retained_target"
    dependency = DependencyRef(
        kind="retained_cell_manifest",
        object_id=result_id,
        object_hash="sha256:" + "7" * 64,
        role="diagnostic.guide_assignment.v1:provided",
    )
    contract, spec, request = _contract_request(
        source,
        "target.reliability.v2",
        {
            "expression_path": "expression.csv",
            "metadata_path": "metadata.csv",
            "target_uid": "target:KLF1",
            "control_uid": "control:NTC",
            "target_gene": "KLF1",
            "signature_genes": ["SIG1"],
            "bootstrap_iterations": 20,
        },
        dependencies=(dependency,),
    )
    staging = tmp_path / "target-output"
    staging.mkdir()
    manifest = staging / "dependency" / "retained_cells.csv"
    manifest.parent.mkdir()
    retained = set(target_cells[:5] + control_cells)
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "retained"])
        for cell in target_cells + control_cells:
            writer.writerow([cell, str(cell in retained).lower()])
    (staging / "_dependency_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "result_id": result_id,
                        "output_hashes": {"retained_cells.csv": file_sha256(manifest)},
                        "local_output_paths": [str(manifest)],
                        "dependency_refs": [
                            {"kind": "retained_cell_manifest", "object_id": result_id}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = execute_capability(spec, request, contract, staging)

    assert result.status.value == "blocked"
    assert result.metrics["retained_manifest_applied"] is True
    assert result.metrics["selected_retained_cell_count"] == 35
    assert any("coverage" in blocker for blocker in result.blockers)
    payload = json.loads((staging / "target_reliability_v2.json").read_text(encoding="utf-8"))
    assert payload["target_gene"] == "KLF1"
    assert payload["target_gene_efficacy"]["effect"] < 0
    assert payload["n_target_cells"] == 5
    assert payload["n_control_cells"] == 30


def test_sceptre_consumes_the_authoritative_retained_cell_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from pertura_workflow.capabilities import effect_candidates
    from pertura_workflow.capabilities.executors import execute_capability

    source = tmp_path / "sceptre-retained"
    source.mkdir()
    for name, payload in {
        "response.csv": "id,c1,c2,c3\nG1,1,2,3\n",
        "guides.csv": "id,c1,c2,c3\ng1,1,0,1\n",
        "guide_map.csv": "grna_id,grna_target\ng1,T1\n",
        "pairs.csv": "grna_target,response_id\nT1,G1\n",
    }.items():
        (source / name).write_text(payload, encoding="utf-8")

    retained_result_id = "result_retained_sceptre"
    retained_hash = "sha256:" + "8" * 64
    design_hash = "sha256:" + "9" * 64
    dependencies = (
        DependencyRef(
            kind="retained_cell_manifest",
            object_id=retained_result_id,
            object_hash=retained_hash,
            role="screen.retained_cells.v1",
        ),
        DependencyRef(
            kind="design_balance",
            object_id="result_design_sceptre",
            object_hash=design_hash,
            role="diagnostic.design_balance.v1",
        ),
    )
    contract, spec, request = _contract_request(
        source,
        "association.sceptre.v1",
        {
            "response_matrix_path": "response.csv",
            "guide_matrix_path": "guides.csv",
            "guide_target_map_path": "guide_map.csv",
            "discovery_pairs_path": "pairs.csv",
            "moi": "high",
        },
        dependencies=dependencies,
    )
    staging = tmp_path / "sceptre-output"
    staging.mkdir()
    retained_manifest = staging / "dependency" / "retained_cells.csv"
    retained_manifest.parent.mkdir()
    retained_manifest.write_text(
        "cell_id,retained\nc1,true\nc2,false\nc3,true\n", encoding="utf-8"
    )
    (staging / "_dependency_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "result_id": retained_result_id,
                        "canonical_hash": retained_hash,
                        "capability_id": "screen.retained_cells.v1",
                        "result_kind": "retained_cell_manifest",
                        "output_hashes": {
                            "retained_cells.csv": file_sha256(retained_manifest)
                        },
                        "local_output_paths": [str(retained_manifest)],
                        "dependency_refs": [
                            {
                                "kind": "retained_cell_manifest",
                                "object_id": retained_result_id,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_runner(profile, runner, config_path, *, timeout):
        del runner, timeout
        assert profile == "sceptre-v1"
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        captured.update(config)
        output = Path(config["output_dir"])
        (output / "sceptre_metadata.json").write_text(
            json.dumps(
                {
                    "calibration_passed": True,
                    "calibration_type1_rate": 0.04,
                    "discovery_executed": True,
                }
            ),
            encoding="utf-8",
        )
        (output / "sceptre_calibration.csv").write_text(
            "response_id,grna_target,p_value\nG1,NTC,0.4\n", encoding="utf-8"
        )
        (output / "sceptre_results.csv").write_text(
            "response_id,grna_target,p_value,fold_change,se_fold_change,FDR\n"
            "G1,T1,0.01,-1.0,0.2,0.02\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(effect_candidates, "_run_r_profile", fake_runner)
    result = execute_capability(
        spec,
        request,
        contract,
        staging,
        runtime_context={"enforce_dependency_consumption": True},
    )

    retained_path = Path(str(captured["retained_cell_ids_path"]))
    assert retained_path.read_text(encoding="utf-8").splitlines() == ["c1", "c3"]
    assert result.metadata["retained_manifest_applied"] is True
    assert retained_hash in result.metadata["consumed_dependency_hashes"]
    assert design_hash not in result.metadata["consumed_dependency_hashes"]
