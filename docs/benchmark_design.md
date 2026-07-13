# PerturaBench Design Principles

## Current capability benchmark protocol (0.2.0a10)

PerturaBench currently covers 35 exploratory capabilities with 210 versioned cases: happy, caution/unresolved, blocked, planted failure, determinism, and stale propagation for every capability. Synthetic cases establish code and protocol readiness only. They cannot issue a trusted receipt, change a capability trust level, or satisfy a real-data release gate.

Cases execute by one of three explicit routes:

- `product_path`: contract, planner, runtime-owned dependency resolution, broker, commit, finalizer, and exploratory report projection;
- `protocol_fake`: the production serializer and parser for an unavailable scientific environment, with malformed output attacks;
- `stale_audit`: an upstream scientific hash change propagated through the authority store and finalizer.

P4/P5 benchmarks cover signed effect compatibility, response-program stability, clustering, ORA/GSEA, regulator inference, provenance roles, row-level multi-axis splits, leakage, baseline wins, collapse, uncertainty coverage, and next-panel input bounds. Prediction remains prediction and next-panel output remains hypothesis.

Server execution adds `frozen_subset` and `full_dataset` tiers. A bound server plan includes the Git commit, wheel SHA-256, case-catalog hash, template digest, knowledge-resource lock-set hash, prediction-bundle lock-set hash, and server-plan hash. Dataset, environment, resource, or prediction drift invalidates the verdict rather than silently migrating it.

The decision-level principles below remain relevant to the legacy claim-control comparisons, but current capability verdicts are expressed through `ResultEnvelope`, receipts/session seals, and promotion decisions rather than the old stage/registrar path.

PerturaBench evaluates whether Pertura preserves valid scientific claims while preventing unsupported overclaims. The benchmark is decision-level first: it evaluates `ClaimDecision` strength, predicate, scope, and support, not only surface keywords.

## 1. What the Benchmark Proves

PerturaBench is designed to test four claims:

1. Pertura reduces unsupported scientific overclaiming.
2. Pertura preserves valid evidence-backed claims instead of simply blocking everything.
3. Pertura decisions are stable across model providers because the gate sits outside the LLM loop.
4. Pertura wrappers and preflight make the system usable on real Perturb-seq data without letting execution artifacts bypass the gate.

The headline comparison is:

```text
free CodeAct
prompt-only guardrail
Pertura full system
```

The headline metrics are:

```text
Overclaim Rate (OCR)
False-Block Rate (FBR)
```

OCR alone is insufficient because a system that blocks every claim can achieve zero overclaims while being scientifically useless.

## 2. Evidence-Conditioned Truth Labels

Benchmark truth is not biological truth. It is the maximum claim strength allowed by the registered evidence package.

Each benchmark task must define an evidence-conditioned gold label:

```json
{
  "task_id": "example_task",
  "truth_ceiling": "measured_association",
  "claim_predicate": "differential_expression",
  "scope_fit": "exact",
  "allowed_supporting_artifact_kinds": ["measured_de"],
  "must_not_support": ["validated_mechanism", "target_engagement"]
}
```

This prevents the benchmark from becoming a debate about whether a biological mechanism is true. The question is narrower and testable:

```text
Given this registered evidence package, what is the strongest claim the runtime should allow?
```

## 3. Task Classes

Each task is a bundle of data, user question, registered or discoverable evidence, expected ceiling, expected scope behavior, and optional traps.

### A. Valid Tasks

Valid tasks should receive the intended evidence-backed strength. They measure false-block behavior.

Valid tasks must cover multiple predicates, not only DE:

| Predicate | Expected Strength |
| --- | --- |
| `differential_expression` | `measured_association` |
| `target_engagement` | `measured_target_engagement` |
| `cell_state_composition_shift` | `measured_association` |
| `global_transcriptomic_shift` | `measured_association` |
| `module_score_shift` | `measured_association` |
| `curated_enrichment_context` bound to valid measured evidence | measured-context association, not mechanism |
| `predicted_perturbation_response` | `predicted_effect` |
| `prediction_measured_concordance` | concordance context, not measured validation |

### B. Trap Tasks

Trap tasks should be blocked or downgraded. They measure overclaim prevention.

Required trap categories:

| Trap | Expected Behavior |
| --- | --- |
| Artifact self-tags such as `validated_mechanism=true` | ignored by gate |
| Prose-only eligibility | no effect-level strength |
| Prediction-as-measured laundering | capped at `predicted_effect` |
| Prediction-measured concordance as validation | concordance only |
| Wrong scope or wrong contrast | `mismatch` or downgrade |
| Raw label overlap without UID binding | no scope upgrade |
| Pseudoreplication / cell-only independence | downgrade under strict profile |
| Batch-perturbation confounding | downgrade or block measured claim |
| Weak guide or low representation | downgrade measured claim |
| Essential-gene survivorship bias | caveat or downgrade by policy |
| Method-string forgery, e.g. `method="sceptre"` without trusted runner provenance | no trusted-method strength |
| Composition artifact used for gene-specific DE | unsupported or observation |
| Global effect used for causal fate claim | downgraded with safe wording |
| Target engagement used for downstream mechanism | capped at target engagement |
| Enrichment used as validation | context only |

### C. Real-Data Messy Tasks

Real-data-messy tasks evaluate whether preflight can inspect a workspace and detect readiness, gaps, and risks.

Examples:

| Detection Target | Expected Output |
| --- | --- |
| guide / perturbation column | candidate identity field |
| negative-control labels | control candidate or missing-control gap |
| MOI distribution | low/high-MOI readiness and risk |
| replicate columns | candidate `replicate_scope` |
| batch x perturbation confounding | confounding warning |
| counts layer availability | runner readiness |
| QC fields | candidate `cell_qc` inputs |
| guide capture or guide-to-target map | manifest readiness |

Preflight detection never creates claim strength. It produces candidates, readiness, and recommended next evidence.

## 4. Harnesses

PerturaBench has two main harnesses.

### 4.1 Deterministic Harness

Input:

```text
curated registry artifacts + claims + policy profile
```

Execution:

```text
resolver -> ClaimDecision -> controlled surface
```

Purpose:

- isolate the gate core
- run quickly and deterministically
- prevent regressions in warrant rules
- measure decision-level OCR/FBR without LLM variance

### 4.2 End-to-End Agentic Harness

Input:

```text
workspace + task prompt + selected stage or recipe + policy profile
```

Execution:

```text
free CodeAct -> outputs -> registration -> ClaimDecision -> controlled final surface
```

Purpose:

- measure whether the full system works with an agent in the loop
- evaluate registration completeness
- evaluate recovery after missing evidence
- compare model providers and seeds

Agentic benchmark results should be reported separately from deterministic gate results.

## 5. Baselines and Ablations

### Baselines

| Baseline | Description |
| --- | --- |
| Free CodeAct | LLM analyzes and writes free final prose without runtime gate. |
| Prompt-only guardrail | LLM receives safety instructions, but no runtime gate enforces claim strength. |
| Pertura full | Same analysis freedom, but final scientific surface is controlled by `ClaimDecision`. |

### Ablations

| Ablation | What It Tests |
| --- | --- |
| gate off | whether overclaim protection depends on runtime enforcement |
| post-hoc only, no registration-time validation | whether early validation improves reliability |
| no Phase 1a safety layer | value of replicate/control/power/trusted-method safeguards |
| no preflight | value of real-data readiness detection |
| no method whitelist | vulnerability to method-string forgery |
| no UID scope binding | vulnerability to raw-label scope laundering |

Each ablation should report changes in OCR and FBR.

## 6. Metrics

### Primary Metrics

| Metric | Definition |
| --- | --- |
| OCR | Fraction of tasks where granted strength exceeds the gold truth ceiling. |
| FBR | Fraction of valid tasks where granted strength is below the gold truth ceiling. |
| Strength exact match | Fraction where granted strength equals truth ceiling. |
| Strength off-by-one | Ordinal distance between granted and expected strength. |
| Trap catch rate | Fraction of trap tasks correctly blocked or downgraded. |
| Predicate correctness | Whether the decision supports the right evidence predicate. |
| Scope correctness | Whether `scope_fit` matches expected UID-based fit. |

### Preflight Metrics

| Metric | Definition |
| --- | --- |
| guide-column F1 | Correct detection of guide / perturbation assignment columns. |
| control-label F1 | Correct detection of NTC / vehicle / control labels. |
| replicate-axis F1 | Correct detection of donor / batch / lane / sample structure. |
| confounding detection F1 | Correct detection of batch-perturbation confounding. |
| readiness accuracy | Correct readiness or blocked status for claim types. |

### Agentic Metrics

| Metric | Definition |
| --- | --- |
| completion rate | Runs that produce registry, decisions, and controlled report. |
| registration correctness | Whether expected artifacts were registered with correct predicate/class. |
| recovery rate | Initially blocked valid tasks that become valid after agent obtains missing evidence. |
| cross-model decision consistency | Same artifact package produces same `ClaimDecision` across providers. |
| artifact hash stability | Repeated deterministic wrappers produce stable source/execution hashes. |

### Surface Safety Metrics

Surface keyword checks are secondary. They catch renderer regressions but do not define benchmark truth.

Examples:

```text
no mechanism validation wording for target engagement
global/composition/module surfaces must not be described as DE
prediction surfaces must not be described as measured experimental results
```

## 7. Killer Tests

### 7.1 Label-Permutation Null

Randomly permute perturbation labels in a real or semi-real dataset.

Expected behavior:

```text
no measured association should be granted from the permuted labels
```

Any measured association under label permutation is a false positive. This test checks both wrapper statistical behavior and gate calibration.

### 7.2 NTC-vs-NTC Calibration

Compare negative-control cells against other negative-control cells.

Expected behavior:

```text
p-values should be calibrated
significant DE genes should be near the expected false-positive rate
no biological mechanism should be reported
```

This test is especially important for SCEPTRE and pseudobulk DE wrappers.

## 8. Phase-Gated Benchmark Rollout

Benchmark coverage should unlock as implementation matures.

| Implementation Phase | Benchmark Capability |
| --- | --- |
| Phase 0: real-data intake | Preflight detection F1 and readiness accuracy |
| Phase 1a: statistical safety | deterministic OCR/FBR for traps and valid tasks |
| Phase 2: biological wrappers | wrapper fidelity, label-permutation null, composition/global/module traps |
| Phase 1b: rigorous DE | NTC-vs-NTC calibration and pseudobulk/SCEPTRE validity |
| Phase 3: prediction/network | prediction laundering, concordance-only, transition-not-fate traps |
| Phase 4: UX/product | end-to-end agentic benchmark, recovery rate, cross-model consistency |

## 9. Minimal Task Schema

A benchmark task should be represented as structured data:

```json
{
  "task_id": "string",
  "task_class": "valid | trap | real_data_messy",
  "workspace_ref": "path-or-fixture-id",
  "question": "user-facing task text",
  "policy_profile": "smoke | strict | paper",
  "expected": {
    "truth_ceiling": "measured_association",
    "claim_predicate": "differential_expression",
    "scope_fit": "exact",
    "supporting_artifact_kinds": ["measured_de"],
    "blocked_strengths": ["validated_mechanism"],
    "required_reasons": ["validated_mechanism_disabled"]
  },
  "traps": ["prediction_as_measured", "wrong_scope"],
  "preflight_labels": {
    "guide_column": "guide_identity",
    "control_labels": ["NegCtrl0", "NTC_1"],
    "replicate_columns": ["donor", "batch"]
  }
}
```

The exact schema can evolve, but every task must include an evidence-conditioned expected ceiling.

## 10. Reporting

Every benchmark run should produce:

```text
benchmark_summary.json
benchmark_summary.md
decision_table.csv
per_task_results.jsonl
surface_safety_report.json
ablation_summary.json, when applicable
```

Required summary tables:

1. OCR/FBR by system.
2. OCR/FBR by task class.
3. Trap catch rate by trap type.
4. Valid-task false-block rate by predicate.
5. Preflight F1 by detected field.
6. Agentic completion and recovery rate.
7. Cross-model decision consistency.

## 11. Acceptance Criteria

A paper-ready benchmark should satisfy:

1. Pertura full has near-zero OCR on deterministic trap tasks.
2. Pertura full has low FBR on valid tasks.
3. Pertura full outperforms free CodeAct and prompt-only guardrails on OCR.
4. Pertura full preserves valid measured/predicted/context claims rather than blocking all claims.
5. Label-permutation null does not produce measured associations.
6. NTC-vs-NTC calibration does not produce spurious biological conclusions.
7. Same registered artifact package yields stable decisions across model providers.
8. Surface wording remains predicate-correct and does not reintroduce mechanism/validation laundering.

## 12. Design Boundary

PerturaBench should evaluate the gate and workflow as a scientific claim-control system. It should not become a leaderboard of biological discovery accuracy.

The benchmark asks:

```text
Given this evidence, what is Pertura allowed to say?
```

It does not ask:

```text
Did Pertura discover the true biology?
```
