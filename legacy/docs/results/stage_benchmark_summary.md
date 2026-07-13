# Pertura Stage Benchmark Summary

This deterministic benchmark freezes the first Evidence-Aware Stage Catalog boundaries.

| case | stage | completion | strengths | metrics | notes |
| --- | --- | --- | --- | --- | --- |
| `guide_assignment_eligibility_only` | `guide_assignment` | `true` | `none` | `6/6` | guide assignment registered as analysis eligibility only |
| `cell_state_reference_context_only` | `cell_state_reference` | `true` | `observation` | `6/6` | cell state reference remained state context and did not support effect claim |
| `measured_de_association_only` | `measured_de` | `true` | `measured_association` | `6/6` | measured DE downgraded mechanism request to measured association |
| `claim_report_decision_surface` | `claim_report` | `true` | `measured_association` | `6/6` | claim report rendered through ClaimDecision |

## Invariants

- guide_assignment is eligibility metadata only and cannot support measured claims by itself
- cell_state_reference is state context only and cannot support perturbation effect claims by itself
- measured_de supports measured_association at most and cannot establish mechanism
- claim_report surfaces scientific conclusions only through ClaimDecision rendering
