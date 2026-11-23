# C.genes: The genes each target changes

**The biology.** The genes each target changes. Most published screens test a target's cells against the control
cells, most often with a Wilcoxon test; fewer sum the cells by replicate (pseudobulk). Some put the lane, batch, donor
or experiment in the test, and some set a minimum of 5 to 400 cells per guide or target. By default this module's test
follows the design (`auto`). Where 3 or more biological replicates (Pertura's default, `min_replicates`) hold the
target's and the control cells, it runs pseudobulk by replicate. Otherwise it runs Wilcoxon on the cells, the field's
most common test, against the control cells of the target's own samples, in its mix (`wilcoxon_same_samples`): from
each sample holding the target's cells, that sample's control cells in proportion to the target's cells there. A
target whose cells sit in other samples than the control cells' would otherwise read the samples' differences as its
genes. On a public screen whose captures hold different libraries (CRISPRai), Wilcoxon against every control cell gave
15,532 discoveries in 4 control-guide groups; against the control cells of the same samples it gave 3, 12, 0 and 3
(Pertura's measurement). In one sample the two are the same test. A run can name another: Wilcoxon against every
control cell (`wilcoxon`), or Seurat's LR with the sample as a covariate. Targets with 10 cells or more are tested by
default (`min_cells`); a target the run names is tested wherever a test can be computed, with a note. A calibration
check runs groups of control guides through the same test, and what a target "changes" is read against what they give
(Pertura's choice).

Any of the field's tests can be named in place of the default: wilcoxon_same_samples, wilcoxon, ttest, mast or
seurat_lr on cells; edger_ql, deseq2, pydeseq2, limma_voom or glmgampoi on pseudobulk; or sceptre. For reproducing a
paper, the module also keeps seurat3_lr (Seurat 3's test, with groups, reference labels, a covariate and thresholds as
the paper states them; `E.combinations` reads its table for the regulatory modes), field (Seurat's FindMarkers with
MAST, at Seurat's own adjusted p) and rank_test (a rank test within each sample, Pertura's own). A named method runs
wherever it can be computed, and the result notes how it differs from the default (a test over 2 replicates where the
default needs 3, for example). The numbers change for one run with `policy`, or in the notebook with `pt.policy(...)`.

**The measurement.** Per target, the changed genes (q < 0.05 by default, `q`; an effect-size cut beside it where one
is set, `min_effect`), up and down, with the test and the reason for it. The calibration's discoveries. Each target's
own target gene as the positive control.

**Named genes.** Genes named by the question (`genes`) are read in every target, from this run's test or from an
earlier run's (`de`), with no new test. They are drawn as a heatmap, and the first three as boxes of their expression
per target, the control cells on top. These are the plots papers draw for cytokines, identity genes or a pathway's
members. With `genes` targets_own, each target's own target gene is read in the other targets' cells. The changes form the
targets' network, drawn as arrows. A target's target gene in its own cells is its knockdown (B.efficacy), not an arrow.

## Ways the result can mislead

### cells as the observations can give excess false positives (Squair et al., Nat Commun 2021; Barry et al., Genome Biology 2024)

- **Shows in:** `leads` (the test used and its reason), `replicates`; `calibration` (the control groups' discoveries)
- **Read:** U1, PF2, R1, ES2
- **Do:** where the design allows, pseudobulk by replicate takes the replicates as the units. Elsewhere the test on
  cells compares each target with the control cells of its own samples, a finding's checks run pseudobulk by sample,
  and the calibration (Pertura's choice). In vivo, a mouse's cells are not independent. In an in vivo T cell screen
  a test on cells stayed inflated with the sample as a covariate and against the control cells of the same mice;
  pseudobulk by mouse was clean (Pertura's measurement).

### a target whose cells sit in other samples than the control cells' reads the samples' differences as its genes (Barry et al., Genome Biology 2024)

- **Shows in:** `leads` (the test used and its reason); `calibration` (each test's control groups' discoveries); the
  same top genes across the targets of one sample
- **Read:** PF14, U3, U1, PF2
- **Do:** the test on cells compares each target with the control cells of its own samples, in its mix
  (`wilcoxon_same_samples`, the default there). Where control guides sit unevenly over the samples, read Wilcoxon
  against every control cell against its own control-guide groups before using it (U3).

### many targets change the same genes, a response most perturbations share

- **Shows in:** not in the result yet: the genes most targets change (`res.shared()` in the notebook)
- **Read:** R3, PF9, PF10
- **Do:** `D.common_response` sets the shared part aside. The claim can then be narrowed to what is specific, or the
  shared response recorded as a result of its own.

### large fold changes of genes the control cells barely express rest on low counts

- **Shows in:** not in the result yet: the detection rate of the changed genes
- **Read:** PF15, ES3
- **Do:** a detection filter fixed before the test, the same for every group, keeps such genes from leading. Their
  fold changes are best shrunk, or set apart, before genes are ranked by effect (PF15).

## Moves it calls for

- **T2:** worth considering when the control-guide groups give discoveries
- **X1:** worth considering when 3 or more targets change genes (3 is Pertura's default)
- **O1:** worth considering when 3 or more targets change genes (3 is Pertura's default)
- **O4:** worth considering when 3 or more targets change genes (3 is Pertura's default)

## Next steps

- The moves above.
- P6.4 (a pair's changed genes against its singles').
