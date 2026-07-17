# Null Calibration Configuration

Pass one JSON file to `run_paired_label_null.R` with:

- `counts_tsv`, `samples_tsv`, and `output_path`;
- `unit_column`, `condition_column`, `baseline`, and `target`;
- optional `robust` and `details_path`.

The counts table is gene-by-sample with the first column containing gene IDs. The sample table contains `sample_id` plus the declared unit and condition columns. The script aligns samples by ID, checks exact pairing and design rank, sorts unit IDs, and writes the required four-column calibration table.
