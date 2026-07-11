---
name: inspect-perturb-seq-design
description: Inspect and reason about Perturb-seq experimental design, including CRISPRi or CRISPRa modality, controls, guide-to-target mapping, MOI, replicate, donor, batch, state, dose, time, and estimand. Use when design fields are unresolved, a method is blocked, or a comparison must be framed correctly.
---

# Inspect Perturb-seq Design

Establish what was measured, what varies independently, and which comparison the data can support.

## Procedure

1. Read the DatasetContract and the dataset-integrity and design-balance results.
2. Separate observed, inferred, confirmed, and unresolved fields. Do not collapse these states.
3. Identify perturbation modality, guide capture source, negative controls, guide-to-target mapping, and whether guides represent single or combinatorial interventions.
4. Identify the independent experimental unit. Distinguish cells, guides, samples, donors, and biological replicates.
5. Examine condition overlap across replicate, donor, batch, state, dose, and time. Surface complete or partial confounding.
6. State the estimand before selecting an analysis: expression, association, composition, state mapping, module response, target efficacy, or combination effect.
7. Use `inspect_dataset` for identity confirmations and diagnostics for design checks. A confirmation cannot create an effect.

Read [design-facts.md](references/design-facts.md) when resolving design fields. Read [estimands.md](references/estimands.md) when translating a biological question into an analysis objective.

## Output

Summarize:

- confirmed design facts;
- unresolved facts that block or limit analysis;
- independent unit and comparison scope;
- compatible objective or capability family;
- limitations that must appear in the report.
