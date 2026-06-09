# Pertura Operator Guide

This guide is for running, observing, auditing, replaying, and sharing Pertura
runs.

## Product Surfaces

Pertura has one canonical product UI:

```bash
pertura --GUI --domain perturbseq
```

That command serves the built-in dependency-free HTML workbench. It is the
default for `--ui builtin` and `--ui auto`; stale React builds under
`frontend/dist` do not replace it.

Use the terminal surface for SSH and CI smoke checks:

```bash
pertura chat ./data --domain perturbseq
pertura inspect runs/run_YYYYMMDD_HHMMSS_xxxxxx
pertura context runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura audit runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
```

React/Vite under `frontend/` is experimental source code. It is not kept in
lock step with the HTML workbench and only runs when explicitly requested with
`--ui react`.

## Running On A Server

For DeepSeek or another OpenAI-compatible endpoint, keep the key in the
environment and pass the endpoint/model explicitly:

```bash
export OPENAI_API_KEY="..."
pertura --GUI --domain perturbseq \
  --provider openai \
  --base-url https://api.deepseek.com \
  --model deepseek-v4-flash
```

On a trusted server, add `--host 0.0.0.0`. Do not expose the workbench publicly
without an external auth/proxy layer.

## Workbench Contract

The GUI and terminal surfaces consume the same first-screen projection:

```text
GET /api/workbench-view
```

The product-first fields are:

- `perturbseq`: Design Ledger, active stage, flow, capability cards, quality
  flags, evidence board, branch board, and product timeline.
- `execution_state`: run mode, current question, issues, candidate actions,
  and navigation state.
- `candidate_actions`: the action strip used by the console.
- `activity.product_events`: timeline cards for planning, code execution,
  artifacts, questions, repair, and completion.
- `artifacts` and `report`: user-visible run outputs.

Debug/operator fields such as `analysis`, `review`, and `agent_context` remain
available, but they should live behind inspector panels.

Use this endpoint to verify the served UI contract:

```text
GET /api/ui-info
```

It reports the requested UI mode, effective UI, canonical UI, primary
projection, console route, SSE route, and Workflow Builder route.

## Console Input

All first-screen user input goes through:

```text
POST /api/console/turn
```

This route is a state router, not durable chat history:

- no run: `workspace + message` starts an agent run
- open interrupt: `answers` or `message` resolves the interrupt
- design question action: structured answers update the Design Ledger
- ready/paused run: a new goal is recorded and the agent continues
- running run: no duplicate job is started; live status is returned
- complete run: report requests generate a report, otherwise actions are shown

## Live Events

The workbench receives event-backed SSE from:

```text
GET /api/events/stream
```

The stream emits `runtime_event`, `product_event`, and `jobs`. The main
timeline should prefer product events; raw runtime/debug events belong in the
inspector.

## Workflow Builder

Workflow editing is current-run scoped:

```text
GET  /api/workflow-builder
POST /api/workflow-builder/draft
POST /api/workflow-builder/apply
POST /api/workflow-builder/reset
```

Drafts are event-backed. Applying a draft validates it, records an apply event,
and emits an explicit `node_entered` event when the active node must reset.

Runtime autopilot handles normal progress: if the current node is complete and
exactly one forward successor is ready, it completes/transitions without asking
the LLM. If several successors are ready, it completes the current node and
opens a structured choose-next interrupt.

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
