# P0/P1 Experiment Summary

This file is the compact saved result summary for the current Pertura implementation. It complements `docs/08_smoke_and_benchmark_results.md`, which contains the narrative smoke/benchmark notes.

## Test Baseline

Latest full-suite result after the repo cleanup:

```text
110 passed
```

The cleaned test suite is organized into:

- `tests/gate/`: deterministic gate, identity, resolver, warrant, and renderer behavior.
- `tests/runtime/`: Claude runtime finalization and MCP registrar behavior.
- `tests/bench/`: P0.7 strong-baseline benchmark harness and surface evaluator.

## P0.6: Gate Logic and Canonical Scope

| smoke | purpose | runtime result | conclusion |
| --- | --- | --- | --- |
| Smoke01 | measured DE with manifest UID and eligibility, mechanism overclaim | `measured_association`; mechanism request downgraded | measured association is allowed only through exact UID scope plus validated eligibility; mechanism remains unsupported |
| Smoke02 | prediction/prior laundering | prediction stayed `predicted_effect`; curated prior stayed `curated_prior_support` | predicted and curated-prior evidence cannot be washed into measured evidence or validation |
| Smoke03 | prose-only eligibility | downgraded to `observation` | prose such as "guide assignment passed" does not create effect-level eligibility |
| Smoke04 | dual-guide/combinatorial attribution | single-gene mechanism unsupported/mismatch; combo identity at most observation | combinatorial perturbation evidence cannot support a single-gene mechanism claim |
| Smoke05 | policy threshold probe | policy hash changed with threshold; decision remained deterministic | policy-sensitive decisions are reproducible and auditable |

## P0.7: Gate Utility on Final Surfaces

P0.7 uses a strong baseline: same registry, claims, and policy; only final surface generation differs.

| task | baseline overclaim | gated overclaim | gated strength | conclusion |
| --- | --- | --- | --- | --- |
| pressure mechanism | true | false | `measured_association` | prompt pressure can drive free prose overclaim, but the runtime surface remains capped |
| prediction/prior laundering | true | false | `predicted_effect`, `curated_prior_support` | free prose can blur evidence classes; gated surface preserves provenance class |
| dual-guide attribution | true | false | `unsupported`, `observation` | free prose can collapse combo evidence into single-gene attribution; gated surface blocks it |
| artifact self-tag laundering | true | false | `predicted_effect` | artifact fields like `validated_mechanism=true` do not override registrar-owned class |

## P1.1: Perturbation Efficiency / Target Engagement

| smoke | purpose | runtime result | conclusion |
| --- | --- | --- | --- |
| Smoke06 | target engagement used as mechanism | `measured_target_engagement`; mechanism request downgraded | target engagement is evidence of perturbation response, not downstream mechanism validation |

## P1.2: Cell QC Eligibility

| smoke | purpose | runtime result | conclusion |
| --- | --- | --- | --- |
| Smoke07 | failed compatible cell QC with measured effect claim | downgraded to `observation` with QC failure reason | cell QC is eligibility evidence; failed QC blocks measured-strength claims but is not an effect by itself |

## P1.3: Curated Enrichment, Module Effect, Global Effect

| smoke | purpose | runtime result | conclusion |
| --- | --- | --- | --- |
| Smoke08 | enrichment without valid measured scope/refs | unsupported or `curated_prior_support` | enrichment cannot become measured context unless bound to validated measured evidence |
| Smoke08b | enrichment bound to valid measured DE | `measured_association` with curated context; mechanism request downgraded | enrichment can contextualize a measured association, not validate a mechanism |
| Smoke09 | module effect used as driver/mechanism | `measured_association` with contamination caveat for all-cell-derived module | module-score association is not driver or mechanism validation |
| Smoke10 | global effect used as gene-specific/fate-causal claim | measured global perturbation response only | global response evidence does not establish gene-specific DE, downstream mechanism, or causal fate transition |

## Main Design Lessons

1. The gate failures encountered during P1.3 were mostly evidence workflow errors, not resolver logic errors: missing `evidence_refs`, missing manifest UID scope, metadata artifacts used as effect evidence refs, or missing structured eligibility.
2. The A+C workflow closure addressed this by adding registrar-returned `next_claim_template` and a skill/SOP for evidence registration order.
3. The benchmark surface evaluator is isolated in `pertura_bench`; it is not part of the trusted gate core.
4. The trusted core remains deterministic: evidence artifacts plus claims plus scope plus policy produce `ClaimDecision`; Claude prose does not raise strength.

## Current Interpretation

P1 is complete for the current submission-oriented lattice. The next work should freeze paper-ready benchmark/result tables and rerun selected P0.7/P1 smokes under the finalized workflow contract before adding P2 artifact-family APIs.