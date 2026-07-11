# Repository tree

This is the intended `0.2.0a5` capability-first layout. It is a boundary map, not an exhaustive generated file listing.

```text
pertura/
|-- pyproject.toml
|-- MANIFEST.in
|-- README.md
|-- compatibility/v0.2/          # generated repository snapshots
|-- benchmarks/                  # manifests, license notes, small goldens
|-- docs/
|   |-- README.md                # current documentation entry
|   |-- 14_capability_first_product_architecture.md
|   |-- 15_v020_implementation_status.md
|   |-- benchmark_design.md
|   `-- legacy/README.md         # navigation to historical material
|-- scripts/                     # freeze, version, benchmark maintenance
|-- src/
|   |-- pertura_core/            # contracts, scope, policy, verification
|   |   `-- compatibility/v0.2/  # wheel-packaged snapshot mirror
|   |-- pertura_workflow/        # capabilities, planner, runners, envs
|   |-- pertura_runtime/         # product CLI, five tools, sessions, UI API
|   |   |-- product_tools/      # provider-neutral definitions and handlers
|   |   |-- agent_bundle/       # four shared skills and Claude plugin manifest
|   |   |-- adapters/           # provider contracts and OpenAI skeleton
|   |   `-- dashboard_static/   # generated dashboard bundle
|   |-- pertura_bench/           # cases, schemas, runners, server plans
|   `-- pertura_gate/            # legacy compatibility/regression only
|-- tests/
|   |-- core/
|   |-- compatibility/
|   |-- runtime/
|   |-- workflow/
|   |-- bench/
|   `-- gate/                    # legacy regression lane
`-- ui/                          # React/Vite dashboard source and lockfile
```

## Product dependency direction

```text
pertura_core      -> no runtime/workflow/bench dependency
pertura_workflow  -> pertura_core
pertura_runtime   -> pertura_core + pertura_workflow
pertura_bench     -> product path through explicit maintainer adapters
pertura_gate      -> compatibility only; never imported by default product startup
```

## Local-only outputs

Runtime runs, reports, artifacts, authority databases, real datasets, benchmark caches, optional environments, build trees, wheels, and `node_modules` are ignored. CI and packaging checks verify that none enter a release artifact.
