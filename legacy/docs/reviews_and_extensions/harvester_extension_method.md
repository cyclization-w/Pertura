# Pertura Extension Method: Trusted-Harvest Wrappers

This document is the complete, followable method for adding external-tool wrappers
(SCEPTRE, Mixscape, Milo/scCODA, CellOracle/pySCENIC, decoupler, cNMF, Augur, QC)
to Pertura without weakening the evidence gate.

The contribution is not "Pertura can run more tools". It is that every wrapped
tool's output is gated identically to native evidence, and its trust comes from a
Pertura-controlled execution channel, not from the model's word.

## 0. The one invariant this method enforces

```text
A measured-tier artifact earns trust only if its execution_hash was produced by a
Pertura-controlled execution (native runner OR subprocess harvest) and is verifiable
against the append-only execution ledger. A caller (Claude) can choose inputs, never
fabricate trust.
```

This single mechanism serves both goals at once:

- it closes the seam where a caller supplies a fake `execution_hash` to a trusted
  method (the measured-tier equivalent of the calibration laundering hole);
- it defines how a harvested external tool earns strict/paper trust.

Native runner and subprocess harvest are the same thing from the gate's view: a
Pertura-controlled execution that stamps a ledger-verified `execution_hash`.

---

## Part A. The trusted execution channel (build this first)

Nothing else works under strict/paper until this exists.

### A.1 Execution ledger

Append-only record, one entry per Pertura-controlled execution:

```text
execution_id
tool_id             # e.g. "sceptre", "mixscape", "celloracle"
tool_version        # probed at run time
method              # canonical method name that enters a trust whitelist
command             # exact argv (subprocess) or native runner id
input_hashes        # sha256 of every declared input file
params_hash         # sha256 of canonicalized parameters
output_hash         # sha256 of the produced structured output
execution_hash      # sha256(tool_id | tool_version | method | command | input_hashes | params_hash | output_hash)
created_at_utc
```

Stored at `artifacts/execution_ledger.jsonl`. It is audit data; it never raises
claim strength by itself.

### A.2 Generic harvest orchestrator (provider-agnostic)

```text
harvest_tool(
    tool_id, command_template, declared_inputs (UID-scoped), params, output_schema,
) -> HarvestResult(structured_output, execution_hash, method, ledger_entry)
```

Behavior:

1. Resolve and hash all declared inputs; refuse paths outside the workspace.
2. Probe tool version; run the tool in a controlled subprocess (captured
   stdout/stderr, timeout, workspace-confined cwd, no network unless declared).
3. Parse the tool output into the structured fields the target registrar needs.
4. Compute `execution_hash` as in A.1; append the ledger entry.
5. Return the structured output plus `execution_hash` and `method`.

Native runners (pseudobulk DE, E-distance, target engagement, NTC/permutation)
use the same ledger and the same `execution_hash` derivation.

### A.3 Two enabling gate changes

1. **Uniform execution_hash path on every measured registrar.** Today only
   `register_measured_de` accepts `execution_hash`. Every measured-tier registrar
   (`register_perturbation_efficiency`, `register_module_effect`,
   `register_global_effect`, `register_composition_effect`) must accept and store a
   top-level `execution_hash`, or none of them can ever be trusted.
2. **Ledger-verified trust.** `is_trusted_execution` (and
   `is_trusted_control_calibration`) must verify that `artifact.execution_hash`
   exists in the execution ledger, not merely that it is a non-empty string.
   A caller-supplied `execution_hash` with no ledger entry is untrusted.

After A.3, `is_trusted_execution` reads:

```text
method in trusted_*_methods
AND execution_hash present
AND execution_hash present in execution ledger      # new
```

### A.4 Method whitelists

- `trusted_runner_methods` (measured tier): add each harvested tool's canonical
  method name (`sceptre`, `mixscape`, `milo`, `sccoda`, `edistance`, `cnmf`,
  `hotspot`, `augur`, `decoupler_ora`, ...).
- Prediction-tier tools (CellOracle, pySCENIC, GEARS, ...) do not need a trust
  whitelist: their ceiling is capped at `predicted_effect` regardless.

---

## Part B. Generic HarvesterSpec

Each wrapper is one declarative spec plus a parser. No new resolver branches.

```text
HarvesterSpec(
    tool_id,
    method,                     # -> whitelist (measured tier only)
    predicate,                  # which EvidencePredicate it produces
    target_registrar,           # register_*
    command_template,           # argv with {placeholders}
    version_probe,              # how to read tool_version
    input_contract,             # required declared inputs + UID scope
    output_parser,              # tool output -> structured fields
    field_mapping,              # structured fields -> registrar kwargs
    trust_tier,                 # "measured" (needs ledger hash) | "prediction" (capped) | "eligibility"
    replicate_handling,         # "external" | "method_internal" (Milo/scCODA)
    self_test,                  # NTC-vs-NTC / label-permutation applicability
)
```

Adding a tool = write one HarvesterSpec + one output parser. The gate, registrars,
resolver, and renderer are untouched.

---

## Part C. Per-tool access contracts

Schematic commands; adapt to installed CLIs/APIs. `{ws}` = workspace.

### C.1 CellOracle + pySCENIC  (prediction tier, build first)
- method: `celloracle_insilico_ko` / `pyscenic_regulon`
- predicate: predicted_cell_state_transition -> capped at `predicted_effect`
- registrar: `register_virtual_cell_state_transition(tool_name, model_or_network_provenance, transition_type, perturbation_query, state_space_reference)`
- command: `python -m pertura_harvest.celloracle --adata {ws}/input.h5ad --grn {ws}/base_grn --ko {gene} --out {ws}/outputs/celloracle.json`
- parse: predicted transition vector, GRN provenance (base network id, links), perturbation_query
- trust: prediction-capped, ledger entry still recorded for audit
- self-test: n/a (prediction)

### C.2 Mixscape / Mixscale  (measured target engagement)
- method: `mixscape`
- predicate: target_engagement -> `measured_target_engagement`
- registrar: `register_perturbation_efficiency(perturbation, target_gene, modality, expected_direction, observed_direction, effect_size, pvalue|padj, method="mixscape", n_target_cells, n_control_cells, quality={pct_perturbed, pct_escaping}, execution_hash)`
- command (pertpy): `python -m pertura_harvest.mixscape --adata {ws}/input.h5ad --pert-col perturbation_uid --control {control_uid} --out {ws}/outputs/mixscape.json`
- parse: pct perturbed/escaping, per-target effect size, p/padj, observed direction
- trust: measured -> needs ledger execution_hash; add `mixscape` to whitelist
- self-test: label-permutation (permuted labels -> no engagement)

### C.3 Milo + scCODA  (measured composition; method_internal replicate showcase)
- method: `milo` / `sccoda`
- predicate: cell_state_composition_shift -> `measured_association`
- registrar: `register_composition_effect(state_source, state_assignment_column, comparison_method, state_counts_by_condition, state_level_deltas, effect_size, pvalue|padj, n_target_cells, n_control_cells, eligibility={replicate_scope:{replicate_handling:"method_internal"}}, execution_hash)`
- command (Milo, R bridge): `Rscript pertura_harvest/milo.R --adata {ws}/input.h5ad --design {ws}/design.tsv --out {ws}/outputs/milo.json`
- command (scCODA, Python): `python -m pertura_harvest.sccoda --adata {ws}/input.h5ad --state-col leiden --condition-col perturbation_uid --reference {ref} --out {ws}/outputs/sccoda.json`
- parse: per-neighborhood/state deltas, spatialFDR/posterior, state counts by condition
- trust: measured -> ledger hash + whitelist; `replicate_handling="method_internal"` (only trusted execution may use this path)
- self-test: label-permutation

### C.4 SCEPTRE  (measured DE, gold; R bridge, last)
- method: `sceptre`
- predicate: differential_expression -> `measured_association`
- registrar: `register_measured_de(contrast_left, contrast_baseline, method="sceptre", n_left, n_baseline, multiple_testing="BH", has_padj=True, columns, source_data, scope, eligibility={control_calibration:{empirical_null:"ntc", n_ntc, calibrated_pvalue:true}}, execution_hash)`
- command: `Rscript pertura_harvest/sceptre.R --counts {ws}/counts.mtx --grna {ws}/grna.tsv --contrast {contrast_uid} --ntc {ntc_list} --out {ws}/outputs/sceptre.csv`
- parse: per-gene logFC, p, calibrated padj; NTC null summary
- trust: measured -> ledger hash + whitelist
- self-test: NTC-vs-NTC (control-vs-control -> ~0 significant) AND label-permutation

### C.5 decoupler / GSEApy  (curated context)
- method: `decoupler_ora` / `gseapy_prerank`
- predicate: curated_enrichment_context -> capped at curated context unless bound
- registrar: `register_curated_enrichment(input_measured_artifact_id, input_gene_set_hash, background_universe, database, database_version, term_id, term_name, method, pvalue, padj)`
- command: `python -m pertura_harvest.decoupler --de {ws}/outputs/de.csv --gene-sets {db} --out {ws}/outputs/enrichment.json`
- parse: term ids, p/padj, gene-set hash, background universe, db version
- trust: bound to a measured artifact; capped regardless -> ledger optional
- self-test: n/a

### C.6 cNMF / Hotspot  (measured module)
- method: `cnmf` / `hotspot`
- predicate: module_score_shift -> `measured_association`
- registrar: `register_module_effect(module_id, module_name, module_source="data_derived", module_gene_set_hash, scoring_method, effect_size, method, pvalue|padj, n_target_cells, n_control_cells, execution_hash)`
- command: `python -m pertura_harvest.cnmf --adata {ws}/input.h5ad --k {k} --out {ws}/outputs/cnmf.json`
- parse: module gene sets (hash), per-module score shift, effect/p
- trust: measured -> ledger hash + whitelist; `module_source=data_derived`
- self-test: label-permutation

### C.7 Augur  (responsiveness ranking)
- method: `augur`
- predicate: perturbation_responsiveness_rank -> ranking_summary only
- registrar: `register_ranking_artifact(artifact_subtype="cell_type_responsiveness", scope, predicate={relation:"perturbation_responsiveness_rank"}, quality={metric:"AUC", ranked_cell_types, per_type_scores, cv_method}, execution_hash)`
- command: `python -m pertura_harvest.augur --adata {ws}/input.h5ad --label-col perturbation_uid --cell-type-col cell_type --out {ws}/outputs/augur.json`
- parse: per-cell-type AUC, ranking, CV method
- trust: ranking != driver; surface says "responsiveness ranking, not driver validation"
- self-test: n/a

### C.8 SoupX/DecontX + Scrublet + CellTypist  (eligibility / state context)
- SoupX/DecontX -> `register_cell_qc(ambient_policy=...)`
- Scrublet -> `register_cell_qc(doublet_policy="scrublet", n_cells_after_qc, ...)`
- CellTypist/Azimuth -> `register_cell_state_reference(annotation_method="celltypist", assignment_column, ...)`
- trust: eligibility/context; not effect evidence.

---

## Part D. Build order (lowest risk first)

```text
0. Trusted execution channel + ledger + ledger-verified is_trusted_execution   (foundation)
1. CellOracle / pySCENIC        prediction-capped: safest first harvest to validate the channel;
                                also demonstrates "prediction cannot launder into measured"
2. Mixscape                     first measured-tier harvest: proves ledger-verified trust works end to end
3. Milo / scCODA                exercises method_internal replicate handling under strict/paper
4. decoupler / cNMF / Augur     cheap batch fill-in across enrichment/module/ranking
5. SCEPTRE                      last: carries the R bridge, occupies the rigor axis
6. SoupX/DecontX + Scrublet + CellTypist   complete the QC/annotation surface
```

Do CellOracle first because prediction tier does not need trust, so it validates
the harvest channel with the least risk; only then move to measured-tier harvests.

---

## Part E. Validation / acceptance per wrapper

Every wrapper must pass three paths before it is considered done:

1. **Positive path.** Complete structured output + UID scope + (measured) trusted
   execution -> intended ceiling under strict/paper.
2. **Negative path.** Missing required fields / wrong claim type / mismatched scope
   -> downgrade to observation with safe wording.
3. **Trust path.** A hand-registered artifact carrying the tool's `method` but with
   an `execution_hash` absent from the ledger -> untrusted -> downgraded under
   strict/paper. (This is the forgery trap; it must have a dedicated test.)

Measured-tier wrappers additionally report a **null self-test table**: run the
wrapper on NTC-vs-NTC and on label-permuted inputs and record the false-positive
rate. This table is both the wrapper's validation and the benchmark's most
convincing evidence that the trusted runners are calibrated.

---

## Part F. What this method closes and delivers

- Closes the measured-tier `execution_hash` forgery seam (ledger-verified trust),
  the same class of hole as the calibration laundering fix.
- Unifies native runner and subprocess harvest under one trust mechanism.
- Keeps the resolver/warrant/renderer untouched: a new tool is one HarvesterSpec +
  one parser + one whitelist entry.
- Produces, at submission time: 8 distinct predicates, all CPU, a few deep native
  runners plus mostly controlled harvests, every one gated by the same door, plus a
  per-runner null false-positive table.

The gate remains the contribution. The wrappers are the gated surface that
demonstrates the gate governs the full Perturb-seq analysis space, and they are the
setting against which the gate's value (capability without trust vs capability with
trust) is measured.
