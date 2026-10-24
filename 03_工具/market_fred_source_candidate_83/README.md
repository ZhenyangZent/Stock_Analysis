# MARKET-83 FRED source candidate

This directory is an isolated, default-disabled review candidate. It contains the exact MARKET-82 adapter, contracts, fixtures and 65-test baseline plus a thin repository wrapper.

Safety properties:

- Importing `candidate.py` has no network, file-write, or validation side effect.
- The CLI is disabled unless `--enable-offline-validation` is explicitly supplied.
- Enabled operation only reads local files beneath an explicit allowed root and verifies every bound SHA-256 before parsing.
- The module contains no network client and no formal market-data writer.
- Successful parsing still reports `SOURCE_PUBLICATION_AVAILABLE_NOT_VERIFIED`, `DATA_INSUFFICIENT`, and `FORMAL_PUBLISH_NOT_ELIGIBLE`.
- Unsupported FRED/ALFRED/FOMC/price adapters fail closed.

`market82/FIXTURES/provider_official_docs` contains documentation examples. `market82/FIXTURES/fred_pagination_synthetic` and the other fixtures are synthetic. None is an actual current M1-M7 acquisition or evidence of observation-level `available_at`.

Offline example:

```powershell
python candidate.py --enable-offline-validation --bundle fixture_bundle.json --allowed-root .
```

This candidate must not be wired into `market_env.py`, fetchers, scoring, snapshots, rules, or publication without separate authority and independent review.
