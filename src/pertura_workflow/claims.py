from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pertura_gate.core.schema import Claim
from pertura_gate.identity.design_manifest import MANIFEST_SCOPE_KEYS, scope_for_raw_label


@dataclass(frozen=True)
class CandidateClaimLink:
    candidate_claim_id: str
    status: str
    reasons: list[str] = field(default_factory=list)
    claim: Claim | None = None
    source_candidate_id: str | None = None
    linked_scope: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_claim_id": self.candidate_claim_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "source_candidate_id": self.source_candidate_id,
            "linked_scope": dict(self.linked_scope),
            "claim": self.claim.to_dict() if self.claim else None,
        }


def normalize_candidate_claims(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return candidate-claim payloads from a recipe config.

    A single legacy `claim` entry is treated as one candidate claim. Multiple
    `candidate_claims` may be supplied for P2.1 linking tests and later workflow
    generation. These are candidate claims only until linked and resolved.
    """

    candidates: list[dict[str, Any]] = []
    raw_candidates = payload.get("candidate_claims")
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if isinstance(item, dict):
                candidates.append(dict(item))
    if not candidates and isinstance(payload.get("claim"), dict):
        legacy_claim = dict(payload["claim"])
        legacy_claim.setdefault("use_default_scope", True)
        legacy_claim.setdefault("use_default_evidence_refs", True)
        candidates.append(legacy_claim)
    return candidates


def link_candidate_claims(
    candidate_claims: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
    default_scope: dict[str, Any] | None = None,
    default_evidence_refs: list[str] | None = None,
    default_subject_id: str | None = None,
) -> list[CandidateClaimLink]:
    links: list[CandidateClaimLink] = []
    for index, payload in enumerate(candidate_claims, start=1):
        candidate_id = str(payload.get("claim_id") or payload.get("candidate_claim_id") or f"candidate_claim_{index}")
        scope, reasons = _link_scope(payload, manifest=manifest, default_scope=default_scope)
        if not scope:
            links.append(
                CandidateClaimLink(
                    candidate_claim_id=candidate_id,
                    status="unlinked",
                    reasons=reasons or ["candidate claim lacks DesignManifest UID scope"],
                    source_candidate_id=_text_or_none(payload.get("source_candidate_id")),
                )
            )
            continue
        evidence_refs = _evidence_refs(payload, default_evidence_refs=default_evidence_refs)
        if not evidence_refs:
            links.append(
                CandidateClaimLink(
                    candidate_claim_id=candidate_id,
                    status="unlinked",
                    reasons=[*reasons, "candidate claim has no registered evidence_refs"],
                    source_candidate_id=_text_or_none(payload.get("source_candidate_id")),
                    linked_scope=scope,
                )
            )
            continue
        claim = Claim.from_dict(
            {
                "claim_id": candidate_id,
                "text": payload.get("text") or "Candidate claim from Pertura workflow.",
                "subject": payload.get("subject") or {"type": "perturbation", "id": payload.get("subject_id") or default_subject_id or scope.get("perturbation_uid")},
                "relation": payload.get("relation") or "associated_with_expression_change",
                "object": payload.get("object") or {"type": "registered_effect", "id": payload.get("object_id") or "registered_evidence"},
                "scope": scope,
                "requested_strength": payload.get("requested_strength") or "measured_association",
                "evidence_refs": evidence_refs,
            }
        )
        links.append(
            CandidateClaimLink(
                candidate_claim_id=candidate_id,
                status="linked",
                reasons=reasons,
                claim=claim,
                source_candidate_id=_text_or_none(payload.get("source_candidate_id")),
                linked_scope=scope,
            )
        )
    return links


def _link_scope(
    payload: dict[str, Any],
    *,
    manifest: dict[str, Any],
    default_scope: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    explicit_scope = payload.get("scope")
    if isinstance(explicit_scope, dict) and _has_manifest_scope(explicit_scope):
        return dict(explicit_scope), ["candidate claim provided explicit manifest UID scope"]

    raw_label = payload.get("perturbation_raw_label") or payload.get("raw_label") or payload.get("condition_raw_label")
    if raw_label:
        scope = scope_for_raw_label(manifest, str(raw_label))
        if scope:
            return scope, ["candidate claim raw label linked through DesignManifest"]
        return {}, ["candidate claim raw label is not present in DesignManifest"]

    uid = payload.get("perturbation_uid")
    if uid:
        scope = _scope_for_uid(manifest, str(uid))
        if scope:
            return scope, ["candidate claim perturbation_uid linked through DesignManifest"]
        return {}, ["candidate claim perturbation_uid is not present in DesignManifest"]

    if default_scope and bool(payload.get("use_default_scope", False)):
        reasons.append("candidate claim explicitly requested recipe default manifest UID scope")
        return dict(default_scope), reasons

    return {}, ["candidate claim did not provide explicit scope, raw label, perturbation_uid, or use_default_scope=true"]


def _scope_for_uid(manifest: dict[str, Any], uid: str) -> dict[str, Any]:
    perturbation = (manifest.get("perturbations") or {}).get(uid)
    if not perturbation:
        return {}
    control_uid = None
    contrast_uid = None
    estimand = None
    for contrast in (manifest.get("contrasts") or {}).values():
        if contrast.get("left_uid") == uid:
            control_uid = contrast.get("baseline_uid")
            contrast_uid = contrast.get("contrast_uid")
            estimand = contrast.get("estimand")
            break
    return {
        key: value
        for key, value in {
            "design_manifest_id": manifest.get("manifest_id"),
            "perturbation_uid": uid,
            "control_uid": control_uid,
            "contrast_uid": contrast_uid,
            "perturbation_kind": perturbation.get("kind"),
            "perturbation_type": perturbation.get("perturbation_type"),
            "estimand": estimand,
        }.items()
        if value not in (None, "")
    }


def _evidence_refs(payload: dict[str, Any], *, default_evidence_refs: list[str] | None) -> list[str]:
    refs = payload.get("evidence_refs")
    if isinstance(refs, list):
        return [str(ref) for ref in refs if str(ref)]
    if payload.get("use_default_evidence_refs", True) and default_evidence_refs:
        return list(default_evidence_refs)
    return []


def _has_manifest_scope(scope: dict[str, Any]) -> bool:
    return any(scope.get(key) for key in MANIFEST_SCOPE_KEYS)


def _text_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)