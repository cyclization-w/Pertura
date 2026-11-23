# B.clonality: Expanded clones among a guide's cells

**The biology.** A guide's cells can descend from a few transduced cells that expanded. A clone then stands in for a
perturbation's effect, or for the reference. Published screens detect expansion in three ways. They compare each
guide's cells with its frequency in the plasmid library. They cap any one perturbation's cells at twice the median
over perturbations. Or they count clones by a UMI or lineage tag in the vector.

**The measurement.** Per target, control group and unit, the share of its cells whose strongest guide is its most
common one, and the largest share per group. Published screens cut at twice the median cells per perturbation, or at
a knee in the vector's UMIs that separates clones from background. This module measures the share and sets no
cut-off (Pertura's choice).

## Ways the result can mislead

### an expanded clone stands in for a perturbation's effect, or for the reference

- **Shows in:** `largest_top_share`; the result table's `top_share` per group and unit
- **Read:** U5, PF3, PF4
- **Do:** reading each guide apart shows whether one clone carries the effect; a finding's guides check holds it
  where at least two of its guides agree (`F.guides_agree`). Each guide's cells against its frequency in the library
  (`B.abundance`) show expansion. In T cells that are not TCR-transgenic, the receptor genes are best left out of the
  profiles (PF4).

## Next steps

- `F.guides_agree`, `C.states_and_composition` read by guide; P3.4.
