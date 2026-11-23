# Guide calling, guide efficacy and combining guides

## G1. Calling rules trade purity against coverage

- **If you see:** a choice between guide-calling rules (a fixed UMI count, a share within the cell, a mixture
  model per guide), or a screen deposited with guide counts as well as calls.
- **Consider:** comparing calling rules on several measures together:
  - cells assigned;
  - the target's own knockdown;
  - total discoveries;
  - false discoveries of control guides.
- **It acts on:** the assignment, not the downstream test.
- **Read it as:** strict calling gives stronger knockdown in fewer cells. Loose calling gives more cells with more
  contamination. The source tuned its thresholds to maximise the significant targets. The number of hits alone does
  not show a good call, so check a rule chosen that way on control-vs-control comparisons. The best rule differed
  between screens and technologies.
- **Stop or turn when:** consider taking the assignment as given, and saying so, when the deposit holds only processed
  calls. The assignment cannot be redone (`not_enough_data` for questions that depend on it).
- **Source:** *Guide assignment in single-cell CRISPR screens using crispat*, Bioinformatics 2024,
  doi:10.1093/bioinformatics/btae535. Pertura's choice: hits alone do not judge a rule.

## G2. Calls that do not match the design

- **If you see:** more cells with several guides than the infection rate (MOI) predicts, high background counts,
  or control guides with many false hits.
- **Consider:** in this order:
  1. inspect the cells with several guides;
  2. test whether background contamination explains the control failures;
  3. only then change the calling model (in the source, a fit on non-zero counts only).
- **It acts on:** the assignment, through a diagnosis.
- **Read it as:** go from an anomaly to its cause to a targeted change. This is not a search for more hits.
- **Stop or turn when:** consider stopping when the cause is found and fixed, or cannot be. Left unfixed, the affected
  guides' results are `not_enough_data`.
- **Source:** *Guide assignment in single-cell CRISPR screens using crispat*, Bioinformatics 2024,
  doi:10.1093/bioinformatics/btae535. Pertura's choice: unfixed guides are `not_enough_data`.

## G3. A knockout whose target gene does not fall

- **If you see:** a knockout guide whose target mRNA does not decrease.
- **Consider:** not reading it as a failed guide. A knockout's mRNA can escape decay, so on-target expression does
  not measure a knockout's success, nor give a truth for judging calling rules.
- **It acts on:** the interpretation of efficacy.
- **Read it as:** check efficacy by another route where there is one (protein, a known downstream effect, another
  guide). Without one, efficacy is unknown, not absent.
- **Stop or turn when:** consider `not_enough_data` when efficacy cannot be shown and the question needs it. This is not
  evidence of no effect.
- **Source:** *Guide assignment in single-cell CRISPR screens using crispat*, Bioinformatics 2024,
  doi:10.1093/bioinformatics/btae535. Pertura's choice: unshown efficacy is `not_enough_data`.

## G4. Guides of one target that disagree

- **If you see:** a target's guides with different effect sizes or directions.
- **Consider:** treating the guides as possibly different treatments before pooling them. Compare them guide by
  guide, and check each guide's knockdown, cells, and seeds shared with other guides.
- **It acts on:** how guides are combined, and what "the target's effect" means.
- **Read it as:**
  - pooled guides estimate an average over guide efficacies, and a guide with no knockdown dilutes it;
  - pool where the guides agree, as one source checked before merging;
  - picking the guide with the largest effect to stand for a target inflates the reported effect.
- **Stop or turn when:** consider keeping the conflict in the record, rather than merging it into one gene-level result
  (the explanation weakened, for a single-effect explanation), when neither efficacy nor off-target activity explains
  the disagreement. Published screens drop or tier such guides instead: some filter on the correlation between a
  target's guides, some tier a finding by how many of its guides support it.
- **Sources:**
  - *Causal effect estimation from trans-regulatory single-cell CRISPR screens*, Cell Genomics 2026,
    doi:10.1016/j.xgen.2026.101251;
  - *Systematic reconstruction of molecular pathway signatures using scalable single-cell perturbation screens*,
    Nat Cell Biol 2025, doi:10.1038/s41556-025-01622-z;
  - *Comprehensive transcription factor perturbations recapitulate fibroblast transcriptional states*, Nat Genet
    2025, doi:10.1038/s41588-025-02284-1;
  - Pertura's choice: an unexplained conflict is kept, not merged.

## G5. Guides of different targets that look alike

- **If you see:** guides of different targets with similar phenotypes, or guides that cluster together.
- **Consider:** a shared seed sequence or off-target activity before biology. Check the guides' seeds and their
  on-target activity. Mask each target gene before clustering guides by phenotype.
- **It acts on:** which guides are admitted, and how the mechanism is read.
- **Read it as:** co-clustering that rests on the target genes themselves, or on a shared seed, is a suspect guide
  effect, not a robust biological grouping. Selecting guides because several agree makes a later consistency check
  depend on that selection.
- **Stop or turn when:** consider keeping the finding as a candidate, and naming the guide question (a candidate, not
  validated), when an off-target explanation remains open.
- **Source:** *Comprehensive transcription factor perturbations recapitulate fibroblast transcriptional states*,
  Nat Genet 2025, doi:10.1038/s41588-025-02284-1. Pertura's choice: open off-target questions keep a candidate.
