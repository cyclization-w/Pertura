---
id: P3
version: 4
updated: 2026-11-09
name: Cell states first, then which perturbations shift them
use_when: >-
  The question is which perturbations move cells between states (a disease-associated state, a mature or
  differentiated state, a cell fate), and the screen's cells span several states. The states are found in the cells
  without the perturbation labels, then each perturbation's share of each state is read against the control cells.
  Needs control guides.
not_when: >-
  The cells are one state and the question is which genes or programs change (P1); a contrast or a signature defines
  the outcome (P2); the order of the states in time is the question itself, which needs a time course and a
  trajectory step Pertura does not have yet.
bring: [optional - a reference atlas or time course of the cell type for P3.1 and P3.7, optional - a state's marker for flow or imaging in P3.10]
requires: [control_guides]
steps:
  - id: P3.1
    does: the cell states, found without the perturbation labels
    part: module:C.states_and_composition
    control: the states fitted on all cells with the labels hidden; the control cells present in every state; clusters
      of cells that lost the cell type's identity set aside before any shift is read
    fork: the source of the states and how fine they are
    fork_default: Leiden clusters of the screen's cells named by their markers, at the module's resolution
  - id: P3.2
    does: each perturbation's perturbed cells, found before its shares are read
    when: editing or knockdown is mosaic, so a perturbation's cells hold escapers
    part: module:B.escape
    control: the control-guide groups through the same classifier
  - id: P3.3
    does: each perturbation's share of each state against the control cells of its own samples
    part: module:C.states_and_composition
    control: the control cells' distribution over the states; the test kept within replicates or samples; groups of
      control guides through the same test
    fork: the test of the shares
    fork_default: the design's test for each target (the module's auto)
  - id: P3.4
    does: a shift checked as the perturbation's, not one clone's or one guide's
    when: in vivo or in organoids, where a perturbation's cells may be a few clones
    part: module:B.clonality
    control: each guide alone; clone barcodes where the library has them
  - id: P3.5
    does: the genes changed within each state, beside the shift of its share
    part: module:C.within_states
    control: each state's own control cells; the cells of each side counted
  - id: P3.6
    does: a state's identity, above all a new state that one perturbation makes
    part: tool:states
    control: the state's markers against doublet markers; a reference cell type or atlas where the user brings one
  - id: P3.7
    does: the states' place on a course, and each perturbation's cells on it
    when: the states form a course (differentiation, maturation, reprogramming)
    part: tool:states trajectory_root
    control: the control cells' place on the same course
  - id: P3.8
    does: the functions each state carries, scored per state and perturbation
    part: tool:scores
    control: the field's gene sets or the user's, their genes stated; the control-guide groups' scores
  - id: P3.9
    does: choose the state of interest and the leads
    part: notebook
    fork: the state that is the outcome, and the shifts that go to validation
    fork_key: true
    control: the shift beyond the control-guide groups, held within samples, the perturbation's guides agreeing
  - id: P3.10
    does: propose the validation
    part: other_experiment
leads: >-
  The perturbations whose shift toward the state of interest goes beyond the control-guide groups, is held within
  samples and is seen with each of their guides; then the ones an arrayed rerun reproduces on the state's marker.
connects:
  - at: P3.5
    to: P1
    seen_in: [Saputra 2025]
  - at: P3.8
    to: P1
    seen_in: [de la Rosa 2025, Aguado-Alvaro 2025]
  - at: P3.3
    to: P6
    seen_in: [Xia 2023]
papers: [de la Rosa 2025, Cai 2026, Saputra 2025, Aguado-Alvaro 2025, Taguchi 2025, Yuan 2026, Li C 2023, Draeger 2022,
  Xia 2023]
---

> Sources are [paper, panel], or step n of the paper's chain, with the paper named by ID and name. Each step in the
> header names the module that runs it. `missing:` marks a step Pertura has no part for yet; it is done in the
> notebook.

The steps are how published screens worked through this question, step by step: an example to adapt to the screen
and the user's question, not an order to follow. The agent may skip, reorder or add a step and say why; the record
keeps it. Only the design gate blocks anything [ADR 0030, autonomy; ADR 0027].

## Scope

These papers first find the states the cells take, then test which perturbations move cells between them [PMC12880918
de la Rosa 2025, Fig. 3b-e; PMID41916284 Cai 2026, steps 3 and 6; PMC10499611 Li C 2023, steps 3 and 5]. The state is
the outcome: a disease-associated state [PMC9448678 Draeger 2022, steps 6-8], a mature state [PMID41270741 Taguchi
2025, steps 4-5], a reprogrammed cell type [PMID41916284 Cai 2026, step 6] or a lineage [doi:10.1016/j.stem.2026.09.005
Yuan 2026, step 4]. A shift in a target's share of a state is itself the target's effect [pitfalls.md: PF11].

## The steps' judgement

**P3.1.** The microglia screen set aside two clusters of cells that had lost microglial identity. It then found nine
states on two axes, activation and proliferation, and its non-targeting guides were present in every state
[PMC9448678 Draeger 2022, step 6]. The organoid screen annotated its cell types on the control and uninduced cells
[PMC10499611 Li C 2023, step 3]. C.states_and_composition finds the states without the labels and names them by their
markers [C.states_and_composition returns].

**P3.2.** Knockout cells were told from escapers with Mixscape before the subtypes were annotated and their shares
tested [PMC13155148 Saputra 2025, steps 4-5]. B.escape gives each target's share of perturbed cells, calibrated on
control-guide groups [B.escape returns].

**P3.3.** Each perturbation's share of each state was read against the non-targeting cells: by Fisher's exact test with
Benjamini-Hochberg correction [PMID41916284 Cai 2026, step 6], by a Cochran-Mantel-Haenszel test stratified by library
replicate [PMC10499611 Li C 2023, step 5], by scCODA with bootstrapping [PMC13155148 Saputra 2025, step 5], or as the
perturbation's distribution over states against the non-targeting background under each condition [PMC12749234
Aguado-Alvaro 2025, step 3]. A density difference on the embedding, knockout cells minus control cells, shows where the
cells went [PMC12880918 de la Rosa 2025, Fig. 3e]. Known regulators moving the states their published roles predict
are the positive control [doi:10.1016/j.stem.2026.09.005 Yuan 2026, step 4].

**P3.4.** Organoids are clonal, so each integration carried a clone barcode: about 125 clones per perturbation, with
4.4 cells each [PMC10499611 Li C 2023, step 2]. The composition effects were then checked across organoid batches
[PMC10499611 Li C 2023, step 6]. B.clonality gives the largest share of a target's cells held by one guide in one unit
[B.clonality returns; pitfalls.md: PF3].

**P3.5.** Most of Cebpb's effect was a change of the cells' distribution over states, not of genes within a state
[PMC10562403 Xia 2023, step 5]. Within each fibroblast subtype, knockout cells were compared with non-targeted cells.
Some pathway changes were one perturbation's, and others were shared [PMC13155148 Saputra 2025, step 7].
C.within_states tests each target's cells against the control cells inside each state, beside the shift of its share
[C.within_states returns].

**P3.6.** TGFBR1 loss made a mixed cluster, about 53% iCAF and 35% ecmCAF by label transfer, whose own changed genes and
pathways carried the bulk interferon rise [PMC13155148 Saputra 2025, steps 5-6]. A state held by one perturbation is
worth checking for doublets first [pitfalls.md: PF13].

**P3.7.** Perturbed hepatocytes were projected onto a wild-type time course: the control cells sat with the embryonic
and neonatal clusters, most cells of the Nr1i3-driven cluster with the adult ones [PMID41270741 Taguchi 2025, step 5].
Induced cardiomyocytes lay at the end of a pseudotime path apart from the fibrotic fate [PMID41916284 Cai 2026, step
5]. Pertura has no module for this step yet; it is done in the notebook [paradigms_of_deep_perturbseq_analysis.md, P3
and P4].

**P3.8.** Curated signatures of lesion-promoting and lesion-resolving functions were read per cluster, each knockout's
per-gene changes drawn as quantile bands around the median, with GSEA for significance [PMC12880918 de la Rosa 2025,
Fig. 4a, 5a]. Collagen biosynthesis, stress-fibre and ECM gene sets were scored per perturbation [PMC12749234
Aguado-Alvaro 2025, step 4]. C.programs scores the field's or the user's gene sets per target [C.programs returns].

## Forks

**P3.1 fork: the source of the states and how fine they are.** Papers took:
- clusters of the screen's own cells named by their markers [PMC12880918 de la Rosa 2025, Fig. 3b; PMC9448678 Draeger
  2022, step 6];
- cell types annotated on the control cells, then every cell assigned [PMC10499611 Li C 2023, step 3];
- a reference course of the unperturbed cell type, with the perturbed cells projected onto it [PMID41270741 Taguchi
  2025, step 5].

The resolution and the states' names change what counts as a shift, so the other readings run beside the chosen one in
workers [ADR 0032, D9; C.states_and_composition choices].

**P3.3 fork: the test of the shares.** Papers took Fisher's test per state [PMID41916284 Cai 2026, step 6], a test
stratified by replicate [PMC10499611 Li C 2023, step 5] or scCODA [PMC13155148 Saputra 2025, step 5].
C.states_and_composition's test is one of fisher, cmh, propeller and sccoda, and auto takes the design's test for each
target [C.states_and_composition choices]. Where there are replicates, the test that keeps them as the unit is the
design's [pitfalls.md: PF2; U1].

**P3.9 fork: the state that is the outcome, and the shifts that go to validation.** Papers took:
- the state the question names, then the perturbation most enriched in it at both time points [PMID41916284 Cai 2026,
  step 6];
- two perturbations that moved one disease-associated state in opposite directions [PMC9448678 Draeger 2022, steps
  7-8];
- the top knockouts expanding the stem-cell state, tallied by their perturbation cluster
  [doi:10.1016/j.stem.2026.09.005 Yuan 2026, step 7];
- a perturbation with one of the strongest composition effects whose own regulatory module was enriched in its changed
  genes [PMC10499611 Li C 2023, step 10].

## Choosing the leads

A strong lead's shift toward the state of interest goes beyond the control-guide groups, is held within samples, and is
seen with each of its guides [C.states_and_composition returns, fails_when; F.guides_agree]. Papers then took the ones
an arrayed rerun reproduced: five shRNAs re-tested one at a time, Calr the strongest [PMID41916284 Cai 2026, step 9];
all 61 activations re-tested by RT-qPCR [PMID41270741 Taguchi 2025, step 7].

## Validation

- the state's marker measured by flow cytometry, and the cells split by it [PMC9448678 Draeger 2022, step 8];
- staining of the progenitors the screen found depleted or enriched, in individually perturbed organoids [PMC10499611
  Li C 2023, step 6];
- single knockouts transferred with control cells into the same animal, so each animal is its own control
  [PMC12880918 de la Rosa 2025, Fig. 2b-i];
- a drug for the regulator at non-toxic doses, moving the same state [PMC9448678 Draeger 2022, step 9].


## Connections

- P1 enters when a state's functions are read as gene programs [PMC12880918 de la Rosa 2025, Fig. 4a; PMC12749234
  Aguado-Alvaro 2025, step 4], or the genes within a state's cells [PMC13155148 Saputra 2025, step 7].
- P6 enters when pairs are read on states [PMC10562403 Xia 2023, step 5].

## Pitfalls, each with its check

- **A shift that is one clone's or one guide's.** Check: each guide alone; clone barcodes where the library has them
  [PMC10499611 Li C 2023, step 2; B.clonality; pitfalls.md: PF3].
- **Escapers dilute a shift.** Check: B.escape before the shares are read, the perturbed cells kept as a cell set that
  says so [PMC13155148 Saputra 2025, step 4; pitfalls.md: PF17].
- **A technical cause of a shift.** Check: guide-UMI depth and cell numbers per perturbation, before the biology
  [pitfalls.md: PF12].
- **A state of one perturbation that is doublets.** Check: its markers [pitfalls.md: PF13].
- **Cells that lost the cell type's identity read as a state.** Check: those clusters set aside before shifts are read,
  and said so [PMC9448678 Draeger 2022, step 6].
- **Cells taken as the replicates.** Check: a test stratified by replicate or run by sample [PMC10499611 Li C 2023,
  step 5; pitfalls.md: PF2].
- **The model system changes the answer.** Check: the same perturbations in another condition, as a contrast
  (E.condition_x_perturbation) [PMC13155148 Saputra 2025, step 8].
- **A change of genes that is a change of states.** Check: C.within_states beside the shares [PMC10562403 Xia 2023,
  step 5; pitfalls.md: PF11].

## Worked chains

**de la Rosa 2025, PMC12880918 (cytokine receptors and macrophage states in neuroinflammation, in vivo).** Two sorted
markers could not describe a continuum, so three scRNA-seq experiments were integrated into monocyte-like cells and
eleven macrophage clusters on a pseudotime [Fig. 3b-d] -> the clusters agreed between the transfer, chimera and
wild-type cells [Ext. Data Fig. 5] -> density differences, knockout minus control: IFNGR1 loss kept cells in earlier
clusters, TNFR1 loss blocked later ones, TGFBR1 and CSF2RA loss pushed cells to later states [Fig. 3e] -> curated
function signatures read per cluster as quantile bands [Fig. 4a, 5a] -> a predicted function measured in the animal,
oxidative bursting by intravital imaging [Fig. 4b-f].

**Cai 2026, PMID41916284 (barriers to cardiac reprogramming in the infarcted heart).** Eleven cell types in the graft,
with induced cardiomyocytes at both time points [step 3] -> the cardiomyocytes at the end of a pseudotime path apart
from the fibrotic fate [step 5] -> each shRNA's enrichment in them against non-targeting cells, Fisher's test with BH:
Calr knockdown the most enriched at both times [step 6] -> the shares of matrix and interferon fibroblasts per
perturbation [step 7] -> five shRNAs re-tested in vivo one at a time, Calr the strongest [step 9].

**Saputra 2025, PMC13155148 (fibroblast subtypes in co-culture with tumour cells).** Subtypes seen in tumours checked in
the culture first [step 1] -> knockout cells told from escapers with Mixscape [step 4] -> subtype shares tested with
scCODA: TGFBR1 loss removed two subtypes and made a mixed cluster [step 5] -> that cluster's own genes and pathways
[step 6] -> the genes within each subtype [step 7] -> the whole screen repeated in monoculture, where the phenotypes
differed [step 8].
