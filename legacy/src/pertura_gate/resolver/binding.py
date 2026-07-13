from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pertura_gate.core.schema import Claim, EvidenceArtifact, EvidenceClass, EvidencePredicate, ScopeFit
from pertura_gate.identity.design_manifest import MANIFEST_SCOPE_KEYS, compare_manifest_scope, manifest_scope_is_strong


@dataclass(frozen=True)
class ArtifactRefResolution:
    artifact: EvidenceArtifact | None
    resolved: bool
    reasons: list[str]


@dataclass(frozen=True)
class BindingResolution:
    owner_artifact: EvidenceArtifact
    prediction_artifact: EvidenceArtifact | None = None
    measured_artifact: EvidenceArtifact | None = None
    scope_fit: ScopeFit = ScopeFit.unknown
    resolved: bool = False
    reasons: list[str] = None  # type: ignore[assignment]
    reported_scope_match: str | None = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            object.__setattr__(self, "reasons", [])


def resolve_artifact_ref(
    registry,
    artifact_id: str | None,
    *,
    expected_class: EvidenceClass | None = None,
    expected_predicate: EvidencePredicate | None = None,
    owner_artifact_id: str | None = None,
    label: str = "artifact",
) -> ArtifactRefResolution:
    if not artifact_id:
        return ArtifactRefResolution(None, False, [f"bound {label} artifact id is missing"])
    artifact = registry.get(str(artifact_id))
    if artifact is None:
        return ArtifactRefResolution(None, False, [f"bound {label} artifact {artifact_id!r} could not be resolved"])
    if owner_artifact_id and artifact.artifact_id == owner_artifact_id:
        return ArtifactRefResolution(artifact, False, [f"bound {label} artifact {artifact_id!r} cannot be the binding artifact itself"])
    if expected_class is not None and artifact.effective_evidence_class != expected_class:
        return ArtifactRefResolution(artifact, False, [f"bound {label} artifact {artifact_id!r} is not {expected_class.value} evidence"])
    if expected_predicate is not None and artifact.effective_evidence_predicate != expected_predicate:
        return ArtifactRefResolution(artifact, False, [f"bound {label} artifact {artifact_id!r} is not {expected_predicate.value} evidence"])
    return ArtifactRefResolution(artifact, True, [])


def compute_claim_artifact_scope_fit(claim: Claim, artifact: EvidenceArtifact) -> ScopeFit:
    fit = compare_manifest_scope(claim.scope, artifact.scope)
    return fit if fit is not None else ScopeFit.unknown


def compute_artifact_scope_fit(left: EvidenceArtifact, right: EvidenceArtifact) -> ScopeFit:
    left_scope = dict(left.scope or {})
    right_scope = dict(right.scope or {})
    if not _has_manifest_scope(left_scope) and not _has_manifest_scope(right_scope):
        return ScopeFit.unknown
    left_manifest = left_scope.get("design_manifest_id")
    right_manifest = right_scope.get("design_manifest_id")
    if not left_manifest or not right_manifest:
        return ScopeFit.unknown
    if left_manifest != right_manifest:
        return ScopeFit.mismatch
    if left_scope.get("manifest_uid_validation_error") or right_scope.get("manifest_uid_validation_error"):
        return ScopeFit.mismatch

    compared = False
    compatible = False
    for key in ["contrast_uid", "perturbation_uid", "control_uid", "estimand", "perturbation_kind"]:
        left_value = left_scope.get(key)
        right_value = right_scope.get(key)
        if left_value and right_value:
            compared = True
            if left_value != right_value:
                return ScopeFit.mismatch
        elif left_value or right_value:
            compatible = True

    if compared:
        return ScopeFit.compatible if compatible else ScopeFit.exact
    return ScopeFit.unknown


def resolve_bound_measured_artifact(
    claim: Claim,
    owner_artifact: EvidenceArtifact,
    measured_id: str | None,
    registry,
) -> BindingResolution:
    measured_ref = resolve_artifact_ref(
        registry,
        measured_id,
        expected_class=EvidenceClass.measured,
        owner_artifact_id=owner_artifact.artifact_id,
        label="measured",
    )
    reasons = list(measured_ref.reasons)
    measured = measured_ref.artifact
    scope_fit = ScopeFit.unknown
    if measured is not None:
        claim_fit = compute_claim_artifact_scope_fit(claim, measured)
        owner_fit = compute_artifact_scope_fit(owner_artifact, measured)
        scope_fit = _merge_scope_fits([claim_fit, owner_fit])
        if not manifest_scope_is_strong(claim_fit):
            reasons.append("bound measured artifact does not have UID-compatible scope for this claim")
        if _has_manifest_scope(owner_artifact.scope) and owner_fit == ScopeFit.mismatch:
            reasons.append("binding artifact scope is incompatible with the bound measured artifact")
    resolved = measured_ref.resolved and measured is not None and not any("incompatible" in reason or "does not have UID-compatible scope" in reason for reason in reasons)
    return BindingResolution(
        owner_artifact=owner_artifact,
        measured_artifact=measured,
        scope_fit=scope_fit,
        resolved=resolved,
        reasons=reasons,
        reported_scope_match=_reported_scope_match(owner_artifact),
    )


def resolve_prediction_measured_binding(
    claim: Claim,
    concordance_artifact: EvidenceArtifact,
    registry,
) -> BindingResolution:
    prediction_id = _first(concordance_artifact.quality, "prediction_artifact_id") or _first(concordance_artifact.predicate, "prediction_artifact_id")
    measured_id = _first(concordance_artifact.quality, "measured_artifact_id") or _first(concordance_artifact.predicate, "measured_artifact_id")
    prediction_ref = resolve_artifact_ref(
        registry,
        str(prediction_id) if prediction_id else None,
        expected_class=EvidenceClass.predicted,
        owner_artifact_id=concordance_artifact.artifact_id,
        label="prediction",
    )
    measured_ref = resolve_artifact_ref(
        registry,
        str(measured_id) if measured_id else None,
        expected_class=EvidenceClass.measured,
        owner_artifact_id=concordance_artifact.artifact_id,
        label="measured",
    )
    reasons = [*prediction_ref.reasons, *measured_ref.reasons]
    prediction = prediction_ref.artifact
    measured = measured_ref.artifact
    scope_fit = ScopeFit.unknown
    if prediction is not None and measured is not None:
        prediction_measured_fit = compute_artifact_scope_fit(prediction, measured)
        claim_measured_fit = compute_claim_artifact_scope_fit(claim, measured)
        owner_measured_fit = compute_artifact_scope_fit(concordance_artifact, measured)
        scope_fit = _merge_scope_fits([prediction_measured_fit, claim_measured_fit, owner_measured_fit])
        if not manifest_scope_is_strong(prediction_measured_fit):
            reasons.append("bound prediction artifact is not UID-compatible with the bound measured artifact")
        if not manifest_scope_is_strong(claim_measured_fit):
            reasons.append("bound measured artifact does not have UID-compatible scope for this claim")
        if _has_manifest_scope(concordance_artifact.scope) and owner_measured_fit == ScopeFit.mismatch:
            reasons.append("concordance artifact scope is incompatible with the bound measured artifact")
    resolved = (
        prediction_ref.resolved
        and measured_ref.resolved
        and prediction is not None
        and measured is not None
        and not any("not UID-compatible" in reason or "does not have UID-compatible scope" in reason or "incompatible" in reason for reason in reasons)
    )
    return BindingResolution(
        owner_artifact=concordance_artifact,
        prediction_artifact=prediction,
        measured_artifact=measured,
        scope_fit=scope_fit,
        resolved=resolved,
        reasons=reasons,
        reported_scope_match=_reported_scope_match(concordance_artifact),
    )


def _merge_scope_fits(fits: list[ScopeFit]) -> ScopeFit:
    if ScopeFit.mismatch in fits:
        return ScopeFit.mismatch
    strong = [fit for fit in fits if fit in {ScopeFit.exact, ScopeFit.compatible}]
    if not strong:
        return ScopeFit.unknown
    if ScopeFit.compatible in strong or ScopeFit.unknown in fits:
        return ScopeFit.compatible
    return ScopeFit.exact


def _has_manifest_scope(scope: dict[str, Any] | None) -> bool:
    return any((scope or {}).get(key) for key in MANIFEST_SCOPE_KEYS)


def _reported_scope_match(artifact: EvidenceArtifact) -> str | None:
    value = _first(artifact.quality, "reported_scope_match", "scope_match")
    return str(value) if value not in (None, "") else None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
