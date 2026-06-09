# Contributing

Pertura is still an alpha research artifact. Contributions should keep the
public product focused on the Perturb-seq workbench while keeping the audited
runtime small and reusable underneath.

## Development Setup

```bash
python -m pip install -e ".[review]"
python -m pytest
python tests/test_harness.py
python -m pertura.claim_tests --json
```

Optional stacks:

```bash
python -m pip install -e ".[server]"      # FastAPI GUI/API
python -m pip install -e ".[perturbseq]"  # scanpy/anndata stack
python -m pip install -e ".[all]"         # all optional integrations
```

## Contribution Guidelines

- Prefer public APIs in `pertura.__init__`: `AnalysisGraph`, `Capability`, and
  `Domain`.
- Add new scientific behavior as a Perturb-seq capability card, template,
  condition, stage card, or rubric before changing the runtime core.
- Keep the first-screen UX in the built-in HTML workbench and terminal surface;
  React is experimental and should not become the only current UI.
- Keep LLM context bounded; do not feed complete event logs, full notebooks, or
  entire graphs into prompts.
- Keep runtime writes behind `GraphController` and event validation.
- Add tests for new public behavior in the script harness and, when relevant,
  package-safe claim tests.

## Before Opening A PR

Run:

```bash
python -m pytest
python tests/test_harness.py
pertura claims --json
python -m pertura.claim_tests --json
```

If optional dependencies are not installed, run `pertura doctor` and mention
which extras were not available.
