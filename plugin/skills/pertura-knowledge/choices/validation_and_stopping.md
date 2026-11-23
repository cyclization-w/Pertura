# Validation, changing the experiment, and when to stop

## V1. Trajectories and pseudotime

- **If you see:** a target that seems to shift cells along a trajectory or a pseudotime.
- **Consider:** checking the trajectory with methods of different assumptions when the topology is unknown, and
  the kinetics behind any velocity estimate.
- **It acts on:** the state process and the model's assumptions.
- **Read it as:** an embedding or pseudotime does not show a dynamic or causal process. A shift along it is first a
  change of states (composition).
- **Stop or turn when:** consider stopping the higher-level interpretation, and recording the composition finding as
  such, when the reference states or the kinetic assumptions are not supported.
- **Source:** *Best practices for single-cell analysis across modalities*, Nat Rev Genet 2023,
  doi:10.1038/s41576-023-00586-w. Pertura's choice: unsupported, it stays a composition finding.

## V2. A candidate nominated by correlation

- **If you see:** a gene or regulator that correlates with the readout of interest across cells or conditions.
- **Consider:** treating the correlation as a reason to nominate the candidate, and testing it by perturbation
  (here, its own guides) before any causal reading.
- **It acts on:** candidate selection and validation.
- **Read it as:** correlation nominates; a perturbation tests. A candidate list chosen from correlations is a prior
  for the screen, not a finding of it.
- **Stop or turn when:** consider keeping the candidate for another experiment (a candidate, not validated) when it has
  no guide in the screen.
- **Source:** *Characterizing the molecular regulation of inhibitory immune checkpoints with multimodal single-cell
  screens*, Nat Genet 2021, doi:10.1038/s41588-021-00778-2. Pertura's choice: without a guide, it stays a candidate.

## V3. A clear separation in a supervised plot

- **If you see:** perturbed and control cells cleanly separated in an embedding made after a supervised step (a
  discriminant analysis on the labels, then UMAP).
- **Consider:** reading it as a display of the difference the method was trained to find, not as evidence.
- **It acts on:** the interpretation of a figure.
- **Read it as:** a supervised projection separates labelled groups by construction, so the separation is not an
  independent validation. Judge the effect by the test, against controls.
- **Stop or turn when:** consider ending the thread without a finding (no new information) when the separation is the
  only support.
- **Source:** *Characterizing the molecular regulation of inhibitory immune checkpoints with multimodal single-cell
  screens*, Nat Genet 2021, doi:10.1038/s41588-021-00778-2. Pertura's choice: a separation alone is not a finding.

## V4. Combining replicates' tests

- **If you see:** a target tested in several mice or replicates, and a choice of how to combine their p-values.
- **Consider:** choosing the combination by the claim:
  - a liberal combination (Fisher's) tests that not every replicate is null;
  - a conservative one (the largest p) tests that every replicate shows the effect.
- **It acts on:** the claim made across replicates.
- **Read it as:** these are two claims, not two p-values for one claim, so never pick the one that gives more hits.
  Replicates without a defined value are left out of the test and counted as such, not set to zero effect.
  Published screens mostly run one test, on the pooled cells with the replicate as a covariate, or on pseudobulk.
  Few combine per-condition tests by Fisher's method. The choice arises only where per-replicate tests exist.
- **Stop or turn when:** consider `not_enough_data`, and saying so, when the claim needs every replicate and some lack
  values.
- **Source:** *Framework for in vivo T cell screens*, J Exp Med 2024, doi:10.1084/jem.20230699. Pertura's choice:
  missing values for such a claim give `not_enough_data`.

## V5. A thread that has said what it can

Papers report the final pipeline, not the routes they dropped, so there is no published stopping rule to copy. The
cards' stops are Pertura's choices, in the record's terms: signals to weigh, not commands. A question closes as:
- **cannot_answer:** a missing unit, reference or readout, or a design with no overlap;
- **not_enough_data:** efficacy or coverage not shown.

A thread ends with:
- the explanation weakened: a diagnostic did not support the explanation;
- a new question: the population, comparison or outcome changed;
- a candidate, not validated: candidates waiting for an independent test;
- no new information: more of the same.

Never end a route only because it gave few hits. Never continue one only because it gives many.
