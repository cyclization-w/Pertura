---
id: P2
version: 2
updated: 2026-11-09
name: A score from a reference contrast, then every perturbation ranked on it
use_when: >-
  The question is which perturbations push cells toward or away from a state or response that a contrast defines:
  two conditions the design holds (stimulated and resting, aged and young, co-culture and monoculture), a signature
  the user brings, or a contrast in outside data (patients against healthy donors). Every perturbation is then scored
  on that one axis against the control cells. Needs control guides.
not_when: >-
  No contrast defines the outcome and the question is which programmes the regulators control (P1) or which states
  the cells move between (P3); too few control guides for groups of them to set the score's line.
bring: [optional - a signature or a contrast from outside data, where the design holds none]
requires: [control_guides]
steps:
  - id: P2.1
    does: choose the contrast that defines the score
    part: notebook
    fork: Which contrast defines the score?
    fork_default: the contrast the design holds between conditions of the control cells; a signature the user
      brings where it holds none
  - id: P2.2
    does: build the signature from the contrast, in the control cells, within each cell type the screen holds
    part: tool:scores contrast
    control: the signature built on part of the control cells or guides holds in the rest; its genes stated
  - id: P2.3
    does: score each perturbation's cells on the signature against the control cells of the same condition
    part: tool:scores contrast
    control: the same score on groups of control guides; the score recomputed within one cell-cycle phase where
      the contrast moves the cycle
  - id: P2.4
    does: rank the perturbations, with the line the control-guide groups set
    part: tool:scores contrast
    control: the control-guide groups' scores as the line, with enough cells per perturbation; each guide alone
  - id: P2.5
    does: each perturbation's score in each condition, where the design holds several
    when: the screen holds two or more conditions or cell types
    part: module:E.condition_x_perturbation
    control: each condition's own control cells
  - id: P2.6
    does: the perturbations acting on each other, where the targets are regulators expressed in the cells
    when: the targets are expressed in the screen's cells, so each can be read in the others' cells
    part: tool:genes genes=targets_own
    control: an edge only where the target's change passes the test's false discovery rate; each target's own
      knockdown read first
  - id: P2.7
    does: choose the leads
    part: notebook
    fork: Which of the perturbations past the line go to validation?
    fork_key: true
    control: the lead's guides agree; the score beyond the control-guide groups in the condition the question names
  - id: P2.8
    does: propose the validation
    part: other_experiment
leads: >-
  The perturbations whose score moves beyond the control-guide groups the way the question asks, in the conditions
  where it lies, with their guides agreeing; then the ones an arrayed rerun reproduces.
connects:
  - at: P2.2
    to: P1
    seen_in: [ZEB2 Cancer Cell 2025]
  - at: P2.4
    to: P3
    seen_in: [Aguado-Alvaro 2025]
papers: [Sengstack 2026, Akana 2026, Aguado-Alvaro 2025, Cao 2026, ZEB2 Cancer Cell 2025, lncRNA senescence Nat Aging
  2026, depression genes Nat Genet 2026]
---

> Sources are [paper, step n], the step of that paper's chain in Pertura's sources
> (`docs/build/biology_depth_papers/analysis_chains_*.md`), or [paper, figure] where the chain gives the panels. Each
> step in the header names the module that runs it.

The steps are how published screens worked through this question, step by step: an example to adapt to the screen
and the user's question, not an order to follow. The agent may skip, reorder or add a step and say why; the record
keeps it. Only the design gate blocks anything [ADR 0030, autonomy; ADR 0027].

## Scope

These papers first fix one axis from a contrast the question defines, then place every perturbation on it, so a
screen of many targets reads as one ranking [Sengstack 2026, step 2; Akana 2026, steps 8-9]. The contrast is often in
the screen's own design: late against early passage [Sengstack 2026, step 2], co-culture with T cells against
monoculture in the control cells [Akana 2026, step 8], a fibrotic against a naive state [Aguado-Alvaro 2025, step 2].
It can also come from outside: patient neurons [depression genes 2026, step 9] or a published polarity score [ZEB2
2025, step 7].

## The steps' judgement

**P2.1.** The contrast is the biological question itself, so it is chosen before any perturbation is read
[paradigms_of_deep_perturbseq_analysis.md, P2]. Where the design holds two conditions, the control cells' difference
between them is the natural contrast, because it measures the response in this screen's cells without any
perturbation [Akana 2026, step 8].

**P2.2.** Akana built its contrast from the non-targeting cells alone, by edgeR on pseudobulk profiles with the
sequencing batch as a covariate, at |logFC| > 0.25 and BH FDR < 0.01 [Akana 2026, step 8]. Sengstack took the late
against early passage log fold changes [Sengstack 2026, step 2]. A signature built and scored on the
same cells looks stronger than it is; Akana left one guide out, built the signature from the others and scored it in
the cells of the guide left out [Akana 2026, step 9].

**P2.3.** Sengstack scored each perturbation by the correlation of its log fold changes against non-targeting cells
with the ageing contrast [Sengstack 2026, step 2]. ZEB2's anti-tumour score was the mean enrichment of the IFN
response, antigen presentation and complement programmes minus the immune-suppression and phagocytosis ones [ZEB2
2025, step 7]. C.programs scores the user's or the agent's gene lists per target against the
control cells, and runs the same score on groups of control guides as its calibration [C.programs returns]. Where the
contrast moves the cell cycle, the score is recomputed within one phase: Sengstack's reversal persisted within a
fixed phase, although most hits shifted cells out of G1 [Sengstack 2026, step 3].

**P2.4.** A ranking needs a line, or every perturbation has a rank. Sengstack drew it at r <= -0.3 [Sengstack 2026,
step 2]. The control-guide groups put through the same score give the screen's own line [C.programs returns;
pitfalls.md: Samples, replicates and cells].

**P2.5.** A perturbation's place on the axis can differ by condition. Akana tested each monoculture signature in
co-culture and the reverse; IFNB1 acted only in co-culture [Akana 2026, step 9]. Cao read each target's effect in
each patient sample, and a circuit was absent where its genes' baseline expression was absent [Cao 2026, step 6].
E.condition_x_perturbation reads each target against each condition's own control cells [E.condition_x_perturbation
returns].

**P2.6.** When the targets are regulators expressed in the cells, each target's changed genes include the other
targets, and the screen holds a network among them. Akana kept the regulators whose changes were consistent with their
phenotype and found a hub raised by two resistance hits and lowered by six sensitizing hits [Akana 2026, step 12].
The lncRNA screen placed HOTAIRM1 as a central hub of its perturbation network [lncRNA senescence 2026, steps 8 and
10]. A regulator whose own knockdown failed is no node: B.efficacy first [pitfalls.md: Knockdown of the target gene].

## Forks

**P2.1 fork: Which contrast defines the score?** Papers took:
- a contrast in the design's control cells: co-culture against monoculture [Akana 2026, step 8];
- a contrast of states the screen's cells pass between: late against early passage [Sengstack 2026, step 2], fibrotic
  against naive [Aguado-Alvaro 2025, step 2];
- programmes from a patient atlas combined into one score [ZEB2 2025, steps 1 and 7];
- patients against controls, as GO profiles [depression genes 2026, step 9].

**P2.7 fork: Which of the perturbations past the line go to validation?** Papers took:
- the top score whose arrayed rerun reversed the most hallmarks [Sengstack 2026, step 5];
- the top score that also agreed with a published score and changed chromatin most [ZEB2 2025, steps 7 and 10];
- the one whose effect ran against the others' direction [lncRNA senescence 2026, step 10];
- the cluster most similar to patients, then its top gene [depression genes 2026, step 9].

## Choosing the leads

A strong lead's score goes beyond the control-guide groups the way the question asks, its guides agree, and it holds
in the condition the question names [P2.7 control; F.guides_agree]. Papers then took the ones an arrayed rerun
reproduced: four TFs re-tested one at a time, EZH2 the strongest [Sengstack 2026, step 5].

## Validation

- an arrayed perturbation with the phenotype the score predicts: proliferation, proteasome and mitochondria
  [Sengstack 2026, step 5]; T cell co-culture and antigen presentation [ZEB2 2025, step 8];
- the lead in an animal: AAV8-EZH2 in aged mice [Sengstack 2026, step 6]; a targeted siRNA in tumours [ZEB2 2025,
  step 12].


## Connections

- P1 enters at P2.2 when the score is built from programmes: ZEB2's anti-tumour score from its TAM modules [ZEB2 2025,
  steps 6-7].
- P3 enters after P2.4 when the ranked perturbations are read on states: Aguado-Alvaro's top fibrotic-score hits went
  to Perturb-seq and their shares of states [Aguado-Alvaro 2025, step 3].

## Pitfalls, each with its check

- **A signature tested on the cells it was built from looks stronger than it is.** Check: build it on part of the
  guides or cells and score the rest [Akana 2026, step 9].
- **A score that follows the cell cycle.** Check: the score within one cell-cycle phase (C.cell_cycle) [Sengstack 2026,
  step 3].
- **A ranking with no line.** Check: the control-guide groups through the same score [C.programs returns].
- **One condition read for all.** Check: the score in each condition against that condition's own control cells
  [Akana 2026, step 9; Cao 2026, step 6].
- **A regulator read as no regulator because its knockdown failed.** Check: B.efficacy before the network [pitfalls.md:
  Knockdown of the target gene].
- **Cells as the observations call too much.** Check: what the same test calls in groups of control guides
  (`pt.calibrate`) [pitfalls.md: Samples, replicates and cells].

## Worked chains

**Sengstack 2026, PNAS (200 TFs by CRISPRa and CRISPRi in late-passage fibroblasts).** Candidates ranked by how their
targets differ between early and late passage [step 1] -> each perturbation correlated with the ageing contrast, hits
at r <= -0.3 [step 2] -> the reversal held within one cell-cycle phase [step 3] -> CRISPRa and CRISPRi rarely mirror
images [step 4] -> four hits rerun arrayed; EZH2 reversed most hallmarks [step 5] -> AAV8-EZH2 in aged mice reduced
steatosis and fibrosis [step 6].

**Akana 2026, Nature Genetics (CRISPRa Perturb-seq of 61 genes in melanoma cells, with T cells or alone).** The
control cells' response to T cells as the contrast, by edgeR on pseudobulk [step 8] -> each target's signature with a
leave-one-guide-out test, and the signatures tested across the two conditions [step 9] -> similar signatures clustered
[step 10] -> the regulators' effects on each other, kept where consistent with their phenotype, a hub at GAS7 [step
12] -> the hits in xenografts [step 14].

**ZEB2 2025, Cancer Cell (122 knockouts in IL-4 conditioned macrophages).** Programmes from a patient atlas [step 1]
-> knockouts grouped by their effects [step 5] -> an anti-tumour score from the programmes, ranked over all knockouts
and checked against a published polarity score [step 7] -> the top knockout's function in co-culture [step 8] -> its
targeting in tumours [step 12].
