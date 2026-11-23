# F.replicates_agree: Agreement between samples

**The biology.** The agreement of the units (mice, donors, captures) on the targets' profiles. Agreement between
biological replicates is reproducibility. Agreement between captures of one pool is a technical check only.

**The measurement.** Per pair of samples, the correlation of every target's profile; a profile in a sample needs 30
cells by default (`min_cells_profile`). Per target and pair, its correlation. Whether the samples are biological or
technical.

## Ways the result can mislead

### targets without an effect pull the screen's r down, and one strong target can carry it

- **Shows in:** the result table's `r` per target, beside `median_r`
- **Read:** PF16, ES1
- **Do:** best read beside `C.genes`' targets that change genes.

### samples of different libraries share few targets

- **Shows in:** `pairs` (the targets each pair of samples shares)
- **Read:** U2
- **Do:** the shared targets are worth stating.

## Further reading

U1, PF1, V4.

## Next steps

- Per-target agreement; pseudobulk by replicate where the replicates are biological.
