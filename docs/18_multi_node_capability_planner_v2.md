# 18. Multi-node Capability Planner V2

## Status and scope

This document defines the intended multi-node planning architecture and the
smaller `0.2.0a19` P0 checkpoint. It is an internal product protocol, not a
claim that Pertura already performs general autonomous scientific planning.

The a19 checkpoint preserves the 44-capability registry, the five public MCP
tools, the v0.2 public schemas, REF-01 through REF-10, scientific task
definitions, dataset splits, evaluators, and scientific environments. It adds
deterministic task-scoped contract disclosure and finalization protection.

## Architecture

```text
Natural-language request
        |
        v
Intent Adapter
        |
        v validated IntentSpec
Deterministic Plan Compiler
        |
        v
Capability DAG + readiness
        |
        v
Progressive Execution Window
        |
        v
Capability execution / CodeAct / refusal
        |
        v
PlanStateDelta
        |
        v
Finalization
```

The Intent Adapter converts a user request to a validated structure. It does
not decide scientific truth, issue receipts, promote claims, or weaken claim
ceilings. The deterministic compiler consumes structured intent,
`DatasetContract`, committed `ResultEnvelope` objects, environment locks, asset
bindings, and the capability registry. Substring or keyword matching is not a
scientific method selector.

The compiler produces a dependency graph and a small active window. Execution
produces a state delta. A future full implementation recompiles the next
window after every committed result. The a19 P0 recompiles only at the start of
each benchmark task.

Planner output is advisory execution structure. It cannot modify receipts,
source classes, claim permissions, claim ceilings, promotion decisions,
reference evidence, or independent evaluator results.

## Future internal protocols

### IntentSpec

`IntentSpec` is the validated boundary between natural language and planning.
It contains:

- objective and requested deliverables;
- estimand and analysis unit;
- dataset, perturbation, population, contrast, and split scope;
- required output kinds;
- resource constraints;
- maximum claim strength and explicit prohibitions;
- optional explicitly requested capability IDs.

An explicit capability ID takes precedence over automatic candidate selection,
but it still passes all design, dependency, scope, asset, and environment
checks.

### CapabilityContractView

`pertura-capability-contract-view-v1` is generated from the registry-owned YAML
specification. It includes the capability ID and scientific hash, summary,
input requirements, full parameter schema, defaults, required parameters,
asset-role parameters, dependencies and policy, environment profile, output
kind, source class, claim permissions, timeout, and a minimal legal high-level
tool call. Asset-valued parameters contain registered asset IDs rather than
filesystem paths.

It never contains benchmark references, evaluator implementations, expected
answers, metric thresholds, paper truth, or grading decisions.

### CapabilityPlanV2

The future `CapabilityPlanV2` contains:

- canonical plan ID and content hash;
- validated `IntentSpec` hash;
- `DatasetContract` ID and hash;
- graph nodes and typed edges;
- active execution window;
- branch conditions and stop conditions;
- resource envelope and finalization reserve.

A `PlanNode` has one of these states:

```text
planned / ready / running / completed / blocked / skipped / failed / stale
```

A `PlanEdge` has one of these types:

```text
hard_dependency / validation_gate / optional_support / artifact_flow
```

Scientific prerequisites define graph topology. They are not a universal fixed
pipeline: different modalities, designs, analysis units, and available assets
legitimately compile to different paths.

### PlanStateDelta

`PlanStateDelta` records newly committed results, dependencies newly satisfied,
nodes invalidated or made stale, blockers, the next active window, and remaining
work. It may cause deterministic replanning in the full V2 design. It cannot
rewrite earlier artifacts or promotion records.

### ExecutionBrief

`ExecutionBrief` is the progressive-disclosure object actually shown to the
model. It exposes two to five current nodes, their status, blockers, complete
contracts, registered assets, minimum legal calls, completion checklist, and
stop conditions. The full registry is not copied into every prompt.

## Deterministic compilation

Compilation follows this order:

1. Honor explicit capability IDs or obtain structured candidates from the
   Intent Adapter.
2. Reject design-, modality-, input-, scope-, and environment-incompatible
   candidates.
3. Calculate the dependency closure and compare it with committed, current,
   scope-compatible results.
4. Classify unavailable hard dependencies, validation gates, optional support,
   and artifact flows.
5. Rank equally valid nodes by dependency satisfaction, scientific
   applicability, resource cost, and capability ID as the deterministic final
   tie-breaker.
6. Emit the first progressive window and explicit stop conditions.

Unsupported work must compile to an explicit blocked, CodeAct, or evidence
interpretation route. The compiler must never silently substitute a different
capability or represent an exploratory CodeAct artifact as a signed capability
result.

## a19 P0

The a19 P0 deliberately omits the natural-language Intent Adapter and same-turn
dynamic replanning. For paper tasks it treats `expected_capability_dag` as a
frozen candidate allowlist, not a directly executable pipeline and not an
answer key.

At the start of each `pertura_full` task the P0 compiler reads:

- the already-created `DatasetContract` and its confirmed/unresolved facts;
- current committed results for the exact contract hash;
- task-relevant registered assets and content hashes;
- registry YAML and capability scientific hashes;
- environment readiness from candidate-scoped frozen manifests (live doctors
  run before resource-lock binding, not inside agent tasks);
- the frozen candidate allowlist.

It writes `task/capability_plans/<task_id>.json` and the current alias
`task/PERTURA_CAPABILITY_PLAN.json`. The prompt receives only the compact active
window, plan location, route, completion checklist, and stop conditions.
`prompt_only` and `free_codeact` receive no capability brief.

Dependencies outside the frozen candidate set are not inserted. A candidate
that therefore cannot run is marked blocked. If the task permits exploratory
work, the route becomes CodeAct; otherwise the blocker is reported. PAPA-06 is
direct CodeAct scientific analysis. PAPA-07 is direct frozen-evidence
interpretation. Neither route may masquerade as a capability receipt.

The brief explicitly tells the model that dataset inspection is already
complete, supplies asset IDs, names the high-level tool and legal parameters,
and prohibits source/YAML/test/environment-directory exploration for contract
discovery.

## Completion guard

All three benchmark conditions use the same guard:

- provider `max_turns` remains 32;
- at most 24 expensive exploration calls per task;
- Bash, Notebook operations, Read/Glob/Grep, and Pertura scientific MCP calls
  count toward the exploration budget;
- the next expensive call activates closure mode and is denied;
- closure mode permits at most two additional reads;
- closure writes are restricted to the current task output directory;
- new Bash, Notebook, and scientific MCP execution is denied;
- the model must use existing artifacts to write `benchmark_result.json` and
  return the provider TurnDraft.

The runner records whether the guard triggered, exploration and closure call
counts, read counts, denied calls, and the triggering tool in each verdict. It
does not fabricate, move, repair, or scientifically complete missing results.

## Benchmark and product boundary

The paper benchmark may freeze explicit candidates to make conditions and
repeats reproducible. A future product Intent Adapter will produce structured
candidates from a real user request. Both paths must use the same deterministic
compiler and contract renderer.

The planner context never includes task references, graders, metric thresholds,
expected scientific values, or evaluation truth. Scientific correctness remains
the responsibility of executed artifacts and independent evaluators.

## a19 validation and rollout

Static validation must establish:

- 44 registry capabilities, 40 active capabilities, and five MCP tools;
- contract views match YAML parameter schemas and scientific hashes;
- all 23 known task/candidate dependency gaps are explicit rather than
  auto-expanded;
- asset-role parameters bind real asset IDs;
- PAPA-06 and PAPA-07 compile to their direct routes;
- no capability brief reaches either baseline;
- the guard behaves identically across conditions;
- existing planner, dependency, synthetic capability, packaging, and version
  tests still pass.

Before formal a19 execution, run these four canaries:

1. PAPA-01 and PAPA-02 `pertura_full` in one workflow session;
2. KANG-01 `pertura_full`;
3. PAPA-06 `prompt_only`;
4. REPL-01 `free_codeact`.

Every canary must produce a schema-valid result and required artifacts, invoke
the independent evaluator, avoid provider cancellation/timeout/max-turns, and
show no contract-discovery source exploration or repeated blocked calls. A
scientific score may pass or fail. If the 24-call threshold is changed, change
it once before the tag and rerun all four canaries.

The a18 outputs remain pilot evidence and are not mixed into a19 formal results.
The six scientific environments, caches, REF packs, task references, and splits
are reused. a19 requires a new main environment/wheel, contract-catalog hash,
resource lock, bound server plan, checkpoint, and tag.

## Sherlock handoff

Generate the answer-free contract catalog before binding the a19 server plan:

```bash
python scripts/generate_a19_capability_contract_catalog.py \
  --output "$PAPER_MANIFESTS/capability-contract-catalog.a19.json"
```

The plan export and bind commands must receive that file through
`--capability-contract-catalog`. Scheduler jobs receive its absolute path in
`PERTURA_CAPABILITY_CONTRACT_CATALOG`; the runner verifies the file hash and
catalog contents before starting any workflow.

The four canary selectors are intentionally short and reuse the formal workflow
runner:

```text
WF-PAPA  pertura_full  PAPA-01,PAPA-02
WF-KANG  pertura_full  KANG-01
WF-PAPA  prompt_only   PAPA-06
WF-REPL  free_codeact  REPL-01
```

Do not create `v0.2.0a19-paperbench` until all four canaries meet the acceptance
criteria above. Any guard-threshold change invalidates all four canary results.
