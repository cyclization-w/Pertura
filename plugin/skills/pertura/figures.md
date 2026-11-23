# Figures

A session ends with a series of figures that show the screen's biology as clearly as a good paper does. A figure is
an argument for one claim, not a plot of whatever a module returned.

Adapted from the nature-figure skill of the nature-skills repository (github.com/Yuan1z0825/nature-skills, Apache
License 2.0): its figure contract and its multi-panel evidence architecture. The Perturb-seq patterns are Pertura's,
drawn from published screens (the figure patterns of the skill pertura-knowledge, FG1).

## Before drawing: the contract

Write it in the record with the figure, before any code:
- **The claim:** one sentence with a verb, about the biology ("knocking out most of these TFs lowers the share of
  terminally exhausted cells, and the cells move to different states depending on the TF"). Not "results of C.states".
- **The panels:** each answers one question, in a role of the result's chain (below). Covering a panel must remove a
  step of the argument no other panel gives. Two panels showing the same comparison under two metrics are one panel.
- **The main evidence gets the largest panel.** Controls and checks are quieter but legible.
- **The statistics:** the n (and whether it counts mice, donors, lanes or cells), what the centre and the spread
  are, the test and its correction. They go in the caption, not left implicit.

## A result's main figure: the structure, taken apart

A result explains a structure, so its main figure shows the structure, then takes it apart. Choose the roles the
line reached, and order them to argue best:
- **the map, with the structure marked:** the structure among the screen's other targets, never the named
  targets alone. Examples: the targets alike bracketed on their similarity matrix; the condition-specific targets
  named on a scatter of the two conditions; the regulators on their network; the claim's genes or programmes by
  every target that changes them (`charts.heatmap`, from the genes run's table of every target); every target on
  the two scores the claim sets apart (`charts.two_scores`); every target ranked on its score beside the
  control-guide band (`charts.ranked_targets`). One point per sample or mouse shows where it holds: a smaller panel
  beside it, or Extended Data;
- **what it does:** the genes or programmes the structure moves;
- **where and when it holds:** conditions, time points, cell types, units;
- **the prediction and its test;**
- **where useful, a model.** The last panel may be the bench experiment that would test the prediction.

The proof panels go to the result's Extended Data figure: the control, each guide, each replicate, an independent
check, quality, and where it does not hold.

- **Its title is its conclusion.** A panel's title, where it has one, says what the panel shows in a short sentence
  ("The BAF core's subunits change expression alike"). The roles order the panels; their names are never printed.
- **Readouts named for what they measure,** with the published gene sets behind them.
- **Boxplots as the box and the cells as points;** a change over many targets as a ranked dot or a heatmap centred
  on zero, its key cells marked.
- **Every panel from code and its table; every number of a legend from the panel's data;** legends and text each
  complete on their own.

## Building a result's figure

One figure of lettered panels, never images pasted together (`pertura.question_modules.figure_series`):

```python
from pertura.question_modules import charts, figure_series
rows = [(60, [(1.2, lambda p: charts.heatmap(M, names, names, None, "", "Correlation of two targets' profiles",
                                             marks=marks, fig=p), "The core's subunits change expression alike"),
              (1, draw_genes, "Their shared genes")]),
        (45, [(1, draw_states, "The core's subunits shift the cells' states alike")])]
fig, letters = figure_series.figure(rows)
save_figure(fig, "R1_the_core", data, caption, "overview", strict=True)
```

- A row is its height in mm and its panels. A panel is its share of the row's width, a function that draws into the
  panel, and its title: a chart of `charts` given `fig=panel`, or your own code on `panel.add_subplot()`.
- The figure is 183 mm wide (`width=figures.SINGLE` gives 89 mm), in the shared style, each panel's letter bold at
  8 pt at its top left. The layout checks run when it is saved.
- The Extended Data figure is built the same way and saved with the role `evidence`.

## The series a session produces

1. **The overview figure:** what the screen is and whether it worked. The design with its numbers, the cells and
   states, knockdown per guide against the controls, cells per perturbation and replicate.
2. **One main figure for each result** (each chosen result): the structure and its parts (above), and its Extended
   Data figure with the proofs. FG1 gives the chart for each kind of evidence.
3. **Supplementary figures:** the screen's controls and quality beyond the overview, and the readings that move a
   number.

## Rules every figure keeps

- **Final size from the start:** 89 mm (one column) or 183 mm (two), built as one figure, never pasted from images.
- **Text 5-8 pt,** Arial, editable in the PDF and SVG; panel letters bold, 8 pt, lowercase, top left.
- **The control is always shown,** as its own row, bar or grey layer, in grey in every panel.
- **Absolute values beside differences:** a reader sees the control's level, not only the change.
- **The test, the n and the error bars named in the caption.** Significance marks (asterisks, p-values) may go in
  the plot.
- **One colour per perturbation or state** across all panels; a diverging blue-white-red scale centred on zero for
  changes; no rainbow scales.
- **A key for every colour, mark or band that stands for a group** (`ax.legend`, one handle each), or direct labels
  where the categories sit still. Words that name a colour ("red: interferon genes") never stand in for the key, in
  the plot or in the caption. Its entries are short labels as papers write them ("Control guides", "Up (adj. p <
  0.05)", "Below threshold"). A key never covers data.
- **No verdicts or explanations inside a plot;** the caption carries them.

## Saving a figure

In a notebook cell, `save_figure(fig, name, data, caption, role)` saves the figure as an entry of the record:
- the PNG at 300 dpi, a PDF with editable text, and `data`, the table (or `{name: table}`) the figure plots;
- `caption`: the figure's content, with its statistics;
- `role`: overview (the map that states the result: a result's main figure), detail (what explains it), evidence
  (what supports it, such as its findings' checks: its Extended Data figure) or limitation (where it fails or is
  untested). Leave it out until the figure has a place.

Saving the same name again makes a new version, which needs its own review. A plot shown inline but not saved is not
a figure of the record. Module figures enter the record by themselves when their run ends.

## Checks before a figure enters the record

`save_figure` runs these, and a figure that fails one is not saved:
- no text below 5 pt;
- no text overlapping other text (labels on points: adjustText's `adjust_text`, installed, moves them apart);
- no text cut at the figure's edge;
- the width is 89 or 183 mm;
- the text is editable.

Then look at the figure at its final size, panel by panel, and ask what a sceptical reader would challenge:
- Is n visible?
- Is the control there?
- Could fewer panels make the claim?
- Does every panel answer its question?

Ask these of each result's figure yourself before the report.
