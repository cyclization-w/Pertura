# A.screen_overview: The screen at a glance

**The biology.** Every target side by side, before any one is followed. Published screens open with the screen as a
whole: the cells per perturbation, each guide's effect on its target gene, how strongly each target moves the profile,
which targets resemble each other, and how each shifts the cells' states (Replogle et al. 2022 relate the
perturbations with a strong effect; in vivo screens draw each perturbation's share of the cell states). In vivo, the
cells per perturbation are a phenotype of their own, of expansion or survival. This module draws these from the first
look's runs, every target in each panel, and adds one measure of its own (Pertura's choice): each target's energy
distance from the control cells, read against groups of control guides drawn to the target's own number of cells,
without a permutation test. `C.any_effect` gives the permutation test when a target is followed.

**The measurement.** Per target: its cells, its units and its cells in each, its guides with 10 cells; its cells per
guide against the control guides' (`B.abundance`); its target gene's change under the guide that moves it furthest the
expected way (down under CRISPRi or a knockout, up under CRISPRa; `B.efficacy`); its energy distance (pertpy's
edistance on the PCA of log-normalised highly variable genes, the receptor gene segments left out, PF4) beside the
95th percentile of the same distance for 20 random groups of control guides, each of a target's number of guides; the
target and its groups drawn to one number of cells, the smallest of its cells, 1,000 and what three groups hold; its
guides' and its units' agreement (`F.guides_agree`, `F.replicates_agree`); its three most similar targets
(`E.similar_targets`); its share of each state minus the control cells' (`C.states_and_composition`), the states of
the user's question first; and `C.genes`' and `D.common_response`'s columns once they have run. `columns.json` says
which columns are filled. The figure also draws, where their runs are given, the cells on the states run's embedding
by state with each state's markers and by condition (FG1, the states), and each target's change of the targets' own
genes where three or more are measured (`C.genes`; P2.6).

## Ways the result can mislead

### a target with few cells reads a larger energy distance from its few cells alone

- **Shows in:** the result table's `effect_line`, drawn from control-guide groups of the target's own cells
- **Read:** PF16, ES1
- **Do:** read each target's distance against its own line, not against one line for all; the cells of each side are
  worth stating.

### a column of a module not run reads as no effect

- **Shows in:** `columns.json`: each column's state, filled or not run
- **Read:** no knowledge yet
- **Do:** a column not run is no evidence either way; run its module where the question needs it.

## Next steps

- `C.any_effect` on the targets that are followed, for the permutation test.
- `C.genes` on the targets past their line.
