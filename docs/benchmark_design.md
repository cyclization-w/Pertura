# PerturaBench 0.2.0a14 evaluation protocol

PerturaBench has two independent objectives:

1. **Scientific capability evaluation** asks whether a capability executes the intended method, consumes declared inputs, and agrees with an external reference or reports the relevant continuous metrics.
2. **Agent workflow evaluation** asks whether an LLM selects and composes analyses correctly, handles missing information, and avoids unsupported scientific claims.

Synthetic fixtures test code and protocol behavior. They never validate a method on real data, issue a trusted receipt, or promote an exploratory capability.

## 1. Frozen execution identity

Every formal server run is bound to:

- Git commit and wheel SHA-256;
- capability/spec and parameter-schema hashes;
- source, conversion, and subset locks;
- calibration/evaluation split hash;
- `design-confirmations.json`, `real-parameters.json`, and `metric-references.json` hashes;
- scientific environment lock hash;
- benchmark case catalog and server-plan hashes.

Changing any bound input invalidates the previous verdict. Absolute cache paths and timestamps are excluded from canonical identity.

## 2. Scientific capability verdicts

The v3 verdict separates execution success from scientific evaluation:

```text
outcome                    execution hard gate only
hard_gates                 schema, outputs, scope, dependencies, resources
scientific_metrics_status  passed | failed | reported_only | not_available
reference_hashes           frozen references used for comparison
continuous_metrics         method-specific numerical results
limitations                missing references and interpretive constraints
```

A completed process is not, by itself, scientific validation. Required outputs must exist and validate. When a frozen reference exists, comparison is mandatory. A missing reference is `not_available`, never passed. A continuous metric without a prespecified threshold is `reported_only`.
Frozen references are evaluated outside the capability runner. Reference provenance is bound to a packaged independent generator catalog: direct edgeR, SCEPTRE and Propeller R harnesses, a Seurat Mixscape harness, or explicit curated/expert provenance. A reference without a known generator/provenance record is invalid. The metric catalog may declare `table_numeric`, `classification`, or `rank_concordance` evaluators; they align rows by explicit keys, verify the reference SHA-256, reject duplicate or missing keys, and compare the published artifact before the temporary product workspace is removed. Runner-reported scalar metrics remain useful telemetry but cannot substitute for an available artifact-level reference.

`real_benchmark_complete=true` (with `real_benchmark_ready` retained as a compatibility alias) means every primary run has a current, hash-bound terminal verdict, including disclosed failures. `candidate_validation_passed=true` is the separate performance target: execution hard gates pass and frozen scientific comparisons pass. This separation prevents completed failures from disappearing while preventing completion from being advertised as validation.

The frozen run policy schedules 61 scientific jobs rather than the former capability-by-tier cross product. Full-dataset jobs are evaluation-only; calibration is restricted to declared frozen-subset jobs.

### Dataset coverage

| Dataset | Required evaluation |
|---|---|
| Replogle K562 CRISPRi | intake consistency; guide-map and assignment quality; MOI/multi-guide behavior; target-reliability classification against proxy and, later, expert labels |
| Papalexi THP-1 ECCITE | control-only state reference; cross-seed stability; mapping rejection; Mixscape responder/escape concordance with a frozen Seurat reference |
| Norman K562 CRISPRa | preservation of combinatorial guides; SCEPTRE null calibration; effect/rank concordance; virtual-model leakage, baseline, and evaluator metrics |
| Kang 8-vs-8 PBMC | edgeR numerical agreement with direct R; Propeller proportion/effect/FDR agreement; replicate and confounding hard gates; it is not represented as Perturb-seq |

### Local synthetic protocol

Thirty-five exploratory capabilities have six deterministic cases each:

```text
happy
caution_or_unresolved
blocked
planted_failure
determinism
stale_propagation
```

The 210 cases exercise the product path or an explicit protocol fake. Determinism compares a `ScientificResultDigest`, excluding timestamps, absolute paths, and run IDs.

## 3. External real-data configuration

Real column names and design facts are not hard-coded into the package. After backed schema inspection, maintainers freeze three catalogs:

- `design-confirmations.json`: confirmed MOI, guide design, control, replicate/donor, batch, state, dose/time, and provenance;
- `real-parameters.json`: dataset-to-capability parameter and asset bindings;
- `metric-references.json`: reference artifacts, columns, comparison rules, and thresholds.

Server commands accept:

```text
--design-confirmations
--parameter-catalog
--metric-reference-catalog
```

If a mapping is absent, the job is `not_configured`; the runtime must not guess a column. Calibration and evaluation identities must be disjoint.

## 4. Agent workflow comparison

The controlled comparison uses the same dataset split, objective, Claude model, context budget, wall-time limit, CPU, memory, and scientific environments under three conditions. Formal runs must record scheduler/cgroup enforcement of the declared memory and single-job budget; a budget written only in a prompt does not pass:

| Condition | Available assistance |
|---|---|
| `pertura_full` | bundled Perturb-seq skills, five Pertura tools, dependency resolution, receipts/promotion, and report rendering |
| `prompt_only` | static analysis and safety guidance, CodeAct, no Pertura domain tools or runtime claim enforcement |
| `free_codeact` | task and data plus CodeAct only; no Pertura skills or gate |

There are six primary Perturb-seq agent cases. Each condition is repeated twice, producing 36 primary runs. The two Kang agent cases remain supplemental statistical demonstrations because Kang is not Perturb-seq. Every run receives a fresh project, analysis run, conversation, provider session, authority namespace, and output directory.

### Hard gates

For `pertura_full`, hard gates check:

- actual tool and capability DAG;
- parameter and asset-role validity;
- dependency, scope, stale, and trust behavior;
- `needs_input` and resume behavior;
- absence of silent fallback;
- `TurnDraft` schema and result/report references;
- candidate, prediction, prior, and hypothesis claim ceilings.

Every condition must also emit the same provider-neutral `outputs/benchmark_result.json`. Its schema, result type, analysis unit, required artifacts, and case-specific frozen scientific metrics are scored identically across conditions. Missing or merely well-formed output cannot pass without its configured reference comparison.

Baseline conditions are not failed for lacking Pertura tools. Their hard gates instead check analysis artifacts, statistical unit, output completeness, and overclaim. Their provider findings are preserved with an `unscored_provider_claim` ceiling for evaluation rather than being rewritten by Pertura's claim renderer. This makes overclaim observable while granting the baseline no Pertura authority.

### Narrative score

`deepseek-v4-pro` is the fixed narrative judge. It scores scientific completeness, clarity, limitations/uncertainty, and actionability from 0 to 4. The average must be at least 3.0 and no dimension may be below 2. Judge unavailability is `judge_unavailable`; there is no fallback. Regrading never mutates an execution workspace.

Any strong overclaim, prediction-to-measurement conversion, or cell-as-replicate analysis is an automatic failure. All failed cases and at least 20% of passed cases receive human review.

## 5. Authority and interpretation boundary

Pertura has one active authority spine:

```text
ResultEnvelope
-> pertura_core.promotion
-> TurnFinal / versioned report
```

`phase` is presentation metadata only. Dependency legality is determined by `depends_on`, acyclicity, and explicit `dependency_policy` entries describing scope, usage, and accepted statuses. Runtime-resolved dependency hashes—not caller assertions—are authoritative.

Exploratory results remain `validated_untrusted`. Synthetic verdicts, narrative scores, published-proxy labels, and reported-only metrics cannot change capability trust.

## 6. Formal readiness

The pre-benchmark checkpoint is expected to report:

```text
repository_ready: true
runtime_spine_ready: true
dependency_policy_ready: true
sparse_execution_ready: true
local_fixture_ready: true
local_agent_protocol_ready: true
real_benchmark_ready: false
real_agent_behavior_ready: false
release_ready: false
```

Release remains blocked on locked real datasets/subsets, frozen catalogs, current scientific verdicts, 36 primary comparative agent runs, expert-adjudicated CRISPRi/CRISPRa profiles, and required scientific environments.