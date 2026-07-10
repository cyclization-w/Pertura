# Product Pivot: Perturbation Reliability Copilot

## Decision

Pertura should be a Perturb-seq reliability and decision product with a thin,
deterministic claim boundary. It should not grow into a second general workflow
engine or encode every analytical method as a bespoke evidence registrar and
predicate.

The primary user question is:

> Is this perturbation effect trustworthy, why or why not, what analysis is
> appropriate for this design, and what is the smallest next experiment or
> check that would change the answer?

## What stays

- Canonical perturbation/contrast identity and scope binding.
- Runtime-owned execution ledger and file hashes.
- Deterministic claim-strength ceilings.
- A small set of policy profiles selected once per run.
- Structured evidence and a controlled final surface.

These pieces are infrastructure. They should remain boring, stable, and small.

## What stops expanding

- One MCP registrar per new scientific noun.
- One handwritten warrant branch per sentence form.
- Stage cards as a substitute for executable diagnostics.
- Benchmarks that only compare gated prose with an intentionally unsafe prompt.
- A second orchestration spine parallel to Claude CodeAct.

New analytical wrappers should normally return a shared `DiagnosticResult`
shape: status, measured metrics, blockers, cautions, recommended actions,
input/output hashes, and a declared scope. The gate consumes that result; it
does not reimplement the analysis.

## Product spine

```text
real data
  -> contract/design inference (all inferred fields remain unconfirmed)
  -> guide assignment and target reliability diagnostics
  -> design-aware method route
  -> trusted analytical runner or free CodeAct analysis
  -> structured diagnostic/effect artifacts
  -> thin deterministic claim gate
  -> target verdict + reasons + next action
```

Claude CodeAct remains the adaptive planner and coding surface. Pertura owns
only four boundaries:

1. read-only inputs and runtime-owned trust files;
2. deterministic runners that may write the canonical execution ledger;
3. run-level policy selection that MCP calls cannot weaken;
4. the final scientific surface.

## First vertical slice now implemented

### Runtime trust spine

- `pertura-claude` defaults to the `strict` profile.
- The selected profile and hash are frozen in `manifest.json`.
- MCP tools no longer accept `policy_profile` as a model-controlled argument.
- The evidence registry, execution ledger, decisions, manifests, and calibrated
  reports are runtime-owned paths.
- The finalizer receives the same policy object as the MCP resolver.
- Pseudobulk and control-calibration executions are ledger-backed.

### Target reliability audit

`run_target_reliability_audit` evaluates one explicit target/control contrast
using a metadata CSV and target-expression CSV. It keeps these signals separate:

- target and control cell coverage;
- target-gene detectability and dropout risk;
- expected CRISPRi/CRISPRa direction;
- per-guide effects and guide concordance;
- batch overlap/imbalance;
- replicate overlap;
- eligibility for downstream measured-effect analysis and for target-engagement
  interpretation.

The result is a structured target verdict (`eligible`, `caution`, or `blocked`)
with reason codes and recommended actions. Its JSON output is bound to a
canonical execution-ledger record.

### Method and virtual-scope router

`route_analysis` makes conservative routes from explicit design facts:

- high-MOI designs route to conditional/SCEPTRE-style association;
- replicated low-MOI designs route to pseudobulk DE;
- unreplicated designs are marked exploratory;
- composition analysis requires a state reference;
- virtual predictions are classified by seen/unseen perturbation, context, and
  combination scope and require anti-collapse baselines and metrics.

The router never upgrades a claim. Missing facts are blockers, not guesses.

## Next product milestones

### P0: prove usefulness on real data

1. Add two small public Perturb-seq datasets with fixed expected diagnostics.
2. Benchmark target verdicts against expert labels: usable, caution, exclude.
3. Measure guide-assignment failure detection, low-detectability warnings,
   guide-disagreement detection, and batch-confounding detection.
4. Replace fixture-only success criteria with task accuracy and time-to-diagnosis.

### P1: finish the QC/diagnostic copilot

1. Read 10x CRISPR feature matrices and guide UMI distributions directly.
2. Diagnose ambient guides, barcode mismatch, reverse complement, and MOI.
3. Add escape/responder estimation and signature-level efficacy adapters for
   Mixscape/Mixscale without claiming those wrappers are ground truth.
4. Produce a per-target reliability table and an interactive failure queue.

### P2: statistical decision support

1. Make pseudobulk, conditional association, composition, and sensitivity
   routes executable behind a shared runner protocol.
2. Require negative-control calibration for confirmatory surfaces.
3. Surface estimand, model formula, replicate unit, contrast, and limitations in
   the user report rather than exposing raw evidence-lattice vocabulary.

### P3: virtual experiment evaluator

1. Treat prediction scope as a contract before model execution.
2. Always run mean and linear baselines.
3. Add perturbation-discriminability, direction, rank, and collapse checks.
4. Recommend the minimum additional observed perturbations or contexts needed
   to make an out-of-scope request evaluable.

## Success metrics

- Experts agree with target eligibility verdicts and reason codes.
- The product catches planted assignment, dropout, guide-disagreement, and
  confounding failures with low false-alarm rates.
- A new user reaches a defensible target-level answer faster than with a
  notebook-only workflow.
- Every strong sentence is reproducible from a runtime-owned execution record.
- The gate and runtime shrink or stay stable while analytical coverage grows.
