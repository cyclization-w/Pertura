---
name: diagnose-perturb-seq-screen
description: Diagnose Perturb-seq guide assignment, screen quality, target efficacy, and response reliability. Use for barcode mismatch, reverse complement, ambient guide signal, multi-guide cells, high MOI, doublets, weak target response, escape cells, or guide disagreement.
---

# Diagnose a Perturb-seq Screen

Trace a screen problem from guide identity through retained cells to target response. Run diagnostics rather than repairing raw data silently.

## Procedure

1. Confirm barcode overlap, suffix behavior, orientation, reverse-complement candidates, and guide-map completeness.
2. Review assignment posteriors and model diagnostics. Preserve ambiguous assignments instead of converting them to hard truth.
3. Check ambient-guide evidence when raw or empty droplets exist. If they do not exist, keep ambient estimation unresolved.
4. Inspect MOI and multi-guide status separately from transcriptomic doublet scores.
5. Review the retained-cell manifest and exclusion reasons before downstream analysis.
6. Assess target detectability, direct direction, guide-level effects, uncertainty, guide disagreement, responder fraction, and escape fraction.
7. Prefer signature-level evidence only when its module is independent of the tested perturbation labels. Record leakage otherwise.
8. Return the runtime reason codes and failure queue. Do not turn a diagnostic pass into a general certification.

Read [guide-qc.md](references/guide-qc.md) for assignment failure modes. Read [target-reliability.md](references/target-reliability.md) for efficacy and responder interpretation.
