# B.dose: How much a target's guides differ in efficacy

**The biology.** A target's guides can differ in efficacy, so pooling them averages over their efficacies (G4, PF8).
Some published screens set a minimum on-target change before a perturbation enters downstream analysis. Others drop
or tier the guides that disagree. They drop them by a filter on the two guides' correlation, as likely off-target, or
rank a target's findings by how many of its guides support them, dropping single-guide support. Guides of graded
efficacy can also show whether the downstream effect scales with the on-target change (C4). By default no guide is
excluded. This module lists each guide's on-target change and the targets whose guides' verdicts differ. By default a
finding's guides check holds the finding where at least two guides go its way and none goes significantly the other
way (Pertura's choice).

**The measurement.** Per guide, its target gene's change and B.efficacy's verdict. Per target and mode, the strongest
and weakest guides and their ratio, with no cut-off. The targets whose guides' verdicts differ.

## Ways the result can mislead

### a target's guides differ in efficacy, so pooling them averages over strong and weak guides

- **Shows in:** `discordant`; the result table's `ratio_strongest_to_weakest` per target
- **Read:** G4, PF8, PF18
- **Do:** reading by guide (`res.by_guide()`) keeps every guide in view; picking the strongest guide alone selects on
  the result (PF18). Where the guides disagree, the finding's guides check holds a finding on at least two guides that
  agree; published screens drop or tier the guides that disagree.

## Next steps

- C4 (whether the downstream effect scales with the on-target change).
