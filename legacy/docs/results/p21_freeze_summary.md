# P2.1 Classic Workflow Freeze Summary

This table freezes deterministic P2.1 classic guide-based Perturb-seq workflow behavior.

| case | completion | strengths | linked claims | unlinked claims | runner steps | notes |
| --- | --- | --- | --- | --- | --- | --- |
| `strict_measured_association` | `true` | `measured_association` | `1` | `0` | `none` | strict structured classic recipe produced measured association and downgraded mechanism request |
| `basic_runners_measured_association` | `true` | `measured_association` | `1` | `0` | `run_basic_target_qc, run_basic_de_for_registered_contrast` | basic target QC and basic DE runners produced structured outputs before gate evaluation |
| `candidate_claim_gap` | `true` | `measured_association` | `1` | `1` | `none` | linked candidate evaluated; unlinked candidate remained a gap |
| `partial_success_missing_manifest` | `true` | `none` | `0` | `0` | `none` | incomplete workspace produced partial-success evidence-gap report |

## Frozen Invariants

- classic workflow reports are rendered through ClaimDecision when linked claims exist
- candidate claims without DesignManifest UID scope remain gaps, not scientific findings
- basic runners produce structured tables/summaries only and do not write biological conclusions
- partial-success workspaces report missing evidence instead of upgrading claim strength
## Natural Claude Smoke

| case | completion | strengths | linked claims | overclaim handling | notes |
| --- | --- | --- | --- | --- | --- |
| `smoke12_natural_classic_perturbseq` | `true` | `measured_association` | `2` | `validates_mechanism` downgraded to `measured_association` | natural Claude run completed design manifest, guide assignment, target QC, cell QC, cell-state context, measured DE, explicit claims, and controlled report |

Smoke12 is a natural agent smoke, not the deterministic benchmark baseline. It is retained as evidence that the P2.1 stage/catalog handoff works in a real Claude CodeAct run while the final scientific surface still comes from ClaimDecision.

