# Pertura Operator Guide

This guide is for running, auditing, replaying, and sharing Pertura runs.

## Run And Inspect

```bash
pertura run ./data --goal "Analyze this perturb-seq dataset"
pertura serve
pertura context runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura audit runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
```

`pertura context` returns the compact LLM/operator dashboard. `pertura audit`
returns the full deterministic run audit.

## Trace And Rethink

```bash
pertura evidence runs/run_YYYYMMDD_HHMMSS_xxxxxx con_123 --json
pertura trace runs/run_YYYYMMDD_HHMMSS_xxxxxx con_123 --json
pertura rethink runs/run_YYYYMMDD_HHMMSS_xxxxxx con_123 --issue "stale support" --json
```

- `evidence` checks whether a conclusion or observation has verified support.
- `trace` expands upstream/downstream derivation paths.
- `rethink` turns failed, stale, weak, suspicious, or unsupported results into
  a compact repair/branch/intervention plan.

## Replay, Fork, Diff

```bash
pertura inspect runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura replay runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura fork runs/run_YYYYMMDD_HHMMSS_xxxxxx EVENT_ID --json
pertura diff runs/run_A runs/run_B --json
```

Replay verifies that the event log rebuilds the stored snapshot and graph.
Fork and diff are intended for counterfactual analysis and parameter branches.

## Capsules

```bash
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --verify --json
```

Capsules include audit/context/provenance/replay metadata and integrity hashes.
Use `--verify` before sharing a run with reviewers.

## Tooling Notes

- `readonly=True` tool schemas expose local-read tools only.
- Web search and VLM plot inspection are external-read tools and need explicit
  provider configuration.
- Jupyter kernel mode is a convenience backend, not a security sandbox.
- Use Docker/subprocess policies for stronger isolation in server deployments.
