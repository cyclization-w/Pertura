# B.efficacy: Each guide's change of its own target gene

**The biology.** Each guide's change of its own target gene, described against a line. Under CRISPRi or CRISPRa it
shows whether and how far a guide moves its target: a target's lack of effect means little where its guides move
nothing, and a guide short of the line can still act (a partial knockdown), which its downstream phenotype and its
target's other guides show. Under knockout the transcript can stay, because it escapes decay or the edit is in frame
(PF7). A knockout guide that leaves it is "transcript unchanged". The perturbation's evidence, its downstream
phenotype and the share of cells that respond (B.escape) are read apart (T3). Under CRISPRi, Pertura's default line
is a fall of 30% or more, the filter of Replogle et al. 2022; other screens use 50%, and the line is a choice
(`threshold`): a description, never a test of whether a guide acts. Published CRISPRa screens call
a significant rise (edgeR) or compare mean UMIs, with no fixed factor. Under CRISPRa the default line is a rise by the
same factor, 1.43 times (Pertura's choice).

**The measurement.** Per guide, the target gene's log2 fold change against the reference cells over all the units
pooled (the units that hold both the guide's cells and reference cells), with each unit's own row beside it; its
detection, and the power to see a halving (a doubling under CRISPRa). A guide is placed by its pooled row, or by the
median over its units where no pooled row can be measured; its change in per cent and where it falls against the
line. A row needs 10 cells of each side by default (`policy`, `min_cells`).

## Ways the result can mislead

### a knockout's mRNA can stay, so its target gene says little

- **Shows in:** `verdicts` (the verdict "transcript unchanged")
- **Read:** G3, PF7
- **Do:** genotyping the edits at the cut site, where the reads cover it, answers it directly. Otherwise the protein
  (CITE-seq antibodies), the phenotype or a downstream signature can stand in, read with `F.guides_agree` and
  `B.escape`.

### a target gene detected in few cells cannot show a change

- **Shows in:** `not_measurable` (power under 0.8 in every row, the units pooled and each unit: Pertura's choice);
  the result table's `power`
- **Read:** ES3, PF15
- **Do:** it is best reported as not measurable; the guides' agreement downstream then carries the evidence.

## Moves it calls for

- **T3:** worth considering when guides do not change their target past the line (Pertura's default line)

## Further reading

PF5, PF6, S3.

## Next steps

- `B.dose` (guides of one target differ), `B.escape` (Mixscape's perturbed and non-perturbed cells), `F.guides_agree`.
- In a pair design, T4 (each member of a pair acts; P6.1).
