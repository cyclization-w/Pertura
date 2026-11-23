# C.any_effect: Each target's effect on the whole profile

**The biology.** A target's effect on the cells' whole profile. Published screens test it in several ways: Mixscape's
classes of a target's cells, Hotelling's T² on 20 principal components, the number of changed genes against the
controls', an energy-distance permutation test, or a classifier. Replogle et al. 2022 ran both the energy test and the
count of changed genes, and the two calls agreed with a Jaccard index of 78.7%. `B.escape` gives Mixscape's classes
and `C.genes` the changed genes. For the reference, published screens either pool all the control cells or match
them to the target's sample, lane or condition. This module runs the energy distance between a target's cells and the
control cells of its own samples, with a permutation test (Pertura's choice). Each side is drawn to at most 1,000
cells, as the overview's distances are (Pertura's choice, for a screen's test in minutes). The distance grows as the
cells fall (Peidli et al. 2024), so two targets' distances compare at the same number of cells; the overview reads
each against control-guide groups of its own number. A target is tested from 10 cells by default (`policy`,
`min_cells`).

**The measurement.** Per target: the energy distance in the PCA of variable genes (pertpy's DistanceTest), its
permutation p-value, the Benjamini-Hochberg q, the cells and the cells drawn on each side, and the target's UMIs
per cell against the control cells'. The control-guide groups go through the same test, drawn the same way.

**Fewer cells** (`fewer_cells`, such as [25, 50, 100, 200]). Published screens down-sample a perturbation's cells to
see how many its call needs. Each target with more cells than a number is drawn to it, 3 times, and tested again,
its control cells drawn as in the whole test. Benjamini-Hochberg runs over the targets tested at that number. The
result gives, per number, the share of the targets called on every cell that are called again, and the targets
called only with fewer cells.

## Ways the result can mislead

### cell numbers, depth or sick cells move the whole profile

- **Shows in:** the result table's `umis_ratio` per target (no threshold in the sources)
- **Read:** ES1, PF16
- **Do:** T7: `res.match_depth()` on the target's gene or score result checks depth; it does not run the distance
  again. The cells of each side are worth stating. Published screens downsample to equal cell numbers before
  comparing effect sizes, put the genes detected, the mitochondrial share, the depth or the cells per perturbation in
  the test, or check for a signature of dying cells.

### the target's cells and the control cells come from different samples, so the distance measures the samples

- **Shows in:** `control_cells` (same_samples by default)
- **Read:** PF14, U3
- **Do:** same_samples is the default; published screens match the control cells to the sample, lane or condition.
  The alternative is all control cells, which other screens pool.

## Further reading

R1 (the control groups' distances, `calibration`, calibrate the test).

## Next steps

- `C.genes` on the targets with an effect.
- Where most targets differ from the control cells, `D.common_response` (R3, PF9).
