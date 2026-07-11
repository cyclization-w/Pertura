# v0.2 capability-first implementation status

This file records the `0.2.0a5` provider-neutral agent-skills checkpoint. It is not the final `0.2.0` scientific release.

## Code checkpoint

- The v0.2 core schemas, five MCP tools, promotion policy, receipt payload, and `ScopeKey` semantics remain frozen.
- Product policy is runtime-neutral and shared by the manifest, broker, receipt, promotion, report, and compatibility snapshot.
- Broker signing keys are ephemeral. Persisted authority-session records retain public keys and signed session roots so separate CLI processes can finalize historical results without re-signing them.
- Receipts record execution provenance inside the controlled Pertura runtime. They are not a claim that arbitrary same-user local code is cryptographically unforgeable; the product boundary is preventing unsupported scientific claim promotion.
- The default capability-first import path does not load legacy registrars, stages, classic recipes, or evidence tools.
- A single planner selects methods from confirmed design facts and committed diagnostics. Runtime-owned dependency resolution reconstructs scope, status, trust, and hash from the commit store.
- Validator-passed exploratory results are committed as `validated_untrusted`, carry no trusted receipt, and cannot support strong measured statements.
- Twenty granular `0.1.0` candidate capabilities cover P0-P3 intake/design, guide assignment/QC, state/module reference, target reliability, SCEPTRE, Propeller, sensitivity, and null calibration.
- Existing composite capabilities remain deprecated compatibility wrappers.
- PerturaBench stores versioned case specifications and executes synthetic cases through the product path. Optional environment integrations explicitly report when they were not run.
- Wheel and sdist checks cover capability specs, scientific runners, environment profiles, dashboard assets, benchmark cases/schemas, compatibility snapshots, and the complete agent skill bundle.
- Product-tool definitions and handlers are provider-neutral; the Claude MCP wrapper is a thin adapter over the same frozen five-tool surface.
- Four bundled Perturb-seq skills provide operational, design, screen-diagnostic, and interpretation guidance without entering receipts, dependencies, or promotion.
- Claude loads only the bundled skills plus explicitly supplied plugin roots. User and project-global skills are excluded by default, while Read/Glob/Grep/Bash/Write/Edit/NotebookEdit remain available.
- The OpenAI Agents SDK adapter is an import-safe contract and schema projection only. It makes no API request and reports `openai_adapter_ready: false`.

Expected audit state:

```text
build_version: 0.2.0a5
repository_ready: true
runtime_spine_ready: true
code_ready: true
local_fixture_ready: true
real_benchmark_ready: false
skill_bundle_ready: true
claude_skill_adapter_ready: true
openai_adapter_ready: false
skill_behavior_benchmark_ready: false
release_ready: false
default Pertura domain tools: 5
```

`optional_environment_ready` is machine-specific and is not conflated with repository correctness.

## Deliberately blocking release

Run:

```bash
pertura release-check --repo .
python -m pertura_bench run-matrix --tier synthetic_ci
python -m pertura_bench export-server-plan --output server-plan.json
```

Final `0.2.0` remains blocked until:

1. Replogle, Papalexi, Norman, and Kang source/conversion/subset locks exist and mapped full-data jobs have portable verdicts.
2. Candidate scientific adapters pass method-specific real-data, null-calibration, failure-detection, runtime, and memory thresholds.
3. `crispri_screen_v1` and `crispra_screen_v1` reference independent expert-adjudicated calibration/evaluation sets and pass production reliability metrics.
4. Optional execution environments used for release verdicts have frozen package/build manifests.
5. Server outputs bind the same Git commit, wheel, case specifications, environment locks, and dataset locks.

Synthetic fixtures, published-proxy labels, copied hashes, or a YAML `validated` flag cannot remove these blockers.
