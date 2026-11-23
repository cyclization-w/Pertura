# Units, replicates, batches and covariates

## U1. Replicates behind the cells

- **If you see:** many significant genes in a cell-level test, more with more cells, and units (mice, donors) that
  differ from each other, so a control-vs-control comparison also gives hits.
- **Consider:** summing counts per real experimental unit (pseudobulk by mouse or donor) and testing across units.
  Random pseudo-replicates (cells split at random into groups) are not units.
- **It acts on:** the unit and the estimator. Say what a pseudobulk sums over.
- **Read it as:** judge the choice by the null. Control groups, or control guides tested as targets, should give few
  hits, as in Zhu et al. 2026, Alda-Catalinas et al. 2024 and Schnitzler et al. 2024. In the source, a bulk method
  run on cells lost its advantage, and so did pseudobulks of random cell groups. Only real replicates restored it.
  Lanes of one pool are technical replicates, not separate animals.
- **Stop or turn when:** consider reporting the result as a description of the pooled cells when the biological
  replicates cannot be told apart (animals pooled before sequencing). Say so, with the n. Do not present technical
  splits as biological replicates.
- **Source:** *Confronting false discoveries in single-cell differential expression*, Nat Commun
  2021, doi:10.1038/s41467-021-25960-2. Pertura's choice: pooled animals reported as pooled cells.

## U2. Cells nested in samples, samples in studies

- **If you see:** a design with more than one level (cells within mice, mice within experiments or batches).
- **Consider:** naming the levels before choosing a method:
  - a simple design suits pseudobulk with a count model;
  - a nested one, a mixed model;
  - a small gene set, a permutation over the right units.
- **It acts on:** the correlation structure and the estimator. A method's name is not a design.
- **Read it as:** benchmark recommendations are priors for the method, not proof on this screen. A permutation is
  valid only over exchangeable units. Total cells are not biological power.
- **Stop or turn when:** consider closing the question as `cannot_answer` when the design is fully confounded (a
  condition is all in one batch). No method recovers that.
- **Source:** *Single-cell differential expression analysis between conditions within nested
  settings*, Brief Bioinform 2025, doi:10.1093/bib/bbaf397. Pertura's choice: full confounding is `cannot_answer`.

## U3. A batch term, and why it is there

- **If you see:** control guides spread unevenly over batches, lanes or replicates, with expression differences
  between those batches.
- **Consider:** a covariate for that batch, only for a confounder fixed before the perturbation (assignment to
  lane, sequencing run). Check its effect on control-vs-control comparisons first.
- **It acts on:** the covariates. Say which kind of confounding you suspect.
- **Read it as:** a control guide's null result is a diagnostic, not proof that nothing is confounded. In the
  low-MOI test paper, adding the replicate improved calibration but did not make it perfect. A permutation-based
  test was used beside it.
- **Stop or turn when:** consider `cannot_answer`, or a new question at the level where they overlap, when perturbed and
  control cells do not overlap in the batch the comparison needs (no positivity). The comparison is then not
  identifiable.
- **Sources:**
  - *Causal effect estimation from trans-regulatory single-cell CRISPR screens*, Cell Genomics
    2026, doi:10.1016/j.xgen.2026.101251;
  - *Robust differential expression testing for single-cell CRISPR screens at low multiplicity of
    infection*, Genome Biol 2024, doi:10.1186/s13059-024-03254-2;
  - Pertura's choice: no overlap is `cannot_answer`.

## U4. Covariates computed from expression

- **If you see:** a division-phase score, a state score or a response score that differs between target and
  controls, and a wish to adjust for it.
- **Consider:** first whether the score is upstream or downstream of the perturbation.
  - Downstream, it may be a mediator or a collider. Adjusting for it changes the question: the effect beyond that
    state, not the total effect.
  - The division phase can be noise or biology.
- **It acts on:** the covariates and the estimand.
- **Read it as:** decide from what the question asks, not from a principal component's correlation. Report which
  effect the adjusted model estimates.
- **Stop or turn when:** consider a new question, with its own population and comparison, when the
  question becomes the effect within a state.
- **Sources:**
  - *Causal effect estimation from trans-regulatory single-cell CRISPR screens*, Cell Genomics 2026,
    doi:10.1016/j.xgen.2026.101251;
  - *Best practices for single-cell analysis across modalities*, Nat Rev Genet 2023,
    doi:10.1038/s41576-023-00586-w;
  - Pertura's choice: a within-state effect is a new thread.

## U5. In vivo, many cells from few clones

- **If you see:** an in vivo screen where a guide's cells may come from few transduced clones (clonal expansion),
  so recovered cells and reads overstate the independent clones.
- **Consider:** estimating coverage from barcode or clone occupancy where the deposit has it. Keep clones, guides,
  pools, mice and technical lanes apart.
- **It acts on:** the coverage and the unit.
- **Read it as:** many recovered cells can come from a handful of clones, so one clone can carry a whole effect.
  Technical lanes do not add biological replicates. A barcode-level test alone gives no inference across mice.
- **Stop or turn when:** consider reporting the design's limit (`not_enough_data`) when clone coverage is too low for
  the question. Do not use technical lanes to raise the n.
- **Source:** *Framework for in vivo T cell screens*, J Exp Med 2024, doi:10.1084/jem.20230699. Pertura's choice: too
  few clones is `not_enough_data`.

## U6. Nuisance variation that control cells share

- **If you see:** variation that control cells show as well as perturbed ones (replicate, division phase, stress),
  blurring the perturbation's signal.
- **Consider:** a local adjustment against control cells, for example subtracting the mean expression of each
  cell's nearest control cells. Then check apart whether the perturbation changes the shares of those states, and
  how the result depends on the adjustment's settings.
- **It acts on:** the expression signal (a nuisance adjustment), with composition checked apart.
- **Read it as:** remove a variation only when it is not the perturbation's effect. Check the shares, and vary the
  settings. The adjustment would partly erase a perturbation that changes cell division.
- **Stop or turn when:** consider the composition as a finding of its own when the perturbation shifts
  the shares of the adjusted states.
- **Source:** *Characterizing the molecular regulation of inhibitory immune checkpoints with multimodal single-cell
  screens*, Nat Genet 2021, doi:10.1038/s41588-021-00778-2. Pertura's choice: shifted shares are their own thread.

## U7. Normalisation to each cell's total

- **If you see:** a target whose cells have more or fewer UMIs than the control cells, or many genes moving both ways
  after normalisation, where the perturbation could change how much RNA a cell makes.
- **Consider:** what the normalisation measures. Scaling each cell to its total gives a gene's share of the cell's
  RNA, and removes a difference in total RNA along with the depth. Spike-ins added in proportion to the cells, or
  cell counts, measure total RNA, with caveats of their own.
- **It acts on:** the quantity measured: a share of the cell's RNA, not an amount per cell.
- **Read it as:**
  - the scaling factors cannot tell technical biases from real differences in total mRNA content, and UMIs do not
    correct for a cell's mRNA content (the first source);
  - in bulk data, cells with high c-Myc made 2 to 3 times more RNA. Normalised to equal totals, some genes seemed to
    rise and others to fall; normalised to cell number, over 90% of the genes rose (the second source);
  - matching depth, or depth as a covariate, estimates the effect beyond total RNA. Where the perturbation moves
    total RNA, that is a changed question.
- **Stop or turn when:** consider reporting the UMIs per cell beside the effect, with both readings open, when the
  screen has no spike-ins or cell counts.
- **Sources:**
  - *Normalizing single-cell RNA sequencing data: challenges and opportunities*, Nat Methods 2017,
    doi:10.1038/nmeth.4292;
  - *Revisiting global gene expression analysis*, Cell 2012, doi:10.1016/j.cell.2012.10.012;
  - Pertura's choice: UMIs per cell reported beside the effect.
