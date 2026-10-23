#!/usr/bin/env python3
"""Run the offline six-row/two-page FRED pagination fixture."""

from __future__ import annotations

import json
from pathlib import Path

from real_source_adapters import aggregate_fred_api_json_pages


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    fixture = ROOT / "FIXTURES" / "fred_pagination_synthetic"
    pages = [
        {"raw_bytes": (fixture / f"response_offset_{offset}.json").read_bytes(), "request_parameters": json.loads((fixture / f"request_offset_{offset}.json").read_text(encoding="utf-8"))}
        for offset in (0, 3)
    ]
    result = aggregate_fred_api_json_pages((fixture / "series_DGS10.json").read_bytes(), pages, "DGS10")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
