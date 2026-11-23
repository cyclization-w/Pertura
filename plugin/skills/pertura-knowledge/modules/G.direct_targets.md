# G.direct_targets: A factor's known targets, as its perturbation moves them

**The biology.** For a perturbed transcription factor, the way its known targets (CollecTRI's regulons) move, against
what its regulon and the perturbation predict. This is support for a direct effect, never proof. This module uses
decoupler and CollecTRI (Pertura's choice).

**The measurement.** Per factor, its activity from its targets' changes (decoupler), its rank among all factors, and
the share of its changed genes that are its known targets.

## Ways the result can mislead

### a regulon is curated from other cells and conditions; a factor can act here through other genes

- **Shows in:** `tag` (prior knowledge)
- **Read:** no knowledge yet (a card on regulons' provenance)
- **Do:** activity and share are read as support, never as proof.

### an activity falls only where the factor's regulon is in use in these cells

- **Shows in:** the result table's `activity`, beside the factor's own mRNA (`B.efficacy`)
- **Read:** PF6
- **Do:** best read with the on-target change.

## Next steps

- X2, X4.
