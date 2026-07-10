from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus, ResultEnvelope
from pertura_core.hashing import file_sha256


_SUFFIX = re.compile(r"-\d+$")
_DNA = set("ACGTN")


def run_guide_assignment_qc(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
) -> ResultEnvelope:
    params = request.parameters
    guide_counts_path = _resolve_input(contract, params.get("guide_counts_path"))
    rna_barcodes_path = _resolve_input(contract, params.get("rna_barcodes_path"))
    guide_map_path = _resolve_input(contract, params.get("guide_map_path"))
    raw_counts_path = _resolve_input(contract, params.get("raw_guide_counts_path"), required=False)
    metadata_path = _resolve_input(contract, params.get("metadata_path"), required=False)
    threshold = float(params.get("posterior_threshold", 0.90))
    if not 0.5 <= threshold < 1:
        raise ValueError("posterior_threshold must be in [0.5, 1)")

    barcodes, guides, counts = _read_count_matrix(guide_counts_path, params.get("barcode_column"))
    rna_barcodes = _read_barcodes(rna_barcodes_path, params.get("rna_barcode_column"))
    guide_map, guide_map_issues = _read_guide_map(guide_map_path)
    blockers = list(guide_map_issues)
    cautions: list[str] = []

    normalized_rna, rna_collision = _normalize_barcodes(rna_barcodes)
    normalized_guide, guide_collision = _normalize_barcodes(barcodes)
    if rna_collision or guide_collision:
        blockers.append("barcode suffix removal would create collisions")
    direct_overlap = len(set(normalized_rna) & set(normalized_guide))
    reverse_barcodes = [_reverse_complement(item) for item in normalized_guide]
    reverse_overlap = len(set(normalized_rna) & set(reverse_barcodes))
    orientation = "reverse_complement" if reverse_overlap > direct_overlap else "forward"
    selected_barcodes = reverse_barcodes if orientation == "reverse_complement" else normalized_guide
    overlap = max(direct_overlap, reverse_overlap)
    if overlap == 0:
        blockers.append("RNA and guide barcode sets do not overlap in either orientation")
    elif orientation == "reverse_complement":
        cautions.append("guide barcodes matched RNA barcodes only after reverse complement")

    missing_guides = sorted(set(guides) - set(guide_map))
    if missing_guides:
        blockers.append(f"guide map is missing {len(missing_guides)} observed guides")

    posterior_by_guide: dict[str, list[float]] = {}
    mixture_parameters: dict[str, dict[str, float]] = {}
    for guide in guides:
        vector = [row[index] for row in counts for index, name in enumerate(guides) if name == guide]
        posterior, fitted = _fit_nb_mixture(vector)
        posterior_by_guide[guide] = posterior
        mixture_parameters[guide] = fitted

    assignments: list[dict[str, Any]] = []
    moi_counts: dict[int, int] = {}
    retained: list[str] = []
    design_moi = str(params.get("design_moi") or "low").strip().lower()
    for row_index, (raw_barcode, normalized_barcode) in enumerate(zip(barcodes, selected_barcodes)):
        assigned = [guide for guide in guides if posterior_by_guide[guide][row_index] >= threshold and counts[row_index][guides.index(guide)] > 0]
        moi = len(assigned)
        moi_counts[moi] = moi_counts.get(moi, 0) + 1
        if (design_moi in {"high", "combinatorial", "multi"} and moi >= 1) or (design_moi not in {"high", "combinatorial", "multi"} and moi == 1):
            retained.append(raw_barcode)
        assignments.append({
            "raw_barcode": raw_barcode,
            "normalized_barcode": normalized_barcode,
            "assigned_guides": assigned,
            "assigned_targets": sorted({guide_map.get(guide, "unmapped") for guide in assigned}),
            "assigned_guide_count": moi,
            "classification": "no_guide" if moi == 0 else ("singlet" if moi == 1 else "multi_guide"),
            "posteriors": {guide: posterior_by_guide[guide][row_index] for guide in guides},
        })

    ambient: dict[str, Any]
    if raw_counts_path:
        raw_barcodes, raw_guides, raw_counts = _read_count_matrix(raw_counts_path, params.get("barcode_column"))
        if raw_guides != guides:
            blockers.append("raw-droplet and filtered guide matrices have different guide columns")
            ambient = {"status": "failed", "reason": "guide columns differ"}
        else:
            cell_set = set(normalized_rna)
            empty_rows = [row for barcode, row in zip(raw_barcodes, raw_counts) if _normalize_one(barcode) not in cell_set]
            ambient = {
                "status": "estimated" if empty_rows else "unresolved",
                "n_empty_droplets": len(empty_rows),
                "mean_guide_umi": {
                    guide: (sum(row[index] for row in empty_rows) / len(empty_rows) if empty_rows else None)
                    for index, guide in enumerate(guides)
                },
            }
            if not empty_rows:
                cautions.append("raw guide matrix contained no identifiable non-cell droplets")
    else:
        ambient = {"status": "unresolved", "reason": "raw droplets were not provided"}
        cautions.append("ambient guide contamination is unresolved because raw droplets were not provided")

    balance = _sample_balance(metadata_path, retained, params) if metadata_path else {"status": "unresolved", "reason": "metadata_path not provided"}
    doublet = _doublet_summary(metadata_path, params) if metadata_path else {"status": "unresolved", "reason": "cell doublet scores not provided"}
    if doublet["status"] == "unresolved":
        cautions.append("cell-doublet diagnostic is unresolved; multi-guide status is not treated as a doublet")

    qc_payload = {
        "schema_version": "pertura-guide-assignment-qc-v1",
        "contract_id": contract.contract_id,
        "barcode": {
            "n_rna": len(rna_barcodes),
            "n_guide": len(barcodes),
            "direct_overlap": direct_overlap,
            "reverse_complement_overlap": reverse_overlap,
            "selected_orientation": orientation,
            "selected_overlap": overlap,
            "suffix_collision": rna_collision or guide_collision,
        },
        "guide_map": {"n_observed_guides": len(guides), "n_mapped_guides": len(set(guides) & set(guide_map)), "issues": guide_map_issues, "missing_guides": missing_guides},
        "assignment": {"posterior_threshold": threshold, "mixture": "two_component_negative_binomial", "parameters": mixture_parameters, "moi_counts": {str(key): value for key, value in sorted(moi_counts.items())}},
        "ambient": ambient,
        "doublet": doublet,
        "balance": balance,
        "retained_cell_count": len(retained),
        "blockers": blockers,
        "cautions": list(dict.fromkeys(cautions)),
    }
    qc_path = staging / "guide_assignment_qc.json"
    retained_path = staging / "retained_cells.csv"
    assignment_path = staging / "guide_assignments.json"
    qc_path.write_text(json.dumps(qc_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assignment_path.write_text(json.dumps(assignments, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with retained_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["raw_barcode", "retained"])
        retained_set = set(retained)
        writer.writerows((barcode, barcode in retained_set) for barcode in barcodes)

    status = DiagnosticStatus.blocked if blockers else (DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed)
    outputs = (qc_path.name, retained_path.name, assignment_path.name)
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=request.scope,
        status=status,
        result_kind=spec.output_kind,
        source_class=spec.source_class,
        summary=f"Guide assignment QC completed: {len(retained)} of {len(barcodes)} cells retained.",
        blockers=tuple(blockers),
        cautions=tuple(dict.fromkeys(cautions)),
        metrics={
            "barcode_overlap": overlap,
            "barcode_overlap_fraction": overlap / max(1, min(len(rna_barcodes), len(barcodes))),
            "retained_cell_count": len(retained),
            "moi_counts": {str(key): value for key, value in sorted(moi_counts.items())},
            "ambient_status": ambient["status"],
            "doublet_status": doublet["status"],
        },
        output_paths=outputs,
        output_hashes={name: file_sha256(staging / name) for name in outputs},
        dependencies=request.dependencies,
        metadata={"posterior_threshold": threshold, "design_moi": design_moi},
    )


def _resolve_input(contract: DatasetContract, value: Any, *, required: bool = True) -> Path | None:
    if value in (None, ""):
        if required:
            raise ValueError("guide assignment capability is missing a required input path")
        return None
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    if not candidate.is_absolute():
        directory_roots = [root for root in roots if root.is_dir()]
        if not directory_roots:
            raise ValueError("relative capability input requires a directory source in DatasetContract")
        candidate = directory_roots[0] / candidate
    resolved = candidate.resolve()
    allowed = any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots)
    if not allowed:
        raise ValueError("capability input is not bound to the authoritative DatasetContract")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _read_count_matrix(path: Path, barcode_column: str | None) -> tuple[list[str], list[str], list[list[int]]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("guide count matrix has no header")
        barcode = barcode_column or reader.fieldnames[0]
        if barcode not in reader.fieldnames:
            raise ValueError(f"barcode column not found: {barcode}")
        guides = [item for item in reader.fieldnames if item != barcode]
        barcodes: list[str] = []
        counts: list[list[int]] = []
        for row in reader:
            raw_barcode = str(row.get(barcode) or "").strip()
            if not raw_barcode:
                continue
            values = []
            for guide in guides:
                number = float(row.get(guide) or 0)
                if number < 0 or not number.is_integer():
                    raise ValueError("guide UMI matrix must contain nonnegative integer counts")
                values.append(int(number))
            barcodes.append(raw_barcode)
            counts.append(values)
    return barcodes, guides, counts


def _read_barcodes(path: Path, column: str | None) -> list[str]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))
    if not rows:
        return []
    if column:
        header = rows[0]
        if column not in header:
            raise ValueError(f"RNA barcode column not found: {column}")
        index = header.index(column)
        values = rows[1:]
    else:
        index = 0
        values = rows
        if rows[0] and rows[0][0].strip().lower() in {"barcode", "cell_id", "cell"}:
            values = rows[1:]
    return [row[index].strip() for row in values if len(row) > index and row[index].strip()]


def _read_guide_map(path: Path) -> tuple[dict[str, str], list[str]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or not {"guide", "target"}.issubset(reader.fieldnames):
            raise ValueError("guide map must contain guide and target columns")
        mapping: dict[str, str] = {}
        issues: list[str] = []
        for row in reader:
            guide = str(row.get("guide") or "").strip()
            target = str(row.get("target") or "").strip()
            if not guide or not target:
                issues.append("guide map contains an empty guide or target")
                continue
            if guide in mapping and mapping[guide] != target:
                issues.append(f"guide maps to multiple targets: {guide}")
            mapping[guide] = target
    return mapping, list(dict.fromkeys(issues))


def _normalize_barcodes(values: list[str]) -> tuple[list[str], bool]:
    normalized = [_normalize_one(item) for item in values]
    collision = len(set(normalized)) != len(set(values))
    return (values if collision else normalized), collision


def _normalize_one(value: str) -> str:
    return _SUFFIX.sub("", value.strip().upper())


def _reverse_complement(value: str) -> str:
    if not value or any(base not in _DNA for base in value):
        return value
    return value.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]


def _fit_nb_mixture(
    values: list[int],
    *,
    max_iterations: int = 200,
    tolerance: float = 1e-6,
) -> tuple[list[float], dict[str, float]]:
    if not values or max(values) == 0:
        return [0.0 for _ in values], {"background_mean": 0.0, "signal_mean": 0.0, "background_size": 1.0, "signal_size": 1.0, "signal_weight": 0.0}
    ordered = sorted(values)
    low = max(0.05, sum(ordered[: max(1, len(values) // 2)]) / max(1, len(values) // 2))
    high_values = ordered[max(0, int(len(values) * 0.8)) :]
    high = max(low + 0.5, sum(high_values) / max(1, len(high_values)))
    size_low = 2.0
    size_high = 2.0
    weight = min(0.5, max(0.01, sum(value > 0 for value in values) / len(values)))
    posterior = [0.0] * len(values)
    for _ in range(max_iterations):
        for index, value in enumerate(values):
            log_bg = math.log(max(1e-12, 1 - weight)) + _nb_logpmf(value, low, size_low)
            log_signal = math.log(max(1e-12, weight)) + _nb_logpmf(value, high, size_high)
            delta = max(-700.0, min(700.0, log_bg - log_signal))
            posterior[index] = 1.0 / (1.0 + math.exp(delta))
        signal_weight = sum(posterior)
        bg_weight = len(values) - signal_weight
        new_weight = min(0.999, max(0.001, signal_weight / len(values)))
        new_high = sum(prob * value for prob, value in zip(posterior, values)) / max(1e-9, signal_weight)
        new_low = sum((1 - prob) * value for prob, value in zip(posterior, values)) / max(1e-9, bg_weight)
        new_size_high = _weighted_nb_size(values, posterior, new_high)
        new_size_low = _weighted_nb_size(values, [1 - item for item in posterior], new_low)
        if max(abs(new_low - low), abs(new_high - high), abs(new_weight - weight)) < tolerance:
            low, high, weight, size_low, size_high = new_low, new_high, new_weight, new_size_low, new_size_high
            break
        low, high, weight, size_low, size_high = new_low, max(new_high, new_low + 1e-6), new_weight, new_size_low, new_size_high
    if high < low:
        posterior = [1 - item for item in posterior]
        low, high, size_low, size_high = high, low, size_high, size_low
        weight = 1 - weight
    return posterior, {"background_mean": low, "signal_mean": high, "background_size": size_low, "signal_size": size_high, "signal_weight": weight}


def _nb_logpmf(value: int, mean: float, size: float) -> float:
    mean = max(mean, 1e-12)
    size = max(size, 1e-6)
    return (
        math.lgamma(value + size)
        - math.lgamma(size)
        - math.lgamma(value + 1)
        + size * math.log(size / (size + mean))
        + value * math.log(mean / (size + mean))
    )


def _weighted_nb_size(values: list[int], weights: list[float], mean: float) -> float:
    total = sum(weights)
    if total <= 1e-9:
        return 1.0
    variance = sum(weight * (value - mean) ** 2 for weight, value in zip(weights, values)) / total
    if variance <= mean + 1e-9:
        return 1e6
    return max(1e-3, min(1e6, mean * mean / (variance - mean)))


def _sample_balance(path: Path, retained: list[str], params: dict[str, Any]) -> dict[str, Any]:
    barcode_column = str(params.get("metadata_barcode_column") or "barcode")
    columns = [str(item) for item in params.get("balance_columns") or ["replicate", "donor", "batch"]]
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    retained_set = set(retained)
    counts: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or barcode_column not in reader.fieldnames:
            return {"status": "unresolved", "reason": f"metadata lacks {barcode_column}"}
        available = [column for column in columns if column in reader.fieldnames]
        for row in reader:
            if str(row.get(barcode_column) or "") not in retained_set:
                continue
            for column in available:
                value = str(row.get(column) or "unresolved")
                counts.setdefault(column, {})[value] = counts.setdefault(column, {}).get(value, 0) + 1
    return {"status": "estimated" if counts else "unresolved", "counts": counts}


def _doublet_summary(path: Path, params: dict[str, Any]) -> dict[str, Any]:
    score_column = str(params.get("doublet_score_column") or "scrublet_score")
    threshold = float(params.get("doublet_threshold", 0.25))
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or score_column not in reader.fieldnames:
            return {"status": "unresolved", "reason": f"metadata lacks {score_column}; Scrublet was not run by this matrix-only capability"}
        scores = [float(row[score_column]) for row in reader if row.get(score_column) not in (None, "")]
    return {"status": "estimated", "score_column": score_column, "threshold": threshold, "n_scored": len(scores), "n_predicted_doublet": sum(value >= threshold for value in scores)}
