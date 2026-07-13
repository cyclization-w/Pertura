# 04. Scope and Eligibility

## Canonical Scope Principle

Raw Perturb-seq metadata often starts as strings:

```text
KLF1_NegCtrl0__KLF1_NegCtrl0
NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0
CEBPE_RUNX1T1__CEBPE_RUNX1T1
```

Pertura allows parsing in an adapter/canonicalization layer, but resolver decisions must use canonical UIDs from a `PerturbationDesignManifest`.

The resolver must not upgrade evidence based on:

- substring matching;
- token overlap;
- basename or path similarity;
- raw label similarity;
- Claude prose.

Raw-string fallback can produce `unknown`, `mismatch`, or at most `observation`; it cannot produce `exact`, `compatible`, or measured strengths.

## PerturbationDesignManifest

The manifest is the identity authority for perturbation scope. It maps raw labels to canonical identities and records:

- source column;
- raw value;
- canonical UID;
- parse rule;
- adapter name and version;
- confidence;
- provenance level.

Supported identity classes include:

- target-gene perturbation;
- guide-level identity;
- combinatorial perturbation;
- control pool;
- compound or treatment condition.

## Compatibility Rules

- KLF1 single-perturbation claim can match a KLF1 single UID.
- DUSP9 claim cannot match a KLF1 artifact.
- CEBPE single-gene claim cannot match CEBPE_RUNX1T1 combinatorial evidence.
- CEBPE_RUNX1T1 combinatorial claim can match the same combination UID.
- Chemical treatment identity cannot directly support molecular target mechanism unless future validated mechanism artifacts enable it.

## EligibilityProfile

Measured effect claims require more than a measured artifact file. They require a runtime-computable eligibility profile.

```text
measured_effect_artifact
+ validated EligibilityProfile
+ compatible canonical scope
+ passing quality predicates
```

Eligibility can come from:

- experiment design artifact;
- guide assignment artifact;
- target QC artifact;
- cell QC artifact;
- structured inline eligibility on the measured artifact.

It cannot come from prose-only statements.

## Important Eligibility Fields

- perturbation-cell mapping;
- control definition;
- target and control cell counts;
- guide counts and cells per guide;
- guide-to-target map hash;
- MOI compatibility;
- estimand;
- control calibration;
- replicate scope;
- cell QC status and structured QC counts.

## Policy-Controlled Eligibility

The policy controls thresholds and requirements. For example:

- minimum target/control cells;
- whether cell QC is required for measured claims;
- whether failed cell QC blocks measured strength;
- high-MOI estimand handling.

Policy changes must change `policy_hash`.