# Pertura Paper Benchmark Incident Log

Last updated: 2026-07-16

This is the durable incident record for the `0.2.0a17`/`0.2.0a18` paper
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
| PB-037 | A scientifically failed paper workflow returned CLI exit code 1 even when provider execution, artifact capture, schema validation, and independent grading all completed. | Record separate `execution_status` and `score_status`; return CLI success for a completed workflow while retaining the failed scientific score unchanged. | Job `34139333` is a valid protocol smoke with a failed free-CodeAct score, not an infrastructure failure. | `fixed_unverified` |

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
