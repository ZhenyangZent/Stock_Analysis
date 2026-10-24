from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
FIXTURES = PAYLOAD / "FIXTURES" / "provider_official_docs"
sys.path.insert(0, str(PAYLOAD / "PROTOTYPE"))

from real_source_adapters import AdapterError, enforce_required_observation_keys, parse_fred_api_json  # noqa: E402


class RealSourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.series_raw = (FIXTURES / "fred_series_GNPCA_documentation_example.json").read_bytes()
        self.observations_raw = (FIXTURES / "fred_observations_GNPCA_documentation_excerpt.json").read_bytes()

    @staticmethod
    def encoded(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def dgs10_payloads(self):
        series = json.loads(self.series_raw)
        row = series["seriess"][0]
        row.update(id="DGS10", title="Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity", frequency="Daily", frequency_short="D", units="Percent", units_short="Percent")
        observations = json.loads(self.observations_raw)
        return series, observations

    def test_45_official_documentation_golden_format_parses(self):
        result = parse_fred_api_json(self.series_raw, self.observations_raw, "GNPCA_DOCUMENTATION_EXAMPLE")
        self.assertEqual("PROVIDER_FORMAT_PARSED", result["adapter_status"])
        self.assertEqual("SOURCE_SEMANTICS_VERIFIED", result["source_semantics_status"])
        self.assertEqual(3, len(result["records"]))

    def test_46_golden_fields_derive_from_raw(self):
        result = parse_fred_api_json(self.series_raw, self.observations_raw, "GNPCA_DOCUMENTATION_EXAMPLE")
        row = result["records"][0]
        self.assertEqual("GNPCA", row["source_id"])
        self.assertEqual("1929-01-01", row["observation_date"])
        self.assertEqual(1065.9, row["value"])
        self.assertEqual("Billions of Chained 2009 Dollars", row["unit"])
        self.assertRegex(row["record_identity_sha256"], r"^[0-9a-f]{64}$")

    def test_47_publication_availability_fetch_evidence_missing_fails_currentness(self):
        result = parse_fred_api_json(self.series_raw, self.observations_raw, "GNPCA_DOCUMENTATION_EXAMPLE")
        self.assertEqual("DATA_INSUFFICIENT", result["currentness_status"])
        self.assertFalse(result["currentness_eligible"])
        self.assertEqual(3, len(result["blockers"]))

    def test_48_value_tamper_breaks_pinned_golden_hash(self):
        payload = json.loads(self.observations_raw); payload["observations"][0]["value"] = "999999"
        with self.assertRaisesRegex(AdapterError, "RAW_ARTIFACT_HASH_MISMATCH"):
            parse_fred_api_json(self.series_raw, self.encoded(payload), "GNPCA_DOCUMENTATION_EXAMPLE")

    def test_49_missing_row_breaks_pinned_golden_hash(self):
        payload = json.loads(self.observations_raw); payload["observations"].pop(); payload["count"] = 2
        with self.assertRaisesRegex(AdapterError, "RAW_ARTIFACT_HASH_MISMATCH"):
            parse_fred_api_json(self.series_raw, self.encoded(payload), "GNPCA_DOCUMENTATION_EXAMPLE")

    def test_50_duplicate_provider_record_rejected(self):
        series, observations = self.dgs10_payloads(); observations["observations"].append(copy.deepcopy(observations["observations"][0])); observations["count"] = 4
        with self.assertRaisesRegex(AdapterError, "FRED_DUPLICATE_RECORD_IDENTITY"):
            parse_fred_api_json(self.encoded(series), self.encoded(observations), "DGS10")

    def test_51_revised_initial_or_realtime_mix_rejected(self):
        series, observations = self.dgs10_payloads(); observations["observations"][0]["realtime_start"] = "2013-08-13"
        with self.assertRaisesRegex(AdapterError, "FRED_REVISED_INITIAL_MIX_OR_NONCURRENT_RANGE"):
            parse_fred_api_json(self.encoded(series), self.encoded(observations), "DGS10")

    def test_52_source_identity_and_unit_contracts_rejected(self):
        series, observations = self.dgs10_payloads(); series["seriess"][0]["id"] = "DCOILBRENTEU"
        with self.assertRaisesRegex(AdapterError, "FRED_SOURCE_ID_MISMATCH"):
            parse_fred_api_json(self.encoded(series), self.encoded(observations), "DGS10")
        series["seriess"][0]["id"] = "DGS10"; series["seriess"][0]["units"] = "Basis Points"
        with self.assertRaisesRegex(AdapterError, "FRED_UNIT_MISMATCH"):
            parse_fred_api_json(self.encoded(series), self.encoded(observations), "DGS10")

    def test_53_missing_day_or_month_rejected_by_schedule_binding(self):
        result = parse_fred_api_json(self.series_raw, self.observations_raw, "GNPCA_DOCUMENTATION_EXAMPLE")
        with self.assertRaisesRegex(AdapterError, "PROVIDER_EXPECTED_OBSERVATION_MISSING"):
            enforce_required_observation_keys(result, ["1929-01-01", "1930-01-01", "1931-01-01", "1932-01-01"])

    def test_54_alfred_initial_and_unknown_adapter_contract_not_approved(self):
        with self.assertRaisesRegex(AdapterError, "FRED_SERIES_CONTRACT_NOT_PINNED"):
            parse_fred_api_json(self.series_raw, self.observations_raw, "ALFRED_INITIAL_UNAPPROVED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
