# D.cutting_response: The response to cutting DNA

**The biology.** The response to cutting itself: the cells of control guides that cut where no gene is (intergenic,
safe-targeting) against the non-targeting ones. Yao et al. 2023 use safe-targeting guides "to test for general
effects of genome-targeting guides". In a knockout screen, cells that were cut too are the fairer control for a
knockout (Morgens et al. 2017, in growth screens; PF9). The module also runs under CRISPRi or CRISPRa, where such
guides do not cut. No source was found for a response to a dead Cas9's binding.

**The measurement.** The energy distance and the changed genes between the two kinds of control, within samples.

## Ways the result can mislead

### the two kinds of control sit in different samples

- **Shows in:** `test` (within samples; by replicate where the design has 3 or more, Pertura's default)
- **Read:** PF14, U3
- **Do:** the comparison is best kept within samples.

### a few control guides are clones

- **Shows in:** `guides` (the guides of each kind); `B.clonality`'s top-guide shares of each control group
- **Read:** U5, PF3
- **Do:** reading each control group apart shows whether a few clones carry it.

## Next steps

- For knockouts, the cutting controls as the reference (R5: intergenic guides for the cutting response).
