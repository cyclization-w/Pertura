# Pertura AAAI Evaluation Protocol v1

Status: frozen pre-benchmark protocol for `0.2.0a13`.

This protocol evaluates two distinct questions:

1. Can Pertura capabilities produce technically correct and reproducible Perturb-seq analyses?
2. Does the complete agent workflow reduce scientific overclaim while retaining useful analysis coverage?

Synthetic fixtures validate code paths only. They cannot establish real-data validity or promote an exploratory capability to trusted status.

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

The frozen policy expands to 61 planned scientific jobs. Full-dataset jobs are evaluation-only; only prespecified frozen-subset jobs use calibration data. Execution success alone is not scientific validation. A required missing reference is `not_available`, not passed. A metric without a prespecified threshold is `reported_only`, not validated.

### 3.1 Replogle

Evaluate:

- intake counts, features, barcodes, and layer consistency;
- guide-map integrity and assignment performance;
- posterior calibration, ambient guide flags, and multi-guide/MOI behavior;
- target efficacy and aggregate reliability;
- macro-F1, per-class recall, and false-block rate against published-proxy and, when available, expert-adjudicated labels.

Published-proxy labels remain non-production evidence.

### 3.2 Papalexi

Evaluate:

- control-only reference-state construction;
- cross-seed clustering stability;
- frozen-reference mapping and low-confidence rejection;
- responder/escape classification;
- concordance with a frozen Seurat Mixscape reference.

Reference modules or labels that touched evaluation perturbation labels must be flagged as leakage and cannot independently confirm a finding.

### 3.3 Norman

Evaluate:

- preservation of combinatorial and high-MOI cells;
- absence of multi-guide-as-doublet errors;
- SCEPTRE calibration and discovery execution;
- null calibration, type-I error, effect and rank concordance;
- virtual split leakage detection;
- baseline win rate, collapse, direction, rank, discriminability, and uncertainty metrics.

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

Use six frozen primary Perturb-seq cases:

1. Replogle guide/screen QC;
2. Replogle target reliability follow-up;
3. Papalexi state/Mixscape analysis;
4. Papalexi label confirmation and stale handling;
5. Norman high-MOI SCEPTRE analysis;
6. Norman virtual evaluation and next-panel reasoning;

Kang edgeR and Propeller agent tasks are reported separately as supplemental statistical demonstrations. They are not part of the primary agent comparison because Kang is not Perturb-seq.

Run each case/condition twice:

```text
6 cases x 3 conditions x 2 repetitions = 36 primary agent executions
```

Two repetitions are a deliberate time and cost compromise. They measure gross workflow stability, not a population distribution. A failed or timed-out run remains an outcome. One rerun is allowed only for a documented infrastructure failure that occurred before usable scientific output; both attempts remain logged.

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

The six primary task cases are the primary strata; cells, genes, findings, and tool events are not independent experimental units.

For each endpoint:

- publish every run-level outcome;
- compute paired condition differences within case and repetition;
- report case-stratified paired bootstrap confidence intervals where meaningful;
- use exact paired binary or paired permutation tests only as secondary analyses;
- report effect sizes and uncertainty regardless of p-values;
- do not describe stochastic repeats as biological replicates.

With six primary cases and two repetitions, emphasis is on effect direction, magnitude, failure modes, and transparent case-level results rather than strong population-level significance claims.

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
7. Run 36 primary agent executions
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
- [ ] all 36 primary executions have a terminal verdict, including failures;
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
