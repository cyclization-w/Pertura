---
id: P6
version: 6
updated: 2026-11-09
name: Each combination against its singles, then why it interacts
use_when: >-
  The screen puts two or more perturbations in the same cells (designed pairs, one perturbation in every cell with a
  second one varying, or cells that carry several guides), and the question is how they combine: whether a pair does
  what its singles do together, or something else. Needs control guides and cells that carry pairs.
not_when: >-
  Only single perturbations were measured (an interaction between regulators can then only be predicted from their
  signatures, not measured); a pair without its singles in the same units; too few control guides for control
  pseudo-pairs.
bring: [optional - a published set of interacting pairs or complexes for P6.13, optional - the two factors' binding data for P6.13]
requires: [pairs, control_guides]
steps:
  - id: P6.1
    does: each testable pair's profile as a combination of its singles'
    part: module:E.combinations
    control: the pair and both singles in the same units with enough cells; control pseudo-pairs at the pair's cell
      numbers through the same fit; each member acting in the pair's cells
    fork: the expectation that counts as no interaction
    fork_default: the pair fitted as a weighted sum of its singles' (Norman et al. 2019's fit; E.combinations'
      fitted_weights)
  - id: P6.2
    does: each pair's place between its singles
    part: module:E.similar_targets
    control: centred profiles; the targeted genes left out; pairs of control guides as the reference
  - id: P6.3
    does: the kind of each pair, from its weights and from what its singles leave unexplained
    part: missing:the pairs grouped by their fitted weights and unexplained part (combinations returns them per pair, not grouped)
    control: the weights against another measure of the interaction where the screen has one; the fit's R-squared;
      known complexes falling together
  - id: P6.4
    does: the pair's changed genes, shared with its singles or only the pair's
    part: missing:the overlap of a pair's changed genes with each single's (shared and pair-specific), then what each group is
    control: each set from its own comparison with the non-targeting cells
  - id: P6.5
    does: each gene's mode in the pair, synergistic, buffering or additive
    part: module:E.combinations
    control: the same calls for each single's genes, as the comparison
  - id: P6.6
    does: each pair's observed and additive score on the readouts that matter
    part: module:E.combinations
    fork: the readouts that matter for this question
    fork_key: true
    control: the singles' average, product or sum as the expectation; a second readout of another kind agreeing
  - id: P6.7
    does: the pair's effect on cell states
    when: the question is about cell states, a state one perturbation induces and whether a second carries it
    part: module:C.states_and_composition
    control: the pair, each single and the control cells compared on the same states, each group's cells counted
  - id: P6.8
    does: each partner's effect against the anchor alone
    when: one perturbation is in every cell and a second one varies
    part: missing:each partner arm's changed genes against the anchor with a non-targeting partner as the reference, by the paper's test (MAST)
    control: the anchor with a non-targeting partner as the reference; the non-targeting partners put through the same test
  - id: P6.9
    does: the anchor's response after the partner, as a residual and as the share moved towards zero
    when: one perturbation is in every cell and a second one varies
    part: missing:a partner arm's per-gene residual from the anchor-only response, and the share of the anchor's response genes moved towards zero
    control: the anchor-only response as the reference
  - id: P6.10
    does: interaction terms for cells that carry several guides by chance, per gene and per module of perturbations
    when: the cells carry several guides by chance, as at high MOI, not by design
    part: missing:a model over all the cells with an interaction term per pair of perturbations, per gene and per module of perturbations
    control: perturbations gathered into modules where per-gene interactions lack power; the same modules in a larger
      conventional screen where there is one
  - id: P6.11
    does: the perturbed factors each gene needs, and the contexts that change a factor's effect
    when: all subsets of a small set of factors, or the perturbed branches of one pathway, are in the screen
    part: missing:per gene, the fewest factors it needs, the contexts that change a factor's effect, and the regulons' overlap
  - id: P6.12
    does: a perturbation's interaction with a treatment
    when: a treatment or condition is crossed with the perturbations
    part: module:E.condition_x_perturbation
    control: each effect against the control cells of the same condition
  - id: P6.13
    does: the reason the pair interacts, from outside knowledge
    part: outside_data
    control: the additive genes or pairs as the comparison for the interacting ones
  - id: P6.14
    does: choose the leads
    part: notebook
    fork: the pairs that go to validation
    fork_key: true
    control: the departure beyond control pseudo-pairs; both members acting; the departure agreeing between units
  - id: P6.15
    does: propose the validation
    part: other_experiment
leads: >-
  The pairs whose departure from the singles goes beyond the control pseudo-pairs, whose members both act and whose
  departure agrees between units, that a second kind of readout also singles out; then the ones a rerun of the double
  perturbation reproduces.
connects:
  - at: P6.1
    to: P1
    seen_in: [Lee J 2026]
  - at: P6.1
    to: P7
    seen_in: [Norman 2019, Reisman 2026]
  - at: P6.6
    to: P2
    seen_in: [Xia 2023]
  - at: P6.7
    to: P3
    seen_in: [Xia 2023]
papers: [Wessels 2023, Xia 2023, Santinha 2023, Yao 2023, Liu 2024, Otto 2023, Aryal 2026, Dixit 2016, Adamson 2016,
  Norman 2019, Hou 2022, Lee J 2026, Chang 2026, Markham 2026, Reisman 2026]
---

> Sources are [paper, panel], or step n of the paper's chain, with the paper named by ID and name. Each step in the
> header names the module that runs it. `missing:` marks a step Pertura has no part for yet; it is done in the
> notebook.

The steps are how published screens worked through this question, step by step: an example to adapt to the screen
and the user's question, not an order to follow. The agent may skip, reorder or add a step and say why; the record
keeps it. Only the design gate blocks anything [ADR 0030, autonomy; ADR 0027].

## Scope

These papers put two or more perturbations in the same cells: designed pairs with the singles measured alongside
[PMC10030154 Wessels 2023, Fig. 4c], one perturbation in every cell with a second varying [PMID41676564 Reisman 2026,
Fig. 1F,G], or cells that carry several guides by chance [PMC11035494 Yao 2023, Fig. 5f]. They measure what a pair
does that its singles do not. Each pair is set against its singles as a whole profile [PMC6746554 Norman 2019, Fig.
4A-F], then gene by gene [PMC5181115 Dixit 2016, Fig. 5B-E], then on the readouts the question names [PMC10562403 Xia
2023, Fig. 5c-e].

With singles only, an interaction can only be predicted, as an index per pair, and then tested in another experiment
[PMC13149302 Aryal 2026, Fig. 3A-B, step 7].

## The steps' judgement

**P6.1.** In one screen the double had 169 cells, against 449 and 400 for its singles [PMC10562403 Xia 2023, Fig.
3g]. So the pair and both singles are counted in the same units first, and control pseudo-pairs are drawn at the
pair's cell numbers [E.combinations returns; pt.pair_fit returns]. The fit writes the pair's profile as two weights on
its singles' profiles (mean R-squared 0.71) [PMC6746554 Norman 2019, Fig. 4A-F]. The same regression explained about
40% of a deletion's signature from three singles' [PMC10567566 Santinha 2023, Fig. 4f-h].

**P6.2.** CBL/CNN1 cells ran from unperturbed through the single to the double [PMC6746554 Norman 2019, Fig. 3C-E].
E.similar_targets correlates each pair's profile with each single's [E.similar_targets returns].

**P6.3.** The weights name the kind: epistasis (KLF1/CEBPA, 0.19 and 0.72), suppression (PTPN12/SNAI1, 0.60 and 0.57),
synergy (CBL/CNN1, 1.24 and 0.8) [PMC6746554 Norman 2019, Fig. 4A-F]. Wessels grouped the fits into four interaction
clusters; 33 pairs fit well, many members of one complex [PMC10030154 Wessels 2023, Fig. 4g, Ext. Data Fig. 8d-f].

**P6.4 and P6.5.** SMARCA4/2, SMARCC1/2 and SMARCD1/2 were synergistic; ARID1A/B, GLTSCR1/1L and cross-subcomplex
pairs were additive [PMC12593249 Otto 2023, Fig. 5D-F, 6B-E]. E.combinations calls the modes with model
regulatory_modes, on a genes step's DE tables [E.combinations choices, inputs].

**P6.7.** Cells with both guides had a lower state-4 fraction than Cebpb cells [PMC10562403 Xia 2023, Fig. 3g].
C.states_and_composition gives each pair's share of each state against its controls [C.states_and_composition returns].

**P6.8 and P6.9 (an anchor).** The partner is read against the anchor alone, with a non-targeting partner where the
library has one: the paired gRNAs changed 28, 18 and 8 genes on average (positive-hit, negative-hit, non-targeting)
[PMID41676564 Reisman 2026, Fig. 1K]. Most genes stayed within 0.3 of the anchor-only response [PMID42146330 Chang 2026,
Ext. Data Fig. 6f].

**P6.10.** Where guides share cells by chance, per-gene interactions lack power. With perturbations gathered into
modules, three modules showed intra-module interactions in guide-pooled data, and none did in a larger conventional
screen [PMC11035494 Yao 2023, Fig. 5f, Suppl. Fig. 6]. A program modelled on both genes' status showed no synergy
[PMID42327190 Lee J 2026, Fig. 4E].

**P6.11.** Tnnt2 rose with Gata4 alone, and Hcn4 needed Hand2 plus Mef2c. TBX5 induced Hcn4 beside HAND2 or MEF2C
alone but repressed it beside both [PMID42146410 Markham 2026, Fig. 3C-F]. PERK/ATF4 had the largest and most specific
regulon of the three UPR branches [PMC5315571 Adamson 2016, Fig. 3F].

**P6.12.** A treatment crossed with a perturbation asks the same question: a KO-by-treatment interaction model (FDR <
0.25) [PMC9732527 Hou 2022, Fig. 6A-B], or an observed-minus-additive synergy score per perturbation [PMC11457336 Liu
2024, Fig. 4H]. E.condition_x_perturbation tests a target's difference between two conditions per program
[E.condition_x_perturbation returns].

**P6.13.** The dominant-interaction genes, not the additive ones, were enriched for co-binding of Nfkb1 and Rela
[PMC5181115 Dixit 2016, Fig. 5F].

## Forks

**P6.1 fork: the expectation that counts as no interaction.** Papers took:
- the singles' combined effect: the average and the product of the singles on one marker [PMC10030154 Wessels 2023,
  Fig. 4c], or an observed-minus-additive score [PMC11457336 Liu 2024, Fig. 4H];
- the pair fitted as a weighted sum of its singles' [PMC6746554 Norman 2019, Fig. 4A-F; PMC10030154 Wessels 2023, Fig.
  4g];
- an interaction term per gene in a model over the cells [PMC12593249 Otto 2023, Fig. 5D-F, 6B-E; PMC5181115 Dixit 2016,
  Fig. 5B-E].

E.combinations offers fitted_weights (the module's default), plain_sum and regulatory_modes [E.combinations choices].
The papers do not settle on one way. The card's default is the weighted sum of the singles, Norman et al.'s fit, as
Norman and Wessels read their pairs [PMC6746554 Norman 2019, Fig. 4A-F; PMC10030154 Wessels 2023, Fig. 4g]. It is an
analytic choice, not the user's: the agent chooses the model and says why, workers run the other models beside it,
and the report says what the choice moves [ADR 0032, D8 and D9].

**P6.6 fork: the readouts that matter for this question.** Papers chose:
- one marker, CD11b, or a sorted marker with each partner's effect paired against its effect alone [PMC10030154 Wessels
  2023, Fig. 4c; PMID41676564 Reisman 2026, Fig. 1F,G];
- two readouts of different kinds, a combination counting when high on both: a surface-protein score and an expression
  program [PMC10562403 Xia 2023, Fig. 5a-e];
- a program that only two regulators moved [PMID42327190 Lee J 2026, Fig. 4E];
- the whole profile [PMC6746554 Norman 2019, Fig. 4A-F].

The user's question, recorded at the start, decides among these [ADR 0030, the flow]. E.combinations returns each
program's observed and additive score, from hallmark sets or given ones [E.combinations returns, choices].

**P6.14 fork: the pairs that go to validation.** Papers took:
- the highest interaction index among regulators of one program: MEN1-KAT6A (synergy index 86) [PMC13149302 Aryal 2026,
  Fig. 3A-B];
- the combination high on two readouts: Med12+Cebpb [PMC10562403 Xia 2023, Fig. 5c-e];
- the partners that converge on one pathway: competing end-joining genes with NBN [PMID42146330 Chang 2026, Fig. 2g].

## Choosing the leads

A strong lead is a pair whose departure goes beyond the control pseudo-pairs at its own cell numbers and agrees between
units, and whose members both act [E.combinations returns, fails_when; paradigms_of_deep_perturbseq_analysis.md, P6
controls]. One that two kinds of readout both single out is the stronger result [PMC10562403 Xia 2023, Fig. 5c-e].

## Validation

Papers re-tested the lead one pair at a time, as with a drug pair for a predicted synergy: Menin and KAT6A inhibitors
(ZIP 20.19) [PMC13149302 Aryal 2026, step 7].


## Connections

- P1 comes first when only two regulators move one program [PMID42327190 Lee J 2026, Fig. 4E].
- P7 comes first when a pooled screen picked the pairs: a fitness interaction [PMC6746554 Norman 2019, Fig. 3C-E], a
  MAP2-sorted paired screen [PMID41676564 Reisman 2026, Fig. 1F,G].
- P2 enters at P6.6 when a score ranks the combinations [PMC10562403 Xia 2023, Fig. 5a,b]; P3 enters at P6.7 when the
  pair is read on cell states [PMC10562403 Xia 2023, Fig. 3g].

## Pitfalls, each with its check

- **A member that does not act makes a pair look like its other single.** Check: each member's target gene in the pair's
  cells before epistasis is called [E.combinations fails_when; pitfalls.md: Knockdown of the target gene].
- **A departure can agree between units by chance, more so with few cells, and per-gene interactions lack power.**
  Check: control pseudo-pairs at the pair's cell numbers; a power analysis beforehand; the cells of the pair and of each
  single beside every call; modules in place of genes [E.combinations fails_when;
  paradigms_of_deep_perturbseq_analysis.md, P6 controls; PMC11035494 Yao 2023, Fig. 5f; pitfalls.md: Rival explanations,
  cell numbers].
- **A pair is one construct, so a departure may be its dose.** Check: said with each departure; a second construct per
  pair settles it [E.combinations fails_when; pitfalls.md: Knockdown of the target gene, guides differ in strength].
- **A shift of states read as a change of genes, or a pair-only state that is doublets.** Check: pt.composition, then
  the genes within one state; the state's markers [pitfalls.md: Rival explanations, composition and doublets;
  PMC10562403 Xia 2023, Fig. 3g].
- **A reference that answers another question.** Check: each call names its reference, the control cells for what the
  pair does, the anchor with a non-targeting partner for what the partner adds [pitfalls.md: Rival explanations, the
  controls; PMID41676564 Reisman 2026, Fig. 1K].

## Worked chains

**Chang 2026, PMID42146330 (one gene activated and one knocked down in each cell).** Three resection genes dominated the
activation arm -> NBN-first combinations paired with competing end-joining genes [Fig. 2g] -> each dual arm against the
NBN-only reference with fGSEA, all three partners suppressing interferon responses [Fig. 4e,f] -> per-gene residuals
from the NBN-only response, 2-4% deviating [Ext. Data Fig. 6f] -> each partner moving about half the primed genes
towards zero [Fig. 4h].

**Dixit 2016, PMC5181115 (pairs and triplets of transcription factors in cells that carry several guides).** A model
with interaction terms classed each pair's targets as additive, synergistic, buffering or dominant [Fig. 5B-E] -> Nfkb1
and Rela targets split into additive and dominant-interaction sets, each tested for ChIP enrichment: only the
interaction set was enriched for co-binding [Fig. 5F].
