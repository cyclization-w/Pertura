---
id: P1
version: 6
updated: 2026-11-09
name: Programs first, then their regulators
use_when: >-
  The question is which perturbations control the cell type's coordinated gene programs (its identity, a function,
  a disease-relevant process), and which regulators act together. The screen perturbs regulators (transcription
  factors, chromatin regulators, risk genes) whose effects spread over many genes. Needs control guides.
not_when: >-
  A contrast or a signature already defines the outcome (P2 first); the question is which cell states the cells move
  between (P3); the perturbations act on one or a few nearby genes (enhancers, variants: P5); too few control guides
  for groups of them (no calibration).
bring: [optional - an atlas's or patients' programs for P1.1, a GWAS or burden gene list for P1.4]
requires: [control_guides]
steps:
  - id: P1.1
    does: find the gene programs
    part: tool:programs
    control: programs found in the control cells only, or fixed gene sets; components that follow a lane or sample
      set aside; K chosen by stability before any regulator is read
    fork: the source of the programs and how many to keep
    fork_default: programs found by cNMF in all the screen's cells, K the most stable of 5 to 15 (the programs
      tool's default); known gene sets where the question names them
  - id: P1.2
    does: say what each program is
    part: missing:a program's top genes tested against gene sets (pt.enrich reads a gene result only)
    control: known pathway members found inside; the program's genes expressed more in this cell type than others
  - id: P1.3
    does: each perturbation's effect on each program
    part: tool:programs
    control: the same test on groups of control guides; the other targets' cells as the reference for a shift that
      every target shares; each guide alone
  - id: P1.4
    does: choose the programs that matter for the question
    part: notebook
    fork: the programs that matter here
    fork_key: true
  - id: P1.5
    does: sets of regulators that act together
    part: module:E.co_functional_sets
    control: each set's stability over resampled features; known complexes or families falling together
  - id: P1.6
    does: the axis, two programs' effects correlated over the regulators
    when: the question is a balance of two programs, or two programs' effects anticorrelate over the regulators
    part: module:E.program_axis
    control: the control-guide groups on the same two programs
  - id: P1.7
    does: rank the regulators on the programs or axis that matter
    part: notebook
    control: the control-guide groups' 95th percentile of the same axis (pt.rank_on with null)
  - id: P1.8
    does: choose the leads
    part: notebook
    fork: the leads that go to validation
    fork_key: true
    control: the lead's guides agree; the design's other test; the calibration
  - id: P1.9
    does: propose the validation
    part: other_experiment
leads: >-
  The regulators of the chosen programs or axis that go beyond the control-guide groups, whose guides agree, the
  ones no prior study named first; then those an arrayed rerun reproduces.
connects:
  - at: P1.4
    to: P5
    seen_in: [Schnitzler 2024, Ota 2025, HSPC Cell Stem Cell 2026, depression genes Nat Genet 2026]
  - at: P1.7
    to: P2
    seen_in: [ZEB2 Cancer Cell 2025, depression genes Nat Genet 2026]
papers: [Schnitzler 2024, Ota 2025, Aryal 2026, ZEB2 Cancer Cell 2025, fibroblast TF states Nat Genet 2025,
  lncRNA senescence Nat Aging 2026, depression genes Nat Genet 2026, HSPC Cell Stem Cell 2026,
  astrocyte TFs Science 2026]
---

> Sources are [paper, figure] where the paper's analysis chain gives the panels, else [paper, step n], the step of
> that paper's chain in Pertura's sources. Each step in the header above names the module or block that runs it.
> `missing:` marks a step Pertura has no part for yet; it is done in the notebook.

The steps are how published screens worked through this question, step by step: an example to adapt to the screen
and the user's question, not an order to follow. The agent may skip, reorder or add a step and say why; the record
keeps it. Only the design gate blocks anything [ADR 0030, autonomy; ADR 0027].

## Scope

A screen of regulators changes thousands of genes at once, and a gene-by-gene result is hard to read. These papers
first find the programs the cells coordinate, then test which perturbations move each program [Ota 2025, step 6;
fibroblast TF states 2025, step 7]. The structure among the regulators then names the lead: an axis of two programs,
or sets of regulators acting together [Schnitzler 2024, Fig. 3a,b; astrocyte TFs 2026, step 7].

Another paradigm may fit better when a contrast already defines the outcome, or when the question is about cell states
[paradigms_of_deep_perturbseq_analysis.md: P2, P3].

## The steps' judgement

**P1.1.** The programs are a choice of the analysis, not a fact of the data. The same screen gives different programs
with different methods and K. Schnitzler ran cNMF for K from 3 to 100 and chose K = 60 by stability and
reconstruction error. It then set aside the 10 components that correlated with the 10x lane (r > 0.15) [Schnitzler
2024, Extended Data Fig. 2c-g]. Programs found on the cells they are tested on look stronger than they are. Finding
them in the control cells only avoids that [C.programs fails_when; Schnitzler 2024, Supplementary Notes 2-3, the
control-cell ablation].

**P1.2.** The papers named each program before it carried a finding. Schnitzler named its programs from their
regulators, motif enrichment, GO enrichment and whether their genes are higher in endothelial cells than elsewhere
[Schnitzler 2024, Fig. 1c,d]. Aryal named the myeloid program P-15 by its GO enrichment [Aryal 2026, step 3].

**P1.3.** A regulator of a program is a perturbation whose cells change that program's usage against the control
cells. Schnitzler tested it with MAST against control-guide cells, with the lane as a covariate. It checked the false
discovery rate by resampling control cells as targets, which gave no regulators [Schnitzler 2024, Fig. 1c, Extended
Data Fig. 3a-c]. C.programs runs the same test on groups of control guides as its calibration, and counts what it
calls there [C.programs returns].

**P1.5.** Grouping the regulators by their effects is the common way to find structure. Published screens correlate
and cluster the perturbations' profiles, and some cluster regulators into co-functional sets [coverage map, rows 1
and 28]. In the astrocyte screen, TF families clustered together, which served as the check on the five
co-functional modules [astrocyte TFs 2026, step 7]. In the HSPC screen, regulators in the same perturbation cluster
were more likely to interact physically (STRING) [HSPC 2026, step 6].

**P1.6.** An axis is two programs that the regulators move in opposite directions. Few published screens make this move
[coverage map, row 47]. It is worth taking when the question is a balance between two programs, or when two programs'
effects anticorrelate over the regulators. Upstream and downstream members of the CCM pathway moved the five CAD
programs in opposite directions, so the screen read as one signalling axis [Schnitzler 2024, Fig. 3a,b]. The S phase and
G2/M programs shared regulators with opposite signs [Ota 2025, step 8].

**P1.7.** A ranking without a threshold from the control guides gives every target a rank. Schnitzler ran control cells,
resampled as if they were targets, through the same procedure that called regulators, and it called none [Schnitzler
2024, analysis_chains_of_sixteen_more_papers.md, the controls column]. `pt.rank_on` reads each target against the
control-guide groups' 95th percentile on the same axis [pt.rank_on].

## Forks

**P1.1 fork: the source of the programs and how many to keep.** Papers found them:
- in all the screen's cells or its perturbation effects: cNMF on the cells [Schnitzler 2024, Extended Data Fig. 2;
  Ota 2025, step 6; astrocyte TFs 2026, step 7], a regularised linear model with 17 programs [Aryal 2026, step 3],
  sparse PCA of the perturbation-induced changes [fibroblast TF states 2025, step 7];
- in the non-targeting cells first [lncRNA senescence 2026, step 8];
- in a patient atlas, then scored in the screen [ZEB2 2025, steps 1-2].

Published screens score known gene sets or signatures per perturbation about as often as they find programs in the
data [coverage map, rows 3 and 8]. C.programs offers `gene_sets` hallmark or given, cnmf, genenmf, genenmf_meta
(programs recurring across samples) or sklearn_nmf, and `fit_on` all or controls. For cNMF, K is the most stable of 5
to 15 unless given (Pertura's choice) [C.programs choices].

**P1.4 fork: the programs that matter here.** Papers chose them:
- by genetic association: CAD variants enriched in five programs by a Fisher test [Schnitzler 2024, Fig. 2b,c];
  loss-of-function burden for a trait [Ota 2025, step 7];
- by patients: only one of 17 programs held for survival in two AML cohorts [Aryal 2026, step 5]; modules specific to
  tumour-associated against healthy macrophages [ZEB2 2025, step 1];
- by disease signatures: GO profiles compared with patient neurons [depression genes 2026, step 9].

The user's question, recorded at the start, decides among these [ADR 0030, the flow].

**P1.8 fork: the leads that go to validation.** Papers took:
- the strongest regulators of the relevant programs: TLNRD1 and CCM2 [Schnitzler 2024, Fig. 3, 4];
- the gene acting on all key programs in one direction with a large loss-of-function effect: SUPT5H [Ota 2025,
  step 10];
- regulators converging on one program: the MEN1-DOT1L-KAT6A hub [Aryal 2026, step 3];
- the one that also passed a rerun under the disease condition: Ferd3l [astrocyte TFs 2026, step 10].

## Choosing the leads

A strong lead goes beyond the control-guide groups on the programs that matter, its guides agree, and it holds under the
design's other test [pitfalls.md: Rival explanations; P1.8 control]. Among such regulators, one that no earlier study
named is the stronger result. TLNRD1 was one of 17 genes, out of 41, that no prior study had nominated [Schnitzler 2024,
Supplementary Notes 2-3].

## Validation

Papers re-tested the leads one at a time:
- an arrayed knockdown with bulk RNA-seq [Schnitzler 2024, Fig. 3c,d; Aryal 2026, step 1];
- the phenotype the programs predict: ACTA2 and collagen for myofibroblast regulators [fibroblast TF states 2025,
  step 10], drug co-inhibition for the hub [Aryal 2026, step 7];
- in vivo: xenografts for HSPC regulators [HSPC 2026, step 8], a disease mouse model [astrocyte TFs 2026, step 12].


## Connections

- P5 enters at P1.4 when genetics chooses the programs [Schnitzler 2024, Fig. 2; Ota 2025, step 7; HSPC 2026,
  step 13; depression genes 2026, step 1].
- P2 enters at P1.7 when a score built from the programs ranks the regulators: an anti-tumour score from the TAM
  modules [ZEB2 2025, step 7].

## Pitfalls, each with its check

- **A program found on the data it is tested on looks stronger than it is.** Check: `fit_on="controls"`, or fixed
  gene sets, and the calibration on control-guide groups [C.programs fails_when; Schnitzler 2024, the control-cell
  ablation].
- **A program of one lane or sample read as biology.** Check: components that follow a lane set aside, or
  genenmf_meta's programs recurring across samples [Schnitzler 2024, Extended Data Fig. 2; C.programs fails_when].
- **A shift that every target shares inflates every program effect.** Check: the same program against the other
  targets' cells (`reference="other_targets"`; D.common_response) [C.programs fails_when].
- **Cells as the observations call too much.** Check: what the same test calls in groups of control guides
  (`pt.calibrate`) [pitfalls.md: Samples, replicates and cells; Schnitzler 2024, Extended Data Fig. 3].
- **A regulator's absence may be a guide that did not work.** Check: B.efficacy before reading a target as no
  regulator, and each guide alone (`res.by_guide()`) [pitfalls.md: Knockdown of the target gene].
- **A program change can be a change of which states the cells are in.** Check: `pt.composition` of the states, then
  the program within one state [pitfalls.md: Rival explanations, composition].

## Worked chains

**Aryal 2026, Leukemia (KMT2A-rearranged AML, 16 regulators, Cas9 knockout).** Knockdown checked, and each signature
agreed with bulk RNA-seq of the same knockouts [step 1] -> Menin-MLL and KAT7 formed discrete states, KAT6A correlated
with MEN1 and DOT1L [step 2] -> 17 programs by a regularised linear model; MEN1, KAT6A and DOT1L all repress the
myeloid program P-15 [step 3] -> P-15 high in monocytes and GMPs in a bone marrow atlas [step 4] -> of 17 programs,
only P-15 predicted survival in both TCGA and Beat AML [step 5] -> a MEN1 x KAT6A synergy index of 86 out of 100
[step 6] -> Menin and KAT6A inhibitors synergised (ZIP 20.19) [step 7].

**Astrocyte TFs 2026, Science (about 1,000 TFs overexpressed in vivo).** Barcodes checked against their ORFs by long
reads [step 1] -> most overexpressions changed many genes, similar across regions [step 5] -> cNMF programs, then TFs
clustered by their program effects into five co-functional modules, TF families together [step 7] -> hdWGCNA as a
second view of the same structure [step 8] -> TF signatures compared with patient and aged-donor snRNA-seq, top 10
per condition [step 9] -> 39 candidates rerun under LPS; only Ferd3l suppressed inflammation and neurotoxic markers
[step 10] -> Ferd3l overexpression improved memory in 5XFAD mice [step 12].

**HSPC 2026, Cell Stem Cell (335 chromatin regulators in primary cord blood HSPCs).** Guides kept when those of one
gene agreed beyond background pairs and changed at least 20 genes [step 2] -> known regulators reproduced their
published abundance shifts [step 4] -> a network of 322 knockouts and 2,519 genes: nine perturbation clusters and
twelve programs [step 5] -> clusters checked against STRING interactions [step 6] -> knockouts ranked by HSC
enrichment, 90% of the top 20 in one cluster; SMYD3 nominated [step 7] -> SMYD3 knockout engrafted better in
xenografts [step 8].
