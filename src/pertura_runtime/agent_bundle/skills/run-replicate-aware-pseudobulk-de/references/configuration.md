# Template Configuration

## Materialization

Pass one JSON file to `materialize_pseudobulk.py` with:

- `input_h5ad`, `output_counts`, and `output_samples`;
- `unit_column`, `condition_column`, and optional `target_column`;
- optional `metadata_tsv`, `selection_tsv`, `cell_id_column`, `layer`, and `gene_column`.

The script writes a gene-by-sample integer table and a sample manifest. It rejects missing joins, duplicate identities, noninteger counts, and groups with zero cells.

## edgeR QL

Pass one JSON file to `run_edger_ql.R` with:

- `mode`: `single` or `per_target`;
- `counts_tsv` or `counts_mtx` plus `genes_tsv`;
- `samples_tsv`, `output_dir`, `unit_column`, `condition_column`, and `baseline`;
- for `single`, `target`; for `per_target`, `target_column`, `control_label`, and optional `eligibility_tsv`;
- optional `full_gene_output` and `robust`.

The sample table must contain `sample_id`. Per-target mode requires at least two paired units for every eligible target. Output filenames come from the task output contract; rename only within the current task directory when the generic defaults differ.
