#!/usr/bin/env python3
"""Build deterministic external synthetic FRED pagination byte fixtures."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "FIXTURES" / "fred_pagination_synthetic"


def write(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def page(offset: int, dates: list[str]) -> tuple[dict, dict]:
    request = {
        "series_id": "DGS10", "file_type": "json", "output_type": 1, "units": "lin",
        "order_by": "observation_date", "sort_order": "asc",
        "realtime_start": "2026-10-24", "realtime_end": "2026-10-24",
        "observation_start": "2026-01-01", "observation_end": "2026-12-31",
        "limit": 3, "offset": offset,
    }
    response = {
        "realtime_start": request["realtime_start"], "realtime_end": request["realtime_end"],
        "observation_start": request["observation_start"], "observation_end": request["observation_end"],
        "units": "lin", "output_type": 1, "file_type": "json",
        "order_by": "observation_date", "sort_order": "asc",
        "count": 6, "offset": offset, "limit": 3,
        "observations": [
            {"realtime_start": "2026-10-24", "realtime_end": "2026-10-24", "date": day, "value": str(4.0 + (offset + index) / 10)}
            for index, day in enumerate(dates)
        ],
    }
    return request, response


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    series = json.loads((ROOT / "FIXTURES" / "provider_official_docs" / "fred_series_GNPCA_documentation_example.json").read_text(encoding="utf-8"))
    series["seriess"][0].update(id="DGS10", frequency="Daily", frequency_short="D", units="Percent", units_short="Percent")
    write("series_DGS10.json", series)
    for offset, dates in ((0, ["2026-01-02", "2026-01-05", "2026-01-06"]), (3, ["2026-01-07", "2026-01-08", "2026-01-09"])):
        request, response = page(offset, dates)
        write(f"request_offset_{offset}.json", request)
        write(f"response_offset_{offset}.json", response)
    write("PROVENANCE.json", {
        "fixture_class": "SYNTHETIC_FRED_PAGINATION_CONTRACT_TEST_ONLY",
        "live_market_fetch_performed": False,
        "official_documentation_basis": "https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        "formal_currentness_acceptance": "PROHIBITED",
        "raw_response_bytes_rewritten_after_generation": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
