# Pertura Test Segments

The current smoke harness is intentionally executable as one dependency-light
script, with a pytest wrapper for package/CI use:

```powershell
python -m pytest
python tests/test_harness.py
```

It is organized into independent logical segments. When splitting into pytest
files, keep the same segment boundaries so each paper claim remains separately
auditable.

Segments now test the perturb-seq native product surface and the audited
runtime separately. Product checks cover the HTML/API contract, candidate
actions, stage cards, capability cards, product events, console turns, and
workflow autopilot. Runtime checks cover the event store, gates, observation
memory, evidence review, replay, fork, and repair.

```powershell
# Run independent paper-claim smoke checks.
pertura claims --json
pertura toolbox --json
python -m pertura.claim_tests
python -m pertura.claim_tests --list-claims
python -m pertura.claim_tests --claim analysis_graph
python -m pertura.claim_tests --claim observation_memory
python -m pertura.claim_tests --claim deliberative_audit
python -m pertura.claim_tests --json
python -m pertura.claim_tests --claim observation_memory --json

# Optional source-tree developer entrypoints.
python tests/test_claim_segments.py --claim analysis_graph
python tests/test_claim_analysis_graph.py
python tests/test_claim_observation_memory.py
python tests/test_claim_deliberative_audit.py

# Show segment ids and claim aliases.
python tests/test_harness.py --list-segments

# Focus the reported checks on one claim alias.
python tests/test_harness.py --segment evidence_chain

# Focus multiple segments or aliases.
python tests/test_harness.py --segment analysis_graph,operator_surface
```

`pertura claims --json` exposes the core claim manifest, capsule claim ids, and
standalone verification commands. `pertura toolbox --json` exposes the local-read
self-audit toolbox shared by operators and the LLM. `test_claim_segments.py`
builds small fixtures for the three core claims without executing the full smoke harness. Use
`--claim` to run one claim at a time, and `--json` to emit a machine-readable
artifact with per-claim and per-check pass/fail records. The `test_claim_*.py`
files are physically independent entrypoints for artifact reviewers; they reuse
the shared claim fixtures so the checks do not drift.
`test_harness.py --segment` still filters the reported checks and selected
segment output inside the larger script, so it remains useful for claim-focused
regression evidence while the rest of the suite is being split.

## Core Claim Coverage

| Claim | Harness segment | What it proves |
| --- | --- | --- |
| User-editable analysis graph spec + gate | `test_claim_analysis_graph.py`; 13. Pertura v2 analysis spec and gating | Public graph API, graph audit, node contracts, gate behavior, node transitions, allowed capabilities, CLI/API contract surfaces |
| Perturb-seq product workbench | `test_pytest_wrapper.py`; operator/product segments in `test_harness.py` | Analysis Console contract, Workflow Builder stage cards, candidate actions, scoped tool surface, product event timeline |
| Scientific observation memory | `test_claim_observation_memory.py`; 6. Observation memory; 10. Trace and impact graph semantics | Conflict/divergence, coverage labels, branch intent entries, provenance index, stale dependency propagation, observation/conclusion provenance |
| Deliberative LLM exploration with commit-time audit | `test_claim_deliberative_audit.py`; 7. Real attempt execution chain; 12. Replay, fork, and scientific diff; 13. tool-loop checks | Free exploration can commit with warning, disallowed capabilities are blocked at commit, capability output contracts, finish-time audit gates, replay/integrity hashes, run capsule checks, local-read audit tools |
| Evidence-chain integrity | 10. Trace and impact graph semantics; 12. Replay, fork, and scientific diff | `review_evidence_chain`, `audit_run`, `trace_upstream`, capsule integrity, deterministic replay |

## Suggested Future Split

- `test_event_graph.py`: reducer, graph derivation, relation semantics.
- `test_memory_context.py`: observation memory, context dashboard, runtime symbols.
- `test_audit_evidence.py`: run audit, evidence-chain review, stale/failed support, finish gates.
- `test_analysis_spec.py`: fluent API, spec validation/audit, node contracts, gated dispatch.
- `test_operator_surfaces.py`: CLI/API helpers, capsule, replay/fork/diff.
- `test_execution_jobs.py`: execution manifests, persistent jobs, cancellation.

The key discipline is not file count; it is that every externally claimed
property has a named, runnable segment.
