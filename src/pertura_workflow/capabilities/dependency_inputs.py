from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest
from pertura_core.hashing import file_sha256


def retained_cells_for_request(
    staging: Path,
    request: CapabilityRunRequest,
    *,
    required: bool = False,
) -> set[str] | None:
    """Load and verify the retained-cell manifest bound to this request.

    Direct algorithm tests may omit scientific dependencies and receive None.
    Broker-mediated execution includes an explicit retained-cell
    dependency; absence, ambiguity, or hash drift is fatal before statistics.
    """

    bindings = [
        item for item in request.dependencies if item.kind == "retained_cell_manifest"
    ]
    asset_bindings = [
        item
        for item in request.dependencies
        if item.kind == "data_asset" and item.role == "asset:retained_cell_manifest"
    ]
    if not bindings and asset_bindings:
        return _retained_cells_from_data_asset(staging, asset_bindings)
    if not bindings:
        if required:
            from pertura_workflow.capabilities.execution_context import execution_context
            if execution_context().get("enforce_dependency_consumption"):
                raise ValueError("required retained-cell dependency is missing")
        return None
    if len({item.object_id for item in bindings}) != 1:
        raise ValueError("retained-cell dependency is ambiguous")
    dependency_result_id = bindings[0].object_id
    projection_path = staging / "_dependency_results.json"
    if not projection_path.is_file():
        raise ValueError("retained-cell dependency projection is missing")
    payload = json.loads(projection_path.read_text(encoding="utf-8"))
    matches = [
        item
        for item in payload.get("results") or ()
        if item.get("result_id") == dependency_result_id
        and any(
            ref.get("kind") == "retained_cell_manifest"
            for ref in item.get("dependency_refs") or ()
        )
    ]
    if len(matches) != 1:
        raise ValueError("retained-cell dependency projection is missing or ambiguous")
    result = matches[0]
    candidates = [
        Path(item)
        for item in result.get("local_output_paths") or ()
        if Path(item).name == "retained_cells.csv"
    ]
    if len(candidates) != 1:
        raise ValueError("retained-cell result must provide exactly one retained_cells.csv")
    manifest = candidates[0]
    expected_hash = (result.get("output_hashes") or {}).get(manifest.name)
    if not expected_hash or file_sha256(manifest) != expected_hash:
        raise ValueError("retained-cell manifest hash does not match its committed result")

    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        cell_field = "raw_barcode" if "raw_barcode" in fields else (
            "cell_id" if "cell_id" in fields else ""
        )
        if not cell_field or "retained" not in fields:
            raise ValueError(
                "retained-cell manifest requires raw_barcode/cell_id and retained columns"
            )
        retained: set[str] = set()
        seen: set[str] = set()
        for row in reader:
            cell = str(row.get(cell_field) or "").strip()
            if not cell:
                raise ValueError("retained-cell manifest contains an empty cell identity")
            if cell in seen:
                raise ValueError(f"retained-cell manifest contains duplicate cell: {cell}")
            seen.add(cell)
            value = str(row.get("retained") or "").strip().lower()
            if value in {"1", "true", "yes", "y"}:
                retained.add(cell)
            elif value not in {"0", "false", "no", "n"}:
                raise ValueError(
                    f"retained-cell manifest has invalid retained flag for {cell}"
                )
    if not retained:
        raise ValueError("retained-cell manifest retains no cells")
    from pertura_workflow.capabilities.execution_context import (
        record_dependency_consumption,
    )

    record_dependency_consumption(
        dependency_result_id=dependency_result_id,
        dependency_result_hash=bindings[0].object_hash,
        dependency_artifact_hash=str(expected_hash),
        usage="row_filter",
        rows_available=len(seen),
        rows_consumed=len(retained),
        columns_consumed=1,
    )
    return retained


def _retained_cells_from_data_asset(
    staging: Path,
    bindings: list[Any],
) -> set[str]:
    from pertura_workflow.capabilities.execution_context import (
        record_dependency_consumption,
    )

    if len({item.object_id for item in bindings}) != 1:
        raise ValueError("retained-cell data asset is ambiguous")
    dependency = bindings[0]
    projection = staging / "_runtime_dependencies.json"
    if not projection.is_file():
        raise ValueError("retained-cell data asset projection is missing")
    payload = json.loads(projection.read_text(encoding="utf-8"))
    matches = [
        item
        for item in payload.get("dependencies") or ()
        if item.get("kind") == "data_asset"
        and item.get("object_id") == dependency.object_id
        and (item.get("payload") or {}).get("role") == "retained_cell_manifest"
    ]
    if len(matches) != 1:
        raise ValueError("retained-cell data asset projection is missing or ambiguous")
    record = matches[0]
    asset = dict(record.get("payload") or {})
    if asset.get("schema_validation_status") != "validated":
        raise ValueError("retained-cell data asset was not schema validated")
    if record.get("object_hash") != dependency.object_hash:
        raise ValueError("retained-cell data asset identity hash is stale")
    manifest = Path(str(asset.get("resolved_path") or ""))
    if not manifest.is_file():
        raise ValueError("retained-cell data asset path is missing")
    if asset.get("content_sha256") != file_sha256(manifest):
        raise ValueError("retained-cell data asset content hash is stale")

    delimiter = "\t" if manifest.suffix.lower() in {".tsv", ".txt"} else ","
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fields = set(reader.fieldnames or ())
        cell_field = (
            "raw_barcode"
            if "raw_barcode" in fields
            else "cell_id" if "cell_id" in fields else ""
        )
        if not cell_field:
            raise ValueError("retained-cell data asset requires raw_barcode or cell_id")
        retained: set[str] = set()
        seen: set[str] = set()
        for row in reader:
            cell = str(row.get(cell_field) or "").strip()
            if not cell or cell in seen:
                raise ValueError("retained-cell data asset has empty or duplicate cells")
            seen.add(cell)
            if "retained" in fields:
                value = str(row.get("retained") or "").strip().lower()
                keep = value in {"1", "true", "yes", "y"}
                if not keep and value not in {"0", "false", "no", "n"}:
                    raise ValueError(f"invalid retained flag for {cell}")
            elif "expected_state" in fields:
                state = str(row.get("expected_state") or "").strip().lower()
                keep = state.startswith("retain")
            elif "status" in fields:
                state = str(row.get("status") or "").strip().lower()
                keep = state in {"retained", "retain", "included", "keep", "kept"}
            else:
                # A validated retained-cell artifact whose row universe is
                # explicitly the retained set may omit a redundant flag.
                keep = True
            if keep:
                retained.add(cell)
    if not retained:
        raise ValueError("retained-cell data asset retains no cells")
    record_dependency_consumption(
        dependency_result_id=dependency.object_id,
        dependency_result_hash=dependency.object_hash,
        dependency_artifact_hash=str(asset["content_sha256"]),
        usage="row_filter_provenance_only",
        rows_available=len(seen),
        rows_consumed=len(retained),
        columns_consumed=1,
    )
    return retained


def apply_retained_cells(
    cells: list[str],
    retained: set[str] | None,
) -> list[str]:
    if retained is None:
        return list(cells)
    return [cell for cell in cells if cell in retained]


def dependency_grounding_metadata(
    retained: set[str] | None,
    selected_cells: list[str],
) -> dict[str, Any]:
    return {
        "retained_manifest_applied": retained is not None,
        "retained_manifest_cell_count": len(retained) if retained is not None else None,
        "selected_retained_cell_count": len(selected_cells),
    }
