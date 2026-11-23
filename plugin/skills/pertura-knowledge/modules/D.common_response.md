# D.common_response: A target's effect beyond the response all perturbations share

**The biology.** The part of a target's effect that remains once the response every perturbation shares is set aside.
The shared response can come from Cas9's cuts, with the damage response and selection they set off, from a stress
response, or from a cell-cycle arrest (PF9). Some published screens name or test a response that many perturbations
share, and some take all other cells as the reference. Systema (Viñas Torné et al. 2025), a methods paper, moves the
reference to the average of the perturbed cells. A common response is not noise: it can be biology to record. This
module tests each target against the other targets' cells, which hold the shared part (Pertura's choice).

**The measurement.** Per target, the energy distance from the other targets' cells (by default drawn to 3 times its
cells, at least 500; `reference_cells` all takes every one), with its permutation test, beside `C.any_effect` against
the control cells.

## Ways the result can mislead

### a response most perturbations share makes each target differ from the control cells

- **Shows in:** `targets_with_an_effect` against the other targets' cells; the result table's `q`
- **Read:** R3, PF9, PF10
- **Do:** each claim can be narrowed to what differs from the other targets, with the common response recorded as a
  result of its own.

### with few targets, the other targets' cells are mostly one or two perturbations

- **Shows in:** the result table's `reference_cells` per target
- **Read:** R5, PF10
- **Do:** with few targets it is worth reading with care; explicit control guides stay the main reference.

### control guides that skip a step every targeting guide takes (cutting)

- **Shows in:** whether the library has cutting controls (intergenic, safe-targeting) beside non-targeting ones (the
  design's controls)
- **Read:** R5, PF9
- **Do:** `D.cutting_response` measures it.

## Next steps

- `D.cutting_response`; the claims narrowed.
