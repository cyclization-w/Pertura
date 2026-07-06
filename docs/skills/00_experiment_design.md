# 00 Experiment Design Skill

Purpose: help the CodeAct agent identify Perturb-seq design facts that define claim scope and eligibility, without turning those facts into scientific effect claims.

Register with `mcp__pertura_evidence__register_experiment_design_artifact` when local files or generated summaries contain structured design evidence.

Fields to capture:

- assay modality: guide-based Perturb-seq, chemical perturbation, or other treatment assignment
- perturbation modality: CRISPRa, CRISPRi, CRISPR-KO, chemical, combinatorial, etc.
- guide capture / treatment assignment availability
- MOI or guide multiplicity assumption
- negative controls and positive controls when locally observed
- replicate axis: donor, batch, biological replicate, guide-level only, independent dataset
- loading/doublet policy and timepoint when available

Claim ceilings:

- Design artifacts define `scope_definition` and `analysis_eligibility` only.
- They do not support measured effects by themselves.
- Missing negative controls block target-vs-control measured association.
- Positive control absence blocks screen-level assay-validation claims, but not target-level measured association.
