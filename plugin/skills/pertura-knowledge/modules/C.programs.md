# C.programs: The gene programs each target moves

**The biology.** Coordinated gene programs, and each target's effect on each. A program is a fixed set scored per
cell, or a program found in the cells (cNMF, NMF). The fixed sets come from:
- any MSigDB collection by its name (Hallmark, immune signatures, cell types and the rest), the sets chosen by name or
  by a word in their names;
- the control cells' own contrast between two levels of a column, built on the whole screen: its genes up and its
  genes down, as published screens build an activation score from the control cells' stimulated against resting
  cells and read every target on it;
- the user's lists.

The proteins the screen measured are tested the same way. Their antibody counts are normalised per cell (CLR), as
Seurat's multimodal vignette does. So is any per-cell value, a cell column or a table the notebook saved, as it is.
Published screens score
fixed or chosen sets per cell with scanpy's score_genes, Seurat's AddModuleScore, UCell, VISION or a mean. Others find
programs in the cells with cNMF, NMF, CoGAPS or WGCNA. Some test a program's usage against the control cells with the
batch or lane, the genes detected or the mitochondrial share as covariates. GeneNMF is offered beside cNMF and NMF
(Pertura's choice).

**The measurement.** Per target and program: the difference from the control cells, q-values, and the control-guide
groups' effect sizes (each program's 95th percentile), per sample. By default (`test` auto) the difference is tested by
a linear model with the sample as a covariate where the cells span several samples, else by Wilcoxon; `test`
linear_model or wilcoxon names either.

## Ways the result can mislead

### a program shift shared by every target (a common response)

- **Shows in:** `reference` (other_targets reads each program against the other targets' cells)
- **Read:** R3, PF9, PF10
- **Do:** `D.common_response`, and the program read against the other targets, show the part every target
  shares.

### a program found on the data it is tested on looks stronger than it is

- **Shows in:** `gene_sets` (fixed or found); `calibration` (the control groups' 95th percentiles)
- **Read:** PG2, PG6, R1
- **Do:** the calibration on control-guide groups shows what the same test gives with no effect; Schnitzler et al.
  2024 tested control cells against control cells and found no regulator. Published screens find programs on the
  screen's cells and test them there, with covariates. Fixed gene sets, or programs fitted on the control cells only
  (fit_on controls), are the alternatives (Pertura's choice).

### a program of one sample or capture found as biology

- **Shows in:** `gene_sets` (genenmf_meta keeps the programs that recur across samples)
- **Read:** PG6, U3
- **Do:** programs that follow the batch or lane are usually set aside; Schnitzler et al. 2024 dropped 10 cNMF
  components as likely batch effects. genenmf_meta, which keeps the programs that recur across samples, is another
  way (Pertura's choice).

## Further reading

Before naming a program, PG4, PG6 and X1 are worth reading. `G.meaning` on its top genes against the detected genes,
with the field's sets or one's own (`gene_lists`), gives a name that rests on more than a few genes.

## Next steps

- O2 (regulators of a program), O3 (`E.program_axis`), O6 (`E.co_functional_sets`).
- P1.1, P1.3. The step that would name a program from its top genes is missing (P1.2).
- P3.8: the functions each cell state carries.
