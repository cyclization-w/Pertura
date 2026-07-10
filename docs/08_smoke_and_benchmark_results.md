# 08. Smoke and Benchmark Results

This file summarizes the key completed smoke/benchmark evidence for the current P0.6/P0.7/P1/P2 stage implementation.

## Test Suite

Latest full-suite status recorded after the virtual perturbation wrapper family implementation:

```text
192 passed
```

## P0.6 Smoke Set

### Smoke01: Measured Association with Eligibility

Result: KLF1 measured DE plus design manifest, experiment design, guide assignment, target QC, and cell QC reached `measured_association`, while mechanism request was downgraded.

Key invariant:

```text
measured DE + validated EligibilityProfile + exact UID scope
-> measured_association
-> no validated mechanism
```

### Smoke02: Prediction / Curated Prior Laundering

Prediction requested as measured stayed `predicted_effect`. Curated prior requested as validated mechanism stayed `curated_prior_support`.

### Smoke03: Prose-Only Eligibility Trap

DE-like artifact with prose-only eligibility downgraded to `observation`. Prose such as "guide assignment passed" did not create measured eligibility.

### Smoke04: Dual-Guide Attribution Trap

Combinatorial guide evidence did not support a single-gene mechanism claim. Combinatorial identity stayed observation/unsupported depending on scope and artifact support.

### Smoke05: Policy Threshold Probe

Policy threshold changes produced different policy hashes and deterministic measured decision behavior.

## P0.7 Gate Utility

Strong-baseline summary:

```text
pressure_mechanism:
  baseline_overclaim=true
  gated_overclaim=false
  decision_strength=measured_association

prediction_prior_laundering:
  baseline_overclaim=true
  gated_overclaim=false
  decision_strength=predicted_effect, curated_prior_support

dual_guide_attribution:
  baseline_overclaim=true
  gated_overclaim=false
  decision_strength=unsupported, observation

artifact_self_tag_laundering:
  prediction self-tags ignored
  gated decision remains predicted_effect
```

P0.7 validates gate utility on scientific surface generation, not just resolver unit logic.

## P1.1 Smoke06: Target Engagement Is Not Mechanism

Target engagement artifact supported `measured_target_engagement` and downgraded a downstream mechanism request.

Key invariant:

```text
perturbation_efficiency
-> measured_target_engagement
-> not downstream mechanism
```

## P1.2 Smoke07: Failed Cell QC Blocks Measured Effect

Compatible failed cell QC downgraded a measured claim to `observation` and named the QC failure in decision reasons.

Key invariant:

```text
measured artifact exists
+ compatible failed cell QC
-> observation
```

## P1.3 Smoke08 / 08b: Curated Enrichment

Smoke08 negative paths:

- missing `evidence_refs` -> unsupported;
- scope not resolved through manifest UID -> curated prior only.

Smoke08b positive path:

```text
valid measured DE + exact UID scope + bound enrichment
-> measured_association with curated context
-> mechanism/validation request downgraded
```

## P1.3 Smoke09: Module Effect

Module effect reached measured module association but did not surface mechanism, driver, or master regulator language. All-cell-derived module produced a contamination caveat.

## P1.3 Smoke10: Global Effect

After A+C workflow closure and structured eligibility, global effect reached measured global perturbation response. The final surface explicitly did not establish gene-specific effect, downstream mechanism, or causal cell-state transition.

## Main Lessons from P1.3 Smokes

The failures were mostly not resolver errors. They were evidence workflow contract errors:

- missing `evidence_refs`;
- missing manifest UID scope;
- metadata artifacts mistakenly placed in effect claim refs;
- missing structured eligibility.

A+C closure addressed this by adding an evidence workflow SOP and registrar-returned `next_claim_template` for bookkeeping.


## P2 Core Freeze

Current frozen baseline before external wrappers:

```text
docs/results/p2_core_freeze_summary.md
docs/results/p2_core_freeze_summary.json
```

The freeze records the completed gate, runtime, workflow, stage, benchmark, and predicate/warrant layers. External wrappers such as CellOracle, scGPT, GEARS, or CPA must enter through structured artifacts and predicate-specific warrant rules, not direct prose.

## P2 Smoke13b: Composition Predicate/Warrant Closure

Smoke13b verifies the predicate/warrant architecture after the first `composition_effect` extension.

Frozen result path:

```text
docs/results/smoke13b_predicate_warrant/
```

Key invariant:

```text
composition_effect
-> EvidencePredicate.cell_state_composition_shift
-> measured cell-state composition association
-> not DE
-> not target engagement
-> not causal fate conversion
-> not downstream mechanism
-> not driver validation
```

Result:

```text
smoke13b_composition_as_fate_mechanism:
  decision=allowed_with_downgrade
  max_strength=measured_association
  blocked_requested_strength=causal_fate_conversion
  scope_fit=exact

smoke13b_composition_association:
  decision=allowed
  max_strength=measured_association
  scope_fit=exact
```

The controlled surface says composition association and explicitly blocks gene-specific effect, target engagement, causal fate conversion, downstream mechanism, and driver validation. It does not use DE wording.
