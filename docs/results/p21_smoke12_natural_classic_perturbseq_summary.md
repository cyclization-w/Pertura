# P2.1 Smoke12: Natural Classic Perturb-seq Minimal Loop

This smoke freezes the first natural Claude run that completed a classic guide-based Perturb-seq loop under the Evidence-Aware Stage Catalog.

- Run: `.claude_runs\<redacted-run-id>`
- Status: `completed`
- Mode: natural Claude smoke, benchmark interaction mode
- Report: `reports/evidence_report.md`
- Decisions: `artifacts/claim_decisions.json`
- Policy hash: `sha256:e36c98adade4d45f4ab631eb7f09d5c3532f0eb0faf55d56b64caf0134b81935`

## Registered Artifacts

| artifact | kind | evidence class | ceiling |
| --- | --- | --- | --- |
| `design_manifest_7eec8ccbed40` | `perturbation_design_manifest` | `observed_metadata` | `observation` |
| `guide_assignment_41b5627e656f` | `guide_assignment` | `observed_metadata` | `observation` |
| `target_qc_ac6f747d4d18` | `target_qc` | `observed_metadata` | `observation` |
| `cell_qc_eb8001927d09` | `cell_qc` | `observed_metadata` | `observation` |
| `cell_state_reference_9c9fd31c957d` | `cell_state_reference` | `observed_metadata` | `observation` |
| `measured_de_22c48e2022eb` | `measured_de` | `measured` | `measured_association` |

## Claim Decisions

| claim | decision | max strength | scope fit | blocked requested strength |
| --- | --- | --- | --- | --- |
| `claim_overreach_KLF1_erythroid_mechanism` | `allowed_with_downgrade` | `measured_association` | `exact` | `validates_mechanism` |
| `claim_measured_KLF1_vs_NegCtrl0_expression` | `allowed` | `measured_association` | `exact` | `none` |

## Result

The natural run completed the intended minimal loop: perturbation design manifest, guide assignment, target QC, cell QC, cell-state context, measured DE, explicit claims, and controlled evidence report.

The mechanism overclaim used a natural non-enum requested strength, `validates_mechanism`. The runtime did not crash; the gate recorded it as blocked requested strength and downgraded the final scientific surface to `measured_association`.

The measured association claim was allowed because the measured DE artifact had exact UID scope and a validated EligibilityProfile supported by guide assignment, target QC, and cell QC.

## Frozen Invariants

- `cell_state_reference` provides state context only; it does not support perturbation effect claims.
- DesignManifest UID scope is the authority for exact claim-artifact matching.
- Measured DE reaches `measured_association` only with compatible eligibility evidence.
- Mechanism or validation wording in a candidate claim is capped by `ClaimDecision`.
- Natural claim fields and non-enum requested strengths must not crash the runtime.
