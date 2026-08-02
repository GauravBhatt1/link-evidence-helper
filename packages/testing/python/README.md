# Python behavioral parity harness

This standalone harness imports only the existing pure aggregation module. It
does not modify legacy files, access production data, or make network requests.

Regenerate deterministic sanitized fixtures:

```bash
python3 packages/testing/python/export_contract_fixtures.py --write
```

Verify checked-in fixtures and canonical JSON Schema validation:

```bash
python3 packages/testing/python/export_contract_fixtures.py --check
python3 -m unittest packages.testing.python.test_parity
```

The manifest records the schema and provenance for every valid and invalid
fixture. URL query values and internal/secret fields are removed before output.
