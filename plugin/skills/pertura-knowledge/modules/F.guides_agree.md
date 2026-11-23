# F.guides_agree: Agreement between a target's guides

**The biology.** The agreement of a target's guides, beyond what two control guides share. Some published screens
correlate the guides of one gene against pairs of guides of different genes and of control guides. Two independent
guides that agree make an off-target effect unlikely. Some published screens drop or tier the guides that disagree.
They drop them by a filter on the two guides' correlation, as likely off-target, or rank a target's findings by how
many of its guides support them, dropping single-guide support. Pertura keeps every guide in the record. A finding's
guides check holds the finding where at least two guides go its way and none goes significantly the other way, so one
weak guide does not veto it (Pertura's choice).

**The measurement.** Every two guides' profiles correlated, as pairs of one target, of two targets and of two control
guides. Each cell is read against its own sample's control cells, pooled over the samples of one kind of capture,
with the targeted genes left out. A guide needs 10 cells by default (`min_cells`). Per target, its pairs' median
correlation, the share of control pairs as high and the share of pairs of two targets' guides as high. Over the
screen, the pairs of one target's guides against the pairs of two targets' guides (Mann-Whitney), as published screens
compare guides of one gene with guides of different genes.

## Ways the result can mislead

### guides of one target sit in different samples, so their profiles differ by sample

- **Shows in:** each cell against its own sample's control cells before a guide's cells are pooled (as built)
- **Read:** PF14
- **Do:** as built.

### a guide's change of its target gene makes the guides of one target alike

- **Shows in:** the targeted genes left out (as built)
- **Read:** PF6
- **Do:** as built.

### guides with few cells have noisy profiles that agree with nothing

- **Shows in:** `control_pairs`, `min_cells`: control pairs of as few cells as the reference
- **Read:** PF16
- **Do:** low agreement is best read with the cells counted.

### profiles set against the same control cells share that mean's noise, the more the more cells a guide has

- **Shows in:** the pairs' `correlation_halves` beside `correlation`; per target, `median_halves`
- **Read:** PF16, R1
- **Do:** as built: the share of control-guide pairs as high is read on the correlation against halves of the
  control cells (Pertura's choice).

## Moves it calls for

- **T1:** worth considering when fewer than half the targets' guides agree beyond the control-guide pairs (half is
  Pertura's default)

## Further reading

G4, G5, PF18.

## Next steps

- Findings checked guide by guide.
