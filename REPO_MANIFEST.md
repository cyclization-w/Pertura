# Repository manifest

This repository root is the authoritative Pertura checkout. Its enclosing New project directory is not a Pertura release root.

## Versioned product inputs

- src/pertura_core/: frozen contracts, canonical scope, promotion policy, and v0.2 compatibility mirror.
- src/pertura_workflow/: capability specifications, planner, dependency policies, validators, scientific runners, and environment profiles.
- src/pertura_runtime/: projects, assets, turns, provider-neutral five-tool handlers, authority sessions, adapters, skills, and dashboard bundle.
- src/pertura_bench/: scientific/agent schemas, cases, execution harnesses, metrics, catalogs, and server-plan export.
- compatibility/v0.2/: generated repository compatibility snapshots.
- benchmarks/: small manifests, licenses, schemas, and deterministic goldens.
- tests/, scripts/, ui/, and docs/: active validation and product documentation.
- legacy/: non-packaged historical code, tests, prompts, and documents.

## Generated but versioned

- src/pertura_runtime/dashboard_static/;
- src/pertura_core/compatibility/v0.2/;
- src/pertura_bench/schemas/;
- synthetic capability and local agent verdicts;
- src/pertura_runtime/agent_bundle/bundle.json.

All generated resources have drift checks.

## Never committed

- runtime workspaces and authority databases;
- build/dist trees, wheels, egg-info, caches, coverage output, and node_modules;
- Micromamba environments or downloaded binaries;
- real H5AD/HDF5/RDS data, converted subsets, and local cache sidecars;
- absolute local paths, secrets, private keys, and provider session credentials.

## Authority and release identity

The active scientific path is:

    ResultEnvelope -> pertura_core.promotion -> TurnFinal / report

A formal server run binds a clean Git commit, wheel hash, capability/spec and parameter hashes, environment locks, dataset locks, three external catalog hashes, and case/plan hashes. Synthetic verdicts, published-proxy labels, or reported-only metrics cannot make a capability trusted or make the release ready.