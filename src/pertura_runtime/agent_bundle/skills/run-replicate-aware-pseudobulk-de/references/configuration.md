# Template Configuration

## Materialization

Pass one JSON file to `materialize_pseudobulk.py` with:

- `input_h5ad`, `output_counts`, and `output_samples`;
- `unit_column`, `condition_column`, and optional `target_column`;
- optional `metadata_tsv`, `selection_tsv`, `cell_id_column`, `layer`, and `gene_column`.

The script writes a gene-by-sample integer table and a sample manifest. It rejects missing joins, duplicate identities, noninteger counts, and groups with zero cells.

Omit `gene_column` when the frozen protocol binds gene identity to `adata.var_names`. Do not switch to a display-symbol column, suffix duplicate symbols, or otherwise create identifiers. Set `gene_column` only when the frozen protocol explicitly names a unique `adata.var` column.

Example using arbitrary column names:

```json
{
  "input_h5ad": "/registered/input.h5ad",
  "metadata_tsv": "/registered/metadata.tsv",
  "selection_tsv": "/registered/evaluation.cells.tsv.gz",
  "cell_id_column": "cell_id",
  "selection_cell_id_column": "cell_id",
  "unit_column": "subject",
  "condition_column": "arm",
  "output_counts": "outputs/tasks/TASK/pseudobulk_counts.tsv",
  "output_samples": "outputs/tasks/TASK/sample_manifest.tsv",
  "output_accounting": "outputs/tasks/TASK/pseudobulk_accounting.json"
}
```

## edgeR QL

Pass one JSON file to `run_edger_ql.R` with:

- `mode`: `single` or `per_target`;
- `counts_tsv` or `counts_mtx` plus `genes_tsv`;
- `samples_tsv`, `output_dir`, `unit_column`, `condition_column`, `baseline`, and the explicit boolean `robust` copied from the frozen protocol;
- for `single`, `target`; for `per_target`, `target_column`, `control_label`, and optional `eligibility_tsv`;
- optional `full_gene_output`.

The sample table must contain `sample_id`. Per-target mode requires at least two paired units for every eligible target. Output filenames come from the task output contract; rename only within the current task directory when the generic defaults differ.

Example for a registered per-target pseudobulk input:

```json
{
  "mode": "per_target",
  "counts_tsv": "/registered/pseudobulk_counts.tsv",
  "samples_tsv": "/registered/sample_manifest.tsv",
  "eligibility_tsv": "/registered/eligibility.tsv",
  "output_dir": "outputs/tasks/TASK",
  "unit_column": "replicate",
  "condition_column": "condition",
  "target_column": "perturbation",
  "baseline": "control",
  "control_label": "control",
  "full_gene_output": true,
  "robust": true
}
```

The example value matches the generic per-target reference protocol. Other frozen tasks may require `false`; never infer or substitute this value.
