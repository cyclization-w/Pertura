from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import pandas as pd

from pertura_bench.capability_availability import (
    availability_by_task,
    build_task_capability_availability,
)
from pertura_bench.paper_agent_execution import (
    CAPABILITY_BINDING_QUALIFICATION_STATUSES,
    _artifact_paths_present,
    run_paper_agent_workflow,
)
from pertura_bench.paper_capability_bindings import minimal_binding_arguments
from pertura_bench.paper_tasks import load_paper_task_catalog
from pertura_bench.task_submission import TaskSubmissionService
from pertura_core.hashing import canonical_hash, file_sha256, path_sha256
from pertura_runtime.product_tools import (
    PRODUCT_TOOL_SPECS,
    dispatch_product_tool,
    get_product_tool_spec,
    product_tool_mcp_result,
)
from pertura_runtime.project.models import TurnStatus
from pertura_workflow.capability_contracts import build_capability_contract_catalog

from qualify_a19_evaluators import (
    _fill_protocol_outputs,
    _materialize_generic_positive,
    _materialize_global_effect_positive,
    _materialize_trans_de_positive,
    _positive_result,
)


_TASK_PATTERN = re.compile(r"task ([A-Z]+-[0-9]+) \(turn")

_SUCCESS_STATUSES_BY_TOOL = {
    "run_diagnostic": {"screen_passed", "caution"},
    "run_analysis": {"completed", "completed_with_caution"},
    "evaluate_virtual_model": {"supported", "limited"},
}

_REAL_SCIENTIFIC_PARITY_TASKS = frozenset(
    {"PAPA-02", "PAPA-03", "PAPA-04", "PAPA-05", "KANG-02"}
)

_REAL_SCIENTIFIC_REQUIRED_CAPABILITIES = {
    "PAPA-02": frozenset({"state.reference.fit.v1"}),
    "PAPA-03": frozenset({"state.reference.map_knn.v1"}),
    "PAPA-04": frozenset(
        {"target.guide_efficacy.v1", "effect.guide_target_sensitivity.v1"}
    ),
    "PAPA-05": frozenset(
        {"target.responder.mixscape.v1", "target.reliability.aggregate.v1"}
    ),
    "KANG-02": frozenset({"composition.propeller.v1"}),
}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _task_map(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for workflow in catalog.get("workflows") or ():
        for raw in workflow.get("turns") or ():
            task = dict(raw)
            task["dataset_id"] = str(workflow["dataset_id"])
            task["workflow_id"] = str(workflow["workflow_id"])
            records[str(task["task_id"])] = task
    return records


def _binding_record(binding, **updates: Any) -> dict[str, Any]:
    """Return one complete qualification record, including failed attempts."""

    provider_schema = get_product_tool_spec(binding.tool_name).json_input_schema()
    provider_arguments = minimal_binding_arguments(binding, objective_prefix="Qualify")
    record = {
        "task_id": binding.task_id,
        "binding_id": binding.binding_id,
        "binding_hash": binding.binding_hash,
        "capability_id": binding.capability_id,
        "capability_scientific_hash": binding.capability_scientific_hash,
        "tool_name": binding.tool_name,
        "contract_id": binding.contract_id,
        "contract_hash": binding.contract_hash,
        "scope": dict(binding.scope),
        "bound_parameters_hash": canonical_hash(binding.bound_parameters),
        "input_assets": [item.model_dump(mode="json") for item in binding.input_assets],
        "dependency_result_ids": list(binding.dependency_result_ids),
        "dependency_verification_states": list(binding.dependency_verification_states),
        "dependency_receipt_ids": list(binding.dependency_receipt_ids),
        "dependency_binding_ids": list(binding.dependency_binding_ids),
        "output_mapping": dict(binding.output_mapping),
        "readiness": binding.readiness,
        "blockers": list(binding.blockers),
        "provider_input_schema_hash": canonical_hash(provider_schema),
        "provider_minimal_arguments_hash": canonical_hash(provider_arguments),
        "provider_schema_validation_status": "not_run",
        "provider_result_visibility_status": "not_run",
        "qualification_status": "failed",
        "qualification_error": None,
        "result_id": None,
        "result_hash": None,
        "receipt_id": None,
        "result_status": None,
        "result_blockers": [],
        "result_cautions": [],
        "response_blockers": [],
        "response_required_upstream": [],
        "response_candidate_result_ids": [],
        "response_dependency_verdicts": [],
        "output_hashes": {},
    }
    record.update(updates)
    return record


def _result_outputs(result: Any, workspace_root: Path) -> dict[str, Path]:
    """Resolve and re-verify one committed capability's published outputs."""

    resolved: dict[str, Path] = {}
    for raw in result.output_paths:
        path = Path(str(raw))
        if not path.is_absolute():
            path = workspace_root / path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        name = path.name
        if name in resolved:
            raise RuntimeError(
                f"{result.capability_id}: duplicate published output name: {name}"
            )
        expected = str(result.output_hashes.get(name) or "")
        observed = path_sha256(path)
        if not expected or expected != observed:
            raise RuntimeError(
                f"{result.capability_id}: published output hash drifted: {name}"
            )
        resolved[name] = path
    return resolved


def _write_tsv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows({name: row.get(name) for name in columns} for row in rows)


def _require_ref03_environment_parity(actual: Mapping[str, Any], paper_root: Path) -> None:
    reference = _read_json(paper_root / "references/REF-03/manifest.json")
    actual_versions = dict((actual.get("environment") or {}).get("versions") or {})
    reference_versions = dict(reference.get("environment") or {})
    key_map = {
        "anndata": "anndata",
        "scanpy": "scanpy",
        "scikit_learn": "scikit-learn",
        "igraph": "igraph",
        "leidenalg": "leidenalg",
    }
    mismatches = {
        reference_name: {
            "reference": str(reference_versions.get(reference_name)),
            "actual": str(actual_versions.get(actual_name)),
        }
        for reference_name, actual_name in key_map.items()
        if str(reference_versions.get(reference_name))
        != str(actual_versions.get(actual_name))
    }
    if mismatches:
        raise RuntimeError(f"PAPA-02 REF-03 environment version drift: {mismatches}")


def _require_ref04_environment_parity(actual: Mapping[str, Any], paper_root: Path) -> None:
    reference = _read_json(paper_root / "references/REF-04/manifest.json")
    actual_versions = dict((actual.get("environment") or {}).get("versions") or {})
    reference_versions = dict(reference.get("environment") or {})
    key_map = {
        "anndata": "anndata",
        "scanpy": "scanpy",
        "pertpy": "pertpy",
        "pandas": "pandas",
        "scikit_learn": "scikit-learn",
        "igraph": "igraph",
        "leidenalg": "leidenalg",
        "scipy": "scipy",
    }
    mismatches = {
        reference_name: {
            "reference": str(reference_versions.get(reference_name)),
            "actual": str(actual_versions.get(actual_name)),
        }
        for reference_name, actual_name in key_map.items()
        if str(reference_versions.get(reference_name))
        != str(actual_versions.get(actual_name))
    }
    if mismatches:
        raise RuntimeError(f"PAPA-05 REF-04 environment version drift: {mismatches}")


def _materialize_real_scientific_outputs(
    *,
    task_id: str,
    results: Mapping[str, Any],
    workspace_root: Path,
    output: Path,
    paper_root: Path,
) -> dict[str, str]:
    """Map real capability outputs to the already-frozen public task artifacts.

    This is a qualification-only adapter.  It does not invent a ResultEnvelope
    or measured claim; every scientific value below comes from a committed,
    receipt-backed capability result whose published hash is re-verified first.
    """

    by_capability = {
        capability_id: _result_outputs(result, workspace_root)
        for capability_id, result in results.items()
    }
    output.mkdir(parents=True, exist_ok=True)

    if task_id == "PAPA-02":
        sources = by_capability["state.reference.fit.v1"]
        provenance = _read_json(sources["state_reference_fit.json"])
        _require_ref03_environment_parity(provenance, paper_root)
        model_root = output / "state_reference_model"
        model_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources["state_reference_fit.npz"], model_root / "model.npz")
        assignments = pd.read_parquet(sources["control_state_assignments.parquet"])
        assignments[["cell_id", "technical_state_id"]].to_csv(
            output / "reference_cell_manifest.tsv", sep="\t", index=False
        )
        shutil.copy2(
            sources["state_reference_fit.json"],
            output / "reference_provenance.json",
        )

    elif task_id == "PAPA-03":
        sources = by_capability["state.reference.map_knn.v1"]
        mapping = pd.read_parquet(sources["state_mapping.parquet"])
        mapping.to_csv(output / "state_mapping.tsv", sep="\t", index=False)
        manifest = _read_json(sources["state_mapping.json"])
        (output / "mapping_rejections.json").write_text(
            json.dumps(
                {
                    "schema_version": "pertura-mapping-rejections-v1",
                    "mapping_probability_threshold": manifest[
                        "mapping_probability_threshold"
                    ],
                    "distance_threshold": manifest["distance_threshold"],
                    "rejected_cell_count": int(mapping["rejected"].sum()),
                    "mapped_cell_count": len(mapping),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        result = results["state.reference.map_knn.v1"]
        (output / "dependency_consumption.json").write_text(
            json.dumps(
                {
                    "schema_version": "pertura-dependency-consumption-v1",
                    "result_id": result.result_id,
                    "result_hash": result.canonical_hash,
                    "dependencies": [
                        item.model_dump(mode="json") for item in result.dependencies
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    elif task_id == "PAPA-04":
        efficacy_path = by_capability["target.guide_efficacy.v1"][
            "target_guide_efficacy.json"
        ]
        efficacy = _read_json(efficacy_path)
        if efficacy.get("schema_version") != "pertura-target-guide-efficacy-set-v1":
            raise RuntimeError("PAPA-04 qualification requires batch efficacy output")
        target_rows: list[dict[str, Any]] = []
        guide_rows: list[dict[str, Any]] = []
        for target in efficacy.get("targets") or ():
            evaluation = dict(target.get("evaluation") or {})
            target_uid = str(target.get("target_uid") or "")
            target_rows.append(
                {
                    "target_uid": target_uid,
                    "direct_effect": evaluation.get("direct_effect"),
                    "direction_supported": evaluation.get(
                        "direction_supported", False
                    ),
                }
            )
            for guide, values in dict(evaluation.get("guide_effects") or {}).items():
                guide_rows.append(
                    {
                        "target_uid": target_uid,
                        "guide": str(guide),
                        "effect": values.get("effect"),
                        "direction_supported": values.get(
                            "direction_supported", False
                        ),
                    }
                )
        sensitivity = _read_json(
            by_capability["effect.guide_target_sensitivity.v1"][
                "guide_target_sensitivity.json"
            ]
        )
        sensitivity_rows = [
            {
                "target_uid": str(target.get("target") or ""),
                "excluded_guide": str(guide),
                "leave_one_guide_out_effect": value,
            }
            for target in sensitivity.get("targets") or ()
            for guide, value in dict(target.get("leave_one_guide_out") or {}).items()
        ]
        _write_tsv(
            output / "target_efficacy.tsv",
            target_rows,
            ["target_uid", "direct_effect", "direction_supported"],
        )
        _write_tsv(
            output / "guide_effects.tsv",
            guide_rows,
            ["target_uid", "guide", "effect", "direction_supported"],
        )
        _write_tsv(
            output / "guide_sensitivity.tsv",
            sensitivity_rows,
            ["target_uid", "excluded_guide", "leave_one_guide_out_effect"],
        )

    elif task_id == "PAPA-05":
        mixscape = by_capability["target.responder.mixscape.v1"]
        cells = pd.read_parquet(mixscape["mixscape_cells.parquet"])
        cells.to_csv(output / "mixscape_cells.tsv", sep="\t", index=False)
        summary = _read_json(mixscape["mixscape_summary.json"])
        _require_ref04_environment_parity(summary, paper_root)
        _write_tsv(
            output / "mixscape_targets.tsv",
            [dict(item) for item in summary.get("targets") or ()],
            ["target_uid", "responder_fraction", "escape_fraction"],
        )
        reliability = _read_json(
            by_capability["target.reliability.aggregate.v1"][
                "target_reliability_aggregate.json"
            ]
        )
        _write_tsv(
            output / "target_reliability.tsv",
            [dict(item) for item in reliability.get("target_verdicts") or ()],
            [
                "target_uid",
                "target_gene",
                "status",
                "direct_effect",
                "direction_supported",
                "responder_fraction",
                "escape_fraction",
                "target_specific_join",
                "limitations",
            ],
        )

    elif task_id == "KANG-02":
        sources = by_capability["composition.propeller.v1"]
        shutil.copy2(
            sources["composition_input_accounting.json"],
            output / "composition_input_accounting.json",
        )
        pd.read_csv(sources["propeller_results.csv"]).to_csv(
            output / "propeller_results.tsv", sep="\t", index=False
        )
        shutil.copy2(
            sources["missing_state_exclusions.tsv"],
            output / "missing_state_exclusions.tsv",
        )
    else:
        raise KeyError(task_id)

    return {
        path.relative_to(output).as_posix(): path_sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }


def _materialize_submission(
    *,
    task: Mapping[str, Any],
    reference_binding: Mapping[str, Any],
    output: Path,
    paper_root: Path,
    capability_results: Mapping[str, Any],
    workspace_root: Path,
    scientific_materialization_error: str | None = None,
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    actual_parity = task_id in _REAL_SCIENTIFIC_PARITY_TASKS
    if actual_parity and scientific_materialization_error is None:
        _materialize_real_scientific_outputs(
            task_id=task_id,
            results=capability_results,
            workspace_root=workspace_root,
            output=output,
            paper_root=paper_root,
        )
    elif not actual_parity:
        evaluator_id = str(reference_binding.get("evaluator_id") or "")
        if evaluator_id == "task.trans_de_edger.v1":
            _materialize_trans_de_positive(reference_binding, output, paper_root)
        elif evaluator_id == "task.global_effect_claims.v1":
            _materialize_global_effect_positive(reference_binding, output, paper_root)
        elif reference_binding.get("evaluators"):
            _materialize_generic_positive(reference_binding, output, paper_root)
    _fill_protocol_outputs(task, reference_binding, output)

    # Protocol-only endpoints can lack evaluator-backed files.  Their frozen
    # artifact schemas are sufficient for this execution qualification because
    # the qualification measures binding executability, not scientific scores.
    contract = dict(task.get("output_contract") or {})
    schemas = dict(contract.get("artifact_schemas") or {})
    for relative in (contract.get("artifact_paths") or {}).values():
        path = output / str(relative)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".json":
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "pertura-binding-qualification-artifact-v1",
                        "qualification_only": True,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        else:
            columns = list(schemas.get(str(relative)) or ("qualification_id",))
            path.write_text("\t".join(columns) + "\n", encoding="utf-8")

    result = _positive_result(task, reference_binding)
    result["dataset_id"] = str(task["dataset_id"])
    result["result_type"] = (
        "scientific_method_parity" if actual_parity else "capability_binding_qualification"
    )
    result["limitations"] = [
        (
            "Internal real-output scientific parity qualification; not a paper result."
            if actual_parity
            else "Internal execution qualification; not a scientific benchmark result."
        )
    ]
    if scientific_materialization_error is not None:
        # A failed binding qualification must remain a failed qualification.  Do
        # not replace an unavailable real capability result with an evaluator
        # positive-control fixture merely to keep the workflow running.
        result["status"] = "blocked"
        result["findings"] = []
        result["limitations"] = [
            "Internal real-output scientific parity qualification failed.",
            scientific_materialization_error,
        ]
    if not _artifact_paths_present(output, dict(task.get("output_contract") or {})):
        raise RuntimeError(
            f"{task['task_id']}: qualification fixture violates the public "
            "artifact path/schema contract"
        )
    return result


class _BindingQualificationExecutor:
    def __init__(
        self,
        *,
        tasks: Mapping[str, Mapping[str, Any]],
        references: Mapping[str, Mapping[str, Any]],
        paper_root: Path,
    ) -> None:
        self.tasks = tasks
        self.references = references
        self.paper_root = paper_root
        self.records: list[dict[str, Any]] = []
        self.scientific_artifact_hashes: dict[str, dict[str, str]] = {}
        self.scientific_result_hashes: dict[str, dict[str, str]] = {}
        self.scientific_materialization_errors: dict[str, str | None] = {}

    @staticmethod
    def _persist_attempt(
        *,
        workspace_root: Path,
        task_id: str,
        records: list[dict[str, Any]],
        capability_results: Mapping[str, Any],
        stage: str,
        scientific_materialization_error: str | None = None,
        fatal_error: str | None = None,
    ) -> None:
        """Persist the primary failure before any downstream adapter can fail."""

        root = workspace_root / "qualification_diagnostics"
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "pertura-binding-qualification-attempt-v1",
            "task_id": task_id,
            "stage": stage,
            "records": records,
            "capability_results": {
                capability_id: {
                    "result_id": str(result.result_id),
                    "result_hash": str(result.canonical_hash),
                    "status": str(getattr(result.status, "value", result.status)),
                }
                for capability_id, result in sorted(capability_results.items())
            },
            "scientific_materialization_error": scientific_materialization_error,
            "fatal_error": fatal_error,
        }
        payload["canonical_hash"] = canonical_hash(payload)
        (root / f"{task_id}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def __call__(self, agent, prompt: str, timeout: int):
        del timeout
        match = _TASK_PATTERN.search(prompt)
        if not match:
            raise RuntimeError("qualification callback could not identify the task")
        task_id = match.group(1)
        task = dict(self.tasks[task_id])
        expected_probes = {
            str(item) for item in task.get("expected_probe_capabilities") or ()
        }
        runtime = agent.product_runtime
        project = runtime.project_workspace
        if project is None:
            raise RuntimeError("binding qualification requires a project workspace")
        turn = project.store.begin_turn(agent.conversation_id, prompt)
        result_ids: list[str] = []
        task_records: list[dict[str, Any]] = []
        capability_results: dict[str, Any] = {}
        records_collected = False
        bindings = tuple(runtime._invocation_bindings.values())
        predecessor_binding_ids = {
            predecessor_id
            for binding in bindings
            for predecessor_id in binding.dependency_binding_ids
        }
        try:
            for binding in bindings:
                response = None
                result_status: str | None = None
                result_blockers: list[str] = []
                result_cautions: list[str] = []
                provider_schema_validation_status = "not_run"
                provider_result_visibility_status = "not_run"
                try:
                    provider_schema = get_product_tool_spec(
                        binding.tool_name
                    ).json_input_schema()
                    provider_arguments = minimal_binding_arguments(
                        binding, objective_prefix="Qualify"
                    )
                    Draft202012Validator.check_schema(provider_schema)
                    Draft202012Validator(provider_schema).validate(provider_arguments)
                    provider_schema_validation_status = "passed"
                    response = dispatch_product_tool(
                        runtime,
                        binding.tool_name,
                        provider_arguments,
                    )
                    visible_result = product_tool_mcp_result(response)
                    visible_content = visible_result.get("content") or ()
                    if (
                        len(visible_content) != 1
                        or visible_content[0].get("type") != "text"
                        or json.loads(visible_content[0]["text"]) != response
                    ):
                        raise RuntimeError(
                            "provider-visible MCP result does not preserve the "
                            "product response"
                        )
                    provider_result_visibility_status = "passed"
                except Exception as exc:
                    task_records.append(
                        _binding_record(
                            binding,
                            qualification_status="failed_execution",
                            qualification_error=(f"{type(exc).__name__}: {exc}"),
                            provider_schema_validation_status=(
                                provider_schema_validation_status
                                if provider_schema_validation_status == "passed"
                                else "failed"
                            ),
                            provider_result_visibility_status=(
                                provider_result_visibility_status
                                if provider_result_visibility_status == "passed"
                                else "failed"
                            ),
                        )
                    )
                    continue

                try:
                    result_id = str(response.get("result_id") or "")
                    response_details = {
                        "response_blockers": list(response.get("blockers") or ()),
                        "response_required_upstream": list(
                            response.get("required_upstream") or ()
                        ),
                        "response_candidate_result_ids": list(
                            response.get("candidate_result_ids") or ()
                        ),
                        "response_dependency_verdicts": [
                            dict(item)
                            for item in response.get("dependency_verdicts") or ()
                        ],
                    }
                    if binding.readiness == "blocked_probe":
                        if binding.capability_id not in expected_probes:
                            raise RuntimeError(
                                "unexpected preflight blocker on an advertised "
                                f"non-probe binding: {binding.blockers}"
                            )
                        if response.get("status") != "blocked" or result_id:
                            raise RuntimeError("blocked probe executed")
                        qualification_status = "expected_blocked_probe"
                        result_hash = None
                        output_hashes: Mapping[str, str] = {}
                        result_status = str(response.get("status") or "")
                        result_blockers: list[str] = []
                        result_cautions: list[str] = []
                    else:
                        if not result_id:
                            raise RuntimeError(
                                "ready binding did not produce a committed result; "
                                f"status={response.get('status')}, "
                                f"blockers={response.get('blockers')}"
                            )
                        result = next(
                            (
                                item
                                for item in runtime.planning_material(
                                    binding.contract_id
                                )[1]
                                if item.result_id == result_id
                            ),
                            None,
                        )
                        if result is None:
                            raise RuntimeError("result was not committed")
                        result_status = str(
                            getattr(result.status, "value", result.status)
                        )
                        result_blockers = list(result.blockers)
                        result_cautions = list(result.cautions)
                        successful_statuses = _SUCCESS_STATUSES_BY_TOOL[
                            binding.tool_name
                        ]
                        if result_status not in successful_statuses:
                            # A committed diagnostic block can be the valid
                            # outcome of a terminal design audit.  It cannot,
                            # however, qualify a binding whose result is an
                            # input to another bound capability.
                            if (
                                binding.tool_name == "run_diagnostic"
                                and binding.binding_id not in predecessor_binding_ids
                                and result_status in {"blocked", "unresolved"}
                            ):
                                qualification_status = (
                                    "executed_terminal_diagnostic_block"
                                )
                            else:
                                raise RuntimeError(
                                    "bound execution produced an unusable result; "
                                    f"status={result_status}, "
                                    f"blockers={result_blockers}, "
                                    f"cautions={result_cautions}"
                                )
                        if result.capability_trust.value == "exploratory":
                            if response.get("receipt_id") is not None:
                                raise RuntimeError(
                                    "exploratory result incorrectly received a "
                                    "trusted receipt"
                                )
                        elif not response.get("receipt_id"):
                            raise RuntimeError("trusted result lacks receipt")
                        result_ids.append(result_id)
                        capability_results[binding.capability_id] = result
                        if result_status in successful_statuses:
                            qualification_status = "executed"
                        result_hash = result.canonical_hash
                        output_hashes = dict(result.output_hashes)

                    task_records.append(
                        _binding_record(
                            binding,
                            qualification_status=qualification_status,
                            result_id=result_id or None,
                            result_hash=result_hash,
                            receipt_id=response.get("receipt_id"),
                            result_status=result_status,
                            result_blockers=result_blockers,
                            result_cautions=result_cautions,
                            output_hashes=output_hashes,
                            provider_schema_validation_status="passed",
                            provider_result_visibility_status="passed",
                            **response_details,
                        )
                    )
                except Exception as exc:
                    task_records.append(
                        _binding_record(
                            binding,
                            qualification_status="failed_validation",
                            qualification_error=(f"{type(exc).__name__}: {exc}"),
                            result_id=(str(response.get("result_id") or "") or None),
                            receipt_id=response.get("receipt_id"),
                            result_status=(
                                result_status
                                or str(response.get("status") or "")
                                or None
                            ),
                            result_blockers=result_blockers,
                            result_cautions=result_cautions,
                            response_blockers=list(response.get("blockers") or ()),
                            response_required_upstream=list(
                                response.get("required_upstream") or ()
                            ),
                            response_candidate_result_ids=list(
                                response.get("candidate_result_ids") or ()
                            ),
                            response_dependency_verdicts=[
                                dict(item)
                                for item in response.get("dependency_verdicts") or ()
                            ],
                            provider_schema_validation_status="passed",
                            provider_result_visibility_status="passed",
                        )
                    )
                    continue

            # Collect and persist binding records before scientific artifact
            # adaptation.  This prevents an adapter error from masking the
            # original capability execution or validation failure.
            self.records.extend(task_records)
            records_collected = True
            self._persist_attempt(
                workspace_root=agent.workspace.root,
                task_id=task_id,
                records=task_records,
                capability_results=capability_results,
                stage="bindings_complete",
            )

            output = agent.workspace.root / "outputs" / "tasks" / task_id
            output.mkdir(parents=True, exist_ok=True)
            scientific_materialization_error: str | None = None
            if task_id in _REAL_SCIENTIFIC_PARITY_TASKS:
                missing = sorted(
                    _REAL_SCIENTIFIC_REQUIRED_CAPABILITIES[task_id]
                    - set(capability_results)
                )
                if missing:
                    scientific_materialization_error = (
                        f"{task_id}: real scientific outputs are unavailable because "
                        f"committed capability results are missing: {missing}"
                    )
            if scientific_materialization_error is None:
                try:
                    result = _materialize_submission(
                        task=task,
                        reference_binding=self.references.get(task_id, {}),
                        output=output,
                        paper_root=self.paper_root,
                        capability_results=capability_results,
                        workspace_root=agent.workspace.root,
                    )
                except Exception as exc:
                    if task_id not in _REAL_SCIENTIFIC_PARITY_TASKS:
                        raise
                    scientific_materialization_error = (
                        f"{task_id}: real scientific output materialization failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
            if scientific_materialization_error is not None:
                result = _materialize_submission(
                    task=task,
                    reference_binding=self.references.get(task_id, {}),
                    output=output,
                    paper_root=self.paper_root,
                    capability_results=capability_results,
                    workspace_root=agent.workspace.root,
                    scientific_materialization_error=(
                        scientific_materialization_error
                    ),
                )
            if task_id in _REAL_SCIENTIFIC_PARITY_TASKS:
                self.scientific_materialization_errors[task_id] = (
                    scientific_materialization_error
                )
                self.scientific_artifact_hashes[task_id] = {
                    path.relative_to(output).as_posix(): path_sha256(path)
                    for path in sorted(output.rglob("*"))
                    if path.is_file()
                }
                self.scientific_result_hashes[task_id] = {
                    capability_id: result.canonical_hash
                    for capability_id, result in sorted(capability_results.items())
                }
            allowed_units = tuple(
                str(item)
                for item in (
                    (task.get("output_contract") or {}).get("allowed_analysis_units")
                    or ()
                )
            )
            service = TaskSubmissionService(agent.workspace.root)
            service.bind_task(
                task_id=task_id,
                dataset_id=str(task["dataset_id"]),
                allowed_analysis_units=allowed_units,
            )
            accepted = service.submit_task_bundle(
                {
                    "benchmark_result": result,
                    "turn_draft": {
                        "schema_version": "pertura-turn-draft-v1",
                        "headline": f"Qualified bound capability surface for {task_id}",
                        "limitations": [
                            "Internal execution qualification; no scientific claim."
                        ],
                    },
                }
            )
            if accepted.get("accepted") is not True:
                raise RuntimeError(
                    f"{task_id}: qualification submission failed: "
                    f"{accepted.get('errors')}"
                )
            project.store.complete_turn(
                turn.turn_id,
                status=TurnStatus.completed,
                provider_final="binding qualification completed",
                result_ids=tuple(result_ids),
                trace={"binding_qualification": True},
            )
            self._persist_attempt(
                workspace_root=agent.workspace.root,
                task_id=task_id,
                records=task_records,
                capability_results=capability_results,
                stage="submission_complete",
                scientific_materialization_error=scientific_materialization_error,
            )
            agent.manifest = SimpleNamespace(
                result_subtype="success",
                num_turns=1,
                message_count=1,
                total_cost_usd=0.0,
            )
            return SimpleNamespace(
                status="completed", error=None, result_subtype="success"
            )
        except Exception as exc:
            if not records_collected:
                self.records.extend(task_records)
            self._persist_attempt(
                workspace_root=agent.workspace.root,
                task_id=task_id,
                records=task_records,
                capability_results=capability_results,
                stage="fatal_error",
                fatal_error=f"{type(exc).__name__}: {exc}",
            )
            project.store.complete_turn(
                turn.turn_id,
                status=TurnStatus.failed,
                provider_final=None,
                result_ids=tuple(result_ids),
                trace={"binding_qualification": True, "failed": True},
            )
            raise


def qualify(
    *,
    repo: Path,
    wheel: Path,
    task_catalog_path: Path,
    task_reference_catalog_path: Path,
    paper_anchor_catalog_path: Path,
    asset_catalog_path: Path,
    capability_contract_catalog_path: Path,
    paper_root: Path,
    cache: Path,
    resource_lock_path: Path,
    work_root: Path,
) -> dict[str, Any]:
    raw_catalog = _read_json(task_catalog_path)
    loaded_catalog = load_paper_task_catalog(task_catalog_path)
    tasks = _task_map(raw_catalog)
    references_payload = _read_json(task_reference_catalog_path)
    references = {
        str(item["task_id"]): item for item in references_payload.get("bindings") or ()
    }
    availability = availability_by_task(
        build_task_capability_availability(
            raw_catalog, build_capability_contract_catalog()
        )
    )
    configured_tasks = {
        task_id
        for task_id, record in availability.items()
        if record["advertised_capability_ids"]
        and tasks[task_id].get("role") != "optional"
    }

    records: list[dict[str, Any]] = []
    workflow_runs: list[dict[str, Any]] = []
    scientific_parity_records: list[dict[str, Any]] = []
    work_root.mkdir(parents=True, exist_ok=True)
    for workflow in loaded_catalog.workflows:
        workflow_id = str(workflow["workflow_id"])
        if not any(
            str(task["task_id"]) in configured_tasks
            for task in workflow.get("turns") or ()
        ):
            continue
        workflow_work = work_root / workflow_id
        workflow_work.mkdir(parents=True, exist_ok=True)
        memory_gb = 48 if workflow_id == "WF-REPL" else 32
        resource_path = workflow_work / "resource-evidence.json"
        resource_path.write_text(
            json.dumps(
                {
                    "schema_version": "pertura-resource-evidence-v1",
                    "mode": "scheduler",
                    "scheduler_job_id": (
                        os.environ.get("SLURM_JOB_ID") or "a19-binding-qualification"
                    ),
                    "requested_memory_gb": memory_gb,
                    "actual_memory_gb": memory_gb,
                    "cpu_count": 1,
                    "n_jobs": 1,
                    "timeout_seconds": 14400,
                    "peak_rss_mb": 0,
                    "wall_clock_seconds": 0,
                    "thread_environment": {
                        "OMP_NUM_THREADS": "1",
                        "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1",
                        "NUMEXPR_NUM_THREADS": "1",
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        executor = _BindingQualificationExecutor(
            tasks=tasks,
            references=references,
            paper_root=paper_root,
        )
        workflow_result = run_paper_agent_workflow(
            workflow_id,
            repo_root=repo,
            cache=cache,
            paper_root=paper_root,
            output=workflow_work / "runs",
            condition="pertura_full",
            repeat_index=1,
            task_catalog_path=task_catalog_path,
            task_reference_catalog_path=task_reference_catalog_path,
            paper_anchor_catalog_path=paper_anchor_catalog_path,
            asset_catalog_path=asset_catalog_path,
            capability_contract_catalog_path=capability_contract_catalog_path,
            resource_evidence_path=resource_path,
            turn_executor=executor,
            verify_checkpoint=False,
        )
        records.extend(executor.records)
        verdicts_by_task = {
            str(item["task_id"]): _read_json(Path(str(item["verdict"])))
            for item in workflow_result.get("task_records") or ()
            if item.get("verdict")
        }
        for task_id in sorted(executor.scientific_artifact_hashes):
            verdict = verdicts_by_task.get(task_id)
            if verdict is None:
                raise RuntimeError(
                    f"{task_id}: real scientific parity verdict was not written"
                )
            scientific_parity_records.append(
                {
                    "task_id": task_id,
                    "status": str(
                        (verdict.get("task_evaluation") or {}).get("status")
                        or "not_available"
                    ),
                    "task_status": str(verdict.get("status") or "failed"),
                    "evaluation": verdict.get("task_evaluation"),
                    "evaluation_hash": canonical_hash(
                        verdict.get("task_evaluation") or {}
                    ),
                    "observed_artifact_hashes": executor.scientific_artifact_hashes[
                        task_id
                    ],
                    "capability_result_hashes": executor.scientific_result_hashes[
                        task_id
                    ],
                    "materialization_error": (
                        executor.scientific_materialization_errors.get(task_id)
                    ),
                }
            )
        workflow_runs.append(
            {
                "workflow_id": workflow_id,
                "analysis_run_id": workflow_result["analysis_run_id"],
                "execution_status": workflow_result["execution_status"],
                "record_count": len(executor.records),
            }
        )

    expected_binding_count = sum(
        len(availability[task_id]["advertised_capability_ids"])
        for task_id in configured_tasks
    )
    expected_pairs = {
        (task_id, capability_id)
        for task_id in configured_tasks
        for capability_id in availability[task_id]["advertised_capability_ids"]
    }
    observed_pairs = [
        (str(item["task_id"]), str(item["capability_id"])) for item in records
    ]
    if len(records) != expected_binding_count:
        raise RuntimeError(
            "binding qualification coverage mismatch: "
            f"expected {expected_binding_count}, observed {len(records)}"
        )
    if (
        len(set(observed_pairs)) != len(observed_pairs)
        or set(observed_pairs) != expected_pairs
    ):
        raise RuntimeError(
            "binding qualification task/capability coverage drifted: "
            f"missing={sorted(expected_pairs - set(observed_pairs))}, "
            f"extra={sorted(set(observed_pairs) - expected_pairs)}"
        )
    observed_scientific_parity = {
        str(item["task_id"]) for item in scientific_parity_records
    }
    if observed_scientific_parity != _REAL_SCIENTIFIC_PARITY_TASKS:
        raise RuntimeError(
            "real scientific parity coverage drifted: "
            f"missing={sorted(_REAL_SCIENTIFIC_PARITY_TASKS - observed_scientific_parity)}, "
            f"extra={sorted(observed_scientific_parity - _REAL_SCIENTIFIC_PARITY_TASKS)}"
        )
    failed_records = [
        item
        for item in records
        if item["qualification_status"] not in CAPABILITY_BINDING_QUALIFICATION_STATUSES
    ]
    failed_scientific_parity = [
        item
        for item in scientific_parity_records
        if item["status"] != "passed"
        or item["task_status"] != "passed"
        or item["materialization_error"] is not None
    ]
    failure_summary = [
        {
            "task_id": item["task_id"],
            "capability_id": item["capability_id"],
            "binding_id": item["binding_id"],
            "readiness": item["readiness"],
            "blockers": item["blockers"],
            "qualification_status": item["qualification_status"],
            "qualification_error": item["qualification_error"],
            "result_status": item["result_status"],
            "result_blockers": item["result_blockers"],
            "result_cautions": item["result_cautions"],
            "response_blockers": item["response_blockers"],
            "response_required_upstream": item["response_required_upstream"],
            "response_candidate_result_ids": item["response_candidate_result_ids"],
            "response_dependency_verdicts": item["response_dependency_verdicts"],
        }
        for item in failed_records
    ]

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    payload = {
        "schema_version": "pertura-capability-binding-qualification-v1",
        "status": "failed" if failed_records or failed_scientific_parity else "passed",
        "passed": not failed_records and not failed_scientific_parity,
        "git_commit": commit,
        "wheel_sha256": file_sha256(wheel),
        "task_catalog_sha256": file_sha256(task_catalog_path),
        "task_reference_catalog_sha256": file_sha256(task_reference_catalog_path),
        "paper_asset_catalog_sha256": file_sha256(asset_catalog_path),
        "capability_contract_catalog_sha256": file_sha256(
            capability_contract_catalog_path
        ),
        "resource_lock_sha256": file_sha256(resource_lock_path),
        "qualified_task_count": len({str(item["task_id"]) for item in records}),
        "qualified_binding_count": len(records),
        "provider_schema_parity_passed": all(
            item.get("provider_schema_validation_status") == "passed"
            for item in records
        ),
        "provider_result_visibility_passed": all(
            item.get("provider_result_visibility_status") == "passed"
            for item in records
        ),
        "provider_tool_schema_hashes": {
            spec.name: canonical_hash(spec.json_input_schema())
            for spec in PRODUCT_TOOL_SPECS
        },
        "expected_blocked_probe_count": sum(
            item["qualification_status"] == "expected_blocked_probe" for item in records
        ),
        "terminal_diagnostic_block_count": sum(
            item["qualification_status"] == "executed_terminal_diagnostic_block"
            for item in records
        ),
        "optional_unconfigured_task_ids": sorted(
            task_id for task_id, task in tasks.items() if task.get("role") == "optional"
        ),
        "workflow_runs": workflow_runs,
        "failure_summary": failure_summary,
        "scientific_parity_task_ids": sorted(_REAL_SCIENTIFIC_PARITY_TASKS),
        "scientific_parity_passed": not failed_scientific_parity,
        "scientific_parity_failure_summary": failed_scientific_parity,
        "scientific_parity_records": scientific_parity_records,
        "records": records,
    }
    payload["canonical_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--task-reference-catalog", type=Path, required=True)
    parser.add_argument("--paper-anchor-catalog", type=Path, required=True)
    parser.add_argument("--asset-catalog", type=Path, required=True)
    parser.add_argument("--capability-contract-catalog", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--resource-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args()

    if args.work_root is None:
        temporary_parent = args.paper_root.parent / "tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="a19-binding-qualification-",
            dir=temporary_parent,
        ) as temporary:
            payload = qualify(
                repo=args.repo.resolve(),
                wheel=args.wheel.resolve(),
                task_catalog_path=args.task_catalog.resolve(),
                task_reference_catalog_path=args.task_reference_catalog.resolve(),
                paper_anchor_catalog_path=args.paper_anchor_catalog.resolve(),
                asset_catalog_path=args.asset_catalog.resolve(),
                capability_contract_catalog_path=(
                    args.capability_contract_catalog.resolve()
                ),
                paper_root=args.paper_root.resolve(),
                cache=args.cache.resolve(),
                resource_lock_path=args.resource_lock.resolve(),
                work_root=Path(temporary),
            )
    else:
        payload = qualify(
            repo=args.repo.resolve(),
            wheel=args.wheel.resolve(),
            task_catalog_path=args.task_catalog.resolve(),
            task_reference_catalog_path=args.task_reference_catalog.resolve(),
            paper_anchor_catalog_path=args.paper_anchor_catalog.resolve(),
            asset_catalog_path=args.asset_catalog.resolve(),
            capability_contract_catalog_path=args.capability_contract_catalog.resolve(),
            paper_root=args.paper_root.resolve(),
            cache=args.cache.resolve(),
            resource_lock_path=args.resource_lock.resolve(),
            work_root=args.work_root.resolve(),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
