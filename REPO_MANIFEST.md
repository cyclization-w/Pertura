# Repository manifest

This repository root is the authoritative Pertura source checkout. The enclosing `New project` directory is not a Pertura release root.

## Versioned product inputs

- `src/pertura_core/`: frozen contracts, canonical scope, promotion policy, and receipt verification.
- `src/pertura_workflow/`: capability specifications, planners, validators, scientific runners, and environment profiles.
- `src/pertura_runtime/`: product CLI, provider-neutral five-tool handlers, shared agent skills, Claude/OpenAI adapters, authority/session store, and dashboard bundle.
- `src/pertura_bench/`: benchmark schemas, case specifications, protocol runner, metrics, and server-plan export.
- `compatibility/v0.2/`: repository compatibility snapshots; packaged mirror under `src/pertura_core/compatibility/v0.2/`.
- `benchmarks/`: small manifests, schemas, license notes, and deterministic goldens only.
- `tests/`, `scripts/`, `ui/`, and `docs/`: validation, maintenance, dashboard source, and documentation.

`src/pertura_gate/` and legacy stage/evidence modules are retained for read-only import compatibility and an independent regression lane. They are not part of the default product orchestration path.

## Generated but versioned

- `src/pertura_runtime/dashboard_static/`
- `src/pertura_core/compatibility/v0.2/`
- `src/pertura_bench/schemas/`
- deterministic benchmark golden verdicts
- `src/pertura_runtime/agent_bundle/bundle.json` with canonical skill content hashes

These outputs require drift checks against their source generators.

## Never committed

- runtime workspaces and authority databases;
- `build/`, `dist/`, wheels, egg-info, caches, coverage output, and `node_modules/`;
- Micromamba environments or downloaded binaries;
- real H5AD/HDF5/RDS data, converted benchmark subsets, and local cache sidecars;
- absolute local paths, secrets, private keys, and API credentials.

## Release identity

A server benchmark must bind a clean Git commit, built wheel hash, capability/case-spec hashes, environment locks, and dataset artifact locks. Synthetic verdicts and published-proxy labels cannot make a capability trusted or make the release ready.
