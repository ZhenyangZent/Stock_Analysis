from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
FIXTURES = PAYLOAD / "FIXTURES" / "provider_official_docs"
sys.path.insert(0, str(PAYLOAD / "PROTOTYPE"))

from real_source_adapters import (  # noqa: E402
    AdapterError,
    aggregate_fred_api_json_pages,
    parse_fred_api_json,
)


class FredPaginationIntegrityTests(unittest.TestCase):
    def setUp(self):
        series = json.loads((FIXTURES / "fred_series_GNPCA_documentation_example.json").read_text(encoding="utf-8"))
        series["seriess"][0].update(
            id="DGS10",
            title="Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity",
            frequency="Daily",
            frequency_short="D",
            units="Percent",
            units_short="Percent",
        )
        self.series_raw = self.encoded(series)

    @staticmethod
    def encoded(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def rows(dates, realtime_start="2026-10-24", realtime_end="2026-10-24"):
        return [
            {"realtime_start": realtime_start, "realtime_end": realtime_end, "date": observed, "value": str(4.0 + index / 10)}
            for index, observed in enumerate(dates)
        ]

    def page(self, *, count, limit, offset, dates, realtime_start="2026-10-24", realtime_end="2026-10-24"):
        payload = {
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
            "observation_start": "2026-01-01",
            "observation_end": "2026-12-31",
            "units": "lin",
            "output_type": 1,
            "file_type": "json",
            "order_by": "observation_date",
            "sort_order": "asc",
            "count": count,
            "offset": offset,
            "limit": limit,
            "observations": self.rows(dates, realtime_start, realtime_end),
        }
        request = {
            "series_id": "DGS10",
            "file_type": "json",
            "output_type": 1,
            "units": "lin",
            "order_by": "observation_date",
            "sort_order": "asc",
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
            "observation_start": "2026-01-01",
            "observation_end": "2026-12-31",
            "limit": limit,
            "offset": offset,
        }
        return {"raw_bytes": self.encoded(payload), "request_parameters": request}

    def two_pages(self):
        return [
            self.page(count=6, limit=3, offset=0, dates=["2026-01-02", "2026-01-05", "2026-01-06"]),
            self.page(count=6, limit=3, offset=3, dates=["2026-01-07", "2026-01-08", "2026-01-09"]),
        ]

    def test_55_legal_partial_page_is_parsed_not_rejected(self):
        page = self.page(count=84, limit=3, offset=0, dates=["2026-01-02", "2026-01-05", "2026-01-06"])
        result = parse_fred_api_json(self.series_raw, page["raw_bytes"], "DGS10", page["request_parameters"])
        self.assertEqual("PROVIDER_FORMAT_VERIFIED", result["provider_format_status"])
        self.assertEqual(84, result["total_count"])
        self.assertEqual(3, result["page_row_count"])
        self.assertFalse(result["pagination_complete"])
        self.assertIn("FRED_PAGINATION_INCOMPLETE", result["blockers"])

    def test_56_two_valid_pages_assemble_complete_rowset(self):
        pages = self.two_pages()
        result = aggregate_fred_api_json_pages(self.series_raw, pages, "DGS10")
        self.assertEqual("PAGINATION_COMPLETE", result["pagination_status"])
        self.assertEqual(6, result["assembled_row_count"])
        self.assertEqual(2, result["page_count"])
        self.assertEqual("SOURCE_PUBLICATION_AVAILABLE_NOT_VERIFIED", result["source_publication_available_status"])
        self.assertEqual("DATA_INSUFFICIENT", result["currentness_status"])

    def test_57_missing_final_page_fails_closed(self):
        with self.assertRaisesRegex(AdapterError, "FRED_PAGINATION_INCOMPLETE"):
            aggregate_fred_api_json_pages(self.series_raw, self.two_pages()[:1], "DGS10")

    def test_58_duplicate_page_fails_closed(self):
        first = self.two_pages()[0]
        with self.assertRaisesRegex(AdapterError, "FRED_DUPLICATE_PAGE"):
            aggregate_fred_api_json_pages(self.series_raw, [first, copy.deepcopy(first)], "DGS10")

    def test_59_offset_gap_fails_closed(self):
        first = self.two_pages()[0]
        gap = self.page(count=6, limit=3, offset=4, dates=["2026-01-08", "2026-01-09"])
        with self.assertRaisesRegex(AdapterError, "FRED_PAGE_OFFSET_NOT_CONTIGUOUS"):
            aggregate_fred_api_json_pages(self.series_raw, [first, gap], "DGS10")

    def test_60_cross_page_total_count_conflict_fails_closed(self):
        first = self.two_pages()[0]
        conflict = self.page(count=7, limit=3, offset=3, dates=["2026-01-07", "2026-01-08", "2026-01-09"])
        with self.assertRaisesRegex(AdapterError, "FRED_CROSS_PAGE_PAGINATION_CONFLICT"):
            aggregate_fred_api_json_pages(self.series_raw, [first, conflict], "DGS10")

    def test_61_cross_page_vintage_conflict_fails_closed(self):
        first = self.two_pages()[0]
        conflict = self.page(count=6, limit=3, offset=3, dates=["2026-01-07", "2026-01-08", "2026-01-09"], realtime_start="2026-10-23", realtime_end="2026-10-23")
        with self.assertRaisesRegex(AdapterError, "FRED_CROSS_PAGE_VINTAGE_CONFLICT"):
            aggregate_fred_api_json_pages(self.series_raw, [first, conflict], "DGS10")

    def test_62_request_response_metadata_tamper_fails_closed(self):
        page = self.two_pages()[0]
        page["request_parameters"]["offset"] = 3
        with self.assertRaisesRegex(AdapterError, "FRED_REQUEST_RESPONSE_METADATA_MISMATCH"):
            aggregate_fred_api_json_pages(self.series_raw, [page], "DGS10")

    def test_63_cross_page_duplicate_record_fails_closed(self):
        first = self.two_pages()[0]
        duplicate = self.page(count=6, limit=3, offset=3, dates=["2026-01-06", "2026-01-08", "2026-01-09"])
        with self.assertRaisesRegex(AdapterError, "FRED_DUPLICATE_RECORD_IDENTITY"):
            aggregate_fred_api_json_pages(self.series_raw, [first, duplicate], "DGS10")

    def test_64_raw_bytes_and_request_identity_are_preserved(self):
        pages = self.two_pages()
        result = aggregate_fred_api_json_pages(self.series_raw, pages, "DGS10")
        for supplied, receipt in zip(pages, result["page_receipts"]):
            self.assertEqual(hashlib.sha256(supplied["raw_bytes"]).hexdigest(), receipt["raw_payload_sha256"])
            self.assertEqual(len(supplied["raw_bytes"]), receipt["raw_payload_bytes"])
            self.assertRegex(receipt["request_parameter_identity_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual("EXPLICIT_PARAMETERS_BOUND_TO_RESPONSE", receipt["request_identity_status"])

    def test_65_page_sort_order_tamper_fails_closed(self):
        page = self.page(count=3, limit=3, offset=0, dates=["2026-01-06", "2026-01-05", "2026-01-02"])
        with self.assertRaisesRegex(AdapterError, "FRED_PAGE_ORDER_INVALID"):
            parse_fred_api_json(self.series_raw, page["raw_bytes"], "DGS10", page["request_parameters"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
