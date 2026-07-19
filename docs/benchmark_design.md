# PerturaBench 0.2.0a19 evaluation protocol

PerturaBench has two independent objectives:

1. **Scientific capability evaluation** asks whether a capability executes the intended method, consumes declared inputs, and agrees with an external reference or reports the relevant continuous metrics.
2. **Agent workflow evaluation** asks whether an LLM selects and composes analyses correctly, handles missing information, and avoids unsupported scientific claims.

Synthetic fixtures test code and protocol behavior. They never validate a method on real data, issue a trusted receipt, or promote an exploratory capability.

The primary mechanism claim is that scientific authority is conditional on resolved, provenance-backed design identity and verified, scope-compatible, dependency-complete evidence. Unresolved design facts produce a checkpointed clarification or fail closed; user confirmation may resolve identity but cannot create scientific evidence. The comparative benchmark is conditional on a frozen, provenance-backed design contract and does not test autonomous statistical-design discovery.

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
Frozen references are evaluated outside the capability runner. Reference provenance is bound to a packaged independent generator catalog: direct edgeR, SCEPTRE and Propeller R harnesses, a Seurat Mixscape harness, or explicit curated/expert provenance. A reference without a known generator/provenance record is invalid. The metric catalog may declare `table_numeric`, `classification`, `rank_concordance`, `posterior_calibration`, `cluster_agreement`, `null_calibration`, or `effect_error` evaluators; they align rows by explicit keys, verify observed and reference SHA-256 identities, reject duplicate or missing keys, and compare persistent product artifacts. Runner-reported scalar metrics remain `reported_only` telemetry and cannot substitute for an artifact-level reference.

`real_benchmark_complete=true` (with `real_benchmark_ready` retained as a compatibility alias) means every primary run has a current, hash-bound terminal verdict, including disclosed failures. `candidate_validation_passed=true` is the separate performance target: execution hard gates pass and frozen scientific comparisons pass. This separation prevents completed failures from disappearing while preventing completion from being advertised as validation.

The artifact-aware frozen run policy schedules 39 scientific jobs rather than a capability-by-tier cross product. Full-dataset jobs are evaluation-only; calibration is restricted to declared frozen-subset jobs. Papalexi target-level evaluation uses a frozen batch within the target efficacy capability instead of rerunning its upstream DAG per target. Capabilities without a scientifically compatible artifact in the four-dataset study are explicitly excluded, not counted as passed.

### Dataset coverage

| Dataset | Required evaluation |
|---|---|
| Replogle K562 CRISPRi | intake/count integrity; perturbation-label and non-targeting-control coverage; target-reliability applicability limits. The frozen processed artifact has no cell-by-guide count matrix, so guide assignment, ambient-guide estimation, and MOI are not scored from it. |
| Papalexi THP-1 ECCITE | raw GDO guide integrity/assignment from a separately hash-bound auxiliary export; retained-cell QC; control-only state reference; cross-seed stability; mapping rejection; Mixscape responder/escape concordance; candidate target-reliability classification. |
| Norman K562 CRISPRa | preservation and correct interpretation of predefined dual-sgRNA constructs; explicit rejection of an inapplicable random high-MOI/SCEPTRE route. P5 virtual-model metrics are optional and require a separately frozen prediction bundle, split, and reference. |
| Kang 8-vs-8 PBMC | direct-R edgeR numerical agreement; Propeller proportion/effect/FDR agreement; paired-donor, replicate, and confounding hard gates. Kang is an external statistical reference, is not represented as Perturb-seq, and is not given fabricated guide/MOI facts. |

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

There are 18 primary Perturb-seq tasks and two supplemental Kang tasks. Each condition is repeated twice, producing 120 required scored turns inside 24 workflow sessions. The optional prediction task does not affect required gates. Every workflow/condition/repeat receives a fresh project, analysis run, conversation, provider session, authority namespace, and output directory; turns within that workflow share the session.

Evaluation is reported in frozen domains. Nine primary tasks with only a protocol hard gate are `protocol_claim_compliance`; the remaining nine primary tasks are `scientific_fidelity`. The two Kang tasks are `supplemental_scientific_fidelity`, and the optional virtual task is `optional_prediction_protocol`. Protocol compliance is not relabeled or pooled as scientific fidelity.

Task-scoped allowed values for `benchmark_result.analysis_unit` are an
answer-free controlled vocabulary in the provider-visible output contract.
Checkpoint validation requires this vocabulary to match the post-provider
evaluator binding exactly. Free-text regular expressions remain scoring-only
lexical compliance diagnostics and are never exposed as provider keywords. For
scientific and supplemental hybrid tasks, lexical compliance is reported but
does not override artifact fidelity or the structured protocol gate. For tasks
whose declared domain is protocol/claim compliance, lexical matching remains a
domain-specific heuristic and is reported with its semantic-equivalence
limitation.

All conditions receive the same frozen `codeact_protocol`, input paths/hashes, split, output contract, hard gates, claim ceiling, and resource budget. Only `pertura_full` receives registered DatasetContract/asset identities, answer-free static capability-contract subsets, five Pertura tools, and its frozen 1-3 task skills. No formal run compiles a Planner active window, CodeAct handoff, or CompletionGuard.

The shared output contract publishes answer-independent artifact semantics:
row-universe source roles, key columns, exactly-once rules,
finite/probability constraints, controlled labels, and legal encodings for
untested rows. It never publishes reference values, tolerances, thresholds, or
expected labels. Checkpoint construction fails unless every observed output
and evaluator key for the 11 scientific tasks has a matching public contract.

The frozen design catalog is registered as a provenance-backed partial
DatasetContract rather than regenerated by shallow H5AD inspection. Every
condition sees the same representation, column bindings, confirmed protocol
facts, asset availability, and unresolved facts. `pertura_full` alone sees the
contract ID/hash, provenance identities, registered asset IDs, and static
capability contracts. Design-audit tasks audit this partial contract; they do
not imply that every design fact was already resolved.

The runner initializes a neutral blocked `benchmark_result.json` before every
turn and records its hash. The provider must atomically submit a typed task
bundle containing both the scientific result and TurnDraft. An accepted receipt
whose hashes match those files defines `provider_scientific_completion`.
`provider_clean_termination` and `termination_reason` are recorded separately:
a later max-turn or timeout affects efficiency reporting but does not erase an
already accepted result. Without an accepted receipt, unchanged, deleted, or
invalid JSON fails closed. The runner never fabricates scientific content from
task metadata, artifacts, or TurnDraft.

The frozen provider budget is 64 turns per task. WF-REPL receives 48 GB and
WF-PAPA, WF-NORM, and WF-KANG receive 32 GB; every condition within one workflow
receives the same allocation, one CPU, and one BLAS/OpenMP thread. Resource
evidence reads the actual Slurm allocation. Correctly allocated agent-caused
OOM is a scored resource failure; preemption or node failure is invalid
infrastructure.

Before canaries, checkpoint refresh runs all 11 scientific evaluators against
hash-bound positive controls and task-appropriate structural, numerical,
analysis-unit, cells-as-replicates, and overclaim negatives. The resulting
`pertura-evaluator-qualification-v1` manifest binds the commit, wheel,
environment, reference, artifact, and verdict hashes. A failed qualification
blocks canaries and cannot be counted as model performance.

### Hard gates

For `pertura_full`, hard gates check:

- actual tool and capability DAG;
- parameter and asset-role validity;
- dependency, scope, stale, and trust behavior;
- `needs_input` and resume behavior;
- absence of silent fallback;
- `TurnDraft` schema and result/report references;
- candidate, prediction, prior, and hypothesis claim ceilings.

Every condition must also submit the same provider-neutral `outputs/tasks/<task_id>/benchmark_result.json`. Its schema, result type, analysis unit, required artifacts, and case-specific frozen scientific metrics are scored identically across conditions. Missing or merely well-formed output cannot pass without its configured reference comparison.

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

Release remains blocked on locked real datasets/subsets, frozen catalogs, current scientific verdicts, all 120 required comparative turns, expert-adjudicated CRISPRi/CRISPRa profiles, and required scientific environments.
