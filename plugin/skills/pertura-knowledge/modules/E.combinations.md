# E.combinations: Each pair against its singles

**The biology.** Two perturbations together against each alone. The pair's profile is fitted as a weighted sum of its
singles' (Norman et al. 2019's regression, with R², the coefficients' magnitude and their asymmetry), or set against
their plain sum; what is left is the interaction. Norman 2019, Wessels 2023, Otto 2023, Dixit 2016, Hou 2022 and
Santinha 2023 read pairs this way, by Norman's fit or by interaction terms, and report the coefficients and the fit.
Otto et al. 2023 found its cells too few to tell additivity from synergy. Yao et al. 2023 found no second-order term
even at q < 0.5. None of these papers asks a departure to reproduce between units. The module's default is Norman's
weighted sum (`model` fitted_weights); plain_sum and regulatory_modes are the other choices.

**The measurement.** The testable pairs: the pair and both singles with `min_cells` cells (20 by default, a choice),
pooled over the units that hold the pair, each cell against its own unit's control cells. Per pair: its cells and its
singles', whether each member acts in the pair's cells, the weights, R² and the size of the departure. An added check
runs where 2 or more units hold the pair and both singles with `min_cells` cells each. It tests whether the departure
agrees between those units, against control pseudo-pairs drawn at the pair's cell numbers. Only then is a pair called
beyond them; without the check, the result is a measurement, with its fit and cells stated (Pertura's choice).

## Ways the result can mislead

### a pair without its singles in the screen cannot be compared with them

- **Shows in:** `testable`, `lacking_a_single`
- **Read:** PF14, U2
- **Do:** only the testable pairs can be read; how many were not testable is worth saying.

### a member that does not act in the pair's cells makes the pair a single

- **Shows in:** `members_not_acting`; the result table's `members_act` per pair
- **Read:** PF5
- **Do:** T4: the pairs whose members both act, past B.efficacy's line, are the ones read as interactions
  (Pertura's choice).

### a departure can be noise, more so with fewer cells

- **Shows in:** the result table's `cells` (and each single's cells beside it); where 2 or more units hold the pair,
  `beyond_the_control_pseudo_pairs` and `control_pseudo_pairs_as_high` per pair
- **Read:** R1, R2, PF16
- **Do:** each departure is best stated with its cells, and read against its pseudo-pairs where the units allow.

## Moves it calls for

- **T4:** worth considering when testable pairs have a member that does not act

## Next steps

- P6.1, P6.5, P6.6; P6.2 (where each pair sits between its singles), P6.7 (states), P6.13 (outside knowledge on why).
