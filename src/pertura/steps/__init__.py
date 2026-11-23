"""Steps (docs/adr/0031): each recurring computation of the analyses, written once. A step has one interface and
one plumbing (the cells and the reference by the design, the minimum cells, the rows in one shape, the package
calls) and, where the field has several standard implementations, several backends. The modules, the notebook's
blocks and the checks run when a finding is recorded all call the steps."""
