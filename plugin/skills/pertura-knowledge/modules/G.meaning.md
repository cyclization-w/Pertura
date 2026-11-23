# G.meaning: The gene sets a finding's genes fall in

**The biology.** The meaning of a finding's changed genes: the gene sets they over-represent (up and down apart,
against the genes the screen detects), or where a ranking enriches (GSEA). By default the detected genes are the
background, as a methods paper recommends (PG4, PF19). Published screens state different backgrounds: the expressed
genes, all genes, all screened genes, or the genome against the expressed genes. An enrichment names a pathway, not a
mechanism in these cells, so it is prior knowledge. Up and down genes are tested apart by default, as some published
screens do (Pertura's choice).

**Each target's own genes, on a genes run.** With a C.genes run (`de`), method ora reads each target's changed genes,
up and down apart, against the genes tested for that target, as published screens read a perturbation's changed genes
(X1); method gsea reads each target's genes ranked by their signed test statistics. q is the package's
Benjamini-Hochberg within each list.

**Activity, on a genes run.** With `method` ulm on a C.genes run (`de`), every target is read at once. Each factor's
activity (CollecTRI) or pathway's activity (PROGENy) comes from the target's signed test statistics over its tested
genes, by decoupler's univariate linear model, as decoupler's tutorials read a contrast by its statistic. The statistic is
each gene's z, from its p, with the sign of its change. A sparse gene's extreme fold change with a weak test then weighs
little. Each sits beside the control-guide groups'
line: the 95th percentile of the same activity in the gene test's own calibration groups. q is decoupler's
Benjamini-Hochberg over the sources within each target.

**The measurement.** Per gene set and direction, the over-representation test or GSEA's NES and FDR, the overlap and its
genes, and the universe. The sets are any MSigDB collection by its name (Hallmark by default; GO, Reactome, immune
signatures, cell types and the rest), for human or mouse, or one's own (`gene_lists`: a lab's sets or another
library's, any organism). The universe is the genes detected in 1% of
cells by default, or the genes given as `universe`, such as the genes a test tested. A set counts at q < 0.05 by default
(`q`).

## Ways the result can mislead

### a whole-genome background inflates enrichment

- **Shows in:** `universe` (the detected genes, by default)
- **Read:** PF19, PG4
- **Do:** the detected genes are the default universe, stated with the result; a universe given in their place is
  stated too.

### up and down genes pooled hide sets that go one way

- **Shows in:** the result table's `direction`
- **Read:** PG4
- **Do:** as built.

### enrichment names a pathway, not a mechanism in these cells

- **Shows in:** `tag` (prior knowledge)
- **Read:** PG4
- **Do:** it is recorded as prior knowledge beside the finding. It never changes a
  finding.

## Next steps

- X2 (`G.direct_targets` for a transcription factor), X3, X4; P1.2.
