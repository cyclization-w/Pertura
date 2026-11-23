# Effect sizes, counting hits, and transcriptome-wide impact

## ES1. Hit counts that track cells and depth

- **If you see:** targets or conditions compared by how many genes reach significance, when they differ in cells,
  depth or noise.
- **Consider:** asking for a different quantity: the distribution of true effects, estimated from each gene's effect
  and its standard error (for example a transcriptome-wide impact), rather than the number of hits.
- **It acts on:** the quantity asked. A transcriptome-wide impact is not another hit list.
- **Read it as:**
  - more hits with more cells is power, not stronger biology;
  - the estimate needs reliable standard errors and the right replicate units. In vivo, resampling cells does not
    replace animals;
  - enrichment run on only the significant genes carries a winner's curse.
- **Stop or turn when:** consider stopping the comparison of distributions, and fixing the estimate first, when the
  inputs' standard errors or the replicate design are not trustworthy. Never choose a DE route because its impact comes
  out larger.
- **Source:** *Transcriptome-wide analysis of differential expression in perturbation atlases*, Nat Genet 2025,
  doi:10.1038/s41588-025-02169-3. Pertura's choice: in vivo, resampled cells do not replace animals.

## ES2. Few true effects among many genes

- **If you see:** a comparison of methods or settings on a screen where only a few genes are truly affected.
- **Consider:** precision and recall of the calls (or the false calls on control-vs-control comparisons and the
  recovery of planted effects), not overall accuracy curves. These look good when nearly everything is null.
- **It acts on:** how the choice is judged.
- **Read it as:** a benchmark's metric is chosen before its results. Judging a method by the hits it gives on the
  screen under study is not a benchmark.
- **Stop or turn when:** consider stopping when the comparison set fixed in advance has run.
- **Source:** *Single-cell differential expression analysis between conditions within nested settings*, Brief
  Bioinform 2025, doi:10.1093/bib/bbaf397. Pertura's choice: stop after the planned comparison set.

## ES3. Sparse genes and the tests that need expression

- **If you see:** a test that is unreliable for genes detected in few cells.
- **Consider:** a detection filter fixed before the test, the same for every group. Say that conclusions do not
  cover the filtered genes.
- **It acts on:** the gene universe.
- **Read it as:** a gene the filter removed is untested, not unaffected. A filter chosen on the group itself changes
  the genes between groups, so the group and its null are not compared on the same genes.
- **Stop or turn when:** consider `not_enough_data` for the gene's question, with that test, when the gene is below
  the filter.
- **Source:** *Single-cell differential expression analysis between conditions within nested settings*, Brief
  Bioinform 2025, doi:10.1093/bib/bbaf397. Pertura's choice: a filtered gene's question is `not_enough_data`.

## ES4. Discoveries counted over selected targets

- **If you see:** discoveries counted or compared over targets that a first test selected (the genes of the targets
  an any-effect test called), or q-values from corrections of different scopes put side by side.
- **Consider:** saying each correction's family (a target's genes, the targets tested) and the selection before it.
  Where the claim is about the selected families together, adjust for the selection, as the first source does.
- **It acts on:** the error rate the reported discoveries carry, and the unit of discovery.
- **Read it as:** a correction within each family controls the false discoveries within that family, not over the
  families a test selected. The second source simulated association tests grouped by variant, with the first
  source's approach. Correcting within each family alone controlled the average over all families. Yet the families
  of the discovered variants tended to have a very large false discovery proportion.
- **Stop or turn when:** consider reporting each target's discoveries with its family, not one rate over the selected
  targets, when the claim is a count over selected targets and no correction for the selection ran.
- **Sources:**
  - *Selective inference on multiple families of hypotheses*, J R Stat Soc B 2014, doi:10.1111/rssb.12028;
  - *Many phenotypes without many false discoveries: error controlling strategies for multitrait association
    studies*, Genet Epidemiol 2016, doi:10.1002/gepi.21942;
  - Pertura's choice: without that correction, discoveries are reported per family.

## ES5. A result that detects nothing

- **If you see:** a target, gene or program not detected, about to be read as having no effect.
- **Consider:** which of three it is: untestable (no cells, no reference, no efficacy shown); not detected with a
  wide interval (imprecise); not detected with a narrow interval around zero. Only the third is evidence of a small
  effect. It needs a margin fixed in advance and a test against it: two one-sided tests against the margin. They
  conclude as a 1-2α confidence interval inside the margin does.
- **It acts on:** the interpretation of a non-detection.
- **Read it as:** a non-significant test, even with its power stated, is not a test of equivalence. For two
  averages, the source found the two one-sided tests better than this power approach in most cases, and in all cases
  with a suited level. The margin is a scientific choice, not one read from the results.
- **Stop or turn when:** consider keeping the result `not_detected`, read as imprecise, when no interval or margin is
  available. Without efficacy or coverage it is `not_enough_data`.
- **Source:** *A comparison of the two one-sided tests procedure and the power approach for assessing the equivalence
  of average bioavailability*, J Pharmacokinet Biopharm 1987, doi:10.1007/BF01068419. Pertura's choice: without a
  margin, `not_detected` is imprecise.
