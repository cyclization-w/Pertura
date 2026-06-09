# Perturb-seq Native Agent Strategy

Status: architecture direction, not an implementation spec.

## Decision

Pertura should be refactored into a perturb-seq native analysis agent, not
rewritten from scratch and not kept product-first as a generic scientific
harness.

The existing audit runtime is valuable and should stay. The public product
surface should change: Pertura should present itself as a perturb-seq analysis
console with an audited execution substrate underneath. "Scientific harness" can
remain an internal engineering layer, but it should no longer be the first thing
the user or the LLM sees.

## Why This Is The Right Cut

The current codebase is already perturb-seq shaped:

- The built-in graph is perturb-seq specific: workspace inspection,
  experimental design, scRNA-seq QC, guide assignment, perturbation validation,
  target QC, state reference, effect exploration, target discovery, biology
  story, and reporting.
- The active work order and repair hints already know about `control_labels`,
  `guide_column`, `target_column`, `state_labels`, `perturbation_modality`,
  `moi`, Scanpy, and AnnData.
- The tests exercise perturb-seq capability contracts, design gates, target
  interpretation, guide assignment, control labels, and `run_de` far more than
  any other scientific domain.

Trying to preserve a broad future harness identity at the product layer now
creates complexity without buying much. The better split is:

- Product: perturb-seq native.
- Runtime: general enough to keep audit, replay, evidence, graph gates, and
  branch provenance reusable.

## External Signals

These systems were inspected as product and architecture references:

- STAT-agent: product-clean spatial transcriptomics chat with a fixed
  orchestrator, skill selection pipeline, prerequisite clarification, SSE
  events, notebook logging, and skill markdown.
  Source: https://github.com/chenyhvvvv/STAT-agent
- ChatSpatial: schema-enforced MCP tool server. Its strength is typed domain
  tools, compact result models, and broad spatial method coverage rather than a
  full chat workbench.
  Source: https://github.com/cafferychen777/ChatSpatial
- CellAtria: document/GEO ingestion plus a standardized CellExpress pipeline,
  exposed through a user-facing agent and terminal/log panels.
  Source: https://github.com/AstraZeneca/cellatria
- CellAgent: broad single-cell and spatial toolkit with a multi-agent framing
  and sc-Omni methods. It shows the value and risk of large tool coverage:
  coverage alone does not make the product flow clear.
  Source: https://github.com/23AIBox/cellagent
- scChat: planner, executor, evaluator, critic, response generator, and RAG for
  contextual scRNA-seq exploration. It is useful as a conversation architecture
  reference, but Pertura should express evaluation through gates and audit
  rather than adding many independent agent roles.
  Source: https://github.com/li-group/scChat
- CellTypeAgent: candidate cell type prediction followed by ontology
  normalization and CellxGene expression-based reranking. The lesson is that
  biological claims should be reranked and verified by domain evidence, not only
  proposed by an LLM.
  Source: https://github.com/jianghao-zhang/CellTypeAgent
- CASSIA: focused single-cell annotation with scoring, annotation boost,
  consensus, subclustering, and HTML reports. The key product lesson is
  first-class quality scores and targeted boost actions for weak results.
  Source: https://github.com/ElliotXie/CASSIA
- CyteType: evidence-based cell type annotation exposed as a clean AnnData
  object API plus typed request/result schemas, remote job status, stored
  results in `adata.uns`, report URLs, marker/expression summaries, and an
  embedded results chat.
  Source: https://github.com/NygenAnalytics/CyteType
- scPilot: omics-native reasoning for annotation, trajectory inference, and
  GRN prediction. Its implementation is an explicit hypothesis -> experiment
  -> environment/tool -> evaluation -> refinement loop.
  Source: https://github.com/maitrix-org/scPilot
- ELISA: expression-grounded interactive single-cell discovery. It routes user
  input through gene, semantic, mixed, comparison, interaction, pathway, plot,
  and report commands backed by cached embeddings and grounded context.
  Source: https://github.com/omaruno/ELISA-An-AI-Agent-for-Expression-Grounded-Discovery-in-Single-Cell-Genomics
- SRAgent: agentic SRA/GEO data and publication retrieval with low-level tools,
  agent wrappers, workflow graphs, accession conversion, metadata extraction,
  and optional SQL progress state.
  Source: https://github.com/ArcInstitute/SRAgent
- OmicClaw: gateway-first OmicVerse workspace with web UI, channels,
  notebooks, terminal, kernel/session services, skill registry, and long-form
  OmicVerse `SKILL.md` checklists.
  Source: https://github.com/Starlitnightly/omicclaw
- OmicVerse / OVAgent: broad multi-omics analysis engine with J.A.R.V.I.S.,
  MCP serving, OVAgent workflow policy, structured runtime events, tool
  metadata registry, and execution repair envelopes.
  Source: https://github.com/Starlitnightly/omicverse
- CellWhisperer: transcriptome-language model plus cellxgene-style interactive
  exploration, dataset preprocessing, hosted/local text embedding, and
  transcriptome-to-text scoring.
  Source: https://github.com/epigen/CellWhisperer
- ChatCell: Cell2Sentence/T5-style natural-language single-cell tasks such as
  annotation, pseudo-cell generation, and drug sensitivity prediction.
  Source: https://github.com/zjunlp/ChatCell
- InstructCell: multimodal instruction-following single-cell model for
  annotation, pseudo-cell generation, and drug sensitivity prediction.
  Source: https://github.com/zjunlp/InstructCell

Evidence level:

- Local code inspected: STAT-agent, ChatSpatial, CellAtria, CellAgent, scChat,
  CellTypeAgent, CASSIA, CyteType, scPilot, ELISA, SRAgent, OmicClaw,
  OmicVerse/OVAgent, CellWhisperer, ChatCell, InstructCell.
- Public landscape map still to verify in code before implementation depends
  on it: AnnoAgent, MAT-Cell, scAgent, BioDiscoveryAgent, PerturbAgent,
  PhenoGraph, SpatialAgent/STAgent.

Broader landscape map:

| System | Main shape | Lesson for Pertura |
| --- | --- | --- |
| STAT-agent | Spatial transcriptomics chat app with skill pipeline, verifier, SSE, notebook logs | Product flow and live execution should be first-class. Do not copy the fixed pipeline. |
| ChatSpatial | MCP server with schema-validated spatial tools and compact result models | Capabilities should be typed, bounded, and domain-specific. |
| CellAtria | Document/GEO ingestion plus standardized CellExpress execution | A clear workflow narrative matters more than genericity. |
| CellAgent | Multi-agent scRNA/ST framework plus sc-Omni toolkit | Broad tool coverage is not enough; product guidance and state clarity are the bottleneck. |
| scChat | Planner/executor/evaluator/critic/response generator with RAG | Evaluation can inspire Pertura, but should be expressed through gates/audit rather than many extra roles. |
| CellTypeAgent | Candidate annotation with Cell Ontology and CellxGene expression verification | LLM proposals should be reranked by external biological evidence. |
| CASSIA | Annotation, scoring, boost, consensus, subclustering, reports | Weak results need quality scores and targeted boost actions. |
| CyteType | Evidence-based multi-agent cell type annotation with ontology mapping, typed schemas, stored results, report URLs | Annotation reasoning should be inspectable and evidence-backed. Product APIs should expose domain objects, not raw traces. |
| scPilot | Omics-native hypothesis/experiment/environment/evaluation loop | Pertura can show this loop as product stages without adding separate agent classes for each role. |
| ELISA | Expression-grounded chat with query routing, retrieval, plots, comparisons, and reports | Chat works when it is backed by domain routers and cached evidence, not free-form history. |
| SRAgent | SRA/GEO accession, metadata, publication, and workflow agents | Dataset ingestion and provenance deserve first-class product states. |
| OmicClaw | Gateway/web workspace with notebooks, terminal, kernel/session services, channels, skill registry | A polished workbench needs runtime observability, but long skill sheets should become compact dynamic cards. |
| OmicVerse / OVAgent | Broad multi-omics engine with J.A.R.V.I.S., MCP, workflow policy, event stream, tool registry, repair loop | Pertura should not compete as a broad platform; it should specialize the same ideas around perturb-seq evidence. |
| CellWhisperer | Transcriptome-text model with cellxgene exploration and text scoring | Natural-language exploration is valuable after data is represented, but it does not replace audited execution. |
| ChatCell | Cell-as-text natural-language tasks with T5 generation | Text bridges help accessibility, but perturb-seq claims need executable evidence and design gates. |
| InstructCell | Multimodal instruction-following for single-cell tasks | Instruction tuning reduces friction; Pertura should express instructions as state-changing product turns. |
| AnnoAgent | Strategist/assessor wrapper over CellTypist, scVI, SVM, scBiGNN | Ensemble execution plus assessor logic is useful for high-risk biological calls. |
| MAT-Cell | Tree-structured multi-agent proof generation for batch-level annotation | Biological claims can be represented as verifiable reasoning trees, not just labels. |
| scAgent | LLM agent for universal single-cell annotation | Agentic annotation needs explicit novelty/OOD handling. |
| BioDiscoveryAgent | Closed-loop design of genetic perturbation experiments | Perturbation reasoning can be branch/search oriented, not only analysis-report oriented. |
| PerturbAgent | Agentic system for genetic perturbation analysis/prediction | Pertura should distinguish analysis evidence from predictive perturbation modeling. |
| PhenoGraph | Multi-agent phenotype-driven spatial discovery with knowledge graphs | Knowledge graph interpretation is useful after evidence is registered. |
| OmicClaw / OmicsClaw | Multi-omics natural-language agent with skills, memory, MCP, OmicVerse/J.A.R.V.I.S. style execution | Persistent memory and skill catalogs are valuable, but Pertura's differentiator is audit-grade evidence. |
| SpatialAgent / STAgent | Spatial biology agents with broad tools and multimodal input | Multimodal UI and deep research are useful, but should not bury the main analysis run. |

The pattern is consistent: successful systems make one domain feel native. The
most compelling products do not ask users to think in terms of a generic
harness; they expose biological objects, workflow progress, live execution, and
quality/review state directly.

## What To Absorb, What Not To Copy

Absorb:

- STAT's live product stream and structured clarification UX.
- ChatSpatial's typed domain tool schemas and compact result payloads.
- CyteType's `AnnData`-native input/output ergonomics, job/report state, and
  evidence-first result storage.
- SRAgent's separation of low-level tools, agent wrappers, and workflow graphs
  for data ingestion/provenance.
- ELISA's query router, plot/report commands, and grounded context trimming.
- scPilot's visible scientific reasoning loop.
- OmicClaw's browser workbench, kernel/terminal/notebook visibility, and skill
  registry layout.
- OVAgent's structured event stream, tool metadata policy, workflow document,
  and normalized repair envelope.
- CellWhisperer/ChatCell/InstructCell's lesson that natural language is a good
  exploration layer once data has a representation.
- CASSIA and CellTypeAgent's scoring, boost, ontology, and evidence
  verification mindset.

Do not copy:

- A fixed STAT-like pipeline as the only run controller.
- A pure chat memory model where conversation history becomes source of truth.
- Broad scRNA/spatial method catalog growth before perturb-seq design, guide,
  control, target, contrast, and evidence loops are reliable.
- Model-centric cell-as-text or embedding exploration as the main run
  controller for perturb-seq analysis.
- Multi-agent role classes when Pertura already has gates, product projections,
  trace events, observation memory, and repair policies that can express the
  same responsibilities with less state sprawl.

## Product Thesis

Pertura is a perturb-seq native analysis agent with audited execution.

The user should experience it as:

1. Load a perturb-seq workspace.
2. Confirm or let the system infer the experimental design.
3. Watch the agent run code, inspect outputs, and make progress through the
   perturb-seq analysis flow.
4. Answer structured questions only when design authority or high-risk repair is
   needed.
5. See figures, tables, notebook cells, artifacts, observations, and a traceable
   report as the run develops.

The LLM should experience it as:

1. A perturb-seq task card.
2. A current analysis node with clear progress.
3. A small set of ready capabilities.
4. Required inputs, prechecks, expected outputs, common errors, and repair hints.
5. A clear next action, including when to advance the graph.

The runtime should continue to enforce:

- Event-sourced state.
- Snapshot/replay.
- Gated dispatch.
- Capability contracts.
- Observation memory.
- Artifact provenance.
- Repair review.
- Branch and evidence traceability.

Pertura should be both a full-workflow perturb-seq analyst and a chat-like
exploration surface, but these are not equal layers:

- The run state is workflow-native: design, node, capability, attempt,
  observation, artifact, branch, report.
- The user interface can be chat-like: one-line requests, questions, live
  output, generated figures, and follow-up exploration.
- The source of truth is never chat history. A user turn becomes one of:
  recorded goal, design answer, graph edit, branch instruction, repair
  decision, report request, or exploratory question tied to existing evidence.

This makes Pertura feel conversational without becoming an unstructured chat
app.

## Perturb-seq Hard Problems Pertura Should Own

Pertura should not claim "an LLM can analyze any dataset." Its stronger claim is
that it makes the specific failure modes of perturb-seq analysis explicit,
audited, and recoverable.

Core hard problems:

- Design authority: controls, guide columns, target mapping, perturbation
  modality, MOI/loading, replicate/batch/time/state columns, and contrast
  definitions are often missing, ambiguous, or encoded in lab-specific names.
- Guide assignment uncertainty: guide count thresholds, multi-guide cells,
  low-MOI assumptions, ambient guide capture, guide-to-target mapping, and
  unassigned/multi-assigned cells change downstream conclusions.
- Target coverage and power: cells per target, cells per guide, guide
  concordance, control balance, and target dropout determine whether a
  negative result is meaningful or just underpowered.
- Confounding: batch, donor, sample, cell state, library size, cell cycle,
  guide capture chemistry, and perturbation viability can masquerade as target
  effects.
- Method sensitivity: DE method, covariates, filtering thresholds, aggregation
  level, pseudo-bulk vs cell-level modeling, and multiple-testing correction
  can flip rankings.
- Evidence translation: turning code outputs into biological claims requires
  traceable links from target, contrast, method, parameters, observations,
  figures, and limitations.
- Recovery from agent errors: LLM code often fails on AnnData/Scanpy API
  details or registers observations incorrectly; repair must be bounded,
  audited, and visible.

Pertura's product promise should be:

```text
For perturb-seq analysis, Pertura turns ambiguous design and agent execution
into a structured, inspectable, branchable evidence workflow.
```

This is narrower than a generic science harness, but much stronger as a
scientific product claim.

## Core Claims v3

Public claims should evolve to:

1. Perturb-seq Design Ledger:
   Pertura builds a source-aware ledger of controls, guides, targets, batches,
   contrasts, modality, MOI, and unresolved design assumptions, then gates
   interpretation on the ledger.
2. Audited Evidence Execution:
   Every perturbation claim is backed by code, parameters, output, registered
   observations, artifacts, and a replayable event trace.
3. Guided Agent Freedom:
   The LLM can choose analyses, ask questions, repair code, and open branches,
   but capability cards and graph gates keep actions scientifically ordered.
4. Observable Analysis Console:
   The user sees the live run: plan, code, stdout/stderr, questions, repairs,
   figures, tables, artifacts, report sections, and current biological stage.
5. Branchable Parameter Search:
   Thresholds, DE methods, covariates, contrasts, filtering choices, and repair
   alternatives can be run as auditable branches and compared before promotion.

Internal implementation claims remain:

- Event-sourced state and replay.
- Observation memory and evidence chain.
- Trace-driven repair and audit.
- Editable graph and capability contracts.

## Keep, Move, Hide

Keep as the audited runtime core:

- `Store`, reducer, `Snapshot`, event schema.
- `gated_dispatch` and graph gate checks.
- Kernel execution, attempts, outcomes, artifacts, observations.
- Observation memory, evidence chain, replay, capsule/audit primitives.
- Candidate actions, execution state, SSE/product events.
- Auto-repair policy, but only with risk and provenance checks.

Move into a perturb-seq product layer:

- Design parsing and design authority.
- Capability cards and method templates.
- Node navigation language.
- GUI view model.
- Conversation/turn routing.
- Domain repair hints and common error hints.
- Report sections and biological summary language.

Hide from the default product surface:

- Harness manifesto language.
- Generic domain authoring language.
- Full debug context views.
- Raw event lists.
- Generic capability terminology when a perturb-seq term is clearer.

These can remain available in operator/debug mode.

## Proposed Package Shape

Add a product layer beside the existing runtime:

```text
pertura/
  core/                         # audited runtime substrate
  agent/                        # provider/tool loop and run control
  product/
    perturbseq/
      ontology.py               # control, guide, target, batch, moi, modality, contrast
      design_ledger.py          # design facts, sources, confidence, missing fields
      capability_catalog.py     # perturb-seq capability cards
      prechecks.py              # suggested and executable checks
      turn_router.py            # user message -> start/answer/continue/edit/report
      view_model.py             # GUI-first perturb-seq workbench projection
      repair_hints.py           # common Scanpy/AnnData/contract repair hints
      report_sections.py        # perturb-seq report outline and claims
      quality.py                # confidence, coverage, contradiction, boost triggers
      sweeps.py                 # parameter grids and branch comparison metadata
```

The existing `pertura/domain/perturbseq.py` can become the seed content for this
layer. The point is not to delete the domain pack immediately, but to stop
forcing a generic domain-pack abstraction to be the primary product language.

## Refactor Boundary

This is a large refactor, not a rewrite.

Keep:

- `pertura/core/store.py`, `reducer.py`, `event_schema.py`, `replay.py`.
- `pertura/agent/gated_dispatch.py`, `loop.py`, `tool_loop.py`,
  `auto_repair.py`.
- `pertura/kernel/*`.
- Existing graph, capability, condition, observation, audit, evidence, and
  report primitives.
- Current FastAPI server and GUI shell while the new product projection grows.

Thin or move behind developer mode:

- Generic harness copy in README, GUI labels, work order headings, and default
  claim names.
- Raw `compile_context()` / debug context surfaces from the default LLM and GUI
  path.
- Full capability browser as a first-screen product panel.
- Any fallback that shows all graph nodes/tools to the LLM when a current
  perturb-seq node card can provide a bounded action menu.

Move into `pertura/product/perturbseq/`:

- Perturb-seq terminology and synonyms.
- Design field inference and source/confidence rules.
- Capability cards and common error/repair hints.
- GUI view model and product timeline labels.
- Graph node copy, biological stage labels, precheck suggestions.
- Quality/boost and branch/sweep metadata.

Delete later, only after replacement:

- Duplicate UI projections that are no longer read by either API or GUI.
- Test helpers that only preserve obsolete generic wording.
- Dead capability/template paths that are not reachable from the perturb-seq
  catalog or operator mode.

Do not delete first. The migration should make the new product path primary,
then remove unreachable legacy surfaces with tests.

## Product-Native Module Responsibilities

`ontology.py`

- Own perturb-seq vocabulary: guide, sgRNA, target, NT control, safe-targeting
  control, donor, batch, replicate, time point, condition, covariate, modality,
  MOI, loading, contrast, pseudo-bulk, guide concordance.
- Provide synonym maps and column-name heuristics.
- Provide display labels and warnings for ambiguous names.

`design_ledger.py`

- Compile design facts from `Snapshot.design`, observations, artifacts, schema
  summaries, and user answers.
- Track source and confidence per field.
- Produce missing/ambiguous/contradictory design issues.
- Produce structured questions and suggested candidate actions.

`capability_catalog.py`

- Convert raw capabilities into perturb-seq cards.
- Include biological question, required inputs, prechecks, default methods,
  expected observations/artifacts/plots, common errors, repair hints,
  risk-level, and branchable parameters.
- Hide raw runtime tools unless the current card explicitly needs them.

`view_model.py`

- Build the GUI-first projection: console state, design ledger, flow state,
  live timeline, artifacts, evidence board, branch board, report preview,
  and debug links.
- Keep product events separate from raw runtime events.

`turn_router.py`

- Route one user message into start, answer design question, continue, edit
  graph/design, approve repair, create branch, run sweep, inspect artifact, or
  generate report.
- Avoid durable free-form chat state; all turns mutate audited state or query
  evidence.

`quality.py`

- Score design completeness, guide assignment reliability, target coverage,
  guide concordance, contrast validity, method sensitivity, evidence strength,
  and report readiness.
- Create boost actions when evidence is weak, conflicting, underpowered, or
  suspicious.

`sweeps.py`

- Define branchable parameters by capability.
- Create branch plans for thresholds, DE methods, covariates, filtering, and
  contrast choices.
- Compare branch outputs and support branch promotion.

## LLM Data Flow

The hot path should become:

```text
Snapshot
  -> PerturbSeqDesignLedger
  -> PerturbSeqNodeCard
  -> CapabilityCard list
  -> ActiveTurnCard markdown
  -> tool loop
  -> gated_dispatch
  -> event store
  -> product events / observations / artifacts / navigation
```

The LLM should not repeatedly inspect the workspace once the dataset profile is
materialized. A node card should carry:

- What is already known.
- What was tried.
- What material output exists.
- What evidence is missing.
- Which capabilities are ready.
- Which tools are intentionally hidden.
- Whether the node should advance.

This keeps the LLM free to reason while preventing repeated low-value actions.

## Capability Definition

Capabilities should be product skills backed by audited runtime tools. They
should not be either raw Python snippets or huge static `SKILL.md` files.

Recommended shape:

```text
CapabilityCard
  id
  title
  biological_question
  stage
  required_design_fields
  required_materials
  prechecks
  default_method
  alternative_methods
  branchable_parameters
  expected_observations
  expected_artifacts
  expected_plots
  registration_contract
  common_errors
  repair_hints
  interpretation_limits
  risk_level
```

Examples:

- `profile_dataset`: summarize AnnData shape, obs/var columns, layers, obsm,
  sparse/dense status, candidate guide/control/target columns, and loaded data
  artifact.
- `resolve_design`: infer and confirm controls, guide column, target mapping,
  modality, MOI/loading, batch/replicate/time/state columns, and contrast.
- `run_scrna_qc`: compute QC metrics, filtering candidates, before/after
  counts, plots, and filtering decision.
- `assign_guides`: assign/validate guides and targets, record thresholds,
  multi-guide policy, and assignment uncertainty.
- `check_target_coverage`: compute cells per target/guide/control, detect weak
  targets, and suggest branch/sweep thresholds.
- `validate_perturbation`: check expected target expression or signature
  direction where measurable.
- `run_target_effects`: run DE/composition/module/trajectory effects under a
  named contrast with covariates and correction method.
- `compare_methods`: branch DE or threshold alternatives and compare stability.
- `build_report`: compile claims with trace links, limitations, figures, and
  tables.

Each card should compile from current state:

- `ready`: all required fields/materials are present.
- `missing`: fields/materials that block execution.
- `suggested_prechecks`: checks that can be run before execution.
- `next_repair`: likely repair if the previous attempt failed.
- `hidden_tools`: raw tools hidden because the node should advance or because
  material output already exists.

This combines STAT-like skill clarity with Pertura's dynamic state and audit
contracts.

## Trace And Audit Model

The user-facing trace should be product-level, with raw audit one click away.

Product trace:

- `planning`: current biological question and selected capability.
- `design_update`: field changed, source, confidence, and whether user
  confirmation is needed.
- `running_code`: code cell started, capability, branch, and expected outputs.
- `execution_output`: stdout/stderr tail and status.
- `artifact_ready`: figure/table/notebook/checkpoint with preview metadata.
- `observation_recorded`: metric, target, contrast, method, branch, confidence.
- `question_opened`: structured design/repair/branch question.
- `repair_proposed`: failed attempt, error class, patch risk, confidence.
- `repair_applied`: automatic or user-approved retry.
- `branch_started`: parameter or method alternative launched.
- `branch_compared`: stability/quality summary.
- `claim_added`: report claim with evidence refs.
- `blocked`: design, safety, capability, or quality blocker.
- `complete`: run or stage completion.

Raw audit remains:

- Full event log.
- Attempt code and outputs.
- Gate decisions.
- Capability contract validation.
- Evidence chain review.
- Replay/capsule verification.

The GUI default timeline should show product trace. Developer inspector should
show raw runtime trace. The LLM sees a compact recent product trace plus the
current node/capability/design card, not the entire event history.

## Branches, Subprocesses, And Parameter Search

Branching should be a first-class audited operation, not an informal notebook
rerun.

Branch types:

- `parameter_sweep`: threshold/method/covariate/filtering grid.
- `repair_branch`: risky code repair or alternative implementation.
- `analysis_branch`: alternate biological route, such as composition vs DE vs
  module scoring.
- `design_branch`: ambiguous control/target/contrast interpretation that cannot
  be resolved immediately.

Branch record:

```text
branch_id
parent_branch_id
reason
capability_id
node_id
parameters
design_overrides
attempt_ids
artifact_ids
observation_ids
quality_summary
promotion_status
```

Search policy:

- Small deterministic sweeps can run automatically when all design gates pass.
- Expensive, high-risk, or interpretation-changing sweeps become candidate
  actions.
- Branches cannot silently overwrite main observations; promotion records why a
  branch result becomes the selected evidence.
- Report claims include branch lineage when a selected result came from a
  sweep.

This is where Pertura can beat product-only agents: parameter exploration
becomes reproducible evidence, not hidden trial-and-error.

## Perturb-seq Concepts As First-Class State

These should not be generic strings hidden in `design`:

- Dataset path and loaded AnnData profile.
- Guide column, guide chemistry, and guide assignment method.
- Target column and guide-to-target mapping.
- Control labels and control authority source.
- Perturbation modality: CRISPR KO, CRISPRi/a, overexpression, drug, mixed.
- MOI/loading assumptions and multi-guide policy.
- Batch/sample/replicate/time/cell-state columns.
- Target coverage and cells-per-target thresholds.
- Guide concordance and target aggregation policy.
- Contrast definitions.
- DE method, covariates, correction method, and filtering thresholds.
- Negative/suspicious result flags.

Each field should carry source and confidence:

```text
source = user_confirmed | data_observed | inferred_from_schema | imported_metadata | llm_hypothesis
```

## Capability Card Shape

Perturb-seq capabilities should be closer to product skills than raw tools:

```text
id
title
biological_question
required_inputs
optional_inputs
prechecks
method_defaults
expected_observations
expected_artifacts
expected_plots
common_errors
repair_hints
report_section
risk_level
branchable_parameters
```

The LLM sees the card. The GUI renders the card. The gate enforces the contract.

## GUI Direction

The default GUI should not look like a harness debugger. It should look like a
perturb-seq analysis console.

Primary areas:

- Analysis Console: user instruction, workspace, active question, candidate
  actions.
- Live Agent Run: product timeline, code cells, stdout/stderr, generated plots,
  artifacts.
- Design Ledger: controls, guides, targets, batches, modality, MOI, contrast,
  confidence, unresolved fields.
- Perturb-seq Flow: editable graph of analysis stages with prechecks and
  completion evidence.
- Evidence Board: observations grouped by target, contrast, method, branch,
  confidence, and conflicts.
- Parameter Sweep/Branch Board: compare thresholds, methods, contrast choices,
  and selected branch.
- Report Preview: claims, figures, tables, limitations, and trace links.

Debug/audit panels remain, but they should not be the first visual center.

## Claims To Recenter

Replace the public three generic claims with perturb-seq claims:

1. Perturb-seq design authority:
   Pertura identifies, records, and gates control/guide/target/batch/contrast
   assumptions before biological interpretation.
2. Audited perturbation evidence:
   Every target effect, QC decision, guide assignment, figure, and report claim
   traces to code, parameters, artifacts, and observations.
3. Graph-native guided freedom:
   The LLM can choose analyses and branch, but graph gates and capability
   contracts keep scientific order and stop unsafe interpretation.
4. Observable agent execution:
   The user sees the live run story: planning, code, output, artifacts,
   questions, repairs, and completion.
5. Branchable analysis and parameter search:
   Thresholds, methods, contrasts, and repair alternatives can be run as
   auditable branches and compared before selecting a result.

The old claims still exist internally as primitives:

- `analysis_graph`
- `observation_memory`
- `deliberative_audit`

But they become implementation claims, not product claims.

## Migration Plan

### Phase 0: Reposition Without Moving Runtime

- Update README and CLAIMS to say perturb-seq native agent.
- Keep generic harness docs as operator/developer material.
- Add a short architecture diagram showing product layer over audit runtime.
- Add this document as the architectural source of truth for the migration.

### Phase 1: Add Perturb-seq Product Projection

- Create `pertura/product/perturbseq/`.
- Add `PerturbSeqDesignLedger`.
- Add `PerturbSeqCapabilityCard`.
- Add `PerturbSeqWorkbenchView`.
- Make `/api/workbench-view` consume this projection by default.
- Keep the existing snapshot/execution-state projection as a compatibility and
  debug layer.

### Phase 2: Make The LLM Hot Path Product Native

- Rename the LLM-facing work order concept to a perturb-seq turn card.
- Feed the LLM the design ledger, node card, capability card, recent outcome,
  and navigation hint.
- Stop showing repeated inspect/load tools when the current node already has
  material dataset output.
- Auto-advance through explicit graph edges when completion evidence exists.
- Add tests that prove `inspect_workspace` and `load_dataset` are hidden once
  dataset materialization exists and the graph can advance.

### Phase 3: Rebuild The GUI Around The Analysis Console

- Make chat/console plus live run output the main visual area.
- Render design facts and unresolved questions as structured controls.
- Render graph progress as biological workflow, not raw nodes.
- Show code cells, figures, artifacts, and report sections inline.
- Use the product trace as the main timeline and keep raw events in inspector.
- Persist/replay the visible agent story from event-derived product events, so
  users can revisit earlier reasoning and outputs.

### Phase 4: Add Quality/Boost Instead Of More Agent Roles

- Compute target/control/guide/contrast confidence from observations.
- Create boost actions for low-confidence, under-covered, conflicting, or
  negative/suspicious results.
- Use CASSIA-like scoring/reporting ideas, but derive scores from perturb-seq
  evidence and Pertura audit state.
- Add quality badges to evidence board and report preview.

### Phase 5: Branch And Parameter Search

- Add branchable parameter metadata to capability cards.
- Support sweeps for guide assignment thresholds, target coverage thresholds,
  DE methods, covariates, contrasts, and filtering choices.
- Store each sweep as attempts/artifacts/observations with branch provenance.
- Render a comparison table before selecting a branch.
- Start with deterministic small sweeps before introducing expensive LLM-planned
  search.

### Phase 6: Evaluation

- Add small real-data perturb-seq smoke fixtures.
- Test that the agent does not repeatedly inspect after dataset profiling.
- Test that design gates block interpretation until controls/guide/target are
  resolved.
- Test that target effect claims trace to code and observations.
- Test branch comparison and report trace links.

## Minimal Implementation Slice

The first implementation slice should be small enough to land cleanly but large
enough to change the product direction:

1. Add `pertura/product/perturbseq/ontology.py`.
2. Add `pertura/product/perturbseq/design_ledger.py`.
3. Add `pertura/product/perturbseq/capability_catalog.py`.
4. Add `pertura/product/perturbseq/view_model.py`.
5. Make `/api/workbench-view` include `perturbseq`:

```text
perturbseq:
  design_ledger
  active_stage
  ready_capabilities
  blocked_capabilities
  suggested_questions
  quality_flags
  product_timeline
```

6. Make `build_active_work_order()` prefer the perturb-seq turn card when the
   domain is `perturbseq`.
7. Update the built-in HTML GUI to render:
   Analysis Console, Live Agent Run, Design Ledger, Perturb-seq Flow, Evidence
   Board, Artifacts, and Report Preview.
8. Add tests for:
   design ledger field sources, capability readiness, no-repeat inspect/load,
   product timeline projection, and candidate actions for missing controls or
   repair approval.

This slice changes the system's center of gravity without rewriting runtime
code.

## Rewrite vs Refactor

Do not start a new project from scratch right now.

Refactor in place because:

- The event-sourced runtime is the hard part and is already present.
- The existing tests encode useful scientific safety behavior.
- The current pain is mostly product layering and prompt/dataflow, not an
  impossible core.
- A rewrite would likely rebuild the same audit primitives later, with less
  coverage and more uncertainty.

A clean migration can still feel like a new product:

- Public docs and GUI become perturb-seq native.
- Generic harness vocabulary moves behind operator/developer mode.
- The runtime core is treated as infrastructure.
- New modules grow under `pertura/product/perturbseq/`.

## Anti-goals

- Do not copy STAT's fixed five-stage pipeline.
- Do not add durable free-form chat history as the source of truth.
- Do not expose all raw tools to the LLM in every turn.
- Do not remove audit, replay, gates, or observation memory.
- Do not add more agent roles when a gate, projection, or product event can do
  the job.
- Do not chase broad single-cell/spatial method coverage before the perturb-seq
  loop is clear and reliable.

## Immediate Next Steps

1. Add `pertura/product/perturbseq/ontology.py` and `design_ledger.py`.
2. Compile a perturb-seq workbench projection from `Snapshot`.
3. Replace default GUI labels with perturb-seq language.
4. Change the LLM work order to read from the perturb-seq turn card.
5. Add tests for no-repeat workspace inspection after dataset profiling.
6. Add a quality/boost projection for weak target evidence and missing design
   authority.
