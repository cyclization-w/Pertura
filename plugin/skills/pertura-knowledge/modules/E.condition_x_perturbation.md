# E.condition_x_perturbation: Effects that differ between conditions

**The biology.** A perturbation's effect compared between conditions (a stimulus, a time point, a tissue, a cell
type). Each effect is read against the control cells of its own sample in the same condition. Liu et al. 2024 runs it
against the same condition's control cells and against the untreated ones. Most published screens test cells.

**The measurement.** Per target and program, the change of the target's effect on the program's score from one
condition to the other. By default the test is the design's, as `C.genes` chooses it (Pertura's choice). Where the
design names biological replicates and 3 or more of them hold 10 of the target's cells and 10 control cells in each
condition, each unit's difference from its control cells is tested over the units: paired where each unit holds both
conditions, Welch otherwise, after Squair et al. 2021. Elsewhere it is one test on cells: least squares of the score
with each sample in each condition its own baseline, the target, and the target in the second condition, whose
coefficient is the change. The cells, control cells and samples of each condition are stated. A change the layout
cannot estimate is left out and counted. Benjamini-Hochberg runs over all the target x program tests. The table
`by_unit` gives, per target, program and unit, the score's difference from that unit's control cells, for the figure.
A named `test`, cells or units, makes one test for every target.

**Other readings** (`what`):
- `genes`: the named genes' log-normalised expression in place of the scores, by the same test;
- `controls`: the control cells' own response, read before the targets as published screens describe it. It gives each
  gene's change from one condition to the other (scanpy's Wilcoxon on cells), and each protein's where the screen
  measured them (CLR per cell);
- `shares`: each target's share of each state in each condition (`cell_states` from a C.states_and_composition run),
  by Fisher's exact test against the control cells of its own samples there. The conditions are set side by side, and
  the states a target moves in one condition only stand out. This reading is descriptive between the conditions.

Any analysis tool also runs once per condition, tissue or cell type with `by`, each apart and gathered.

## Ways the result can mislead

### a condition's own shift moves every target alike

- **Shows in:** each effect is against the control cells of its own sample in the same condition (the table
  `by_unit`)
- **Read:** PF14, R5, U6
- **Do:** as built; the result says which control cells each condition uses (C1).

### cells as the observations take a difference between samples for an effect

- **Shows in:** the cells and samples of each condition in the table; `test` in the result; the targets with one
  sample in a condition, named in the result
- **Read:** U1, PF2, V4
- **Do:** where 3 or more replicates hold each condition, the units are the replicates, as built. On cells, a target
  with one sample in a condition has a change that is also a difference between samples, which the finding says.

### a layout where the change cannot be told from the samples' baselines gives a number anyway

- **Shows in:** `not_estimable` in the result: the targets whose change was not tested
- **Read:** U3, PF14
- **Do:** as built: the change is tested only where a sample in each condition holds both the target's cells and
  control cells.

## Next steps

- C1; P6.12.
