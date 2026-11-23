# E.opposite_modes: Activation against inhibition of one gene

**The biology.** CRISPRa and CRISPRi of one gene in one library. Where both act, opposite profiles show the gene's own
dosage effect. A profile that does not oppose can be a threshold or a non-linear effect.

**The measurement.** Per gene, the correlation of the activation and inhibition profiles, against pairs of different
genes. Each profile is read against its unit's controls, pooled only within one kind of capture and weighted by
cells, with the targeted genes left out. A profile's cells are counted where they are pooled, and it needs 30 of
them by default (`policy`, `min_cells_profile`). With more than one kind of capture (the delivery, the batch, the
design's other grouping factor), each kind's correlation stands beside the pooled one. `kinds_disagree` says where
they differ in sign or in passing the null.

## Ways the result can mislead

### activation and inhibition in different batches differ by batch, not by mode

- **Shows in:** `kinds_disagree`; each kind's `correlation_top` beside the pooled one; `across_kinds`, the pairs no
  kind of capture holds both of
- **Read:** U3
- **Do:** each kind's value is worth reading. Where the kinds disagree, the pooled value is not the gene's.

### a mode that does not work gives no profile to oppose

- **Shows in:** `both_directions_act` (`B.efficacy` for both modes)
- **Read:** PF5, PF6
- **Do:** the comparison is worth reading only where both modes move the gene (C3).

## Next steps

- C3.
