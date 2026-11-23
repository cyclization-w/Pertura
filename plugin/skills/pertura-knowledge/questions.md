# The questions an analysis meets

This file holds the questions an analysis of a Perturb-seq screen meets, by the step of the work where they come up.
Each question has a short answer, the papers behind it, and the entries it rests on.

- **The short answer** gives Pertura's default way, the other ways the field uses, and when to choose another. Each
  sentence ends with the ids of the entries it rests on.
- **A number in a short answer is a default, not a rule:** a test's 10 cells, a profile's 30, the 3 biological
  replicates of the design's default test, q < 0.05 for a changed row, and B.efficacy's 30% line. A run changes them
  with `policy` (a module's input) or `pt.policy(...)` in the notebook. A target or a method the run names runs wherever
  it can be computed, and its result notes how it differs from the default.
- **The sources** are a few papers, each with what it did or showed. "Pertura's choice" names a rule of Pertura's own
  that no paper gives.
- **The short answers are drafts for a person's review** (docs/adr/0033). Where an answer and an entry differ, the entry
  holds.
- **A question's id is a short word** (`units`, `calibration`). `read_knowledge` with nothing asked lists the questions
  by step. With a question's id, it gives the question and the titles of the entries it rests on, and `read_knowledge`
  with those ids gives the entries.

## The design

### question. The question of an analysis

- **Short answer:** Fix the question before the method: the cells, the reference, the readout, the replicate and the
  claim (HJ2, U2). The usual estimand is the effect of receiving a guide, not of the target gene's level (PF17, U4). The
  cells can be all of a target's cells, a subset chosen in advance or one chosen by the response; the reference can be
  the control cells of its own samples, all control cells or all other perturbed cells (S2, PF17, PF10, PF14,
  `B.escape`). Each of these, and a claim beyond a shared response, beyond total RNA or between conditions, is its own
  question (HJ2, U4, R3, C1). Each readout has its module (`C.genes`, `C.programs`, `C.any_effect`,
  `C.states_and_composition`, `B.abundance`). State what n counts (PF1, U1). Name the question as a thread;
  alternatives tried at a choice change the method, not the question (M4, HJ2).
- **Sources:** Christensen et al. 2026 (the quantity an analysis aims to estimate is its estimand; conditioning on a
  variable the perturbation changes biases the estimate); Viñas Torné et al. 2025 (control cells and all other perturbed
  cells give different readings of the same profiles); Squair et al. 2021 (a test must account for the variation between
  biological replicates); Pertura's choice: a thread holds each question while its methods change.
- **Read:** HJ2, U2, PF17, U4, S2, PF10, PF14, PF1, U1, R3, C1, M4, `B.escape`, `C.genes`, `C.programs`, `C.any_effect`,
  `C.states_and_composition`, `B.abundance`

### units. The unit an effect is tested over

- **Short answer:** Name the grouping first, such as cells within mice and mice within batches (U2). By default
  (Pertura's choice, changeable), test by pseudobulk across biological replicates where 3 or more hold both target and
  control cells, else Wilcoxon on cells against the control cells of the target's own samples; a run can put the
  sample in the test or sum by sample (U1, U5, PF2, `C.genes`).
  Other ways: a mixed model for a nested design, or a permutation over exchangeable units for a small gene set (U2).
  Lanes, captures and random splits of one pool are technical replicates, not biological ones (PF1, U1). State what n
  counts; animals pooled before sequencing give pooled cells (PF1, U1, `A.what_was_done`). Only biological replicates
  that agree show reproducibility (`F.replicates_agree`). Combine replicates' p-values by the claim: Fisher's method for
  an effect in at least one, the largest p for one in all (V4).
- **Sources:** Squair et al. 2021 (tests on cells gave false discoveries that pseudobulk by biological replicate did
  not; more replicates reduced them, more cells made them worse); Barry et al. 2024 (tests on single-cell CRISPR screens
  gave excess false positives against non-targeting guides); Milling et al. 2024 (in vivo T cell screens, where many
  recovered cells can come from a few clones); Pertura's choice: by default, pseudobulk where three replicates hold both
  sides, else Wilcoxon on cells against the control cells of the target's own samples.
- **Read:** U1, U2, U5, PF1, PF2, R1, V4, `A.what_was_done`, `F.replicates_agree`, `F.guides_agree`, `C.genes`

### batches. Batches and covariates

- **Short answer:** Consider a batch covariate where control guides are spread unevenly over batches that differ in
  expression, for a confounder fixed before the perturbation, checked first on control-against-control comparisons (U3,
  `C.genes`). Other ways: normalise each gene within a lane against its control cells, or adjust each cell against its
  nearest control cells, where the variation removed is not the perturbation's effect; then check whether the
  perturbation shifts the adjusted states, and vary the settings (PF14, U6, `C.genes`). If target and control cells sit
  in separate batches, no covariate can separate the perturbation from the batch (U3). A score computed from expression
  suits as a covariate only if the perturbation does not change it; otherwise the adjustment removes part of the effect
  (U4).
- **Sources:** Replogle et al. 2022 and Adamson et al. 2016 (each lane normalised to its own control cells); Barry et
  al. 2024 (the replicate as a covariate improved calibration without making it perfect); Christensen et al. 2026
  (adjusting for a variable downstream of the perturbation changes what is estimated); Papalexi et al. 2021 (each cell
  adjusted against its nearest control cells, with the states' shares checked apart).
- **Read:** U3, U4, U6, PF14, `C.genes`

### controls. The reference a target is compared with

- **Short answer:** By default (Pertura's choice), the reference is the cells carrying control guides from the target's
  own samples; the alternatives are all control cells pooled, the `control_cells` choice of `C.any_effect`, or all other
  perturbed cells (R5, PF14, PF10, `C.any_effect`). Against the cells of other targets, an effect means only that the
  target differs from the rest of the panel (PF10, R5). For a knockout, if the library has intergenic or safe-targeting
  guides that cut where no gene is, their cells make a reference that shares the response to cutting (R5, PF9,
  `D.cutting_response`). If the required control guides are missing, state which comparisons remain possible and which
  questions this screen cannot answer (R5).
- **Sources:** Replogle et al. 2022 and Adamson et al. 2016 (each lane normalised to its own control cells); Morgens et
  al. 2017 (cells that were cut too as the fairer control for cutting, in growth screens); Yao et al. 2023
  (safe-targeting guides "to test for general effects of genome-targeting guides"); Pertura's choice: the control cells
  of the target's own samples by default.
- **Read:** R5, PF9, PF10, PF14, `C.any_effect`, `D.cutting_response`

## Cells and guides

### guide-calls. The rule that calls each cell's guides

- **Short answer:** Calling rules (a fixed UMI count, a share within the cell, a mixture model per guide) trade purity
  against coverage: strict calling gives stronger knockdown in fewer cells, loose calling more cells with more
  contamination, and the best rule differs between screens (G1). Compare rules on cells assigned, knockdown, discoveries
  and the control guides' false discoveries, and check the chosen rule on control-against-control comparisons, not on
  hits (G1). If calls do not match the design, such as more cells with several guides than the infection rate predicts,
  find the cause before changing the calling model (G2). Calls on one or two guide UMIs may be stray guide RNA;
  `B.guide_calls_and_their_evidence` reports their share by sample, and a stricter rule can be kept as a cell set
  (`B.guide_calls_and_their_evidence`). Take processed calls as given, and say so (G1).
- **Sources:** crispat (Bioinformatics 2024: compared calling rules across screens and technologies, tuned its
  thresholds to the most significant targets, and fitted its model on the non-zero counts after diagnosing the calls);
  Pertura's choice: rules judged on control-against-control comparisons, not on hits, and calls on one or two UMIs
  reported by sample, with no threshold.
- **Read:** G1, G2, `B.guide_calls_and_their_evidence`

### vectors. Guides that travel together on one vector

- **Short answer:** If the library puts a target's guides on one vector, two guides in a cell are the design, not a
  doublet, so removing cells with two guides would remove the vector's cells (`B.vector_structure`). Confirm the vectors
  with the user in the design, then count cells and abundance per vector (`B.vector_structure`). If more cells carry
  several guides than the infection rate predicts, inspect them before changing the calling rule (G2).
- **Sources:** crispat (Bioinformatics 2024: inspected the cells with several guides and the background before changing
  its calling model); Pertura's choice: the vectors confirmed in the design, and cells counted per vector.
- **Read:** G2, `B.vector_structure`

### efficacy. Whether each guide changes its own target

- **Short answer:** Read each guide's target gene beside its effects: a target's lack of effect means little where its
  guides move nothing, and a guide short of the line can still act (`B.efficacy`, PF5). By default (`B.efficacy`'s `threshold`, changeable), the line
  is a 30% knockdown under CRISPRi and a rise by the same factor under CRISPRa; other screens use 50%, or for CRISPRa a
  significant rise or a ratio of mean UMIs (`B.efficacy`). Under knockout the mRNA can stay, so a flat target gene reads
  as "transcript unchanged"; the evidence is then a downstream signature, the protein, the edits at the cut site, or the
  guides agreeing (PF7, G3). A target gene detected in too few cells is not measurable (`B.efficacy`, ES3). The target
  gene's own change shows that the guide acts, not what the gene does (PF6). Read apart the perturbation's evidence, its
  downstream phenotype and the share of cells that respond: a guide short of the line whose phenotype its target's
  other guides share acts (T3, PF7).
- **Sources:** Replogle et al. 2022 (kept the perturbations knocked down by at least 30%); Zhu et al. 2026 (excluded 869
  inefficient guides); Tuladhar et al. 2019 (about half of CRISPR-edited cell lines made unexpected mRNAs or proteins
  from the targeted gene); Meyers et al. 2024 (a downstream signature as the evidence that a knockout guide worked);
  Pertura's choice: the 30% line as a changeable default, and under CRISPRa a rise by the same factor.
- **Read:** G3, PF5, PF6, PF7, T3, ES3, `B.efficacy`

### escape. Cells that carry a guide but did not respond (non-perturbed cells)

- **Short answer:** By default (Pertura's choice), read all the cells assigned to a target; Mixscape's perturbed cells
  from `B.escape` are an option, the one published screens take when they discard the rest (`B.escape`, S3). The two
  answer different questions, since keeping the cells that responded selects by an outcome of the perturbation; each
  result says which it reads (PF17, S2, M3). The share classed not perturbed is not the editing failure rate (S3). Where
  the response is graded, as is common under CRISPRi, a continuous score per cell such as Mixscale's fits better than a
  split; say it was learned on the same data (S1, `B.escape`). Where one guide's cells respond unevenly, read each
  cell's response against its own target-gene level (L5).
- **Sources:** Papalexi et al. 2021 (Mixscape; removed the cells it classed not perturbed, since they "dampen the
  biological effects associated with each knockout"); Saputra et al. 2025 (removed the cells Mixscape classed not
  perturbed before testing genes); Christensen et al. 2026 (conditioning on a variable the perturbation changes biases
  the estimate of its effect); Pertura's choice: all of a target's cells by default.
- **Read:** S1, S2, S3, PF17, L5, M3, P3.2, `B.escape`

### guides-agree. Whether a target's guides agree

- **Short answer:** Compare a target's guides with each other and against pairs of control guides (`F.guides_agree`,
  T1). Two independent guides that agree make an off-target effect unlikely; a guide with no knockdown dilutes the
  pooled average (`F.guides_agree`, G4). Pertura's guides check holds a finding where at least two guides go its way and
  none is significant the other way (T1, `F.guides_agree`). Where guides disagree, Pertura reads them one by one and
  keeps the disagreement; published screens drop guides by their correlation, or tier a finding by how many guides
  support it (G4, `F.guides_agree`, PF18). Never pick the guide with the largest effect, since that effect is inflated
  (G4, PF18). Guides of different targets that look alike may share a seed or off-target activity (G5).
- **Sources:** Zhu et al. 2026 (filtered on the correlation between a target's two guides); Alda-Catalinas et al. 2024
  (tiered findings by the number of guides that support them); Göring et al. 2001 (the largest of many estimates is
  inflated); Pertura's choice: two agreeing guides with none against, and the disagreement kept in the record rather
  than dropped or tiered.
- **Read:** G4, G5, PF18, T1, `F.guides_agree`

### dose. Guides of one target that differ in efficacy

- **Short answer:** Guides of one target can differ in strength, so pooling them mixes treatments; estimate the effects
  guide by guide (`B.dose`, PF8). `B.dose` gives each guide's on-target change and verdict, and names the targets whose
  guides differ in verdict (`B.dose`). By default no guide is excluded: Pertura reads such a target guide by guide and
  keeps the conflict in the record, while published screens set a minimum on-target change, or drop or tier the guides
  that disagree (`B.dose`, G4). Do not pick the strongest guide to stand for the target (G4). Guides of different
  on-target strength also form a dose series: test whether the downstream effect scales with the on-target change (C4).
- **Sources:** Christensen et al. 2026 (guides of different efficiency are different treatments; one remedy is to
  estimate the effects guide by guide); Zhu et al. 2026 (most disagreements between a target's guides came from their
  on-target knockdown); Pertura's choice: no guide excluded by default, and the conflict kept in the record.
- **Read:** G4, PF5, PF8, C4, `B.dose`

### cell-numbers. Enough cells for a target

- **Short answer:** By default (Pertura's choice, changeable), test the targets with at least 10 cells, and count how
  many that leaves; published screens set minimums from 5 to 400 cells, and some downsample to equal cells before
  comparing effect sizes (`C.genes`, `A.what_was_done`, T6). The cells per sample and kind of call are the denominator
  of every comparison (`A.what_was_done`). Few cells give noisy effects and many give tiny significant ones, so give the
  effect size and each side's cells (PF16). Read an effect in halves of the data only where they are separate data, such
  as halves of the samples or, in vitro, of the cells; in vivo a guide's cells can be clones (T6, PF3). A change not
  seen in few cells means not enough cells, not no change (`C.within_states`, PF16).
- **Sources:** Replogle et al. 2022 (a minimum of 25 cells; downsampled to equal cell numbers before comparing effect
  sizes); Nadig et al. 2025 (counts of significant genes agreed across two screens far less than an estimate of the
  transcriptome-wide effect); Pertura's choice: 10 cells per target as a changeable default, and halves of the data only
  where they are separate data.
- **Read:** PF16, PF3, T6, T7, `A.what_was_done`, `C.genes`, `C.within_states`

### abundance. A target's cell numbers as fitness

- **Short answer:** Read a target's cell numbers as fitness against the library's representation: the counts of the
  plasmid or original library, or an early time point (`B.abundance`, PF3). Without these, cell numbers are not fitness;
  ask the user for the counts, and test against them with MAGeCK (`B.abundance`). Pertura reads a guide far above its
  share of the library as clonal expansion until shown otherwise; published screens also cap a target's cells at twice
  the median over targets (PF3, `B.clonality`, `B.abundance`). `B.abundance` gives each target's cells against the
  control guides' median and range; in vivo, read `B.clonality`'s top-guide shares beside it (`B.abundance`,
  `B.clonality`).
- **Sources:** Sivakumar et al. 2025 (the cells of TP53's guides rose by clonal expansion, which the plasmid library's
  even counts showed); Dixit et al. 2016 (a guide's cells read against its share of the original library); Pertura's
  choice: a guide far above its share read as expansion until shown otherwise.
- **Read:** PF3, `B.abundance`, `B.clonality`

### clones. A guide's cells that are a few clones

- **Short answer:** In vivo, a guide's cells can descend from a few transduced cells, so one clone can carry a whole
  effect (U5, PF3). The field reads each guide's cells against its share of the library, caps a perturbation's cells at
  twice the median, or counts clones by a UMI or lineage tag in the vector (PF3, `B.clonality`). A finding seen with two
  guides is not one clone's (PF3, `F.guides_agree`). `B.clonality` gives the share of a group's cells held by its most
  common guide, with no cut-off; published screens cut at twice the median or at a knee in the vector's UMIs
  (`B.clonality`). In T cells that are not TCR-transgenic, read receptor V genes among a target's changed genes as
  clonality, and leave them out of profiles and clustering (PF4).
- **Sources:** Milling et al. 2024 (in vivo T cell screens; in TCR-transgenic cells the receptor cannot mark clones);
  Datlinger et al. 2025 (in vivo bottlenecks, clonal competition and drift; clones counted by a UMI in the vector);
  Heumos et al. 2023 (a T-cell clonotype is identified by its V genes and CDR3 sequences); Chung et al. 2026 (regressed
  the TCR genes out during clustering); Pertura's choice: the top-guide share as the measure, and receptor genes read as
  clonality.
- **Read:** U5, PF3, PF4, P3.4, `B.clonality`, `F.guides_agree`

### nearby-genes. The genes beside a target or an element

- **Short answer:** CRISPRi acts on a locus, so read the genes beside the target, and its alternative promoters, in the
  target's cells to show that they are spared (T5). Read an enhancer perturbation on the genes near it, by distance
  (C5).
- **Sources:** Pertura's choice: under CRISPRi, the neighbouring genes and alternative promoters checked in the target's
  cells.
- **Read:** T5, C5

## Effects

### any-effect. Whether a target changes the overall expression profile

- **Short answer:** By default (Pertura's choice), `C.any_effect` tests each target's whole profile by the energy
  distance from the control cells of its own samples, with a permutation test, and runs control-guide groups through it
  (`C.any_effect`, PF14, R1). Published screens also use Mixscape's classes (`B.escape`), the number of changed genes
  (`C.genes`), Hotelling's T² on principal components or a classifier, the last two outside the modules (`C.any_effect`,
  `B.escape`, `C.genes`). Cell numbers, depth or sick cells also move the whole profile, so read each distance beside
  the target's UMIs per cell against the control cells', and check its genes at matched depth (`C.any_effect`, T7, ES1).
  Where most targets differ from the control cells, test the response they share (`D.common_response`, R3). The first
  look's overview reads each target's distance without the permutation test, against control-guide groups drawn to the
  target's own number of cells, since a small group reads a larger distance from its few cells alone
  (`A.screen_overview`, PF16).
- **Sources:** Replogle et al. 2022 (ran both the energy test and the count of changed genes; the two calls agreed with
  a Jaccard index of 78.7%); Papalexi et al. 2021 (Mixscape's classes of a target's cells); Pertura's choice: the energy
  distance against the control cells of the target's own samples, and in the overview against control-guide groups of
  the target's own number of cells.
- **Read:** R1, R3, PF14, PF16, T7, ES1, `C.any_effect`, `A.screen_overview`, `B.escape`, `C.genes`, `D.common_response`

### genes. The genes a target changes

- **Short answer:** By default (Pertura's choice, changeable), `C.genes` tests targets with at least 10 cells and calls
  a gene changed at q < 0.05, with an optional effect-size cut, by the design's test: pseudobulk by replicate where 3 or
  more hold both sides, else Wilcoxon on cells against the control cells of the target's own samples (`C.genes`,
  PF2). A run can name another test: Wilcoxon against every control cell, Seurat's likelihood-ratio test with the
  sample as a covariate, MAST or the t-test on cells, edgeR-QL, DESeq2,
  pyDESeq2, limma-voom or glmGamPoi on pseudobulk, or SCEPTRE (`C.genes`). Mixscape's perturbed cells are an
  option beside all of a target's cells (`B.escape`, S3). Read the changed genes against control-guide groups
  (`C.genes`, R1). Fix a detection filter before the test, and shrink large fold changes of barely expressed genes
  before ranking (ES3, PF15). A change many targets share is not the target's own (R3, PF9).
- **Sources:** Squair et al. 2021 (pseudobulk by replicate avoids the false discoveries of tests on cells); Barry et al.
  2024 (tests on single-cell CRISPR screens gave excess false positives against non-targeting guides); Zhu, Ibrahim and
  Love 2019 (fold changes of low-count genes vary widely; shrink them); Pertura's choice: as changeable defaults, the
  design's test by the three-replicate rule, 10 cells per target and q < 0.05, and all of a target's cells.
- **Read:** PF2, PF9, PF15, ES3, R1, R3, S3, `C.genes`, `B.escape`

### calibration. Whether a test is calibrated on this screen

- **Short answer:** Before using a test on many targets, run it on groups of control guides taken as targets (R1, T2,
  `C.genes`). Other checks: a target's count of changed genes against the control groups' counts, or a QQ plot of
  targeting against non-targeting p-values (T2). If the test calls control groups too often, change the covariates or
  the QC, and check again before any discovery (R1, T2). Build the null from control guides, not from genes assumed
  unaffected, since an effect away from the target gene can be real (R2). Try a new method for the failure it addresses
  (R1). If calibration cannot be met, consider stopping inference with that test, rather than looking for one that gives
  significance (R1).
- **Sources:** Barry et al. 2024 (traced a test's failures on control-against-control pairs to few cells expressing the
  gene and a replicate confounded with guide frequency, and fixed that mechanism); Alda-Catalinas et al. 2024 (ran the
  test on cells with non-targeting guides); Zhao 2025 (QQ plots of targeting against non-targeting guides' p-values);
  Pertura's choice: no inference with an uncalibrated test.
- **Read:** R1, R2, T2, `C.genes`

### multiple-testing. The scope of multiple testing

- **Short answer:** State how many tests a result ran, which were corrected together, any selection before the test, and
  the unit of discovery; a correction controls false discoveries only among the tests it covers (`C.genes`,
  `C.any_effect`). By default, `C.genes` corrects by Benjamini-Hochberg within each target's genes, and `C.any_effect`
  across the targets it tests (`C.genes`, `C.any_effect`). `C.states_and_composition` and `C.programs` correct across
  every target and state or program they test together (`C.states_and_composition`, `C.programs`). Running `C.genes` on
  the targets `C.any_effect` calls selects targets before their genes are tested (`C.any_effect`, `C.genes`). A count of
  changed genes over the selected targets is then controlled by neither correction; report each target's discoveries
  with its family, or correct for the selection (ES4, `C.genes`, `C.any_effect`).
- **Sources:** Benjamini and Bogomolov 2014 (families of tests selected on the data need a correction for the selection
  as well); Peterson et al. 2016 (correcting within each family alone left the families with discoveries with a very
  large false discovery proportion); Pertura's choice: each module's scope of correction, stated with its result.
- **Read:** ES4, `C.genes`, `C.any_effect`, `C.states_and_composition`, `C.programs`

### shared-response. A response every perturbation shares

- **Short answer:** By default (Pertura's choice), `D.common_response` compares each target with the other targets'
  cells as well as the control cells; targets that still differ have effects of their own (`D.common_response`, PF9).
  Another baseline is the mean of all perturbed cells, Systema's reference (R3, PF9). An effect that does not exceed the
  shared shift is not specific to the target, but the shift can be real biology, so record it as the shared response
  (R3). Cas9's cuts, stress or a cell-cycle arrest can give such a response (PF9). With few targets, the control guides
  stay the main reference (`D.common_response`, PF10). Where the library has guides that cut where no gene is, measure
  the response to cutting against the non-targeting ones (`D.cutting_response`).
- **Sources:** Viñas Torné et al. 2025 (Systema: moves the reference to the average of the perturbed cells; simple
  perturbed-mean baselines matched or beat complex models); Haapaniemi et al. 2018 and Ihry et al. 2018 (Cas9's cuts set
  off a p53-dependent damage response, with selection against cells whose p53 works); Yao et al. 2023 (safe-targeting
  guides to test for general effects of genome-targeting guides); Pertura's choice: each target tested against the other
  targets' cells.
- **Read:** R3, PF9, PF10, `D.common_response`, `D.cutting_response`

### normalisation. What the expression measures, and what normalisation keeps

- **Short answer:** Scaling each cell to its total makes a value a gene's share of that cell's RNA, and removes a
  difference in total RNA along with the depth (U7). `C.genes`' edgeR-QL reads raw counts summed by sample; the other
  tests and modules read log counts per 10,000 or counts scaled to each cell's total (`C.genes`, `C.any_effect`,
  `C.states_and_composition`, `B.efficacy`). A target's different UMIs per cell can be technical or its own effect on
  total RNA; `C.any_effect` reports them as `umis_ratio` (U7, `C.any_effect`). Matching depth, or depth as a covariate,
  then asks whether an effect holds beyond total RNA (U7, T7, U4). Spike-ins added in proportion to the cells, or
  counted cells, measure total RNA; without them, report a change in UMIs per cell with both readings open (U7,
  `C.any_effect`).
- **Sources:** Vallejos et al. 2017 (scaling factors cannot tell technical biases from real differences in total mRNA
  content); Lovén et al. 2012 (cells with high c-Myc made 2 to 3 times more RNA, and normalising to equal totals hid
  it); Pertura's choice: the UMIs per cell reported beside each distance, with both readings open.
- **Read:** U7, T7, U4, `C.genes`, `C.any_effect`, `C.states_and_composition`, `B.efficacy`

### depth. An effect that follows read depth or cell state

- **Short answer:** Run an effect that could follow read depth or cell state again at matched depth, or with the depth
  or the state as a covariate (T7). Pertura's check, `res.match_depth()`, thins the deeper side's counts to the other
  side's median depth and runs the gene or score result again (T7, `C.any_effect`). Published screens instead put the
  genes detected, the mitochondrial share, the depth or the cells per perturbation in the test, downsample to equal cell
  numbers, or check for a signature of dying cells (T7, `C.any_effect`). Adjusting for a state that the perturbation
  itself changes estimates the effect beyond that state, not the total effect (U4). Counts of changed genes track cells
  and depth as well as the effect (ES1).
- **Sources:** Ota et al. 2025 (the genes detected and the mitochondrial share as covariates of the per-gene test); Zhu
  et al. 2026 (the cells per perturbation in the per-gene model); Pertura's choice: the check at matched depth.
- **Read:** T7, U4, ES1, `C.any_effect`

### effect-size. How large an effect is, beside how many genes pass

- **Short answer:** The number of genes that pass a test follows the cells and the depth as well as the effect, so it
  measures a perturbation's strength poorly (PF16, ES1). Consider instead the distribution of true effects, estimated
  from each gene's effect and its standard error (a transcriptome-wide impact); Pertura has no module for it (ES1). That
  estimate needs reliable standard errors and the right replicate units, and in vivo, resampling cells does not replace
  animals (ES1). Never choose a differential expression route because its impact comes out larger (ES1). Give every
  effect with its size and the cells behind it (PF16).
- **Sources:** Nadig et al. 2025 (for the same perturbations in two screens, counts of significant genes agreed far less
  than the transcriptome-wide impact, R² 28.4% against 59.7%); Pertura's choice: in vivo, animals rather than resampled
  cells as the units of the estimate.
- **Read:** ES1, PF16

## States

### states. Cell states, and the share of them a target's cells fill

- **Short answer:** Fit the states without the perturbation labels, then compare each target's share of each state with
  the control cells' (`C.states_and_composition`, PF11). By default (Pertura's choice, changeable), the test is
  propeller across replicates where 3 or more hold both sides, else Fisher's exact test against the control cells of the
  target's own samples; propeller by sample, scCODA, the Cochran-Mantel-Haenszel test and all control cells as the
  reference are the other choices (`C.states_and_composition`, PF14). A shift of the shares is itself an effect, and a
  change of a gene's mean can be a change of states (PF11). Rule out technical causes (guide-UMI depth, cell numbers,
  the injection, the vector) and doublets first (PF12, PF13, L2). Read the shares per guide as a check
  (`C.states_and_composition`, PF8). Cluster markers are descriptive, not independent evidence (PG5).
- **Sources:** Dixit et al. 2016 (with each cell's state in the model, some perturbations' effects turned out to be
  changes of subtype proportions); Zheng et al. 2024 (guide-UMI depth shifted a cell type's share; shares read per guide
  for the variable performance of guides); Shen et al. 2026 (injection injury and AAV transduction moved cells toward a
  less mature state); Pertura's choice: the design's test by the three-replicate rule as a changeable default, and the
  control cells of the target's own samples as the reference.
- **Read:** PF8, PF11, PF12, PF13, PF14, PG5, L2, U1, U2, P3, `C.states_and_composition`

### within-states. A change within a state, beside a shift between states

- **Short answer:** Expression within one state, the mean over all cells and the share of cells in the state are three
  different questions (HJ2). `C.within_states` tests a target's cells against the control cells within each state, which
  tells a target that moves cells between states from one that changes them within a state; adding each cell's state to
  the model is the other way (`C.within_states`, L1, PF11). With few of a target's cells in a state, no change means not
  enough cells (`C.within_states`, PF16). A change within a state can also be a finer state, so compare it with the
  state's own markers, or try a finer clustering (`C.within_states`, PG5).
- **Sources:** Dixit et al. 2016 (each cell's state in the model to recover the effects within a state); Xia et al. 2023
  (changed genes within each state beside the states' shares); Belk et al. 2022 (genes tested within each state);
  Pertura's choice: a change within a state compared with the state's markers.
- **Read:** L1, HJ2, PF11, PF16, PG5, P3.5, `C.within_states`

### cell-cycle. The cell cycle, and whether to adjust for it

- **Short answer:** `C.cell_cycle` scores each cell's phase from the S and G2/M genes and compares each target's phase
  shares with the control cells of its own samples by the design's test, as for states (`C.cell_cycle`, PF14). A target
  that stops or speeds cycling moves every proliferation-linked gene and program, so it reads as a broad effect
  (`C.cell_cycle`). Published screens often regress the cycle out; in `C.genes` the scores can enter as covariates
  (`C.cell_cycle`). First decide whether the cycling is upstream or downstream of the perturbation: adjusting for a
  downstream effect removes part of it, so the adjusted test estimates the effect beyond the cycle (U4, U6,
  `C.cell_cycle`). Cell-cycle genes respond to stress and differentiation too, so read the phases with the states
  (`C.cell_cycle`, PF11).
- **Sources:** Tirosh et al. 2016 (the S and G2/M genes that score the phase); Christensen et al. 2026 (adjusting for a
  variable downstream of the perturbation changes what is estimated); Papalexi et al. 2021 (variation that control cells
  share removed against each cell's nearest control cells); Pertura's choice: the control cells of the target's own
  samples as the reference.
- **Read:** U4, U6, PF11, PF14, `C.cell_cycle`

### trajectories. A shift along a trajectory or a pseudotime

- **Short answer:** A shift along an embedding or a pseudotime is first a change of states, a composition finding; it
  does not show a dynamic or causal process (V1). Where the topology is unknown, check the trajectory with methods of
  different assumptions, and check the kinetics behind any velocity estimate (V1). Draw each perturbation's cells as a
  density on the embedding, and read a graded change as the cells' position on a reference axis (L3, P3.7, FG1).
- **Sources:** Heumos et al. 2023 (best practices: an embedding or a pseudotime does not show a dynamic process; check
  the trajectory with methods of different assumptions); Taguchi 2025 (perturbed hepatocytes projected onto a wild-type
  time course).
- **Read:** V1, L3, P3.7, FG1

## Programs and gene sets

### programs. The gene programs a target moves

- **Short answer:** Score fixed gene sets (MSigDB Hallmark, the user's lists) or find programs by cNMF, NMF or GeneNMF,
  then read each target's effect on each against control-guide groups (`C.programs`, R1, O1). Published screens also
  score sets with UCell, VISION or a mean, and find programs with CoGAPS or WGCNA (`C.programs`). They mostly test a
  program on the cells it was found on, with batch covariates; as such a program can look stronger than it is, Pertura
  offers fixed sets or programs fitted on the control cells only (PG1, `C.programs`). Set aside programs that follow the
  batch or lane (`C.programs`). Name a program once it is stable across resamples and numbers of factors and matches an
  independent reference (PG6, PG1). When testing a gene that went into a score, leave it out of the score (PG2).
- **Sources:** Schnitzler et al. 2024 (programs found by cNMF, the components that tracked batch dropped; control cells
  tested against control cells gave no regulator); a Bayesian factor model for single-cell CRISPR screens (Nat Methods
  2023: permuting the perturbation labels showed how much the model borrowed wrongly from them); Pertura's choice:
  GeneNMF, and fixed gene sets or programs fitted on the control cells only as alternatives.
- **Read:** PG1, PG2, PG6, O1, P1, P2, R1, `C.programs`

### gene-sets. What a list of changed genes means

- **Short answer:** By default, take the genes the assay detected as the background, correct for the many sets, record
  the database's version, and use over-representation for a list and a rank-based test such as GSEA for a ranking (PG4,
  PF19, X1, `G.meaning`). Published screens also use the expressed genes, all genes, all screened genes, or the targeted
  genes for an enrichment among the targets that are hits (`G.meaning`, PF19). Test up and down genes apart
  (`G.meaning`). An enrichment names a pathway, not a mechanism in these cells: record it as prior knowledge; it does
  not by itself change a finding (PG4, `G.meaning`). Overlapping sets make one target seem to move several pathways;
  exclusive sets, labelled as such, measure something narrower (PG3). Never change the background, the database or the
  test to reach a wanted pathway (PG4).
- **Sources:** Wijesooriya et al. 2022 (across seven RNA-seq datasets, a whole-genome background gave results on average
  only 44% similar to those with the detected genes); Sivakumar et al. 2025 (the genes expressed in at least 5% of cells
  as the background); Replogle et al. 2022 (the targeted genes as the background for an enrichment among the targets
  that are hits); Pertura's choice: an enrichment recorded as prior knowledge beside the finding.
- **Read:** PG3, PG4, PF19, X1, `G.meaning`

### regulators. The regulators of a program, and the axes between programs

- **Short answer:** Each perturbation's effect on each program names the program's regulators (O2, `C.programs`). Two
  programs' effects correlated over the regulators give an axis; read it with its scatter, since a few strong targets
  can make a correlation alone, and with exclusive gene sets, since programs that share genes correlate by construction
  (O3, `E.program_axis`, PG3). `E.co_functional_sets` clusters the regulators of one program by their effects on the
  other programs into sets that act together (O6, `E.co_functional_sets`). The number of sets is a cut of the tree, so
  try other cuts and check each set's stability; a set of a few weak regulators can be noise (`E.co_functional_sets`,
  P1, PG6). A correlation nominates a regulator; its perturbation tests it (V2).
- **Sources:** Schnitzler et al. 2024 (tested control cells against control cells as targets and found no regulator);
  Pertura's choice: an axis read with its scatter and exclusive gene sets, and regulators clustered by the correlation
  of their effects on the other programs.
- **Read:** O2, O3, O6, V2, PG3, PG6, P1, `C.programs`, `E.program_axis`, `E.co_functional_sets`

## Across perturbations

### similar-targets. Targets whose profiles look alike

- **Short answer:** By default, correlate the targets' mean profiles, normalised to each sample's control cells and
  pooled over the samples, with the targeted genes left out; correlating within each sample (`profile` by_sample) is the
  other choice (`E.similar_targets`, PF14, PF6). Targets of one complex look alike, so read each pair against
  control-guide pairs and check clusters against known complexes as prior knowledge (O4, `E.similar_targets`,
  `F.guides_agree`). A shared response makes targets look alike: published screens relate only targets with a strong
  effect, and Pertura can relate those that still differ from the other targets' cells (`E.similar_targets`, ES1, R3,
  `D.common_response`). Guides of different targets that look alike may share a seed or off-target activity (G5). Read
  why two look alike from their shared genes and whether one changes the other's target gene (O5).
- **Sources:** Replogle et al. 2022 (the correlation between mean expression profiles "as a scale-invariant metric of
  similarity", the target genes masked, clusters tested against known complexes); Schnitzler et al. 2024 (placed a
  strong regulator of unknown function by its correlation with a known gene's knockdown); Zhu et al. 2026 (related only
  the perturbations with a strong effect, the on-target z-scores set to zero); Pertura's choice: each pair read against
  control-guide pairs, and similarity among the targets beyond the shared response as an option.
- **Read:** O4, O5, G5, R3, PF6, PF14, ES1, `E.similar_targets`, `F.guides_agree`, `D.common_response`

### pairs. A pair of perturbations against each alone

- **Short answer:** Read a pair where it and both its singles are in the screen, with the cells of each
  (`E.combinations`). `E.combinations` fits the pair's profile as a weighted sum of its singles' and against their plain
  sum; published screens also use per-gene interaction terms, or the average and the product of the singles
  (`E.combinations`, C2, T4). Check that each member acts in the pair's cells, since a pair with a member that does not
  act behaves like its other single (T4, `E.combinations`). Few cells limit what a fit can decide (`E.combinations`,
  PF16). Where 2 or more units hold the pair and its singles, an added check tests whether the departure agrees between
  them against control pseudo-pairs; otherwise the fit is a measurement with its cells (`E.combinations`).
- **Sources:** Norman et al. 2019 (the pair's profile fitted as a weighted sum of its singles'); Wessels et al. 2023
  (each pair against the average and the product of its singles); Otto et al. 2023 and Yao et al. 2023 (too few cells to
  tell additivity from synergy; no second-order term even at q < 0.5); Pertura's choice: the check that each member
  acts, and the control pseudo-pairs as an added check where the units allow.
- **Read:** T4, C2, P6, PF16, `E.combinations`

### two-directions. Activation and inhibition of one gene

- **Short answer:** Where a library activates and inhibits one gene, compare the two profiles where both directions move
  the gene (C3, `E.opposite_modes`, `B.efficacy`). Opposite profiles show the gene's own dosage effect, and a profile
  that does not oppose can be a threshold or a non-linear effect (`E.opposite_modes`). Activation and inhibition
  captured in different batches differ by batch, so compare them within one kind of capture and give each kind's value
  beside the pooled one (`E.opposite_modes`, U3).
- **Sources:** Pertura's choice: the two directions compared where both act, within one kind of capture, and a profile
  that does not oppose read as a possible threshold or non-linear effect.
- **Read:** C3, U3, `E.opposite_modes`, `B.efficacy`

### conditions. An effect that differs between conditions or cell types

- **Short answer:** Compare a perturbation's effect between conditions against each condition's own control cells, so a
  condition's own shift does not move every target, and say which control cells each uses; the untreated control cells
  can be a second reference (`E.condition_x_perturbation`, C1, PF14). By default (Pertura's choice, changeable), the
  change is tested over biological replicates where 3 or more hold the target's and control cells in each condition,
  else in one test on cells with each sample its own baseline; `test` (cells or units) sets one test for all targets
  (`E.condition_x_perturbation`, PF2, U1). On cells, a target with one sample in a condition has a change that is also a
  sample difference, and its finding says so (`E.condition_x_perturbation`, PF1). In a screen of several cell types or
  lines, read the effect within each (L4).
- **Sources:** Liu et al. 2024 (each condition's effect against that condition's control cells and against the untreated
  ones); Squair et al. 2021 (the units as the replicates, which the test over replicates follows); Pertura's choice: as
  a changeable default, the test over replicates where three hold each condition, else one test on cells.
- **Read:** C1, L4, PF1, PF2, PF14, U1, P2, `E.condition_x_perturbation`

## Findings and validation

### findings. What a finding says, and what it rests on

- **Short answer:** Every finding states its n and its cells: what its replicates are and how many cells each side has
  (M2, PF1, PF16). Its checks run when it is recorded: each guide alone, the design's other test, and the calibration on
  control-guide groups (T1, PF2, T2). Two more checks are Pertura's own: the lead test at matched depth, where published
  screens put the depth or the genes detected in the model instead, and the lead on each half of the data where the
  halves are separate data (T7, T6). A finding whose checks disagree says so, with no verdict drawn over them (PF2).
  Where the deposit has its own guide calls, the finding is read again on them (`B.guide_calls_and_their_evidence`).
- **Sources:** Squair et al. 2021 (pseudobulk by replicate against tests on cells); Alda-Catalinas et al. 2024 (the test
  run on cells with non-targeting guides; findings tiered by the guides that support them); Ota et al. 2025 (the genes
  detected and the mitochondrial share in the per-gene test, where Pertura checks at matched depth); Pertura's choice:
  the checks at matched depth and on halves of the data, and no verdict over the checks.
- **Read:** M2, PF1, PF16, PF2, T1, T2, T6, T7, `B.guide_calls_and_their_evidence`

### target-evidence. How to read one target's evidence

- **Short answer:** Pertura reads a target's evidence as separate dimensions, each from its own module, rather than one
  score: efficacy, the share of cells perturbed, the whole profile, genes and programs, states, and the agreement of
  guides and samples (`B.efficacy`, `B.escape`, `C.any_effect`, `C.genes`, `C.programs`, `C.states_and_composition`,
  `C.within_states`, `F.guides_agree`, `F.replicates_agree`). Each has its limits: a knockout's target gene can stay
  flat, the share perturbed is not the editing rate, and cell numbers and depth also move the whole profile (PF7, S3,
  PF16). Read genes and programs against control-guide groups (R1, T2). A whole-profile test that detects nothing leaves
  the other questions open (`C.any_effect`, `C.genes`). A guide that does not act leaves each question untested, not
  answered no (`B.efficacy`, PF5). Give each dimension's result with its n, and say which agree (PF16, HJ4).
- **Sources:** Papalexi et al. 2021 (an edit without a detectable expression shift is classed not perturbed); Tuladhar
  et al. 2019 (about half of CRISPR-edited cell lines made unexpected mRNAs or proteins from the targeted gene);
  Replogle et al. 2022 (where some perturbations did not knock down their target, the share of perturbations with an
  effect is a lower bound); Pertura's choice: no single score over the dimensions.
- **Read:** PF5, PF7, PF16, S3, R1, T2, HJ4, `B.efficacy`, `B.escape`, `C.any_effect`, `C.genes`, `C.programs`,
  `C.states_and_composition`, `C.within_states`, `F.guides_agree`, `F.replicates_agree`

### nothing-detected. What a result that detects nothing says

- **Short answer:** Say which of three a result that detects nothing is: untestable, not detected with a wide interval,
  or not detected with a narrow interval around zero (V5, HJ4). Untestable means `not_enough_data` (no cells, no
  reference, or a perturbation not shown to act) or `cannot_answer` (the design lacks the sample, reference or readout);
  neither is evidence of no effect (V5, HJ4, `B.efficacy`). A wide interval, as with few cells or a rarely detected
  gene, leaves an effect that matters possible (PF16, ES3). Only a narrow interval inside a margin fixed in advance,
  tested by two one-sided tests, shows a small effect (ES5). The modules give no such interval, so a `not_detected`
  stays the second case until one is computed in the notebook (ES5, `C.genes`, `C.any_effect`). A `measured` result,
  such as `B.dose`'s, is not a non-detection (`B.dose`).
- **Sources:** Schuirmann 1987 (two one-sided tests against a margin did better than reading a non-significant test with
  its power); Pertura's choice: the three readings, with `not_enough_data` and `cannot_answer` as the record's terms.
- **Read:** V5, HJ4, ES5, PF16, ES3, `B.efficacy`, `B.dose`, `C.genes`, `C.any_effect`

### choices. A choice that could change a reading

- **Short answer:** Judge the alternatives at a choice by how control guides tested as targets behave, never by which
  gives more hits, with the measure chosen before the results (HJ1, R1, M4, ES2). A different population, comparison or
  quantity is a new question, not another method, and selecting cells by their response is one (HJ2, S2, PF17). Never
  pick the guide with the largest effect, combine rules until something passes, or drop replicates that disagree; the
  record keeps every run, so what was tried shows (PF18). Where a result rests on a published choice that Pertura's
  defaults do not make, such as keeping only the cells Mixscape calls perturbed, say which choice it rests on (HJ3,
  PF17).
- **Sources:** Barry et al. 2024 (tests judged with non-targeting guides as the null); Christensen et al. 2026
  (conditioning on a variable the perturbation changes biases the estimate of its effect); Göring et al. 2001 (an effect
  picked as the largest of many estimates is inflated); Simmons et al. 2011 (flexible analysis raises false positives,
  and the remedy is to disclose what was done).
- **Read:** HJ1, HJ2, HJ3, M4, ES2, R1, S2, PF17, PF18

### own-methods. A method of one's own

- **Short answer:** Before trusting a method of your own, plant known effects in the screen's control cells by binomial
  thinning of their real counts, which keeps the unwanted variation of real data (R4, M1). The check covers only what
  the planting kept; thinning lowers counts, so it plants decreases directly, and increases need another construction
  (R4). Fix the planted scenarios in advance, and judge the method by its false calls on control-against-control
  comparisons and its recovery of the planted effects, with the measure chosen before the results (R4, ES2). Do not tune
  the method until something passes (M1).
- **Sources:** Gerard 2020 (binomial thinning of real counts keeps the unwanted variation that a simulation from a
  theoretical model loses); Pertura's choice: the check on the screen's own control cells, with the scenarios fixed in
  advance.
- **Read:** R4, M1, ES2

### direct-targets. A factor's known targets

- **Short answer:** For a perturbed transcription factor, `G.direct_targets` scores with decoupler how its known targets
  in CollecTRI's regulons move, and the share of its changed genes that are known targets: support for a direct effect,
  not proof (`G.direct_targets`, X2). A regulon is curated from other cells and conditions, so the factor can act here
  through other genes; read its activity beside its own on-target change (`G.direct_targets`, PF6). A mean expression
  change alone does not tell direct from indirect regulation (R2).
- **Sources:** Christensen et al. 2026 (the target gene moves because of the guide, down under CRISPRi and up under
  CRISPRa); Pertura's choice: the factor's activity from its known targets, with decoupler and CollecTRI.
- **Read:** X2, R2, PF6, `G.direct_targets`

### outside-data. Outside knowledge and outside data

- **Short answer:** Pertura's order brings in outside knowledge after the moves that trust, organise and locate a
  result, and records it as prior knowledge with its source (prior knowledge); it informs a finding and does
  not by itself change one (X1, `G.meaning`). Known cell states and atlases place the screen's states and programs, and
  genetics, patients and other screens place its regulators and hits; the user approves any download first (X3, X4).
- **Sources:** Zhang 2023 (GO enrichment of each guide's changed genes against non-targeted cells); Pertura's choice:
  outside knowledge last, recorded as prior knowledge.
- **Read:** X1, X3, X4, `G.meaning`

### figures. The figure for each kind of evidence

- **Short answer:** Draw composition as each perturbation's cell density on the embedding, bars of the state shares, or
  a perturbation-by-state heatmap of signed significance (FG1). Draw a target's own mRNA as a box, histogram or ridge
  plot, dots per guide or a cumulative distribution, and effects on genes or programs as a heatmap centred on zero
  (FG1). Draw enrichment as a perturbation-by-gene-set bubble plot, or as the heatmap's gene groups with their top terms
  (FG1). Show replicate agreement once, replicate against replicate with its r; each animal as a dot belongs to arrayed
  follow-ups (FG1, `F.replicates_agree`). Pertura keeps the control grey in every panel, and each perturbation or state
  in one colour (FG1). A clean separation after a supervised step displays what the method was trained to find; it is
  not evidence (V3).
- **Sources:** Replogle et al. 2022 (the target's own mRNA against the controls; replicate agreement as day 6 against
  day 8); Liu et al. 2024, Meyers et al. 2024 and Schnitzler et al. 2024 (the target's own mRNA as a box, histogram or
  ridge plot); Papalexi et al. 2021 (a supervised projection separates labelled groups by construction); Pertura's
  choice: the control grey in every panel, and fixed colours.
- **Read:** FG1, V3, M7, `F.replicates_agree`

### validation. The experiment that would settle a finding

- **Short answer:** Published screens settle a lead by rerunning its perturbation arrayed, then by other means, such
  as a drug for the regulator or an animal model (P1.9, P2.8, P3.10, P6.15). Test a candidate nominated by a
  correlation by its own perturbation; one without a guide in the screen stays a candidate for another experiment (V2,
  M6).
- **Sources:** the validation steps of the paradigm cards' papers; Pertura's choice: the user chooses what is
  validated.
- **Read:** V2, M6, P1.9, P2.8, P3.10, P6.15

### stopping. When a thread has said what it can

- **Short answer:** Never end a route only because it gave few hits, and never continue one only because it gives many
  (V5). Consider ending a thread when it has said what it can: the explanation weakened, a new question opened, a
  candidate awaits an independent test, or more of the same adds no new information (V5, M5). `not_enough_data`
  (efficacy or coverage not shown) is no evidence of no effect, and `cannot_answer` says what is missing: a sample, a
  reference, a readout or an overlap (HJ4, V5).
- **Sources:** Pertura's choice: these signals for ending a thread, since papers report their final pipeline, not the
  routes they dropped, so there is no rule to copy.
- **Read:** V5, HJ4, M5
