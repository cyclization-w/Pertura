# Pertura Paper Benchmark Incident Log

Last updated: 2026-07-17

This is the durable incident record for the `0.2.0a17`/`0.2.0a18`/`0.2.0a19` paper
benchmark. It records failures that can affect correctness, reproducibility,
fairness, runtime completion, or interpretation. It is not a substitute for
the frozen task, reference, asset, resource, or server-plan manifests.

## Recording rules

- Never record API keys, tokens, credentials, private URLs, or complete secret
  environment files. Record only whether authentication succeeded and rotate
  any credential that appeared in a terminal transcript.
- Preserve failed run directories and logs. Mark invalid runs explicitly; do
  not silently overwrite or count them as model failures.
- Separate infrastructure failures from scientific/task failures. A model can
  be scored only after provider, environment, asset, runner, and evaluator
  infrastructure has completed correctly.
- Record the first failing gate and downstream consequences separately. For
  example, a failed upstream task can make a later task fail its dependency
  gate without creating a second root cause.
- A code fix is `fixed_unverified` until the same minimal smoke path succeeds.
  It becomes `verified` only after retained evidence demonstrates the fix.
- Any task, reference, split, or scoring semantic change after checkpoint
  freeze requires a new checkpoint and reruns for every affected condition and
  repeat. Runner-only fixes require a rebuilt wheel, updated resource lock,
  rebound server plan, and reruns only of affected jobs.

Status values:

- `open`: unresolved and blocks affected runs.
- `fixed_unverified`: patched or operationally corrected, awaiting smoke.
- `verified`: corrected and demonstrated by retained evidence.
- `accepted_limitation`: intentionally retained and reported as a limitation.
- `invalid_run`: the affected run cannot be scored as model performance.
- `superseded`: belongs to a retired protocol path and is retained for history.

## Recent agent smoke incidents

### PB-032 — Paper CodeAct preflight used the main runner environment

- Date: 2026-07-15/16
- Phase: agent smoke
- Affected run: Sherlock Slurm job `34111544`, `WF-KANG`,
  `free_codeact`, repeat 1
- Symptom: both turns produced a working note instead of task artifacts. The
  first error was `PythonEnvironmentError`; `scanpy`, `pertpy`, and `decoupler`
  were missing from the main `pertura-aaai-py311-a18` environment.
- Root cause: the generic Claude runtime default preflight expected packages
  spanning several deliberately isolated scientific profiles. The paper
  runner did not bind CodeAct to the frozen `python-science-v1` interpreter.
- Consequence: `KANG-01` lacked `benchmark_result.json` and all required
  artifacts. `KANG-02` additionally failed `dependencies_present`. The later
  TurnDraft JSON-format note was a secondary consequence, not a provider
  response failure.
- Resolution: the paper runner now resolves
  `PERTURA_PYTHON_SCIENCE_ENV/bin/python` and preflights only the general
  CodeAct package surface. Pertpy and decoupler remain in their dedicated
  environments.
- Verification evidence: Sherlock job `34112683` passed the corrected Python
  preflight and advanced to SDK skill-surface validation.
- Benchmark treatment: job `34111544` is an infrastructure-invalid run and
  must not be scored as free-CodeAct performance.
- Status: `verified`

### PB-033 — Provider-native skills were treated as Pertura skill leakage

- Date: 2026-07-15/16
- Phase: agent smoke
- Affected run: Sherlock Slurm job `34112683`, `WF-KANG`,
  `free_codeact`, repeat 1
- Symptom: the SDK initialized with unnamespaced provider-native skills such
  as `batch`, `code-review`, and `dataviz`; the runtime expected an empty skill
  list for free CodeAct and terminated before the first tool call.
- Root cause: skill validation compared the complete provider skill surface
  for exact equality instead of comparing the Pertura-managed namespace.
- Resolution: validate exact equality only for managed namespaced skills,
  always treat unexpected `pertura:*` skills as leakage, and retain the full
  provider-native surface in the run manifest for cross-condition comparison.
- Required verification: rebuild/rebind and rerun the KANG free-CodeAct smoke.
- Benchmark treatment: job `34112683` is infrastructure-invalid and must not
  be scored as free-CodeAct performance.
- Status: `fixed_unverified`

### PB-034 — Auto-approved tools shadowed the input-readonly callback

- Date: 2026-07-15/16
- Phase: agent smoke security/fairness audit
- Affected run: Sherlock Slurm job `34112683`
- Symptom: the SDK emitted `CanUseToolShadowedWarning` for all generic CodeAct
  tools because whole-tool `allowed_tools` entries bypassed `can_use_tool`.
- Root cause: the input/runtime-state protection policy was implemented as a
  permission callback even though the same tools were intentionally
  auto-approved for noninteractive benchmark execution.
- Resolution: enforce the policy in one mandatory `PreToolUse` hook, combine
  it with audit logging, return `deny` only for protected mutations, and return
  no permission override for safe calls. The runtime now requires SDK hook
  support instead of silently omitting the guard.
- Required verification: the next smoke must contain Pre/Post hook records,
  must not emit the shadow warning, and must still permit writes under the
  declared output directory.
- Benchmark treatment: no tool ran in job `34112683`, so no input mutation was
  observed; the job is already invalid under PB-033.
- Status: `fixed_unverified`

### PB-035 -- KANG turn exhaustion exposed stale repair grading

- Date: 2026-07-15/16
- Phase: agent smoke
- Affected run: Sherlock Slurm job `34114385`, `WF-KANG`,
  `free_codeact`, repeat 1
- Symptom: both KANG turns ended with SDK subtype `error_max_turns` after
  21 reported turns. `KANG-01` was graded at 18:30:55 without a result, while
  its `benchmark_result.json` appeared at 18:50:57 during `KANG-02`.
- Root cause: the 20-turn generic runtime default was too tight for the
  multi-step CodeAct task. The later turn was explicitly allowed to repair
  missing upstream files, but the runner neither regraded the repaired task
  nor required the complete upstream artifact contract for dependency
  satisfaction. Background Bash also made turn completion boundaries less
  deterministic.
- Resolution: use a frozen 32-turn paper-only budget for every condition,
  require synchronous Bash in benchmark mode, require complete dependency
  artifact contracts, and re-evaluate all task verdicts once at workflow end.
  Per-task scientific wall-time limits remain unchanged.
- Required verification: rebuild/rebind and rerun the KANG free-CodeAct smoke;
  confirm no background task is accepted, `max_turns_per_task` is 32, and any
  additive upstream repair is reflected in the final verdict.
- Benchmark treatment: job `34114385` is a protocol smoke and must not be
  scored as model performance.
- Status: `fixed_unverified`

### PB-043 -- Detailed capability contracts are not surfaced to the agent

- Date: 2026-07-16/17
- Phase: formal agent execution and product-surface review
- Affected checkpoint/runs: `v0.2.0a18-paperbench`; in particular the
  `WF-PAPA` `pertura_full` repeat-1 run rooted at
  `be39fdb4bf904466aa820e4c2d1ac462`
- Symptom: the five public MCP tools expose only shallow top-level input
  shapes. Fields such as `parameters`, `scope`, and `dependencies` appear as
  generic objects or arrays, while output documentation lists required field
  names without the complete nested types, status transitions, artifact-role
  semantics, or task-specific minimal valid calls. The authoritative
  capability YAML specifications contain these details, but they are not
  automatically included in the model context. Agents may therefore spend
  turns using Bash, repository reads, or failed calls to rediscover contracts
  that the runtime already knows. In the cited PAPA run, the agent produced
  the required scientific artifacts but exhausted its turn budget before
  writing `benchmark_result.json`; the trace supports excessive exploration
  as a contributing factor but does not prove that contract visibility was
  the sole cause.
- First failing gate: `benchmark_result_schema_valid`, followed by the
  required artifact-path/role gates because no result manifest bound the
  already-created files.
- Root cause: the agent-facing MCP schema and procedural skills are separated
  from the richer capability registry. Capability-specific parameter schemas,
  defaults, dependencies, output kinds, claim permissions, and completion
  conditions are enforced internally but are not rendered as task-conditioned
  tool contracts for the model. Pertura is not missing a planner entirely:
  `pertura_workflow.planner` already provides deterministic exact-alias and
  design-aware single-capability routing, explicit-capability validation,
  blocker reporting, required-upstream guidance, and authoritative committed
  dependency resolution. It deliberately rejects substring route definitions.
  However, its current `CapabilityPlan` selects one capability and reports a
  flat `required_upstream` list; it does not compile a multi-capability DAG,
  render capability contracts into model context, track node/artifact state,
  or reserve a finalization phase. The paper task catalog separately records
  `expected_capability_dag` for protocol validation and evaluation, but that
  metadata does not currently drive execution or get exposed as an executable
  plan to the agent.
- Resolution: do not change the frozen a18 capability, tool, task, or prompt
  surface during formal execution. For the next product checkpoint, extend
  the existing planner rather than creating a disconnected planner: preserve
  its current exact-alias/design gates and dependency resolver, add a versioned
  multi-node plan with nodes, edges, readiness, artifact roles, and plan-state
  transitions, and generate contract cards directly from the hashed capability
  YAML registry. Bind benchmark tasks to explicit capability IDs rather than
  keyword matching; for open product requests, parse a validated structured
  intent before invoking the same deterministic compiler. Inject only the
  relevant contracts into `pertura_full`, including minimal valid calls,
  required/optional inputs, status handling, artifact roles, next actions, and
  stop/finalization conditions. Pair the planner extension with a runtime
  finalization reserve so contract visibility alone is not expected to
  guarantee delivery. Tasks unsupported by registered capabilities must be
  allowed to produce an explicit CodeAct, evidence-interpretation, or blocked
  route instead of being forced into a capability DAG.
- Verification evidence: a19 P0 now renders hash-bound task-scoped contract
  views, records explicit missing allowlist dependencies, reserves a uniform
  closure phase, and has local regression coverage for cross-condition
  isolation and result-directory write restrictions. Sherlock canary evidence
  is still required to demonstrate reduced discovery calls and successful
  result-manifest finalization with the live provider.
- Benchmark treatment: retain all a18 executions as pilot evidence only. Do
  not retrospectively repair them or mix them with a19 formal results. Start
  formal scoring only after all four a19 canaries pass and the a19 checkpoint
  is frozen.
- Status: `fixed_unverified`

### PB-044 -- Canary readiness re-ran every scientific environment doctor

- Date: 2026-07-16/17
- Phase: a19 live canary
- Affected checkpoint/runs: pre-tag a19 checkpoint at commit
  `dcf8c076c4ee998bc239deadd07b0be9f7250e0a`; Sherlock job `34239260`
- Symptom: KANG-01 `pertura_full` failed before provider initialization while
  the runner was checking `perturbseq-python-v1`, an environment unrelated to
  the task. The Pertpy import smoke exceeded the environment doctor's
  120-second subprocess timeout.
- Root cause: the first P0 readiness implementation iterated every environment
  profile declared anywhere in the capability registry and called
  `environment_lock`, which deliberately performs a full live doctor. This
  duplicated checkpoint-time validation inside every task, ignored the
  task-scoped candidate allowlist, and made startup sensitive to shared
  filesystem import latency.
- Resolution: derive profiles only from the current task's frozen capability
  candidates and validate their already-frozen environment manifests and lock
  hashes without launching micromamba, Python, R, or scientific imports. Full
  environment doctors remain mandatory before resource-lock binding.
- Verification evidence: local regression tests verify that an edgeR-only
  candidate reads only the edgeR manifest and launches no unrelated profile.
  The refreshed KANG-01 run `adf4d508f2c64b228e1b11dbccc1d1d1`
  reached provider execution without invoking the unrelated Pertpy doctor;
  its later CodeAct handoff issue is tracked separately as PB-045.
- Benchmark treatment: job `34239260` is infrastructure-invalid and is not a
  model or scientific failure. No other a19 canaries may start until the
  refreshed checkpoint passes KANG-01.
- Status: `verified`

### PB-045 -- CodeAct route lacked a deterministic environment handoff

- Date: 2026-07-16/17
- Phase: a19 live canary
- Affected checkpoint/runs: pre-tag a19 checkpoint at commit
  `7080d42340ae67962b4e39df4aa8edac1b5e9db0`; KANG-01 run
  `adf4d508f2c64b228e1b11dbccc1d1d1`
- Symptom: the compiler correctly marked all three frozen KANG capabilities
  blocked, but the resulting `route=codeact` brief exposed only the blocked
  capability contracts. The provider then reconsidered Python, rpy2, and
  edgeR instead of directly using the already-locked edgeR environment, and
  produced no task artifact during the observed pilot interval.
- Root cause: a19 P0 compiled capability contracts and route status but did
  not instantiate an execution contract for the CodeAct branch. The edgeR
  profile appeared only inside a blocked node, whose minimal capability call
  was correctly forbidden. The task had no deterministic script path or
  invocation command for the legal fallback.
- Resolution: bind frozen generic CodeAct protocol ids for PAPA-06, KANG-01,
  and KANG-02. Compile a canonical-hashed handoff from the task binding,
  registered assets, output contract, and candidate-scoped frozen environment
  readiness. Inline its method, environment variable, invocation, outputs,
  and non-receipt authority boundary only for `pertura_full`.
- Verification evidence: local tests cover deterministic handoff and plan
  hashes, edgeR invocation, output binding, environment blocking, authority
  separation, and cross-condition prompt isolation. A refreshed live KANG-01
  canary is required.
- Benchmark treatment: the affected run is retained as a pre-fix pilot and is
  not scored. Do not start another canary until the refreshed KANG-01 handoff
  is observed as ready and used without environment discovery.
- Status: `fixed_unverified`

### PB-046 -- KANG-01 exhausted its task budget without a result checkpoint

- Date: 2026-07-16/17
- Phase: a19 live canary
- Affected checkpoint/runs: pre-tag a19 KANG-01 run
  `f6a96ca613494661aef3353a578461c8`
- Symptom: the agent materialized a design matrix, preprocessing script, and
  pseudobulk counts, but continued method exploration until the 7,200-second
  task timeout. It never wrote `benchmark_result.json`, so independent
  evaluation was unavailable and the provider turn was cancelled.
- Root cause: the frozen CodeAct handoff named the method and environment but
  did not provide reusable method execution skills or require a conservative
  result checkpoint before expensive work. The model therefore owned too
  much procedural rediscovery and had no durable partial result at closure.
- Resolution: add four reusable, task-scoped skills for plan consumption,
  replicate-aware edgeR execution, design-preserving null calibration, and
  checkpoint/finalization. Bind them deterministically by task, expose them
  only to `pertura_full`, and audit both baseline conditions for skill access.
  Package the parameterized Python/R templates in the wheel without adding a
  capability or changing scientific references/evaluators.
- Verification evidence: local skill validation, frozen task-binding tests,
  template tests, baseline leakage-audit tests, wheel-content audit, and all
  four refreshed live canaries are required before tagging a19.
- Benchmark treatment: the affected run is infrastructure-invalid pilot
  evidence and is not scored. A refreshed KANG-01 canary must demonstrate
  plan -> DE -> null -> finalize and a schema-valid result.
- Status: `fixed_unverified`

## Incident index

### Repository, build, and checkpoint

| ID | Incident | Resolution | Benchmark treatment | Status |
|---|---|---|---|---|
| PB-001 | Server Git did not support `git -C`, leaving `BENCHMARK_COMMIT` empty. | Resolve the commit from inside the repository with `cd ... && git rev-parse HEAD`. | No run was started. | `verified` |
| PB-002 | Artifact locks failed `canonical_hash` validation after the a17 schema/code update. | Recompute canonical lock identities while preserving artifact hashes and inspect every changed lock. | Old incompatible locks were not used for scoring. | `verified` |
| PB-003 | Papalexi artifact lock reported source-manifest drift after license/source metadata changed. | Reconcile the reviewed manifest, conversion-script hash, artifact hash, and lock chain; migrate only the reviewed Papalexi lock. | No scientific output was reused across unmatched source locks. | `verified` |
| PB-004 | An a18 wheel contained forbidden legacy modules because the build directory was stale. | Build from a clean Git archive/source tree and rerun distribution-content audit. | The contaminated wheel was discarded. | `verified` |
| PB-005 | The Sherlock refresh helper initially failed because the main environment lacked the Python `build` module. | Install the fixed build dependency once in the main benchmark environment. | No checkpoint was produced by the failed refresh. | `verified` |
| PB-006 | Plan validation initially reported zero required turns because the diagnostic read the wrong field. | Sum `required_task_count` over the 24 `paper_agent_workflow` jobs. | Diagnostic-only error; bound plan still contained 120 turns. | `verified` |

### Data, locks, and split protocol

| ID | Incident | Resolution | Benchmark treatment | Status |
|---|---|---|---|---|
| PB-007 | Early subset planning passed a dataset-specific string into a schema field that only allowed `crispri` or `crispra`. | Use the real modality and keep grouping identity in the split/group fields. | Failed candidate specs were discarded. | `verified` |
| PB-008 | Kang split generation raised `Grouper for 'ind' not 1-dimensional` because the donor column was duplicated during projection/rename. | Deduplicate projected metadata columns before grouping. | Failed split attempt was discarded. | `verified` |
| PB-009 | Papalexi subset materialization appeared hung with no output files or progress log while consuming one CPU. | Diagnose process state and I/O; later paper-v1 execution used split-scoped sequential reads instead of waiting for the retired full materialization path. | Original materialization path is not evidence for the paper results. | `superseded` |
| PB-010 | Migrated Sherlock sidecars still contained nlab2 absolute paths and failed with `local benchmark sidecar escapes the declared cache`. | Rebind/migrate path-bearing sidecars to the Sherlock cache root while preserving file hashes. | Unrebound sidecars were rejected. | `verified` |
| PB-011 | Kang has six evaluation singlets without a cell-state annotation. | Keep them for state-independent edgeR and explicitly exclude them from Propeller; record the exclusion count. | Reported limitation, not imputed. | `accepted_limitation` |
| PB-012 | Papalexi evaluation controls had no `rep1` controls, so replicate-stratified Mixscape was not estimable. | Use frozen evaluation controls globally, retain rep2/rep3 counts, and record the policy reason in REF-04. | No claim of replicate-stratified Mixscape. | `accepted_limitation` |

### Reference generation and scientific environments

| ID | Incident | Resolution | Benchmark treatment | Status |
|---|---|---|---|---|
| PB-013 | A REF generator was missing because the server remained at an older detached commit. | Fetch the benchmark branch and check out the fetched commit before running the generator. | Missing-script attempt produced no reference. | `verified` |
| PB-014 | REF-04 Pertpy signature generation first failed because the selected control group was empty. | Correct control selection and explicitly record the fallback control policy. | Failed REF-04 output was discarded. | `verified` |
| PB-015 | REF-04 Mixscape failed because Pertpy called a scikit-learn `GaussianMixture._m_step` ABI that lacked `xp`. | Build a compatible frozen perturbseq environment and validate the ABI in the environment doctor. | Only the successful frozen-environment REF-04 is indexed. | `verified` |
| PB-016 | REF-05 rejected six Kang cells with missing state identities. | Apply the explicit state-independent/state-dependent inclusion policy described in PB-011. | The six-cell exclusion is present in the manifest. | `verified` |
| PB-017 | REF-05 Propeller failed first in a one-level model matrix and then in `propeller.ttest` input handling. | Inspect `getTransformedProps` structure and use the correct donor-paired design/input representation. | Failed R attempts are not reference evidence. | `verified` |
| PB-018 | SCEPTRE setup referenced a nonexistent GitHub tag/archive and then failed source dependency builds. | Install the fixed SCEPTRE version through the reproducible Conda route and validate with environment doctor. | Failed source-bootstrap environments were discarded. | `verified` |
| PB-019 | SCEPTRE calibration rejected `n_processors=1`; the API requires `auto` or an integer at least 2. | Keep the job CPU budget controlled while passing an accepted SCEPTRE calibration setting. | The failed OBS-06 attempt was not scored. | `verified` |
| PB-020 | Interpretation-environment pip builds failed on the legacy server toolchain. | Install fixed interpretation dependencies through Conda and verify decoupler/gseapy imports. | Failed environment prefixes were not bound. | `verified` |
| PB-021 | Several long REF computations appeared stalled during HDF5-backed sequential reads. | Inspect CPU, `/proc` I/O, process state, and logs before intervention; allow active scans to complete. | No process was killed solely for lack of log progress. | `verified` |

### Catalog and evaluator binding

| ID | Incident | Resolution | Benchmark treatment | Status |
|---|---|---|---|---|
| PB-022 | PAPA-02 reference binding pointed to a nonexistent REF-03 path and left metrics unbound. | Correct the path to `REF-03/control_state_reference/control_assignments.tsv` and rerun binding validation. | Invalid bound catalog was discarded. | `verified` |
| PB-023 | A diagnostic expected top-level `asset_count`, but the bound asset catalog stores assets by workflow. | Sum `len(workflow.assets)`; confirmed 40 assets across four workflows. | Diagnostic-only failure. | `verified` |
| PB-024 | Early benchmark results could self-report scalar metrics without independent evidence. | Bind every task to an artifact evaluator/reference or an explicit protocol hard gate. | Self-reported metrics remain indices only. | `verified` |

### Server, provider, and shell operations

| ID | Incident | Resolution | Benchmark treatment | Status |
|---|---|---|---|---|
| PB-025 | nlab2 completed TCP connections but reset TLS handshakes to both Anthropic and DeepSeek. | Confirm the network-layer failure with direct TLS probes and migrate execution to Sherlock, where TLS and SDK probes pass. | nlab2 provider attempts are infrastructure-invalid. | `verified` |
| PB-026 | Sherlock's first TLS diagnostic script reported an `AttributeError` because the diagnostic context-manager code was malformed. | Use a direct socket/SSL probe; both provider TLS routes then passed. | Diagnostic-only failure. | `verified` |
| PB-027 | Provider environment files were corrupted by OSC 633 terminal control sequences and literal pasted shell content. | Write the five-line secret file programmatically, verify escape/newline counts, set mode 600, and source it only at job start. | Corrupted-secret jobs are infrastructure-invalid. | `verified` |
| PB-028 | Provider probe had no token or used an invalid token; one credential appeared in terminal output. | Recreate the secret file without echoing the token, verify only length/fingerprint, and rotate any exposed key. | Authentication failures are not model failures. | `verified` |
| PB-029 | A provider probe failed because `PERTURA_REPO` was not exported. | Export all required runtime paths in the job script before launching the SDK. | Probe-only failure. | `verified` |
| PB-030 | The first KANG smoke script ended after writing resource evidence, so Slurm completed in seconds without running the workflow. | Inspect the generated script and append/verify the actual runner command before submission. | Completed Slurm job was not a benchmark run. | `verified` |
| PB-031 | First real KANG smoke crashed in grading with `NameError: anchors is not defined`. | Bind declared paper anchors through `_judge_task_context`; add a regression test; rebuild wheel/resource lock/plan. | The crashing run is infrastructure-invalid. | `verified` |
| PB-032 | Paper CodeAct scientific preflight used the main runner environment. | Bind the frozen `python-science-v1` interpreter and its general package surface. | Slurm job `34111544` is infrastructure-invalid. | `verified` |
| PB-033 | Provider-native skills were treated as Pertura skill leakage. | Compare only the exact managed skill namespace and record provider-native skills separately. | Slurm job `34112683` is infrastructure-invalid. | `fixed_unverified` |
| PB-034 | Generic tool auto-approval shadowed the input-readonly callback. | Enforce protected-path policy in one mandatory `PreToolUse` hook. | Rerun required before formal benchmark. | `fixed_unverified` |
| PB-035 | KANG turns exhausted the 20-turn default and later repair left an upstream verdict stale. | Use 32 paper turns, synchronous benchmark Bash, complete dependency contracts, and final workflow regrading. | Job `34114385` is protocol-only and not scored. | `fixed_unverified` |
| PB-036 | The paper task prompt did not clearly distinguish `benchmark_result.json` from the final `pertura-turn-draft-v1` response, so both KANG tasks wrote TurnDraft-only fields into otherwise complete scientific result files. | Supply one schema-valid task-specific result template, an exact allowed-field list, and an explicit two-output distinction to all three conditions. | Job `34132113` is protocol-only and not scored; job `34139333` verified schema-valid outputs in both turns. | `verified` |
| PB-037 | A scientifically failed paper workflow returned CLI exit code 1 even when provider execution, artifact capture, schema validation, and independent grading all completed. | Record separate `execution_status` and `score_status`; return CLI success for a completed workflow while retaining the failed scientific score unchanged. | Jobs `34139333` and `34207525` both completed execution, retained failed scientific scores, and returned CLI success. | `verified` |
| PB-038 | The paper runner exposed only full-workflow execution, so a targeted PAPA smoke would unnecessarily execute all eight turns. | Add an explicitly non-formal `--smoke-task` selector that retains the same runtime, assets, prompt, and evaluator while limiting provider invocation to catalog-selected turns. | Targeted PAPA-07 and PAPA-06 jobs each invoked exactly one selected task; formal server-plan jobs do not set the selector. | `verified` |
| PB-039 | PAPA asset registration passed benchmark-specific kinds such as `environment_lock` directly into the narrower product `DataAssetRef.kind` field. | Use an explicit fail-closed adapter: preserve product-native kinds and map environment locks, executables, protocols, reference locks, and priors to `external_resource` with curated-prior source class. | Later PAPA jobs registered the same bound asset catalog and reached provider execution. | `verified` |
| PB-040 | Isolated PAPA-07 smoke still exposed full upstream repair contracts, so the agent spent the complete 1,800-second budget inspecting environments and missing dependencies instead of writing the interpretation outputs. | In `--smoke-task` mode, suppress upstream contracts, prohibit dependency repair and unrelated environment inspection, and mark evidence-interpretation turns as read-only over frozen evidence. | Job `34199744` used only the current frozen evidence inputs before an independently recorded provider-stream timeout; formal multi-turn dependency repair remains unchanged. | `verified` |
| PB-041 | The PAPA-07 output contract listed artifact paths relative to the task output directory without naming that base explicitly, so job `34195147` wrote otherwise usable artifacts directly under `outputs/` and the independent evaluator could not find them. | State the exact workspace-relative destination for every required artifact and explicitly prohibit writing task artifacts directly under `outputs/`; keep the frozen catalog, reference, thresholds, and evaluator unchanged. | Job `34195147` is protocol-only because independent evaluation could not consume the misplaced artifacts; rerun the isolated smoke. | `fixed_unverified` |
| PB-042 | After dependency repair and artifact destinations were made explicit, PAPA-07 job `34199744` completed one Bash call and three Read calls by `17:31:23Z`, then emitted no further provider event or `ResultMessage` for the remaining roughly 20 minutes before exhausting its 1,800-second budget; cancellation emitted an ignored Claude SDK subprocess-transport cleanup warning after the verdict was safely written. | Keep the frozen wall timeout and fail-closed missing-output result; do not synthesize, move, or repair artifacts. Classify this trace as provider-stream inactivity rather than a running scientific subprocess, and treat the post-verdict transport warning as an upstream cleanup limitation unless it prevents termination or leaves live processes. | The smoke is protocol-only; a formal recurrence is an execution timeout under the frozen `failed_no_fallback` policy, not an independent scientific-evaluator result. | `accepted_limitation` |
| PB-043 | The agent sees shallow generic MCP schemas while detailed capability YAML contracts remain internal. An existing deterministic planner performs design-aware single-capability routing and dependency validation, but it does not expose a multi-step executable plan or contracts to the model. | a19 P0 adds hash-bound task-scoped contract rendering, frozen-candidate compilation, explicit blockers/routes, and a uniform completion guard; full dynamic Planner V2 remains future work. | a18 is retained as pilot evidence only; a19 formal scoring remains gated on four live canaries. | `fixed_unverified` |
| PB-044 | KANG-01 a19 canary startup re-ran doctors for every registry environment and timed out in an unrelated Pertpy import smoke. | Scope readiness to current task candidates and validate frozen manifest lock hashes without launching scientific subprocesses; keep full doctors at checkpoint time. | Job `34239260` is infrastructure-invalid; the refreshed run verified startup before exposing the separate PB-045 handoff issue. | `verified` |
| PB-045 | KANG-01 compiled a legal CodeAct fallback without a deterministic environment/script handoff, so the provider rediscovered edgeR execution. | Bind a frozen generic CodeAct protocol and compile a hash-bound runner, assets, outputs, and authority boundary only for `pertura_full`. | Run `adf4d508f2c64b228e1b11dbccc1d1d1` is pre-fix pilot evidence only; refresh and rerun KANG-01. | `fixed_unverified` |
| PB-046 | KANG-01 produced partial scientific files but exhausted its task budget without `benchmark_result.json`. | Add task-scoped plan, edgeR, null-calibration, and finalization skills with an early conservative checkpoint and baseline leakage audit. | Run `f6a96ca613494661aef3353a578461c8` is infrastructure-invalid pilot evidence; all four a19 canaries must be rerun. | `fixed_unverified` |
| PB-047 | The refreshed KANG-01 handoff required one task-authored `run_edger.R`, while its bound method skills required Python pseudobulk materialization, edgeR QL, and paired-label null calibration as separate steps; the generic table reader also misclassified `.tsv.gz` selections as CSV. | Make the handoff publish one ordered bound-skill pipeline with no wrapper-script requirement, freeze KANG's `ind`/`stim`/`cell_id` column bindings, and recognize compressed TSV suffix chains. | Run `f7adfbda583f4bd0968b6d23f7954ee7` is a pre-fix infrastructure pilot and must not be scored; rerun KANG-01 after refreshing the checkpoint. | `fixed_unverified` |

## Successful retained milestones

These are milestones, not incidents, but they define the recovery point after
which earlier failed attempts must not be mistaken for current blockers.

- All four source/license chains were reviewed and locked.
- Eight split-v2 specifications were generated with zero group, control, and
  cell overlap between calibration and evaluation.
- REF-01 through REF-10 were generated, validated, frozen, and indexed.
- OBS-06 completed with all hard gates true and independent metrics passing.
- The task-reference catalog bound 21 tasks; the paper asset catalog bound 40
  assets across four workflows.
- Six Sherlock scientific environments passed setup and doctor.
- The a18 Sherlock plan bound 24 workflow jobs and 120 required scored turns.
- DeepSeek direct API and Claude Agent SDK probes passed on Sherlock.
- PAPA-06 prompt-only job `34207525` completed in 1,148 seconds with all
  result-schema, artifact-role, artifact-path, timeout, and resource gates
  passing.  Its independent evaluator correctly rejected 117,707 observed
  target-gene rows against 167,841 frozen rows because the agent filtered
  50,134 required keys; all nine eligible targets were retained and no extra
  key was introduced.  This is a valid scientific baseline failure and a
  successful execution-path smoke.

## Adding a new incident

Add the incident before changing code or resubmitting a formal job whenever
possible. Use this template:

```markdown
### PB-NNN — Short title

- Date:
- Phase:
- Affected checkpoint/job/run:
- Symptom:
- First failing gate:
- Root cause:
- Resolution:
- Verification evidence:
- Benchmark treatment: valid, invalid, rerun required, or reported limitation
- Status: open, fixed_unverified, verified, accepted_limitation, invalid_run,
  or superseded
```

When a fix is verified, update the original entry rather than creating a
second disconnected entry. Include stable job/run IDs and artifact paths, but
never credentials.
