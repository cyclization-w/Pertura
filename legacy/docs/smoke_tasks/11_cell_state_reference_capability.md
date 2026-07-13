# Smoke 11: Cell State Reference Capability

Purpose: verify that `--stage cell_state_reference` can run on a real AnnData object, produce a structured state-reference summary, register it as state context, and stop without perturbation-effect claims.

## Fixture

Create the synthetic fixture:

```powershell
Set-Location <path-to-pertura-repo>
python scripts\make_synthetic_state_reference_fixture.py --out fixtures\synthetic_state_reference
```

Expected files:

```text
fixtures/synthetic_state_reference/synthetic_state_reference.h5ad
fixtures/synthetic_state_reference/fixture_manifest.json
```

## Claude Smoke Command

```powershell
Set-Location <path-to-pertura-repo>
pertura-claude `
  --stage cell_state_reference `
  --input "fixtures\synthetic_state_reference" `
  --interaction-mode benchmark `
  --max-turns 50 `
  --task "Run only the cell_state_reference stage on the provided synthetic AnnData fixture. Inspect the h5ad file, create outputs/state_reference_summary.json with preprocessing, PCA/neighbors/UMAP/clustering, marker, and annotation metadata when available, register it with register_cell_state_reference_artifact, and stop. Do not run perturbation effect analysis, do not evaluate claims, and do not write scientific conclusions."
```

If the console entry point is unavailable, use:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pertura_runtime.claude.cli `
  --stage cell_state_reference `
  --input "fixtures\synthetic_state_reference" `
  --interaction-mode benchmark `
  --max-turns 50 `
  --task "Run only the cell_state_reference stage on the provided synthetic AnnData fixture. Inspect the h5ad file, create outputs/state_reference_summary.json with preprocessing, PCA/neighbors/UMAP/clustering, marker, and annotation metadata when available, register it with register_cell_state_reference_artifact, and stop. Do not run perturbation effect analysis, do not evaluate claims, and do not write scientific conclusions."
```

## Acceptance

The run should produce:

```text
outputs/state_reference_summary.json
artifacts/evidence_artifacts.jsonl
artifacts/analysis_state_manifest.json
```

The registered artifact should have:

```text
kind = cell_state_reference
evidence_class = observed_metadata
artifact_intrinsic_ceiling = observation
artifact_roles include scope_definition and state_context
```

The summary should mention state-reference methods such as PCA, neighbors, UMAP, clustering, markers, or annotation metadata. It must not create claim decisions or measured perturbation effect evidence.