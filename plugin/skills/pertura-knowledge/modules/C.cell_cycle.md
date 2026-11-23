# C.cell_cycle: Each target's cell-cycle phases

**The biology.** Each cell's phase from the S and G2/M genes (Tirosh et al. 2016), and each target's phase shares. A
target that stops or speeds cycling moves every proliferation gene and program, so it reads as a broad effect.

**The measurement.** Per cell, the scores and the phase. Per target and phase, the share against the control cells by
the design's test by default, as in `C.states_and_composition`: propeller on the replicates' proportions where 3 or
more biological replicates hold both sides (Pertura's default, `min_replicates`), else Fisher's exact test on the
cells against the control cells of the same samples. A named `test` (fisher, cmh, propeller, sccoda) runs in its
place. Then q-values, and the control groups' 95th percentiles.

## Ways the result can mislead

### a target that stops or speeds cycling moves every proliferation-linked gene and program

- **Shows in:** `pairs_changed` (the targets whose phase shares change)
- **Read:** U4, PF11
- **Do:** whether the cycling is upstream or downstream of the perturbation (U4) is the first question, since
  adjusting for a downstream effect removes it. The gene test can then take the cell-cycle scores as latent variables
  (`C.genes` params cell_cycle). Published screens often score the cycle and regress it out.

### cell-cycle genes respond to stress or differentiation too

- **Shows in:** the phase shares (the table `phase_shares`), read with the cells' states
- **Read:** U4, PF11
- **Do:** best read beside `C.states_and_composition`.

## Next steps

- T7 (not state), L1.
