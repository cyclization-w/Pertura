# A.what_was_done: The cells the experiment holds, by unit and kind

**The biology.** This module counts the cells of the experiment per unit (capture, mouse, donor): cells with one
target, with two or more, with a control guide, with an ambiguous call, and with no call. Every later step rests on
these counts. They show which comparisons the screen can make, and in which units. Published screens set a minimum of
5 to 400 cells per guide or target. Pertura's default counts a target as testable from 10 cells; a run changes it
with `policy` (`min_cells`), and a target the run names is tested wherever a test can be computed, with a note.

**The measurement.** Counts from the screen object, per unit and kind of cell, and the screen object's flags. Per
capture, the cells' UMIs, genes detected and mitochondrial and ribosomal shares. The cells that fail the quality rule
the user confirmed in the design (`data.qc`) are counted as failed QC, by reason, and every later module leaves them
out.

## Ways the result can mislead

### the units are lanes of one pool, not animals or donors

- **Shows in:** `independent_units` (the confirmed design's biological replicate)
- **Read:** U1, U2, PF1, PF2
- **Do:** the n is then technical, and is best reported as such. The design's test runs on cells with the sample as
  a covariate, and pseudobulk by sample is a check.

### a call rule can dilute some samples' groups more than others

- **Shows in:** the result table's `ambiguous or several` and `no guide` cells per `unit`
- **Read:** G1, G2
- **Do:** where samples differ in their shares of ambiguous cells or cells without a guide,
  `B.guide_calls_and_their_evidence` shows the evidence behind the calls. Comparing the deposit's calls with another
  rule shows what the rule moves.

## Next steps

- `B.guide_calls_and_their_evidence`, `B.abundance`.
- Where the design delivers pairs, `E.combinations` (P6.1); where it has conditions, `E.condition_x_perturbation`
  (C1).
