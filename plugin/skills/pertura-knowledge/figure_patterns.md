# Figure patterns from published screens

How published single-cell CRISPR screens show each kind of evidence, from the figures of five papers and the charts
other published screens use most. Each row is a starting point for the claim at hand, to adapt where another chart
shows the evidence more plainly. The chart functions named are in `pertura.question_modules.figures`.

Sources:
- Replogle et al., Cell 2022 (genome-scale Perturb-seq);
- Papalexi et al., Nat Genet 2021 (Mixscape);
- Santinha et al., Nature 2023 (in vivo AAV-Perturb-seq);
- Chung et al., Nature 2026 (in vivo TF screen in T cells);
- Datlinger et al., Nat Methods 2017 (CROP-seq);
- Pertura's review of the full texts of published screens, for the charts they use most.

No study of Pertura's evaluation test set is a source.

## FG1. The chart for each kind of evidence

| Evidence (question) | The chart in published screens | Shown alongside |
|---|---|---|
| The design (A) | a schematic: library, cells, delivery, model, readout, with the numbers | cells, perturbations, guides per target, replicates |
| Guides and knockdown (B) | per target, the target's own mRNA against the controls as a box, histogram or ridge plot (Replogle et al. 2022, Liu et al. 2024, Meyers et al. 2024, Schnitzler et al. 2024); or dots per guide, or the cumulative distribution over all perturbations | the control as a row; each guide of a target side by side |
| Cells per perturbation (B) | bars or dots per perturbation | the minimum used for a test, as a line |
| Which perturbations act (C) | DE genes per perturbation (and per cell type), dot size or bar | the threshold line; significance by a test |
| The states (C) | the embedding coloured by state, labelled on the map; the states' marker genes as a dot plot | cell numbers per state; confounders (replicate, cell cycle) on the same map |
| Where a perturbation's cells go (C) | each perturbation's cells as a density on the embedding, the controls first, all cells in grey behind (`density_maps`); the commonest composition chart | the same map and scale in every small panel |
| Composition (C) | per perturbation, bars of the state shares, the control row on top, sorted by the main state (`composition_bars`); or a perturbation-by-state heatmap of signed significance | the cells per perturbation; the replicates where the design has them; the test |
| Effects on genes or programs (C, D) | perturbation × gene or program heatmap, clustered, diverging scale centred on zero, annotation bars for perturbation and cell type | the scale's units; the clustering said |
| Similar perturbations (E) | perturbation-perturbation correlation heatmap; an embedding of perturbations labelled by complex | validation against known complexes (pairs in CORUM against all pairs) |
| A score against the control (C, G) | violins or ridges per perturbation, the control in grey | the cells per group; the score's genes |
| Replicates agree (F) | replicate against replicate, the targets' effects as a scatter with its r, once for the screen (Replogle et al. 2022: day 6 against day 8); each animal as a dot (`effects_with_replicates`) only in arrayed follow-ups | the r and the targets it is over |
| An independent check (F, H) | the screen against an arrayed or flow experiment (correlation heatmap, or bars with the control); where none exists, the proposed experiment | the same perturbations in both |
| Meaning (G) | a perturbation-by-gene-set bubble plot, the commonest; or the heatmap's gene groups with their top terms and example genes beside them | the terms' source, as prior knowledge |

By default the control is grey in every panel, and each perturbation or state keeps its colour through the figure
and the series (Pertura's choice).
