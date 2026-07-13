# 07. Runtime Surfaces

## Scientific Surface

The scientific conclusion surface is the runtime-rendered evidence report:

```text
reports/evidence_report.md
```

It is generated from `ClaimDecision.allowed_surface`, not Claude free prose.

## Audit Surfaces

Claude draft final text and intermediate notes are audit/debug material. They can be retained for transparency, but they are not the official scientific conclusion surface.

The final summary should not expose `logs/claude_final.md` as a scientific report path.

## Strong Baseline Definition

P0.7 uses a strong baseline for evaluation:

```text
gated and baseline share the same registry, claims, and policy snapshot.
Only final surface generation differs:
  gated -> ClaimDecision renderer
  baseline -> free-prose surface
```

This makes gate the comparison variable.

## Surface Evaluator

The P0.7 surface evaluator is benchmark-only. It is not part of the runtime gate.

It flags surface over-claiming such as:

- mechanism / causal / validation language unsupported by decisions;
- prediction or curated prior written as measured/observed/found;
- artifact self-tags repeated as truth.

The evaluator uses lexical checks as a measurement tool. The actual gate decisions are deterministic functions of registry, scope, eligibility, claim, and policy.

## ask_user Policy

For benchmark mode:

```text
ask_user disabled for gated and baseline paths
```

Design ambiguity is not interactively resolved. Gated path downgrades/blocks unresolved assumptions; baseline path may guess but receives no user answer.

Future interactive mode should trigger ask_user during design intake/planning, not at claim-time after analysis has already run.