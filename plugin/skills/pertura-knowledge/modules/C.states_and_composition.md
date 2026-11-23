# C.states_and_composition: Cell states, and each target's share of them

**The biology.** Cell states found without the perturbation labels (Leiden clusters named by their markers), and each
target's share of cells in each state against the control cells. A target can move cells between states. That shift
is itself an effect of the target, not a confounder. Published screens test a perturbation's share of a state most
often with Fisher's exact test, then the chi-square test, propeller or scCODA. Most test the cells pooled; a few take
the replicates as the units or put the batch in the test. They take all the non-targeting cells as the reference, or
match them per replicate. By default this module's test follows the design: propeller (speckle) on the replicates'
proportions where 3 or more biological replicates (Pertura's default, `min_replicates`) hold the target's and the
control cells, else Fisher's exact test on the cells against the control cells of the target's own samples (Pertura's
choice). A target needs 10 cells by default (`min_cells`).

**The measurement.** Per target and state, the share against the reference by that test, with q-values (a share counts
as changed at q < 0.05 by default, `q`) and the difference per sample. The Cochran-Mantel-Haenszel test, propeller by
sample and scCODA are choices. The control-guide groups go through the same test as calibration.

**Naming evidence.** Each state comes with the sets it fits best, beside its markers. These are the cell types of
MSigDB by default (`naming_sets`; ImmuneSigDB for immune cells' states, one's own lists, or none). They are read as
decoupler's tutorial annotates clusters: each cell is scored on the sets by decoupler's univariate linear model, and
each state's sets are ranked against the other states' cells. The tutorial asks for an expert to review the names;
here the agent names each state from this evidence and its markers.

**Along a trajectory** (`trajectory_root`: a state, or `{column: level}` such as the first time point). Published
screens place perturbations along a differentiation or activation path by pseudotime or diffusion maps. Each cell's
diffusion pseudotime is computed from the root on the states' own graph (scanpy's `tl.diffmap` and `tl.dpt` at their
defaults, as scanpy recommends `tl.dpt` for pseudotime alone). The root cell is the root group's cell nearest its
mean profile. Per target, its cells are read along the pseudotime against the control cells of its own samples: the
shift of the medians, Mann-Whitney on the cells and Benjamini-Hochberg over the targets. Each control guide is read
the same way against the other control cells, and their absolute shifts' 95th percentile is the band. The figure
shows the cells coloured by pseudotime, the densities of the targets that moved most against the control cells', and
every target's shift beside the band. The reading is descriptive: an order of states, not fates. The pseudotime is
kept in `cell_states`, so `C.programs` can test it as a cell value.

## Ways the result can mislead

### one of a target's guides can carry its shift in a state

- **Shows in:** the state shares with each guide alone
- **Read:** PF8, PF13
- **Do:** the shares per guide show it; Zheng et al. 2024 read composition per guide because guides perform
  variably. For a state held by one perturbation, its markers are worth checking for doublets (PF13).

### a change of a gene's mean is a change of states

- **Shows in:** `C.genes` beside these shares
- **Read:** PF11
- **Do:** L1: `C.within_states` tells a shift between states from a change within them.

### the target's cells and the control cells come from different samples, so the shares measure the samples

- **Shows in:** `control_cells` (same_samples by default)
- **Read:** PF14
- **Do:** the alternative is all control cells, as published screens take for composition. The default is
  same_samples (Pertura's choice).

## Moves it calls for

- **L1:** worth considering when target-by-state pairs change

## Further reading

PF12 (technical causes of a shift, guide-UMI depth and cell numbers, are worth ruling out first), PG5 (a cluster's
markers are descriptive), U1 and U2 (the unit of a composition test: most published screens pool the cells, a few
take the replicates).

## Next steps

- L1 (`C.within_states`), L2 (what a state is: its markers, doublets, a reference cell type), L3 (along a trajectory).
- P3.1 and P3.3, the P3 card (cell states first, then which perturbations shift them); P6.7.
