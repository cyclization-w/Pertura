"""Source-aware perturb-seq design ledger projection."""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot, _model_dump

from .ontology import (
    DESIGN_FIELDS,
    FIELD_ORDER,
    candidate_columns_from_text,
    confidence_label,
    normalize_design_key,
    source_label,
)


def compile_design_ledger(snap: Snapshot | None) -> dict[str, Any]:
    """Compile perturb-seq design facts without mutating runtime state."""
    if snap is None:
        return {
            "view_type": "perturbseq_design_ledger",
            "fields": [],
            "summary": {"known": 0, "missing": len(FIELD_ORDER), "blocking_missing": []},
            "suggested_questions": [],
            "dataset_profile": {},
        }

    raw_design = dict(getattr(snap, "design", {}) or {})
    design = {normalize_design_key(key): value for key, value in raw_design.items()}
    meta = _normalized_meta(getattr(snap, "design_meta", {}) or {})
    candidates = _schema_candidates(snap)
    dataset_profile = _dataset_profile(snap)

    fields = []
    questions = []
    for spec in DESIGN_FIELDS:
        value = design.get(spec.field_id)
        has_value = _has_value(value)
        field_meta = meta.get(spec.field_id, {})
        status = "known" if has_value else "missing"
        if not has_value and candidates.get(spec.field_id):
            status = "candidate"
        field = {
            "field_id": spec.field_id,
            "label": spec.label,
            "value": value if has_value else None,
            "display_value": _display_value(value) if has_value else "",
            "status": status,
            "source": source_label(field_meta.get("source") or ("data_observed" if candidates.get(spec.field_id) else "")),
            "confidence": confidence_label(field_meta.get("confidence"), has_value=has_value),
            "required_before_interpretation": spec.required_before_interpretation,
            "candidates": candidates.get(spec.field_id, []),
            "question": spec.question,
            "evidence_refs": _evidence_refs_for_field(snap, spec.field_id),
        }
        fields.append(field)
        if not has_value and spec.required_before_interpretation:
            questions.append({
                "field_id": spec.field_id,
                "question": spec.question,
                "options": candidates.get(spec.field_id, []),
                "severity": "blocking",
            })

    known = [item for item in fields if item["status"] == "known"]
    missing = [item for item in fields if item["status"] != "known"]
    blocking_missing = [
        item["field_id"] for item in missing
        if item.get("required_before_interpretation")
    ]
    return {
        "view_type": "perturbseq_design_ledger",
        "fields": fields,
        "dataset_profile": dataset_profile,
        "summary": {
            "known": len(known),
            "missing": len(missing),
            "blocking_missing": blocking_missing,
            "ready_for_interpretation": not blocking_missing,
        },
        "suggested_questions": questions[:5],
    }


def _normalized_meta(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, value in meta.items():
        norm = normalize_design_key(key)
        if isinstance(value, dict):
            out[norm] = value
        else:
            out[norm] = {"source": value}
    return out


def _schema_candidates(snap: Snapshot) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for obs in getattr(snap, "observations", []) or []:
        text = " ".join(str(item) for item in [
            getattr(obs, "target", ""),
            getattr(obs, "metric", ""),
            getattr(obs, "value", ""),
            getattr(obs, "parameters", {}),
        ])
        for field_id, values in candidate_columns_from_text(text).items():
            out.setdefault(field_id, [])
            for value in values:
                if value not in out[field_id]:
                    out[field_id].append(value)
    return {key: values[:8] for key, values in out.items()}


def _dataset_profile(snap: Snapshot) -> dict[str, Any]:
    observations = []
    for obs in getattr(snap, "observations", []) or []:
        metric = str(getattr(obs, "metric", "") or "")
        target = str(getattr(obs, "target", "") or "")
        if any(token in f"{target} {metric}".lower() for token in ("shape", "schema", "dataset", "workspace", "obs", "var")):
            observations.append({
                "observation_id": obs.observation_id,
                "target": obs.target,
                "metric": obs.metric,
                "value": obs.value,
                "attempt_id": obs.attempt_id,
            })
    loaded = bool(observations) or any(
        getattr(art, "kind", "") in {"anndata", "dataset", "table", "mapping_table"}
        for art in getattr(snap, "artifacts", []) or []
    )
    return {
        "workspace": getattr(snap, "workspace", ""),
        "loaded": loaded,
        "observations": observations[-5:],
        "artifacts": [
            _model_dump(art) for art in (getattr(snap, "artifacts", []) or [])
            if getattr(art, "kind", "") in {"anndata", "dataset", "table", "mapping_table"}
        ][-5:],
    }


def _evidence_refs_for_field(snap: Snapshot, field_id: str) -> list[str]:
    refs = []
    for obs in getattr(snap, "observations", []) or []:
        text = f"{obs.target} {obs.metric} {obs.value}".lower()
        if field_id.replace("_", " ") in text or field_id in text:
            refs.append(obs.observation_id)
    return refs[-5:]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _display_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value)
