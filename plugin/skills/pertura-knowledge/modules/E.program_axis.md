# E.program_axis: Two programs the regulators move together or apart

**The biology.** Two programs that the regulators move in opposite (or the same) directions: an axis of the cell's
regulation.

**The measurement.** For every two programs, the correlation of the targets' effects, its q-value, and the targets
counted.

## Ways the result can mislead

### a few strong targets make a correlation alone

- **Shows in:** the result table's `targets`; the scatter
- **Read:** PF16, V2
- **Do:** the scatter shows it. The correlation is a nomination, to be tested by perturbation.

### programs sharing genes correlate by construction

- **Shows in:** the result table's `shared_top_genes`
- **Read:** PG3, PG2
- **Do:** exclusive gene sets, labelled as such, take the shared genes out.

## Next steps

- P1.6, P1.7 (rank the regulators on the axis).
