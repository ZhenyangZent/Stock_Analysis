import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("market_brief.py")
SPEC = importlib.util.spec_from_file_location("market_brief", MODULE_PATH)
market_brief = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = market_brief
SPEC.loader.exec_module(market_brief)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


class MarketBriefTests(unittest.TestCase):
    RUN_ID = "SNAP_20261019T174908Z_275da2dd"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "00_規範" / "rules_歷史").mkdir(parents=True)
        self.snapshot_root = self.root / "02_執行紀錄" / "影子快照"
        self.run_dir = self.snapshot_root / self.RUN_ID
        self.run_dir.mkdir(parents=True)
        self.rules = {"protocol_version": "1.13.0", "fixture": True}
        self.rules_bytes = compact(self.rules)
        (self.root / "00_規範" / "rules_歷史" / "rules_1.13.0.json").write_bytes(self.rules_bytes)
        (self.root / "00_規範" / "rules.json").write_bytes(compact({"protocol_version": "1.13.2", "fixture": True}))
        self.snapshot = {
            "run_id": self.RUN_ID,
            "schema_version": "S1A_RESEARCH_SNAPSHOT_0.2",
            "derivation_version": "S1A_DERIVATION_0.2",
            "data_cutoff_at": "2026-10-19T17:49:08+00:00",
            "anchors": {"TW": "2026-10-19", "US": "2026-10-16"},
            "session_binding": {
                "TW": {"session_date": "2026-10-19"},
                "US": {"session_date": "2026-10-16"},
            },
            "limitations": ["FIXTURE_LIMIT"],
            "market": {
                "assessment": {
                    "filled": 7,
                    "score": -1,
                    "scores": {"M1": -1, "M2": 0, "M3": 0, "M4": 0, "M5": 0, "M6": 1, "M7": -1},
                    "status": "黃燈",
                },
                "values": {
                    "M1": {"value": "升息", "data_date": "2026-09-16", "reason": "fixture"},
                    "M2": {"value": 9.0, "data_date": "2026-10-15", "reason": "fixture"},
                    "M3": {"value": -1.0, "data_date": "2026-10-14", "reason": "fixture"},
                    "M4": {"value": 0.0, "data_date": "2026-09-01", "reason": "fixture"},
                    "M5": {"value": -0.02, "data_date": "2026-09-01", "reason": "fixture"},
                    "M6": {"value": 5.6, "data_date": "2026-10-16", "reason": "fixture"},
                    "M7": {"value": 53.0, "data_date": "2026-10-15", "reason": "fixture"},
                },
            },
            "theme_breadth": {
                "memory": {
                    "name": "記憶體",
                    "value": 100.0,
                    "score": 1,
                    "n_have": 1,
                    "n_total": 1,
                    "accelerating": ["MU"],
                    "decelerating": [],
                    "missing": [],
                }
            },
            "activated_fields": {
                "themes": {"memory": {"I2": {"value": 1}}},
                "companies": {},
            },
            "companies": [
                {
                    "ticker": "MU",
                    "market": "US",
                    "anchor_session": "2026-10-16",
                    "price_status": "OK",
                    "formal_readiness": {"C5": True, "C6": True, "I2": True},
                    "trend": {"C1": 10.0, "C2": 2.0, "C3": 1.0, "latest_period_end": "2026-08-31"},
                    "health": {"status": "穩健"},
                    "valuation_v1": {"status": "OK", "value": 1.2},
                    "attribution": [
                        {
                            "status": "OK",
                            "window_days": 20,
                            "classification": "主要是大盤",
                            "total_return": -0.1,
                            "benchmark_return": -0.08,
                            "start_date": "2026-09-18",
                            "end_date": "2026-10-16",
                        }
                    ],
                    "risk": {
                        "status": "影子模式",
                        "data_cutoff": "2026-10-16",
                        "vol": {"20": 0.4},
                        "forecasts": {"HS": {"20": {"var99": 0.3}}},
                        "max_drawdown": 0.5,
                    },
                }
            ],
        }
        self.input_manifest = {
            "schema_version": "S1A_RESEARCH_SNAPSHOT_0.2",
            "derivation_version": "S1A_DERIVATION_0.2",
            "data_cutoff_at": self.snapshot["data_cutoff_at"],
            "anchors": copy.deepcopy(self.snapshot["anchors"]),
            "session_binding": copy.deepcopy(self.snapshot["session_binding"]),
            "macro_limitation": "MACRO_PUBLICATION_TIME_NOT_CAPTURED",
            "macro_revision_vintage_available": False,
            "inputs": {
                "rules": {
                    "sha256": hashlib.sha256(self.rules_bytes).hexdigest(),
                    "cas_sha256": hashlib.sha256(self.rules_bytes).hexdigest(),
                    "byte_length": len(self.rules_bytes),
                    "storage": "CAS",
                }
            },
        }
        self.research = b"# historical fixture\n"
        self.resign()

    def tearDown(self):
        self.temp.cleanup()

    def resign(self):
        payload_data = {
            "input_manifest.json": compact(self.input_manifest),
            "research.md": self.research,
            "snapshot.json": compact(self.snapshot),
        }
        for name, data in payload_data.items():
            (self.run_dir / name).write_bytes(data)
        payloads = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data)}
            for name, data in payload_data.items()
        }
        manifest = {
            "run_id": self.RUN_ID,
            "input_manifest_sha256": payloads["input_manifest.json"]["sha256"],
            "status": "OK",
            "payloads": payloads,
            "errors": [],
            "recovered_interrupted": [],
        }
        manifest_bytes = compact(manifest)
        (self.run_dir / "manifest.json").write_bytes(manifest_bytes)
        marker = {
            "run_id": self.RUN_ID,
            "committed_at": "2026-10-19T17:49:23+00:00",
            "published_at": "2026-10-19T17:49:23+00:00",
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "snapshot_sha256": payloads["snapshot.json"]["sha256"],
            "research_page_sha256": payloads["research.md"]["sha256"],
        }
        (self.run_dir / "COMMITTED.json").write_bytes(compact(marker))

    def load(self):
        return market_brief.load_committed_snapshot(self.RUN_ID, project_root=self.root, snapshot_root=self.snapshot_root)

    def assert_code(self, code, function):
        with self.assertRaises(market_brief.BriefError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_requires_exact_run_id_and_blocks_traversal(self):
        self.assert_code(
            "RUN_ID_FORMAT_GATE",
            lambda: market_brief.load_committed_snapshot("../SNAP_bad", project_root=self.root, snapshot_root=self.snapshot_root),
        )

    def test_valid_fixture_reproduces_historical_market_and_warnings(self):
        brief = market_brief.build_brief(self.load())
        self.assertEqual(brief["mode"], "HISTORICAL_ONLY")
        self.assertFalse(brief["current_claim"])
        self.assertEqual(brief["market"]["historical_light"], "黃燈")
        self.assertEqual(brief["market"]["historical_score"], -1)
        scores = {row["id"]: row["score"] for row in brief["market"]["indicators"]}
        self.assertEqual((scores["M1"], scores["M6"], scores["M7"]), (-1, 1, -1))
        self.assertEqual(brief["predictive_validity"]["status"], "NOT_VALIDATED")
        self.assertIsNone(brief["predictive_validity"]["confidence_percentage"])
        self.assertEqual(brief["repository_rules"]["version"], "1.13.2")
        self.assertFalse(brief["repository_rules"]["used_to_reinterpret_snapshot"])
        markdown = market_brief.render_markdown(brief)
        self.assertIn("HISTORICAL_ONLY", markdown)
        self.assertIn("M4/M5", markdown)
        self.assertIn("BLOCKED_T46_CURRENTNESS", markdown)
        self.assertNotIn("CURRENT</", markdown)

    def test_hash_mismatch_fails_closed(self):
        (self.run_dir / "snapshot.json").write_bytes(b"{}\n")
        self.assert_code("PAYLOAD_SHA256_GATE", self.load)

    def test_missing_payload_fails_closed(self):
        (self.run_dir / "research.md").unlink()
        self.assert_code("REQUIRED_FILE_MISSING", self.load)

    def test_mixed_snapshot_run_id_fails_closed(self):
        self.snapshot["run_id"] = "SNAP_20261018T174908Z_aaaaaaaa"
        self.resign()
        self.assert_code("MIXED_SNAPSHOT_RUN_ID_GATE", self.load)

    def test_cutoff_mismatch_fails_closed(self):
        self.input_manifest["data_cutoff_at"] = "2026-10-18T17:49:08+00:00"
        self.resign()
        self.assert_code("CUTOFF_BINDING_GATE", self.load)

    def test_stale_indicator_is_flagged_but_never_current(self):
        self.snapshot["market"]["values"]["M2"]["data_date"] = "2026-09-01"
        self.resign()
        brief = market_brief.build_brief(self.load())
        self.assertIn("M2_STALE_AT_SNAPSHOT_CUTOFF", brief["data_quality"]["quality_flags"])
        row = next(item for item in brief["market"]["indicators"] if item["id"] == "M2")
        self.assertEqual(row["freshness_at_cutoff"], "STALE_AT_SNAPSHOT_CUTOFF")
        self.assertEqual(brief["mode"], "HISTORICAL_ONLY")

    def test_t46_fields_are_historical_frozen_only(self):
        document = market_brief.build_brief(self.load())["document_research"]
        self.assertEqual(document["status"], "BLOCKED_T46_CURRENTNESS")
        self.assertEqual(document["permitted_interpretation"], "HISTORICAL_FROZEN_EVIDENCE_ONLY")
        self.assertFalse(document["current_claim"])
        self.assertEqual(set(document["affected_fields"]), {"C5", "C6", "I2"})

    def test_read_only_source_bytes_and_timestamps_do_not_change(self):
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.run_dir.iterdir() if path.is_file()}
        market_brief.build_brief(self.load())
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.run_dir.iterdir() if path.is_file()}
        self.assertEqual(before, after)

    def test_outputs_cannot_be_written_inside_snapshot_root(self):
        brief = market_brief.build_brief(self.load())
        self.assert_code(
            "OUTPUT_INSIDE_SNAPSHOT_ROOT_GATE",
            lambda: market_brief.write_outputs(
                brief,
                self.run_dir / "brief.md",
                self.root / "brief.json",
                self.snapshot_root,
            ),
        )

    def test_cli_requires_snapshot_run_id(self):
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--markdown-output", str(self.root / "a.md"), "--json-output", str(self.root / "a.json")],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--snapshot-run-id", proc.stderr)

    def test_real_committed_snapshot_acceptance_identity(self):
        root = MODULE_PATH.parents[1]
        run_dir = root / "02_執行紀錄" / "影子快照" / self.RUN_ID
        if not run_dir.is_dir():
            self.skipTest("legal committed snapshot fixture is unavailable")
        verified = market_brief.load_committed_snapshot(self.RUN_ID, project_root=root)
        brief = market_brief.build_brief(verified)
        self.assertEqual(verified.hashes["snapshot.json"], "988062b8c3a6e329034ff1c683f55227a88b1c1c871ad048cab95c450521a383")
        self.assertEqual(verified.rules_version, "1.13.0")
        self.assertEqual(verified.rules_sha256, "31810daca648eafdfe83a6c7323cfda10910b152c3f0c8361b2429fbbe3deb17")
        self.assertEqual(verified.current_rules_version, "1.13.2")
        self.assertEqual(verified.current_rules_sha256, "fe072304ff62092b355d1c9c844227eb3bee1a7abc1475d0888d50291885e4d1")
        self.assertEqual(brief["market"]["historical_light"], "黃燈")
        self.assertEqual(brief["market"]["historical_score"], -1)
        scores = {row["id"]: row["score"] for row in brief["market"]["indicators"]}
        self.assertEqual((scores["M1"], scores["M6"], scores["M7"]), (-1, 1, -1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
