from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import pit_freeze_only as pit
import research_snapshot

HERE = pathlib.Path(__file__).resolve().parent


def write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(pit.canonical_bytes(row) for row in rows))


def inventory(root: pathlib.Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*") if path.is_file()
    }


class FreezeOnlyPlumbingTests(unittest.TestCase):
    def setUp(self):
        temp_parent = HERE / "_pit38_tmp"
        temp_parent.mkdir(exist_ok=True)
        self.source_tmp = tempfile.TemporaryDirectory(dir=temp_parent)
        self.stage_tmp = tempfile.TemporaryDirectory(dir=temp_parent)
        self.root = pathlib.Path(self.source_tmp.name)
        self.stage_parent = pathlib.Path(self.stage_tmp.name)
        raw = self.root / "raw" / "D1.bin"
        text = self.root / "text" / "D1.txt"
        raw.parent.mkdir(); text.parent.mkdir()
        raw.write_bytes(b"official raw bytes")
        text.write_bytes(b"official text bytes")
        self.docs = [{
            "doc_id": "D1", "issuer_id": "ISSUER_X", "doc_type": "10-Q",
            "official_published_at": "2025-05-01T00:00:00+00:00",
            "available_at": "2025-05-01T00:00:00+00:00",
            "fetched_at": "2025-05-01T00:00:00+00:00",
            "raw_path": "raw/D1.bin", "raw_sha256": pit.sha256(raw.read_bytes()),
            "text_path": "text/D1.txt", "text_sha256": pit.sha256(text.read_bytes()),
        }]
        self.extractions = [{
            "extraction_id": "E1", "doc_ids": ["D1"],
            "available_at": "2025-05-02T00:00:00+00:00",
        }]
        self.values = [{
            "value_id": "V1", "field": "C6", "scope": "ISSUER_X", "value": "C",
            "doc_ids": ["D1"], "extraction_ids": ["E1"],
            "available_at": "2025-05-03T00:00:00+00:00",
        }]

    def tearDown(self):
        self.source_tmp.cleanup()
        self.stage_tmp.cleanup()

    def _spec(self, *, voids=(), reviews=(), cutoff="2025-09-01T00:00:00+00:00",
              witnesses=True, trust_mode="SYNTHETIC_TEST_ONLY") -> dict:
        paths = {
            "docs_ledger": "docs.jsonl", "ai_extractions": "extractions.jsonl",
            "ai_field_values": "values.jsonl", "ai_field_value_voids": "voids.jsonl",
            "ai_reviews": "reviews.jsonl",
        }
        rows = {
            "docs_ledger": self.docs, "ai_extractions": self.extractions,
            "ai_field_values": self.values, "ai_field_value_voids": list(voids),
            "ai_reviews": list(reviews),
        }
        for name, relative in paths.items():
            write_jsonl(self.root / relative, rows[name])
        docs_data = (self.root / paths["docs_ledger"]).read_bytes()
        trust = pit.synthetic_docs_trust(docs_data, len(self.docs))
        trust["mode"] = trust_mode
        spec = {"cutoff_at": cutoff, "inputs": paths, "docs_trust": trust,
                "historical_witnesses": {}}
        if witnesses:
            cutoff_dt = pit._parse_time(cutoff, "test")
            for ledger in pit.EVENT_LEDGERS:
                data = (self.root / paths[ledger]).read_bytes()
                selected, prefix = pit._event_prefix(data, rows[ledger], cutoff_dt, ledger)
                self.assertEqual(len(selected), prefix["effective_row_count"])
                spec["historical_witnesses"][ledger] = pit.synthetic_witness(
                    ledger, pit.sha256(data), prefix, cutoff)
        return spec

    def _freeze(self, spec: dict, name="stage"):
        return pit.freeze_only(self.root, spec, self.stage_parent / name,
                               allow_synthetic_test_authority=True)

    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(pit.FreezeOnlyError) as cm:
            fn(*args, **kwargs)
        self.assertEqual(code, cm.exception.code)

    def test_valid_freeze_and_replay_preserve_source_bytes_and_metadata(self):
        spec = self._spec()
        before = inventory(self.root)
        manifest = self._freeze(spec)
        verification = pit.verify_freeze(self.root, manifest, self.stage_parent / "stage")
        self.assertEqual(before, inventory(self.root))
        self.assertEqual(set(pit.REQUIRED_LEDGERS), set(manifest["inputs"]))
        self.assertEqual("NOT_GRANTED_FREEZE_ONLY", manifest["pit_certification"])
        self.assertEqual({"doc_id", "raw", "text"}, set(manifest["document_artifacts"][0]))
        self.assertEqual("PASS", verification["staging_integrity"])
        self.assertEqual(0, verification["source_drift_count"])

    def test_missing_historical_witness_fails_closed_before_staging(self):
        spec = self._spec(witnesses=False)
        stage = self.stage_parent / "missing-witness"
        self.assert_code("HISTORICAL_PREFIX_PROVENANCE_UNVERIFIED", pit.freeze_only,
                         self.root, spec, stage, allow_synthetic_test_authority=True)
        self.assertFalse(stage.exists())

    def test_witness_tamper_is_detected(self):
        spec = self._spec()
        spec["historical_witnesses"]["ai_reviews"]["effective_row_count"] = 99
        self.assert_code("HISTORICAL_WITNESS_HASH_MISMATCH", self._freeze, spec)

    def test_self_declared_production_witness_is_not_authority(self):
        spec = self._spec()
        witness = spec["historical_witnesses"]["ai_reviews"]
        witness["witness_type"] = "APPEND_JOURNAL_CHECKPOINT"
        signed = {key: witness.get(key) for key in (
            "witness_type", "scope", "ledger", "source_sha256",
            "prefix_sha256", "effective_row_count", "cutoff_at")}
        witness["witness_sha256"] = pit.sha256(pit.canonical_bytes(signed))
        self.assert_code("HISTORICAL_PREFIX_AUTHORITY_REQUIRED", self._freeze, spec)

    def test_t46_live_row_count_is_blocked_without_reading_artifacts(self):
        self.docs = [{"doc_id": f"D{i}"} for i in range(pit.T46_LIVE_ROW_COUNT)]
        spec = self._spec()
        self.assert_code("T46_LIVE_LEDGER_NOT_AUTHORIZED", self._freeze, spec)

    def test_t46_clean_prefix_identity_is_not_authority(self):
        spec = self._spec(trust_mode="T46_VERIFIED_CLEAN_PREFIX")
        self.assert_code("T46_CLEAN_PREFIX_AUTHORITY_REQUIRED", self._freeze, spec)

    def test_future_void_is_frozen_but_not_effective(self):
        spec = self._spec(voids=[{
            "void_id": "VOID1", "voided_value_id": "V1",
            "created_at": "2026-01-01T00:00:00+00:00",
        }])
        manifest = self._freeze(spec)
        prefix = manifest["event_prefixes"]["ai_field_value_voids"]
        self.assertEqual((1, 0, 1), (prefix["source_row_count"], prefix["effective_row_count"],
                                     prefix["future_row_count"]))

    def test_future_review_is_frozen_but_not_effective(self):
        spec = self._spec(reviews=[{
            "review_id": "R1", "value_id": "V1",
            "created_at": "2026-01-01T00:00:00+00:00",
        }])
        manifest = self._freeze(spec)
        self.assertEqual(0, manifest["event_prefixes"]["ai_reviews"]["effective_row_count"])

    def test_non_monotonic_event_ledger_fails_closed(self):
        spec = self._spec()
        write_jsonl(self.root / "reviews.jsonl", [
            {"review_id": "R2", "value_id": "V1", "created_at": "2025-08-20T00:00:00+00:00"},
            {"review_id": "R1", "value_id": "V1", "created_at": "2025-08-19T00:00:00+00:00"},
        ])
        self.assert_code("EVENT_LEDGER_NOT_APPEND_TIME_ORDERED", self._freeze, spec)

    def test_void_cannot_predate_target_availability(self):
        spec = self._spec(voids=[{
            "void_id": "VOID1", "voided_value_id": "V1",
            "created_at": "2025-05-02T00:00:00+00:00",
        }])
        self.assert_code("VOID_PRECEDES_TARGET_AVAILABILITY", self._freeze, spec)

    def test_review_cannot_predate_target_availability(self):
        spec = self._spec(reviews=[{
            "review_id": "R1", "value_id": "V1",
            "created_at": "2025-05-02T00:00:00+00:00",
        }])
        self.assert_code("REVIEW_PRECEDES_TARGET_AVAILABILITY", self._freeze, spec)

    def test_review_without_provable_target_fails_closed(self):
        spec = self._spec(reviews=[{
            "review_id": "R1", "flag": "GENERIC",
            "created_at": "2025-05-04T00:00:00+00:00",
        }])
        self.assert_code("REVIEW_TARGET_PROVENANCE_UNVERIFIED", self._freeze, spec)

    def test_review_effective_time_after_created_is_conflict(self):
        spec = self._spec(reviews=[{
            "review_id": "R1", "value_id": "V1",
            "created_at": "2025-05-04T00:00:00+00:00",
            "effective_as_of": "2025-05-05T00:00:00+00:00",
        }])
        self.assert_code("REVIEW_TIME_CONFLICT", self._freeze, spec)

    def test_artifact_hash_mismatch_fails_before_staging(self):
        self.docs[0]["raw_sha256"] = "0" * 64
        spec = self._spec()
        self.assert_code("DOCUMENT_HASH_MISMATCH", self._freeze, spec)

    def test_staging_inside_source_is_rejected(self):
        spec = self._spec()
        self.assert_code("STAGING_INSIDE_SOURCE_ROOT", pit.freeze_only, self.root, spec,
                         self.root / "stage", allow_synthetic_test_authority=True)

    def test_manifest_tamper_is_detected(self):
        manifest = self._freeze(self._spec())
        manifest["dependencies"]["documents"] = 999
        self.assert_code("FREEZE_MANIFEST_DERIVATION_MISMATCH", pit.verify_freeze,
                         self.root, manifest, self.stage_parent / "stage")

    def test_manifest_prefix_tamper_with_rehashed_derivation_is_recomputed(self):
        manifest = self._freeze(self._spec())
        manifest["event_prefixes"]["ai_reviews"]["future_row_count"] = 9
        signed = {key: value for key, value in manifest.items() if key != "derivation_sha256"}
        manifest["derivation_sha256"] = pit.sha256(pit.canonical_bytes(signed))
        self.assert_code("FREEZE_EVENT_PREFIX_MISMATCH", pit.verify_freeze,
                         self.root, manifest, self.stage_parent / "stage")

    def test_default_cli_still_uses_existing_formal_path(self):
        with mock.patch.object(research_snapshot, "formal_run", return_value={"status": "FORMAL_SENTINEL"}) as formal:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, research_snapshot.main([]))
        formal.assert_called_once_with()

    def test_existing_dry_run_path_is_unchanged(self):
        with mock.patch.object(research_snapshot, "dry_run", return_value={"status": "DRY_SENTINEL"}) as dry:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, research_snapshot.main(["--dry-run"]))
        dry.assert_called_once_with()

    def test_all_five_ledger_bytes_are_preserved_in_staging(self):
        spec = self._spec()
        manifest = self._freeze(spec)
        stage = self.stage_parent / "stage"
        for name, source_relative in spec["inputs"].items():
            meta = manifest["inputs"][name]
            staged = stage / meta["staging_path"]
            self.assertTrue(staged.is_file(), name)
            self.assertEqual((self.root / source_relative).read_bytes(), staged.read_bytes())

    def test_tampered_staged_manifest_is_rejected(self):
        manifest = self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        staged_manifest = json.loads((stage / "freeze_manifest.json").read_text(encoding="utf-8"))
        staged_manifest["cutoff_at"] = "2025-08-01T00:00:00+00:00"
        (stage / "freeze_manifest.json").write_bytes(pit.canonical_bytes(staged_manifest))
        self.assert_code("FREEZE_MANIFEST_DERIVATION_MISMATCH", pit.verify_freeze,
                         self.root, manifest, stage)

    def test_rehashed_staged_manifest_derivation_is_recomputed_from_staging(self):
        self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        path = stage / "freeze_manifest.json"
        staged = json.loads(path.read_text(encoding="utf-8"))
        staged["event_prefixes"]["ai_reviews"]["future_row_count"] = 9
        signed = {key: value for key, value in staged.items() if key != "derivation_sha256"}
        staged["derivation_sha256"] = pit.sha256(pit.canonical_bytes(signed))
        path.write_bytes(pit.canonical_bytes(staged))
        self.assert_code("FREEZE_EVENT_PREFIX_MISMATCH", pit.verify_freeze,
                         self.root, staged, stage)

    def test_missing_staged_manifest_is_rejected(self):
        manifest = self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        (stage / "freeze_manifest.json").unlink()
        self.assert_code("STAGED_MANIFEST_MISSING", pit.verify_freeze,
                         self.root, manifest, stage)

    def test_document_identifier_aliases_map_to_unique_safe_paths(self):
        raw2 = self.root / "raw" / "D2.bin"
        text2 = self.root / "text" / "D2.txt"
        raw2.write_bytes(b"second raw")
        text2.write_bytes(b"second text")
        self.docs[0]["doc_id"] = "A/../B"
        self.docs.append({
            "doc_id": "B", "issuer_id": "ISSUER_Y", "doc_type": "10-Q",
            "official_published_at": "2025-05-01T00:00:00+00:00",
            "available_at": "2025-05-01T00:00:00+00:00",
            "fetched_at": "2025-05-01T00:00:00+00:00",
            "raw_path": "raw/D2.bin", "raw_sha256": pit.sha256(raw2.read_bytes()),
            "text_path": "text/D2.txt", "text_sha256": pit.sha256(text2.read_bytes()),
        })
        self.extractions[0]["doc_ids"] = ["A/../B"]
        self.values[0]["doc_ids"] = ["A/../B"]
        spec = self._spec()
        stage = self.stage_parent / "collision"
        manifest = pit.freeze_only(self.root, spec, stage,
                                   allow_synthetic_test_authority=True)
        paths = {item[field]["staging_path"]
                 for item in manifest["document_artifacts"] for field in ("raw", "text")}
        self.assertEqual(4, len(paths))
        self.assertTrue(all(".." not in pathlib.PurePosixPath(path).parts for path in paths))
        self.assertEqual("PASS", pit.verify_freeze(self.root, manifest, stage)["staging_integrity"])

    def test_unprovable_staging_mapping_collision_fails_before_staging(self):
        raw2 = self.root / "raw" / "D2.bin"
        text2 = self.root / "text" / "D2.txt"
        raw2.write_bytes(b"second raw")
        text2.write_bytes(b"second text")
        self.docs.append({
            "doc_id": "D2", "issuer_id": "ISSUER_Y", "doc_type": "10-Q",
            "official_published_at": "2025-05-01T00:00:00+00:00",
            "available_at": "2025-05-01T00:00:00+00:00",
            "fetched_at": "2025-05-01T00:00:00+00:00",
            "raw_path": "raw/D2.bin", "raw_sha256": pit.sha256(raw2.read_bytes()),
            "text_path": "text/D2.txt", "text_sha256": pit.sha256(text2.read_bytes()),
        })
        spec = self._spec()
        stage = self.stage_parent / "forced-collision"
        with mock.patch.object(pit, "_doc_staging_component", return_value="0" * 64):
            self.assert_code("STAGING_PATH_COLLISION", pit.freeze_only,
                             self.root, spec, stage, allow_synthetic_test_authority=True)
        self.assertFalse(stage.exists())

    def test_staging_only_replay_survives_source_drift_and_reports_it(self):
        manifest = self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        (self.root / "values.jsonl").write_bytes(b'{"changed":true}\n')
        (self.root / "raw" / "D1.bin").write_bytes(b"changed source raw")
        checked = pit.verify_freeze(self.root, manifest, stage)
        self.assertEqual("PASS", checked["staging_integrity"])
        self.assertEqual(2, checked["source_drift_count"])
        replay = pit.verify_freeze(None, manifest, stage)
        self.assertEqual("PASS", replay["staging_integrity"])
        self.assertEqual("NOT_CHECKED", replay["source_drift"][0]["status"])

    def test_staged_ledger_tamper_is_rejected(self):
        manifest = self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        ledger = stage / manifest["inputs"]["ai_field_values"]["staging_path"]
        ledger.write_bytes(ledger.read_bytes() + b'{"value_id":"EVIL"}\n')
        self.assert_code("FROZEN_LEDGER_IDENTITY_MISMATCH", pit.verify_freeze,
                         self.root, manifest, stage)

    def test_explicit_staged_manifest_digest_is_checked_without_granting_authority(self):
        manifest = self._freeze(self._spec())
        stage = self.stage_parent / "stage"
        digest = pit.sha256((stage / "freeze_manifest.json").read_bytes())
        checked = pit.verify_freeze(None, manifest, stage,
                                    expected_staged_manifest_sha256=digest)
        self.assertEqual("NOT_GRANTED_FREEZE_ONLY", checked["external_trusted_time_authority"])
        self.assert_code("TRUSTED_MANIFEST_DIGEST_MISMATCH", pit.verify_freeze,
                         None, manifest, stage, expected_staged_manifest_sha256="0" * 64)


if __name__ == "__main__":
    unittest.main()
