# From judgements to analysis choices

Advice, not rules. These entries come from published screens and methods papers, each read for what the authors saw,
what they chose because of it, what the choice acted on, and how they read the result. Nothing here comes from the
papers of the screens Pertura is developed or evaluated on. Each row of HJ1 names its cards, and the cards give the
sources. No paper's findings are given. The cards are in `choices/`.

## HJ1. Choices worth trying, with their reasons

| What was seen | A choice worth trying | What it acts on | How to read the result | Cards |
|---|---|---|---|---|
| Cell-level tests give false discoveries tied to variation between replicates | Pseudobulk by the real replicate (mouse, donor, sample) | The observations and the estimator. Random pseudo-replicates do not replace biological replicates | Judge by how negative controls behave (control guides tested as if they were targets), never by which choice gives more hits | U1, U2 |
| Control guides differ for technical reasons (batch, lane) | Add a justified batch term, and check it on the control guides | A covariate. A confounder fixed before the perturbation is not a variable the perturbation changes | Where perturbed and control cells do not overlap in a batch, no model makes the comparison identifiable | U3 |
| Guide-calling rules differ in purity and coverage | Compare calling rules, and check how the library was read | The assignment, not the downstream test | Look at false calls and real coverage, not at the number of hits | G1, G2 |
| A target's guides disagree | Check knockdown, off-target and seed effects | Guide efficacy, and how guides are combined | Keep the conflict in the record rather than merge it into a gene-level result. Published screens drop such guides, or tier a finding by the guides that support it | G4, G5 |
| Responses are continuous and weak (CRISPRi), so a split into responders and non-responders fits badly | Model the response as continuous, with an explicit rule for small responses | The cell's response. A score is not a ground truth of editing | Record where the score came from, and that it was learned on the same data | S1 |
| Hit counts may reflect cells, depth and noise rather than biology | Estimate the distribution of true effects from the effects and their standard errors | The quantity asked. A transcriptome-wide impact is not another hit list | Check the inputs' standard errors and units first. Consider stopping the comparison if they are unreliable | ES1 |
| Enrichment depends on the background that could be measured | State the detected gene universe, the database's version and the test family | The interpretation | Do not change the background or the database to reach a wanted pathway | PG4 |

## HJ2. Before the method, the question

Most choices that matter change the question, not the method. Within one question, the population, the comparison
and the quantity are fixed. The quantity an analysis aims to estimate is its estimand (Christensen et al. 2026; U4,
PF17). Expression within one cell state, the mean over all cells, and the share of cells in that state are three
different questions. One check does not serve all three. Selecting cells by their response is not a technical
filter: it changes the population.

## HJ3. What a published choice changes

Published screens make choices that Pertura's defaults do not. The screens that say what they do with the cells
Mixscape calls not perturbed discard them. Tian et al. 2019 kept, for its tests, the half of each target's cells with
the best on-target knockdown. Such a choice changes the population. The result then holds for the cells that
responded, selected by an outcome of the perturbation (PF17). It does not hold for all the cells given the guide. A
guide picked to stand for a target because its effect is the largest gives an inflated effect (PF18). Whether the
choice is a paper's or Pertura's, a result says which population, reference and guides it rests on, and what each
changes.

## HJ4. The point to stop

Papers mostly report the final pipeline, not the routes they tried and rejected. There is no published stopping rule
to copy, so these are Pertura's own. A finding or a thread states its standing in one of these words:

- **detected** or **not_detected**, with the runs it rests on and its figure. A not_detected without an interval or
  a test against a margin is imprecise, not evidence of no effect (ES5);
- **not_enough_data**: coverage or an effective perturbation is not shown. This is not evidence of no effect;
- **cannot_answer**: the design is confounded, units are missing, or there is no matching reference or readout.
  Say what is missing.

A thread ends when it has given what it can. Its reason says which end it reached:

- the explanation is weakened: a diagnostic or independent evidence does not support it. Record it; the thread can
  end there. Never drop a route only because it gave few hits;
- a new question: the population, mechanism, comparison or outcome would have to change. Open it as a thread of its
  own;
- a candidate, not validated: the next step is validation or the user's decision. A candidate is still called a
  candidate;
- no new information: more of the same would add nothing.
