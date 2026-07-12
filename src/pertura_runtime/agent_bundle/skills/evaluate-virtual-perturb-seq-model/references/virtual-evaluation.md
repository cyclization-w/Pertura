# Virtual evaluation reference

## Split scope

A valid split states which perturbations, contexts, combinations, dose/time values, and donors belong to train, validation, and test. Distinguish interpolation from extrapolation. Report results per held-out axis when more than one axis changes.

## Mandatory baselines

- Control mean asks whether the model improves over predicting the control response.
- Context mean asks whether context alone explains the apparent performance.
- Linear/additive asks whether a combination model improves over summing observed single-perturbation effects.

A model that does not beat every applicable baseline is limited, even when a global correlation appears high.

## Metrics

Use signed direction agreement for effect direction. Use row-wise rank correlation for within-perturbation gene ranking and transposed rank for across-perturbation discrimination. Report magnitude error separately from rank. Measure true-match rank or top-one retrieval for discriminability. Compare predicted and observed variance and pairwise distance to detect collapse.

Uncertainty is useful only when tied to the same held-out units. Report empirical interval coverage and width against nominal coverage.

## Failure modes

Block evaluation on confirmed leakage. Mark it limited when training provenance is incomplete, predictions collapse toward one profile, a mandatory baseline wins, identifiers do not align, or uncertainty coverage is materially wrong. A good average score cannot erase these failures.

## Next panel

Rank candidates with explicit uncertainty, information gain, response-program coverage, biological diversity, feasibility, and cost. Keep weights and budget visible. The result is a design hypothesis, not evidence that a candidate perturbation will work.
