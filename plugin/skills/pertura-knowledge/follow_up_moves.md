# Follow-up moves: the next step a result calls for

Published screens go deep by asking, at each step, the question the last result raised, until a lead is explained
or the data run out. The moves below are the ones published single-cell CRISPR screens made, read from their Results.
Each names the result that can call for it, what to run, and papers that made it. They are suggestions to weigh
against the question, not steps to take in turn. Run a move with its tool (the table's tool call), on the slice the
result names (`cells`, `by`, `targets`); in the notebook with the blocks (`pt.help()`) where no tool does it; or with
code of your own where neither does. `what` names the move and its trigger. A result names the moves its numbers
suggest, by their ids; the numbers that raise a move are Pertura's defaults. A move that opens a new question becomes
a thread, raised by the result that suggested it.

The common moves as tool calls and as block calls, on the cell set the result rests on (`res` is that result):

| Move | Tool call | Block call |
|---|---|---|
| Guides agree | `reproducibility` view `guides` | `res.by_guide()` |
| The test is calibrated | every result's calibration | `pt.calibrate(res)` |
| Depth does not carry it | | `res.match_depth()` |
| Between or within states | `states` view `within`; the other tools on one state's cells, `cells={"state": "S03"}` | `pt.states(cells)`, then the gene or score call on `pt.screen().subset(cells=...)` of one state's cells |
| Along a trajectory | `states` with `trajectory_root`: each target along the pseudotime | `pt.states(cells, root="S03")` or `root={"day": "0"}`: each cell's diffusion pseudotime from the root in `extra["states"]` |
| Shares of states or phases | `states`; `states` view `cell_cycle` | `pt.composition(of=pt.states(cells))`, or `of=pt.cell_cycle()`; `method` is fisher (the commonest test in published screens), propeller by sample, sccoda, cmh, milo (neighbourhoods), or auto (the design's test per target); then `pt.calibrate` |
| Programs in the cells | `programs` | `pt.programs(method=...)` (cNMF, NMF, GeneNMF), then `pt.scores(programs=res)` |
| Replicates agree before pooling | `reproducibility` view `replicates` | `pt.replicate_agreement()` |
| By condition | the same tool with `by="condition_<factor>"`; `conditions` | the same call with `by="condition_<factor>"` |
| Shared by many targets | `impact` view `shared` | `res.shared()` (genes) |
| Targets that act alike | `similarity` | `pt.similarity()` (each pair against control-guide pairs; `level="guide"` for guides), then `pt.cluster_targets(of=sim)` |
| Programs that targets move together or against each other | `similarity` view `axis` | `pt.correlate_effects(of=scores)`; regulators ranked on two programs: `pt.rank_on(of=scores, up=..., down=..., null=pt.calibrate(scores))` |
| Sets of regulators | `similarity` view `sets` | `pt.cluster_targets(of=scores)` (hierarchical, hdbscan or leiden; each set's stability) |
| The meaning of the changed genes | `enrichment` | `pt.enrich(of=genes)` (up and down apart, the tested genes as the universe; `gene_lists` for one's own sets; `method="gsea"`; `backend="clusterprofiler"`), prior knowledge |
| A pair against its singles | `combinations` | `pt.pair_fit()` (Norman et al. 2019's fit; control pseudo-pairs as an added check, Pertura's choice) |
| The cell types that respond, in a screen of several | | `pt.cell_type_response(column=...)` (Augur's AUC per cell type) |
| The part that stands out from the average perturbation | the same tool with `against="other_targets"` | the same call with `reference="other_targets"` |
| The cells the perturbation reached | `guides` view `escape` | `pt.response()` (Mixscape: perturbed or not) or `pt.response(method="mixscale")` (a score per cell); the same call on the perturbed cells, kept with `pt.save_cells` |
| Other cells, replicates or q than Pertura's defaults | `policy` in a tool's inputs, for one run | `pt.policy(min_cells=..., min_replicates=..., q=...)` for the notebook; the result says how it differs from the default |

A finding's own checks run when it is recorded: each guide alone, the design's other test, the calibration check, the
deposit's calls, matched depth, and halves of the data where they are separate data. The last two are Pertura's own
checks. The moves below are for the questions a result raises beyond these checks.

## One way through a lead

1. Read the result with what it rests on: the cells, the samples, the guides, the test.
2. List the moves it suggests (below), and keep the ones that serve the question.
3. A trust move, where one is suggested, usually comes first, since the rest rests on it. Then organise and locate,
   then the design's contrasts. Outside knowledge usually comes last, and it is always recorded beside the finding as
   prior knowledge, said with the claim it informs. This order is Pertura's suggestion.
4. Run the move, and say which earlier result it changes.
5. A lead ends when it is explained as far as the screen can show, when it fails a trust move, or when no move
   applies. A failed lead is a result too, and is reported as one. Then propose the validation of what is left (the
   paradigm cards' last step).

Trust, organise and locate moves are the agent's to run. Which leads to follow is worth asking the user. A move that
needs outside data or a download waits for the user's approval.

## When a line stops

A line that stops at something the analysis as run cannot answer has met a block. A block is the next problem: each
kind has routes published screens took (the moves below, by id), or no route, said with what the design holds.

| Kind | For example | Routes |
|---|---|---|
| The readout does not measure it | a knocked-out gene's transcript stays; RNA cannot show a protein | X2, C4, T3, a second readout where the screen holds one |
| Two things cannot be told apart | a shift between states against a graded change within them | L1, O1 (a programme's score in place of clusters), L3 |
| Pooled | two models or conditions read together | C1 (each condition apart) |
| Too few | cells or units | T6 (guides pooled, a coarser state, guide by guide), or no route |
| Confounded by the design | the condition is the batch | T7 (within one batch, or the batch as a covariate), often no route |
| Data quality | sparse genes with extreme fold changes | T2, matched depth, a minimum expression |
| A reference missing | no unperturbed sample | the part that stands out from the average perturbation (reference other_targets), or no route |

A line set aside for its interest, not its data, is not a block.

## Trust the result

### T1. Guides agree

- **Worth considering when:** a target has two or more guides.
- **Run:** `reproducibility` view `guides` (F.guides_agree); its table names the targets whose guides do not agree beyond the control-guide pairs. For
  a finding, read each guide alone (`res.by_guide()`, or the finding's guides check). For one state or score, read
  each guide's value against the control cells.
- **Published screens:**
  - Xia 2023 (Fig. 4b): the guides against each gene clustered on the top variable genes
  - Liang 2023 (Fig. 1f): guides clustered by their effects on the top variable genes
  - Alda-Catalinas 2024 (Fig. 4A): four guides per element combined by Fisher's method, with confidence tiers by the
    number of guides that support it

### T2. The test is calibrated

- **Worth considering when:** there are more changed genes than expected, or one guide carries them.
- **Run:** the calibration check of `genes` (C.genes) beside the targets' counts. A target is read against what control-guide groups
  give through the same test. A second test is a choice.
- **Published screens:**
  - Alda-Catalinas 2024 (Fig. S3A,B): the test run on cells with non-targeting guides
  - Zhao 2025 (Fig. 1D): QQ plots of targeting against non-targeting guides' p-values

### T3. Each perturbation acts

- **Worth considering when:** a guide moves its target gene less than the line or not at all, or some cells look
  unperturbed.
- **Run:** `guides` view `own_gene` (B.efficacy), then view `escape` (B.escape), and `reproducibility` view `guides`
  (F.guides_agree). Read three things apart: the perturbation's evidence (under CRISPRi or CRISPRa its target gene's
  change; under knockout its protein or the edits where measured, as its transcript can stay), its downstream
  phenotype (its changed genes or states against the control-guide band), and the share of its cells that respond
  (B.escape). The 30% line describes a knockdown and tests nothing: a guide short of it still acts where its phenotype
  agrees with its target's other guides. A target with a phenotype and a flat target gene is reported as such.
- **Published screens:**
  - Santinha 2023 (Ext. Data Fig. 4b,c): editing is mosaic, so unperturbed nuclei were removed by a discriminant
    analysis on each perturbation's changed genes, and the pseudobulk test repeated
  - Liang 2023 (Fig. 3a-f): a guide does not guarantee a knockout, so cells were kept by their perturbation
    probability (MIMOSCA) before genes were ranked
  - Madrazo 2024 (Fig. S3B): some targets were knocked down poorly, so a stress score was read against each target's
    knockdown efficiency

### T4. Each member of a pair acts

- **Worth considering when:** a label has two guides.
- **Run:** each member's target gene in the pair's cells against its single and the control cells. A pair with a
  member that does not act behaves like its other single, and `combinations` (E.combinations) with model regulatory_modes uses such
  pairs as
  its reference. Only the pairs whose members both act are read as interactions (Pertura's choice).
- **Published screens:**
  - Wessels 2023 (Fig. 4c and 4g): each pair's effect against the average and the product of its singles, then each
    pair's profile decomposed as a combination of its singles'

### T5. Neighbours are spared

- **Worth considering when:** CRISPRi acts at a locus.
- **Run:** the genes beside the target, and alternative promoters, in the target's cells.

### T6. Enough cells

- **Worth considering when:** targets have few cells, or effects are weak.
- **Run:** the field's minimum of cells per guide or target (published screens use 5 to 400; Pertura's default is 10,
  changed with `pt.policy(min_cells=...)` or `policy`), and the targets it leaves. Then the effect with fewer cells
  (`reproducibility` view `fewer_cells`), or in halves of the data where the halves are separate data: halves of the samples, or of the cells in vitro. In
  vivo a guide's cells can be clones, so halves of its cells are not separate data. The check on halves is Pertura's
  choice.
- **Published screens:**
  - Replogle 2022: a minimum of 25 cells per guide or target; cells downsampled to equal numbers across arms before
    effect sizes were compared

### T7. Not state, depth or coverage

- **Worth considering when:** an effect could follow cell state or read depth.
- **Run:** the same test with the depth or the state as a covariate, as published screens put such terms in the
  per-gene test; or the test at matched depth (`res.match_depth()`, Pertura's choice). Where the perturbation itself
  moves the cells' total RNA, either check asks a changed question: whether the effect holds beyond total RNA (U7).
- **Published screens:**
  - Ota 2025: the genes detected and the mitochondrial share as covariates of the per-gene test
  - Zhu 2026: the cells per perturbation in the per-gene model

## Organise the effects

### O1. Programs

- **Worth considering when:** targets change many genes.
- **Run:** `scores` with the field's gene sets, or `programs` for the screen's own (C.programs, cNMF), then each
  target's effect on each program.
- **Published screens:**
  - Morris 2023 (Fig. 5A,B): a large network of mixed direction split by single-cell co-expression into coherent parts
  - Sunshine 2023 (Fig. 5A,B): a published gene list scored per cell, to find the perturbations that change that
    response

### O2. Regulators of a program

- **Worth considering when:** programs are defined.
- **Run:** each perturbation's effect on each program (`scores` or `programs`, C.programs).

### O3. An axis across regulators

- **Worth considering when:** targets have effects on several programs.
- **Run:** `similarity` view `axis` (E.program_axis) on the table of a `scores` or `programs` run: two programs'
  effects correlated over the perturbations.

### O4. Similar perturbations

- **Worth considering when:** perturbation profiles are at hand.
- **Run:** `similarity` (E.similar_targets): the targets' profiles against each other, with pairs of control guides as the
  reference, and against known complexes as prior knowledge.
- **Published screens:**
  - Schnitzler 2024 (Fig. 4b): a strong regulator of unknown function placed by its correlation with a known gene's
    knockdown across programs
  - Sunshine 2023 (Fig. 4D): perturbations grouped by function from their cluster-occupancy profiles

### O5. The reason two look alike

- **Worth considering when:** two perturbations look similar.
- **Run:** their shared genes (`similarity` gives them), and whether one changes the other's target gene.

### O6. Co-functional sets

- **Worth considering when:** one program has several regulators.
- **Run:** `similarity` view `sets` (E.co_functional_sets): those regulators clustered by their effects on the other programs.

## Locate them in the cells

### L1. Between or within states

- **Worth considering when:** state composition shifts.
- **Run:** `states` view `within` (C.within_states) on the cell_states and result tables of a `states` run. It
  shows whether a target moves cells between states, changes them within a state, or both.
- **Published screens:**
  - Xia 2023 (Suppl. Fig. 6k-m): changed genes within each cell state beside the states' shares
  - Sunshine 2023 (Fig. 4B,C): each perturbation's odds of falling in each cluster against non-targeting cells

### L2. A state's identity

- **Worth considering when:** a state is new or mixed.
- **Run:** its markers (the dot plot), doublets, a reference cell type.

### L3. Along a trajectory

- **Worth considering when:** a change is graded.
- **Run:** the cells' position on a reference axis, or along a pseudotime (`states` with
  `trajectory_root`); density maps.

### L4. Cell types

- **Worth considering when:** the screen holds several cell types or lines.
- **Run:** the effect within each (the same tool with `by` the column of cell types, or with `cells`).

### L5. Uneven response

- **Worth considering when:** one guide's cells respond unevenly.
- **Run:** each cell's response against its own target-gene level.

## Use the design's own contrasts

### C1. Condition by perturbation

- **Worth considering when:** the screen holds two or more conditions or tissues.
- **Run:** `conditions` (E.condition_x_perturbation; what `controls` gives the control cells' own response);
  say which control cells each condition uses.

### C2. Pairs

- **Worth considering when:** the library holds guide pairs.
- **Run:** `combinations` (E.combinations), read for the pairs whose members act.

### C3. Two directions

- **Worth considering when:** one gene has both CRISPRa and CRISPRi.
- **Run:** `conditions` view `modes` (E.opposite_modes), read where both directions move the gene (`guides` view
  `own_gene`).

### C4. Dose

- **Worth considering when:** guides or elements differ in on-target strength.
- **Run:** whether the downstream effect scales with the on-target change.

### C5. Enhancer to gene

- **Worth considering when:** an enhancer is perturbed.
- **Run:** the genes nearby, by distance.

## Explain with outside knowledge (prior knowledge, recorded as such)

### X1. Pathways

- **Worth considering when:** targets change genes.
- **Run:** `enrichment` with `de` (the `genes` run) and `targets` (G.meaning): each target's changed genes, up and
  down apart, against the genes tested for it, with the field's sets (`collection`) or one's own (`gene_lists`); method
  gsea for each target's ranked genes. Record what papers say beside the finding (prior knowledge).
- **Published screens:**
  - Zhang 2023 (Fig. 2J): GO enrichment of each guide's changed genes against non-targeted cells

### X2. Direct targets

- **Worth considering when:** a perturbed transcription factor changes genes.
- **Run:** `enrichment` view `direct_targets` (G.direct_targets) on a `genes` run's table (CollecTRI, kept
  offline): the factor's activity from its known
  targets (Pertura's choice).

### X3. Reference states

- **Worth considering when:** states or programs are found.
- **Run:** known cell states or atlases, after the user approves any download.

### X4. Genetics, patients, other screens

- **Worth considering when:** programs, regulators or hits are found.
- **Run:** published data the user points to, or approves fetching.
