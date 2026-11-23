# Pitfalls

Mistakes that make a Perturb-seq finding look better than it is. Read them before the first analysis. The names
B.efficacy, D.common_response and so on are Pertura's modules, under the design's questions A-H.

## Samples, replicates and cells

### PF1. Say what the n is

Biological replicates (mice, donors, separate infections) and technical replicates (lanes or wells of one pool) are
different. A test of differential expression has to account for the variability between biological replicates
(Squair et al. 2021). A lane covariate adjusts for differences between lanes. It does not turn lanes into animals.
Where animals were pooled before sequencing, a result describes the pooled cells. Say so, with the n.
- **Source:** Squair et al., *Confronting false discoveries in single-cell differential expression*, Nat Commun 2021,
  doi:10.1038/s41467-021-25960-2. Pertura's choice: lanes are not animals; pooled animals are reported as pooled
  cells.

### PF2. Cells as the observations can give excess false positives

Tests that compare individual cells give false discoveries that tests on pseudobulk (cells summed by biological
replicate) do not. Adding replicates reduces them; adding cells makes them worse (Squair et al. 2021). On single-cell
CRISPR screens, the existing tests were miscalibrated against non-targeting guides, with excess false positives (Barry
et al. 2024). The design chooses the default test: pseudobulk by replicate where 3 or more biological replicates hold
both sides; otherwise a test on cells against the control cells of the target's own samples, with pseudobulk by sample
as a finding's check where 3 samples hold both sides.
Three is Pertura's default and can be changed; a test named for a run needs 2 replicates a side, and its result notes
the difference. A finding whose two tests disagree says so.
- **Source:** Squair et al., Nat Commun 2021, doi:10.1038/s41467-021-25960-2; Barry et al., *Robust differential
  expression testing for single-cell CRISPR screens at low multiplicity of infection*, Genome Biology 2024,
  doi:10.1186/s13059-024-03254-2. Pertura's choice: three replicates by default, and pseudobulk as a finding's check.

### PF3. In vivo, a guide's cells can be few clones

A clone makes a guide's cells look abundant and its change look well supported, but its cells are not independent.
In vivo screens pass through bottlenecks, clonal competition and random drift. These can obscure the biological
signal (Datlinger et al. 2025) and change the rates of false positives and false negatives (Milling et al. 2024).
Treat a guide far above its share of the library as expansion until shown otherwise. The cells of TP53's guides rose
by clonal expansion, which the plasmid library's even counts showed (Sivakumar et al. 2025, in vitro). Dixit et al.
2016 read a guide's cells against its share of the original library. Read B.clonality's top-guide shares beside
B.abundance, against the library's counts where the user has them. Count the clones where the vector carries a UMI
(Datlinger et al. 2025; Milling et al. 2024). A finding seen with two guides is not one clone's (F.guides_agree).
- **Source:** Datlinger et al., *Systematic discovery of CRISPR-boosted CAR T cell immunotherapies*, Nature 2025,
  doi:10.1038/s41586-025-09507-9; Milling et al., *Framework for in vivo T cell screens*, J Exp Med 2024,
  doi:10.1084/jem.20230699; Sivakumar et al., *Benchmarking and optimizing Perturb-seq in differentiating human
  pluripotent stem cells*, Stem Cell Reports 2025, doi:10.1016/j.stemcr.2025.102713; Dixit et al., Cell 2016,
  doi:10.1016/j.cell.2016.11.038. Pertura's choice: expansion until shown otherwise; two guides as the check against
  one clone.

### PF4. In T cells, clones carry their receptor genes

A T-cell clonotype is identified by its V genes and CDR3 sequences (Heumos et al. 2023), so the cells of one clone
share their receptor V genes. In T cells that are not TCR-transgenic, read receptor V genes among a target's changed
genes as clonality, not as the perturbation's effect. Leave the receptor genes out of the profiles and the
clustering. Chung et al. 2026 regressed the TCR genes out during clustering, in TCR-transgenic cells. In
TCR-transgenic T cells the receptor cannot mark clones (Milling et al. 2024).
- **Source:** Heumos et al., *Best practices for single-cell analysis across modalities*, Nat Rev Genet 2023,
  doi:10.1038/s41576-023-00586-w; Chung et al., *Atlas-guided discovery of transcription factors for T cell
  programming*, Nature 2026, doi:10.1038/s41586-025-09989-7; Milling et al., J Exp Med 2024,
  doi:10.1084/jem.20230699. Pertura's choice: receptor genes among a target's changed genes are read as clonality.

## Knockdown of the target gene

### PF5. Many guides do not knock down their target

Check the target gene (B.efficacy) before reading a target's lack of effect. Without knockdown, the question cannot be
answered for that guide. It does not mean the gene does nothing. In a genome-scale CRISPRi screen, the target fell
significantly for 73% of the guides. The guides without a detectable knockdown had a low baseline expression or fewer
cells (Zhu et al. 2026). In another screen, about 70% of CRISPRi guides repressed their target by more than half
(Sivakumar et al. 2025). Where some perturbations did not knock down their target, the share of perturbations with an
effect is a lower bound (Replogle et al. 2022). Published screens set such guides aside. For their functional analyses,
Replogle et al. 2022 kept the perturbations with at least 30% knockdown or with the target not detected. Zhu et al. 2026
excluded 869 inefficient guides. In Pertura, the knockdown line is B.efficacy's `threshold` choice: 30% by default, and
changeable.
- **Source:** Zhu et al., *Genome-scale perturb-seq in primary human CD4+ T cells maps context-specific regulators of
  T cell programs and human immune traits*, Cell 2026, doi:10.1016/j.cell.2026.08.002; Sivakumar et al., Stem Cell
  Reports 2025, doi:10.1016/j.stemcr.2025.102713; Replogle et al., Cell 2022, doi:10.1016/j.cell.2022.05.013.
  Pertura's choice: B.efficacy's line at 30% by default; without knockdown, the question cannot be answered for that
  guide.

### PF6. The target gene moves because of the guide

The target gene goes down under CRISPRi and up under CRISPRa (Christensen et al. 2026). This shows that the guide
works (B.efficacy). It is not a finding about the gene's function. When perturbations are compared or clustered,
published screens mask the target genes. Replogle et al. 2022 masked them so that the knockdown would not drive the
clustering. Zhu et al. 2026 set the on-target z-scores to zero.
- **Source:** Christensen et al., *Causal effect estimation from trans-regulatory single-cell CRISPR screens*, Cell
  Genomics 2026, doi:10.1016/j.xgen.2026.101251; Replogle et al., Cell 2022, doi:10.1016/j.cell.2022.05.013; Zhu et
  al., Cell 2026, doi:10.1016/j.cell.2026.08.002. Pertura's choice: the target's own change is not a finding about
  its function.

### PF7. A knockout's mRNA often stays

A knockout's mRNA can escape nonsense-mediated decay, or the repair can be in frame. Dixit et al. 2016, Otto et al.
2023 and Meyers et al. 2024 say so. In CRISPR-edited cell lines, about half made unexpected mRNAs or proteins from the
targeted gene (Tuladhar et al. 2019). A flat target gene after knockout says nothing on its own. In one knockout
screen, 64 guides out of 111 lowered their target. The others were ineffective guides, edits that escaped decay but
agreed with the target's other guides, or targets expressed little or not at all (Otto et al. 2023). For knockout,
the evidence that a guide worked is a downstream signature (Meyers et al. 2024), protein where it was measured,
agreement between the target's guides (F.guides_agree), and the edits at the cut site where the reads cover it
(Zheng et al. 2024).
- **Source:** Dixit et al., Cell 2016, doi:10.1016/j.cell.2016.11.038; Otto et al., Mol Cell 2023,
  doi:10.1016/j.molcel.2023.03.013; Meyers et al., Haematologica 2024, doi:10.3324/haematol.2023.284901; Tuladhar et
  al., *CRISPR-Cas9-based mutagenesis frequently provokes on-target mRNA misregulation*, Nat Commun 2019,
  doi:10.1038/s41467-019-12028-5; Zheng et al., Cell 2024, doi:10.1016/j.cell.2024.04.050.

### PF8. Guides of one target can differ in strength

B.dose measures it. A guide's efficiency can vary across a target's guides, so treating them as one perturbation
mixes different treatments. One remedy is to estimate the effects guide by guide (Christensen et al. 2026). In a
genome-scale screen, most disagreements between a target's guides came from their on-target knockdown or the
strength of their effects (Zhu et al. 2026). Zheng et al. 2024 tested composition per guide because gRNA performance
is known to vary. Analyse by guide. Keep the conflict rather than merging it into one gene-level result.
- **Source:** Christensen et al., Cell Genomics 2026, doi:10.1016/j.xgen.2026.101251; Zhu et al., Cell 2026,
  doi:10.1016/j.cell.2026.08.002; Zheng et al., Cell 2024, doi:10.1016/j.cell.2024.04.050. Pertura's choice: the
  conflict stays in the record.

## Rival explanations that are always there

### PF9. A response common to the perturbations

Cas9's cuts set off a p53-dependent response to DNA damage. With it comes selection against the cells whose p53
works (Haapaniemi et al. 2018; Ihry et al. 2018). A stress response or a cell-cycle arrest can occur across many
perturbations alike (Viñas Torné et al. 2025). A target's effect that every target shows is that common response
(D.common_response). Compare the target with the other targets' cells as well as with the control cells. Systema
moves the reference to the average of the perturbed cells. The targets that still differ have effects of their own.
Where the library has them, control guides that cut where no gene is (safe-targeting, intergenic) are the fairer
control for the cutting (Morgens et al. 2017; D.cutting_response).
- **Source:** Haapaniemi et al., Nat Med 2018, doi:10.1038/s41591-018-0049-z, and Ihry et al., Nat Med 2018,
  doi:10.1038/s41591-018-0050-6 (in retinal pigment epithelial cells and in stem cells); Viñas Torné et al.
  (Systema), Nat Biotechnol 2025, doi:10.1038/s41587-025-02777-8; Morgens et al., Nat Commun 2017,
  doi:10.1038/ncomms15178 (in growth screens).

### PF10. Control-guide cells and all other perturbed cells answer different questions

Against the control-guide cells, a target's effect is what the target does. Against all other perturbed cells, it
is what stands out from the average perturbation. Published screens use both, most often the control cells. The two
references give different readings of the same profiles (Viñas Torné et al. 2025).
- **Source:** Viñas Torné et al. (Systema), Nat Biotechnol 2025, doi:10.1038/s41587-025-02777-8. Pertura's choice:
  each reference is read as its own question.

### PF11. A shift in the states a target's cells fill is itself an effect

A target can change which cell states its cells are in. That shift is itself an effect of the target, and is
reported as one. Many published screens show a perturbation's shares of states, most with a test. A change of a
gene's mean can then be a change of states. With each cell's state in the model, some guides' effects were accounted
for; Dixit et al. 2016 read this as those perturbations mainly changing the subtype proportions. Fit the states
without perturbation labels, on all the cells, or on cells without a perturbation with the others placed on them
(Dixit et al. 2016). Then compare (C.states_and_composition). Keep a shift of the shares apart from a change within a
state. Test within each state (C.within_states), or add each cell's state to the model, as Dixit et al. 2016 did to
recover the effects within a state.
- **Source:** Dixit et al., *Perturb-seq: dissecting molecular circuits with scalable single-cell RNA profiling of
  pooled genetic screens*, Cell 2016, doi:10.1016/j.cell.2016.11.038. Pertura's choice: the states are fitted on all
  the cells.

### PF12. Technical causes of a composition shift come first

Guide-UMI depth that differs between guides can shift the share of cell types a guide's cells fill. Zheng et al.
2024 found a Spearman correlation of 0.84 with one cell type's share, which was gone after down-sampling. The
injection and the vector can move the cells they reach into a state. Shen et al. 2026 saw injection injury and AAV
transduction move cells toward a less mature oligodendrocyte state. Differences between guides may also reflect the
cells collected (Zheng et al. 2024). Check these causes before reading biology.
- **Source:** Zheng et al., Cell 2024, doi:10.1016/j.cell.2024.04.050; Shen et al., Nat Commun 2026,
  doi:10.1038/s41467-026-69677-6.

### PF13. A state held by one perturbation may be doublets

Doublets of two cell types can give wrong cell-type labels (Heumos et al. 2023). In one screen, a cluster made mostly
of one target's cells expressed some cell-type markers and markers of its own. The authors read it as not doublets
(Zheng et al. 2024). They had also scored doublets in each 10x channel and removed the clusters with high scores.
Check such a state's markers, and the doublet calls where they were made, before reading it. The source checked one
cluster and gives no general rule.
- **Source:** Heumos et al., Nat Rev Genet 2023, doi:10.1038/s41576-023-00586-w; Zheng et al., Cell 2024,
  doi:10.1016/j.cell.2024.04.050. Pertura's choice: a state held by one perturbation is checked before it is read.

### PF14. Targets and control cells from different samples

A target is best compared with the control cells of its own samples. Otherwise, a target that sits in one sample also
differs by its sample. Replogle et al. 2022 and Adamson et al. 2016 normalise each lane or batch to its own control
cells. Published screens pool all the control cells at least as often as they match them by sample. Some compare with
all other perturbed cells instead.
- **Source:** Replogle et al., Cell 2022, doi:10.1016/j.cell.2022.05.013 ("Within each gemgroup, for each gene, we
  compute the mean and standard deviation of expression within control cells"); Adamson et al., Cell 2016,
  doi:10.1016/j.cell.2016.11.048 ("cells were normalized with respect to control cells within the same lane").
  Pertura's choice: control cells matched by sample.

### PF15. Large fold changes of genes the controls barely express

Where a gene's counts are low, its estimated log fold change varies widely. A large estimate need not be a large
difference (Zhu et al. 2019). Shrink the fold changes, or set such genes apart, before ranking genes by effect size.
- **Source:** Zhu, Ibrahim and Love, *Heavy-tailed prior distributions for sequence count data*, Bioinformatics 2019,
  doi:10.1093/bioinformatics/bty895.

### PF16. Few cells give noisy effects; many cells give tiny significant ones

A target with few cells has noisy effects. A target with many cells has tiny significant ones. The number of genes
that pass a test follows the cells as well as the effect. For the same perturbations in two screens, the number of
significant genes agreed far less than an estimate of the transcriptome-wide effect (R² 28.4% against 59.7%; Nadig
et al. 2025). Report both the effect size and the cells.
- **Source:** Nadig et al., Nat Genet 2025, doi:10.1038/s41588-025-02169-3. Pertura's choice: the cells are reported
  beside the effect size.

## Choices that change the question

### PF17. Filtering cells by their response changes the question

Keeping only the cells where the knockdown worked, or the cells in one cluster, selects cells by a variable the
perturbation itself changes. Conditioning on such a variable biases the estimated effect of the perturbation, even
where the variable only mediates the effect (Christensen et al. 2026). Published screens do keep only the cells
classed as perturbed. Saputra et al. 2025 removed the cells Mixscape classed as not perturbed before testing genes.
Such a selection changes the population, so it is another question. Open it as a thread of its own,
beside the analysis of all the target's cells.
- **Source:** Christensen et al., Cell Genomics 2026, doi:10.1016/j.xgen.2026.101251; Saputra et al., Mol Oncol 2025,
  doi:10.1002/1878-0261.70153. Pertura's choice: a selection of cells is read as conditioning, and opened as a
  thread of its own.

### PF18. Do not pick the guide with the largest effect, or combine rules until something passes

Do not pick the guide with the largest effect, combine significance rules until something passes, or drop replicates
that disagree. Flexibility in how data are analysed and reported greatly raises the real false-positive rate. The
remedy Simmons et al. 2011 give is to disclose what was done. An effect picked as the largest of many estimates is
inflated (Göring et al. 2001). Published screens ask a target's guides to agree instead. Zhu et al. 2026 filtered on
the correlation between a target's two guides, each with a significant knockdown (F.guides_agree). The record keeps
every run, so what was tried shows.
- **Source:** Simmons, Nelson and Simonsohn, *False-positive psychology*, Psychol Sci 2011,
  doi:10.1177/0956797611417632; Göring, Terwilliger and Blangero, *Large upward bias in estimation of locus-specific
  effects from genomewide scans*, Am J Hum Genet 2001, doi:10.1086/324471; Zhu et al., Cell 2026,
  doi:10.1016/j.cell.2026.08.002. Pertura's choice: these points applied to guides, rules and replicates; the record
  as the disclosure.

### PF19. The gene universe of an enrichment is the genes the assay detected

Use the genes the assay detected, not the genome. Most genes are not expressed and cannot be called changed, and
single-cell data detect only a few thousand. Across seven RNA-seq datasets, a whole-genome background gave results
on average only 44% similar to those with the detected genes (Wijesooriya et al. 2022). Use the genes detected at a
level where they could be called changed. Sivakumar et al. 2025 used the genes expressed in at least 5% of cells.
For an enrichment among the targets that are hits, the background is the targeted genes (Replogle et al. 2022).
- **Source:** Wijesooriya et al., *Urgent need for consistent standards in functional enrichment analysis*, PLoS
  Comput Biol 2022, doi:10.1371/journal.pcbi.1009935; Sivakumar et al., Stem Cell Reports 2025,
  doi:10.1016/j.stemcr.2025.102713; Replogle et al., Cell 2022, doi:10.1016/j.cell.2022.05.013.
