# B.abundance: Each target's cells against the control guides'

**The biology.** Each target's cell count against the control guides'. With the library's representation (plasmid or
early counts), the count reads as fitness or selection. Without it, cell numbers are not fitness. Some published
screens compare a guide's cells with the plasmid library or with an early time point, and test guide abundance with
MAGeCK. A guide's cells can also be a few expanded clones (PF3).

**The measurement.** Per target: the cells of each guide and their median; the control guides' median and range; the
log2 ratio; the share of control guides with as many cells; and whether the target is above every control guide. With
a comparison, MAGeCK's test. The module also gives the largest share of a target's cells held by one guide in one
sample (Pertura's choice).

## Ways the result can mislead

### an expanded clone makes its target look abundant

- **Shows in:** the result table's `median_cells_per_guide` and `cells_per_guide` per target, against the library's
  counts where they were given (MAGeCK)
- **Read:** U5, PF3
- **Do:** published screens compare each guide's cells with its frequency in the library and read the excess as
  expansion, or cap any target's cells at twice the median over targets; either fits. `B.clonality`'s top-guide
  shares are worth reading beside them (Pertura's choice).

### control guides that are themselves a few clones make a poor yardstick

- **Shows in:** `control_guides` and `control_median_cells_per_guide`: the control guides' range beside their median
- **Read:** U5, PF3, R5
- **Do:** `B.clonality`'s top-guide shares of the control groups show whether the yardstick is a few clones.

### without the library's representation, cell numbers are not fitness

- **Shows in:** whether the library's counts were given (not in the result yet)
- **Read:** no knowledge yet (a card on reading cell numbers as fitness)
- **Do:** the plasmid or early counts, where the user has them, let MAGeCK read the numbers against the library.

## Next steps

- `B.clonality`; MAGeCK with the library's counts.
- Checks on the cell numbers: a minimum of cells per guide or target, with the targets it removes (published screens
  use 5 to 400 cells; Pertura's default is 10, changed with `policy`); downsampling to equal cell numbers (T6).
