# Smoke 11: Cell State Reference Capability Summary

Status: passed.

Run path:

```text
.claude_runs/claude_20260706_211214_350df677
```

This smoke verified the `cell_state_reference` Evidence-Aware Stage Catalog path on a synthetic AnnData fixture. It is a capability smoke, not only a boundary smoke.

## Registered Artifact

```text
artifact_id = cell_state_reference_384949db1765
kind = cell_state_reference
evidence_class = observed_metadata
artifact_roles = scope_definition, state_context
artifact_intrinsic_ceiling = observation
```

## Generated Outputs

```text
outputs/processed_state_reference.h5ad
outputs/run_state_reference.py
outputs/state_markers.csv
outputs/state_reference_summary.json
artifacts/evidence_artifacts.jsonl
artifacts/analysis_state_manifest.json
reports/evidence_report.md
```

## Capability Covered

The run produced structured state-reference metadata for:

- normalize_total and log1p preprocessing
- HVG metadata
- PCA
- neighbors
- UMAP
- Leiden clustering
- marker analysis
- cluster-to-state annotation mapping
- processed h5ad handoff

## Boundary Result

The stage produced no ClaimDecision objects and no measured perturbation-effect artifact. The registered artifact remained observed metadata and state context only.

## Non-Blocking Follow-Ups

- Preserve structured embedding metadata rather than stringifying dicts in the MCP registrar result.
- For real datasets, require annotation provenance and avoid expanding biological state names from model memory.