# B.guide_calls_and_their_evidence: The evidence behind each cell's guide call

**The biology.** The evidence for each cell's guide call. A wrong call puts unperturbed cells among a target's cells
and dilutes its effect. Calling rules trade purity against coverage (G1). A call on one or two guide UMIs may be stray
guide RNA in the droplet. A sample with much ambient guide RNA has many ambiguous cells.

**The measurement.** Per unit and kind of label, the share of cells. For single-target cells, the median guide UMIs
and the share on 2 UMIs or fewer. Cells per guide and unit.

## Ways the result can mislead

### calls on one or two UMIs may be stray guide RNA

- **Shows in:** `share_on_2_umis_or_fewer_by_unit` (no threshold in the sources)
- **Read:** G1, G2
- **Do:** a finding's deposit-calls check shows whether the calls carry it. A stricter rule can be kept as a cell set
  (`pt.save_cells`), named in the findings that use it.

### one sample's ambient guide RNA can make many of its cells ambiguous

- **Shows in:** the result table's `share` of each `kind` per `unit`
- **Read:** G2, U3
- **Do:** the sample's share of ambiguous cells is worth stating. Whether its cells are kept is the user's to
  confirm, as a calling rule.

## Next steps

- `B.efficacy` (whether the called guides act), `B.abundance`.
