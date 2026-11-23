# Weak, continuous or mixed responses

## S1. A graded response

- **If you see:** a target's cells spread along a gradient of response (common under CRISPRi), so a split into
  responders and non-responders fits badly.
- **Consider:** modelling the response as continuous: a per-cell perturbation score as a weight or a term in the
  model, with an explicit rule for targets whose response is too weak to score.
- **It acts on:** what "the response" is. A score is not a measured truth of editing.
- **Read it as:**
  - record where the score came from, and that it was learned on the same data;
  - when testing genes that went into the score, leave them out of it;
  - a continuous model suits graded responses. It is not right for every modality.
- **Stop or turn when:** consider stopping the scoring and falling back to the unweighted analysis when too few genes
  respond to build the score. The source did so, and did not call the perturbation ineffective (`not_enough_data` for
  that target's graded response).
- **Source:** *Systematic reconstruction of molecular pathway signatures using scalable single-cell perturbation
  screens*, Nat Cell Biol 2025, doi:10.1038/s41556-025-01622-z.

## S2. Selecting cells by their response

- **If you see:** a wish to keep only the cells where the perturbation "worked" (strong knockdown, a high score),
  or to drop low responders.
- **Consider:** that this changes the population. The result then holds for the cells that responded, selected by
  an outcome of the perturbation.
- **It acts on:** the population. It is another question.
- **Read it as:**
  - a population chosen in advance (a cell type, a state fitted without perturbation labels) differs from one
    chosen by response after the perturbation;
  - the two answer different questions;
  - write down which one the thread makes;
  - published screens do make this selection: those that say what they do with the cells Mixscape calls not
    perturbed discard them.
- **Stop or turn when:** consider a question of its own, beside the analysis of all the target's cells,
  when the selection is by response. Its results say which cells they read. A fixed test that selects the same cells
  does not make the two one question. A selection by response treats control cells, which have no perturbation,
  differently from targets.
- **Source:** *Causal effect estimation from trans-regulatory single-cell CRISPR screens*, Cell
  Genomics 2026, doi:10.1016/j.xgen.2026.101251. Pertura's choice: a selection by response is a thread.

## S3. Cells with a guide but no response

- **If you see:** cells carrying a target's guide that look like controls (escapees: in-frame edits, no effective
  knockdown).
- **Consider:** classifying each cell against the control cells, with a mixture whose not-perturbed component is the
  controls. The source removed the cells classed not perturbed before its downstream analyses, because keeping them
  would dampen each knockout's effects. By default, read all the assigned cells, with the perturbed class as an
  option. Each result says which cells it reads.
- **It acts on:** the classification of the response, and with it the population.
- **Read it as:**
  - the share classed not perturbed is not the editing failure rate. An edit without a detectable expression shift
    is classed not perturbed, and cell death changes the apparent rate;
  - a class defined by the outcome makes the downstream effect depend on that selection.
- **Stop or turn when:** consider `not_enough_data` when no expression shift separates the classes, rather than forcing
  a perturbed class. This does not mean the gene has no function.
- **Source:** *Characterizing the molecular regulation of inhibitory immune checkpoints with multimodal single-cell
  screens*, Nat Genet 2021, doi:10.1038/s41588-021-00778-2. Pertura's choice: all assigned cells by default.
