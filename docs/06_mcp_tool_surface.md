# 06. Five-tool product surface

Pertura exposes five high-level domain tools. They bound formal scientific execution and reporting; they do not replace Claude CodeAct.

```text
mcp__pertura__inspect_dataset
mcp__pertura__run_diagnostic
mcp__pertura__run_analysis
mcp__pertura__evaluate_virtual_model
mcp__pertura__finalize_report
```

Claude may still use `Read`, `Glob`, `Grep`, `Bash`, `Write`, `Edit`, and notebook tools for exploration. Results produced only through free CodeAct remain exploratory unless a registered capability validates and commits them.

## Tool responsibilities

### `inspect_dataset`

Creates a new versioned `DatasetContract`, records observed/inferred/confirmed/unresolved design fields, and recommends the next capability. It does not run a scientific analysis.

### `run_diagnostic`

Executes one intake, guide/QC, design-balance, or target-reliability diagnostic through the capability registry and controlled runtime. It returns compact IDs, status, blockers/cautions, summaries, and output paths.

### `run_analysis`

Routes an objective, or validates an explicitly requested capability, through the single product planner. The planner uses confirmed design facts, committed diagnostics, environment availability, and method constraints. Missing dependencies block; the runtime does not silently fall back to a weaker method.

### `evaluate_virtual_model`

Reserves the v0.2 virtual-evaluation surface. Until a bundled evaluator is implemented and benchmarked, it returns an explicit out-of-scope/not-implemented result and grants no claim permission.

### `finalize_report`

Reads committed results and authority-session records, verifies receipts and current dependencies, applies the frozen promotion policy, and renders trusted and exploratory sections separately. It does not register new evidence or re-sign historical results.

## Dependency inputs

Tool schemas retain an optional `dependencies` array for v0.2 compatibility. Callers may provide result IDs only as disambiguation hints. The runtime reconstructs dependency kind, hash, scope, status, trust, and stale state from its own commit store; caller-supplied authority fields cannot upgrade a result.

## Return shape

Domain tools return compact structured payloads. Large tables are written as JSON/Parquet and plots as PNG/SVG. A typical execution response includes:

```json
{
  "result_id": "result-...",
  "receipt_id": null,
  "status": "completed_with_caution",
  "blockers": [],
  "cautions": ["synthetic-only exploratory capability"],
  "summary": "Candidate analysis completed.",
  "output_paths": ["artifacts/..."],
  "scope_id": "scope-..."
}
```

A null receipt indicates a validated-untrusted candidate result. Such a result may enter the dependency DAG but cannot support a strong measured statement.

## Compatibility boundary

The tool names and JSON contracts are frozen under `compatibility/v0.2/tool-surface.json`. Adding an exploratory capability with no claim permission does not add another MCP tool.

The former registrar/evidence MCP surface is retained only for legacy regression tests. See [the historical registrar tool document](legacy/06_registrar_tool_surface.md).
