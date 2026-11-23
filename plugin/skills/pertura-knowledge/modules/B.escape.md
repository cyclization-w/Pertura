# B.escape: Perturbed and non-perturbed cells within a target's cells

**The biology.** Within a target's cells, some cells were changed by the perturbation and some were not (mosaic
editing, in-frame edits, weak knockdown in some cells). Mixscape (Papalexi et al. 2021) calls each cell perturbed or
non-perturbed against its nearest control cells; Mixscale gives a continuous score. Some published screens use or
extend Mixscape, and those that say what they do with the non-perturbed cells discard them. Most screens do not use
it. The two populations answer different questions. All cells show what the perturbation does to the cells that
received its guide. The perturbed cells show what it does in the cells it changed, which is a selection on an outcome
of the perturbation (PF17). Each result says which population it uses. By default the analyses read all of a
target's cells, and the perturbed cells this module calls are an option (Pertura's choice).

**The measurement.** Mixscape's class per cell (pertpy's Mixscape) and the share perturbed per target, or the target
gene's detection per sample. The same call runs on control-guide groups as calibration. With Mixscale, the table
`graded` gives per target whether its score follows its target gene within its cells: Spearman's rho of the score
with the gene's log-normalised expression, the gene's mean in each third of the scores, and the Benjamini-Hochberg q
over the targets, as published screens ask whether a downstream change scales with the on-target change.

## Ways the result can mislead

### mosaic editing and transcript escape both keep the target gene detected

- **Shows in:** the result table's `share_perturbed` per target; `calibration` (the control groups' share)
- **Read:** S3, PF17
- **Do:** all of a target's cells are read by default. Mixscape's perturbed cells are an option, kept as a cell set
  that says so (`pt.save_cells`); each result says which population it uses (PF17).

### perturbation_driven_clusters (Pertura's own method): whether a cluster holds its groups in equal representation is a judgement

- **Shows in:** `method` (the readings, run as variants: rule, factor, resolution)
- **Read:** PG5, S2
- **Do:** running the readings as variants shows what each changes, for the report to say.

## Next steps

- S1 (a graded response: `pt.response(method="mixscale")`), L5 (uneven response).
- P3.2: the perturbed cells before a perturbation's shares of the states are read.
