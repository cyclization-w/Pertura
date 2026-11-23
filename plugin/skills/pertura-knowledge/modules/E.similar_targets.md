# E.similar_targets: Targets with similar profiles

**The biology.** Targets with similar profiles. Perturbations of one complex or pathway look alike, so an unknown gene
can be placed by the known ones it resembles. Published screens correlate the perturbations' mean profiles, usually
by Pearson's correlation (Replogle et al. 2022: "the correlation between mean expression profiles as a
scale-invariant metric of similarity"). Replogle 2022, Adamson 2016, Norman 2019 with Wessels 2023, and Akana 2026
correlate profiles z-normalised to the control cells. Replogle 2022 and Zhu 2026 relate only the perturbations with a
strong effect. Replogle 2022, Zhu 2026 and Norman 2019 test the clusters against known complexes. This module reads
each pair against pairs of control guides (Pertura's choice).

**The measurement.** The correlation of every two targets' profiles over well-expressed genes, with the targeted genes
left out and each cell against its own unit's control cells. By default the profiles are pooled over the units of
one kind of capture, and a target needs `min_cells` cells in all (30 by default, Pertura's `min_cells_profile`).
With `profile` by_sample, they are correlated within each unit and averaged over the units holding both; a target
needs `min_cells` cells in a unit. Each pair is read against control-guide pairs, and each target's most similar
targets are listed. With more than one kind of capture, each kind's correlation and reference stand beside them, with
`kinds_disagree`.

Pairs with opposite effects stand beside the pairs alike: a pair is opposite when it correlates as low as at most 5%
of the control-guide pairs. Given a genes run (`de`), each pair past a line lists the genes both targets change: the
same way for a pair alike, opposite ways for an opposite pair. This is why two targets look alike, read in their genes.
The pairs whose two target genes share a GO cellular component (a known complex) are set against the other pairs, as
published screens check their groups against known complexes. This check is prior knowledge.

## Ways the result can mislead

### a response many targets share makes them look alike

- **Shows in:** `D.common_response`: which targets still differ from the other targets' cells
- **Read:** R3, PF9
- **Do:** published screens relate the targets with a strong effect against the control cells. Reading similarity
  among the targets that still differ from the other targets is an option (Pertura's choice).

### a target's change of its target gene makes it look like targets that change that gene

- **Shows in:** the targeted genes are left out (Replogle et al. 2022)
- **Read:** G5
- **Do:** as built.

### targets of few cells have noisy profiles that correlate by chance

- **Shows in:** `control_pairs_95th_percentile`; the result table's `control_pairs_as_high` and `units`
- **Read:** PF16
- **Do:** each pair is best read against the control-guide pairs, with each target's cells stated.

### profiles set against the same control cells share that mean's noise, the more the more cells a target has

- **Shows in:** the result table's `correlation_halves` beside `correlation`
- **Read:** PF16, R1
- **Do:** as built: the share of control-guide pairs as high is read on the correlation against halves of the
  control cells (Pertura's choice).

## Further reading

G5 (guides of different targets that look alike: a shared seed or off-target before biology).

## Next steps

- O5 (why two look alike: their shared genes, whether one changes the other's target gene), O6.
- X3 and X4 (known complexes as prior knowledge); P6.2.
