from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STAGE_ROOT = REPO_ROOT / "docs" / "stages"


class StageCatalogError(ValueError):
    """Raised when a requested Pertura stage is missing or malformed."""


def load_stage_index(stage_root: Path | None = None) -> dict[str, Any]:
    root = stage_root or DEFAULT_STAGE_ROOT
    payload = _load_json_yaml(root / "index.yaml")
    stages = payload.get("stages")
    if not isinstance(stages, list):
        raise StageCatalogError("stage catalog index must contain a stages list")
    return payload


def available_stage_ids(stage_root: Path | None = None) -> list[str]:
    index = load_stage_index(stage_root)
    return [str(item.get("stage_id")) for item in index.get("stages", []) if item.get("stage_id")]


def validate_stage_id(stage_id: str | None, stage_root: Path | None = None) -> str | None:
    if stage_id in (None, ""):
        return None
    stage_id = str(stage_id)
    ids = available_stage_ids(stage_root)
    if stage_id not in ids:
        raise StageCatalogError(f"unknown stage id: {stage_id}. Available stages: {', '.join(ids)}")
    return stage_id


def _stage_entry(stage_id: str, stage_root: Path | None = None) -> dict[str, Any]:
    validate_stage_id(stage_id, stage_root)
    index = load_stage_index(stage_root)
    for item in index.get("stages", []):
        if item.get("stage_id") == stage_id:
            return dict(item)
    raise StageCatalogError(f"unknown stage id: {stage_id}")


def load_stage_contract(stage_id: str, stage_root: Path | None = None) -> dict[str, Any]:
    root = stage_root or DEFAULT_STAGE_ROOT
    entry = _stage_entry(stage_id, root)
    return _load_json_yaml(root / str(entry["contract"]))


def load_stage_card(stage_id: str, stage_root: Path | None = None) -> str:
    root = stage_root or DEFAULT_STAGE_ROOT
    entry = _stage_entry(stage_id, root)
    path = root / str(entry["card"])
    if not path.exists():
        raise StageCatalogError(f"stage card missing for {stage_id}: {path}")
    return path.read_text(encoding="utf-8").lstrip("\ufeff")


def build_stage_prompt_section(stage_id: str | None, stage_root: Path | None = None) -> str:
    stage_id = validate_stage_id(stage_id, stage_root)
    if stage_id is None:
        return ""
    contract = load_stage_contract(stage_id, stage_root)
    card = load_stage_card(stage_id, stage_root)
    allowed_tools = contract.get("allowed_mcp_tools") or []
    outputs = contract.get("required_outputs") or []
    boundaries = contract.get("must_not_support") or []
    return "\n".join([
        "# Selected Pertura Stage",
        "",
        f"Stage id: `{stage_id}`",
        f"Stage role: `{contract.get('stage_role', 'unknown')}`",
        f"Default turn surface: `{contract.get('turn_final_surface_type', 'progress_only')}`",
        "",
        "Only execute this stage in this turn. Do not load or perform other stage cards unless the user starts a new turn.",
        "Scratch files and EvidenceCandidates do not support scientific claims until registered and evaluated.",
        "Write all stage outputs, registered metadata, and summaries in English. Prefer ASCII punctuation; avoid smart quotes, non-ASCII dashes, and decorative symbols.",
        "",
        "## Required stage outputs",
        *[f"- `{item}`" for item in outputs],
        "",
        "## Stage boundary",
        *[f"- Must not support: `{item}`" for item in boundaries],
        "",
        "## Stage MCP tools",
        *( [f"- `{item}`" for item in allowed_tools] if allowed_tools else ["- No stage-specific evidence MCP tool required."] ),
        "",
        "## Stage card",
        "",
        card.strip(),
        "",
    ])


def _load_json_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise StageCatalogError(f"stage catalog file missing: {path}")
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StageCatalogError(
            f"{path} must be JSON-compatible YAML so Pertura can load the catalog without optional dependencies"
        ) from exc
    if not isinstance(payload, dict):
        raise StageCatalogError(f"stage catalog file must contain an object: {path}")
    return payload