from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pertura_gate.core.schema import ScopeFit


MANIFEST_SCOPE_KEYS = {"design_manifest_id", "perturbation_uid", "control_uid", "contrast_uid", "estimand"}
CONTROL_TOKENS = {"ntc", "non_targeting", "nontargeting", "non-targeting", "mock", "vehicle", "dmso", "untreated"}


@dataclass(frozen=True)
class PerturbationIdentity:
    perturbation_uid: str
    perturbation_type: str
    modality: str = "unknown"
    kind: str = "single"
    raw_labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    control_role: str | None = None
    dose: str | None = None
    dose_unit: str | None = None
    timepoint: str | None = None
    confidence: float = 1.0
    provenance_level: str = "deterministic_rule"

    def to_dict(self) -> dict[str, Any]:
        return {
            "perturbation_uid": self.perturbation_uid,
            "perturbation_type": self.perturbation_type,
            "modality": self.modality,
            "kind": self.kind,
            "raw_labels": list(self.raw_labels),
            "targets": list(self.targets),
            "control_role": self.control_role,
            "dose": self.dose,
            "dose_unit": self.dose_unit,
            "timepoint": self.timepoint,
            "confidence": self.confidence,
            "provenance_level": self.provenance_level,
        }


@dataclass(frozen=True)
class ContrastIdentity:
    contrast_uid: str
    left_uid: str
    baseline_uid: str
    contrast_type: str
    estimand: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contrast_uid": self.contrast_uid,
            "left_uid": self.left_uid,
            "baseline_uid": self.baseline_uid,
            "contrast_type": self.contrast_type,
            "estimand": self.estimand,
        }


@dataclass(frozen=True)
class RawLabelMapping:
    source_column: str
    raw_value: str
    canonical_uid: str
    parse_rule: str
    adapter_name: str
    adapter_version: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_column": self.source_column,
            "raw_value": self.raw_value,
            "canonical_uid": self.canonical_uid,
            "parse_rule": self.parse_rule,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class PerturbationDesignManifest:
    manifest_id: str
    dataset_id: str | None
    adapter_name: str
    adapter_version: str
    provenance_level: str
    perturbations: dict[str, PerturbationIdentity] = field(default_factory=dict)
    contrasts: dict[str, ContrastIdentity] = field(default_factory=dict)
    raw_label_index: dict[str, str] = field(default_factory=dict)
    raw_label_mappings: list[RawLabelMapping] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "dataset_id": self.dataset_id,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "provenance_level": self.provenance_level,
            "perturbations": {key: value.to_dict() for key, value in sorted(self.perturbations.items())},
            "contrasts": {key: value.to_dict() for key, value in sorted(self.contrasts.items())},
            "raw_label_index": dict(sorted(self.raw_label_index.items())),
            "raw_label_mappings": [mapping.to_dict() for mapping in self.raw_label_mappings],
        }


def build_guide_label_manifest(
    *,
    manifest_id: str,
    raw_labels: list[str],
    dataset_id: str | None = None,
    source_column: str = "guide_identity",
    guide_to_target_map: dict[str, str] | None = None,
    adapter_version: str = "guide_label_v1",
    provenance_level: str = "deterministic_rule",
) -> PerturbationDesignManifest:
    perturbations: dict[str, PerturbationIdentity] = {}
    contrasts: dict[str, ContrastIdentity] = {}
    raw_index: dict[str, str] = {}
    mappings: list[RawLabelMapping] = []
    target_map = {str(key): str(value) for key, value in (guide_to_target_map or {}).items()}

    control_uid = "control:negative_control_pool"
    control_identity = PerturbationIdentity(
        perturbation_uid=control_uid,
        perturbation_type="control_pool",
        modality="guide_based",
        kind="control",
        raw_labels=[],
        control_role="negative_control",
        provenance_level=provenance_level,
    )

    for raw_label in raw_labels:
        raw = str(raw_label)
        parsed = parse_guide_label(raw, guide_to_target_map=target_map)
        uid = parsed["perturbation_uid"]
        raw_index[raw] = uid
        mappings.append(
            RawLabelMapping(
                source_column=source_column,
                raw_value=raw,
                canonical_uid=uid,
                parse_rule=parsed["parse_rule"],
                adapter_name="guide_label_v1",
                adapter_version=adapter_version,
                confidence=parsed["confidence"],
            )
        )
        if parsed["kind"] == "control":
            existing = perturbations.get(control_uid, control_identity)
            perturbations[control_uid] = PerturbationIdentity(
                perturbation_uid=control_uid,
                perturbation_type="control_pool",
                modality="guide_based",
                kind="control",
                raw_labels=sorted(set([*existing.raw_labels, raw])),
                control_role="negative_control",
                confidence=min(existing.confidence, parsed["confidence"]),
                provenance_level=provenance_level,
            )
            continue

        existing = perturbations.get(uid)
        raw_labels_for_uid = sorted(set([*(existing.raw_labels if existing else []), raw]))
        perturbations[uid] = PerturbationIdentity(
            perturbation_uid=uid,
            perturbation_type=parsed["perturbation_type"],
            modality=parsed["modality"],
            kind=parsed["kind"],
            raw_labels=raw_labels_for_uid,
            targets=parsed["targets"],
            confidence=parsed["confidence"] if existing is None else min(existing.confidence, parsed["confidence"]),
            provenance_level=provenance_level,
        )
        if parsed["inferred_control_uid"]:
            baseline = parsed["inferred_control_uid"]
            perturbations.setdefault(baseline, control_identity)
            contrast_uid = contrast_uid_for(uid, baseline)
            contrasts[contrast_uid] = ContrastIdentity(
                contrast_uid=contrast_uid,
                left_uid=uid,
                baseline_uid=baseline,
                contrast_type="combinatorial_vs_control" if parsed["kind"] == "combinatorial" else "target_vs_control",
                estimand="combinatorial" if parsed["kind"] == "combinatorial" else "single_target_marginal",
            )

    return PerturbationDesignManifest(
        manifest_id=manifest_id,
        dataset_id=dataset_id,
        adapter_name="guide_label_v1",
        adapter_version=adapter_version,
        provenance_level=provenance_level,
        perturbations=perturbations,
        contrasts=contrasts,
        raw_label_index=raw_index,
        raw_label_mappings=mappings,
    )


def build_treatment_condition_manifest(
    *,
    manifest_id: str,
    conditions: list[dict[str, Any] | str],
    dataset_id: str | None = None,
    source_column: str = "condition",
    adapter_version: str = "treatment_condition_v1",
    provenance_level: str = "deterministic_rule",
) -> PerturbationDesignManifest:
    perturbations: dict[str, PerturbationIdentity] = {}
    contrasts: dict[str, ContrastIdentity] = {}
    raw_index: dict[str, str] = {}
    mappings: list[RawLabelMapping] = []
    vehicle_uid: str | None = None

    parsed_conditions = [_parse_treatment_condition(item) for item in conditions]
    for parsed in parsed_conditions:
        uid = parsed["perturbation_uid"]
        raw = parsed["raw_label"]
        raw_index[raw] = uid
        mappings.append(
            RawLabelMapping(
                source_column=source_column,
                raw_value=raw,
                canonical_uid=uid,
                parse_rule=parsed["parse_rule"],
                adapter_name="treatment_condition_v1",
                adapter_version=adapter_version,
                confidence=parsed["confidence"],
            )
        )
        perturbations[uid] = PerturbationIdentity(
            perturbation_uid=uid,
            perturbation_type=parsed["perturbation_type"],
            modality="chemical",
            kind=parsed["kind"],
            raw_labels=[raw],
            targets=[],
            control_role=parsed["control_role"],
            dose=parsed["dose"],
            dose_unit=parsed["dose_unit"],
            timepoint=parsed["timepoint"],
            confidence=parsed["confidence"],
            provenance_level=provenance_level,
        )
        if parsed["control_role"] in {"vehicle", "untreated"} and vehicle_uid is None:
            vehicle_uid = uid

    if vehicle_uid:
        for uid, identity in perturbations.items():
            if uid == vehicle_uid or identity.control_role:
                continue
            contrast_uid = contrast_uid_for(uid, vehicle_uid)
            contrasts[contrast_uid] = ContrastIdentity(
                contrast_uid=contrast_uid,
                left_uid=uid,
                baseline_uid=vehicle_uid,
                contrast_type="treatment_vs_vehicle",
                estimand="treatment_effect",
            )

    return PerturbationDesignManifest(
        manifest_id=manifest_id,
        dataset_id=dataset_id,
        adapter_name="treatment_condition_v1",
        adapter_version=adapter_version,
        provenance_level=provenance_level,
        perturbations=perturbations,
        contrasts=contrasts,
        raw_label_index=raw_index,
        raw_label_mappings=mappings,
    )


def parse_guide_label(raw_label: str, *, guide_to_target_map: dict[str, str] | None = None) -> dict[str, Any]:
    raw = str(raw_label)
    target_map = guide_to_target_map or {}
    if raw in target_map:
        mapped_target = _gene_token(target_map[raw])
        if mapped_target:
            return {
                "perturbation_uid": target_uid(mapped_target),
                "perturbation_type": "target_gene",
                "modality": "guide_based",
                "kind": "single",
                "targets": [mapped_target],
                "inferred_control_uid": None,
                "parse_rule": "guide_label_v1:guide_to_target_map_exact",
                "confidence": 1.0,
            }
    parts = [part for part in raw.split("__") if part]
    core = parts[0] if parts else raw
    raw_tokens = [token for token in re.split(r"[_\s+/;-]+", core) if token]
    targets: list[str] = []
    control_seen = False
    for token in raw_tokens:
        mapped = target_map.get(token)
        if mapped:
            token = mapped
        if _is_control_label(token):
            control_seen = True
            continue
        target = _gene_token(token)
        if target:
            targets.append(target)
    unique_targets = sorted(set(targets))
    if not unique_targets:
        return {
            "perturbation_uid": "control:negative_control_pool",
            "perturbation_type": "control_pool",
            "modality": "guide_based",
            "kind": "control",
            "targets": [],
            "inferred_control_uid": None,
            "parse_rule": "guide_label_v1:control_token",
            "confidence": 1.0,
        }
    kind = "combinatorial" if len(unique_targets) > 1 else "single"
    uid = combo_uid(unique_targets) if kind == "combinatorial" else target_uid(unique_targets[0])
    return {
        "perturbation_uid": uid,
        "perturbation_type": "combinatorial" if kind == "combinatorial" else "target_gene",
        "modality": "combinatorial" if kind == "combinatorial" else "guide_based",
        "kind": kind,
        "targets": unique_targets,
        "inferred_control_uid": "control:negative_control_pool" if control_seen else None,
        "parse_rule": "guide_label_v1:split_double_underscore_and_control_tokens",
        "confidence": 0.95 if control_seen or "__" in raw else 0.85,
    }


def compare_manifest_scope(claim_scope: dict[str, Any] | None, artifact_scope: dict[str, Any] | None) -> ScopeFit | None:
    claim = dict(claim_scope or {})
    artifact = dict(artifact_scope or {})
    claim_has_manifest = _has_manifest_scope(claim)
    artifact_has_manifest = _has_manifest_scope(artifact)
    if not claim_has_manifest and not artifact_has_manifest:
        return None
    if claim.get("design_manifest_id") and artifact.get("design_manifest_id") and claim["design_manifest_id"] != artifact["design_manifest_id"]:
        return ScopeFit.mismatch
    if claim_has_manifest != artifact_has_manifest:
        return ScopeFit.unknown

    compared = False
    compatible = False
    if claim.get("contrast_uid"):
        compared = True
        if claim.get("contrast_uid") != artifact.get("contrast_uid"):
            return ScopeFit.mismatch
        return ScopeFit.exact
    if claim.get("perturbation_uid"):
        compared = True
        if claim.get("perturbation_uid") != artifact.get("perturbation_uid"):
            return ScopeFit.mismatch
    if claim.get("control_uid"):
        compared = True
        if artifact.get("control_uid") and claim.get("control_uid") != artifact.get("control_uid"):
            return ScopeFit.mismatch
        if not artifact.get("control_uid"):
            compatible = True
    if claim.get("estimand"):
        compared = True
        if artifact.get("estimand") and claim.get("estimand") != artifact.get("estimand"):
            return ScopeFit.mismatch
        if not artifact.get("estimand"):
            compatible = True
    if claim.get("perturbation_kind") and artifact.get("perturbation_kind"):
        compared = True
        if claim.get("perturbation_kind") != artifact.get("perturbation_kind"):
            return ScopeFit.mismatch
    if not compared:
        return ScopeFit.unknown
    return ScopeFit.compatible if compatible else ScopeFit.exact


def manifest_scope_is_strong(scope_fit: ScopeFit | None) -> bool:
    return scope_fit in {ScopeFit.exact, ScopeFit.compatible}


def _has_manifest_scope(scope: dict[str, Any]) -> bool:
    return any(scope.get(key) for key in MANIFEST_SCOPE_KEYS)


def scope_for_raw_label(manifest: PerturbationDesignManifest | dict[str, Any], raw_label: str) -> dict[str, Any]:
    payload = manifest.to_dict() if isinstance(manifest, PerturbationDesignManifest) else dict(manifest)
    raw_index = dict(payload.get("raw_label_index") or {})
    uid = raw_index.get(str(raw_label))
    if not uid:
        return {}
    perturbation = (payload.get("perturbations") or {}).get(uid) or {}
    control_uid = None
    contrast_uid = None
    estimand = None
    for contrast in (payload.get("contrasts") or {}).values():
        if contrast.get("left_uid") == uid:
            control_uid = contrast.get("baseline_uid")
            contrast_uid = contrast.get("contrast_uid")
            estimand = contrast.get("estimand")
            break
    return {
        key: value
        for key, value in {
            "design_manifest_id": payload.get("manifest_id"),
            "perturbation_uid": uid,
            "control_uid": control_uid,
            "contrast_uid": contrast_uid,
            "perturbation_kind": perturbation.get("kind"),
            "perturbation_type": perturbation.get("perturbation_type"),
            "estimand": estimand,
        }.items()
        if value not in (None, "")
    }


def target_uid(gene: str) -> str:
    return "target:" + _safe_token(gene).upper()


def combo_uid(targets: list[str]) -> str:
    return "combo:" + "+".join(_safe_token(target).upper() for target in sorted(targets))


def contrast_uid_for(left_uid: str, baseline_uid: str) -> str:
    return f"contrast:{left_uid}:vs:{baseline_uid}"


def _parse_treatment_condition(item: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(item, dict):
        raw = str(item.get("raw_label") or item.get("condition") or item.get("treatment") or item.get("compound") or "")
        compound = str(item.get("compound") or item.get("treatment") or raw).strip()
        dose = _text_or_none(item.get("dose"))
        dose_unit = _text_or_none(item.get("dose_unit") or item.get("unit"))
        timepoint = _text_or_none(item.get("timepoint") or item.get("time"))
        control_role = _control_role(compound or raw)
    else:
        raw = str(item)
        compound, dose, dose_unit, timepoint = _parse_condition_string(raw)
        control_role = _control_role(compound or raw)
    if control_role:
        uid = f"control:{control_role}:{_safe_token(compound or raw).lower()}"
        perturbation_type = "control_pool"
        kind = "control"
    else:
        uid = "treatment:" + ":".join(part for part in [_safe_token(compound).lower(), _safe_token(dose).lower(), _safe_token(timepoint).lower()] if part)
        perturbation_type = "treatment_condition"
        kind = "dose_time_condition" if dose or timepoint else "treatment"
    return {
        "raw_label": raw,
        "perturbation_uid": uid,
        "perturbation_type": perturbation_type,
        "kind": kind,
        "dose": dose,
        "dose_unit": dose_unit,
        "timepoint": timepoint,
        "control_role": control_role,
        "parse_rule": "treatment_condition_v1:condition_fields_or_dose_time_tokens",
        "confidence": 0.95 if isinstance(item, dict) else 0.8,
    }


def _parse_condition_string(raw: str) -> tuple[str, str | None, str | None, str | None]:
    text = raw.strip()
    dose_match = re.search(r"(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>uM|um|碌M|nM|nm|mM|mm)", text, flags=re.IGNORECASE)
    time_match = re.search(r"(?P<time>\d+(?:\.\d+)?)\s*(?P<unit>hr|hrs|h|hour|hours|d|day|days)", text, flags=re.IGNORECASE)
    compound = text
    dose = dose_match.group("dose") if dose_match else None
    dose_unit = dose_match.group("unit") if dose_match else None
    timepoint = (time_match.group("time") + time_match.group("unit")) if time_match else None
    if dose_match:
        compound = compound.replace(dose_match.group(0), "")
    if time_match:
        compound = compound.replace(time_match.group(0), "")
    compound = re.sub(r"[_\-]+", " ", compound).strip() or text
    return compound, dose, dose_unit, timepoint


def _gene_token(value: str) -> str | None:
    token = re.sub(r"[^A-Za-z0-9]+", "", str(value).strip())
    if not token or token.isdigit() or _is_control_label(token):
        return None
    return token.upper()


def _is_control_label(value: str) -> bool:
    token = str(value).strip().lower().replace("-", "_")
    return token.startswith("negctrl") or token in CONTROL_TOKENS


def _control_role(value: str) -> str | None:
    token = str(value).strip().lower().replace("-", "_")
    if token in {"dmso", "vehicle"}:
        return "vehicle"
    if token in {"untreated", "mock"}:
        return "untreated"
    if _is_control_label(token):
        return "negative_control"
    return None


def _safe_token(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "unknown"


def _text_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)

