from __future__ import annotations

from pertura_gate.identity.design_manifest import build_guide_label_manifest
from pertura_workflow.claims import link_candidate_claims, normalize_candidate_claims


def _manifest() -> dict:
    return build_guide_label_manifest(
        manifest_id="design_manifest_test",
        dataset_id="synthetic",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
    ).to_dict()


def test_candidate_claim_raw_label_links_to_manifest_uid_scope() -> None:
    links = link_candidate_claims(
        [
            {
                "claim_id": "claim_klf1",
                "text": "KLF1 has a measured association.",
                "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
            }
        ],
        manifest=_manifest(),
        default_evidence_refs=["measured_de_001"],
        default_subject_id="KLF1",
    )

    assert len(links) == 1
    assert links[0].status == "linked"
    assert links[0].claim is not None
    assert links[0].claim.scope["perturbation_uid"] == "target:KLF1"
    assert links[0].claim.evidence_refs == ["measured_de_001"]


def test_candidate_claim_without_scope_stays_unlinked() -> None:
    links = link_candidate_claims(
        [{"claim_id": "claim_missing_scope", "text": "KLF1 changes expression."}],
        manifest=_manifest(),
        default_evidence_refs=["measured_de_001"],
    )

    assert links[0].status == "unlinked"
    assert links[0].claim is None
    assert "did not provide" in links[0].reasons[0]


def test_legacy_single_claim_uses_recipe_default_scope() -> None:
    candidates = normalize_candidate_claims({"claim": {"claim_id": "legacy_claim"}})
    assert candidates[0]["use_default_scope"] is True