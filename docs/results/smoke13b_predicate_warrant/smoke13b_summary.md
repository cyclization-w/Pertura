# Smoke 13b: Composition Predicate/Warrant Closure

- Status: `completed`
- Registry: `docs/results/smoke13b_predicate_warrant/artifacts/evidence_artifacts.jsonl`
- Evidence report: `docs/results/smoke13b_predicate_warrant/reports/evidence_report.md`
- Composition artifact: `composition_effect_a48561c33ab5`
- Evidence predicate: `cell_state_composition_shift`
- Intrinsic ceiling: `measured_association`

## Decisions

| claim | decision | max_strength | scope_fit | blocked_requested_strength |
| --- | --- | --- | --- | --- |
| `smoke13b_composition_as_fate_mechanism` | `allowed_with_downgrade` | `measured_association` | `exact` | `causal_fate_conversion` |
| `smoke13b_composition_association` | `allowed` | `measured_association` | `exact` | `none` |

## Checks

- `fate_mechanism_claim_downgraded_to_measured_association`: `true`
- `composition_association_claim_allowed`: `true`
- `surface_mentions_composition_association`: `true`
- `surface_blocks_fate_mechanism`: `true`
- `surface_not_de`: `true`

## Controlled Surfaces

### `smoke13b_composition_as_fate_mechanism`

Registered composition evidence supports a measured cell-state composition association for KLF1. This does not establish a gene-specific effect, target engagement, causal fate conversion, downstream mechanism, or driver validation.

### `smoke13b_composition_association`

Registered composition evidence supports a measured cell-state composition association for KLF1. This does not establish a gene-specific effect, target engagement, causal fate conversion, downstream mechanism, or driver validation.
