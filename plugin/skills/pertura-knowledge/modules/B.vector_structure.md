# B.vector_structure: Guides that travel on one vector

**The biology.** Some libraries put a target's guides on one vector (dual-guide vectors, arrays). In these libraries,
two guides in a cell are the design, not a doublet. Removing cells with two guides would remove the vector's cells.

**The measurement.** Per target and unit, the share of its single-target cells that carry two or more of its guides.

## Ways the result can mislead

### removing cells with two guides would remove a two-guide vector's cells

- **Shows in:** `share_overall`; per target, the result table's `share` (high means two guides per vector)
- **Read:** G2
- **Do:** confirming the vectors in the design (`perturbation.vectors`) lets cells and abundance be counted per
  vector.

## Next steps

- `B.abundance` per construct.
