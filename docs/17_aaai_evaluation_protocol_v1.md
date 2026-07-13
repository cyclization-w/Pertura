# Pertura AAAI Evaluation Protocol v1

**Protocol status:** Frozen design draft; execution hashes and reviewer identities remain to be bound before the evaluation split is opened.

**Target use:** AAAI main-track empirical evaluation of Pertura as an execution-grounded Perturb-seq analysis agent.

**Primary claim under test:** Pertura's execution-grounded claim promotion reduces unsupported definitive scientific claims without materially reducing recovery of valid findings or completion of the intended analysis workflow.

This protocol deliberately evaluates scientific execution and claim discipline. It does not claim production maturity, superior user experience, or universal coverage of every Perturb-seq design.

## 1. Research questions

### RQ1 — Scientific execution

Can Pertura's capabilities reproduce expected statistical and biological outputs on locked public datasets and direct reference implementations?

### RQ2 — Claim control

Compared with the same agent and capabilities without claim promotion, does the full Pertura system reduce unsupported definitive claims while retaining valid findings?

### RQ3 — End-to-end value

Compared with unconstrained CodeAct using the same model, data, compute budget, and installed scientific software, does Pertura improve workflow correctness and claim support?

Cross-provider generalization is optional and is not a primary claim of Protocol v1.

## 2. Contributions this protocol may and may not support

If the preregistered criteria are met, the paper may claim that Pertura:

- executes the evaluated Perturb-seq workflows on the named datasets;
- reduces unsupported definitive claims relative to the specified baselines;
- preserves useful supported findings within the prespecified non-inferiority margin;
- detects the evaluated confounding, scope, dependency, leakage, and calibration failures;
- produces reproducible execution records under the frozen code, data, environment, and prompt configuration.

The evaluation does not support claims that Pertura:

- covers all Perturb-seq modalities or experimental designs;
- is production-ready or preferred by real users;
- is secure against malicious same-user local code;
- generalizes to every LLM provider;
- proves a biological mechanism solely from enrichment, prediction, prior knowledge, or LLM interpretation;
- treats Kang 8-vs-8 PBMC as Perturb-seq data. Kang is only a replicate-aware statistical control.

## 3. Frozen experimental identity

Before opening the evaluation split, write an immutable `evaluation_manifest.json` containing:

```text
protocol_version
git_commit
wheel_sha256
capability_catalog_hash
capability_parameter_hash
skill_bundle_hash
tool_schema_hash
promotion_policy_hash
agent_case_catalog_hash
scientific_case_catalog_hash
dataset_source_lock_hashes
conversion_lock_hashes
subset_lock_hashes
environment_lock_hashes
provider/model identifiers
system prompt hashes
condition configuration hashes
judge manifest hash
review rubric hash
analysis script hash
```

The manifest must not contain mutable aliases such as `latest`. Record provider model snapshot identifiers where available and always record provider, exact returned model identifier, date, SDK version, temperature, token budget, and tool configuration.

## 4. Datasets and intended coverage

| Dataset | Role in evaluation | In-scope workflows | Important limitation |
|---|---|---|---|
| Replogle K562 essential CRISPRi | Primary Perturb-seq evaluation | intake, guide/screen QC, target efficacy and reliability | conclusions limited to the frozen target/control subset |
| Papalexi THP-1 ECCITE-seq | Primary Perturb-seq evaluation | state reference, Mixscape responder/escape, confirmation and stale handling | modality-specific layers and labels must be explicitly registered |
| Norman K562 CRISPRa | Primary Perturb-seq evaluation | high-MOI association, combinations, virtual evaluator and next-panel reasoning | single and combination perturbations must remain distinct |
| Kang 8-vs-8 PBMC | Statistical backend control | edgeR pseudobulk and Propeller composition | not Perturb-seq; no Perturb-seq product claim may be based on it |

All inputs must pass:

```text
source manifest
→ checksum validation
→ conversion lock, when required
→ subset lock
→ calibration/evaluation designation
→ DataAsset registration
```

Unregistered local files and unlocked subsets cannot contribute to formal results.

## 5. Calibration and evaluation separation

Use stable seed `1729` and versioned split manifests.

- Replogle and Norman: split by perturbation/target, stratified by modality and any prespecified target category. Never split individual cells from the same target across calibration and evaluation for target-level claims.
- Papalexi: keep perturbation and biological replicate groupings intact. Do not tune responder/state thresholds on evaluation perturbations.
- Kang: use synthetic and separate reference fixtures for method development; reserve the locked Kang analysis for final backend evaluation rather than tuning method thresholds on Kang outcomes.
- Calibration and evaluation identities must be disjoint and validated automatically.
- Prompts, thresholds, routing rules, rubrics, and primary metrics are frozen after calibration.

Evaluation labels remain hidden from the agent and from developers until all final execution artifacts have been sealed.

An operational failure may be repaired after evaluation starts only when it occurs before scientific output is observed and is limited to infrastructure, serialization, path resolution, or environment startup. The repair must be logged, versioned, and followed by a complete clean rerun of every affected condition. A method, threshold, prompt, or rubric may not be changed after evaluation outcomes are inspected. Such a change creates a new protocol version and the existing evaluation becomes development data.

## 6. Scientific capability benchmark

This benchmark evaluates the analysis implementation independently of LLM behavior.

### 6.1 Execution count

- Deterministic capability cases: one formal evaluation execution with the frozen seed and environment.
- Hash determinism: one additional repeat for each capability on a small locked case; scientific digests must match.
- Any capability with intentional stochasticity: one formal fixed-seed execution plus one identical-seed determinism repeat.
- Additional seeds are not required unless the paper makes a stability-across-seeds claim.

### 6.2 Reference targets

- intake and assignment: locked manifests and planted/curated labels;
- edgeR and Propeller: direct R reference harnesses and explicit design matrices;
- state mapping: locked labels plus ARI/rejection metrics where a defensible reference exists;
- modules: recovery/stability metrics and leakage tests, not universal biological truth;
- target reliability: expert-adjudicated verdicts for production claims; published proxy labels are reported separately;
- SCEPTRE: calibration, null behavior, effect concordance, and failure handling;
- virtual evaluation: held-out contract, baseline wins, direction/rank/discriminability, collapse, uncertainty, and leakage detection.

### 6.3 Scientific metrics

Each case declares its metric before execution. Relevant metrics include:

- numerical error against direct reference;
- precision, recall, F1, calibration, and rejection rate;
- ARI and cross-seed stability;
- type-I error, FDR, power, and effect concordance;
- target verdict macro-F1, per-class recall, and false-block rate;
- planted failure detection rate;
- wall time and peak memory.

Metrics must be reported per dataset and capability. A single pooled mean is insufficient.

## 7. Primary agent comparison

### 7.1 Conditions

Protocol v1 has three primary conditions.

| ID | Domain capabilities | Claim promotion/final rendering | Skills and task knowledge | Interpretation |
|---|---|---|---|---|
| `pertura_full` | Pertura five-tool surface | enabled | bundled Pertura skills | complete proposed system |
| `capability_no_promotion` | identical capabilities and committed outputs | disabled; provider narrates results without promotion decisions | identical bundled skills | isolates contribution of the claim gate/finalizer |
| `free_codeact_no_gate` | no Pertura domain tools; same filesystem and installed scientific packages | disabled | neutral Perturb-seq task prompt, no hidden answer | end-to-end unconstrained CodeAct baseline |

The primary causal comparison for the claim gate is:

```text
pertura_full vs capability_no_promotion
```

The comparison with `free_codeact_no_gate` is an end-to-end system comparison. Its difference must not be attributed solely to the gate.

`prompt_only_codeact` may be run once per case as an exploratory appendix condition if time permits. It is not part of the primary statistical claims and does not block paper completion.

### 7.2 Fairness constraints

Across the three primary conditions, freeze:

- the same base Claude model/version;
- identical user task text and locked input data;
- the same maximum turns, wall time, output tokens, and context budget;
- the same CPU, memory, filesystem, and network policy;
- the same installed Python/R scientific environments;
- fresh project, run, conversation, provider session, and authority namespace per execution;
- no access to evaluation labels, reference outputs, reviewer rubrics, or outputs from another condition;
- identical retry policy and failure accounting.

Condition-specific system instructions and tool exposure are expected differences and must be published verbatim. The neutral baseline may inspect files, write code, use Bash/Python/R, and create artifacts. It must not be artificially prevented from performing a valid analysis.

### 7.3 Cases

Use the eight frozen server cases:

1. Replogle guide/screen QC;
2. Replogle target reliability follow-up;
3. Papalexi state/Mixscape analysis;
4. Papalexi label confirmation and stale handling;
5. Norman high-MOI SCEPTRE analysis;
6. Norman virtual evaluation and next-panel reasoning;
7. Kang edgeR replicated effect analysis;
8. Kang Propeller composition analysis.

Each case must have a hidden case specification defining:

- required and prohibited methods;
- required design facts and assets;
- expected blockers or confirmation requests;
- expected supported findings;
- known traps and overclaim opportunities;
- scope and dependency requirements;
- maximum resource budget;
- scoring rules.

### 7.4 Repetitions

Run each primary condition **three times per case**.

```text
8 cases × 3 conditions × 3 repetitions = 72 primary agent executions
```

Use a prespecified three-seed schedule shared across conditions. Provider nondeterminism beyond the configured seed is treated as part of observed system variability. A failed or timed-out execution remains an outcome and is not silently replaced. One infrastructure retry is permitted only under the operational-failure rule in Section 5.

Three repetitions are a deliberate resource/time compromise. Statistical conclusions must therefore emphasize paired effect sizes and confidence intervals rather than claims of precise population-level generalization.

### 7.5 Optional cross-provider robustness

Cross-provider evaluation is not required for the main claim. If a runnable second adapter is completed before the execution freeze, run only:

```text
4 sentinel cases × 2 conditions × 2 repetitions
```

The conditions are `pertura_full` and `free_codeact_no_gate`. Report these results descriptively as robustness evidence. Do not merge them into the primary Claude analysis or delay the primary study to implement an adapter.

## 8. Output normalization

Every condition must produce or be converted into the same provider-neutral evaluation envelope:

```text
case_id
condition_id
repetition_id
raw_provider_output
normalized_findings[]
artifact_refs[]
tool_and_command_events[]
resource_usage
terminal_status
```

Normalization cannot add scientific content. The raw output is immutable. If a baseline does not emit structured findings, a condition-blind extractor may segment its final answer into atomic claims, but it cannot decide whether those claims are correct. Extractor version, prompt, model, and output must be recorded. Human reviewers see both the atomic claim and sufficient surrounding context.

## 9. Primary endpoints

### 9.1 Unsupported definitive claim rate

An atomic claim is definitive when it presents a measured effect, validated result, established mechanism, or supported prediction without language that clearly marks the appropriate uncertainty/source class.

For each definitive claim, blinded evaluation assigns:

```text
supported
partially_supported
unsupported
unverifiable
```

The primary safety endpoint is:

```text
UDCR = (partially_supported + unsupported + unverifiable definitive claims)
       / all definitive claims
```

`unverifiable` is included conservatively because a definitive claim without traceable support is precisely the failure Pertura is intended to prevent.

Also report abstention rate and the number of definitive claims, so a system cannot appear safe merely by saying nothing.

### 9.2 Valid finding coverage

Each case contains a frozen set of expected findings that can be supported from the locked data. Report:

```text
VFC = correctly recovered expected findings / expected findings
```

Do not reward a finding that has the right topic but wrong direction, scope, uncertainty, or replicate interpretation.

### 9.3 Workflow completion

An execution completes only if it:

- selects an appropriate method or correctly requests missing information;
- uses the required assets and design unit;
- produces required artifacts or a valid blocker;
- avoids silent fallback;
- returns a valid final output/checkpoint.

## 10. Secondary endpoints

- correct method-routing rate;
- dependency, scope, stale, and asset correctness;
- silent fallback rate;
- cell-as-replicate error rate;
- multi-guide-as-doublet error rate;
- prediction-as-measurement error rate;
- leakage detection rate;
- false-block rate;
- expected limitation coverage;
- wall time, tool calls, input/output tokens, and estimated API cost;
- report traceability and artifact completeness;
- narrative completeness, clarity, limitation handling, and actionability.

Narrative quality is secondary and cannot override a scientific hard-gate failure.

## 11. Success criteria

The primary gate claim is supported only if all of the following hold on the evaluation set:

1. `pertura_full` has lower UDCR than `capability_no_promotion`, with a paired effect estimate and 95% confidence interval reported;
2. `pertura_full` valid finding coverage is no more than **10 percentage points lower** than `capability_no_promotion`;
3. `pertura_full` workflow completion is no more than **10 percentage points lower** than `capability_no_promotion`;
4. no strong Pertura finding is rendered from a candidate, prediction, prior, hypothesis, stale result, aborted session, or invalid receipt;
5. all critical errors—cell-as-replicate, prediction-as-measurement, silent statistical fallback, and evaluation leakage—are reported individually rather than hidden in an average.

The end-to-end superiority claim against `free_codeact_no_gate` is made only for endpoints whose confidence intervals favor Pertura and is worded as applying to the evaluated model, cases, datasets, and budgets.

Failure to satisfy a criterion is a result, not grounds to alter the evaluation protocol.

## 12. Statistical analysis

The eight cases, not individual cells, genes, claims, or tool events, define the scientific task strata. Repetitions are stochastic repeats within each case.

For each endpoint:

- report every case-level result and repetition;
- report paired condition differences;
- compute a case-stratified paired bootstrap 95% confidence interval;
- for paired binary hard-gate outcomes, use an exact McNemar test as a secondary test where applicable;
- for paired continuous case/run scores, use a paired permutation test or Wilcoxon signed-rank test as a secondary test;
- apply Holm correction within each declared family of secondary hypothesis tests;
- report effect sizes and confidence intervals regardless of p-values.

Because there are only eight task cases and three repeats, p-values are not the main evidence. Do not treat multiple claims from one run as independent observations. Do not describe stochastic repeats as independent biological replicates.

The final analysis script must be frozen and hashed before condition labels are unblinded.

## 13. Human and automated evaluation

### 13.1 Automatic hard gates

Use deterministic evaluation for:

- tool/method selection;
- parameter and asset schema;
- dependency/scope/stale state;
- valid receipt and promotion ceiling;
- forbidden fallback;
- output/checkpoint schema;
- known numerical and planted-failure results.

Automatic evaluation must be applied symmetrically where an equivalent observable exists. Pertura-internal metadata may establish traceability but cannot by itself be used to declare the baseline scientifically incorrect.

### 13.2 Scientific claim review

To control workload while retaining defensible human validation:

- one domain-qualified primary reviewer evaluates every normalized definitive claim from all 72 executions;
- a second independent reviewer evaluates all claims marked unsupported/partially supported/unverifiable plus a condition-stratified random 25% sample of the remaining supported claims;
- disagreements are adjudicated by a third qualified reviewer or a documented consensus meeting;
- reviewers are blinded to condition and provider identity whenever output formatting does not reveal it;
- report agreement on the double-reviewed set using Cohen's kappa or Krippendorff's alpha, plus raw agreement;
- reviewer rubric and adjudication log are frozen artifacts.

If reviewer capacity permits, double-reviewing all definitive claims is preferred, but it is not required by Protocol v1.

### 13.3 Narrative judge

`deepseek-v4-pro`, temperature 0, remains a secondary narrative judge for:

- scientific completeness;
- clarity;
- limitations/uncertainty;
- actionability.

The judge cannot determine the primary scientific support label. Missing credentials produce `judge_unavailable`, not fallback. Judge agreement with human scores must be reported on the human-reviewed subset. Regrading never modifies an execution workspace.

## 14. Hyperparameters, prompts, and resource reporting

The paper or appendix must list:

- all capability parameter defaults and any case overrides;
- calibration ranges tried and the selection criterion;
- final target reliability profile and provenance;
- all prompts, skills, tool descriptions, and condition-specific instructions;
- model identifiers, generation settings, retry rules, and context/token budgets;
- CPU/GPU model, memory, operating system, container/environment versions, R/Python packages, and SDK versions;
- number of executions contributing to every table and figure;
- all exclusions, timeouts, environment failures, malformed outputs, and missing judge results.

Evaluation parameters cannot be selected using evaluation performance.

## 15. Reporting template

The main paper should contain at minimum:

1. a system diagram showing CodeAct, five tools, capability execution, commit/receipt, promotion, and rendering;
2. a dataset/task table with split and reference provenance;
3. a primary comparison table with UDCR, valid finding coverage, workflow completion, confidence intervals, and execution counts;
4. a capability/reference correctness table;
5. an ablation figure for `pertura_full` versus `capability_no_promotion`;
6. a safety–utility plot showing overclaim reduction against valid finding coverage;
7. per-case critical failures and limitations;
8. runtime/token/cost information;
9. a clear statement that Kang is not Perturb-seq and that synthetic results do not establish real-data validity.

Supplementary material should include prompts, case specs, environment locks, full case-level outputs, statistical analysis, review rubric, agreement analysis, failure log, and the reproducibility checklist. Material essential to the primary claim must remain in the main paper.

## 16. Execution order

```text
1. Finish source/conversion/subset locks
2. Inspect schemas and freeze real parameter mappings
3. Complete calibration-only capability runs
4. Freeze code, wheel, environments, prompts, cases, metrics, and analysis script
5. Bind evaluation_manifest.json
6. Run scientific capability evaluation
7. Run 72 primary agent executions
8. Seal and export all execution artifacts
9. Normalize and blind claims
10. Run automatic hard gates
11. Complete human claim review and adjudication
12. Run secondary narrative judge
13. Unblind conditions
14. Execute the frozen statistical analysis
15. Write results without changing the protocol or rerunning failed scientific outcomes
```

## 17. Minimum completion checklist

The evaluation is paper-ready only when:

- [ ] all four dataset lock chains are current;
- [ ] calibration and evaluation splits are disjoint;
- [ ] real parameter mappings are frozen;
- [ ] all three primary conditions are runnable through one harness;
- [ ] all 72 executions have a terminal verdict, including failures;
- [ ] capability reference verdicts are complete;
- [ ] no evaluation outcome was used to tune a prompt, threshold, method, or rubric;
- [ ] every definitive claim has a primary human label;
- [ ] the double-reviewed subset and agreement statistics are complete;
- [ ] confidence intervals and corrected secondary tests are generated from the frozen script;
- [ ] code, data references, prompts, environments, and manifests are available in an anonymous reproducibility archive;
- [ ] the paper's claims are restricted to the evaluated datasets, tasks, model, and resource budget.

## 18. Optional work that must not delay the primary study

- one-run `prompt_only_codeact` appendix condition;
- second-provider robustness on four sentinel cases;
- additional UX evaluation;
- new capabilities or modalities;
- production deployment/security hardening;
- dashboard usability testing.

These items require a new declared analysis or protocol extension and cannot be silently folded into the primary results after evaluation begins.
