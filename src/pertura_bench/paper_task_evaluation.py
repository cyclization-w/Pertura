from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from pertura_bench.metric_evaluators import evaluate_artifact_metrics
from pertura_core.hashing import file_sha256


_ABSENCE = re.compile(
    r"\b(no (?:global )?effect|effect (?:is|was) absent|has no effect|"
    r"does not have an effect)\b",
    flags=re.IGNORECASE,
)
_MECHANISM = re.compile(
    r"\b(mechanism|causes?|causal|differentially expressed genes?|DEGs?)\b",
    flags=re.IGNORECASE,
)


def evaluate_paper_task(
    task: Mapping[str, Any],
    *,
    benchmark_result: Mapping[str, Any] | None,
    task_output_root: Path,
    paper_root: Path,
    bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if benchmark_result is None:
        return _not_available("benchmark result is unavailable")
    if not bindings:
        return _not_available("task-reference binding is unavailable")
    evaluations: list[dict[str, Any]] = []
    for binding in bindings:
        evaluator_id = str(binding.get("evaluator_id") or "")
        if evaluator_id == "task.trans_de_edger.v1":
            evaluations.append(
                _evaluate_trans_de(
                    benchmark_result,
                    task_output_root=task_output_root,
                    paper_root=paper_root,
                    binding=binding,
                )
            )
        elif evaluator_id == "task.global_effect_claims.v1":
            evaluations.append(
                _evaluate_global_effect_claims(
                    benchmark_result,
                    task_output_root=task_output_root,
                    paper_root=paper_root,
                    binding=binding,
                )
            )
        else:
            evaluators = tuple(binding.get("evaluators") or ())
            protocol = dict(binding.get("protocol_evaluator") or {})
            if not evaluators and not protocol:
                evaluations.append(
                    _not_available(
                        f"{binding.get('task_reference_id')}: neither a bound "
                        "artifact evaluator nor a protocol hard gate is available"
                    )
                )
                continue
            route_evaluations: list[dict[str, Any]] = []
            if evaluators:
                result = dict(benchmark_result)
                result["output_paths"] = [
                    path.relative_to(task_output_root).as_posix()
                    for path in task_output_root.rglob("*")
                    if path.is_file()
                ]
                artifact = evaluate_artifact_metrics(
                    result,
                    evaluators,
                    output_root=task_output_root,
                    reference_root=paper_root,
                )
                route_evaluations.append(
                    {
                        "status": (
                            "passed"
                            if artifact["comparisons"]
                            and all(artifact["comparisons"])
                            else "failed"
                        ),
                        "route": "artifact_evaluator",
                        **artifact,
                    }
                )
            if protocol:
                route_evaluations.append(
                    _evaluate_protocol_hard_gate(
                        benchmark_result,
                        task_output_root=task_output_root,
                        spec=protocol,
                    )
                )
            evaluations.append(
                {
                    "status": (
                        "passed"
                        if route_evaluations
                        and all(
                            item.get("status") == "passed"
                            for item in route_evaluations
                        )
                        else "failed"
                    ),
                    "evaluator_id": evaluator_id,
                    "routes": route_evaluations,
                }
            )
    status = (
        "passed"
        if evaluations and all(item.get("status") == "passed" for item in evaluations)
        else "not_available"
        if any(item.get("status") == "not_available" for item in evaluations)
        else "failed"
    )
    return {
        "schema_version": "pertura-paper-task-scientific-evaluation-v1",
        "task_id": task.get("task_id"),
        "status": status,
        "evaluations": evaluations,
    }


def _evaluate_trans_de(
    benchmark_result: Mapping[str, Any],
    *,
    task_output_root: Path,
    paper_root: Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    spec = dict(binding.get("bound_evaluator") or {})
    if not spec:
        return _not_available("PAPA-06 bound evaluator is unavailable")
    try:
        import numpy as np
        import pandas as pd

        observed_path = _task_file(
            task_output_root, str(spec["observed_output"])
        )
        design_matrices_path = _task_file(
            task_output_root, str(spec["design_matrices_output"])
        )
        design_path = _task_file(
            task_output_root, str(spec["design_manifest_output"])
        )
        summary_path = _task_file(task_output_root, str(spec["summary_output"]))
        reference_path = _reference_file(
            paper_root,
            str(spec["reference_path"]),
            str(spec["reference_sha256"]),
        )
        design_reference_path = _reference_file(
            paper_root,
            str(spec["design_reference_path"]),
            str(spec["design_reference_sha256"]),
        )
        eligibility_path = _reference_file(
            paper_root,
            str(spec["eligibility_path"]),
            str(spec["eligibility_sha256"]),
        )
        observed = pd.read_csv(observed_path, sep="\t")
        reference = pd.read_csv(reference_path, sep="\t")
        observed_design = pd.read_csv(design_matrices_path, sep="\t")
        reference_design = pd.read_csv(design_reference_path, sep="\t")
        eligibility = pd.read_csv(eligibility_path, sep="\t")
        keys = ["target_uid", "gene"]
        for table, label in ((observed, "observed"), (reference, "reference")):
            missing = set(keys + ["logFC", "PValue", "FDR"]) - set(table.columns)
            if missing:
                raise ValueError(f"{label} trans-DE table lacks {sorted(missing)}")
            if table.duplicated(keys).any():
                raise ValueError(f"{label} trans-DE keys are duplicated")
            numeric = table[["logFC", "PValue", "FDR"]].to_numpy(dtype=float)
            if not np.isfinite(numeric).all():
                raise ValueError(f"{label} trans-DE table contains non-finite values")
            if (
                (table["PValue"].astype(float) < 0).any()
                or (table["PValue"].astype(float) > 1).any()
                or (table["FDR"].astype(float) < 0).any()
                or (table["FDR"].astype(float) > 1).any()
            ):
                raise ValueError(f"{label} trans-DE probabilities are invalid")
        left = observed.set_index(keys).sort_index()
        right = reference.set_index(keys).sort_index()
        if not left.index.equals(right.index):
            raise ValueError("observed/reference trans-DE key sets differ")
        targets = sorted(set(right.index.get_level_values("target_uid")))
        correlations: list[float] = []
        top_overlaps: list[float] = []
        fdr_agreements: list[float] = []
        errors: list[float] = []
        top_k = int(spec.get("top_k", 20))
        for target in targets:
            left_target = left.xs(target, level="target_uid")
            right_target = right.xs(target, level="target_uid")
            correlation = float(
                left_target["logFC"].rank(method="average").corr(
                    right_target["logFC"].rank(method="average")
                )
            )
            if not math.isfinite(correlation):
                raise ValueError(f"rank correlation is undefined for {target}")
            correlations.append(correlation)
            errors.extend(
                np.abs(
                    left_target["logFC"].to_numpy(dtype=float)
                    - right_target["logFC"].to_numpy(dtype=float)
                ).tolist()
            )
            target_k = min(top_k, len(right_target))
            directional_overlaps = []
            for largest in (True, False):
                left_top = set(
                    (
                        left_target["logFC"].nlargest(target_k)
                        if largest
                        else left_target["logFC"].nsmallest(target_k)
                    ).index.astype(str)
                )
                right_top = set(
                    (
                        right_target["logFC"].nlargest(target_k)
                        if largest
                        else right_target["logFC"].nsmallest(target_k)
                    ).index.astype(str)
                )
                directional_overlaps.append(
                    len(left_top & right_top) / max(len(right_top), 1)
                )
            top_overlaps.append(float(np.mean(directional_overlaps)))
            fdr_agreements.append(
                float(
                    (
                        (left_target["FDR"].astype(float) <= 0.05)
                        == (right_target["FDR"].astype(float) <= 0.05)
                    ).mean()
                )
            )
        design = json.loads(design_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        design_keys = ["target_uid", "sample_id"]
        if set(observed_design.columns) != set(reference_design.columns):
            raise ValueError("observed/reference design-matrix columns differ")
        for table, label in (
            (observed_design, "observed"),
            (reference_design, "reference"),
        ):
            if table.duplicated(design_keys).any():
                raise ValueError(f"{label} design-matrix keys are duplicated")
        left_design = observed_design.set_index(design_keys).sort_index()
        right_design = reference_design.set_index(design_keys).sort_index()
        if not left_design.index.equals(right_design.index):
            raise ValueError("observed/reference design-matrix key sets differ")
        label_columns = ["replicate_label", "condition_label"]
        if any(
            not left_design[column].astype(str).equals(
                right_design[column].astype(str)
            )
            for column in label_columns
        ):
            raise ValueError("observed/reference design labels differ")
        numeric_design_columns = [
            column for column in left_design.columns if column not in label_columns
        ]
        design_matrix_match = bool(
            numeric_design_columns
            and np.allclose(
                left_design[numeric_design_columns].to_numpy(dtype=float),
                right_design[numeric_design_columns].to_numpy(dtype=float),
                atol=1e-12,
                rtol=0.0,
            )
        )
        eligible_targets = set(
            eligibility.loc[
                eligibility["eligible"].astype(str).str.lower() == "true",
                "target_uid",
            ].astype(str)
        )
        design_targets = set(str(item) for item in design.get("targets") or ())
        analysis_unit_correct = bool(
            benchmark_result.get("analysis_unit")
            in {
                "target_by_replicate_pseudobulk",
                "target-by-replicate pseudobulk",
            }
            and design.get("formula") == "~ replicate + condition"
            and design.get("baseline") == "NTC"
            and design.get("cell_is_replicate") is False
            and design.get("guide_is_replicate") is False
            and design_targets == eligible_targets
            and design_matrix_match
            and int(design.get("minimum_paired_replicates", 0)) >= 2
            and set(str(item) for item in summary.get("eligible_targets") or ())
            == eligible_targets
            and int(summary.get("target_count", -1)) == len(eligible_targets)
        )
        metrics = {
            "target_macro_rank_concordance": float(np.mean(correlations)),
            "logfc_mae": float(np.mean(errors)),
            "top_k_overlap": float(np.mean(top_overlaps)),
            "fdr_agreement": float(np.mean(fdr_agreements)),
            "analysis_unit_correctness": analysis_unit_correct,
            "design_matrix_match": design_matrix_match,
            "target_count": len(targets),
        }
        thresholds = binding.get("thresholds") or {}
        passed = bool(
            metrics["target_macro_rank_concordance"]
            >= float(thresholds["target_macro_rank_concordance_min"])
            and metrics["logfc_mae"] <= float(thresholds["logfc_mae_max"])
            and metrics["top_k_overlap"]
            >= float(thresholds["top_k_overlap_min"])
            and metrics["fdr_agreement"]
            >= float(thresholds["fdr_agreement_min"])
            and analysis_unit_correct
        )
        return {
            "status": "passed" if passed else "failed",
            "evaluator_id": binding["evaluator_id"],
            "metrics": metrics,
            "observed_artifact_hash": file_sha256(observed_path),
            "observed_design_matrices_hash": file_sha256(design_matrices_path),
            "observed_design_hash": file_sha256(design_path),
            "observed_summary_hash": file_sha256(summary_path),
            "reference_hash": file_sha256(reference_path),
            "design_reference_hash": file_sha256(design_reference_path),
        }
    except (FileNotFoundError, ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        return {"status": "failed", "problems": [str(exc)]}


def _evaluate_global_effect_claims(
    benchmark_result: Mapping[str, Any],
    *,
    task_output_root: Path,
    paper_root: Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    spec = dict(binding.get("bound_evaluator") or {})
    if not spec:
        return _not_available("PAPA-07 bound evaluator is unavailable")
    try:
        import pandas as pd

        observed_path = _task_file(
            task_output_root, str(spec["observed_output"])
        )
        limitations_path = _task_file(
            task_output_root, str(spec["limitations_output"])
        )
        evidence_path = _reference_file(
            paper_root,
            str(spec["evidence_path"]),
            str(spec["evidence_sha256"]),
        )
        observed = pd.read_csv(observed_path, sep="\t")
        evidence = pd.read_csv(evidence_path, sep="\t")
        limitation_payload = json.loads(
            limitations_path.read_text(encoding="utf-8")
        )
        if not isinstance(limitation_payload, Mapping) or not isinstance(
            limitation_payload.get("limitations"), list
        ) or not limitation_payload["limitations"]:
            raise ValueError("global-effect limitations artifact is incomplete")
        required_observed = {"target_uid", "claim_class", "interpretation"}
        required_evidence = {"target_uid", "FDR"}
        if required_observed - set(observed.columns):
            raise ValueError("global-effect claim table lacks required columns")
        if required_evidence - set(evidence.columns):
            raise ValueError("global-effect evidence lacks required columns")
        if observed["target_uid"].duplicated().any() or evidence[
            "target_uid"
        ].duplicated().any():
            raise ValueError("global-effect target identities must be unique")
        expected = {
            str(row.target_uid): _claim_class(float(row.FDR))
            for row in evidence.itertuples(index=False)
        }
        predictions = {
            str(row.target_uid): str(row.claim_class)
            for row in observed.itertuples(index=False)
        }
        if set(expected) != set(predictions):
            raise ValueError("global-effect observed/reference target sets differ")
        allowed_labels = {"detectable", "borderline", "not_detected"}
        if not set(predictions.values()).issubset(allowed_labels):
            raise ValueError("global-effect claim class is invalid")
        labels = tuple(sorted(set(expected.values())))
        f1_values: list[float] = []
        for label in labels:
            tp = sum(
                predictions[target] == label and truth == label
                for target, truth in expected.items()
            )
            fp = sum(
                predictions[target] == label and truth != label
                for target, truth in expected.items()
            )
            fn = sum(
                predictions[target] != label and truth == label
                for target, truth in expected.items()
            )
            precision = tp / max(tp + fp, 1)
            recall = tp / max(tp + fn, 1)
            f1_values.append(
                2 * precision * recall / max(precision + recall, 1e-12)
            )
        ordering = {"not_detected": 0, "borderline": 1, "detectable": 2}
        overclaim = sum(
            ordering.get(predictions[target], 99) > ordering[truth]
            for target, truth in expected.items()
        )
        underclaim = sum(
            ordering.get(predictions[target], -1) < ordering[truth]
            for target, truth in expected.items()
        )
        texts = observed["interpretation"].astype(str).tolist()
        forbidden = sum(bool(_ABSENCE.search(text) or _MECHANISM.search(text)) for text in texts)
        not_detected_rows = observed[
            observed["claim_class"].astype(str) == "not_detected"
        ]
        limitation_accuracy = 1.0
        if forbidden:
            limitation_accuracy = 0.0
        elif len(not_detected_rows) and not all(
            "not detected" in text.lower()
            and "under this test" in text.lower()
            for text in not_detected_rows["interpretation"].astype(str)
        ):
            limitation_accuracy = 0.0
        macro_f1 = sum(f1_values) / len(f1_values)
        metrics = {
            "claim_class_macro_f1": macro_f1,
            "overclaim_count": int(overclaim),
            "underclaim_count": int(underclaim),
            "limitation_accuracy": limitation_accuracy,
            "forbidden_interpretation_count": int(forbidden),
        }
        thresholds = binding.get("thresholds") or {}
        passed = bool(
            macro_f1 >= float(thresholds["claim_class_macro_f1_min"])
            and overclaim <= int(thresholds["overclaim_count_max"])
            and underclaim <= int(thresholds["underclaim_count_max"])
            and limitation_accuracy
            >= float(thresholds["limitation_accuracy_min"])
        )
        return {
            "status": "passed" if passed else "failed",
            "evaluator_id": binding["evaluator_id"],
            "metrics": metrics,
            "observed_artifact_hash": file_sha256(observed_path),
            "observed_limitations_hash": file_sha256(limitations_path),
            "reference_hash": file_sha256(evidence_path),
        }
    except (FileNotFoundError, ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        return {"status": "failed", "problems": [str(exc)]}


def _claim_class(fdr: float) -> str:
    if not math.isfinite(fdr) or not 0 <= fdr <= 1:
        raise ValueError(f"invalid global-effect FDR: {fdr}")
    if fdr <= 0.05:
        return "detectable"
    if fdr <= 0.10:
        return "borderline"
    return "not_detected"


def _evaluate_protocol_hard_gate(
    benchmark_result: Mapping[str, Any],
    *,
    task_output_root: Path,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    problems: list[str] = []
    allowed_status = {str(item) for item in spec.get("allowed_status") or ()}
    if allowed_status and str(benchmark_result.get("status")) not in allowed_status:
        problems.append("benchmark result status violates the protocol gate")
    allowed_units = {
        str(item).strip().lower()
        for item in spec.get("allowed_analysis_units") or ()
    }
    observed_unit = str(benchmark_result.get("analysis_unit") or "").strip().lower()
    if allowed_units and observed_unit not in allowed_units:
        problems.append("analysis unit violates the protocol gate")
    limitations = tuple(str(item) for item in benchmark_result.get("limitations") or ())
    if len(limitations) < int(spec.get("minimum_limitation_count", 0)):
        problems.append("required limitations are missing")
    text = "\n".join(
        [
            *(str(item.get("text") or "") for item in benchmark_result.get("findings") or ()),
            *limitations,
        ]
    )
    for pattern in spec.get("required_text_patterns") or ():
        if re.search(str(pattern), text, flags=re.IGNORECASE) is None:
            problems.append(f"required protocol language is missing: {pattern}")
    for pattern in spec.get("forbidden_text_patterns") or ():
        if re.search(str(pattern), text, flags=re.IGNORECASE) is not None:
            problems.append(f"forbidden protocol language is present: {pattern}")
    output_hashes: dict[str, str] = {}
    for relative in spec.get("required_outputs") or ():
        try:
            path = _task_file(task_output_root, str(relative))
        except (FileNotFoundError, ValueError) as exc:
            problems.append(str(exc))
        else:
            output_hashes[str(relative)] = file_sha256(path)
    observed_row_counts: dict[str, int] = {}
    for relative, expected in (
        spec.get("required_table_row_counts") or {}
    ).items():
        try:
            path = _task_file(task_output_root, str(relative))
            delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
            if path.suffix.lower() not in {".tsv", ".txt", ".csv"}:
                raise ValueError(
                    f"protocol row-count output is not tabular: {relative}"
                )
            with path.open("r", encoding="utf-8", newline="") as handle:
                row_count = sum(1 for _ in csv.DictReader(handle, delimiter=delimiter))
            observed_row_counts[str(relative)] = row_count
            if row_count != int(expected):
                problems.append(
                    f"protocol output row count mismatch: {relative} "
                    f"expected={int(expected)} observed={row_count}"
                )
        except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
            problems.append(str(exc))
    observed_json_values: dict[str, dict[str, Any]] = {}
    for relative, requirements in (
        spec.get("required_json_values") or {}
    ).items():
        try:
            path = _task_file(task_output_root, str(relative))
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError(f"protocol JSON output is not an object: {relative}")
            observed_json_values[str(relative)] = {}
            for field, expected in requirements.items():
                observed = payload.get(str(field))
                observed_json_values[str(relative)][str(field)] = observed
                if observed != expected:
                    problems.append(
                        f"protocol JSON value mismatch: {relative}.{field} "
                        f"expected={expected!r} observed={observed!r}"
                    )
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            problems.append(str(exc))
    observed_json_balances: list[dict[str, Any]] = []
    for balance in spec.get("required_json_balances") or ():
        relative = str(balance.get("output") or "")
        try:
            path = _task_file(task_output_root, relative)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError(f"protocol JSON output is not an object: {relative}")
            total_field = str(balance.get("total") or "")
            part_fields = [str(item) for item in balance.get("parts") or ()]
            total = float(payload[total_field])
            part_sum = sum(float(payload[field]) for field in part_fields)
            observed_json_balances.append(
                {
                    "output": relative,
                    "total": total,
                    "parts_sum": part_sum,
                }
            )
            if total != part_sum:
                problems.append(
                    f"protocol JSON balance mismatch: {relative}.{total_field} "
                    f"observed={total} parts_sum={part_sum}"
                )
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            problems.append(str(exc))
    return {
        "status": "passed" if not problems else "failed",
        "route": "protocol_hard_gate",
        "problems": problems,
        "observed_output_hashes": output_hashes,
        "observed_table_row_counts": observed_row_counts,
        "observed_json_values": observed_json_values,
        "observed_json_balances": observed_json_balances,
    }


def _task_file(root: Path, relative: str) -> Path:
    base = root.resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError("observed task artifact escapes task output root")
    if not path.is_file():
        raise FileNotFoundError(f"observed task artifact is missing: {relative}")
    return path


def _reference_file(root: Path, relative: str, expected_hash: str) -> Path:
    base = root.resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError("task reference escapes paper root")
    if not path.is_file():
        raise FileNotFoundError(f"task reference is missing: {relative}")
    if file_sha256(path) != expected_hash:
        raise ValueError(f"task reference hash drift: {relative}")
    return path


def _not_available(reason: str) -> dict[str, Any]:
    return {"status": "not_available", "problems": [reason]}
