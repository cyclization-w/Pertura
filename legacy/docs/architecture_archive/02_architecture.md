# 02. Architecture

## Main Data Flow

```text
Claude CodeAct workspace
  -> outputs/ or artifacts/ files
  -> MCP evidence registrars
  -> artifacts/evidence_artifacts.jsonl
  -> evaluate_claims
  -> ClaimDecision list
  -> render_evidence_report
  -> reports/evidence_report.md
```

Claude can use normal exploration tools and Python. The runtime only takes control at evidence registration and final scientific surface rendering.

## Trust Domains

The cleaned repository is split into three packages:

```text
src/pertura_gate/       trusted deterministic gate core
src/pertura_runtime/    untrusted Claude/agent runtime adapter
src/pertura_bench/      benchmark harness and surface evaluator
```

Architecture invariant:

```text
pertura_runtime -> pertura_gate
pertura_bench   -> pertura_gate
pertura_gate    -> neither pertura_runtime nor pertura_bench
```

This makes two important boundaries structural:

- the gate does not know that Claude exists;
- the gate does not import benchmark-only lexical surface checks.

## Gate Core

Source directories:

```text
src/pertura_gate/core/
src/pertura_gate/identity/
src/pertura_gate/evidence/
src/pertura_gate/resolver/
src/pertura_gate/render/
```

Important files:

- `core/schema.py`: enums and dataclasses for artifacts, claims, decisions, eligibility.
- `core/policy.py`: versioned gate policy and policy hashing.
- `identity/design_manifest.py`: perturbation design manifest support.
- `identity/canonical_scope.py` / `identity/scope.py`: canonical scope comparison.
- `evidence/registry.py`: append-only evidence registry and registrar implementations.
- `resolver/resolver.py`: claim-conditioned decision logic.
- `resolver/warrant.py`: shared strength/warrant helpers.
- `render/renderer.py`: controlled report text generation.

## Claude Runtime Integration

Source directory:

```text
src/pertura_runtime/claude/
```

Important files:

- `cli.py`: `pertura-claude` CLI entry.
- `prompt.py`: runtime prompt and evidence workflow instructions.
- `finalizer.py`: decision-aware finalization priority.
- `manifest.py`: run manifest and analysis state manifest.
- `mcp_server.py`: MCP server assembly.
- `tools/evidence_tools.py`: Claude-visible evidence MCP tool surface.

## Benchmark Surface

Source directory:

```text
src/pertura_bench/
```

Important files:

- `p07_harness.py`: strong-baseline P0.7 harness.
- `surface_eval.py`: deterministic benchmark-only overclaim evaluator.

## Test Surface

Important tests:

- `tests/gate/test_claim_resolver.py`: core resolver and lattice behavior.
- `tests/gate/test_artifact_strength.py`: artifact strength and renderer behavior.
- `tests/gate/test_identity_manifest.py`: canonical UID parsing and manifest behavior.
- `tests/gate/test_import_boundaries.py`: trust-domain import boundaries.
- `tests/runtime/test_claude_evidence_tools.py`: MCP registrar behavior and returned templates.
- `tests/runtime/test_final_surface.py`: runtime final and evidence report behavior.
- `tests/bench/test_p07_gate_utility.py`: strong-baseline P0.7 harness and surface evaluator.

## Finalization Priority

The runtime finalizer should prefer claim-conditioned surfaces:

1. Use an existing claim-calibrated report if present.
2. If claims and registry exist, rerender the evidence report.
3. If claim decisions exist, render a decision table/report.
4. Fall back to artifact-only evidence summary only when no claim path exists.

This prevents script-style outputs from becoming the scientific final when a decision surface exists.

## Analysis State Manifest

Each run writes an analysis state manifest under:

```text
artifacts/analysis_state_manifest.json
```

This is an audit and continuation object. It records registry paths, artifact IDs, decision IDs, policy hash, report path, and generated outputs. It is not itself evidence and must not increase claim strength.