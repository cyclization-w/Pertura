from __future__ import annotations

import json
import os
import sys
from pathlib import Path

for root_text in [os.environ.get("PERTURA_REPO_ROOT"), str(Path(__file__).resolve().parents[1])]:
    if not root_text:
        continue
    src = Path(root_text).expanduser().resolve() / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.core.policy import GatePolicy
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_claim
from pertura_gate.core.schema import Claim, StrengthCeiling


def _workspace_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "outputs").is_dir() and (candidate / "artifacts").is_dir() and (candidate / "reports").is_dir():
            return candidate
    return cwd


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    workspace = _workspace_root()
    outputs = workspace / "outputs"
    artifacts = workspace / "artifacts"
    reports = workspace / "reports"
    outputs.mkdir(exist_ok=True)
    artifacts.mkdir(exist_ok=True)
    reports.mkdir(exist_ok=True)

    de_path = outputs / "policy_threshold_dummy_de.csv"
    de_path.write_text("gene,log2fc,pvalue,padj\nGENE_X,1.2,0.001,0.01\n", encoding="utf-8")

    registry = EvidenceRegistry.for_run(workspace)
    manifest = registry.register_perturbation_design_manifest(
        path="outputs/policy_threshold_dummy_de.csv",
        dataset_id="policy_threshold_smoke",
        adapter_name="guide_label_v1",
        raw_labels=["KO_NegCtrl0__KO_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        source_column="guide_identity",
    )
    scope = scope_for_raw_label(manifest.metadata["manifest"], "KO_NegCtrl0__KO_NegCtrl0")
    artifact = registry.register_measured_de(
        path="outputs/policy_threshold_dummy_de.csv",
        contrast_left="KO_NegCtrl0__KO_NegCtrl0",
        contrast_baseline="NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
        method="deterministic dummy DE",
        n_left=37,
        n_baseline=80,
        multiple_testing="Benjamini-Hochberg",
        has_padj=True,
        scope=scope,
        eligibility={
            "perturbation_cell_mapping": {
                "assignment_method": "deterministic smoke fixture",
                "guide_to_target_map_hash": "sha256:policy-threshold-guide-map",
            },
            "control_definition": {
                "negative_controls": ["NegCtrl0"],
                "control_label": "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
            },
            "target_qc": {
                "n_target_cells": 37,
                "n_control_cells": 80,
                "guides_per_target": 1,
                "cells_per_guide": {"KO_NegCtrl0__KO_NegCtrl0": 37},
                "min_cell_policy": "policy probe variable",
            },
            "assay_modality": "guide_based_perturb_seq",
            "perturbation_modality": "CRISPR",
            "moi": "low",
            "estimand": "single_target_marginal",
            "control_calibration": {"negative_control_status": "available"},
        },
    )
    claim = Claim(
        claim_id="smoke05_policy_threshold_claim",
        text="KO perturbation has a measured association under the policy threshold probe.",
        subject={"type": "perturbation", "id": "KO"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    strict = GatePolicy(minimum_measured_n=50)
    relaxed = GatePolicy(minimum_measured_n=30)
    strict_decision = resolve_claim(claim, registry, policy=strict)
    relaxed_decision = resolve_claim(claim, registry, policy=relaxed)
    decisions_payload = {
        "strict_policy": {
            "minimum_measured_n": strict.minimum_measured_n,
            "policy_hash": strict.policy_hash,
            "decision": strict_decision.to_dict(),
        },
        "relaxed_policy": {
            "minimum_measured_n": relaxed.minimum_measured_n,
            "policy_hash": relaxed.policy_hash,
            "decision": relaxed_decision.to_dict(),
        },
    }
    _write_json(outputs / "policy_threshold_decisions.json", decisions_payload)
    _write_json(artifacts / "claim_decisions.json", {"decisions": [strict_decision.to_dict(), relaxed_decision.to_dict()]})

    notes = [
        "# Smoke 05 Policy Threshold Probe",
        "",
        f"- Strict policy hash: `{strict.policy_hash}`",
        f"- Relaxed policy hash: `{relaxed.policy_hash}`",
        f"- Strict max strength: `{strict_decision.max_strength.value}`",
        f"- Relaxed max strength: `{relaxed_decision.max_strength.value}`",
        "- Same registry and claim were evaluated under two explicit policy objects.",
        "",
    ]
    (outputs / "policy_threshold_notes.md").write_text("\n".join(notes), encoding="utf-8")
    render_evidence_report(
        registry=registry,
        claims=[claim],
        title="Smoke 05: Policy Threshold Probe",
        write_path=reports / "evidence_report.md",
        policy=relaxed,
    )
    print("policy_threshold_probe_ok")
    print(outputs / "policy_threshold_decisions.json")
    print(reports / "evidence_report.md")


if __name__ == "__main__":
    main()


