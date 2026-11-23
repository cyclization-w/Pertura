# C.within_states: Changes within a state, beside shifts between states

**The biology.** It tells a target that moves cells between states from one that changes cells within a state. It
tests the genes changed within each state, the target's cells against the control cells in it, beside the shift in
the target's share. Xia et al. 2023 and Belk et al. 2022 test it this way.

**The measurement.** Per target and state, the changed genes by the design's test, and the cells of each. A target
and state are tested from 10 cells of each side by default (`min_cells`, a choice).

## Ways the result can mislead

### few of a target's cells in a state give no power to see a change within it

- **Shows in:** the result table's `cells` and `tested` per target and state
- **Read:** ES1, PF16
- **Do:** the counts are worth reporting. With few cells, no change reads as "not enough cells", not as "no
  change".

### a state's own markers differ little between target and control cells, so a change within the state can be a finer state

- **Shows in:** the result table's `genes_changed_within`, against the state's markers
- **Read:** PG5
- **Do:** comparing with the markers helps; a finer clustering is a choice worth trying.

## Next steps

- L2 (what a state is); P3.5.
