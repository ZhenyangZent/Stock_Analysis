from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
FIXTURES = PAYLOAD / "FIXTURES"
EVIDENCE = PAYLOAD / "EVIDENCE_BYTES"
TEST_TMP = PAYLOAD.parent / ".test_tmp"
TEST_TMP.mkdir(exist_ok=True)
sys.path.insert(0, str(PAYLOAD / "PROTOTYPE"))

from candidate_interface import run_candidate  # noqa: E402
from hardened_currentness_validator import validate_receipt  # noqa: E402


class HardenedCurrentnessTests(unittest.TestCase):
    maxDiff = None

    def load(self, name="complete_current_market_only.json"):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def result(self, name):
        return validate_receipt(self.load(name), EVIDENCE)

    def codes(self, name):
        return {row["code"] for row in self.result(name)["errors"]}

    def assert_code(self, name, code):
        self.assertIn(code, self.codes(name))

    @staticmethod
    def canonical_sha(value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _semantic_attack(self, source_id, mutate_observations=None, mutate_raw=None):
        temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        evidence = Path(temp.name) / "evidence"
        shutil.copytree(EVIDENCE, evidence)
        receipt = self.load()
        indicator = next(row for row in receipt["indicators"] if row["source_id"] == source_id)
        if mutate_observations:
            mutate_observations(indicator["observations"])
        raw_path = evidence / "raw_sources" / f"{source_id}.json"
        raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if mutate_raw:
            mutate_raw(raw_payload["records"])
            self.write_json(raw_path, raw_payload)
        raw_bytes = raw_path.read_bytes()
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        for row in indicator["observations"]:
            row["source_payload_sha256"] = raw_sha
        source_receipt_path = evidence / "source_receipts" / f"{source_id}.json"
        source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
        source_receipt["response_artifact"] = {
            "path": f"raw_sources/{source_id}.json",
            "sha256": raw_sha,
            "bytes": len(raw_bytes),
        }
        source_receipt["canonical_source_rowset_sha256"] = self.canonical_sha(raw_payload["records"])
        source_receipt["rowset_sha256"] = self.canonical_sha(indicator["observations"])
        source_receipt["requested_items"] = len(raw_payload["records"])
        source_receipt["succeeded_items"] = len(raw_payload["records"])
        self.write_json(source_receipt_path, source_receipt)
        receipt_ref = next(row for row in receipt["source_receipt_refs"] if row["source_id"] == source_id)
        receipt_bytes = source_receipt_path.read_bytes()
        receipt_ref["sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
        receipt_ref["bytes"] = len(receipt_bytes)
        return temp, receipt, evidence

    def test_01_complete_receipt_data_current_but_not_formal(self):
        result = self.result("complete_current_market_only.json")
        self.assertEqual("PASS", result["validator_status"])
        self.assertEqual("DATA_CURRENT", result["data_currentness_status"])
        self.assertEqual("CODE_NOT_INTEGRATED", result["code_integration_status"])
        self.assertEqual("PIT_NOT_APPROVED", result["pit_vintage_status"])
        self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])
        self.assertEqual("DISABLED", result["candidate_formal_publish_capability"])
        self.assertTrue(all(row["status"] == "CURRENT" for row in result["indicator_results"]))

    # MARKET-72 original 14-case semantics.
    def test_02_m5_2025_10_missing(self): self.assert_code("missing_m5_2025_10.json", "EXACT_CALENDAR_KEY_MISSING")
    def test_03_source_time_after_cutoff(self): self.assert_code("source_time_after_cutoff.json", "SOURCE_TIME_AFTER_CUTOFF")
    def test_04_duplicate_observation(self): self.assert_code("duplicate_observation.json", "DUPLICATE_OBSERVATION")
    def test_05_source_sha_mismatch(self): self.assert_code("source_sha_mismatch.json", "ARTIFACT_BYTE_HASH_MISMATCH")
    def test_06_missing_trading_day(self): self.assert_code("missing_trading_day.json", "REQUIRED_OBSERVATION_MISSING")
    def test_07_partial_source_failure(self): self.assert_code("partial_source_failure.json", "PARTIAL_SOURCE_FAILURE")
    def test_08_price_anchor_mismatch(self): self.assert_code("price_anchor_mismatch.json", "PRICE_ANCHOR_MISMATCH")
    def test_09_m1_event_calendar_required(self): self.assert_code("missing_m1_event_calendar.json", "M1_EVENT_CALENDAR_MISSING")
    def test_10_m1_publication_receipt_required(self): self.assert_code("missing_m1_publication_receipt.json", "M1_PUBLICATION_RECEIPT_MISSING")
    def test_11_conflicting_m4_month(self): self.assert_code("conflicting_m4_month.json", "EXACT_CALENDAR_KEY_CONFLICT")
    def test_12_mixed_alfred_revised(self): self.assert_code("mixed_alfred_revised.json", "VINTAGE_POLICY_MIXED")
    def test_13_provider_period_lag(self): self.assert_code("provider_period_lag.json", "MISSING_EXPECTED_TRADING_OR_PROVIDER_PERIOD")
    def test_14_t46_dependency_forbidden(self): self.assert_code("t46_dependency_forbidden.json", "T46_DEPENDENCY_FORBIDDEN")

    # Required observation presence for every M1-M7 lane.
    def test_15_empty_m1(self): self.assert_code("empty_observations_m1.json", "REQUIRED_OBSERVATION_MISSING")
    def test_16_empty_m2(self): self.assert_code("empty_observations_m2.json", "REQUIRED_OBSERVATION_MISSING")
    def test_17_empty_m3(self): self.assert_code("empty_observations_m3.json", "REQUIRED_OBSERVATION_MISSING")
    def test_18_empty_m4(self): self.assert_code("empty_observations_m4.json", "REQUIRED_OBSERVATION_MISSING")
    def test_19_empty_m5(self): self.assert_code("empty_observations_m5.json", "REQUIRED_OBSERVATION_MISSING")
    def test_20_empty_m6(self): self.assert_code("empty_observations_m6.json", "REQUIRED_OBSERVATION_MISSING")
    def test_21_empty_m7(self): self.assert_code("empty_observations_m7.json", "REQUIRED_OBSERVATION_MISSING")

    def test_22_observation_timestamp_order(self):
        self.assert_code("observation_available_before_published.json", "OBSERVATION_TIMESTAMP_ORDER_INVALID")

    def test_23_observation_source_time_cross_binding(self):
        self.assert_code("observation_source_time_cross_mismatch.json", "OBSERVATION_SOURCE_TIME_CROSS_BINDING_MISMATCH")

    def test_24_observation_source_id_cross_binding(self):
        self.assert_code("observation_source_id_mismatch.json", "OBSERVATION_SOURCE_MISMATCH")

    def test_25_receipt_governance_cannot_self_authorize(self):
        result = self.result("synthetic_self_authority.json")
        self.assertEqual("PASS", result["validator_status"])
        self.assertEqual("DATA_CURRENT", result["data_currentness_status"])
        self.assertEqual("CODE_NOT_INTEGRATED", result["code_integration_status"])
        self.assertEqual("PIT_NOT_APPROVED", result["pit_vintage_status"])
        self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])
        self.assertIn("RECEIPT_GOVERNANCE_CLAIMS_IGNORED", {row["code"] for row in result["warnings"]})

    def test_26_unapproved_alfred_policy(self):
        self.assert_code("unapproved_alfred_policy.json", "VINTAGE_POLICY_NOT_APPROVED_BY_SOURCE_POLICY")

    def _tampered_evidence(self, relative, content_suffix=b"\nTAMPER"):
        temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        root = Path(temp.name) / "evidence"
        shutil.copytree(EVIDENCE, root)
        path = root / relative
        path.write_bytes(path.read_bytes() + content_suffix)
        return temp, root

    def test_27_actual_source_bytes_tamper(self):
        temp, root = self._tampered_evidence("raw_sources/FRED_DGS10.json")
        try:
            result = validate_receipt(self.load(), root)
            codes = {row["code"] for row in result["errors"]}
            self.assertIn("ARTIFACT_BYTE_HASH_MISMATCH", codes)
            self.assertIn("OBSERVATION_SOURCE_BYTES_MISMATCH", codes)
            self.assertEqual("DATA_INSUFFICIENT", result["data_currentness_status"])
        finally:
            temp.cleanup()

    def test_28_source_receipt_bytes_tamper(self):
        temp, root = self._tampered_evidence("source_receipts/FRED_DGS10.json", b" ")
        try:
            self.assertIn("ARTIFACT_BYTE_HASH_MISMATCH", {row["code"] for row in validate_receipt(self.load(), root)["errors"]})
        finally:
            temp.cleanup()

    def test_29_schedule_receipt_bytes_tamper(self):
        temp, root = self._tampered_evidence("schedule_receipts/M2.json", b" ")
        try:
            self.assertIn("ARTIFACT_BYTE_HASH_MISMATCH", {row["code"] for row in validate_receipt(self.load(), root)["errors"]})
        finally:
            temp.cleanup()

    def test_30_schedule_payload_bytes_tamper(self):
        temp, root = self._tampered_evidence("raw_schedules/M2.json", b" ")
        try:
            codes = {row["code"] for row in validate_receipt(self.load(), root)["errors"]}
            self.assertIn("ARTIFACT_BYTE_HASH_MISMATCH", codes)
            self.assertIn("OBSERVATION_SCHEDULE_BYTES_MISMATCH", codes)
        finally:
            temp.cleanup()

    def test_31_candidate_interface_default_disabled(self):
        result = run_candidate(FIXTURES / "complete_current_market_only.json", EVIDENCE)
        self.assertEqual("CANDIDATE_DISABLED", result["status"])
        self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])

    def test_32_candidate_synthetic_enable_still_not_formal(self):
        result = run_candidate(FIXTURES / "complete_current_market_only.json", EVIDENCE, enable_synthetic_validation=True)
        self.assertEqual("PASS", result["validator_status"])
        self.assertEqual("SYNTHETIC_VALIDATION_ONLY", result["interface_status"])
        self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])

    def _load_copied_validator_after_registry_tamper(self, registry_name):
        temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        root = Path(temp.name) / "payload"
        shutil.copytree(PAYLOAD, root)
        registry = root / "CONTRACTS" / registry_name
        registry.write_bytes(registry.read_bytes() + b" ")
        module_path = root / "PROTOTYPE" / "hardened_currentness_validator.py"
        spec = importlib.util.spec_from_file_location(f"tampered_{registry_name}", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return temp, root, module

    def test_33_authority_registry_is_pinned(self):
        temp, root, module = self._load_copied_validator_after_registry_tamper("authority_registry.json")
        try:
            result = module.validate_receipt(json.loads((root / "FIXTURES" / "complete_current_market_only.json").read_text(encoding="utf-8")), root / "EVIDENCE_BYTES")
            self.assertIn("PINNED_REGISTRY_HASH_MISMATCH", {row["code"] for row in result["errors"]})
            self.assertEqual("FAIL_CLOSED", result["validator_status"])
            self.assertEqual("DATA_CURRENT", result["data_currentness_status"])
            self.assertEqual("CODE_NOT_INTEGRATED", result["code_integration_status"])
            self.assertEqual("PIT_NOT_APPROVED", result["pit_vintage_status"])
            self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])
        finally:
            temp.cleanup()

    def test_34_source_policy_registry_is_pinned(self):
        temp, root, module = self._load_copied_validator_after_registry_tamper("source_policy_registry.json")
        try:
            result = module.validate_receipt(json.loads((root / "FIXTURES" / "complete_current_market_only.json").read_text(encoding="utf-8")), root / "EVIDENCE_BYTES")
            self.assertIn("PINNED_REGISTRY_HASH_MISMATCH", {row["code"] for row in result["errors"]})
            self.assertEqual("DATA_INSUFFICIENT", result["data_currentness_status"])
        finally:
            temp.cleanup()

    def test_35_value_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows[0].update(value=12345.6789))
        try:
            result = validate_receipt(receipt, evidence)
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in result["errors"]})
            self.assertEqual("DATA_INSUFFICIENT", result["data_currentness_status"])
        finally:
            temp.cleanup()

    def test_36_observation_date_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows[0].update(observation_date="2026-02-18"))
        try:
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_37_source_record_missing_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_CPILFESL_REVISED", mutate_raw=lambda rows: rows.pop(2))
        try:
            codes = {row["code"] for row in validate_receipt(receipt, evidence)["errors"]}
            self.assertIn("OBSERVATION_SOURCE_ROWSET_COUNT_MISMATCH", codes)
            self.assertIn("SOURCE_RECORD_MISSING_FOR_OBSERVATION", codes)
        finally:
            temp.cleanup()

    def test_38_vintage_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_UNRATE_REVISED", lambda rows: rows[0].update(vintage_policy="N/A"))
        try:
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_39_unit_mismatch_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows[0].update(unit="BASIS_POINTS"))
        try:
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_40_source_id_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows[0].update(source_id="FRED_BRENT"))
        try:
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_41_calendar_key_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows[0].update(calendar_key="2026-01"))
        try:
            self.assertIn("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_42_observation_count_tamper_rehashed_metadata_rejected(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10", lambda rows: rows.append(copy.deepcopy(rows[0])))
        try:
            self.assertIn("OBSERVATION_SOURCE_ROWSET_COUNT_MISMATCH", {row["code"] for row in validate_receipt(receipt, evidence)["errors"]})
        finally:
            temp.cleanup()

    def test_43_unsupported_real_adapter_fails_closed(self):
        temp, receipt, evidence = self._semantic_attack("FRED_DGS10")
        try:
            source_receipt_path = evidence / "source_receipts" / "FRED_DGS10.json"
            source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
            source_receipt["adapter_id"] = "FRED_CSV_REAL"
            self.write_json(source_receipt_path, source_receipt)
            ref = next(row for row in receipt["source_receipt_refs"] if row["source_id"] == "FRED_DGS10")
            raw = source_receipt_path.read_bytes(); ref["bytes"] = len(raw); ref["sha256"] = hashlib.sha256(raw).hexdigest()
            result = validate_receipt(receipt, evidence)
            self.assertIn("SOURCE_ADAPTER_UNSUPPORTED", {row["code"] for row in result["errors"]})
            self.assertEqual("FAIL_CLOSED", result["validator_status"])
        finally:
            temp.cleanup()

    def test_44_source_adapter_registry_is_pinned(self):
        temp, root, module = self._load_copied_validator_after_registry_tamper("source_adapter_registry.json")
        try:
            result = module.validate_receipt(json.loads((root / "FIXTURES" / "complete_current_market_only.json").read_text(encoding="utf-8")), root / "EVIDENCE_BYTES")
            self.assertIn("PINNED_REGISTRY_HASH_MISMATCH", {row["code"] for row in result["errors"]})
            self.assertEqual("DATA_INSUFFICIENT", result["data_currentness_status"])
            self.assertEqual("FORMAL_PUBLISH_NOT_ELIGIBLE", result["formal_publish_status"])
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
