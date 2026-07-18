# Null Calibration Configuration

Pass one JSON file to `run_paired_label_null.R` with:

- `counts_tsv`, `samples_tsv`, and `output_path`;
- `unit_column`, `condition_column`, `baseline`, and `target`;
- the explicit boolean `robust` copied from the frozen primary-fit protocol;
- optional `details_path`.

The counts table is gene-by-sample with the first column containing gene IDs. The sample table contains `sample_id` plus the declared unit and condition columns. The script aligns samples by ID, checks exact pairing and design rank, sorts unit IDs, and writes the required four-column calibration table.

Use pseudobulk counts materialized from the registered calibration selection, never the evaluation selection. Example with arbitrary column names:

```json
{
  "counts_tsv": "outputs/tasks/TASK/calibration_pseudobulk_counts.tsv",
  "samples_tsv": "outputs/tasks/TASK/calibration_sample_manifest.tsv",
  "output_path": "outputs/tasks/TASK/null_calibration.tsv",
  "unit_column": "subject",
  "condition_column": "arm",
  "baseline": "vehicle",
  "target": "drug",
  "robust": false
}
```

The example value illustrates a frozen `robust=false` primary fit. Null calibration must use the exact same value as that fit.
