from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_bench.paper_tasks import (
    PAPER_SCIENTIFIC_EVALUATOR_TASKS,
    load_paper_task_catalog,
    validate_task_reference_catalog,
)
from pertura_core.hashing import canonical_hash, file_sha256


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _reference(path: str, digest: str, paper_root: Path) -> Path:
    resolved = (paper_root / path).resolve()
    if resolved != paper_root and paper_root not in resolved.parents:
        raise ValueError(f"reference escapes paper root: {path}")
    if not resolved.is_file() or file_sha256(resolved) != digest:
        raise ValueError(f"reference is missing or drifted: {path}")
    return resolved


def _filtered_reference(spec: Mapping[str, Any], paper_root: Path) -> pd.DataFrame:
    source = _reference(
        str(spec["reference_path"]),
        str(spec["reference_sha256"]),
        paper_root,
    )
    frame = pd.read_csv(source, sep="\t")
    for column, expected in (spec.get("reference_filters") or {}).items():
        if column not in frame.columns:
            raise ValueError(f"reference filter column is missing: {column}")
        frame = frame.loc[
            frame[column].astype(str).str.lower() == str(expected).lower()
        ]
    return frame.reset_index(drop=True)


def _materialize_generic_positive(
    binding: Mapping[str, Any], output: Path, paper_root: Path
) -> list[tuple[Path, tuple[str, ...], Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for evaluator in binding.get("evaluators") or ():
        grouped.setdefault(str(evaluator["observed_output"]), []).append(evaluator)
    observed: list[tuple[Path, tuple[str, ...], Mapping[str, Any]]] = []
    for relative, evaluators in grouped.items():
        frame = _filtered_reference(evaluators[0], paper_root)
        for spec in evaluators:
            for observed_name, reference_name in (
                (spec.get("observed_label_column"), spec.get("reference_label_column")),
                (spec.get("observed_value_column"), spec.get("reference_value_column")),
            ):
                if observed_name and observed_name not in frame.columns:
                    if not reference_name or reference_name not in frame.columns:
                        raise ValueError(
                            f"cannot construct observed column {observed_name}: {relative}"
                        )
                    frame[str(observed_name)] = frame[str(reference_name)]
            if spec.get("type") == "posterior_calibration":
                probability_column = str(spec.get("probability_column") or "")
                label_column = str(spec.get("reference_label_column") or "")
                if probability_column and probability_column not in frame.columns:
                    if not label_column or label_column not in frame.columns:
                        raise ValueError(
                            "cannot construct posterior-calibration positive "
                            f"column {probability_column}: {relative}"
                        )
                    labels = pd.to_numeric(frame[label_column], errors="coerce")
                    if labels.isna().any() or not set(labels.unique()).issubset(
                        {0.0, 1.0}
                    ):
                        raise ValueError(
                            "posterior-calibration reference labels must be binary: "
                            f"{relative}"
                        )
                    # The qualification positive is deliberately perfect: its
                    # probability is the frozen binary reference label. This
                    # tests that the evaluator can accept a valid artifact
                    # without changing the reference or formal thresholds.
                    frame[probability_column] = labels.astype(float)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(destination, sep="\t", index=False)
        keys = tuple(str(item) for item in evaluators[0].get("key_columns") or ())
        observed.append((destination, keys, evaluators[0]))
    return observed


def _materialize_trans_de_positive(
    binding: Mapping[str, Any], output: Path, paper_root: Path
) -> list[tuple[Path, tuple[str, ...], Mapping[str, Any]]]:
    spec = dict(binding["bound_evaluator"])
    observed = output / str(spec["observed_output"])
    design = output / str(spec["design_matrices_output"])
    observed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _reference(
            str(spec["reference_path"]), str(spec["reference_sha256"]), paper_root
        ),
        observed,
    )
    shutil.copyfile(
        _reference(
            str(spec["design_reference_path"]),
            str(spec["design_reference_sha256"]),
            paper_root,
        ),
        design,
    )
    design_frame = pd.read_csv(design, sep="\t")
    design_frame["condition_label"] = design_frame.apply(
        lambda row: (
            "NTC"
            if str(row["condition_label"]).casefold() == "control"
            else str(row["target_uid"])
        ),
        axis=1,
    )
    design_frame.to_csv(design, sep="\t", index=False)
    eligibility = pd.read_csv(
        _reference(
            str(spec["eligibility_path"]),
            str(spec["eligibility_sha256"]),
            paper_root,
        ),
        sep="\t",
    )
    targets = sorted(
        eligibility.loc[
            eligibility["eligible"].astype(str).str.lower() == "true", "target_uid"
        ].astype(str)
    )
    (output / str(spec["design_manifest_output"])).write_text(
        json.dumps(
            {
                "formula": "~ replicate + condition",
                "baseline": "NTC",
                "robust": True,
                "cell_is_replicate": False,
                "guide_is_replicate": False,
                "minimum_paired_replicates": 2,
                "targets": targets,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (output / str(spec["summary_output"])).write_text(
        json.dumps(
            {"eligible_targets": targets, "target_count": len(targets)},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return [(observed, ("target_uid", "gene"), spec)]


def _materialize_global_effect_positive(
    binding: Mapping[str, Any], output: Path, paper_root: Path
) -> list[tuple[Path, tuple[str, ...], Mapping[str, Any]]]:
    spec = dict(binding["bound_evaluator"])
    evidence = pd.read_csv(
        _reference(
            str(spec["evidence_path"]), str(spec["evidence_sha256"]), paper_root
        ),
        sep="\t",
    )

    def claim_class(fdr: float) -> str:
        if fdr <= 0.05:
            return "detectable"
        if fdr <= 0.10:
            return "borderline"
        return "not_detected"

    observed = output / str(spec["observed_output"])
    observed.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "target_uid": str(row.target_uid),
                "claim_class": claim_class(float(row.FDR)),
                "interpretation": "Evidence is reported within the registered test scope.",
            }
            for row in evidence.itertuples(index=False)
        ]
    ).to_csv(observed, sep="\t", index=False)
    (output / str(spec["limitations_output"])).write_text(
        json.dumps(
            {"limitations": ["The registered test does not establish mechanism."]}
        ),
        encoding="utf-8",
    )
    return [(observed, ("target_uid",), spec)]


def _positive_result(
    task: Mapping[str, Any], binding: Mapping[str, Any]
) -> dict[str, Any]:
    protocol = dict(binding.get("protocol_evaluator") or {})
    allowed_status = list(protocol.get("allowed_status") or ("completed",))
    allowed_units = list(
        protocol.get("allowed_analysis_units")
        or (task.get("output_contract") or {}).get("allowed_analysis_units")
        or ("dataset",)
    )
    if task["task_id"] == "PAPA-06":
        allowed_units = ["target_by_replicate_pseudobulk"]
    return {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": str(task["task_id"]),
        "dataset_id": str(task.get("dataset_id") or "qualification"),
        "result_type": "evaluator_qualification_positive",
        "analysis_unit": str(allowed_units[0]),
        "status": str(allowed_status[0]),
        "findings": [
            {
                "finding_id": "qualification",
                "text": "Answer-free positive-control output for evaluator qualification.",
                "metric_ids": [],
                "artifact_roles": list(task.get("required_artifact_roles") or ()),
            }
        ],
        "metrics": {},
        "limitations": ["Evaluator qualification fixture; not a scientific claim."],
        "artifact_roles": list(task.get("required_artifact_roles") or ()),
    }


def _fill_protocol_outputs(
    task: Mapping[str, Any], binding: Mapping[str, Any], output: Path
) -> None:
    contract = dict(task.get("output_contract") or {})
    schemas = dict(contract.get("artifact_schemas") or {})
    for relative in (contract.get("artifact_paths") or {}).values():
        path = output / str(relative)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text("{}\n", encoding="utf-8")
        else:
            columns = schemas.get(str(relative)) or ["qualification_id"]
            path.write_text("\t".join(columns) + "\n", encoding="utf-8")

    protocol = dict(binding.get("protocol_evaluator") or {})
    for relative, count in (protocol.get("required_table_row_counts") or {}).items():
        path = output / str(relative)
        columns = schemas.get(str(relative)) or ["qualification_id"]
        rows = [
            {column: f"{column}_{index}" for column in columns}
            for index in range(int(count))
        ]
        pd.DataFrame(rows, columns=columns).to_csv(path, sep="\t", index=False)
    for relative, values in (protocol.get("required_json_values") or {}).items():
        payload = dict(values)
        for balance in protocol.get("required_json_balances") or ():
            if str(balance.get("output")) != str(relative):
                continue
            for part in balance.get("parts") or ():
                payload.setdefault(str(part), 0)
            payload[str(balance["total"])] = sum(
                float(payload[str(part)]) for part in balance.get("parts") or ()
            )
        (output / str(relative)).write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8"
        )


def _evaluate(
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    output: Path,
    paper_root: Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    return evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper_root,
        bindings=[binding],
    )


def _tree_hash(root: Path) -> str:
    return canonical_hash(
        [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
    )


def _require_failed(
    name: str,
    verdict: Mapping[str, Any],
    *,
    task_id: str,
) -> dict[str, Any]:
    status = str(verdict.get("status") or "")
    if status != "failed":
        raise RuntimeError(f"{task_id}: {name} negative control {status or 'missing'}")
    return {"status": "failed", "verdict_hash": canonical_hash(verdict)}


def _numeric_observed_column(spec: Mapping[str, Any]) -> str | None:
    candidates = [
        *(str(item) for item in spec.get("value_columns") or ()),
        str(spec.get("observed_value_column") or ""),
        str(spec.get("probability_column") or ""),
        str(spec.get("pvalue_column") or ""),
    ]
    return next((item for item in candidates if item), None)


def _negative_controls(
    *,
    task: Mapping[str, Any],
    result: Mapping[str, Any],
    binding: Mapping[str, Any],
    positive_root: Path,
    control_root: Path,
    observed: list[tuple[Path, tuple[str, ...], Mapping[str, Any]]],
    paper_root: Path,
) -> dict[str, Any]:
    """Exercise only answer-independent attacks against a qualified positive."""

    task_id = str(task["task_id"])
    if not observed:
        raise RuntimeError(f"{task_id}: evaluator has no observed artifact")
    controls: dict[str, Any] = {"artifact_controls": {}, "result_controls": {}}
    for artifact_index, (source_path, keys, primary_spec) in enumerate(observed):
        relative = source_path.relative_to(positive_root)
        artifact_name = relative.as_posix()
        artifact_controls: dict[str, Any] = {}
        controls["artifact_controls"][artifact_name] = artifact_controls

        missing_root = control_root / f"artifact-{artifact_index}-missing"
        shutil.copytree(positive_root, missing_root)
        (missing_root / relative).unlink()
        artifact_controls["missing_artifact"] = _require_failed(
            f"{artifact_name}:missing_artifact",
            _evaluate(task, result, missing_root, paper_root, binding),
            task_id=task_id,
        )

        if source_path.suffix != ".tsv" or not keys:
            continue
        frame = pd.read_csv(source_path, sep="\t")
        if frame.empty:
            raise RuntimeError(f"{task_id}: positive observed artifact is empty")

        missing_key_root = control_root / f"artifact-{artifact_index}-missing-key"
        shutil.copytree(positive_root, missing_key_root)
        missing_key_path = missing_key_root / relative
        frame.drop(columns=[keys[0]]).to_csv(missing_key_path, sep="\t", index=False)
        artifact_controls["missing_key"] = _require_failed(
            f"{artifact_name}:missing_key",
            _evaluate(task, result, missing_key_root, paper_root, binding),
            task_id=task_id,
        )

        duplicate_root = control_root / f"artifact-{artifact_index}-duplicate"
        shutil.copytree(positive_root, duplicate_root)
        duplicate_path = duplicate_root / relative
        pd.concat([frame, frame.iloc[[0]]], ignore_index=True).to_csv(
            duplicate_path, sep="\t", index=False
        )
        artifact_controls["duplicate_key"] = _require_failed(
            f"{artifact_name}:duplicate_key",
            _evaluate(task, result, duplicate_root, paper_root, binding),
            task_id=task_id,
        )

        row_root = control_root / f"artifact-{artifact_index}-row-universe"
        shutil.copytree(positive_root, row_root)
        row_path = row_root / relative
        frame.iloc[1:].to_csv(row_path, sep="\t", index=False)
        artifact_controls["wrong_row_universe"] = _require_failed(
            f"{artifact_name}:wrong_row_universe",
            _evaluate(task, result, row_root, paper_root, binding),
            task_id=task_id,
        )

        matching_specs = [
            spec
            for spec in binding.get("evaluators") or (primary_spec,)
            if str(spec.get("observed_output") or "") == artifact_name
        ]
        classification_spec = next(
            (
                spec
                for spec in matching_specs
                if str(spec.get("type") or "") == "classification"
            ),
            None,
        )
        if classification_spec is not None:
            label_column = str(
                classification_spec.get("observed_label_column") or ""
            )
            if label_column in frame.columns:
                label_root = control_root / f"artifact-{artifact_index}-wrong-label"
                shutil.copytree(positive_root, label_root)
                label_path = label_root / relative
                attacked = frame.copy()
                if classification_spec.get("label_type") == "boolean":
                    attacked[label_column] = attacked[label_column].map(
                        lambda value: not _strict_fixture_boolean(value)
                    )
                    control_name = "wrong_boolean_label"
                else:
                    attacked[label_column] = "__invalid_evaluator_label__"
                    control_name = "wrong_categorical_label"
                attacked.to_csv(label_path, sep="\t", index=False)
                artifact_controls[control_name] = _require_failed(
                    f"{artifact_name}:{control_name}",
                    _evaluate(task, result, label_root, paper_root, binding),
                    task_id=task_id,
                )

        rejection_spec = next(
            (
                spec
                for spec in matching_specs
                if str(spec.get("type") or "") == "cluster_agreement"
                and spec.get("rejection_column")
            ),
            None,
        )
        if rejection_spec is not None:
            rejection_column = str(rejection_spec["rejection_column"])
            if rejection_column in frame.columns:
                rejection_root = (
                    control_root / f"artifact-{artifact_index}-wrong-rejection"
                )
                shutil.copytree(positive_root, rejection_root)
                rejection_path = rejection_root / relative
                attacked = frame.copy()
                attacked[rejection_column] = attacked[rejection_column].map(
                    lambda value: not _strict_fixture_boolean(value)
                )
                attacked.to_csv(rejection_path, sep="\t", index=False)
                artifact_controls["wrong_boolean_rejection"] = _require_failed(
                    f"{artifact_name}:wrong_boolean_rejection",
                    _evaluate(task, result, rejection_root, paper_root, binding),
                    task_id=task_id,
                )
        numeric_column = (
            "logFC"
            if task_id == "PAPA-06"
            else next(
                (
                    column
                    for spec in matching_specs
                    if (column := _numeric_observed_column(spec)) is not None
                ),
                None,
            )
        )
        if numeric_column and numeric_column in frame.columns:
            nonfinite_root = control_root / f"artifact-{artifact_index}-nonfinite"
            shutil.copytree(positive_root, nonfinite_root)
            nonfinite_path = nonfinite_root / relative
            attacked = frame.copy()
            attacked.loc[attacked.index[0], numeric_column] = float("inf")
            attacked.to_csv(nonfinite_path, sep="\t", index=False)
            artifact_controls["nonfinite_value"] = _require_failed(
                f"{artifact_name}:nonfinite_value",
                _evaluate(task, result, nonfinite_root, paper_root, binding),
                task_id=task_id,
            )

            numeric_root = control_root / f"artifact-{artifact_index}-wrong-value"
            shutil.copytree(positive_root, numeric_root)
            numeric_path = numeric_root / relative
            attacked = frame.copy()
            attacked.loc[attacked.index[0], numeric_column] = 1_000_000.0
            attacked.to_csv(numeric_path, sep="\t", index=False)
            artifact_controls["wrong_effect_or_probability"] = _require_failed(
                f"{artifact_name}:wrong_effect_or_probability",
                _evaluate(task, result, numeric_root, paper_root, binding),
                task_id=task_id,
            )

    protocol = dict(binding.get("protocol_evaluator") or {})
    if protocol.get("allowed_analysis_units") or task_id == "PAPA-06":
        wrong_unit = dict(result)
        wrong_unit["analysis_unit"] = "cell_as_biological_replicate"
        controls["result_controls"]["wrong_analysis_unit"] = _require_failed(
            "wrong_analysis_unit",
            _evaluate(task, wrong_unit, positive_root, paper_root, binding),
            task_id=task_id,
        )

    if task_id == "PAPA-06":
        cell_root = control_root / "cells-as-replicates"
        shutil.copytree(positive_root, cell_root)
        spec = dict(binding["bound_evaluator"])
        design_path = cell_root / str(spec["design_manifest_output"])
        design = _read_json(design_path)
        design["cell_is_replicate"] = True
        design_path.write_text(json.dumps(design), encoding="utf-8")
        controls["result_controls"]["cells_as_replicates"] = _require_failed(
            "cells_as_replicates",
            _evaluate(task, result, cell_root, paper_root, binding),
            task_id=task_id,
        )
        role_root = control_root / "unknown-condition-role"
        shutil.copytree(positive_root, role_root)
        role_spec = dict(binding["bound_evaluator"])
        role_path = role_root / str(role_spec["design_matrices_output"])
        role_frame = pd.read_csv(role_path, sep="\t")
        role_frame.loc[role_frame.index[0], "condition_label"] = "unknown-arm"
        role_frame.to_csv(role_path, sep="\t", index=False)
        controls["result_controls"]["unknown_condition_role"] = _require_failed(
            "unknown_condition_role",
            _evaluate(task, result, role_root, paper_root, binding),
            task_id=task_id,
        )

    if task_id == "PAPA-07":
        overclaim_root = control_root / "overclaim"
        shutil.copytree(positive_root, overclaim_root)
        spec = dict(binding["bound_evaluator"])
        claim_path = overclaim_root / str(spec["observed_output"])
        claims = pd.read_csv(claim_path, sep="\t")
        candidates = claims.index[claims["claim_class"] != "detectable"]
        if len(candidates) == 0:
            raise RuntimeError("PAPA-07: no non-detectable row for overclaim control")
        claims.loc[candidates[0], "claim_class"] = "detectable"
        claims.loc[
            candidates[0], "interpretation"
        ] = "A causal mechanism is established by this measurement."
        claims.to_csv(claim_path, sep="\t", index=False)
        controls["result_controls"]["overclaim"] = _require_failed(
            "overclaim",
            _evaluate(task, result, overclaim_root, paper_root, binding),
            task_id=task_id,
        )
    return controls


def _strict_fixture_boolean(value: Any) -> bool:
    text = str(value).strip().casefold()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"qualification fixture contains invalid boolean: {value}")


def qualify(
    *,
    repo: Path,
    wheel: Path,
    task_catalog_path: Path,
    task_reference_catalog_path: Path,
    paper_root: Path,
    resource_lock_path: Path,
) -> dict[str, Any]:
    task_catalog = load_paper_task_catalog(task_catalog_path)
    references = _read_json(task_reference_catalog_path)
    problems = validate_task_reference_catalog(references, task_catalog.tasks())
    if problems:
        raise ValueError("invalid task reference catalog: " + "; ".join(problems))
    bindings = {
        str(binding["task_id"]): binding for binding in references.get("bindings") or ()
    }
    tasks = {
        str(task["task_id"]): task
        for workflow in task_catalog.workflows
        for task in workflow.get("turns") or ()
    }
    missing = sorted(PAPER_SCIENTIFIC_EVALUATOR_TASKS - set(bindings))
    if missing:
        raise ValueError(f"scientific evaluator bindings are missing: {missing}")

    records = []
    with tempfile.TemporaryDirectory(prefix="pertura-a19-evaluator-") as temporary:
        root = Path(temporary)
        for task_id in sorted(PAPER_SCIENTIFIC_EVALUATOR_TASKS):
            task = tasks[task_id]
            binding = bindings[task_id]
            output = root / task_id / "positive"
            output.mkdir(parents=True)
            evaluator_id = str(binding["evaluator_id"])
            if evaluator_id == "task.trans_de_edger.v1":
                observed = _materialize_trans_de_positive(binding, output, paper_root)
            elif evaluator_id == "task.global_effect_claims.v1":
                observed = _materialize_global_effect_positive(
                    binding, output, paper_root
                )
            else:
                observed = _materialize_generic_positive(binding, output, paper_root)
            _fill_protocol_outputs(task, binding, output)
            result = _positive_result(task, binding)
            positive = _evaluate(task, result, output, paper_root, binding)
            if positive.get("status") != "passed":
                raise RuntimeError(f"{task_id}: positive control failed: {positive}")

            negative_controls = _negative_controls(
                task=task,
                result=result,
                binding=binding,
                positive_root=output,
                control_root=root / task_id / "negative",
                observed=observed,
                paper_root=paper_root,
            )

            records.append(
                {
                    "task_id": task_id,
                    "evaluator_id": evaluator_id,
                    "evaluation_domain": binding["evaluation_domain"],
                    "public_contract_hash": canonical_hash(
                        task.get("output_contract") or {}
                    ),
                    "reference_binding_hash": canonical_hash(binding),
                    "positive_artifact_hash": _tree_hash(output),
                    "positive_verdict_hash": canonical_hash(positive),
                    "positive_status": "passed",
                    "negative_controls": negative_controls,
                }
            )

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    resource_lock = _read_json(resource_lock_path)
    payload = {
        "schema_version": "pertura-evaluator-qualification-v1",
        "status": "passed",
        "passed": True,
        "git_commit": commit,
        "wheel_sha256": file_sha256(wheel),
        "task_catalog_sha256": file_sha256(task_catalog_path),
        "task_reference_catalog_sha256": file_sha256(task_reference_catalog_path),
        "resource_lock_sha256": file_sha256(resource_lock_path),
        "environment_locks": resource_lock.get("environment_locks") or {},
        "task_count": len(records),
        "records": records,
        "negative_control_coverage": [
            "missing_artifact",
            "missing_key",
            "duplicate_key",
            "wrong_row_universe",
            "nonfinite_value_where_numeric",
            "wrong_analysis_unit",
            "wrong_effect_or_fdr",
            "wrong_categorical_label",
            "wrong_boolean_label_or_rejection",
            "unknown_condition_role",
            "cells_as_replicates",
            "overclaim",
        ],
    }
    payload["canonical_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--task-reference-catalog", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--resource-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = qualify(
        repo=args.repo.resolve(),
        wheel=args.wheel.resolve(),
        task_catalog_path=args.task_catalog.resolve(),
        task_reference_catalog_path=args.task_reference_catalog.resolve(),
        paper_root=args.paper_root.resolve(),
        resource_lock_path=args.resource_lock.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "task_count": payload["task_count"],
                "canonical_hash": payload["canonical_hash"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
