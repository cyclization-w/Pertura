from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

from pertura_core import AnalysisStatus
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities.candidate_common import blocked, envelope


def _load_script(name: str) -> ModuleType:
    repo = Path(__file__).resolve().parents[2]
    path = repo / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_ref06(root: Path) -> Path:
    generator = _load_script("generate_paper_ref06.py")
    datasets = root / "datasets.json"
    datasets.write_text(
        json.dumps(
            {
                "datasets": {
                    "norman_k562_crispra_2019": {
                        "artifact_path": "/frozen/norman.h5ad",
                        "auxiliary_assets": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ref01 = root / "REF-01"
    ref01.mkdir()
    (ref01 / "manifest.json").write_text(
        json.dumps(
            {
                "reference_pack_id": "REF-01",
                "readiness": "generated",
                "pending_jobs": [],
            }
        ),
        encoding="utf-8",
    )
    (ref01 / "dataset_profiles.json").write_text(
        json.dumps(
            {
                "datasets": {
                    "norman_k562_crispra_2019": {
                        "artifact_sha256": "sha256:" + "a" * 64,
                        "obs_columns": ["guide_identity", "guide_merged"],
                        "layers": {"counts": {}},
                        "obsm_keys": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    output = root / "REF-06"
    result = generator.generate(datasets, ref01, output)
    assert result["passed"] is True
    return output


def _make_bindings(root: Path, ref06: Path) -> Path:
    output = root / "capability-reference-bindings.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-capability-reference-bindings-v1",
                "passed": True,
                "problems": [],
                "scenarios": [
                    {
                        "scenario_id": "CAP-06",
                        "capability_bindings": [
                            {
                                "capability_id": "association.sceptre.v1",
                                "reference_pack_id": "REF-06",
                                "target_evidence_level": "passed_planted_reference",
                                "scoring_route": "planted_fixture_comparison",
                                "release_scope": "primary",
                                "reference_manifest_sha256": file_sha256(
                                    ref06 / "manifest.json"
                                ),
                                "metrics": [
                                    "type_i_error",
                                    "power",
                                    "fdr",
                                    "effect_rank_concordance",
                                    "correct_refusal",
                                ],
                            }
                        ],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def _fake_executor(*, bad_nulls: bool = False):
    def execute(
        spec: Any,
        request: Any,
        contract: Any,
        staging: Path,
        *,
        runtime_context: dict[str, Any],
    ) -> Any:
        assert runtime_context["enforce_dependency_consumption"] is True
        if contract.dataset_id == "norman_k562_crispra_2019":
            assert "guide_matrix_path" not in request.parameters
            return blocked(spec, request, contract, "guide_matrix_path is required")

        fixture = Path(request.parameters["response_matrix_path"]).parent
        truth_path = fixture.parent / "sceptre_synthetic_truth.tsv"
        with truth_path.open("r", encoding="utf-8", newline="") as handle:
            truth = list(csv.DictReader(handle, delimiter="\t"))
        results = staging / "sceptre_results.csv"
        with results.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "response_id",
                    "grna_target",
                    "p_value",
                    "fold_change",
                    "se_fold_change",
                    "FDR",
                ],
            )
            writer.writeheader()
            for row in truth:
                positive = row["is_positive"] == "true"
                false_positive = bad_nulls and not positive
                writer.writerow(
                    {
                        "response_id": row["response_id"],
                        "grna_target": row["grna_target"],
                        "p_value": 0.001 if positive or false_positive else 0.5,
                        "fold_change": row["true_log_rate_ratio"],
                        "se_fold_change": 0.1,
                        "FDR": 0.01 if positive or false_positive else 0.5,
                    }
                )
        calibration = staging / "sceptre_calibration.csv"
        calibration.write_text("pair,p_value\nnull,0.5\n", encoding="utf-8")
        metadata = staging / "sceptre_metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "calibration_passed": True,
                    "calibration_type1_rate": 0.05,
                    "discovery_executed": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        dependency = request.dependencies[0]
        retained = staging / "retained_cells.csv"
        return envelope(
            spec,
            request,
            contract,
            status=AnalysisStatus.completed_with_caution,
            summary="Fake production SCEPTRE result for harness testing.",
            metrics={
                # These deliberately plausible self-reports must not be used by
                # the independent artifact evaluator.
                "calibration_passed": True,
                "type_i_error": 0.0,
                "power": 1.0,
                "fdr": 0.0,
            },
            outputs=(calibration, results, metadata),
            metadata={
                "method": "sceptre_0.99.0",
                "retained_manifest_applied": True,
                "dependency_consumption_enforced": True,
                "consumed_dependency_hashes": [dependency.object_hash],
                "dependency_consumption_records": [
                    {
                        "dependency_result_id": dependency.object_id,
                        "dependency_result_hash": dependency.object_hash,
                        "dependency_artifact_hash": file_sha256(retained),
                        "usage": "row_filter",
                        "consumer_capability_id": spec.capability_id,
                        "rows_available": 1200,
                        "rows_consumed": 1200,
                        "columns_consumed": 1,
                        "derived_output_hashes": [],
                    }
                ],
            },
        )

    return execute


def _environment(_: str) -> dict[str, Any]:
    return {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "sceptre-v1",
        "lock_hash": "sha256:" + "b" * 64,
        "resource_hashes": {},
        "expected_versions": {},
        "versions": {"sceptre": "0.99.0"},
    }


def test_obs06_runs_production_contract_and_independent_artifact_evaluation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runner = _load_script("run_paper_obs06.py")
    evaluator = _load_script("evaluate_paper_obs06.py")
    ref06 = _make_ref06(tmp_path)
    bindings = _make_bindings(tmp_path, ref06)
    observed = tmp_path / "OBS-06"

    from pertura_workflow.capabilities import effect_candidates

    def fake_r_profile(
        profile: str, runner_path: Any, config_path: Path, *, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert profile == "sceptre-v1"
        assert timeout == 7200
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        output = Path(config["output_dir"])
        truth_path = (
            Path(config["response_matrix_path"]).parent.parent
            / "sceptre_synthetic_truth.tsv"
        )
        with truth_path.open("r", encoding="utf-8", newline="") as handle:
            truth = list(csv.DictReader(handle, delimiter="\t"))
        with (output / "sceptre_results.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "response_id",
                    "grna_target",
                    "p_value",
                    "fold_change",
                    "se_fold_change",
                    "FDR",
                ],
            )
            writer.writeheader()
            for row in truth:
                positive = row["is_positive"] == "true"
                writer.writerow(
                    {
                        "response_id": row["response_id"],
                        "grna_target": row["grna_target"],
                        "p_value": 0.001 if positive else 0.5,
                        "fold_change": row["true_log_rate_ratio"],
                        "se_fold_change": 0.1,
                        "FDR": 0.01 if positive else 0.5,
                    }
                )
        (output / "sceptre_calibration.csv").write_text(
            "pair,p_value\nnull,0.5\n", encoding="utf-8"
        )
        (output / "sceptre_metadata.json").write_text(
            json.dumps(
                {
                    "calibration_passed": True,
                    "calibration_type1_rate": 0.05,
                    "discovery_executed": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess([str(runner_path)], 0, "", "")

    monkeypatch.setattr(effect_candidates, "_run_r_profile", fake_r_profile)
    execution = runner.run_observed(
        bindings_path=bindings,
        ref06_root=ref06,
        output_root=observed,
        environment_loader=_environment,
    )
    assert execution["passed"] is True
    assert all(execution["hard_gates"].values())

    verdict_path = observed / "verdict.json"
    verdict = evaluator.evaluate(
        observed_root=observed,
        ref06_root=ref06,
        output_path=verdict_path,
    )
    assert verdict["passed"] is True
    assert verdict["metrics"]["type_i_error"]["observed"] == 0.0
    assert verdict["metrics"]["power"]["observed"] == 1.0
    payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert payload["metric_provenance"]["self_reported_capability_metrics_used"] is False
    assert payload["counts"] == {
        "truth_pairs": 100,
        "observed_pairs": 100,
        "positive_pairs": 20,
        "null_pairs": 80,
    }


def test_obs06_evaluator_rejects_good_self_report_when_artifact_is_bad(
    tmp_path: Path,
) -> None:
    runner = _load_script("run_paper_obs06.py")
    evaluator = _load_script("evaluate_paper_obs06.py")
    ref06 = _make_ref06(tmp_path)
    bindings = _make_bindings(tmp_path, ref06)
    observed = tmp_path / "OBS-06-bad"
    execution = runner.run_observed(
        bindings_path=bindings,
        ref06_root=ref06,
        output_root=observed,
        executor=_fake_executor(bad_nulls=True),
        environment_loader=_environment,
    )
    assert execution["passed"] is True

    verdict = evaluator.evaluate(
        observed_root=observed,
        ref06_root=ref06,
        output_path=observed / "verdict.json",
    )
    assert verdict["passed"] is False
    assert verdict["metrics"]["type_i_error"]["observed"] == 1.0
    assert verdict["metrics"]["type_i_error"]["passed"] is False
    assert verdict["metrics"]["fdr"]["passed"] is False
