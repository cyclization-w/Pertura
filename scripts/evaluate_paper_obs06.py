from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pertura-paper-obs06-verdict-v1"
EXPECTED_OBSERVED_SCHEMA = "pertura-paper-obs06-v1"
EXPECTED_REFERENCE_SCHEMA = "pertura-paper-ref06-v1"
CAPABILITY_ID = "association.sceptre.v1"
ALPHA = 0.10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_table(path: Path, *, delimiter: str) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError(f"table has no header: {path}")
        return [
            {str(key): str(value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def _finite_probability(value: str, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{label} is outside [0, 1]")
    return number


def _finite_number(value: str, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            ranks[index] = average
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("rank correlation requires matched nontrivial vectors")
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return 0.0
    return numerator / (left_scale * right_scale)


def _spearman(left: list[float], right: list[float]) -> float:
    return _pearson(_ranks(left), _ranks(right))


def _metric(
    observed: float | bool,
    *,
    operator: str,
    threshold: float | bool,
) -> dict[str, Any]:
    if operator == "lte":
        passed = float(observed) <= float(threshold)
    elif operator == "gte":
        passed = float(observed) >= float(threshold)
    elif operator == "eq":
        passed = observed == threshold
    else:
        raise ValueError(f"unsupported metric operator: {operator}")
    return {
        "observed": observed,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def _verify_observed_files(observed_root: Path, observed: dict[str, Any]) -> None:
    declared: dict[str, str] = {}
    for section in ("planted_execution", "norman_refusal"):
        declared.update(observed.get(section, {}).get("files") or {})
    for relative, expected in declared.items():
        path = (observed_root / relative).resolve()
        if observed_root.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"observed artifact is missing or escaped its root: {relative}")
        if _sha256(path) != expected:
            raise ValueError(f"observed artifact hash drift: {relative}")


def evaluate(
    *,
    observed_root: Path,
    ref06_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    observed_path = observed_root / "manifest.json"
    reference_path = ref06_root / "manifest.json"
    observed = _read_json(observed_path)
    reference = _read_json(reference_path)
    if observed.get("schema_version") != EXPECTED_OBSERVED_SCHEMA:
        raise ValueError("unsupported OBS-06 schema")
    if reference.get("schema_version") != EXPECTED_REFERENCE_SCHEMA:
        raise ValueError("unsupported REF-06 schema")
    if (
        observed.get("observed_pack_id") != "OBS-06"
        or observed.get("reference_pack_id") != "REF-06"
        or observed.get("capability_id") != CAPABILITY_ID
    ):
        raise ValueError("OBS-06 identity mismatch")
    if reference.get("reference_pack_id") != "REF-06":
        raise ValueError("REF-06 identity mismatch")
    if observed.get("input_files", {}).get("ref06_manifest") != _sha256(reference_path):
        raise ValueError("OBS-06 is not bound to the current REF-06 manifest")
    _verify_observed_files(observed_root, observed)

    result_path = observed_root / "planted" / "sceptre_results.csv"
    truth_path = ref06_root / "sceptre_synthetic_truth.tsv"
    reference_results_path = ref06_root / "sceptre_reference_results.tsv"
    suitability_path = ref06_root / "norman_sceptre_suitability.json"
    expected_reference_hashes = reference.get("output_files") or {}
    for path in (truth_path, reference_results_path, suitability_path):
        if _sha256(path) != expected_reference_hashes.get(path.name):
            raise ValueError(f"REF-06 output hash drift: {path.name}")

    truth_rows = _read_table(truth_path, delimiter="\t")
    result_rows = _read_table(result_path, delimiter=",")
    required_result_columns = {
        "response_id",
        "grna_target",
        "p_value",
        "fold_change",
        "FDR",
    }
    if result_rows and not required_result_columns.issubset(result_rows[0]):
        raise ValueError("SCEPTRE observed result lacks required columns")

    truth: dict[tuple[str, str], dict[str, str]] = {}
    for row in truth_rows:
        key = (row["response_id"], row["grna_target"])
        if key in truth:
            raise ValueError(f"REF-06 truth duplicates pair: {key}")
        truth[key] = row
    observed_pairs: dict[tuple[str, str], dict[str, str]] = {}
    for row in result_rows:
        key = (row["response_id"], row["grna_target"])
        if key in observed_pairs:
            raise ValueError(f"SCEPTRE result duplicates pair: {key}")
        observed_pairs[key] = row
    pair_identity_match = set(observed_pairs) == set(truth)

    null_pvalues: list[float] = []
    positive_discoveries: list[bool] = []
    discoveries: list[tuple[tuple[str, str], bool]] = []
    true_effects: list[float] = []
    observed_effects: list[float] = []
    if pair_identity_match:
        for key, truth_row in truth.items():
            row = observed_pairs[key]
            pvalue = _finite_probability(row["p_value"], label=f"p_value {key}")
            fdr = _finite_probability(row["FDR"], label=f"FDR {key}")
            effect = _finite_number(row["fold_change"], label=f"fold_change {key}")
            positive = truth_row["is_positive"].lower() == "true"
            discovery = fdr <= ALPHA
            discoveries.append((key, discovery))
            if positive:
                positive_discoveries.append(discovery)
                true_effects.append(float(truth_row["true_log_rate_ratio"]))
                observed_effects.append(effect)
            else:
                null_pvalues.append(pvalue)

    if pair_identity_match and null_pvalues and positive_discoveries:
        type_i_error = sum(value <= 0.05 for value in null_pvalues) / len(null_pvalues)
        power = sum(positive_discoveries) / len(positive_discoveries)
        discovered_keys = [key for key, selected in discoveries if selected]
        false_discoveries = sum(
            truth[key]["is_positive"].lower() != "true" for key in discovered_keys
        )
        fdr = false_discoveries / len(discovered_keys) if discovered_keys else 0.0
        effect_rank_concordance = _spearman(true_effects, observed_effects)
    else:
        type_i_error = 1.0
        power = 0.0
        fdr = 1.0
        effect_rank_concordance = 0.0

    suitability = _read_json(suitability_path)
    correct_refusal = bool(
        observed.get("norman_refusal", {}).get("correct_refusal") is True
        and suitability.get("correct_refusal") is True
        and observed.get("norman_refusal", {}).get("status") == "blocked"
    )
    no_silent_fallback = bool(
        observed.get("hard_gates", {}).get("no_silent_fallback") is True
    )
    metrics = {
        "type_i_error": _metric(type_i_error, operator="lte", threshold=0.10),
        "power": _metric(power, operator="gte", threshold=0.90),
        "fdr": _metric(fdr, operator="lte", threshold=0.10),
        "effect_rank_concordance": _metric(
            effect_rank_concordance, operator="gte", threshold=0.90
        ),
        "correct_refusal": _metric(correct_refusal, operator="eq", threshold=True),
    }
    hard_gates = {
        "observed_execution_hard_gates": observed.get("execution_hard_gates_passed")
        is True,
        "pair_identity_match": pair_identity_match,
        "no_silent_fallback": no_silent_fallback,
        "reference_is_independent": reference.get("independent_of_pertura_results")
        is True,
        "reference_complete": reference.get("readiness") == "generated"
        and not reference.get("pending_jobs"),
    }
    passed = all(hard_gates.values()) and all(
        item["passed"] for item in metrics.values()
    )
    verdict = {
        "schema_version": SCHEMA_VERSION,
        "verdict_id": "OBS-06::association.sceptre.v1",
        "observed_pack_id": "OBS-06",
        "reference_pack_id": "REF-06",
        "capability_id": CAPABILITY_ID,
        "capability_version": observed.get("capability_version"),
        "outcome": "passed" if passed else "failed",
        "passed": passed,
        "scientific_metrics_status": "passed" if all(
            item["passed"] for item in metrics.values()
        ) else "failed",
        "hard_gates": hard_gates,
        "metrics": metrics,
        "continuous_metrics": {
            name: item["observed"] for name, item in metrics.items()
        },
        "counts": {
            "truth_pairs": len(truth),
            "observed_pairs": len(observed_pairs),
            "positive_pairs": len(positive_discoveries),
            "null_pairs": len(null_pvalues),
        },
        "reference_hashes": {
            "ref06_manifest": _sha256(reference_path),
            "sceptre_truth": _sha256(truth_path),
            "independent_reference_results": _sha256(reference_results_path),
            "norman_suitability": _sha256(suitability_path),
        },
        "observed_hashes": {
            "obs06_manifest": _sha256(observed_path),
            "sceptre_results": _sha256(result_path),
            "planted_result_envelope": _sha256(
                observed_root / "planted" / "result_envelope.json"
            ),
            "norman_result_envelope": _sha256(
                observed_root / "norman_refusal" / "result_envelope.json"
            ),
        },
        "metric_provenance": {
            "observed_artifact_role": "sceptre_results",
            "observed_artifact_hash": _sha256(result_path),
            "reference_id": "REF-06",
            "evaluator_id": "paper-obs06-artifact-evaluator-v1",
            "self_reported_capability_metrics_used": False,
        },
        "limitations": [
            "Performance metrics use planted high-MOI truth, not a fifth real dataset.",
            "Independent Welch-test p-values are provenance-bound but are not required to equal SCEPTRE p-values.",
            "Norman contributes a correct-refusal result rather than a real-data SCEPTRE performance claim.",
        ],
    }
    _write_json(output_path, verdict)
    return {
        "schema_version": "pertura-paper-obs06-verdict-validation-v1",
        "passed": passed,
        "outcome": verdict["outcome"],
        "hard_gates": hard_gates,
        "metrics": metrics,
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independently score OBS-06 SCEPTRE artifacts against REF-06."
    )
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--ref06", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(
        observed_root=args.observed.resolve(),
        ref06_root=args.ref06.resolve(),
        output_path=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
