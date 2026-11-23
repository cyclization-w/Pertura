# Programs, factors, gene sets and enrichment

## PG1. Genes that move together

- **If you see:** many genes moving together under several targets, with weak single-gene evidence.
- **Consider:** a program-level analysis: a gene set fixed beforehand, or a factor model whose factors are
  associated with the perturbations. Subtract what control guides do to the factors.
- **It acts on:** the resolution of the outcome. Keep program claims and gene claims apart.
- **Read it as:**
  - a factor's association with a target does not mean every gene loading on it is affected;
  - posterior inclusion probabilities and local false sign rates are not p-values;
  - the number of factors is a modelling choice. Check its effect, and do not choose it for the richest result.

  In the source, permuting the perturbation labels showed how much the model wrongly borrowed from them.
- **Stop or turn when:** consider fixing the program's genes and testing it on guides or samples it was not found on, or
  fitting it on the control cells only, when the program was found on the cells it would be tested on. Published screens
  also test programs found on the screen's cells against the control cells with batch covariates, sometimes with a
  control-against-control check.
- **Source:** *A new Bayesian factor analysis method improves detection of genes and biological processes
  affected by perturbations in single-cell CRISPR screening*, Nat Methods 2023, doi:10.1038/s41592-023-02017-4.
  Pertura's choice: test a program on cells it was not found on.

## PG2. A score that uses the genes it is tested on

- **If you see:** a response score or program score built from genes that you then test one by one.
- **Consider:** leaving the tested gene out of the score when testing it. Check the score against permuted labels.
- **It acts on:** the dependence between the score and the outcome.
- **Read it as:** leaving one gene out removes the most direct circularity, not every selection effect. Say that
  the score was learned on the same data.
- **Stop or turn when:** consider stopping the scoring and falling back to the unweighted analysis, as the source did,
  when too few genes respond to give a stable score. That is not evidence that the perturbation failed.
- **Source:** *Systematic reconstruction of molecular pathway signatures using scalable single-cell perturbation
  screens*, Nat Cell Biol 2025, doi:10.1038/s41556-025-01622-z.

## PG3. Overlapping gene sets

- **If you see:** pathway gene sets that share many genes, so a target seems to move several pathways at once.
- **Consider:** gene sets made exclusive (shared genes removed), labelled as such, and checked against an
  independent dataset where one exists.
- **It acts on:** the program's definition. The score now measures something narrower; say so.
- **Read it as:** a shared gene that responds to many pathways is not evidence for any one of them.
- **Stop or turn when:** consider stopping when the exclusive sets are too small to score.
- **Source:** *Systematic reconstruction of molecular pathway signatures using scalable single-cell perturbation
  screens*, Nat Cell Biol 2025, doi:10.1038/s41556-025-01622-z. Pertura's choice: stop at sets too small to score.

## PG4. Enrichment

- **If you see:** a list of hits to interpret with gene sets.
- **Consider:**
  - the background as the genes the assay detected, not the genome;
  - a correction for the many sets tested;
  - the database's version, recorded;
  - the right test for the input: an over-representation test for a hit list, a rank-based test for a full
    ranking.
- **It acts on:** the interpretation.
- **Read it as:** a whole-genome background changes over-representation results substantially. Never change the
  background, the database or the test family to reach a wanted pathway. An enrichment is a candidate, not a
  finding.
- **Stop or turn when:** consider stopping the formal interpretation when the background, ranking or version cannot be
  recovered.
- **Source:** *Urgent need for consistent standards in functional enrichment analysis*, PLoS Comput Biol 2022,
  doi:10.1371/journal.pcbi.1009935. Pertura's choice: without the background, ranking or version, the reading stays
  informal.

## PG5. Clusters and their markers

- **If you see:** states found by clustering, and markers tested between those clusters.
- **Consider:** treating the marker p-values as descriptive. Clusters and markers come from the same data, so the
  p-values are inflated.
- **It acts on:** the interpretation of exploratory labels.
- **Read it as:** a significant cluster marker is not independent evidence. A change in how many cells sit in a
  state (composition) is a different outcome from a change in expression. It needs its own analysis
  (C.states_and_composition).
- **Stop or turn when:** consider fitting the states without perturbation labels when the question is about the states
  themselves, and recording composition questions and within-state questions apart.
- **Source:** *Best practices for single-cell analysis across modalities*, Nat Rev Genet 2023,
  doi:10.1038/s41576-023-00586-w. Pertura's choice: composition and within-state questions kept apart.

## PG6. A program worth interpreting

- **If you see:** programs found by a factorisation or a clustering, about to be named and interpreted.
- **Consider:** checking their stability (bootstrap consensus, a varied factor number), and comparing them with
  independent references or other contexts before giving them a biological name.
- **It acts on:** the program's discovery and its interpretation.
- **Read it as:** a stable program that matches an independent reference supports a name. A pathway name on an
  unstable factor is a label, not an explanation. Program discovery and differential expression are different tasks
  with different outputs.
- **Stop or turn when:** consider not reporting it as a program (the explanation weakened) when the program is not
  stable across resamples.
- **Source:** *Comprehensive transcription factor perturbations recapitulate fibroblast transcriptional states*, Nat
  Genet 2025, doi:10.1038/s41588-025-02284-1. Pertura's choice: an unstable program is best not reported as a program.
