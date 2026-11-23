---
name: pertura-knowledge
description: Methods and follow-up examples for analysing a Perturb-seq screen with Pertura. Published results are prior knowledge, never evidence for this screen. Queries withhold the workspace's excluded papers and their study versions. Read at a choice of analysis, next comparison or figure form.
---

# Pertura's knowledge base

What published single-cell CRISPR screens and methods papers did and checked. It is kept apart from the evidence in
your data. The agent and the user read the same entries. A module's result names the entries for each way it can
mislead, and the user's page links to them.

- **Source boundary:** all three paper queries apply the workspace's `literature_excluded` before counting studies
  or choosing examples. A matching PMID, DOI or title phrase withholds every version sharing that study's `work`.
  A paper without a title is withheld when title filtering is required. Published observations illustrate methods
  and follow-ups; a finding about the current screen must rest on this screen's own data and recorded runs.
- **Paper examples:** `read_knowledge` takes `query` and `kind`:
  - `analysis`, with an analysis kind such as `genes`: methods, comparisons and reported checks;
  - `proceed`, with an analysis or evidence kind such as `similarity`: the observation, the next question and the
    comparison the paper used to answer it;
  - `figure`, with an evidence or analysis kind such as `similar targets` or `genes`: charts, colours and the targets
    shown. Consult this before drawing when choosing how to present evidence.
  Each returns counts and up to three examples with the paper, panels, original quote and method location. Counts
  describe this library; the current screen's modality and readout come first among examples. Each analysis kind's
  first call also gives `analysis` (once per project); module lookups give it too. Detected module results give
  `proceed` where the library has a stated link. Result proposals and the reviewer's `read_record` figure replies
  give `figure`, matched to the declared findings and source analyses, with at most three examples in total.
  A release places the reviewed `knowledge.db` in this folder; `PERTURA_KNOWLEDGE` can point to another folder
  holding the cards and store.
- **Start from a question:** `questions.md` holds the questions an analysis meets, by the step of the work. Each has
  a short answer, its sources, and the entries it rests on. The short answers are drafts for a person's review.
  Where a short answer and an entry differ, the entry holds. `read_knowledge` with nothing asked lists the
  questions. With a question's id (units, calibration), it gives that question.
- **Read an entry** with `read_knowledge` (by its id, by a module's name, or by words), or open its file in this
  folder.
- **Every entry has an id** (a question's word, R3, PF9, T3, P6.4, or a module's name), listed in the index below.
- **Entries cite papers and what they did.** Where a rule is Pertura's own (a threshold, a default, a stopping
  rule), the entry's Source line names it as "Pertura's choice".
- **Advice, not rules:**
  - an entry fits only when what it describes is what you see in this screen;
  - a paper's thresholds were chosen for its data. Do not copy them as Pertura's;
  - the numbers in the entries are Pertura's defaults. The user or you can change them (`policy` in a module's
    run, `pt.policy(...)` in the notebook, or a module's own choice, such as B.efficacy's `threshold`). A target
    or method you name runs wherever it can be computed, and its result notes how it differs from the default;
  - paper examples describe published results. A comparison they suggest is tested in this screen's data; record
    biological knowledge you use, said with the claim it informs;
  - a choice you make from an entry goes into the record as usual (the doubt it answers, the runs that test it, what
    they showed, your reading), naming the entry.

## The contents

| Kind | Ids | Where |
|---|---|---|
| The questions an analysis meets, by step, each with a short answer and the entries it rests on | short words (units, guide-calls) | `questions.md` |
| Cards on analysis choices, from methods papers | U, R, G, S, PG, ES, V | `choices/` |
| How judgements become analysis choices | HJ | `how_judgements_become_choices.md` |
| Pitfalls | PF | `pitfalls.md` |
| The follow-up moves published screens made | T, O, L, C, X | `follow_up_moves.md` |
| Paradigm cards: the routes deep analyses take, step by step | P1, P3, P6 and their steps | `paradigms/` |
| The figures published screens draw | FG | `figure_patterns.md` |
| The modules: the biology each tests, the ways its result can mislead, the next steps | a module's name | `modules/` |
| The moments in the tools that carry knowledge | M | `moments.md` |

**The cards on analysis choices** each come from a methods paper, or from a screen outside Pertura's development
and evaluation screens. Each paper was read for what the authors saw, what they chose because of it, what the choice
acted on, how they read the result, and when they stopped. Read the card for the choice in front of you:

| The choice | File |
|---|---|
| Which unit to test by; replicates; batches and covariates | `choices/units_and_covariates.md` |
| Which reference; control guides; what counts as a null | `choices/references_and_nulls.md` |
| Guide calling, guide efficacy, combining guides | `choices/guides.md` |
| Weak, continuous or mixed responses; responders | `choices/responses.md` |
| Programs, factors, gene sets and enrichment | `choices/programs_and_gene_sets.md` |
| Effect sizes, counting hits, transcriptome-wide impact | `choices/effect_sizes.md` |
| Validation, changing the experiment, and when to stop | `choices/validation_and_stopping.md` |

Each card has the same parts:
- **If you see:** the trigger, in what the screen shows;
- **Consider:** the choice;
- **It acts on:** the part of the analysis it changes (unit, reference, assignment, estimator, features, program,
  interpretation, validation). A choice that changes the population, comparison or outcome is a new question. Open
  it as one;
- **Read it as:** how to judge the result. Never judge it by which choice gives more hits;
- **Stop or turn when:** signals that the thread may have said what it can, or may turn, in the record's terms
  (how a thread ends). They are signals to weigh, not commands. Unless it says what the source did, this part is
  Pertura's choice;
- **Source:** the paper (title, journal, year, DOI), and Pertura's choice where the card makes one.

## The index

<!-- index: made from the files by `python -m pertura.knowledge write-index`; do not edit by hand -->

| Id | Entry | File |
|---|---|---|
| question | The question of an analysis | `questions.md` |
| units | The unit an effect is tested over | `questions.md` |
| batches | Batches and covariates | `questions.md` |
| controls | The reference a target is compared with | `questions.md` |
| guide-calls | The rule that calls each cell's guides | `questions.md` |
| vectors | Guides that travel together on one vector | `questions.md` |
| efficacy | Whether each guide changes its own target | `questions.md` |
| escape | Cells that carry a guide but did not respond (non-perturbed cells) | `questions.md` |
| guides-agree | Whether a target's guides agree | `questions.md` |
| dose | Guides of one target that differ in efficacy | `questions.md` |
| cell-numbers | Enough cells for a target | `questions.md` |
| abundance | A target's cell numbers as fitness | `questions.md` |
| clones | A guide's cells that are a few clones | `questions.md` |
| nearby-genes | The genes beside a target or an element | `questions.md` |
| any-effect | Whether a target changes the overall expression profile | `questions.md` |
| genes | The genes a target changes | `questions.md` |
| calibration | Whether a test is calibrated on this screen | `questions.md` |
| multiple-testing | The scope of multiple testing | `questions.md` |
| shared-response | A response every perturbation shares | `questions.md` |
| normalisation | What the expression measures, and what normalisation keeps | `questions.md` |
| depth | An effect that follows read depth or cell state | `questions.md` |
| effect-size | How large an effect is, beside how many genes pass | `questions.md` |
| states | Cell states, and the share of them a target's cells fill | `questions.md` |
| within-states | A change within a state, beside a shift between states | `questions.md` |
| cell-cycle | The cell cycle, and whether to adjust for it | `questions.md` |
| trajectories | A shift along a trajectory or a pseudotime | `questions.md` |
| programs | The gene programs a target moves | `questions.md` |
| gene-sets | What a list of changed genes means | `questions.md` |
| regulators | The regulators of a program, and the axes between programs | `questions.md` |
| similar-targets | Targets whose profiles look alike | `questions.md` |
| pairs | A pair of perturbations against each alone | `questions.md` |
| two-directions | Activation and inhibition of one gene | `questions.md` |
| conditions | An effect that differs between conditions or cell types | `questions.md` |
| findings | What a finding says, and what it rests on | `questions.md` |
| target-evidence | How to read one target's evidence | `questions.md` |
| nothing-detected | What a result that detects nothing says | `questions.md` |
| choices | A choice that could change a reading | `questions.md` |
| own-methods | A method of one's own | `questions.md` |
| direct-targets | A factor's known targets | `questions.md` |
| outside-data | Outside knowledge and outside data | `questions.md` |
| figures | The figure for each kind of evidence | `questions.md` |
| validation | The experiment that would settle a finding | `questions.md` |
| stopping | When a thread has said what it can | `questions.md` |
| U1 | Replicates behind the cells | `choices/units_and_covariates.md` |
| U2 | Cells nested in samples, samples in studies | `choices/units_and_covariates.md` |
| U3 | A batch term, and why it is there | `choices/units_and_covariates.md` |
| U4 | Covariates computed from expression | `choices/units_and_covariates.md` |
| U5 | In vivo, many cells from few clones | `choices/units_and_covariates.md` |
| U6 | Nuisance variation that control cells share | `choices/units_and_covariates.md` |
| U7 | Normalisation to each cell's total | `choices/units_and_covariates.md` |
| R1 | Calibrate before discovering | `choices/references_and_nulls.md` |
| R2 | A null built from control guides | `choices/references_and_nulls.md` |
| R3 | The shift every perturbation shares | `choices/references_and_nulls.md` |
| R4 | Checking your own method on planted effects | `choices/references_and_nulls.md` |
| R5 | Explicit controls, not the rest of the panel | `choices/references_and_nulls.md` |
| G1 | Calling rules trade purity against coverage | `choices/guides.md` |
| G2 | Calls that do not match the design | `choices/guides.md` |
| G3 | A knockout whose target gene does not fall | `choices/guides.md` |
| G4 | Guides of one target that disagree | `choices/guides.md` |
| G5 | Guides of different targets that look alike | `choices/guides.md` |
| S1 | A graded response | `choices/responses.md` |
| S2 | Selecting cells by their response | `choices/responses.md` |
| S3 | Cells with a guide but no response | `choices/responses.md` |
| PG1 | Genes that move together | `choices/programs_and_gene_sets.md` |
| PG2 | A score that uses the genes it is tested on | `choices/programs_and_gene_sets.md` |
| PG3 | Overlapping gene sets | `choices/programs_and_gene_sets.md` |
| PG4 | Enrichment | `choices/programs_and_gene_sets.md` |
| PG5 | Clusters and their markers | `choices/programs_and_gene_sets.md` |
| PG6 | A program worth interpreting | `choices/programs_and_gene_sets.md` |
| ES1 | Hit counts that track cells and depth | `choices/effect_sizes.md` |
| ES2 | Few true effects among many genes | `choices/effect_sizes.md` |
| ES3 | Sparse genes and the tests that need expression | `choices/effect_sizes.md` |
| ES4 | Discoveries counted over selected targets | `choices/effect_sizes.md` |
| ES5 | A result that detects nothing | `choices/effect_sizes.md` |
| V1 | Trajectories and pseudotime | `choices/validation_and_stopping.md` |
| V2 | A candidate nominated by correlation | `choices/validation_and_stopping.md` |
| V3 | A clear separation in a supervised plot | `choices/validation_and_stopping.md` |
| V4 | Combining replicates' tests | `choices/validation_and_stopping.md` |
| V5 | A thread that has said what it can | `choices/validation_and_stopping.md` |
| HJ1 | Choices worth trying, with their reasons | `how_judgements_become_choices.md` |
| HJ2 | Before the method, the question | `how_judgements_become_choices.md` |
| HJ3 | What a published choice changes | `how_judgements_become_choices.md` |
| HJ4 | The point to stop | `how_judgements_become_choices.md` |
| PF1 | Say what the n is | `pitfalls.md` |
| PF2 | Cells as the observations can give excess false positives | `pitfalls.md` |
| PF3 | In vivo, a guide's cells can be few clones | `pitfalls.md` |
| PF4 | In T cells, clones carry their receptor genes | `pitfalls.md` |
| PF5 | Many guides do not knock down their target | `pitfalls.md` |
| PF6 | The target gene moves because of the guide | `pitfalls.md` |
| PF7 | A knockout's mRNA often stays | `pitfalls.md` |
| PF8 | Guides of one target can differ in strength | `pitfalls.md` |
| PF9 | A response common to the perturbations | `pitfalls.md` |
| PF10 | Control-guide cells and all other perturbed cells answer different questions | `pitfalls.md` |
| PF11 | A shift in the states a target's cells fill is itself an effect | `pitfalls.md` |
| PF12 | Technical causes of a composition shift come first | `pitfalls.md` |
| PF13 | A state held by one perturbation may be doublets | `pitfalls.md` |
| PF14 | Targets and control cells from different samples | `pitfalls.md` |
| PF15 | Large fold changes of genes the controls barely express | `pitfalls.md` |
| PF16 | Few cells give noisy effects; many cells give tiny significant ones | `pitfalls.md` |
| PF17 | Filtering cells by their response changes the question | `pitfalls.md` |
| PF18 | Do not pick the guide with the largest effect, or combine rules until something passes | `pitfalls.md` |
| PF19 | The gene universe of an enrichment is the genes the assay detected | `pitfalls.md` |
| T1 | Guides agree | `follow_up_moves.md` |
| T2 | The test is calibrated | `follow_up_moves.md` |
| T3 | Each perturbation acts | `follow_up_moves.md` |
| T4 | Each member of a pair acts | `follow_up_moves.md` |
| T5 | Neighbours are spared | `follow_up_moves.md` |
| T6 | Enough cells | `follow_up_moves.md` |
| T7 | Not state, depth or coverage | `follow_up_moves.md` |
| O1 | Programs | `follow_up_moves.md` |
| O2 | Regulators of a program | `follow_up_moves.md` |
| O3 | An axis across regulators | `follow_up_moves.md` |
| O4 | Similar perturbations | `follow_up_moves.md` |
| O5 | The reason two look alike | `follow_up_moves.md` |
| O6 | Co-functional sets | `follow_up_moves.md` |
| L1 | Between or within states | `follow_up_moves.md` |
| L2 | A state's identity | `follow_up_moves.md` |
| L3 | Along a trajectory | `follow_up_moves.md` |
| L4 | Cell types | `follow_up_moves.md` |
| L5 | Uneven response | `follow_up_moves.md` |
| C1 | Condition by perturbation | `follow_up_moves.md` |
| C2 | Pairs | `follow_up_moves.md` |
| C3 | Two directions | `follow_up_moves.md` |
| C4 | Dose | `follow_up_moves.md` |
| C5 | Enhancer to gene | `follow_up_moves.md` |
| X1 | Pathways | `follow_up_moves.md` |
| X2 | Direct targets | `follow_up_moves.md` |
| X3 | Reference states | `follow_up_moves.md` |
| X4 | Genetics, patients, other screens | `follow_up_moves.md` |
| M1 | A finding that rests on a method of one's own | `moments.md` |
| M2 | Every finding | `moments.md` |
| M3 | A cell set | `moments.md` |
| M4 | Alternatives tried at a choice | `moments.md` |
| M5 | A thread ended | `moments.md` |
| M6 | A proposal for validation | `moments.md` |
| M7 | A figure for the report | `moments.md` |
| FG1 | The chart for each kind of evidence | `figure_patterns.md` |
| P1 | Programs first, then their regulators | `paradigms/P1_programs_then_regulators.md` |
| P2 | A score from a reference contrast, then every perturbation ranked on it | `paradigms/P2_a_score_from_a_contrast_then_every_perturbation_ranked.md` |
| P3 | Cell states first, then which perturbations shift them | `paradigms/P3_states_then_their_shifts.md` |
| P6 | Each combination against its singles, then why it interacts | `paradigms/P6_combinations_and_their_interactions.md` |
| A.screen_overview | The screen at a glance (run by code, in the first look) | `modules/A.screen_overview.md` |
| A.what_was_done | The cells the experiment holds, by unit and kind (run by code, in the first look) | `modules/A.what_was_done.md` |
| B.abundance | Each target's cells against the control guides' (run by `guides` view `cells`) | `modules/B.abundance.md` |
| B.clonality | Expanded clones among a guide's cells (run by `guides` view `clones`) | `modules/B.clonality.md` |
| B.dose | How much a target's guides differ in efficacy (run by `guides` view `dose`) | `modules/B.dose.md` |
| B.efficacy | Each guide's change of its own target gene (run by `guides` view `own_gene`) | `modules/B.efficacy.md` |
| B.escape | Perturbed and non-perturbed cells within a target's cells (run by `guides` view `escape`) | `modules/B.escape.md` |
| B.guide_calls_and_their_evidence | The evidence behind each cell's guide call (run by `guides` view `calls`) | `modules/B.guide_calls_and_their_evidence.md` |
| B.vector_structure | Guides that travel on one vector (run by `guides` view `vectors`) | `modules/B.vector_structure.md` |
| C.any_effect | Each target's effect on the whole profile (run by `impact` view `distance`; `reproducibility` view `fewer_cells`) | `modules/C.any_effect.md` |
| C.cell_cycle | Each target's cell-cycle phases (run by `states` view `cell_cycle`) | `modules/C.cell_cycle.md` |
| C.genes | The genes each target changes (run by `genes`) | `modules/C.genes.md` |
| C.programs | The gene programs each target moves (run by `scores`; `programs`) | `modules/C.programs.md` |
| C.states_and_composition | Cell states, and each target's share of them (run by `states`) | `modules/C.states_and_composition.md` |
| C.within_states | Changes within a state, beside shifts between states (run by `states` view `within`) | `modules/C.within_states.md` |
| D.common_response | A target's effect beyond the response all perturbations share (run by `impact` view `shared`) | `modules/D.common_response.md` |
| D.cutting_response | The response to cutting DNA (run by `guides` view `cutting`) | `modules/D.cutting_response.md` |
| E.co_functional_sets | Regulators of one program that act together (run by `similarity` view `sets`) | `modules/E.co_functional_sets.md` |
| E.combinations | Each pair against its singles (run by `combinations` view `pairs`) | `modules/E.combinations.md` |
| E.condition_x_perturbation | Effects that differ between conditions (run by `conditions`) | `modules/E.condition_x_perturbation.md` |
| E.opposite_modes | Activation against inhibition of one gene (run by `conditions` view `modes`) | `modules/E.opposite_modes.md` |
| E.program_axis | Two programs the regulators move together or apart (run by `similarity` view `axis`) | `modules/E.program_axis.md` |
| E.similar_targets | Targets with similar profiles (run by `similarity` view `alike`) | `modules/E.similar_targets.md` |
| F.guides_agree | Agreement between a target's guides (run by `reproducibility` view `guides`) | `modules/F.guides_agree.md` |
| F.replicates_agree | Agreement between samples (run by `reproducibility` view `replicates`) | `modules/F.replicates_agree.md` |
| G.direct_targets | A factor's known targets, as its perturbation moves them (run by `enrichment` view `direct_targets`) | `modules/G.direct_targets.md` |
| G.meaning | The gene sets a finding's genes fall in (run by `enrichment` view `meaning`) | `modules/G.meaning.md` |

<!-- end of index -->
