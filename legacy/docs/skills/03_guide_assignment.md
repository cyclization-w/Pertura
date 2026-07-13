# 03 Guide Assignment Skill

Purpose: capture perturbation-cell mapping evidence as structured eligibility for measured Perturb-seq claims.

Register with `mcp__pertura_evidence__register_guide_assignment_artifact` when local files or generated summaries contain structured assignment information.

Fields to capture:

- assignment method: Cell Ranger guide calling, thresholding, custom UMI/read-count rule, treatment assignment, etc.
- assigned, unassigned, and multi-guide cell counts
- guide distribution and cells per guide when available
- ambient guide handling or filtering rule
- MOI inference
- guide-to-target map hash or other stable structured reference
- target summary: perturbation labels, target genes, guide groups

Claim ceilings:

- Guide assignment is eligibility evidence, not effect evidence.
- Prose like "guide assignment passed" is ignored without structured fields.
- High-MOI designs require an explicit estimand. Naive single-target marginal claims should downgrade/block unless covariates or a combinatorial scope are documented.
