# 01. System Overview

## One-Sentence Positioning

Pertura is not a full Perturb-seq pipeline runner. It is a runtime-owned evidence gate that keeps Claude's CodeAct analysis flexible while making the final scientific surface claim-conditioned, provenance-aware, and policy-versioned.

```text
free Claude CodeAct analysis
  -> runtime-registered evidence artifacts
  -> explicit claims
  -> claim-conditioned resolver
  -> controlled scientific final surface
```

## What Pertura Adds Beyond Free CodeAct

Free CodeAct is useful because it can explore unknown workspaces, inspect files, write Python, run scanpy/pertpy/custom code, and adapt to messy data. The risk is that the same free prose can overstate scientific conclusions.

Pertura adds a runtime layer for the formal conclusion boundary:

- evidence artifacts must be registered through runtime-owned registrars;
- claims must reference artifact IDs, not filenames or prose;
- scope must resolve through canonical manifest UIDs, not raw label similarity;
- measured claims need a validated `EligibilityProfile`;
- evidence class and strength ceilings are owned by runtime validators;
- final scientific text is rendered from `ClaimDecision`, not Claude's draft prose.

## Core Scientific Invariant

User-visible scientific conclusions must not be decided by:

- Claude free prose;
- prompt pressure;
- artifact self-tags such as `validated_mechanism=true`;
- raw guide-label string similarity;
- prose-only statements such as "guide assignment passed".

They must be decided by:

- registered evidence artifacts;
- canonical scope identities;
- structured eligibility and quality fields;
- explicit claims;
- versioned gate policy.

## What It Is Not

Pertura does not currently include real external runners for g:Profiler, Enrichr, Mixscape, Milo, scCODA, trajectory analysis, CellOracle, scGPT, GEARS, or Cell Ranger. Claude may run those tools freely in CodeAct if available, but Pertura only registers the structured evidence outputs and controls what they can support.

It also does not currently provide automatic claim extraction as the main path. Explicit claims are the deterministic benchmark and paper path.