# 05 Target QC and Replication Skill

Purpose: capture target/control QC, control calibration, and replication scope needed to decide whether a measured effect can support a claim.

Register target/control QC with `mcp__pertura_evidence__register_target_qc_artifact`.
Register replication only through `mcp__pertura_evidence__register_replication_artifact`, using already-registered measured artifact IDs and a named replication rule.

Fields to capture for target QC:

- target and control labels
- target/control cell counts
- guides per target and cells per guide
- guide consistency when available
- negative-control calibration and control-pool heterogeneity
- min-cell policy name and observed counts
- batch and donor coverage
- estimand and model covariates for high-MOI conditional claims

Replication rules:

- Replication input must be existing measured artifact IDs, not Claude-written summary prose.
- Guide consistency is a quality predicate unless policy explicitly upgrades it.
- Biological replicate, donor replicate, or independent dataset axes can support `replicated_measured_association` if each measured artifact is individually valid and scope-compatible.

Claim ceilings:

- Passing target QC can help measured DE reach `measured_association`.
- Failed target/control QC downgrades or blocks measured effect claims.
- Replication does not establish validated mechanism by itself.
