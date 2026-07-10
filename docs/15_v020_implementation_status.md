# v0.2.0 capability-first implementation status

This file records the 0.2.0a3 code checkpoint. It must not be represented as
the final 0.2.0 scientific release.

## Code checkpoint

- The v0.2 core schemas, five MCP tools, PromotionPolicy, receipt payload and
  ScopeKey semantics remain frozen.
- Trusted capability receipts use a broker-lifetime in-memory Ed25519 key.
  Receipts record controlled-runtime provenance; they are not an adversarial
  same-user sandbox claim.
- Validator-passed exploratory results are committed as
  validated_untrusted, carry no receipt and cannot support strong measured
  statements.
- Twenty granular 0.1.0 candidate capabilities cover P0-P3 intake/design,
  guide assignment/QC, state/module reference, target reliability, high-MOI
  SCEPTRE, Propeller composition, sensitivity and null calibration.
- Existing composite capabilities remain deprecated compatibility wrappers.
- pertura_bench now exposes a 20-capability coverage matrix, six local
  protocol cases per candidate and a scheduler-neutral server plan.
- SCEPTRE, Propeller and Mixscape adapters are implemented but their optional
  environments and real-data integrations are intentionally not run locally.

Expected audit state:

    build_version: 0.2.0a3
    code_ready: true
    local_fixture_ready: true
    real_benchmark_ready: false
    release_ready: false
    default Pertura domain tools: 5
    bundled capability specs: 29
    new exploratory candidate specs: 20

## Deliberately blocking release

Run:

    pertura release-check --repo .
    python -m pertura_bench capabilities matrix
    python -m pertura_bench export-server-plan --output server-plan.json

Final 0.2.0 remains blocked until:

1. Replogle, Papalexi, Norman and Kang locks/subsets exist and all mapped
   full-data capability jobs have portable verdicts.
2. Candidate scientific adapters pass their method-specific real-data and null
   calibration thresholds.
3. crispri_screen_v1 and crispra_screen_v1 reference independent
   expert-adjudicated calibration/evaluation sets and pass the production
   reliability metrics.
4. Optional execution environments used for release verdicts have frozen
   package/build manifests.

Synthetic fixtures, published proxy labels, copied hashes or a YAML
validated flag cannot remove these blockers.
