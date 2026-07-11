# Guide and Screen QC

## Barcode and orientation

Check RNA and guide barcode overlap before normalization. Test suffix removal for collisions. Treat forward, reverse-complement, and partial-overlap patterns as candidates until the mapping evidence selects one.

## Assignment

Use the committed posterior matrix when available. Hard assignments are a projection of uncertainty, not the original evidence. Preserve all guide posteriors for high-MOI or combinatorial designs.

## Ambient guide signal

Ambient estimates require raw or empty droplets. Without them, report the missing evidence rather than estimating background from retained cells alone. An ambient profile is diagnostic and does not rewrite the raw guide matrix.

## MOI and doublets

MOI describes perturbation load. Multi-guide describes multiple captured guides. A cell-doublet score describes transcriptomic evidence of more than one cell. Keep these fields and exclusion reasons separate.

## Retention

Downstream capabilities should use the committed retained-cell manifest. Review sample balance after filtering so exclusions do not create a new confounding pattern.
