# Pertura Claim Verification

Pertura v2 exposes three reviewer-facing claims:

```text
analysis_graph       user-editable analysis graph + gate
observation_memory   scientific observation memory
deliberative_audit   audited LLM commit + trace-driven rethinking
```

## Commands

```bash
pertura claims --json
python -m pertura.claim_tests --json
python -m pertura.claim_tests --claim analysis_graph
python -m pertura.claim_tests --claim observation_memory
python -m pertura.claim_tests --claim deliberative_audit
python tests/test_harness.py
python -m pytest
```

`pertura claims --json` prints package-safe module commands and source-tree
commands. The package-safe runner is:

```bash
python -m pertura.claim_tests --json
```

## What Each Claim Checks

- `analysis_graph`: editable graph specs audit cleanly, C-tier gates block
  missing design authority, and interrupts are opened instead of silently
  crossing blocked nodes.
- `observation_memory`: repeated scientific observations are grouped by
  variable and expose conflict, coverage, divergence, method, branch, and
  intent context.
- `deliberative_audit`: missing capability declarations are blocked, evidence
  chain review verifies successful support, and `plan_rethinking` exposes a
  trace/repair action menu for questionable results.

## Capsule Verification

```bash
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --verify --json
```

Capsule verification ties the event log, stored snapshot, graph projection,
integrity hashes, audit results, and claim matrix together for review.
