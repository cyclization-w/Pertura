# Pertura Claim Verification

Pertura's public product is a Perturb-seq native analysis agent. Its reviewer
surface still exposes three runtime claims that make that product auditable:

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
  crossing blocked nodes. In the product UI, this backs the Perturb-seq Flow
  and structured clarification actions.
- `observation_memory`: repeated scientific observations are grouped by
  variable and expose conflict, coverage, divergence, method, branch, and
  intent context. In the product UI, this backs the Evidence Board and report
  preview.
- `deliberative_audit`: missing capability declarations are blocked, evidence
  chain review verifies successful support, and `plan_rethinking` exposes a
  trace/repair action menu for questionable results. In the product UI, this
  backs audited repair and live agent status.

## Capsule Verification

```bash
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --verify --json
```

Capsule verification ties the event log, stored snapshot, graph projection,
integrity hashes, audit results, and claim matrix together for review.
