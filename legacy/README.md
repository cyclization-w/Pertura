# Pertura legacy regression archive

This directory contains the retired registrar/evidence lattice, stage workflow,
classic recipes, their historical prompts, and regression tests. It is not part
of the Pertura wheel or sdist and must never be added to the product runtime
`PYTHONPATH`.

The archive is executable only in the dedicated compatibility lane:

```bash
PYTHONPATH=legacy/src:src python -m pytest -c legacy/pytest.ini legacy/tests
```

Legacy outputs are historical/read-only evidence. They cannot create a v0.2
`ResultEnvelope`, receipt, promotion decision, or strong measured statement.
