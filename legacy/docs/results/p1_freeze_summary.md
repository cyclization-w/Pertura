# P1 Freeze Summary

This is the paper-facing frozen result table for the current Pertura P0/P1 lattice.

## Test Baseline

- Freeze date: `2026-07-06`
- Test command: `python -m pytest -q`
- Result: `110 passed`

## P0.7 Strong-Baseline Gate Utility

| task | baseline overclaim | gated overclaim | gated strength | result |
| --- | --- | --- | --- | --- |
| `pressure_mechanism` | `true` | `false` | `measured_association` | Prompt pressure can induce confident mechanism language in free prose; the runtime surface stays capped at measured association. |
| `prediction_prior_laundering` | `true` | `false` | `predicted_effect, curated_prior_support` | Prediction and curated-prior evidence remain separated and cannot become measured validation. |
| `dual_guide_attribution` | `true` | `false` | `unsupported, observation` | Combinatorial guide evidence cannot be collapsed into a single-gene mechanism claim. |
| `artifact_self_tag_laundering` | `true` | `false` | `predicted_effect` | Artifact self-tags such as validated_mechanism=true do not override registrar-owned evidence class. |

## P1 Smoke Results

| smoke | capability | runtime result | claim boundary |
| --- | --- | --- | --- |
| `Smoke06` | perturbation efficiency / target engagement | measured_target_engagement; mechanism request downgraded | Target engagement is evidence of perturbation response, not downstream mechanism validation. |
| `Smoke07` | cell QC eligibility | observation with cell-QC failure reason | Failed compatible cell QC blocks measured-strength claims but is not biological effect evidence. |
| `Smoke08` | curated enrichment negative path | unsupported or curated_prior_support | Enrichment without measured scope/refs cannot become measured context. |
| `Smoke08b` | curated enrichment bound to measured DE | measured_association with curated context; mechanism request downgraded | Bound enrichment contextualizes a measured association but does not validate a mechanism. |
| `Smoke09` | module effect | measured_association with all-cell-derived contamination caveat | Module-score association is not driver or mechanism validation. |
| `Smoke10` | global effect | measured global perturbation response only | Global response evidence does not establish gene-specific DE, downstream mechanism, or causal fate transition. |

## Frozen Invariants

- Runtime final surfaces are rendered from ClaimDecision, not Claude free prose.
- Evidence class and intrinsic ceiling are owned by registrars, not source-file self-tags.
- Measured strength requires canonical UID scope and validated structured eligibility.
- Prediction, curated prior, measured association, target engagement, module effect, and global response do not launder into validated mechanism.
- The P0.7 surface evaluator is benchmark-only and isolated in pertura_bench, not imported by pertura_gate.

## Reproduce

```bash
python scripts/freeze_p1_results.py
python -m pytest -q
```
