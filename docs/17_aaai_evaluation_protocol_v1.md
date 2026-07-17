# Pertura AAAI Evaluation Protocol v1

Status: final pre-benchmark protocol for `0.2.0a19`.

This protocol evaluates two distinct questions:

1. Can Pertura capabilities produce technically correct and reproducible Perturb-seq analyses?
2. Does the complete agent workflow reduce scientific overclaim while retaining useful analysis coverage?

Synthetic fixtures validate code paths only. They cannot establish real-data validity or promote an exploratory capability to trusted status.

The mechanism claim is deliberately bounded:

> Pertura makes scientific authority conditional on resolved, provenance-backed design identity and verified, scope-compatible, dependency-complete evidence. Unresolved design facts trigger a checkpointed clarification or fail closed; user confirmation may resolve identity but cannot create scientific evidence.

The main benchmark is conditional on a frozen, provenance-backed design contract. It evaluates execution completeness, method applicability, evidence traceability, and claim calibration. It does not claim that the system autonomously derives an optimal statistical design from arbitrary raw data, that the LLM always asks the optimal clarification question, or that results generalize beyond the frozen data, tasks, model, provider, and resource budget.

The formal a19 path is:

```text
frozen task/assets/confirmed protocol
-> condition-specific tools, static contracts, and skills
-> Claude SDK execution
-> provider artifacts plus TurnDraft
-> runner structural validation
-> independent evaluator
-> Pertura promotion and narrative evaluation
```

The runner does not compile an execution brief, CodeAct handoff, active-window state, or CompletionGuard. The shared `codeact_protocol` is a precommitted curator/user-confirmed design contract and contains no reference value, grader, metric threshold, expected result, or evaluation truth.

## 1. Frozen study boundary

The primary study uses four datasets:

- Replogle K562 CRISPRi Perturb-seq;
- Papalexi THP-1 ECCITE-seq;
- Norman K562 CRISPRa single and combinatorial perturbations;
- Kang 8-vs-8 PBMC, used only as an independent-replicate statistical reference and explicitly not described as Perturb-seq.

The evaluated software artifact is one Git commit and one wheel. Every run binds the commit, wheel hash, capability/spec hash, parameter-schema hash, skill-bundle hash, case-catalog hash, data lock chain, environment lock, and condition configuration.

No new capability, prompt rule, threshold, or scoring criterion may be introduced after evaluation begins. Benchmark-discovered bugs may be fixed only by creating a new checkpoint and rerunning every affected condition.

## 2. Data and split discipline

Each dataset follows:

```text
source manifest
-> checksum-verified source artifact
-> deterministic conversion lock
-> deterministic subset lock
-> disjoint calibration/evaluation split
-> DataAsset registration
-> benchmark execution
```

Absolute cache paths are local sidecars and never part of canonical identity. Calibration and evaluation subset locks additionally bind ordered cell-identity manifests; overlapping cell identities fail before execution. Dataset licenses remain `required` until an identified human reviewer records the review basis; fetch/conversion code cannot mark a license reviewed automatically. Calibration data may be used for parameter/profile development. Evaluation data must not be used to select thresholds, prompts, methods, references, or scoring rules.

Before execution, freeze three external catalogs:

- `design-confirmations.json`: confirmed MOI, guide design, controls, guide-target map, replicate/donor/batch fields, and provenance;
- `real-parameters.json`: dataset-specific column and asset mappings for each capability;
- `metric-references.json`: reference artifacts, metrics, directions, thresholds where prespecified, and provenance.

All three catalog hashes enter the server plan and every real-data verdict. Missing mappings produce `not_configured`; the runtime must not guess column names.

## 3. Scientific capability benchmark

Scientific capability evaluation is separate from agent workflow evaluation. It uses the product runtime, registered assets, normal dependency resolution, broker execution, result validation, and final verdict generation.

A real-data capability verdict contains:

- execution hard gates;
- required output/schema checks;
- scientific metrics status;
- reference hashes;
- continuous metrics;
- input, runner, spec, policy, and environment hashes;
- limitations.

The artifact-aware frozen policy expands to 39 planned scientific jobs. Full-dataset jobs are evaluation-only; only prespecified frozen-subset jobs use calibration data. Papalexi target efficacy evaluates a frozen batch of target definitions inside each capability run so that the expensive upstream DAG is not rerun once per target. Capabilities without a scientifically compatible artifact in this four-dataset study are explicitly excluded rather than silently treated as validated. Execution success alone is not scientific validation. A required missing reference is `not_available`, not passed. A metric without a prespecified threshold is `reported_only`, not validated.

### 3.1 Replogle

Evaluate:

- intake counts, features, barcodes, and layer consistency;
- perturbation-label, target, and non-targeting-control coverage;
- preservation of `sgID_AB` construct identity without treating it as a measured guide-count matrix;
- target-reliability applicability and explicit blocking of unsupported guide-assignment, ambient, MOI, and replicate-aware claims.

The current processed artifact cannot support guide-assignment calibration or aggregate target-reliability scoring. Published-proxy labels remain non-production evidence and may be used only in a later artifact version that contains the required guide-level inputs.

### 3.2 Papalexi

Evaluate:

- guide-map and RNA/GDO barcode integrity from the checksum-bound auxiliary GDO export;
- negative-binomial guide assignment, posterior diagnostics, retained-cell QC, and explicit unresolved ambient status when raw droplets are absent;
- control-only reference-state construction;
- cross-seed clustering stability;
- frozen-reference mapping and low-confidence rejection;
- responder/escape classification;
- candidate guide efficacy and aggregate target reliability;
- concordance with a frozen Seurat Mixscape reference.

Reference modules or labels that touched evaluation perturbation labels must be flagged as leakage and cannot independently confirm a finding.

### 3.3 Norman

Evaluate:

- preservation of predefined single and combinatorial dual-sgRNA construct labels;
- absence of multi-guide-as-doublet errors;
- correct refusal to reinterpret the artifact as random high-MOI guide exposure or run SCEPTRE without a cell-by-guide count matrix;
- correct fail-closed SCEPTRE suitability/refusal when cell-by-guide counts are absent.

Virtual split, leakage, baseline, rank, uncertainty, and next-panel metrics are
supplemental and run only when a prediction bundle, split, and reference are
independently frozen. Their absence is `not_configured` and does not block the
primary comparison.

### 3.4 Kang

Evaluate:

- edgeR result equivalence to the direct R reference;
- Propeller proportion/effect/FDR equivalence to the direct R reference;
- replicate-unit and confounding hard gates;
- numerical error, design matrix, contrast, and pseudobulk/sample manifest agreement.

Kang results support statistical implementation validation only.

### 3.5 Reporting

Report metrics per dataset and capability, including failures. Do not replace them with one pooled score. At minimum report numerical error, precision/recall/F1 where labels exist, calibration/rejection, ARI/stability, type-I error/FDR/power, effect/rank concordance, planted-failure detection, wall time, and peak memory.

## 4. Primary agent workflow comparison

### 4.1 Conditions

The primary comparison has exactly three conditions:

| Condition | Tools and runtime | Skills/instructions | Purpose |
|---|---|---|---|
| `pertura_full` | five Pertura tools plus free CodeAct; runtime dependency, receipt, promotion, and report enforcement | bundled Pertura skills | complete system |
| `prompt_only` | no Pertura domain tools or runtime claim enforcement; free CodeAct and the same scientific environments | static Perturb-seq analysis and safety instructions | tests how far prompting alone can reproduce the behavior |
| `free_codeact` | no Pertura domain tools, skills, or gate; free CodeAct and the same scientific environments | objective and data description only | unconstrained end-to-end baseline |

The comparison is a system comparison. Differences must not be attributed solely to one component such as promotion.

### 4.2 Fairness constraints

Across conditions freeze. The harness records scheduler/cgroup enforcement of memory and one scientific job; a prompt-only declaration is insufficient:

- the same Claude model and version;
- the same dataset split and task objective;
- the same context, output-token, turn, wall-time, CPU, and memory budgets;
- the same filesystem and network policy;
- the same installed Python/R scientific environments;
- the same infrastructure retry rule;
- fresh project, run, conversation, provider session, authority namespace, and output directory for every execution.

Condition-specific tool exposure and instructions are published verbatim. Baselines may inspect files, write code, and use Bash/Python/R. Baselines are scored using condition-appropriate gates and do not fail merely because they lack Pertura tools or receipts.

### 4.3 Cases and repetitions

Use 18 frozen primary Perturb-seq tasks: four Replogle, eight Papalexi, and six Norman tasks. Two Kang paired-donor tasks are supplemental statistical demonstrations; Kang is not Perturb-seq and is never assigned a guide design or MOI. `VIRT-01` is optional and does not affect the required release gate.

The claim surface is reported in three strata:

1. design adequacy and method applicability (`REPL-01`, `REPL-03`, `NORM-01`, `NORM-03`);
2. contract-conditioned scientific execution, including `PAPA-06`, `KANG-01`, and `KANG-02`;
3. claim authority and scope enforcement across split discipline, frozen-reference use, dependency consumption, staleness, source classes, receipts, and promotion.

These mappings follow the frozen artifacts actually available to the benchmark. The processed Replogle artifact contains perturbation labels but not a cell-by-guide count matrix, so it cannot evaluate guide assignment or ambient-guide estimation. Norman contains predefined dual-sgRNA constructs rather than random high-MOI guide exposure, so the primary Norman task must not invoke SCEPTRE. Papalexi's raw GDO assay is exported as a separately hash-bound auxiliary asset bundle for guide-level tasks.

The server configuration must bind the following observed fields rather than guess from names:

| Dataset | Expression counts | Perturbation/control facts | Independent unit and state facts | Method boundary |
|---|---|---|---|---|
| Replogle | `X` (raw integer-like counts) | `gene`; control value `non-targeting`; `sgID_AB` is an observed construct label | `gem_group` is a technical capture group, not an independent biological replicate | No guide-count matrix is present; guide assignment, ambient estimation, MOI, and replicate-aware effects are out of scope for this artifact. |
| Papalexi | `X` (RNA counts); `data` is normalized | `guide_ID`; target `gene`; control value `NT`; confirmed low-MOI, single-guide design | `replicate` has three independent transductions; `orig.ident` is sequencing-lane/batch context | Guide tasks require the separately exported `GDO` MEX, `guide_map.tsv`, `rna_barcodes.tsv`, and `cell_metadata.tsv`. |
| Norman | `counts` layer (raw); `X` is normalized | `guide_identity`/`guide_merged`; control value `ctrl`; predefined combinatorial dual-sgRNA constructs | `gemgroup` is technical; no independent biological replicate is asserted | Do not confirm `design_moi=high`; do not route to SCEPTRE without cell-by-guide counts and a compatible exposure design. |
| Kang | `X` (raw counts) | condition `stim`, control value `ctrl`; no guide or target facts | donor `ind` (eight paired donors); state `cell`; `multiplets` is a cell-QC label | Not Perturb-seq. Use for paired design, Propeller, and direct-R edgeR reference tests only; never fabricate guide/MOI confirmations. |

Run each required task/condition twice inside a shared workflow session:

```text
20 required tasks x 3 conditions x 2 repetitions = 120 scored turns
4 workflows x 3 conditions x 2 repetitions = 24 agent sessions
```

Two repetitions are a deliberate time and cost compromise. They measure gross workflow stability, not a population distribution. A failed or timed-out run remains an outcome. One rerun is allowed only for a documented infrastructure failure that occurred before usable scientific output; both attempts remain logged.

### 4.4 Clarification boundary

The 120-turn experiment has no live human intervention. Its frozen design catalog represents a completed product interaction:

```text
unresolved design fact
-> checkpointed needs_input
-> explicit user confirmation
-> new DatasetContract version
-> dependent result invalidation and stale propagation
-> resume the same conversation/provider session
-> execution or continued block
```

Deterministic lifecycle, contract, promotion, and `NORM-03` tests establish this mechanism. The experiment does not evaluate the linguistic optimality of the clarification question. Confirmation can resolve identity, but it cannot create an effect, measurement, validation, or receipt.

## 5. Agent outputs and hard gates

Every run exports a provider-neutral envelope. All three conditions must produce the same condition-neutral scientific result schema so correctness can be compared independently of Pertura-specific receipts or tool IDs:

```text
input_manifest.json
events.jsonl
turn_finals/
authority_projection.json
reports/
execution_verdict.json
judge/grade.json
usage.json
benchmark_result.json
```

Before each task, the runner writes the same schema-valid neutral blocked checkpoint for all conditions. The provider must update it from actual execution evidence. An unchanged, deleted, or invalid result fails the `provider_result_updated` and/or schema gate; the runner never derives findings, analysis units, artifact roles, or completion from the task protocol, TurnDraft, filenames, or references.

For `pertura_full`, automatic hard gates include:

- actual tool and capability DAG;
- parameter schema and asset roles;
- dependency ID/hash/scope/status/trust and stale state;
- correct needs-input and resume behavior;
- no silent statistical fallback;
- TurnDraft/TurnFinal schema;
- report result-ID traceability;
- candidate, prediction, prior, hypothesis, stale, aborted, or legacy evidence not rendered as strong measured findings;
- no cell-as-replicate, prediction-as-measurement, or evaluation leakage.

For `prompt_only` and `free_codeact`, hard gates focus on:

- the shared `benchmark_result.json` schema and case-specific frozen reference metrics;
- required analysis artifacts and output completeness;
- correct statistical unit and method compatibility;
- explicit blockers rather than fabricated results;
- no silent fallback;
- no prediction-as-measurement or unsupported strong claim;
- adequate limitations and traceability to generated artifacts.

A baseline does not fail for absence of Pertura-specific IDs, receipts, or tool calls.

Infrastructure-invalid events are catalog/hash/environment/provider-auth/SDK-init failures, bundled-skill leakage into a baseline, Slurm OOM/preemption, and isolated provider network outages. They may be rerun at the same checkpoint. Task timeout, max-turns, provider cancellation caused by the task wall-time, omitted/invalid result submission, wrong artifacts, and independent scientific-evaluator failure are scored agent failures and are not rerun.

## 6. Primary endpoints

Report per case, repetition, and condition:

- unsupported definitive claim rate (UDCR);
- valid finding coverage;
- workflow completion;
- critical scientific error rate;
- required artifact completion;
- blocker/needs-input correctness;
- statistical-unit correctness;
- limitation coverage;
- wall time, tool calls, tokens, and estimated API cost.

Narrative quality is secondary and cannot override a hard-gate failure.

## 7. Narrative judging and human review

The fixed narrative judge is `deepseek-v4-pro`. Record provider, model, prompt, rubric, temperature, and hash in `JudgeManifest`. If unavailable, record `judge_unavailable`; do not fall back to another model.

Score 0-4 for scientific completeness, clarity, limitations/uncertainty, and actionability. The descriptive target is mean at least 3.0 with no dimension below 2, but judge scores cannot convert a failed execution into a pass.

Human reviewers inspect all failed runs and at least 20% of passed runs. Reviewers are blinded to condition where feasible. Disagreements on definitive claims are adjudicated. Do not expose evaluation references or rubrics to the agent.

## 8. Statistical analysis

The statistical unit is task x condition x repeat, nested within workflow session. Cells, genes, findings, and tool events are not independent experimental units, and 120 turns are not treated as 120 independent samples.

For each endpoint:

- publish every run-level outcome;
- compute paired condition differences within case and repetition;
- report workflow-clustered or hierarchical paired bootstrap confidence intervals where meaningful;
- use exact paired binary or paired permutation tests only as secondary analyses;
- report effect sizes and uncertainty regardless of p-values;
- do not describe stochastic repeats as biological replicates.

With two repetitions, emphasis is on gross workflow stability, effect direction, magnitude, failure modes, and transparent task-level results rather than full provider/model variance or strong population-level significance claims. Report effect size, pass rate, critical-error rate, and uncertainty rather than relying on one pooled p-value.

## 9. Success interpretation

Pertura supports its intended claim only if:

1. `pertura_full` reduces unsupported definitive claims and critical scientific errors relative to both baselines on the evaluated tasks;
2. valid finding coverage and workflow completion remain practically useful and are reported with paired uncertainty;
3. no Pertura strong finding is based on candidate, prediction, prior, hypothesis, stale result, invalid dependency, aborted session, or invalid receipt;
4. real scientific capability metrics and reference comparisons are reported independently of agent narrative quality;
5. all cell-as-replicate, prediction-as-measurement, silent fallback, and leakage failures are disclosed individually.

Failure to meet a criterion is a study result, not permission to alter the protocol after unblinding.

## 10. Execution order

```text
1. Freeze source/conversion/subset locks
2. Inspect schemas and freeze design, parameter, and reference catalogs
3. Run calibration-only capability jobs
4. Freeze commit, wheel, environments, skills, cases, metrics, and analysis script
5. Bind the evaluation manifest and server-plan hashes
6. Run real scientific capability evaluation
7. Run 24 workflow sessions containing 120 required scored turns
8. Seal and export all execution artifacts
9. Run automatic hard gates
10. Run the fixed narrative judge
11. Complete blinded human review and adjudication
12. Unblind conditions
13. Execute the frozen statistical analysis
14. Report results without silently replacing failures
```

## 11. Paper-ready checklist

- [ ] all four dataset lock chains are current;
- [ ] calibration and evaluation splits are disjoint;
- [ ] design, parameter, and metric-reference catalogs are frozen;
- [ ] required scientific reference verdicts and continuous metrics exist;
- [ ] all three conditions run through one condition-aware harness;
- [ ] all 120 required turns have a terminal verdict, including failures;
- [ ] judge-unavailable runs are not silently regraded by another model;
- [ ] all failed and at least 20% of passed runs have human review;
- [ ] the analysis script and endpoint definitions were frozen before unblinding;
- [ ] code, prompts, manifests, locks, environment descriptions, and case-level outputs are archived;
- [ ] paper claims are restricted to the evaluated datasets, tasks, model, and resource budget.

## 12. Out of scope for the primary study

Do not delay the primary benchmark for:

- additional providers or OpenAI runtime execution;
- new modalities or capabilities;
- production UX or security hardening;
- dashboard usability studies;
- more datasets or large repetition counts.

These require a declared protocol extension and cannot be silently folded into the frozen primary analysis.
