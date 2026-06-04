# Pertura Install And Server Smoke

This guide verifies that Pertura works outside the source-tree development
environment. Run it on a server or clean machine with network access.

## Build Artifacts

```bash
cd pertura_v2
python -m pip install -U pip build
python -m build
```

Expected artifacts:

```text
dist/pertura_agent-0.2.0-py3-none-any.whl
dist/pertura_agent-0.2.0.tar.gz
```

## Clean Reviewer Install

```bash
python -m venv /tmp/pertura_smoke
/tmp/pertura_smoke/bin/python -m pip install "dist/pertura_agent-0.2.0-py3-none-any.whl[review]"
/tmp/pertura_smoke/bin/pertura doctor
/tmp/pertura_smoke/bin/pertura claims --json
/tmp/pertura_smoke/bin/python -m pertura.claim_tests --json
```

Windows equivalent:

```powershell
python -m venv C:\tmp\pertura_smoke
C:\tmp\pertura_smoke\Scripts\python.exe -m pip install "dist\pertura_agent-0.2.0-py3-none-any.whl[review]"
C:\tmp\pertura_smoke\Scripts\pertura.exe doctor
C:\tmp\pertura_smoke\Scripts\pertura.exe claims --json
C:\tmp\pertura_smoke\Scripts\python.exe -m pertura.claim_tests --json
```

Expected:

- `doctor` reports `core`, `cli`, `kernel`, and `notebook` as OK.
- `pertura claims --json` returns package-safe `python -m pertura.claim_tests` commands.
- `python -m pertura.claim_tests --json` passes all claim checks.

## Server / GUI Install

```bash
/tmp/pertura_smoke/bin/python -m pip install "dist/pertura_agent-0.2.0-py3-none-any.whl[server]"
/tmp/pertura_smoke/bin/pertura doctor
```

Expected:

- `server` reports OK.
- `pertura serve` can start a FastAPI/GUI process for a prepared run.

## Perturb-seq Install

```bash
/tmp/pertura_smoke/bin/python -m pip install "dist/pertura_agent-0.2.0-py3-none-any.whl[perturbseq]"
/tmp/pertura_smoke/bin/pertura doctor
```

Expected:

- `perturbseq` reports OK.
- `scanpy`, `anndata`, and the scientific stack import successfully.

## Real Data Smoke

Use a small matrix-level workspace first. Do not start with a huge production
dataset.

```bash
/tmp/pertura_smoke/bin/pertura init /path/to/workspace
/tmp/pertura_smoke/bin/pertura spec audit --domain perturbseq --json
/tmp/pertura_smoke/bin/pertura run /path/to/workspace --goal "Analyze this perturb-seq dataset" --steps 3
/tmp/pertura_smoke/bin/pertura context runs/<run_id> --json
/tmp/pertura_smoke/bin/pertura audit runs/<run_id> --json
```

The first real-data target is not biological completeness. It is verifying
that the harness can:

- inspect the workspace,
- enter an analysis node,
- select a capability,
- execute a notebook attempt,
- register observations/artifacts,
- expose audit/rethinking context.

## Current Local Status

Local source-tree validation on 2026-06-04:

- Wheel build: passed via `python -m pip wheel . -w dist`.
- Source-tree harness: `459/459 passed`.
- Pytest wrapper: `3 passed`.
- Claim runner: `18/18 passed`.
- Clean dependency install: pending server validation because local network
  install was not allowed in the current sandbox.
