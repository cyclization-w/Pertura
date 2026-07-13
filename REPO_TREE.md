# Repository tree

This is the intended 0.2.0a11 pre-benchmark layout. It is a boundary map, not an exhaustive generated file list.

    pertura/
    |-- pyproject.toml
    |-- MANIFEST.in
    |-- README.md
    |-- compatibility/v0.2/          # generated public snapshots
    |-- benchmarks/                  # source manifests, licenses, small goldens
    |-- docs/                        # capability-first active documentation only
    |-- legacy/                      # excluded evidence/registrar/stage archive
    |   |-- src/
    |   |-- tests/
    |   |-- docs/
    |   `-- pytest.ini
    |-- scripts/                     # active freeze, audit, benchmark tools
    |-- src/
    |   |-- pertura_core/            # frozen contracts, scope, promotion
    |   |-- pertura_workflow/        # capabilities, planner, runners, envs
    |   |-- pertura_runtime/         # projects, authority, five tools, adapters, UI
    |   `-- pertura_bench/           # cases, schemas, metrics, server plans
    |-- tests/                       # default capability-first product tests
    |-- ui/                          # React/Vite dashboard source
    |-- REPO_TREE.md
    `-- REPO_MANIFEST.md

## Active dependency direction

    pertura_core      -> no runtime/workflow/bench dependency
    pertura_workflow  -> pertura_core
    pertura_runtime   -> pertura_core + pertura_workflow
    pertura_bench     -> explicit product/maintainer interfaces

There is no active bridge to legacy/. The archive is excluded from wheel/sdist and runs only under its explicit legacy test lane.

## Local-only outputs

Runs, reports, authority databases, real datasets, benchmark caches, scientific environments, build artifacts, wheels, and node_modules are ignored and must not enter a release artifact.