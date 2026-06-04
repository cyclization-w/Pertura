# Pertura v2 Harness Review Issues

> Review date: 2026-06-03
>
> Scope: `pertura_v2` research artifact, CLI, run capsule, tool permissions,
> event store, kernel runtime, reporting, tests, packaging, and public claims.
>
> Use this file as the repair backlog. The goal is to move Pertura v2 from a
> strong research prototype / reviewable architecture to a reviewer-safe,
> installable, cross-platform harness artifact.

## Current High-Level Judgment

Update 2026-06-04: the original P0/P1 reviewer backlog below has been
addressed in the local source-tree artifact. Pertura v2 now exposes a coherent
reviewer surface around analysis graph gates, capability contracts, observation
memory, evidence-chain audit, trace-driven rethinking, replay/fork/diff,
capsule export, and claim-specific smoke tests. The remaining gaps are no
longer local harness semantics; they are clean-environment / server / real-data
validation tasks that need optional extras, API keys, Docker/server policy, and
Perturb-seq dependencies.

The full script harness passes locally:

```powershell
python tests\test_harness.py
# Results: 454/454 passed
```

The wheel-safe claim runner also passes:

```powershell
python -m pertura.claim_tests --json
# 18/18 passed
```

The standard pytest reviewer wrapper now collects and runs:

```powershell
python -m pytest -q
# 3 passed
```

## Verification Commands Already Run

```powershell
python -m pytest --collect-only -q
# 3 tests collected

python -m pytest -q
# 3 passed

python tests\test_harness.py
# 454/454 passed

python -m pertura._cli claims --json
# emits package-safe python -m pertura.claim_tests commands and command arrays

python -m pertura.claim_tests --json
# 18/18 passed

python -m pertura._cli doctor
# core/cli/kernel/notebook OK; optional llm/server/perturbseq extras missing
```

Original spot checks are now covered by regression checks:

- `KernelSession.execute()` after `x=123` records `kernel_state.variables`.
- HTML report escaping/offline safety is tested with malicious `<script>` input.
- `tool_schemas(readonly=True)` excludes `execute_code`, `search_web`, and
  `view_plot`, while local audit/rethinking tools remain visible.

## Resolution Audit: 2026-06-04

The original tables below are retained as historical reviewer notes. Current
local resolution status:

| ID | Current Status | Verification |
| --- | --- | --- |
| P0-01 | Resolved locally | Kernel state regression proves persistent variables are visible. |
| P0-02 | Resolved locally | Offline-safe HTML and XSS regression pass. |
| P0-03 | Resolved locally | `pertura claims --json` emits module commands and command arrays. |
| P0-04 | Resolved for review install path | `review` extra includes kernel/notebook dependencies; `doctor` reports exact missing extras. Clean venv install still belongs to external CI. |
| P0-05 | Resolved locally | `python -m pertura.claim_tests --json` is package-safe and passes. |
| P0-06 | Resolved locally | Missing hard completion blocks `complete_node`; no `node_completed` is emitted. |
| P0-07 | Resolved locally | Missing selected capability blocks `execute_code`. |
| P0-08 | Resolved locally | Tool tiers are `local_read`, `external_read`, `execute`, `state_change`, `privileged`; read-only schema is local-only. |
| P1-01 | Resolved locally | Covered by P0-01. |
| P1-02 | Resolved locally | `python -m pytest -q` collects and runs wrapper tests. |
| P1-03 | Partially resolved / deployment-bound | AST safety is advisory; subprocess/docker isolation and file/network policy exist, but final bio Docker image/policy must be validated on server. |
| P1-04 | Resolved locally | `Store.append()` validates event schema by default; unsafe append is explicit. |
| P1-05 | Resolved locally | Projection metadata stores UTC timestamp and projection hashes. |
| P1-06 | Resolved locally | Read-only schema excludes execution and external-read tools. |
| P1-07 | Resolved locally | Known events are either reduced into state or treated as audited/log-only runtime events. |
| P1-08 | Resolved locally | Persistent job lifecycle and cancellation/stale behavior are covered in harness tests. |
| P1-09 | Resolved locally | Store writes use `BEGIN IMMEDIATE` and projection hash checks. |
| P1-10 | Resolved locally | Weak behavior tests were flipped to blocking semantics. |
| P1-11 | Resolved locally | Unknown event and invalid first event are rejected. |
| P1-12 | Resolved locally | Kernel profile/runtime/cache are redirected into run-scoped paths; remaining Jupyter platform warning is environmental. |

Remaining validation beyond local source-tree work:

- Clean venv / wheel / sdist install smoke for `.[review]` and `.[all]`
  on a server with network access. Local wheel build passed on 2026-06-04 via
  `python -m pip wheel . -w dist`; dependency install was deferred because
  network package installation was not allowed in the current sandbox. See
  `INSTALL.md`.
- FastAPI/GUI run with `.[server]`.
- OpenAI/Anthropic provider run with real API keys.
- Perturb-seq run with `scanpy` / bio stack installed on a real workspace.
- Server Docker image and final bio-tool embedding policy.

## Original P0: Must Fix Before Reviewer Delivery

| ID | Status | Issue | Evidence | Suggested Fix |
| --- | --- | --- | --- | --- |
| P0-01 | Exists | Kernel state is silently empty. `_get_kernel_state()` never produces a usable dashboard snapshot. | `pertura/kernel/session.py`: outer module lacks `import json`; `_execute_sync("_get_kernel_state()")` reads only `stdout`, but a bare expression in Jupyter returns `execute_result`, which `_execute_sync` ignores. | Add outer `import json`; either call `print(_get_kernel_state())` or capture `execute_result` / `display_data`; fail loudly or record a warning when state capture fails. Add a regression test asserting `x=123` appears in `kernel_state.variables`. |
| P0-02 | Exists | HTML report claims self-contained but loads CDN assets and directly injects unescaped text. | `pertura/reporting.py:136-193` loads cytoscape/dagre from CDN and interpolates report fields into HTML. | Either remove the self-contained claim or vendor/embed assets; escape all text with `html.escape`; serialize data via safe JSON script tags. Add XSS regression with `<script>` in goal/narrative/observation values. |
| P0-03 | Exists | `pertura claims --json` emits Windows-only backslash paths. | `pertura/core/claims.py` hardcodes `tests\\test_claim_*.py`; `_cli.py` hardcodes `python tests\\test_claim_segments.py --json`. | Emit structured command arrays or POSIX-neutral paths using `PurePosixPath`; include both `module_command` and `source_tree_command`. |
| P0-04 | Mostly Exists | `pip install .[cli]` does not install kernel deps even though default run sandbox is `kernel`. | `pyproject.toml`: `cli = ["rich"]`, `kernel = ["jupyter_client", "ipykernel"]`; `_cli.py` defaults sandbox to `kernel`. | Include kernel deps in `cli`, change default sandbox to `subprocess`, or make `pertura run` preflight fail with a clear install hint before constructing runtime. Test in clean venv. |
| P0-05 | Exists | Wheel likely excludes `tests/`, but capsule/claim `independent_command` points at `tests\...`. | `pyproject.toml` only packages `pertura*`; claim manifest points to source-tree tests. | Package claim tests as `pertura.claim_tests`, expose `python -m pertura.claim_tests ...`, or mark commands as source-tree-only and provide wheel-safe verification commands. |
| P0-06 | Exists | Completion gates default to warning and can still complete nodes. | `ConditionSpec.failure_mode = "warn"`; `GateEvaluator` maps ordinary completion failure to `warn`; `_dispatch_complete_node` records warning then emits `node_completed`. | Make completion failures block by default when `hard=True`; reserve `warn` for explicitly soft conditions. Add tests that missing hard completion cannot complete node. |
| P0-07 | Exists | Missing `capability_ids` only warns but still executes/plans attempt. | `gated_dispatch._check_execute_allowed()` emits `missing_capability_declaration` warning; `tests/test_harness.py` treats this as pass. | For nodes with allowed capabilities, require a declared capability unless the node explicitly allows free-form execution. Update test expectation from `planned_attempt` to `blocked`. |
| P0-08 | Exists | Read-tier tools include external network and execution affordances. | `permissions.py` marks `search_web` and `view_plot` as `read`; `tool_schemas(readonly=True)` includes `execute_code`; `search_web` uses OpenAI web search; `view_plot` can call VLM. | Split tiers into `local_read`, `external_read`, `execute`, `state_change`; make `readonly=True` local-only by default; add policy/approval for external calls. |

## Original P1: Engineering Quality / Credibility

| ID | Status | Issue | Evidence | Suggested Fix |
| --- | --- | --- | --- | --- |
| P1-01 | Exists | `kernel/session.py` lacks outer `import json`. | `json.loads(...)` is used after state capture. | Add import and regression test. This overlaps P0-01. |
| P1-02 | Exists | Standard pytest collects zero tests. | `python -m pytest -q` reports no tests collected. | Convert script checks to pytest functions or add pytest wrapper tests that call harness segments. |
| P1-03 | Exists | AST safety checks are bypassable. | Alias import, `getattr(os, "system")`, and `Path.write_text()` all returned no violations. | Treat AST check as advisory lint only; strengthen checks and enforce real sandbox/file/network policy. |
| P1-04 | Exists | `Store.append()` is public and bypasses schema/controller validation. | `Store` is exported from `pertura.core`; `GraphController` validates, but raw `Store.append()` does not. Raw unknown events can be appended. | Make raw append private/internal or add validation in `Store.append(validate=True)` with an explicit unsafe escape hatch for replay/fork internals. |
| P1-05 | Exists | `snapshots.updated` and `graph.updated` store `run_id` instead of timestamp. | `store.py` writes `snap.run_id` to both `updated` columns. | Store event timestamp or current UTC timestamp; include projection version/hash if useful. |
| P1-06 | Partially Exists | `tool_schemas(readonly=True)` still contains `execute_code`. | Confirmed by runtime spot check; docstring says execute tools are always included. | Rename option to `exclude_state_change` or actually enforce read-only by excluding execute tools. |
| P1-07 | Exists | Some emitted/known event types are not reduced into snapshot state. | `event_schema.py` knows `analysis_spec_compiled`, `node_transition_requested`, `safety_violation_recorded`, `attempt_soft_timeout`; reducer does not handle them directly. | Either handle them, record them as findings/runtime status, or explicitly mark as log-only in schema and audit. |
| P1-08 | Exists | Lease has TTL but no stale process/heartbeat detection. | `Store.acquire_lease()` only checks expiry and owner. | Add heartbeat/owner PID metadata and stale owner cleanup; use SQLite transaction lock where possible. |
| P1-09 | Partially Exists | Event/snapshot/graph projection writes are not fully concurrency safe. | Writes happen in one SQLite connection context, so not completely separate transactions. But snapshot is read/reduced before the write transaction, so concurrent writers can stale-project. | Use `BEGIN IMMEDIATE`; read current snapshot/events and write projections inside the same transaction; add concurrency test. |
| P1-10 | Exists | Test suite encodes weak behavior as success. | `execute_code without capability declaration warns only` is a passing check. | Change tests to reflect desired harness guarantees, not current weak behavior. |
| P1-11 | Exists | Raw append can silently accept unknown event types and produce a snapshot with empty run metadata. | Spot check: `Store.append([Event(event_type="unknown_unvalidated", ...)])` succeeded. | Same as P1-04; add schema validation and require first event to be valid `run_started` unless replay/fork mode. |
| P1-12 | Exists | Jupyter kernel startup can write to user-level IPython history/security paths. | Spot check produced IPython permission/history warnings. | Configure kernel profile/history/cache into run dir or temp dir; avoid writing outside review artifact paths. |

## P2: Open-Source Hygiene / Packaging

| ID | Status | Issue | Evidence | Suggested Fix |
| --- | --- | --- | --- | --- |
| P2-01 | Exists | Naming residue: `Petura`, `blackboard`, `BLACKBOARD_*`. | `pertura/spec/models.py` docstring says `Petura v2`; `tests/test_harness.py` says blackboard; config fallback uses `.blackboard`; env fallbacks use `BLACKBOARD_*`. | Standardize public text on `Pertura`; keep legacy fallbacks only if documented as migration compatibility. |
| P2-02 | Mostly Not Found | Mojibake in UTF-8 file contents was not confirmed. | `rg` did not find `鈥`, `鈹`, replacement chars in relevant files; terminal output mojibake was display encoding. | Do not treat as source bug unless future scans find actual mojibake. |
| P2-03 | Exists | No physical `LICENSE` file. | Root of `pertura_v2` has no `LICENSE`; pyproject has license text only. | Add `LICENSE` file matching MIT. |
| P2-04 | Exists | Version/classifier mismatch. | `version = "1.0.0"` but classifier is `Development Status :: 3 - Alpha`. | Use `0.x` while alpha, or change classifier only after P0/P1 are addressed. |
| P2-05 | Exists | Public `__init__.py` docstring is stale. | It still describes agenda/rubric style and "SOP-graph" rather than current analysis graph/capability contract thesis. | Rewrite public API docstring around `AnalysisGraph`, `Capability`, `Workbench`, `audit_analysis_graph`. |
| P2-06 | Exists | `Domain` docstring says "the only extension point", now false. | `pertura/domain/base.py`. | Update to mention analysis graph, capabilities, tools, policies, condition compiler, and report/audit surfaces. |
| P2-07 | Exists | README uses Windows paths in commands. | Commands such as `runs\...`, `python tests\...`, `examples\...`. | Prefer forward slashes or show platform-specific examples. |
| P2-08 | Exists | Generated artifacts are present in source tree. | `runs/`, `.pytest_cache/`, many `__pycache__/` directories exist under `pertura_v2`. `.gitignore` excludes them, but working tree contains them. | Remove generated artifacts from source distribution and repository state; keep only fixtures required by tests. |
| P2-09 | Exists | README mixes research claims, engineering guarantees, CLI commands, and operator commands. | README sections put claim manifest, capsule verification, runtime workflow, and quickstart together. | Split into `README`, `CLAIMS.md`, `INSTALL.md`, `OPERATOR_GUIDE.md`, and `REVIEWER_CHECKLIST.md` or clearly label claim types. |
| P2-10 | Exists | Package name differs from import/CLI naming. | Project name is `pertura-agent`; import is `pertura`; CLI is `pertura`. | Decide whether distribution should be `pertura` / `pertura-agent`; document mapping. |
| P2-11 | Exists | Optional extras are fragmented in a way that can surprise users. | `cli`, `kernel`, `notebook`, `perturbseq`, `server` are separate, while default commands may need several. | Add clear extras: `review`, `runtime`, `all`; make `doctor` suggest the exact extra for intended command. |
| P2-12 | Exists | Capsule references physical test scripts but does not verify they exist. | Capsule claim check uses `independent_command`; no packaging/runtime existence check. | Add capsule command validation or package-safe module commands. |

## Corrections To The Original Issue List

Some original issue descriptions are directionally right but should be worded
more precisely before using them in a review response:

- `Store.append()` event/snapshot/graph writes are not completely outside one
  transaction; the write statements share a SQLite connection context. The
  real issue is validation bypass plus stale projection risk because the
  snapshot is computed before entering the write transaction.
- Mojibake appears to be mostly terminal encoding, not confirmed source file
  corruption.
- `pip install .[cli]` crash should be verified in a clean venv. Static config
  strongly suggests the problem because default sandbox is `kernel` and kernel
  deps are outside `cli`.

## Additional Harness Gaps Beyond The 30 Issues

These are not just cleanup; they define what a credible scientific agent
harness should guarantee.

### 1. Enforcement Contract

The artifact needs a clear table of guarantees:

- What is hard-blocked before execution?
- What is advisory and merely recorded?
- What is blocked before node completion?
- What is blocked before final report / conclusion?
- Which guarantees hold in source tree, wheel install, and replay/capsule mode?

Without this, reviewers can reasonably interpret warnings as failed guardrails.

### 2. Tool Permission Model

The current three-tier model is too coarse. Recommended tiers:

- `local_read`: inspect local run/workspace state only.
- `external_read`: network/VLM/web/search calls.
- `execute`: code/kernel/subprocess/container execution.
- `state_change`: graph/run/event mutations.

Then `readonly=True` should mean `local_read` only unless explicitly requested.

### 3. Real Sandbox Boundary

AST safety should not be the primary security boundary. A reviewer-safe harness
needs:

- File write allowlist enforced outside Python source inspection.
- Network allow/deny policy.
- Secret redaction for env vars and config files.
- Container/subprocess isolation documented by backend.
- Clear statement that Jupyter kernel mode is convenience, not secure sandbox.

### 4. Installable Reviewer Capsule

The reviewer should be able to install a wheel and run:

```powershell
pertura claims --json
python -m pertura.claim_tests --json
pertura capsule <run_dir> --verify --json
```

Those commands should not depend on an unpacked source tree unless explicitly
documented.

### 5. CI Matrix

Minimum CI gates:

- `python -m pytest` collects and runs tests.
- `python tests/test_harness.py` still works if kept as script harness.
- `pip install .[review]` then `pertura claims --json`.
- Wheel/sdist build and install smoke.
- Windows and Linux path compatibility.
- No generated `runs/`, `.pytest_cache/`, `__pycache__` in sdist/wheel.

### 6. Event Store Conformance

Need a small conformance suite for:

- First event must initialize a valid run.
- Unknown event types are rejected.
- Entity payloads are schema-valid.
- Reducer handles or explicitly ignores every known event type.
- Replay hash is stable.
- Concurrent append does not corrupt projections.

### 7. Report Safety

Generated reports are review artifacts. They need:

- Offline mode or explicit online dependency label.
- Escaped HTML.
- Stable JSON report.
- Hashes tying report to event log/capsule.
- Clear warning if report has unresolved audit errors.

## Recommended Repair Order

1. Fix claim/packaging path surfaces: `claims`, capsule `independent_command`,
   wheel-safe tests.
2. Harden gates: completion failures and missing capability declarations should
   block by default.
3. Split tool permissions and remove `execute_code` / external tools from true
   read-only schema.
4. Fix kernel state capture and add regression test.
5. Add pytest wrappers so `python -m pytest` works.
6. Add Store validation or make raw append private.
7. Make HTML report offline-safe or honestly label it online; escape all fields.
8. Clean open-source hygiene: LICENSE, version/classifier, stale names, README
   path style, generated artifacts.
9. Add clean venv install smoke for `.[cli]`, `.[kernel]`, and future
   `.[review]`.

## Definition Of Done For "Qualified Harness"

A qualified Pertura v2 harness should satisfy all of the following:

- Source-tree tests and standard pytest both pass.
- Wheel install exposes all advertised reviewer commands.
- Hard gates actually block unsafe/incomplete commits.
- Read-only tools cannot execute code or make network calls unless explicitly
  elevated.
- Kernel/runtime dashboard reflects real persistent variables.
- Event log, snapshot, graph, report, and capsule can be replay-verified.
- Generated report is safe to open offline.
- Public README claims match actual runtime behavior.
- A clean reviewer machine can reproduce the three core claims without knowing
  internal project layout.
