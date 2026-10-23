from __future__ import annotations

import builtins
import copy
import json
import pathlib
import unittest
from unittest import mock

import t46_reader_candidate as candidate


FIXTURES = pathlib.Path(__file__).with_name("fixtures")


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def base_input(corrected: bool = False) -> dict:
    golden = load_json("t46_reader_candidate_golden.json")
    return {
        "adapterSchema": candidate.ADAPTER_SCHEMA,
        "governanceMode": "SINGLE_CUTOFF_ONLY",
        "authorityMode": "PROSPECTIVE_ONLY",
        "documentManifest": copy.deepcopy(golden["document_manifest"]),
        "expectedDocumentManifestSha256": golden["document_manifest_sha256"],
        "corrections": copy.deepcopy(golden["corrected"]["corrections"] if corrected else []),
        "evidenceRegistry": copy.deepcopy(golden["corrected"]["evidence_registry"] if corrected else {}),
        "documentAsOf": golden["cutoff"],
        "knowledgeCutoff": golden["cutoff"],
    }


def reseal(event: dict) -> dict:
    return candidate.seal_correction(event)


class CandidateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.capability = candidate.issue_synthetic_test_capability()

    def assertCode(self, code: str, fn, *args, **kwargs):
        with self.assertRaises(candidate.CandidateError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)


class DisabledBoundaryTests(CandidateTestCase):
    def test_candidate_constants_are_disabled(self):
        self.assertFalse(candidate.CANDIDATE_ENABLED)
        self.assertFalse(candidate.FORMAL_T46_RECOVERY_AUTHORIZED)
        self.assertFalse(candidate.FORMAL_READER_ACTIVATION_AUTHORIZED)

    def test_formal_entry_fails_before_file_access(self):
        with mock.patch.object(builtins, "open") as opened:
            self.assertCode("FORMAL_T46_SOURCE_REJECTED_CANDIDATE_DISABLED",
                            candidate.open_formal_reader, "docs_ledger.jsonl")
            opened.assert_not_called()

    def test_missing_synthetic_capability(self):
        self.assertCode("SYNTHETIC_TEST_CAPABILITY_REQUIRED", candidate.open_synthetic_reader,
                        base_input(), capability=None)

    def test_forged_synthetic_capability(self):
        forged = object.__new__(candidate._SyntheticTestCapability)
        object.__setattr__(forged, "_marker", object())
        self.assertCode("SYNTHETIC_TEST_CAPABILITY_REQUIRED", candidate.open_synthetic_reader,
                        base_input(), capability=forged)

    def test_formal_manifest_cannot_enter_synthetic_lane(self):
        raw = base_input()
        raw["documentManifest"]["scope"] = "FORMAL"
        raw["expectedDocumentManifestSha256"] = candidate.hash_object(raw["documentManifest"])
        self.assertCode("FORMAL_T46_AUTHORITY_NOT_GRANTED", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_governance_mode_cannot_be_switched_by_parameter(self):
        raw = base_input()
        raw["governanceMode"] = "ALL_AS_OF"
        self.assertCode("GOVERNANCE_MODE_NOT_APPROVED", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_authority_mode_cannot_be_switched_by_parameter(self):
        raw = base_input()
        raw["authorityMode"] = "RETROACTIVE"
        self.assertCode("AUTHORITY_MODE_NOT_APPROVED", candidate.open_synthetic_reader,
                        raw, capability=self.capability)


class GoldenParityTests(CandidateTestCase):
    def test_canonical_json_contract(self):
        self.assertEqual(b'{"a":1,"b":2}\n', candidate.canonical_bytes({"b": 2, "a": 1}))

    def test_document_manifest_hash_matches_javascript(self):
        golden = load_json("t46_reader_candidate_golden.json")
        self.assertEqual(golden["document_manifest_sha256"],
                         candidate.hash_object(golden["document_manifest"]))

    def _assert_lane(self, corrected: bool):
        golden = load_json("t46_reader_candidate_golden.json")
        lane = golden["corrected" if corrected else "plain"]
        handle = candidate.open_synthetic_reader(base_input(corrected), capability=self.capability)
        inspected = candidate.inspect_reader_handle(handle)
        self.assertEqual(lane["active_doc_ids"], inspected["active_doc_ids"])
        self.assertEqual(lane["effective_view_sha256"], inspected["effective_view"]["effective_view_sha256"])
        self.assertEqual(lane["correction_log_sha256"], inspected["effective_view"]["correction_log_sha256"])
        self.assertEqual(lane["handle_id"], inspected["handle_id"])
        requested = {"I1": lane["active_doc_ids"]}
        queue = candidate.rebuild_synthetic_queue(reader_handle=handle,
                                                   requested_by_issuer=requested,
                                                   capability=self.capability)
        self.assertEqual(lane["queue_sha256"], queue["queue_sha256"])

    def test_plain_lane_matches_javascript_golden(self):
        self._assert_lane(False)

    def test_corrected_lane_matches_javascript_golden(self):
        self._assert_lane(True)


class TemporalAndCorrectionTests(CandidateTestCase):
    def test_strict_date_accepts_leap_day_and_nanoseconds(self):
        self.assertIsInstance(candidate.parse_timestamp_strict("2024-02-29T23:59:59.123456789Z"), int)

    def test_strict_date_rejects_february_30(self):
        self.assertCode("TIMESTAMP_INVALID", candidate.parse_timestamp_strict,
                        "2025-02-30T00:00:00Z")

    def test_strict_date_rejects_missing_timezone(self):
        self.assertCode("TIMESTAMP_INVALID", candidate.parse_timestamp_strict,
                        "2025-02-28T00:00:00")

    def test_strict_date_rejects_invalid_offset(self):
        self.assertCode("TIMESTAMP_INVALID", candidate.parse_timestamp_strict,
                        "2025-02-28T00:00:00+24:00")

    def test_future_document_is_excluded(self):
        raw = base_input()
        raw["documentManifest"]["documents"][1]["available_at"] = "2026-01-11T00:00:00Z"
        raw["documentManifest"]["documents"][1] = candidate.seal_document(raw["documentManifest"]["documents"][1])
        raw["expectedDocumentManifestSha256"] = candidate.hash_object(raw["documentManifest"])
        handle = candidate.open_synthetic_reader(raw, capability=self.capability)
        self.assertEqual(["D1"], candidate.inspect_reader_handle(handle)["active_doc_ids"])

    def test_late_authority_is_rejected(self):
        raw = base_input(True)
        event = raw["corrections"][0]
        event["authority_ref"]["authorized_at"] = "2026-10-22T00:00:00Z"
        event["authority_ref"] = candidate.seal_authority(event["authority_ref"])
        raw["corrections"][0] = reseal(event)
        self.assertCode("AUTHORITY_NOT_VALID_AT_CORRECTION_CREATION",
                        candidate.open_synthetic_reader, raw, capability=self.capability)

    def test_missing_evidence_is_rejected(self):
        raw = base_input(True)
        raw["evidenceRegistry"] = {}
        self.assertCode("EVIDENCE_BINDING_MISMATCH", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_backdated_effective_time_is_rejected(self):
        raw = base_input(True)
        event = raw["corrections"][0]
        event["effective_at"] = "2026-01-02T00:00:00Z"
        raw["corrections"][0] = reseal(event)
        self.assertCode("CORRECTION_BACKDATED_EFFECTIVE_AT", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_future_target_reference_is_rejected(self):
        raw = base_input(True)
        raw["documentManifest"]["documents"][1]["available_at"] = "2026-01-04T00:00:00Z"
        raw["documentManifest"]["documents"][1] = candidate.seal_document(raw["documentManifest"]["documents"][1])
        raw["expectedDocumentManifestSha256"] = candidate.hash_object(raw["documentManifest"])
        event = raw["corrections"][0]
        event["target_row_sha256"] = raw["documentManifest"]["documents"][1]["row_sha256"]
        event["source_document_manifest_sha256"] = raw["expectedDocumentManifestSha256"]
        raw["corrections"][0] = reseal(event)
        self.assertCode("TARGET_NOT_AVAILABLE_AT_CORRECTION_CREATION",
                        candidate.open_synthetic_reader, raw, capability=self.capability)

    def test_missing_target_is_rejected(self):
        raw = base_input(True)
        event = raw["corrections"][0]
        event["target_doc_id"] = "MISSING"
        raw["corrections"][0] = reseal(event)
        self.assertCode("CORRECTION_TARGET_NOT_FOUND", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_supersede_replacement_missing(self):
        raw = base_input(True)
        event = raw["corrections"][0]
        event.update(record_type="SUPERSEDE", replacement_doc_id="MISSING",
                     replacement_row_sha256="0" * 64)
        raw["corrections"][0] = reseal(event)
        self.assertCode("CORRECTION_REPLACEMENT_NOT_FOUND", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_supersede_self_reference(self):
        raw = base_input(True)
        event = raw["corrections"][0]
        event.update(record_type="SUPERSEDE", replacement_doc_id="D2",
                     replacement_row_sha256=event["target_row_sha256"])
        raw["corrections"][0] = reseal(event)
        self.assertCode("SUPERSEDE_SELF_REFERENCE", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_duplicate_identical_correction_is_idempotent(self):
        raw = base_input(True)
        raw["corrections"].append(copy.deepcopy(raw["corrections"][0]))
        handle = candidate.open_synthetic_reader(raw, capability=self.capability)
        view = candidate.inspect_reader_handle(handle)["effective_view"]
        self.assertEqual(1, view["duplicate_noop_count"])

    def test_conflicting_terminal_corrections_are_rejected(self):
        raw = base_input(True)
        second = copy.deepcopy(raw["corrections"][0])
        second["reason_code"] = "DUPLICATE_REGISTRATION"
        raw["corrections"].append(reseal(second))
        self.assertCode("CONFLICTING_TERMINAL_CORRECTION", candidate.open_synthetic_reader,
                        raw, capability=self.capability)

    def test_nonmonotonic_correction_log_is_rejected(self):
        raw = base_input(True)
        second = copy.deepcopy(raw["corrections"][0])
        second["created_at"] = "2026-01-02T12:00:00Z"
        second["effective_at"] = "2026-01-02T12:00:00Z"
        raw["corrections"].append(reseal(second))
        self.assertCode("CORRECTION_LOG_NOT_APPEND_TIME_ORDERED",
                        candidate.open_synthetic_reader, raw, capability=self.capability)

    def test_unequal_cutoff_requires_temporal_contract(self):
        raw = base_input()
        self.assertCode("TEMPORAL_CONTRACT_REQUIRED_FOR_UNEQUAL_CUTOFFS",
                        candidate.resolve_effective_document_view,
                        document_manifest=raw["documentManifest"],
                        expected_document_manifest_sha256=raw["expectedDocumentManifestSha256"],
                        corrections=[], evidence_registry={}, document_as_of="2026-01-10T00:00:00Z",
                        knowledge_cutoff="2026-01-09T00:00:00Z")

    def test_adapter_rejects_unequal_cutoff_even_before_contract(self):
        raw = base_input()
        raw["knowledgeCutoff"] = "2026-01-09T00:00:00Z"
        self.assertCode("SINGLE_CUTOFF_MISMATCH", candidate.open_synthetic_reader,
                        raw, capability=self.capability)


class HandleAndQueueTests(CandidateTestCase):
    def test_arbitrary_object_cannot_impersonate_handle(self):
        self.assertCode("READER_HANDLE_NOT_ISSUED", candidate.rebuild_synthetic_queue,
                        reader_handle={"handle_id": "forged"}, requested_by_issuer={"I1": ["FAKE"]},
                        capability=self.capability)

    def test_invalidated_handle_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        candidate.invalidate_reader_handle(handle)
        self.assertCode("READER_HANDLE_INVALIDATED", candidate.inspect_reader_handle, handle)

    def test_inspected_view_is_a_defensive_copy(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        inspected = candidate.inspect_reader_handle(handle)
        inspected["effective_view"]["documents"].clear()
        self.assertEqual(["D1", "D2"], candidate.inspect_reader_handle(handle)["active_doc_ids"])

    def test_internal_view_tamper_fails_closed(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        candidate._HANDLES[handle]["view"]["documents"].clear()
        self.assertCode("EFFECTIVE_VIEW_HASH_MISMATCH", candidate.inspect_reader_handle, handle)

    def test_serialized_handle_round_trip(self):
        handle = candidate.open_synthetic_reader(base_input(True), capability=self.capability)
        envelope = candidate.serialize_reader_handle(handle)
        restored = candidate.restore_reader_handle(envelope, capability=self.capability)
        self.assertEqual(candidate.inspect_reader_handle(handle)["handle_id"],
                         candidate.inspect_reader_handle(restored)["handle_id"])

    def test_serialized_handle_tamper_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        envelope = candidate.serialize_reader_handle(handle)
        envelope["knowledge_cutoff"] = "2026-01-09T00:00:00Z"
        self.assertCode("SERIALIZED_HANDLE_HASH_MISMATCH", candidate.restore_reader_handle,
                        envelope, capability=self.capability)

    def test_serialized_cache_version_change_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        envelope = candidate.serialize_reader_handle(handle)
        envelope["cache_contract_version"] = "STALE"
        envelope["serialized_handle_sha256"] = candidate.hash_object(
            candidate._without(envelope, "serialized_handle_sha256"))
        self.assertCode("SERIALIZED_HANDLE_CACHE_VERSION_MISMATCH",
                        candidate.restore_reader_handle, envelope, capability=self.capability)

    def test_serialized_correction_prefix_change_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(True), capability=self.capability)
        envelope = candidate.serialize_reader_handle(handle)
        envelope["resolver_input"]["corrections"] = []
        envelope["serialized_handle_sha256"] = candidate.hash_object(
            candidate._without(envelope, "serialized_handle_sha256"))
        self.assertCode("SERIALIZED_HANDLE_BINDING_MISMATCH", candidate.restore_reader_handle,
                        envelope, capability=self.capability)

    def test_queue_rejects_inactive_document(self):
        handle = candidate.open_synthetic_reader(base_input(True), capability=self.capability)
        self.assertCode("QUEUE_REFERENCES_INACTIVE_DOCUMENT", candidate.rebuild_synthetic_queue,
                        reader_handle=handle, requested_by_issuer={"I1": ["D2"]},
                        capability=self.capability)

    def test_queue_rejects_issuer_mismatch(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        self.assertCode("QUEUE_ISSUER_MISMATCH", candidate.rebuild_synthetic_queue,
                        reader_handle=handle, requested_by_issuer={"WRONG": ["D1"]},
                        capability=self.capability)

    def test_queue_rejects_duplicate_document(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        self.assertCode("QUEUE_DUPLICATE_DOC", candidate.rebuild_synthetic_queue,
                        reader_handle=handle, requested_by_issuer={"I1": ["D1", "D1"]},
                        capability=self.capability)

    def test_stale_queue_body_tamper_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        queue = candidate.rebuild_synthetic_queue(reader_handle=handle,
                                                  requested_by_issuer={"I1": ["D1"]},
                                                  capability=self.capability)
        queue["companies"][0]["new_report_doc_ids"] = ["FAKE"]
        self.assertCode("STALE_QUEUE_HASH_MISMATCH", candidate.rebuild_synthetic_queue,
                        reader_handle=handle, requested_by_issuer={"I1": ["D1"]},
                        previous_queue=queue, capability=self.capability)

    def test_stale_queue_cache_version_is_rejected(self):
        handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        queue = candidate.rebuild_synthetic_queue(reader_handle=handle,
                                                  requested_by_issuer={"I1": ["D1"]},
                                                  capability=self.capability)
        queue["cache_contract_version"] = "STALE"
        queue["queue_sha256"] = candidate.hash_object(candidate._without(queue, "queue_sha256"))
        self.assertCode("STALE_QUEUE_CACHE_VERSION_MISMATCH", candidate.rebuild_synthetic_queue,
                        reader_handle=handle, requested_by_issuer={"I1": ["D1"]},
                        previous_queue=queue, capability=self.capability)

    def test_stale_queue_correction_binding_is_rejected(self):
        old_handle = candidate.open_synthetic_reader(base_input(), capability=self.capability)
        old_queue = candidate.rebuild_synthetic_queue(reader_handle=old_handle,
                                                      requested_by_issuer={"I1": ["D1"]},
                                                      capability=self.capability)
        new_handle = candidate.open_synthetic_reader(base_input(True), capability=self.capability)
        self.assertCode("STALE_QUEUE_EFFECTIVE_VIEW_BINDING", candidate.rebuild_synthetic_queue,
                        reader_handle=new_handle, requested_by_issuer={"I1": ["D1"]},
                        previous_queue=old_queue, capability=self.capability)


class CoverageTests(CandidateTestCase):
    def inventory(self) -> dict:
        return load_json("t46_trusted_reader_inventory.json")

    def request(self, inventory: dict | None = None) -> dict:
        return {"inventory": self.inventory() if inventory is None else inventory,
                "expectedInventorySha256": candidate.TRUSTED_READER_INVENTORY_SHA256}

    def test_trusted_inventory_hash_is_exact(self):
        self.assertEqual(candidate.TRUSTED_READER_INVENTORY_SHA256,
                         candidate.hash_object(self.inventory()))

    def test_current_inventory_is_blocked_with_12_critical_readers(self):
        result = candidate.evaluate_reader_coverage(self.request())
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(14, result["reader_count"])
        self.assertEqual(12, result["critical_reader_count"])
        self.assertEqual(12, result["blocker_count"])
        self.assertEqual(["R08_AUTO_LOOP_DYNAMIC_EXTRACTOR", "R14_EXTERNAL_OR_UNVERSIONED_READER"],
                         result["unresolved_identity_ids"])
        self.assertFalse(result["formal_coverage_authorized"])

    def test_empty_inventory_cannot_pass(self):
        inventory = self.inventory()
        inventory["entries"] = []
        self.assertCode("READER_INVENTORY_HASH_MISMATCH", candidate.evaluate_reader_coverage,
                        self.request(inventory))

    def test_missing_critical_reader_cannot_pass(self):
        inventory = self.inventory()
        inventory["entries"] = inventory["entries"][:-1]
        self.assertCode("READER_INVENTORY_HASH_MISMATCH", candidate.evaluate_reader_coverage,
                        self.request(inventory))

    def test_caller_supplied_reduced_hash_is_untrusted(self):
        inventory = self.inventory()
        inventory["entries"] = inventory["entries"][:1]
        request = {"inventory": inventory, "expectedInventorySha256": candidate.hash_object(inventory)}
        self.assertCode("READER_INVENTORY_EXPECTED_HASH_UNTRUSTED",
                        candidate.evaluate_reader_coverage, request)

    def test_migration_status_change_invalidates_inventory_hash(self):
        inventory = self.inventory()
        inventory["entries"][0]["migration_status"] = "MIGRATED_VERIFIED"
        self.assertCode("READER_INVENTORY_HASH_MISMATCH", candidate.evaluate_reader_coverage,
                        self.request(inventory))


if __name__ == "__main__":
    unittest.main()
