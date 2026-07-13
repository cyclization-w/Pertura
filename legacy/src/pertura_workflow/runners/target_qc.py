from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_METHOD = "basic_target_qc_v1"


def run_basic_target_qc(
    workspace: str | Path,
    *,
    metadata_csv: str | Path,
    target_uid: str,
    control_uid: str,
    target: str,
    control: str = "negative_control_pool",
    output_path: str | Path | None = None,
    cell_id_column: str = "cell_id",
    condition_column: str = "perturbation_uid",
    guide_column: str | None = None,
    guide_to_target_csv: str | Path | None = None,
    guide_column_in_map: str = "guide",
    target_column_in_map: str = "target",
    minimum_cells: int | None = None,
) -> dict[str, Any]:
    """Compute narrow target/control QC for an already UID-linked contrast.

    This runner only summarizes assignment counts and guide coverage. It does
    not infer perturbation identity, controls, cell type, or biological effect.
    """

    if not target_uid or not control_uid:
        raise ValueError("target_uid and control_uid are required")
    if not target:
        raise ValueError("target is required")

    root = Path(workspace).resolve()
    metadata_path = _resolve_workspace_file(root, metadata_csv)
    guide_target_map = _read_guide_map(
        _resolve_workspace_file(root, guide_to_target_csv) if guide_to_target_csv else None,
        guide_column=guide_column_in_map,
        target_column=target_column_in_map,
    )
    rows = _read_metadata(metadata_path)
    if cell_id_column not in rows.fieldnames or condition_column not in rows.fieldnames:
        raise ValueError(f"metadata_csv must include {cell_id_column!r} and {condition_column!r}")
    if guide_column and guide_column not in rows.fieldnames:
        raise ValueError(f"metadata_csv is missing guide column {guide_column!r}")

    n_target_cells = 0
    n_control_cells = 0
    cells_per_guide: Counter[str] = Counter()
    guides_for_target: set[str] = set()
    control_guides: set[str] = set()
    skipped_cells = 0

    for row in rows.records:
        condition = str(row.get(condition_column) or "").strip()
        guide = str(row.get(guide_column) or "").strip() if guide_column else ""
        if condition == target_uid:
            n_target_cells += 1
            if guide:
                cells_per_guide[guide] += 1
                mapped_target = guide_target_map.get(guide)
                if mapped_target in (None, "", target):
                    guides_for_target.add(guide)
        elif condition == control_uid:
            n_control_cells += 1
            if guide:
                cells_per_guide[guide] += 1
                control_guides.add(guide)
        else:
            skipped_cells += 1

    guides_per_target = len(guides_for_target) if guide_column else None
    observed_minimum = min(n_target_cells, n_control_cells)
    passes_min_cells = observed_minimum >= minimum_cells if minimum_cells is not None else None
    guide_consistency = _guide_consistency(guides_for_target, cells_per_guide)
    control_calibration = {
        "negative_control_available": n_control_cells > 0,
        "negative_control_type": control,
        "n_control_cells": n_control_cells,
        "n_control_guides": len(control_guides) if guide_column else None,
    }
    result = {
        "method": DEFAULT_METHOD,
        "target": target,
        "control": control,
        "target_uid": target_uid,
        "control_uid": control_uid,
        "n_target_cells": n_target_cells,
        "n_control_cells": n_control_cells,
        "guides_per_target": guides_per_target,
        "cells_per_guide": dict(sorted(cells_per_guide.items())),
        "guide_consistency": guide_consistency,
        "control_calibration": {key: value for key, value in control_calibration.items() if value is not None},
        "min_cells_policy": f"minimum_cells_{minimum_cells}" if minimum_cells is not None else None,
        "passes_min_cells": passes_min_cells,
        "skipped_cells": skipped_cells,
    }
    out_path = _resolve_output_path(root, output_path, target)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    result["path"] = str(out_path)
    result["relative_path"] = str(out_path.relative_to(root)) if _is_relative_to(out_path, root) else str(out_path)
    return result


class _MetadataRows:
    def __init__(self, fieldnames: list[str], records: list[dict[str, str]]) -> None:
        self.fieldnames = fieldnames
        self.records = records


def _read_metadata(path: Path) -> _MetadataRows:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return _MetadataRows(fieldnames, [dict(row) for row in reader])


def _read_guide_map(path: Path | None, *, guide_column: str, target_column: str) -> dict[str, str]:
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or guide_column not in reader.fieldnames or target_column not in reader.fieldnames:
            raise ValueError(f"guide_to_target_csv must include {guide_column!r} and {target_column!r}")
        return {str(row.get(guide_column) or "").strip(): str(row.get(target_column) or "").strip() for row in reader if str(row.get(guide_column) or "").strip()}


def _guide_consistency(guides: set[str], cells_per_guide: Counter[str]) -> str | None:
    if not guides:
        return None
    if len(guides) == 1:
        return "single_guide_observed"
    observed_counts = [cells_per_guide[guide] for guide in guides]
    if min(observed_counts) <= 0:
        return "guide_missing_cells"
    return "multiple_guides_observed"


def _resolve_workspace_file(root: Path, path: str | Path | None) -> Path:
    if path is None:
        raise ValueError("path is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"path is outside workspace: {path}")
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def _resolve_output_path(root: Path, output_path: str | Path | None, target: str) -> Path:
    if output_path is None:
        safe = "".join(ch if ch.isalnum() else "_" for ch in target).strip("_") or "target"
        return (root / "outputs" / f"basic_target_qc_{safe}.json").resolve()
    candidate = Path(output_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"output_path is outside workspace: {output_path}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False