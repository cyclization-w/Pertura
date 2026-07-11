# Perturb-seq Design Facts

## Perturbation identity

Keep guide barcode, guide sequence, target label, target gene, and perturbation condition separate. A guide-to-target table is a mapping claim, not an observed transcriptional effect. Preserve raw and normalized barcodes so orientation or suffix corrections remain auditable.

## Controls

Distinguish non-targeting guides, safe-targeting guides, untreated cells, mock conditions, and positive controls. Record how control cells were assigned and whether controls share sample, donor, batch, dose, and time with the tested condition.

## Experimental units

Cells provide observations but usually do not replace independent biological units. Guides can measure heterogeneity within a target but are not automatically biological replicates. Determine whether inference is paired within donor or replicate, independent across samples, or confounded.

## Perturbation load

Low-MOI screens can support single-target contrasts when assignment is reliable. High-MOI and combinatorial screens retain the complete guide posterior or combination identity and require conditional association reasoning. Multi-guide status is not the same as a transcriptomic doublet.

## Context

Track cell state, donor, replicate, batch, dose, time, and stimulation as distinct scope dimensions. A comparison is exact only when the committed result and requested statement refer to compatible dimensions.
