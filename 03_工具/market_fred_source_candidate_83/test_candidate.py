from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("candidate83", ROOT / "candidate.py")
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)
BUNDLE = json.loads((ROOT / "fixture_bundle.json").read_text(encoding="utf-8"))


class CandidateTests(unittest.TestCase):
    def test_default_disabled_does_not_require_or_read_inputs(self):
        result = candidate.run_candidate({"bad": "ignored"}, enabled=False, allowed_root="does-not-exist")
        self.assertEqual(result["candidate_status"], "CANDIDATE_DISABLED")
        self.assertEqual(result["formal_publish_status"], "FORMAL_PUBLISH_NOT_ELIGIBLE")

    def test_import_has_no_side_effect_and_no_output(self):
        code = f"import importlib.util; s=importlib.util.spec_from_file_location('x', r'{ROOT / 'candidate.py'}'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_valid_hash_bound_fixture_is_offline_only(self):
        result = candidate.run_candidate(BUNDLE, enabled=True, allowed_root=ROOT)
        self.assertEqual(result["provider_format_status"], "PROVIDER_FORMAT_VERIFIED")
        self.assertEqual(result["pagination_status"], "PAGINATION_COMPLETE")
        self.assertEqual(result["source_publication_available_status"], "SOURCE_PUBLICATION_AVAILABLE_NOT_VERIFIED")
        self.assertEqual(result["currentness_status"], "DATA_INSUFFICIENT")
        self.assertEqual(result["formal_publish_status"], "FORMAL_PUBLISH_NOT_ELIGIBLE")

    def test_unsupported_adapter_fails_closed(self):
        bundle = copy.deepcopy(BUNDLE)
        bundle["adapter"] = "ALFRED_UNAPPROVED"
        with self.assertRaisesRegex(candidate.CandidateError, "UNSUPPORTED_ADAPTER_FAIL_CLOSED"):
            candidate.run_candidate(bundle, enabled=True, allowed_root=ROOT)

    def test_outside_allowed_root_is_rejected(self):
        bundle = copy.deepcopy(BUNDLE)
        bundle["series"]["path"] = ".."
        with self.assertRaisesRegex(candidate.CandidateError, "SOURCE_PATH_OUTSIDE_ALLOWED_ROOT"):
            candidate.run_candidate(bundle, enabled=True, allowed_root=ROOT)

    def test_missing_source_path_fails_closed(self):
        bundle = copy.deepcopy(BUNDLE)
        bundle["series"]["path"] = "missing.json"
        with self.assertRaises(FileNotFoundError):
            candidate.run_candidate(bundle, enabled=True, allowed_root=ROOT)

    def test_source_byte_tamper_rejected_even_when_valid_json(self):
        binding = copy.deepcopy(BUNDLE["pages"][0]["response"])
        binding["sha256"] = BUNDLE["pages"][1]["response"]["sha256"]
        with self.assertRaisesRegex(candidate.CandidateError, "SOURCE_BYTE_SHA256_MISMATCH"):
            candidate._read_bound_file(ROOT.resolve(), binding, "page[0].response")

    def test_request_metadata_tamper_rejected(self):
        bundle = copy.deepcopy(BUNDLE)
        bundle["pages"][0]["request"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(candidate.CandidateError, "SOURCE_BYTE_SHA256_MISMATCH"):
            candidate.run_candidate(bundle, enabled=True, allowed_root=ROOT)

    def test_cli_default_is_disabled(self):
        completed = subprocess.run([sys.executable, str(ROOT / "candidate.py")], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["candidate_status"], "CANDIDATE_DISABLED")


if __name__ == "__main__":
    unittest.main()
