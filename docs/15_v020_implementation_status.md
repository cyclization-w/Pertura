# v0.2 capability-first implementation status

This file records the `0.2.0a17` pre-benchmark checkpoint. It is not the final `0.2.0` scientific release.

## Frozen product boundary

- The v0.2 core schemas, five MCP tools, receipt payload, `ScopeKey`, and `PromotionPolicy` payload/hash are unchanged.
- The active authority spine is `ResultEnvelope -> pertura_core.promotion -> TurnFinal/report`.
- The retired evidence lattice, registrar, stages, classic recipes, evidence MCP, and legacy finalizer live under `legacy/`. They are excluded from wheel/sdist and the default import path.
- `phase` remains in `CapabilitySpec` for presentation, ordering, and soft recommendations only. It does not determine scope or dependency topology.

## Runtime and scientific dependencies

- Every declared dependency has an explicit policy with `scope`, `usage`, and `accepted_statuses`.
- Registry validation requires exact agreement between `depends_on` and dependency-policy keys, validates values, and rejects cycles.
- Runtime reconstructs dependency ID, hash, kind, scope, status, trust, and freshness from the authority store. Caller-supplied assertions are ignored or rejected.
- Executors report consumed dependency hashes. Validators require actual consumption of every `scientific_input`, `row_filter`, and `parameter_source`.
- A missing, stale, mismatched, or empty row filter blocks before a scientific runner. SCEPTRE consumes the retained-cell manifest.
- Results bind the current capability/spec hash and dependency-policy hash. Older results without matching hashes remain historical and cannot satisfy a new dependency.
- `design_moi` and `guide_design` are authoritative only when confirmed. Unknown MOI blocks edgeR/SCEPTRE routing.
- High/combinatorial designs retain multi-guide cells. Low/single designs may exclude multi-guide cells without classifying them as transcriptomic doublets.
- Validator-passed exploratory results are committed as `validated_untrusted`; they have no trusted receipt and cannot support a strong measured statement.

## Sparse and resource-aware execution

- `GuideCountSource` supports materialized CSR, backed H5AD/MuData, 10x HDF5/MEX, and small delimited compatibility inputs.
- Guide assignment writes sparse posterior output without building a dense cell-by-guide Python matrix.
- CSV/TSV, Mixscape, Scrublet, and other unavoidable dense steps estimate memory before allocation.
- Default resource budget remains 4 GiB and one job. Over-budget work is blocked rather than relying on OOM.
- State, module, and virtual-evaluation paths retain their existing sparse/chunked execution semantics.

## Product lifecycle

- Projects, runs, assets, conversations, turns, provider bindings, and report revisions live in the project store.
- Scientific results, receipts, dependencies, session seals, and promotion remain exclusively in the authority store.
- Broker signing keys are ephemeral. Persistent authority sessions retain public keys and signed session roots for cross-process finalization.
- Claude resumes a compatible provider session and preserves raw provider output. Runtime renders structured `TurnDraft` content below committed-result claim ceilings.
- Asset identity excludes absolute paths. Missing or drifted assets propagate stale state.
- `finalize_report` is explicit, versioned, and idempotent by content digest.
- Product tools and handlers are provider-neutral. Claude is runnable; the OpenAI adapter remains an import-safe schema/instruction skeleton.

## Benchmark status

- Thirty-five exploratory capabilities have 210 deterministic synthetic cases.
- Twelve local fake-provider cases exercise project, asset, turn, resume, stale, repair, and report behavior.
- Scientific real-data verdict v3 separates current run completion from frozen-reference performance. The explicit run policy schedules 61 jobs and never duplicates full data as calibration.
- Three external catalogs bind confirmed design facts, capability parameters/assets, and metric references. Metric references additionally bind independent packaged generators or explicit curated/expert provenance.
- The primary agent catalog defines six Perturb-seq tasks under `pertura_full`, `prompt_only`, and `free_codeact`, repeated twice: 36 primary server runs. Two Kang tasks remain supplemental.
- Every primary agent condition must emit the same condition-neutral benchmark result and pass case-specific scientific reference metrics. Formal resource gates require scheduler/cgroup enforcement; prompt declarations do not count.
- Synthetic or reported-only verdicts cannot promote capability trust.
- The edgeR scientific golden now uses portable deterministic unit/cell variation instead of zero-variance pseudobulks, and its R provenance emits a canonical sessionInfo string array; the production validator continues to reject negative F or dispersion values.

Expected release audit after all local freezes are regenerated:

```text
build_version: 0.2.0a17
repository_ready: true
runtime_spine_ready: true
project_lifecycle_ready: true
asset_registry_ready: true
conversation_turn_ready: true
report_revision_ready: true
dependency_policy_ready: true
sparse_execution_ready: true
code_ready: true
local_fixture_ready: true
local_agent_protocol_ready: true
skill_bundle_ready: true
claude_skill_adapter_ready: true
openai_adapter_ready: false
real_benchmark_ready: false
real_agent_behavior_ready: false
release_ready: false
```

`optional_environment_ready` is machine-specific and is not conflated with repository correctness.

## Local verification

```bash
python -m pytest -q
python scripts/check_version_sync.py --repo .
python scripts/freeze_v020_contracts.py --check
python scripts/export_benchmark_schemas.py --check
python scripts/freeze_capability_parameters.py --check
python scripts/audit_capabilities.py --repo .
python -m pertura_bench validate-cases --repo .
python -m pertura_bench skills validate --repo .
python -m pertura_bench run-matrix   --tier synthetic_ci --repo . --write-frozen-synthetic-verdicts
python -m pertura_bench agent run-local   --repo . --output .p07_runs/agent-local --write-frozen-verdicts
```

The separate legacy lane is:

```bash
PYTHONPATH=legacy/src:src python -m pytest -c legacy/pytest.ini legacy/tests
```

## Release blockers

Release remains blocked until:

1. Replogle, Papalexi, Norman, and Kang artifact/conversion/subset locks exist.
2. Design-confirmation, real-parameter, and metric-reference catalogs are frozen.
3. Required scientific jobs have current hard gates and method-specific metrics/references.
4. All 36 primary comparative agent runs and required narrative/human review are complete.
5. CRISPRi and CRISPRa production reliability profiles reference expert-adjudicated calibration/evaluation sets.
6. Every required scientific environment passes doctor and is bound into verdict provenance.

Synthetic fixtures, published-proxy labels, copied hashes, or a YAML `validated` flag cannot remove these blockers.
