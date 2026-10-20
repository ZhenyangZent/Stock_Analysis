"""回歸測試：每個修過的錯都留一個測試，改程式或規則後必須全部通過。
用法：python 03_工具/test_regression.py
"""
import copy, os, sys, glob, subprocess, unittest, json, pathlib, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_json, load_rules, canonical, score_one
from verdict import judge
from validate import validate

RULES = load_rules()

def F(v):
    return {"value": v, "status": "已開原文", "type": "實績", "source": "https://example.com", "as_of": "2026-10-01", "note": ""}

def good_run(event="無"):
    """每一層都過關的紀錄，預期結論是「可列入研究」。"""
    return {"protocol_version": RULES["protocol_version"], "run_date": "2026-10-05", "info_cutoff": "2026-10-05", "model": "test",
            "market": {"M1": F("降息"), "M2": F(-30), "M3": F(0), "M4": F(0.0), "M5": F(0.0), "M6": F(5), "M7": F(0)},
            "themes": {"memory": {"I1": F(70), "I2": F(1), "I3": F("上漲"), "I4": F("上修")}},
            "companies": [{"ticker": "MU", "name": "美光", "theme": "memory", "maturity_tier": 1, "benchmark": "SOXX",
                           "event_within_days": event,
                           "fields": {"C1": F(50), "C2": F(5), "C3": F(2), "C4": F("高於"), "C5": F("無"), "C6": F("A")},
                           "valuation": {"V1": F(0.9), "V2": F(-5)}}]}

def verdict_of(run):
    return judge(run, RULES)["companies"][0]["verdict"]

class EventGate(unittest.TestCase):
    def test_baseline_passes(self):
        self.assertEqual(verdict_of(good_run()), "可列入研究")
    def test_missing_event_cannot_pass(self):
        self.assertEqual(verdict_of(good_run(None)), "資料不足")
    def test_negative_event_is_invalid_not_pre_event(self):
        self.assertEqual(verdict_of(good_run(-5)), "資料不足")
    def test_non_integer_event_is_invalid(self):
        for bad in (2.5, True, "明天", ""):
            self.assertEqual(verdict_of(good_run(bad)), "資料不足", bad)
    def test_event_boundary(self):
        d = RULES["verdict"]["event_block_days"]
        self.assertEqual(verdict_of(good_run(d)), "觀望：事件前")
        self.assertEqual(verdict_of(good_run(0)), "觀望：事件前")
        self.assertEqual(verdict_of(good_run(d + 1)), "可列入研究")
    def test_validate_rejects_bad_event(self):
        for bad in (None, -5, 2.5):
            errs, _ = validate(good_run(bad), RULES)
            self.assertTrue(any("event_within_days" in e for e in errs), bad)
        self.assertEqual(validate(good_run(), RULES)[0], [])

class InsufficientIsNotNeutral(unittest.TestCase):
    def test_market_insufficient(self):
        run = good_run()
        for k in ("M2", "M3", "M6"):
            run["market"][k] = {"value": None, "status": "未查", "type": "實績", "source": "", "as_of": "", "note": ""}
        self.assertEqual(verdict_of(run), "無法判定：市場資料不足")
    def test_theme_insufficient(self):
        run = good_run()
        for k in ("I1", "I2"):
            run["themes"]["memory"][k] = {"value": None, "status": "未查", "type": "實績", "source": "", "as_of": "", "note": ""}
        self.assertEqual(verdict_of(run), "無法判定：主線資料不足")
    def test_theme_absent(self):
        run = good_run(); run["themes"] = {}
        result = judge(run, RULES)["companies"][0]
        self.assertEqual(result["verdict"], "資料不足")
        self.assertTrue(result["validation_errors"])
    def test_real_wait_still_wait(self):
        run = good_run(); run["companies"][0]["fields"]["C1"]["value"] = 10; run["companies"][0]["fields"]["C2"]["value"] = -5
        run["companies"][0]["fields"]["C3"]["value"] = -0.5; run["companies"][0]["fields"]["C4"]["value"] = "符合"; run["companies"][0]["fields"]["C6"]["value"] = "B"
        self.assertEqual(verdict_of(run), "等待")
    def test_red_market_and_rich_valuation(self):
        run = good_run()
        run["market"].update({"M1": F("升息"), "M2": F(40), "M3": F(15), "M6": F(-10)})
        run["companies"][0]["valuation"] = {"V1": F(2.0), "V2": F(30)}
        self.assertEqual(verdict_of(run), "不行：環境不利且估值偏高")

class EarlierGatesUnchanged(unittest.TestCase):
    def test_tier3(self):
        run = good_run(); run["companies"][0]["maturity_tier"] = 3
        self.assertEqual(verdict_of(run), "不行：尚無營收驗證")
    def test_unverified_revenue(self):
        run = good_run(); run["companies"][0]["fields"]["C1"]["status"] = "僅摘要"
        self.assertEqual(verdict_of(run), "待查證")

class RuleVersionBinding(unittest.TestCase):
    def test_archive_exists_for_every_run(self):
        for p in glob.glob(os.path.join(ROOT, "02_執行紀錄", "*.json")):
            load_rules(load_json(p).get("protocol_version"))  # 找不到會丟例外
    def test_unknown_version_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_rules("0.0.0-不存在")
    def test_archived_thresholds_are_used(self):
        old = load_rules("1.0.0")
        changed = copy.deepcopy(old); changed["company"]["indicators"]["C1"]["rule"]["good_above"] = 999
        run = good_run()
        self.assertNotEqual(judge(run, old)["companies"][0]["company_score"], judge(run, changed)["companies"][0]["company_score"])
    def test_current_rules_archived(self):
        v = RULES["protocol_version"]
        path = os.path.join(ROOT, "00_規範", "rules_歷史", f"rules_{v}.json")
        self.assertTrue(os.path.exists(path), f"目前版 {v} 還沒存到 rules_歷史/")
        self.assertEqual(load_json(path), RULES)

class UnitsAndEntities(unittest.TestCase):
    def test_every_indicator_has_unit(self):
        for layer in ("market", "theme", "company", "valuation"):
            for k, s in RULES[layer]["indicators"].items():
                self.assertIn("unit", s, k)
    def test_fraction_looking_percent_warns(self):
        run = good_run(); run["companies"][0]["fields"]["C1"]["value"] = 0.5
        _, warns = validate(run, RULES)
        self.assertTrue(any("C1" in w and "百分比" in w for w in warns))
    def test_alias_resolves(self):
        self.assertEqual(canonical("2408.TW", RULES), "2408")
        self.assertEqual(canonical("2330", RULES), "2330")   # 2330 與 TSM 是不同證券，不可互轉
        self.assertEqual(canonical("MU", RULES), "MU")
    def test_alias_ticker_rejected(self):
        run = good_run(); run["companies"][0]["ticker"] = "2408.TW"
        self.assertTrue(any("標準代號" in e for e in validate(run, RULES)[0]))
    def test_adr_and_local_share_are_distinct(self):
        run = good_run(); run["companies"][0]["ticker"] = "2330"
        self.assertTrue(any("不同證券" in e for e in validate(run, RULES)[0]))
        self.assertNotIn("2330", RULES["entities"]["aliases"])
    def test_disabled_v2_never_affects_anything(self):
        """V2 停用後：只填 V2 不算有填估值；V2 填多差都不改變分數與結論；不列在缺漏。"""
        from common import score_layer
        self.assertEqual(RULES["valuation"]["indicators"]["V2"]["status"], "DISABLED_BY_USER_DECISION")
        total, detail, filled, missing = score_layer({"V2": F(99)}, RULES["valuation"])
        self.assertEqual((total, filled, missing), (0, 0, ["V1"])); self.assertNotIn("V2", detail)
        base = good_run(); bad = good_run(); none = good_run()
        bad["companies"][0]["valuation"]["V2"] = F(99); del none["companies"][0]["valuation"]["V2"]
        rs = [judge(x, RULES)["companies"][0] for x in (base, bad, none)]
        self.assertEqual({(c["verdict"], c["valuation_score"]) for c in rs}, {("可列入研究", 1)})
        only_v2 = good_run(); only_v2["companies"][0]["valuation"] = {"V2": F(-5)}
        c = judge(only_v2, RULES)["companies"][0]
        self.assertEqual(c["verdict"], "資料不足"); self.assertNotIn("V2", c["why"])
    def test_disabled_v2_validation(self):
        errs, warns = validate(good_run(), RULES)
        self.assertEqual(errs, []); self.assertTrue(any("V2" in w and "已停用" in w for w in warns))
        run = good_run(); del run["companies"][0]["valuation"]["V2"]
        errs, warns = validate(run, RULES)
        self.assertEqual(errs, []); self.assertFalse(any("V2" in w for w in warns))   # 不填也不算錯
    def test_old_rule_versions_still_score_v2(self):
        old = load_rules("1.1.3"); run = good_run(); run["companies"][0]["valuation"]["V2"] = F(99)
        self.assertEqual(judge(run, old)["companies"][0]["valuation_detail"]["V2"], -1)   # 舊紀錄照舊規則重算，不被新決定改寫

    def test_disabled_c4_never_affects_current_company_score(self):
        from common import score_layer
        self.assertEqual(RULES["company"]["indicators"]["C4"]["status"], "DISABLED_BY_USER_DECISION")
        base = good_run(); omitted = good_run(); bad = good_run()
        del omitted["companies"][0]["fields"]["C4"]
        bad["companies"][0]["fields"]["C4"] = F("低於")
        results = [judge(x, RULES)["companies"][0] for x in (base, omitted, bad)]
        self.assertEqual({(x["company_score"], x["verdict"]) for x in results}, {(4, "可列入研究")})
        total, detail, filled, missing = score_layer(base["companies"][0]["fields"], RULES["company"])
        self.assertEqual((total, filled), (4, 5)); self.assertNotIn("C4", detail); self.assertNotIn("C4", missing)

    def test_rules_190_theme_and_maturity_contract(self):
        optical = RULES["themes"]["optical"]
        self.assertNotIn("MRVL", optical["watchlist"])
        self.assertEqual(optical["description_only"], ["MRVL"])
        self.assertIn("MRVL", RULES["themes"]["network_asic"]["watchlist"])
        from data_spine import load_registry
        registry = load_registry()
        by_ticker = {x["ticker"]: x["security_id"] for x in registry["securities"]}
        tracked = {ticker for theme in RULES["themes"].values() for ticker in theme["watchlist"]}
        expected = {by_ticker[ticker] for ticker in tracked}
        self.assertEqual(set(RULES["maturity_tier_by_security"]), expected)
        self.assertEqual(set(RULES["maturity_tier_by_security"].values()), {1})
        self.assertEqual(RULES["maturity_tier_metadata"]["unlisted_security"], "MISSING")

    def test_rules_188_replay_keeps_c4_and_mrvl_optical(self):
        old = load_rules("1.8.8"); run = good_run(); run["protocol_version"] = "1.8.8"
        self.assertEqual(validate(run, old)[0], [])
        result = judge(run, old)["companies"][0]
        self.assertEqual((result["company_score"], result["verdict"]), (5, "可列入研究"))
        self.assertIn("C4", result["company_detail"])
        self.assertIn("MRVL", old["themes"]["optical"]["watchlist"])
        from verdict import render
        self.assertIn("已停用的估值指標：V2", render(judge(good_run(), RULES), RULES))
    def test_valuation_caveat_is_always_shown(self):
        from verdict import render
        self.assertIn("這個參考不準", render(judge(good_run(), RULES), RULES))
    def test_watchlists_use_canonical_ids(self):
        for tid, t in RULES["themes"].items():
            for x in t["watchlist"]:
                self.assertEqual(canonical(x, RULES), x, f"{tid}:{x}")
    def test_bad_values_score_as_missing(self):
        r = RULES["company"]["indicators"]["C1"]["rule"]
        for bad in (None, True, "50", float("nan")):
            self.assertIsNone(score_one(bad, r))

class S2SecondSegment(unittest.TestCase):
    LEGACY_RULES = load_rules("1.9.0")

    def computed_fixture(self):
        """Build an isolated committed snapshot; never depend on live formal runs."""
        import contextlib, tempfile
        from unittest import mock
        import computed_provenance as cp
        import research_snapshot as rs

        @contextlib.contextmanager
        def fixture():
            with tempfile.TemporaryDirectory() as d:
                base = pathlib.Path(d); run_id = "SNAP_TEST_PROVENANCE"; run_dir = base / run_id
                run_dir.mkdir()
                snapshot = {"data_cutoff_at": "2026-10-13T09:41:04+00:00",
                            "companies": [{"security_id": "SEC_MU_US", "ticker": "MU",
                                           "trend": {"C1": 379.0}}]}
                input_manifest = {"environment": {"python": rs.sys.version, "tzdata": "TEST"},
                                  "data_cutoff_at": snapshot["data_cutoff_at"], "inputs": {}}
                payloads = {}
                for name, value in (("snapshot.json", snapshot), ("input_manifest.json", input_manifest)):
                    data = rs.canonical_bytes(value); (run_dir / name).write_bytes(data)
                    payloads[name] = {"sha256": rs.digest(data)}
                manifest_bytes = rs.canonical_bytes({"payloads": payloads})
                (run_dir / "manifest.json").write_bytes(manifest_bytes)
                marker = {"run_id": run_id, "snapshot_sha256": payloads["snapshot.json"]["sha256"],
                          "manifest_sha256": rs.digest(manifest_bytes)}
                (run_dir / "COMMITTED.json").write_bytes(rs.canonical_bytes(marker))
                run = good_run(); run["run_cutoff_at"] = "2026-10-13T18:00:00+08:00"
                run["companies"][0]["fields"]["C1"] = {
                    "value": 379.0, "status": "OFFICIAL_FILING_COMPUTED", "type": "實績",
                    "source": "S1A committed snapshot", "as_of": "2026-10-13", "note": "",
                    "provenance": {"adapter": "S1A_ADAPTER_V1", "snapshot_run_id": run_id,
                                   "snapshot_sha256": marker["snapshot_sha256"],
                                   "field_path": "companies/SEC_MU_US/trend/C1"}}
                cp._REPLAY_CACHE.clear()
                with mock.patch.object(cp, "SNAPSHOT_ROOT", base), \
                     mock.patch.object(rs, "replay_snapshot", return_value=copy.deepcopy(snapshot)):
                    yield run
                cp._REPLAY_CACHE.clear()
        return fixture()

    def _validate_in_fixture(self, mutate=None):
        with self.computed_fixture() as run:
            if mutate: mutate(run)
            run["protocol_version"] = "1.9.0"
            return run, validate(run, self.LEGACY_RULES)[0]

    def _judge_in_fixture(self, mutate=None):
        with self.computed_fixture() as run:
            if mutate: mutate(run)
            run["protocol_version"] = "1.9.0"
            return run, judge(run, self.LEGACY_RULES)["companies"][0]

    def test_official_computed_revenue_replays_and_passes(self):
        run, errs = self._validate_in_fixture()
        self.assertEqual(errs, [])
        _, result = self._judge_in_fixture()
        self.assertEqual(result["verdict"], "可列入研究")
        self.assertEqual(result["validation_errors"], [])

    def test_fake_provenance_is_blocked_inside_judge(self):
        def mutate(run):
            run["companies"][0]["fields"]["C1"]["provenance"]["snapshot_run_id"] = "SNAP_DOES_NOT_EXIST"
        _, result = self._judge_in_fixture(mutate)
        self.assertEqual(result["verdict"], "待查證")
        self.assertTrue(any("DERIVED_SOURCE_MISMATCH" in e for e in result["validation_errors"]))

    def test_replay_value_mismatch_is_blocked(self):
        def mutate(run): run["companies"][0]["fields"]["C1"]["value"] += 1
        _, errs = self._validate_in_fixture(mutate)
        self.assertTrue(any("REPLAY_VALUE_MISMATCH" in e for e in errs))

    def test_cross_security_field_path_is_blocked(self):
        def mutate(run):
            run["companies"][0]["fields"]["C1"]["provenance"]["field_path"] = "companies/SEC_AVGO_US/trend/C1"
        _, errs = self._validate_in_fixture(mutate)
        self.assertTrue(any("SECURITY_ID_MISMATCH" in e for e in errs))

    def test_future_snapshot_is_blocked(self):
        def mutate(run): run["run_cutoff_at"] = "2026-10-13T09:00:00+00:00"
        _, errs = self._validate_in_fixture(mutate)
        self.assertTrue(any("SNAPSHOT_AFTER_RUN_CUTOFF" in e for e in errs))

    def test_rules_without_computed_contract_reject_status(self):
        from computed_provenance import verify_computed_field
        with self.computed_fixture() as run:
            rules = copy.deepcopy(self.LEGACY_RULES); rules["verdict"].pop("computed_revenue_accepted")
            ok, reason = verify_computed_field(run, rules, run["companies"][0], "C1", run["companies"][0]["fields"]["C1"])
        self.assertFalse(ok); self.assertIn("RULE_CONTRACT_MISSING", reason)

    def test_computed_status_outside_rules_allowlist_is_blocked(self):
        run = good_run(); run["protocol_version"] = "1.9.0"; run["market"]["M1"]["status"] = "OFFICIAL_FILING_COMPUTED"
        run["market"]["M1"]["provenance"] = {"adapter":"S1A_ADAPTER_V1"}
        result = judge(run, self.LEGACY_RULES)["companies"][0]
        self.assertEqual(result["verdict"], "待查證")
        self.assertTrue(any("RULE_CONTRACT_" in e for e in result["validation_errors"]))

    def test_missing_maturity_tier_cannot_pass(self):
        run = good_run(); run["companies"][0].pop("maturity_tier")
        self.assertEqual(judge(run, RULES)["companies"][0]["verdict"], "資料不足")

    def test_schema2_mixed_market_has_explicit_error(self):
        from data_spine import validate_schema2_run
        run = {"run_schema_version":2, "run_batch_id":"B", "analysis_session_id":"US_X",
               "run_cutoff_at":"2026-10-13T00:00:00+00:00", "calendar_version_id":"X",
               "calendar_sha256":"x", "companies":[{"ticker":"MU","event_ids":[]},{"ticker":"2408","event_ids":[]}]}
        errs = validate_schema2_run(run, RULES, sessions=[], events=[], observations=[])
        self.assertIn("MIXED_MARKET_RUN:TW,US", errs)

    def test_schema2_requires_run_batch_id(self):
        from data_spine import validate_schema2_run
        run = {"run_schema_version":2, "analysis_session_id":"US_X", "run_cutoff_at":"2026-10-13T00:00:00+00:00",
               "calendar_version_id":"X", "calendar_sha256":"x", "companies":[]}
        self.assertIn("schema 2 缺少 run_batch_id", validate_schema2_run(run, RULES, sessions=[], events=[], observations=[]))

    def test_schema1_historical_result_is_unchanged(self):
        run = load_json(os.path.join(ROOT, "02_執行紀錄", "2026-10-05_claude.json"))
        result = judge(run, load_rules("1.0.0"))
        self.assertEqual([(c["ticker"], c["verdict"], c["company_score"]) for c in result["companies"]],
                         [("MU", "資料不足", 3), ("AVGO", "資料不足", 2)])


class S3AComputedFieldContract(unittest.TestCase):
    """S3a：三層計算欄位只能來自同一份最新、可重播且仍是 canonical prefix 的快照。"""
    def strict_fixture(self):
        import contextlib, tempfile
        from unittest import mock
        import computed_provenance as cp
        import research_snapshot as rs
        from data_spine import load_registry

        @contextlib.contextmanager
        def fixture():
            with tempfile.TemporaryDirectory() as d:
                base = pathlib.Path(d); run_id = "SNAP_S3A_CURRENT"; run_dir = base / run_id
                run_dir.mkdir()
                snapshot = {
                    "data_cutoff_at": "2026-10-13T09:41:04+00:00",
                    "companies": [{"security_id": "SEC_MU_US", "ticker": "MU",
                                   "trend": {"C1": 379.0, "C2": 12.0, "C3": 4.0,
                                             "latest_period_end": "2026-09-03"},
                                   "valuation_v1": {"value": 1.2, "status": "OK",
                                                    "current": {"anchor_date": "2026-10-12"}}}],
                    "market": {"values": {"M1": {"value": "降息", "data_date": "2026-09-30"},
                                            "M2": {"value": -30, "data_date": "2026-10-10"},
                                            "M3": {"value": 0, "data_date": "2026-10-10"},
                                            "M4": {"value": 0.0, "data_date": "2026-09-30"},
                                            "M5": {"value": 0.0, "data_date": "2026-09-30"},
                                            "M6": {"value": 5, "data_date": "2026-10-10"},
                                            "M7": {"value": 0, "data_date": "2026-10-10"}}},
                    "theme_breadth": {"memory": {"value": 70.0}}}
                rules_bytes = rs.canonical_bytes(RULES); events = b'{"event_id":"E1"}\n'
                inputs_meta = {
                    "rules": {"sha256": rs.digest(rules_bytes), "byte_length": len(rules_bytes),
                              "cas_sha256": rs.digest(rules_bytes), "storage": "CAS"},
                    "events": {"sha256": rs.digest(events), "byte_length": len(events),
                               "cas_sha256": rs.digest(events), "storage": "PREFIX_AND_CAS",
                               "prefix_byte_length": len(events), "prefix_sha256": rs.digest(events)}}
                input_manifest = {"environment": {"python": rs.sys.version, "tzdata": "TEST"},
                                  "data_cutoff_at": snapshot["data_cutoff_at"], "inputs": inputs_meta}
                payloads = {}
                for name, value in (("snapshot.json", snapshot), ("input_manifest.json", input_manifest)):
                    data = rs.canonical_bytes(value); (run_dir / name).write_bytes(data)
                    payloads[name] = {"sha256": rs.digest(data)}
                manifest_bytes = rs.canonical_bytes({"payloads": payloads})
                (run_dir / "manifest.json").write_bytes(manifest_bytes)
                marker = {"run_id": run_id, "snapshot_sha256": payloads["snapshot.json"]["sha256"],
                          "manifest_sha256": rs.digest(manifest_bytes)}
                (run_dir / "COMMITTED.json").write_bytes(rs.canonical_bytes(marker))
                archive = base / f"rules_{RULES['protocol_version']}.json"; archive.write_bytes(rules_bytes)
                run = good_run(); run["run_cutoff_at"] = "2026-10-13T18:00:00+08:00"
                run["protocol_version"] = RULES["protocol_version"]
                def derived(value, status, path, as_of="2026-10-13"):
                    return {"value": value, "status": status, "type": "實績", "source": "S1A committed snapshot",
                            "as_of": as_of, "note": "",
                            "provenance": {"adapter": "S1A_ADAPTER_V1", "snapshot_run_id": run_id,
                                           "snapshot_sha256": marker["snapshot_sha256"], "field_path": path}}
                for key, value in (("C1", 379.0), ("C2", 12.0), ("C3", 4.0)):
                    run["companies"][0]["fields"][key] = derived(value, "OFFICIAL_FILING_COMPUTED",
                                                                   f"companies/SEC_MU_US/trend/{key}", "2026-09-03")
                for key, item in snapshot["market"]["values"].items():
                    run["market"][key] = derived(item["value"], "PUBLIC_DATA_COMPUTED", f"market/values/{key}", item["data_date"])
                run["themes"]["memory"]["I1"] = derived(70.0, "OFFICIAL_FILING_COMPUTED", "themes/memory/I1")
                run["companies"][0]["valuation"]["V1"] = derived(
                    1.2, "PUBLIC_DATA_COMPUTED", "companies/SEC_MU_US/valuation/V1", "2026-10-12")
                injected = {"snapshot_root": base, "registry": load_registry(), "canonical_inputs": {"events": events},
                            "rules_archive_path": archive}
                cp._REPLAY_CACHE.clear()
                with mock.patch.object(cp, "SNAPSHOT_ROOT", base), \
                     mock.patch.object(rs, "replay_snapshot", return_value=copy.deepcopy(snapshot)):
                    yield run, injected, snapshot, base, marker
                cp._REPLAY_CACHE.clear()
        return fixture()

    def errors(self, mutate=None, inputs_mutate=None):
        with self.strict_fixture() as (run, inputs, snapshot, base, marker):
            if mutate: mutate(run)
            if inputs_mutate: inputs_mutate(inputs)
            return validate(run, RULES, inputs)[0]

    def test_all_three_layers_and_public_status_pass(self):
        self.assertEqual(self.errors(), [])

    def test_company_market_theme_path_forgery_is_blocked(self):
        cases = [
            lambda r: r["companies"][0]["fields"]["C2"]["provenance"].update(field_path="companies/SEC_AVGO_US/trend/C2"),
            lambda r: r["market"]["M2"]["provenance"].update(field_path="market/values/M3"),
            lambda r: r["themes"]["memory"]["I1"]["provenance"].update(field_path="themes/optical/I1"),
            lambda r: r["companies"][0]["valuation"]["V1"]["provenance"].update(
                field_path="companies/SEC_AVGO_US/valuation/V1")]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.assertTrue(any("DERIVED_SOURCE_MISMATCH" in e for e in self.errors(mutate)))

    def test_replay_value_mismatch_in_each_layer_is_blocked(self):
        cases = [lambda r: r["companies"][0]["fields"]["C3"].update(value=5.0),
                 lambda r: r["market"]["M7"].update(value=1),
                 lambda r: r["themes"]["memory"]["I1"].update(value=71.0),
                 lambda r: r["companies"][0]["valuation"]["V1"].update(value=1.3)]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.assertTrue(any("REPLAY_VALUE_MISMATCH" in e for e in self.errors(mutate)))

    def test_all_computed_fields_must_share_one_snapshot(self):
        def mutate(run): run["market"]["M1"]["provenance"]["snapshot_run_id"] = "SNAP_OTHER"
        self.assertTrue(any("COMPUTED_FIELDS_SNAPSHOT_MISMATCH" in e for e in self.errors(mutate)))

    def test_rules_archive_hash_mismatch_is_blocked(self):
        def mutate(inputs): pathlib.Path(inputs["rules_archive_path"]).write_bytes(b"{}\n")
        self.assertTrue(any("RULES_VERSION_MISMATCH" in e for e in self.errors(inputs_mutate=mutate)))

    def test_canonical_prefix_mismatch_is_blocked(self):
        def mutate(inputs): inputs["canonical_inputs"]["events"] = b'{"event_id":"OTHER"}\n'
        self.assertTrue(any("INPUT_NOT_CANONICAL_PREFIX" in e for e in self.errors(inputs_mutate=mutate)))

    def test_non_latest_snapshot_is_blocked(self):
        def add_later(run, inputs, snapshot, base, marker):
            import research_snapshot as rs
            later = base / "SNAP_S3A_LATER"; later.mkdir()
            later_snapshot = copy.deepcopy(snapshot); later_snapshot["data_cutoff_at"] = "2026-10-13T09:50:00+00:00"
            im = {"environment": {"python": rs.sys.version, "tzdata": "TEST"},
                  "data_cutoff_at": later_snapshot["data_cutoff_at"], "inputs": {}}
            payloads = {}
            for name, value in (("snapshot.json", later_snapshot), ("input_manifest.json", im)):
                data = rs.canonical_bytes(value); (later / name).write_bytes(data); payloads[name] = {"sha256": rs.digest(data)}
            mb = rs.canonical_bytes({"payloads": payloads}); (later / "manifest.json").write_bytes(mb)
            (later / "COMMITTED.json").write_bytes(rs.canonical_bytes(
                {"run_id": later.name, "snapshot_sha256": payloads["snapshot.json"]["sha256"], "manifest_sha256": rs.digest(mb)}))
        with self.strict_fixture() as (run, inputs, snapshot, base, marker):
            add_later(run, inputs, snapshot, base, marker)
            errs = validate(run, RULES, inputs)[0]
        self.assertTrue(any("SNAPSHOT_NOT_LATEST" in e for e in errs))

    def test_legacy_190_contract_does_not_accept_expanded_statuses(self):
        old = load_rules("1.9.0"); run = good_run(); run["protocol_version"] = "1.9.0"
        run["market"]["M1"]["status"] = "PUBLIC_DATA_COMPUTED"
        self.assertTrue(any("RULE_CONTRACT_" in e for e in validate(run, old)[0]))

def xlsx_formulas(path):
    """只用標準函式庫讀 xlsx：回傳 {分頁名: {儲存格: 公式或 None}}（只含有內容的儲存格）。"""
    import zipfile, xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    z = zipfile.ZipFile(path)
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    res = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
        target = rels[sh.get(rid)]
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        cells = {}
        for c in ET.fromstring(z.read(target)).iter("{%s}c" % ns["m"]):
            f = c.find("m:f", ns)
            cells[c.get("r")] = None if f is None else "=" + (f.text or "")
        res[sh.get("name")] = cells
    return res

class Spreadsheet(unittest.TestCase):
    """不依賴 openpyxl，任何 Python 環境都會真的執行，不會被跳過。"""
    def test_event_log_formula_column(self):
        import re
        files = glob.glob(os.path.join(ROOT, "01_共用資料", "*.xlsx"))
        self.assertTrue(files, "找不到試算表")
        for p in files:
            book = xlsx_formulas(p)
            ev = book["Event_Log"]
            rows = sorted({int(re.sub(r"[A-Z]+", "", k)) for k in ev} - {1})
            self.assertTrue(rows)
            for r in rows:
                self.assertIsNone(ev.get(f"H{r}"), f"H{r} 應是手填欄，不可有公式")
                self.assertEqual(ev.get(f"I{r}"), f'=IF(AND(ISNUMBER(G{r}),ISNUMBER(H{r})),G{r}-H{r},"")')
            for name, cells in book.items():  # 全活頁簿不可有自我參照
                for ref, f in cells.items():
                    if f:
                        col, row = re.match(r"([A-Z]+)(\d+)", ref).groups()
                        self.assertIsNone(re.search(r"(?<![A-Z!$])\$?" + col + r"\$?" + row + r"(?!\d)", f), f"{name}!{ref}")

class PriceData(unittest.TestCase):
    def setUp(self):
        import tempfile; self.d = tempfile.mkdtemp()
    def test_total_return_with_dividend_and_split(self):
        from pricedata import total_returns, tr_index
        s = [("2026-01-01", 100.0, None), ("2026-01-02", 98.0, None), ("2026-01-05", 49.0, None)]
        acts = {"2026-01-02": {"div": 2.0, "split": 1.0}, "2026-01-05": {"div": 0.0, "split": 2.0}}
        self.assertEqual([round(r, 9) for _, r in total_returns(s, acts)], [0.0, 0.0])   # 除息與分割都不該算成下跌
        self.assertAlmostEqual(total_returns(s)[1][1], -0.5)                            # 沒有紀錄時才會看到假下跌
        self.assertAlmostEqual(tr_index(s, acts)[-1][1], 1.0)
    def test_append_only_never_overwrites(self):
        from pricedata import append_rows, PRICE_COLS, load_prices
        p = os.path.join(self.d, "prices.csv")
        row = lambda c: {"date": "2026-09-01", "ticker": "MU", "close": c, "volume": 1, "source": "t", "fetched_at": "2026-09-02T00:00:00+00:00"}
        self.assertEqual(append_rows(p, PRICE_COLS, [row(100)]), (1, []))
        n, conflicts = append_rows(p, PRICE_COLS, [row(101)])
        self.assertEqual(n, 0); self.assertEqual(len(conflicts), 1)
        self.assertEqual(load_prices(RULES, p)["MU"][0][1], 100.0)
        self.assertEqual(append_rows(p, PRICE_COLS, [row(100)]), (0, []))
    def test_legacy_three_column_file_upgrades(self):
        from pricedata import append_rows, PRICE_COLS, load_prices
        p = os.path.join(self.d, "prices.csv")
        with open(p, "w", encoding="utf-8") as f: f.write("date,ticker,close\n2026-09-01,TSM,100\n2026-09-01,2330,2440\n2026-09-01,2408.TW,60\n")
        append_rows(p, PRICE_COLS, [{"date": "2026-09-02", "ticker": "TSM", "close": 101, "volume": "", "source": "t", "fetched_at": "2026-09-02T00:00:00+00:00"}])
        px = load_prices(RULES, p)
        self.assertEqual([c for _, c, _ in px["TSM"]], [100.0, 101.0])
        self.assertEqual([c for _, c, _ in px["2330"]], [2440.0])      # 台股普通股不可併進美元的存託憑證
        self.assertEqual([c for _, c, _ in px["2408"]], [60.0])        # 同一檔的不同寫法才合併
    def test_twse_parser_on_real_sample(self):
        from fetch_prices import parse_tw, roc_date
        fields = ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"]
        data = [["115/09/01", "31,855,287", "77,463,413,685", "2,395.00", "2,440.00", "2,390.00", "2,440.00", "+35.00", "65,271", ""],
                ["115/09/02", "1", "1", "--", "--", "--", "--", " 0.00", "0", ""]]
        rows = parse_tw(fields, data, "TSM", "TWSE", "now")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["date"], rows[0]["close"], rows[0]["volume"]), ("2026-09-01", 2440.0, 31855287.0))
        self.assertEqual(roc_date("115/09/30"), "2026-09-30")
        with self.assertRaises(ValueError):
            parse_tw(["日期", "其他"], data, "X", "TWSE", "now")
    def test_listing_covers_watchlists_and_benchmarks(self):
        need = {x for t in RULES["themes"].values() for x in t["watchlist"]} | {RULES["benchmarks"]["default_us"], RULES["benchmarks"]["default_tw"]}
        self.assertEqual(need - set(RULES["prices"]["listing"]), set())

    def test_price_identity_errors_are_reported_not_silently_dropped(self):
        from pricedata import load_prices
        p = os.path.join(self.d, "prices.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("date,ticker,security_id,currency,close,volume,source,fetched_at\n")
            f.write("2026-09-01,TSM,SEC_TSM_US,TWD,100,1,test,now\n")
        prices, issues = load_prices(RULES, p, return_issues=True)
        self.assertEqual(prices, {})
        self.assertEqual(len(issues), 1)
        self.assertIn("CURRENCY_MISMATCH", issues[0]["errors"][0])
        with self.assertRaisesRegex(ValueError, "PRICE_IDENTITY_ERRORS"):
            load_prices(RULES, p)

    def test_price_loader_reads_registry_once(self):
        import pricedata
        from unittest import mock
        p = os.path.join(self.d, "prices.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("date,ticker,security_id,currency,close,volume,source,fetched_at\n")
            f.write("2026-09-01,TSM,SEC_TSM_US,USD,100,1,test,now\n")
            f.write("2026-09-02,TSM,SEC_TSM_US,USD,101,1,test,now\n")
        with mock.patch.object(pricedata, "load_registry", wraps=pricedata.load_registry) as loader:
            pricedata.load_prices(RULES, p)
            self.assertEqual(loader.call_count, 1)

class RiskModule(unittest.TestCase):
    def rets(self, n=3000, sigma=0.02, seed=7):
        import random
        rnd = random.Random(seed)
        return [rnd.gauss(0, sigma) for _ in range(n)]
    def test_shadow_status_and_verdict_isolation(self):
        self.assertEqual(RULES["risk"]["status"], "SHADOW")
        with open(os.path.join(ROOT, "03_工具", "verdict.py"), encoding="utf-8") as fh: src = fh.read()
        self.assertNotIn("risk", src)  # 影子模式不可影響結論
    def test_known_distribution(self):
        import risk
        cfg = dict(RULES["risk"], lookback_days=3000)
        f = risk.forecast(self.rets(), cfg, "HS", 1)
        self.assertAlmostEqual(f["var95"], 1.645 * 0.02, delta=0.003)
        self.assertAlmostEqual(f["var99"], 2.326 * 0.02, delta=0.005)
        self.assertGreater(f["es975"], f["var95"])
        f20 = risk.forecast(self.rets(), cfg, "HS", 20)
        self.assertAlmostEqual(f20["var95"], 1.645 * 0.02 * 20 ** 0.5, delta=0.03)
    def test_insufficient_history_gives_no_forecast(self):
        import risk
        self.assertIsNone(risk.forecast(self.rets(100), RULES["risk"], "HS", 1))
        r = risk.asset_report("X", [(f"2026-01-{i:02d}", 100 + i, 1) for i in range(1, 29)], None, None, RULES["risk"], "2026-01-28", "US")
        self.assertTrue(r["status"].startswith("資料不足")); self.assertNotIn("forecasts", r)
    def test_no_lookahead(self):
        import risk
        rets = self.rets(600)
        a = risk.forecast(rets[:500], RULES["risk"], "EWMA_HS", 5)
        b = risk.forecast((rets[:500] + [-0.5] * 100)[:500], RULES["risk"], "EWMA_HS", 5)
        self.assertEqual(a, b)
    def test_ewma_reacts_to_volatility_jump(self):
        import risk
        calm = self.rets(500, 0.01); stressed = calm[:-20] + self.rets(20, 0.05, seed=3)
        hs, ew = risk.forecast(stressed, RULES["risk"], "HS", 1), risk.forecast(stressed, RULES["risk"], "EWMA_HS", 1)
        self.assertGreater(ew["var95"], 1.5 * hs["var95"])
    def test_kupiec(self):
        import risk
        self.assertGreater(risk.kupiec(250, 2, 0.01)[1], 0.5)    # 250 次裡超出 2 次，符合 1%
        self.assertLess(risk.kupiec(250, 10, 0.01)[1], 0.01)     # 超出 10 次，明顯低估
        self.assertEqual(risk.kupiec(100, 0, 0.05)[0] > 0, True)
    def test_small_sample_is_never_called_validated(self):
        import risk
        recs = [{"model": "HS", "h": 1, "level": 0.99, "date": f"d{i:03d}", "ticker": "A", "hit": False, "loss": 0.0, "var975": None, "es975": None} for i in range(60)]
        self.assertIn("樣本不足", "\n".join(risk.summarize(recs, RULES["risk"])))
    def test_same_day_hits_across_tickers_count_as_one_date(self):
        """20 檔同一天一起超出，不可以當成 20 次獨立證據。"""
        import risk
        def recs(n_tickers):
            out_ = []
            for i in range(300):
                for k in range(n_tickers):
                    out_.append({"model": "HS", "h": 1, "level": 0.95, "date": f"d{i:03d}", "ticker": f"T{k}", "hit": i % 12 == 0, "loss": 0.0, "var975": None, "es975": None})
            return out_
        row1 = [l for l in risk.summarize(recs(1), RULES["risk"]) if l.startswith("| HS")][0].split("|")
        row20 = [l for l in risk.summarize(recs(20), RULES["risk"]) if l.startswith("| HS")][0].split("|")
        self.assertEqual(row1[4].strip(), "300"); self.assertEqual(row20[4].strip(), "300")     # 日期數不變
        self.assertEqual(row1[8].strip(), row20[8].strip())                                       # p 值不因為多複製 20 檔而變小
    def test_hac_widens_error_for_clustered_hits(self):
        """超出事件連續發生時，標準誤要比當成獨立樣本時大；沒有相依時不可比一般標準誤小。"""
        import risk, random
        clustered = ([1.0] * 5 + [0.0] * 95) * 5
        rnd = random.Random(3); iid = [1.0 if rnd.random() < 0.05 else 0.0 for _ in range(500)]
        plain = lambda xs: risk.stdev(xs) / len(xs) ** 0.5
        self.assertGreater(risk.hac_se(clustered), 1.5 * plain(clustered))
        self.assertGreaterEqual(risk.hac_se(iid), plain(iid) * 0.999)
        self.assertEqual(risk.hac_lag(416), 5); self.assertEqual(risk.hac_lag(100), 4)
        self.assertEqual(RULES["risk"]["hac"]["lag_rule"][:5], "floor")
    def test_es_backtest_detects_underestimated_tail(self):
        import risk
        def recs(ratio):
            return [{"model": "HS", "h": 1, "level": None, "date": f"d{i:03d}", "ticker": "A", "hit": True, "loss": 0.10 * ratio * (1 + 0.1 * ((i % 5) - 2)), "var975": 0.05, "es975": 0.10} for i in range(60)]
        self.assertIn("初步：低估尾端損失", "\n".join(risk.summarize(recs(1.5), RULES["risk"])))
        self.assertIn("初步：未發現低估", "\n".join(risk.summarize(recs(1.0), RULES["risk"])))
        self.assertIn("不是正式的尾端損失檢定", "\n".join(risk.summarize(recs(1.0), RULES["risk"])))
        self.assertIn("樣本不足", "\n".join(risk.summarize(recs(1.5)[:10], RULES["risk"])))
    def _two_market_reports(self):
        import risk, datetime
        def series(n, end):
            rets = self.rets(n, 0.02, seed=n); px, out_ = 100.0, []
            d = datetime.date.fromisoformat(end) - datetime.timedelta(days=n)
            for r in rets:
                d += datetime.timedelta(days=1); px *= 1 + r; out_.append((d.isoformat(), px, 1000))
            return out_
        tw, us, old = series(300, "2026-10-05"), series(301, "2026-10-02"), series(302, "2026-09-20")
        cfg = RULES["risk"]
        return [risk.asset_report("2408", tw, None, None, cfg, "2026-10-05", "TW", "2026-10-05"),
                risk.asset_report("MU", us, None, None, cfg, "2026-10-05", "US", "2026-10-02"),
                risk.asset_report("AVGO", old, None, None, cfg, "2026-10-05", "US", "2026-10-02")]
    def test_each_market_uses_its_own_cutoff_in_ledger(self):
        """台股最新 10/5、美股最新 10/2 時，美股也要寫進帳本，基準日用它自己的 10/2。"""
        import risk, tempfile, csv
        d = tempfile.mkdtemp(); old = (risk.LEDGER, risk.LEDGER2, risk.VOID)
        risk.LEDGER, risk.LEDGER2, risk.VOID = [os.path.join(d, x) for x in ("a.csv", "b.csv", "v.csv")]
        try:
            reps = self._two_market_reports()
            self.assertEqual([r["status"] for r in reps[:2]], ["影子模式", "影子模式"])
            self.assertTrue(reps[2]["status"].startswith("資料過期"))
            n = risk.write_ledger(reps, "m" * 64, "SHADOW")
            self.assertEqual(n, 2 * len(RULES["risk"]["models"]) * len(RULES["risk"]["horizons"]))
            with open(risk.LEDGER2, encoding="utf-8") as f: rows = list(csv.DictReader(f))
            self.assertEqual({(r["ticker"], r["as_of"]) for r in rows}, {("2408", "2026-10-05"), ("MU", "2026-10-02")})
            self.assertEqual(risk.write_ledger(reps, "m" * 64, "SHADOW"), 0)            # 重跑不重複
            self.assertEqual(risk.write_ledger(reps, "x" * 64, "SHADOW"), 0)            # 換模型版本也不覆蓋已記的預測
            self.assertTrue(all(len(r["forecast_id"].split("|")) == 6 for r in rows))   # 識別碼含模型與資料指紋
            # 作廢 v2 的預測要寫到資料指紋；作廢後，換了版本的模型可以補記，補記的不會跟著被作廢
            ids = [r["forecast_id"] for r in rows if r["ticker"] == "MU"]
            with open(risk.VOID, "w", encoding="utf-8") as f:
                f.write("forecast_id_prefix,reason,by,date\n2026-10-05|2408|,只寫到代號的作廢對 v2 無效,t,2026-10-05\n" + "".join(f"{i},測試,t,2026-10-05\n" for i in ids))
            self.assertEqual(risk.write_ledger(reps, "x" * 64, "SHADOW"), len(ids))
            self.assertEqual(risk.write_ledger(reps, "x" * 64, "SHADOW"), 0)
            valid, n_all, n_void, n_dup = risk.ledger_rows()
            self.assertEqual((n_all, n_void, n_dup, len(valid)), (18, 6, 0, 12))
        finally:
            risk.LEDGER, risk.LEDGER2, risk.VOID = old
    def test_data_id_changes_when_history_is_corrected(self):
        import risk
        s1 = [(f"2026-01-{i:02d}", 100.0 + i, 1) for i in range(1, 29)]
        s2 = list(s1); s2[5] = (s2[5][0], 999.0, 1)
        a = risk.asset_report("X", s1, None, None, RULES["risk"], "2026-01-28", "US")["data_id"]
        self.assertEqual(a, risk.asset_report("X", s1 + [("2026-02-01", 1.0, 1)], None, None, RULES["risk"], "2026-01-28", "US")["data_id"])  # 之後的資料不影響
        self.assertNotEqual(a, risk.asset_report("X", s2, None, None, RULES["risk"], "2026-01-28", "US")["data_id"])
    def test_walk_forward_separates_markets(self):
        import risk, datetime
        d0 = datetime.date(2024, 1, 1); prices = {}
        for t, seed in (("2408", 1), ("MU", 2)):
            px, ser = 100.0, []
            for i, r in enumerate(self.rets(330, 0.02, seed=seed)):
                px *= 1 + r; ser.append(((d0 + datetime.timedelta(days=i)).isoformat(), px, 1))
            prices[t] = ser
        text = "\n".join(risk.walk_forward(RULES, prices, {}))
        self.assertIn("## 台股（1 檔）", text); self.assertIn("## 美股（1 檔）", text)
    def test_unrecorded_split_is_flagged_not_reported_as_loss(self):
        import risk, datetime
        d0 = datetime.date(2024, 1, 1); rets = self.rets(400, 0.01)
        px, series = 100.0, []
        for i, r in enumerate(rets):
            px *= (1 + r) * (0.25 if i == 200 else 1)
            series.append(((d0 + datetime.timedelta(days=i)).isoformat(), px, 1000))
        split_day = series[200][0]
        bad = risk.asset_report("0050", series, None, None, RULES["risk"], series[-1][0], "TW")
        self.assertTrue(bad["status"].startswith("資料可疑")); self.assertEqual(len(bad["suspect_days"]), 1)
        good = risk.asset_report("0050", series, {split_day: {"div": 0.0, "split": 4.0}}, None, RULES["risk"], series[-1][0], "TW")
        self.assertEqual(good["suspect_days"], []); self.assertLess(good["max_drawdown"], 0.5)
    def test_drawdown(self):
        import risk
        mdd, cur, longest = risk.drawdown([1, 1.2, 0.9, 1.0, 1.3, 1.17])
        self.assertAlmostEqual(mdd, 0.25); self.assertAlmostEqual(cur, 0.1); self.assertEqual(longest, 2)

class DataSpinePhase1(unittest.TestCase):
    def sessions(self):
        rows = []
        for market, tz, dates, hours in (
            ("US", "-04:00", ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"], ("09:30:00", "16:00:00")),
            ("TW", "+08:00", ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"], ("09:00:00", "13:30:00")),
        ):
            for d in dates:
                rows.append({"session_id": f"{market}_{d}", "market": market, "session_date": d,
                             "open_at": f"{d}T{hours[0]}{tz}", "close_at": f"{d}T{hours[1]}{tz}",
                             "session_status": "OPEN", "calendar_version_id": "TEST"})
        return rows

    def test_registry_separates_issuer_and_security(self):
        from data_spine import load_registry, resolve_security
        reg = load_registry()
        tsm, s1 = resolve_security("TSM", reg); local, s2 = resolve_security("2330", reg)
        self.assertEqual(tsm["issuer_id"], local["issuer_id"])
        self.assertNotEqual(tsm["security_id"], local["security_id"])
        self.assertEqual((tsm["currency"], local["currency"]), ("USD", "TWD"))
        self.assertEqual((s1, s2), ("OK", "KNOWN_SECURITY_NOT_ENABLED"))

    def test_s3b_quote_must_exist_and_event_batch_is_hash_bound(self):
        import tempfile
        from earnings_events import prepare
        from data_spine import append_event_batch, read_jsonl
        payload = {"source_checked_at":"2026-10-13T16:23:56+00:00",
                   "coverage_start_session_id":"US_2026-10-12",
                   "companies":[{"ticker":"ANET","status":"KNOWN_UPCOMING",
                     "event_local_date":"2026-10-15","event_timezone":"America/New_York",
                     "time_precision":"DATE_ONLY","source_url":"https://investors.arista.com/test",
                     "source_quote":"release results on October 15", "source_text":"We will release results on October 15.",
                     "source_published_date":"2026-10-01","evidence_method":"TEST"}]}
        rows, statuses, _ = prepare(payload, payload["source_checked_at"])
        self.assertEqual((len(rows), statuses[0]["status"]), (2, "KNOWN_UPCOMING"))
        bad = copy.deepcopy(payload); bad["companies"][0]["source_quote"] = "not on page"
        with self.assertRaisesRegex(ValueError, "SOURCE_QUOTE_NOT_FOUND"):
            prepare(bad, bad["source_checked_at"])
        folder = tempfile.mkdtemp(); events = os.path.join(folder, "events.jsonl"); ledger = os.path.join(folder, "ledger.jsonl")
        entry = append_event_batch(rows, "test", events, ledger, payload["source_checked_at"])
        self.assertEqual(entry["event_count"], 2)
        self.assertEqual(read_jsonl(ledger)[0]["file_sha256_after"], entry["file_sha256_after"])

    def test_s3b_unknown_does_not_create_event(self):
        from earnings_events import prepare
        payload = {"source_checked_at":"2026-10-13T16:23:56+00:00",
                   "coverage_start_session_id":"US_2026-10-12",
                   "companies":[{"ticker":"AVGO","status":"UNKNOWN","source_url":"https://investors.broadcom.com/"}]}
        rows, statuses, evidence = prepare(payload, payload["source_checked_at"])
        self.assertEqual(rows, []); self.assertEqual(evidence, []); self.assertEqual(statuses[0]["status"], "UNKNOWN")

    def test_s3b_reconfirmation_adds_coverage_only_without_forking_event(self):
        from earnings_events import prepare
        stamp="2026-10-13T16:23:56+00:00"
        event={"event_id":"E_ANET","record_type":"EVENT","issuer_id":"ISSUER_ARISTA",
               "event_local_date":"2026-11-03","event_local_time":None,"event_status":"SCHEDULED",
               "event_timezone":"America/New_York","time_precision":"DATE_ONLY"}
        payload={"source_checked_at":"2026-10-18T21:25:00+08:00",
                 "coverage_start_session_id":"US_2026-10-16",
                 "companies":[{"ticker":"ANET","status":"RECONFIRMED","reconfirm_event_id":"E_ANET",
                   "event_local_date":"2026-11-03","source_url":"https://investors.arista.com/",
                   "source_quote":"Arista Networks to Announce Q3 2026 Financial Results on Tuesday, November 3, 2026",
                   "source_text":"Arista Networks to Announce Q3 2026 Financial Results on Tuesday, November 3, 2026",
                   "evidence_method":"USER_BROWSER_CAPTURE"}]}
        rows,statuses,evidence=prepare(payload,payload["source_checked_at"],existing_events=[event])
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]["record_type"],"COVERAGE")
        self.assertEqual(rows[0]["evidence_method"],"USER_BROWSER_CAPTURE")
        self.assertEqual(statuses[0]["status"],"RECONFIRMED")
        self.assertEqual(statuses[0]["event_id"],"E_ANET"); self.assertEqual(len(evidence),1)

    def test_same_security_alias_resolves(self):
        from data_spine import resolve_security
        self.assertEqual(resolve_security("2408.TW")[0]["security_id"], "SEC_2408_TW")

    def test_cross_currency_price_is_rejected(self):
        from pricedata import validate_price_identity
        row = {"ticker": "TSM", "security_id": "SEC_TSM_US", "currency": "TWD"}
        self.assertTrue(any("CURRENCY_MISMATCH" in e for e in validate_price_identity(row, RULES)))

    def test_date_only_taipei_event_blocks_previous_new_york_session(self):
        from data_spine import event_effective_sessions, resolve_security
        event = {"event_local_date": "2026-10-15", "event_timezone": "Asia/Taipei", "time_precision": "DATE_ONLY"}
        eff = event_effective_sessions(event, resolve_security("TSM")[0], self.sessions())
        self.assertEqual(eff["block_effective_session_id"], "US_2026-10-14")
        self.assertEqual(eff["information_effective_session_id"], "US_2026-10-16")

    def test_date_only_event_day_stays_blocked_until_information_session(self):
        from data_spine import derive_event_distance
        stamp="2026-10-01T00:00:00+00:00"
        events = [
            {"event_id":"E1","issuer_id":"ISSUER_NANYA","event_local_date":"2026-10-15",
             "event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY","available_at":stamp,"ingested_at":stamp},
            {"event_id":"E2","issuer_id":"ISSUER_NANYA","event_local_date":"2026-10-16",
             "event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY","available_at":stamp,"ingested_at":stamp},
            {"event_id":"COV","record_type":"COVERAGE","issuer_id":"ISSUER_NANYA",
             "coverage_start_session_id":"TW_2026-10-15","coverage_end_session_id":"TW_2026-10-16",
             "checked_at":"2026-10-14T00:00:00+00:00","available_at":stamp,"ingested_at":stamp},
        ]
        ledger=[{"batch_id":"B","written_at":stamp,"event_ids":["E1","E2","COV"]}]
        got=derive_event_distance("2408", "TW_2026-10-15", ["E1", "E2", "COV"], events, self.sessions(),
                                  cutoff="2026-10-15T05:00:00+00:00",event_ledger=ledger)
        self.assertEqual((got["status"],got["distance"]),("KNOWN_UPCOMING",0))

    def test_information_and_block_policies_are_opposite_conservative_directions(self):
        from data_spine import event_effective_sessions, resolve_security
        event = {"event_local_date": "2026-10-15", "event_timezone": "Asia/Taipei", "time_precision": "DATE_ONLY"}
        eff = event_effective_sessions(event, resolve_security("TSM")[0], self.sessions())
        self.assertLess(eff["block_effective_session_id"], eff["information_effective_session_id"])

    def _event_result(self, events, declared=None, checked="2026-10-14T00:00:00+00:00", end="TW_2026-10-19", none_window=2, sessions=None):
        from data_spine import derive_event_distance
        stamp="2026-10-01T00:00:00+00:00"
        rows=[]
        for event in events:
            rows.append(dict(event,issuer_id="ISSUER_NANYA",available_at=stamp,ingested_at=stamp))
        coverage={"event_id":"COV","record_type":"COVERAGE","issuer_id":"ISSUER_NANYA",
                  "coverage_start_session_id":"TW_2026-10-13","coverage_end_session_id":end,
                  "checked_at":checked,"available_at":stamp,"ingested_at":stamp}
        rows.append(coverage)
        ids=[x["event_id"] for x in rows]
        ledger=[{"batch_id":"B","written_at":stamp,"event_ids":ids}]
        return derive_event_distance("2408","TW_2026-10-13",ids if declared is None else declared,
                                     rows,sessions or self.sessions(),cutoff="2026-10-15T00:00:00+00:00",
                                     event_ledger=ledger,none_window_sessions=none_window)

    def test_unledgered_backfill_is_invisible(self):
        from data_spine import derive_event_distance
        stamp="2026-10-01T00:00:00+00:00"
        event={"event_id":"E1","issuer_id":"ISSUER_NANYA","event_local_date":"2026-10-15",
               "event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY","available_at":stamp,"ingested_at":stamp}
        got=derive_event_distance("2408","TW_2026-10-13",[],[event],self.sessions(),
                                  cutoff="2026-10-15T00:00:00+00:00",event_ledger=[])
        self.assertEqual(got["status"],"UNKNOWN_OR_INCOMPLETE"); self.assertEqual(got["evidence"],[])

    def test_event_ids_are_only_a_cross_check(self):
        event={"event_id":"E1","event_local_date":"2026-10-15","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        got=self._event_result([event],declared=[])
        self.assertEqual(got["reason"],"EVENT_IDS_MISMATCH")

    def test_cancelled_event_can_yield_verified_none_with_fresh_coverage(self):
        base={"event_id":"E1","event_local_date":"2026-10-15","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        cancel={"event_id":"E2","event_status":"CANCELLED","supersedes_event_id":"E1",
                "event_local_date":"2026-10-15","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        self.assertEqual(self._event_result([base,cancel])["status"],"VERIFIED_NONE_IN_WINDOW")

    def test_rescheduled_event_uses_latest_visible_date(self):
        base={"event_id":"E1","event_local_date":"2026-10-14","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        moved={"event_id":"E2","event_status":"RESCHEDULED","supersedes_event_id":"E1",
               "event_local_date":"2026-10-16","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        got=self._event_result([base,moved])
        self.assertEqual((got["status"],got["distance"]),("KNOWN_UPCOMING",2))

    def test_event_chain_fork_fails_closed(self):
        base={"event_id":"E1","event_local_date":"2026-10-14","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        a=dict(base,event_id="E2",supersedes_event_id="E1"); b=dict(base,event_id="E3",supersedes_event_id="E1")
        self.assertEqual(self._event_result([base,a,b])["reason"],"EVENT_CHAIN_FORK")

    def test_far_event_without_covering_record_is_unknown(self):
        event={"event_id":"E1","event_local_date":"2026-10-19","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        sessions=self.sessions()+[{"session_id":"TW_2026-10-20","market":"TW","session_date":"2026-10-20",
                                  "open_at":"2026-10-20T09:00:00+08:00","close_at":"2026-10-20T13:30:00+08:00",
                                  "session_status":"OPEN","calendar_version_id":"TEST"}]
        got=self._event_result([event],end="TW_2026-10-15",sessions=sessions)
        self.assertEqual(got["reason"],"EVENT_COVERAGE_INCOMPLETE")

    def test_coverage_older_than_seven_days_is_unknown(self):
        event={"event_id":"E1","event_local_date":"2026-10-15","event_timezone":"Asia/Taipei","time_precision":"DATE_ONLY"}
        got=self._event_result([event],checked="2026-10-01T00:00:00+00:00")
        self.assertEqual(got["reason"],"EVENT_COVERAGE_INCOMPLETE")

    @staticmethod
    def _us_event_sessions():
        import datetime
        rows=[]; day=datetime.date(2026,10,1)
        while day <= datetime.date(2026,11,10):
            if day.weekday() < 5:
                value=day.isoformat(); rows.append({"session_id":f"US_{value}","market":"US","session_date":value,
                    "open_at":f"{value}T09:30:00-04:00","close_at":f"{value}T16:00:00-04:00",
                    "session_status":"OPEN","calendar_version_id":"TEST"})
            day += datetime.timedelta(days=1)
        return rows

    def test_official_us_date_remains_valid_through_event_without_fresh_coverage(self):
        from data_spine import derive_event_distance
        stamp="2026-10-01T12:00:00+00:00"
        event={"event_id":"E_ANET","record_type":"EVENT","issuer_id":"ISSUER_ARISTA",
               "event_type":"EARNINGS_RESULTS","event_status":"SCHEDULED","event_local_date":"2026-11-03",
               "event_timezone":"America/New_York","time_precision":"DATE_ONLY",
               "evidence_method":"OFFICIAL_PAGE_CAPTURE;ROBOTS_ALLOW","source_system":"OFFICIAL_COMPANY_IR",
               "available_at":stamp,"ingested_at":stamp}
        ledger=[{"batch_id":"B","written_at":stamp,"event_ids":["E_ANET"]}]
        policy={"enabled":True,"official_evidence_methods":["OFFICIAL_PAGE_CAPTURE","USER_BROWSER_CAPTURE","SEC_8K"],
                "official_source_systems":["SEC_EDGAR_8K"]}
        got=derive_event_distance("ANET","US_2026-10-20",["E_ANET"],[event],self._us_event_sessions(),
                                  cutoff="2026-10-20T20:00:00+00:00",event_ledger=ledger,
                                  official_event_coverage_policy=policy)
        self.assertEqual((got["status"],got["coverage_basis"]),("KNOWN_UPCOMING","OFFICIAL_EVENT_DATE_VALID_THROUGH_EVENT"))

    def test_unknown_us_date_still_requires_fresh_seven_day_coverage(self):
        from data_spine import derive_event_distance
        stamp="2026-10-01T12:00:00+00:00"; sessions=self._us_event_sessions()
        coverage={"event_id":"COV","record_type":"COVERAGE","issuer_id":"ISSUER_ARISTA",
                  "coverage_start_session_id":"US_2026-10-20","coverage_end_session_id":"US_2026-11-03",
                  "checked_at":stamp,"available_at":stamp,"ingested_at":stamp}
        ledger=[{"batch_id":"B","written_at":stamp,"event_ids":["COV"]}]
        policy={"enabled":True,"official_evidence_methods":["OFFICIAL_PAGE_CAPTURE"]}
        got=derive_event_distance("ANET","US_2026-10-20",["COV"],[coverage],sessions,
                                  cutoff="2026-10-20T20:00:00+00:00",event_ledger=ledger,
                                  none_window_sessions=10,official_event_coverage_policy=policy)
        self.assertEqual((got["status"],got["reason"]),("UNKNOWN_OR_INCOMPLETE","EVENT_COVERAGE_INCOMPLETE"))

    def test_sec_8k_reschedule_supersedes_old_event_as_value_revision(self):
        import weekly_refresh as wr
        old={"event_id":"E_OLD","issuer_id":"ISSUER_ARISTA","event_local_date":"2026-11-03",
             "event_timezone":"America/New_York"}
        filing={"accessionNumber":"0000000000-26-000001","filingDate":"2026-10-20",
                "accepted_at":"2026-10-20T12:00:00+00:00"}
        document={"url":"https://www.sec.gov/example.htm","text":"Arista will report financial results on November 4, 2026."}
        detection=wr._extract_sec_earnings_date(document["text"])
        row=wr._build_sec_reschedule_event(old,filing,document,detection,"2026-10-20T13:00:00+00:00")
        self.assertEqual((row["event_local_date"],row["supersedes_event_id"],row["supersede_type"],row["evidence_method"]),
                         ("2026-11-04","E_OLD","VALUE_REVISION","SEC_8K"))
        self.assertEqual(row["available_at"],filing["accepted_at"])

    def test_event_writer_rejects_more_than_six_fractional_digits(self):
        from data_spine import validate_event_record
        stamp="2026-10-16T12:31:52.3212099+00:00"
        row={"event_id":"E_BAD_PRECISION","record_type":"EVENT","issuer_id":"ISSUER_ARISTA",
             "event_type":"EARNINGS_RESULTS","event_status":"SCHEDULED","event_local_date":"2026-11-03",
             "event_timezone":"America/New_York","time_precision":"DATE_ONLY","available_at":stamp,
             "ingested_at":stamp,"source_url":"https://example.test","source_quote":"official date",
             "source_checked_at":stamp,"source_excerpt_sha256":"0"*64}
        errors=validate_event_record(row)
        self.assertTrue(any("小數秒不得超過 6 位" in error for error in errors))

    def test_legacy_seven_digit_event_timestamp_is_explicitly_truncated_on_read(self):
        from data_spine import derive_event_distance
        stamp="2026-10-16T12:31:52.3212099+00:00"
        event={"event_id":"E_LEGACY","record_type":"EVENT","issuer_id":"ISSUER_ARISTA",
               "event_type":"EARNINGS_RESULTS","event_status":"SCHEDULED","event_local_date":"2026-11-03",
               "event_timezone":"America/New_York","time_precision":"DATE_ONLY",
               "evidence_method":"OFFICIAL_PAGE_CAPTURE","available_at":stamp,"ingested_at":stamp}
        ledger=[{"batch_id":"B","written_at":stamp,"event_ids":["E_LEGACY"]}]
        policy={"enabled":True,"official_evidence_methods":["OFFICIAL_PAGE_CAPTURE"]}
        got=derive_event_distance("ANET","US_2026-10-20",["E_LEGACY"],[event],self._us_event_sessions(),
                                  cutoff="2026-10-20T20:00:00+00:00",event_ledger=ledger,
                                  official_event_coverage_policy=policy)
        self.assertEqual(got["status"],"KNOWN_UPCOMING")

    def test_invalid_event_timestamp_returns_unknown_with_reason(self):
        from data_spine import derive_event_distance
        event={"event_id":"E_INVALID","record_type":"EVENT","issuer_id":"ISSUER_ARISTA",
               "event_type":"EARNINGS_RESULTS","event_status":"SCHEDULED","event_local_date":"2026-11-03",
               "event_timezone":"America/New_York","time_precision":"DATE_ONLY",
               "available_at":"not-a-time","ingested_at":"2026-10-01T00:00:00+00:00"}
        ledger=[{"batch_id":"B","written_at":"2026-10-01T00:00:00+00:00","event_ids":["E_INVALID"]}]
        got=derive_event_distance("ANET","US_2026-10-20",["E_INVALID"],[event],self._us_event_sessions(),
                                  cutoff="2026-10-20T20:00:00+00:00",event_ledger=ledger)
        self.assertEqual(got["status"],"UNKNOWN_OR_INCOMPLETE")
        self.assertIn("EVENT_TIMESTAMP_INVALID:E_INVALID",got["reason"])

    def test_session_distance_skips_weekend(self):
        from data_spine import trading_session_distance
        d, err = trading_session_distance("US_2026-10-16", "US_2026-10-19", "US", self.sessions())
        self.assertEqual((d, err), (1, None))
        self.assertEqual(trading_session_distance("US_2026-10-16", "US_2026-10-16", "US", self.sessions()), (0, None))

    @staticmethod
    def _tw_sessions_between(start, end):
        import datetime
        rows=[]; day=start
        while day <= end:
            if day.weekday() < 5:
                value=day.isoformat()
                rows.append({"session_id":f"TW_{value}","market":"TW","session_date":value,
                             "open_at":f"{value}T09:00:00+08:00","close_at":f"{value}T13:30:00+08:00",
                             "session_status":"OPEN","calendar_version_id":"TEST"})
            day += datetime.timedelta(days=1)
        return rows

    def test_tw_q3_statutory_window_distance(self):
        import datetime
        from data_spine import derive_tw_statutory_event_window
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        got=derive_tw_statutory_event_window("2408","TW_2026-10-16",[],sessions)
        self.assertEqual((got["status"],got["distance"]),("KNOWN_UPCOMING",6))
        self.assertEqual((got["window_start"],got["statutory_deadline"]),("2026-10-25","2026-11-14"))
        self.assertEqual(got["coverage_record"]["evidence_method"],"STATUTORY_CALENDAR")

    def test_schema2_uses_tw_statutory_window_only_when_enabled(self):
        import datetime
        from data_spine import schema2_run_inputs
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        run={"run_schema_version":2,"run_cutoff_at":"2026-10-16T13:30:00+08:00",
             "analysis_session_id":"TW_2026-10-16","companies":[{"ticker":"2408","event_ids":[],"valuation":{}}]}
        rules=copy.deepcopy(RULES)
        rules.setdefault("verdict", {})["tw_statutory_event_window"]={"enabled":True,"policy_id":"G-S3b-TW"}
        config={"market_calendar":{"coverage_sessions_before":0,"coverage_sessions_after":20}}
        got=schema2_run_inputs(run,rules,sessions=sessions,events=[],event_ledger=[],observations=[],config=config)
        self.assertEqual((got[0]["event_value"],got[0]["event_reason"],got[0]["event_source"]),(6,None,"G-S3b-TW"))
        rules["verdict"]["tw_statutory_event_window"]["enabled"]=False
        legacy=schema2_run_inputs(run,rules,sessions=sessions,events=[],event_ledger=[],observations=[],config=config)
        self.assertEqual(legacy[0]["event_reason"],"EVENT_COVERAGE_INCOMPLETE")

    def test_tw_actual_filing_ends_window_at_next_session(self):
        import datetime
        from data_spine import derive_tw_statutory_event_window
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        obs=[{"issuer_id":"ISSUER_NANYA","fiscal_year":2026,"fiscal_period":"Q3","period_end":"2026-09-30",
              "available_at":"2026-11-05T18:00:00+08:00","availability_basis":"OFFICIAL_MOPS_TIMESTAMP"}]
        during=derive_tw_statutory_event_window("2408","TW_2026-11-05",obs,sessions)
        after=derive_tw_statutory_event_window("2408","TW_2026-11-06",obs,sessions)
        self.assertEqual((during["distance"],during["window_end_exclusive_session_id"]),(0,"TW_2026-11-06"))
        self.assertEqual((after["fiscal_year"],after["fiscal_period"]),(2026,"Q4"))

    def test_tw_statutory_deadline_observation_does_not_end_window_early(self):
        import datetime
        from data_spine import derive_tw_statutory_event_window
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        obs=[{"issuer_id":"ISSUER_NANYA","fiscal_year":2026,"fiscal_period":"Q3","period_end":"2026-09-30",
              "available_at":"2026-10-20T23:59:59+08:00","availability_basis":"STATUTORY_DEADLINE_CONSERVATIVE"}]
        got=derive_tw_statutory_event_window("2408","TW_2026-10-26",obs,sessions)
        self.assertEqual(got["distance"],0); self.assertIsNone(got["actual_available_at"])
        self.assertEqual(got["window_end_exclusive_session_id"],"TW_2026-11-16")

    def test_tw_correction_date_conservative_observation_does_not_end_window_early(self):
        import datetime
        from data_spine import derive_tw_statutory_event_window
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        obs=[{"issuer_id":"ISSUER_NANYA","fiscal_year":2026,"fiscal_period":"Q3","period_end":"2026-09-30",
              "available_at":"2026-10-20T23:59:59+08:00",
              "availability_basis":"STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE"}]
        got=derive_tw_statutory_event_window("2408","TW_2026-10-26",obs,sessions)
        self.assertEqual(got["distance"],0); self.assertIsNone(got["actual_available_at"])
        self.assertEqual(got["window_end_exclusive_session_id"],"TW_2026-11-16")

    def test_tw_cross_year_annual_report_window(self):
        import datetime
        from data_spine import derive_tw_statutory_event_window
        sessions=self._tw_sessions_between(datetime.date(2026,10,1),datetime.date(2027,4,2))
        got=derive_tw_statutory_event_window("2408","TW_2027-01-15",[],sessions)
        self.assertEqual((got["fiscal_year"],got["fiscal_period"]),(2026,"Q4"))
        self.assertEqual((got["window_start"],got["statutory_deadline"]),("2027-01-25","2027-03-31"))
        self.assertGreater(got["distance"],0)

    def test_missing_future_calendar_is_insufficient(self):
        from data_spine import calendar_coverage
        self.assertEqual(calendar_coverage("US_2026-10-19", "US", self.sessions(), before=1, after=1),
                         (False, "CALENDAR_COVERAGE_MISSING"))

    def test_calendar_revision_is_append_only(self):
        import tempfile
        from data_spine import append_market_session, load_bound_sessions, load_sessions, register_calendar_version, verify_calendar_binding
        p = os.path.join(tempfile.mkdtemp(), "sessions.csv")
        base = {"session_id":"S1","market":"TW","session_date":"2026-10-15","open_at":"2026-10-15T09:00:00+08:00",
                "close_at":"2026-10-15T13:30:00+08:00","session_status":"OPEN","calendar_version_id":"V1","source":"test",
                "source_published_at":"2026-01-01T00:00:00+08:00","available_at":"2026-01-01T00:00:00+08:00",
                "ingested_at":"2026-01-01T00:00:00+08:00","supersedes_session_id":""}
        append_market_session(p, base)
        v1 = register_calendar_version("V1", p, p + ".versions.jsonl", "2026-01-01T00:00:00+00:00")
        with self.assertRaises(ValueError): append_market_session(p, dict(base, session_id="S2"))
        revised = dict(base, session_id="S2", session_status="CLOSED", supersedes_session_id="S1")
        append_market_session(p, revised)
        register_calendar_version("V2", p, p + ".versions.jsonl", "2026-01-02T00:00:00+00:00")
        self.assertEqual([r["session_id"] for r in load_sessions(p)], ["S2"])
        with open(p, encoding="utf-8") as f:
            self.assertEqual(sum(1 for _ in f), 3)
        self.assertEqual(verify_calendar_binding("V1", v1["calendar_sha256"], p, p + ".versions.jsonl"), (True, None))
        self.assertEqual([r["session_id"] for r in load_bound_sessions("V1", v1["calendar_sha256"], p, p + ".versions.jsonl")], ["S1"])

    def test_calendar_writer_rejects_embedded_newline(self):
        import tempfile
        from data_spine import append_market_session
        p = os.path.join(tempfile.mkdtemp(), "sessions.csv")
        row = {"session_id":"S1\nS2","market":"TW","session_date":"2026-10-15",
               "open_at":"2026-10-15T09:00:00+08:00","close_at":"2026-10-15T13:30:00+08:00",
               "session_status":"OPEN","calendar_version_id":"V1","source":"test",
               "source_published_at":"2026-01-01T00:00:00+08:00","available_at":"2026-01-01T00:00:00+08:00",
               "ingested_at":"2026-01-01T00:00:00+08:00","supersedes_session_id":""}
        with self.assertRaisesRegex(ValueError, "不得包含換行"):
            append_market_session(p, row)

    def test_malformed_market_session_is_not_silently_skipped(self):
        from data_spine import _market_sessions
        with self.assertRaisesRegex(ValueError, "INVALID_MARKET_SESSION:S_BAD"):
            _market_sessions([{"session_id":"S_BAD","market":"TW","session_status":"OPEN",
                               "open_at":"bad","close_at":"2026-10-15T13:30:00+08:00"}], "TW")

    def test_observation_requires_ingested_and_backfill_has_no_available_time(self):
        from data_spine import validate_observation
        row = {"observation_id":"O1","issuer_id":"I","security_id":None,"metric_id":"EPS","value":1,"unit":"USD",
               "period_end":"2026-09-30","source_url":"x","source_published_at":"2026-10-01T00:00:00+00:00",
               "available_at":"2026-10-01T00:00:00+00:00","ingested_at":None,"availability_status":"BACKFILLED_UNKNOWN"}
        e = validate_observation(row)
        self.assertTrue(any("ingested_at" in x for x in e)); self.assertTrue(any("available_at" in x for x in e))

    def test_backfill_cannot_be_historical_replay(self):
        from data_spine import validate_observation
        row = {"observation_id":"O1","issuer_id":"I","security_id":None,"metric_id":"EPS","value":1,"unit":"USD",
               "period_end":"2026-09-30","source_url":"x","source_published_at":None,"available_at":None,
               "ingested_at":"2026-10-01T00:00:00+00:00","availability_status":"BACKFILLED_UNKNOWN"}
        self.assertTrue(any("不可用於歷史" in x for x in validate_observation(row, historical_replay=True)))

    def test_observation_correction_adds_new_row(self):
        import tempfile
        from data_spine import append_jsonl, read_jsonl
        p = os.path.join(tempfile.mkdtemp(), "obs.jsonl")
        append_jsonl(p, {"observation_id":"O1","supersedes_observation_id":None}, "observation_id", "supersedes_observation_id")
        append_jsonl(p, {"observation_id":"O2","supersedes_observation_id":"O1"}, "observation_id", "supersedes_observation_id")
        self.assertEqual(len(read_jsonl(p)), 2)

    def test_portfolio_three_states(self):
        from data_spine import portfolio_gate_status, validate_portfolio
        self.assertEqual(validate_portfolio({"portfolio_state":"NOT_PROVIDED","positions":[]}), [])
        self.assertEqual(validate_portfolio({"portfolio_state":"PROVIDED_EMPTY","positions":[]}), [])
        self.assertEqual(validate_portfolio({"portfolio_state":"PROVIDED_NONEMPTY","positions":[{"security_id":"SEC_MU_US"}]}), [])
        self.assertTrue(validate_portfolio({"portfolio_state":"PROVIDED_NONEMPTY","positions":[]}))
        self.assertEqual(portfolio_gate_status({"portfolio_state":"PROVIDED_EMPTY","positions":[]})["status"], "PASS_EMPTY")
        self.assertEqual(portfolio_gate_status({"portfolio_state":"PROVIDED_NONEMPTY","positions":[{"security_id":"SEC_MU_US"}]})["status"], "NOT_IMPLEMENTED_SCHEMA2")

    def test_schema2_portfolio_status_is_explicit_in_output(self):
        from verdict import render
        result = judge(good_run(), RULES)
        result["portfolio_check"] = {"status":"NOT_IMPLEMENTED_SCHEMA2","note":"未執行部位上限檢查"}
        self.assertIn("部位檢查狀態：NOT_IMPLEMENTED_SCHEMA2", render(result, RULES))

    def test_common_lock_does_not_grant_portfolio_write(self):
        from data_spine import assert_portfolio_write_authority
        with self.assertRaises(PermissionError): assert_portfolio_write_authority("COMMON_LOCK")

    def test_fx_selection_and_conversion(self):
        from data_spine import select_fx, convert_amount
        rows = [{"fx_pair":"USD/TWD","rate":"32","fixing_at":"2026-10-05T08:00:00+00:00","available_at":"2026-10-05T08:01:00+00:00"},
                {"fx_pair":"USD/TWD","rate":"33","fixing_at":"2026-10-05T21:00:00+00:00","available_at":"2026-10-05T21:01:00+00:00"}]
        hit, err = select_fx(rows, "USD/TWD", "2026-10-05T20:00:00+00:00", "2026-10-05T22:00:00+00:00")
        self.assertIsNone(err); self.assertEqual(convert_amount(100, hit["rate"]), 3200)

    def test_stale_fx_is_insufficient(self):
        from data_spine import select_fx
        rows = [{"fx_pair":"USD/TWD","rate":"32","fixing_at":"2026-10-01T00:00:00+00:00","available_at":"2026-10-01T00:01:00+00:00"}]
        self.assertEqual(select_fx(rows, "USD/TWD", "2026-10-05T20:00:00+00:00", "2026-10-05T22:00:00+00:00")[1], "FX_STALE_OVER_72H")

    def v1_history(self, as_of="2026-10-06", missing=0):
        from data_spine import required_complete_months
        months = required_complete_months(as_of, 60)
        return [{"month_end": m + "-28", "value": None if i < missing else 10.0,
                 "available_at":"2026-10-06T12:00:00+00:00"} for i, m in enumerate(months)]

    def eps4(self):
        return [{"period_end":d,"value":1.0,"available_at":"2026-10-01T00:00:00+00:00"} for d in
                ("2026-09-30","2026-06-30","2026-03-31","2025-12-31")]

    def test_v1_pe_requires_four_positive_quarters(self):
        from data_spine import calculate_v1
        eps = self.eps4(); eps[-1]["value"] = -1
        self.assertEqual(calculate_v1("SEC_TSM_US", "2026-10-06T16:00:00-04:00", 12, self.v1_history(), eps, RULES, "America/New_York")["status"], "NOT_APPLICABLE_EARNINGS")

    def test_v1_requires_60_complete_months_and_48_valid(self):
        from data_spine import calculate_v1
        cutoff = "2026-10-06T16:00:00-04:00"
        self.assertEqual(calculate_v1("SEC_TSM_US", cutoff, 12, self.v1_history()[1:], self.eps4(), RULES, "America/New_York")["status"], "NOT_APPLICABLE_INSUFFICIENT_HISTORY")
        self.assertEqual(calculate_v1("SEC_TSM_US", cutoff, 12, self.v1_history(missing=13), self.eps4(), RULES, "America/New_York")["status"], "DATA_INSUFFICIENT_VALID_MONTHS")

    def test_memory_securities_use_predeclared_pb(self):
        from data_spine import calculate_v1
        r = calculate_v1("SEC_MU_US", "2026-10-06T16:00:00-04:00", 12, self.v1_history(), [], RULES, "America/New_York")
        self.assertEqual(r["method"], "TRAILING_PB_RELATIVE_TO_5Y_MEDIAN"); self.assertEqual(r["status"], "OK")

    def test_schema2_event_value_refuses_declared_distance(self):
        from data_spine import event_value
        c1 = {"event_within_days": 2, "derived_event_within_trading_sessions": 99}
        c2 = {"event_within_days": 99, "derived_event_within_trading_sessions": 2, "derivation_version":"EVENT_DISTANCE_1.2.0"}
        self.assertEqual(event_value(c1, {})[0], 2)
        self.assertEqual(event_value(c2, {"run_schema_version":2}), (None, "SCHEMA2_CANONICAL_RECOMPUTE_REQUIRED"))

    def test_empty_canonical_tables_reject_fake_schema2_derivations(self):
        from data_spine import CALENDAR_VERSIONS_PATH, read_jsonl
        empty_binding = next(x for x in read_jsonl(CALENDAR_VERSIONS_PATH)
                             if x.get("calendar_version_id") == "EMPTY_INITIAL_1.2.0")
        run = good_run(); run["run_schema_version"] = 2; run.pop("info_cutoff")
        run.update({"run_batch_id":"BATCH_TEST", "analysis_session_id":"US_2026-10-05", "run_cutoff_at":"2026-10-05T16:30:00-04:00",
                    "calendar_version_id":"EMPTY_INITIAL_1.2.0",
                    "calendar_sha256":empty_binding["calendar_sha256"]})
        c = run["companies"][0]; c.pop("event_within_days")
        c.update({"event_ids":[], "derived_event_within_trading_sessions":25, "derivation_version":"EVENT_DISTANCE_1.2.0"})
        c["valuation"]["V1"] = F(0.9)
        errs, _ = validate(run, RULES)
        self.assertTrue(any("EVENT_DERIVATION_FAILED" in e for e in errs))
        self.assertTrue(any("DERIVED_V1_MISMATCH" in e for e in errs))
        self.assertNotEqual(judge(run, RULES)["companies"][0]["verdict"], "可列入研究")

    def test_schema2_recomputes_event_and_v1_and_rejects_mismatch(self):
        import datetime, tempfile
        from data_spine import (append_market_session, load_sessions, register_calendar_version,
                                required_complete_months, schema2_run_inputs, validate_schema2_run)
        temp = tempfile.mkdtemp(); p = os.path.join(temp, "sessions.csv")
        d = datetime.date(2026, 9, 21); dates = []
        while len(dates) < 85:
            if d.weekday() < 5: dates.append(d)
            d += datetime.timedelta(days=1)
        for i, day in enumerate(dates):
            ds = day.isoformat()
            append_market_session(p, {"session_id":f"US_{ds}","market":"US","session_date":ds,
                "open_at":f"{ds}T09:30:00-04:00","close_at":f"{ds}T16:00:00-04:00","session_status":"OPEN",
                "calendar_version_id":"TEST_V1","source":"test","source_published_at":"2026-01-01T00:00:00+00:00",
                "available_at":"2026-01-01T00:00:00+00:00","ingested_at":"2026-01-01T00:00:00+00:00",
                "supersedes_session_id":""})
        binding = register_calendar_version("TEST_V1", p, p + ".versions.jsonl", "2026-01-01T00:00:00+00:00")
        sessions = load_sessions(p); analysis = dates[5].isoformat(); event_day = dates[9].isoformat()
        stamp="2026-10-01T00:00:00+00:00"
        events = [{"event_id":"E1","issuer_id":"ISSUER_MICRON","event_local_date":event_day,
                   "event_timezone":"America/New_York","time_precision":"DATE_ONLY","available_at":stamp,"ingested_at":stamp},
                  {"event_id":"COV","record_type":"COVERAGE","issuer_id":"ISSUER_MICRON",
                   "coverage_start_session_id":f"US_{analysis}","coverage_end_session_id":f"US_{event_day}",
                   "checked_at":"2026-10-05T00:00:00-04:00","available_at":stamp,"ingested_at":stamp}]
        event_ledger=[{"batch_id":"B","written_at":stamp,"event_ids":["E1","COV"]}]
        observations = []
        for i, month in enumerate(required_complete_months("2026-10-06", 60)):
            observations.append({"observation_id":f"PB{i}","security_id":"SEC_MU_US","metric_id":"TRAILING_PB",
                "value":10.0,"period_end":month+"-28","available_at":"2026-10-01T12:00:00-04:00",
                "ingested_at":"2026-10-01T12:01:00-04:00","availability_status":"POINT_IN_TIME_KNOWN"})
        run = good_run(); run["run_schema_version"] = 2; run.pop("info_cutoff")
        run.update({"run_batch_id":"BATCH_TEST", "analysis_session_id":f"US_{analysis}","run_cutoff_at":"2026-10-06T16:30:00-04:00",
                    "calendar_version_id":"TEST_V1","calendar_sha256":binding["calendar_sha256"]})
        c = run["companies"][0]; c.pop("event_within_days"); c["event_ids"] = ["E1","COV"]
        c["valuation"]["V1"] = F(None); c["valuation"]["V1"]["status"] = "未查"; c["valuation"]["V1"]["source"] = ""; c["valuation"]["V1"]["as_of"] = ""
        got = schema2_run_inputs(run, RULES, sessions=sessions, events=events, observations=observations,event_ledger=event_ledger)[0]
        self.assertEqual(got["event_value"], 3); self.assertEqual(got["v1"]["status"], "OK"); self.assertAlmostEqual(got["v1"]["value"], 1.0)
        self.assertEqual(validate_schema2_run(run, RULES, sessions=sessions, events=events, observations=observations,event_ledger=event_ledger, sessions_path=p), [])
        c["derived_event_within_trading_sessions"] = 25; c["valuation"]["V1"] = F(0.9)
        errs = validate_schema2_run(run, RULES, sessions=sessions, events=events, observations=observations,event_ledger=event_ledger, sessions_path=p)
        self.assertTrue(any("DERIVED_EVENT_MISMATCH" in e for e in errs)); self.assertTrue(any("DERIVED_V1_MISMATCH" in e for e in errs))

    def test_v1_success_is_current_over_median(self):
        from data_spine import calculate_v1
        r = calculate_v1("SEC_TSM_US", "2026-10-06T16:00:00-04:00", 12, self.v1_history(), self.eps4(), RULES, "America/New_York")
        self.assertEqual(r["status"], "OK"); self.assertAlmostEqual(r["value"], 1.2)

    def test_v1_cutoff_does_not_admit_next_local_day(self):
        from data_spine import calculate_v1
        eps = self.eps4()
        eps[0]["available_at"] = "2026-10-07T00:30:00+08:00"
        r = calculate_v1("SEC_TSM_US", "2026-10-06T13:30:00+08:00", 12, self.v1_history(), eps, RULES, "Asia/Taipei")
        self.assertEqual(r["status"], "NOT_APPLICABLE_EARNINGS")

    def test_v1_history_rows_must_be_available_by_cutoff(self):
        from data_spine import calculate_v1
        hist = self.v1_history(); hist[0]["available_at"] = "2026-10-07T00:00:00+00:00"
        r = calculate_v1("SEC_TSM_US", "2026-10-06T16:00:00-04:00", 12, hist, self.eps4(), RULES, "America/New_York")
        self.assertEqual(r["status"], "NOT_APPLICABLE_INSUFFICIENT_HISTORY")

    def test_news_and_sentiment_remain_disabled(self):
        self.assertEqual(RULES["data_spine"]["news_module"], "DISABLED")
        self.assertEqual(RULES["data_spine"]["sentiment_module"], "DISABLED")

    def test_prompt_requires_only_enabled_indicators(self):
        with open(os.path.join(ROOT, "00_規範", "03_提示詞_聊天版.md"), encoding="utf-8") as f: text = f.read()
        self.assertIn("所有**啟用中的指標**都要有欄位", text)
        self.assertIn("標為停用的指標可以省略", text)

    def test_portfolio_is_not_created_by_implementation(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "01_共用資料", "portfolio.json")))

    def test_score_has_forward_only_limitation_header(self):
        with open(os.path.join(ROOT, "03_工具", "score.py"), encoding="utf-8") as f: src = f.read()
        self.assertIn("追蹤名單為事後挑選的熱門股；本報告不代表歷史策略績效。", src)


class FinancialHealthModule(unittest.TestCase):
    CUTOFF = "2026-11-15T23:59:59+00:00"

    def row(self, metric, value, end, period_type, n, available="2026-10-31T23:59:59+00:00"):
        return {"observation_id":f"O{n}","issuer_id":"ISSUER_MICRON","security_id":None,
                "metric_id":metric,"value":value,"unit":"USD" if "SHARES" not in metric else "shares",
                "period_start":None,"period_end":end,"period_type":period_type,
                "source_url":"https://www.sec.gov/","source_published_at":available,"available_at":available,
                "ingested_at":available,"availability_status":"POINT_IN_TIME_KNOWN",
                "supersedes_observation_id":None}

    def baseline(self):
        rows = []; n = 0
        for metric, value in (("FIN_CASH",100),("FIN_SHORT_TERM_INVESTMENTS",20),("FIN_DEBT_CURRENT",10),
                              ("FIN_DEBT_NONCURRENT",20),("FIN_CURRENT_ASSETS",150),("FIN_CURRENT_LIABILITIES",100)):
            n += 1; rows.append(self.row(metric,value,"2026-09-30","INSTANT",n))
        quarters = ("2025-12-31","2026-03-31","2026-06-30","2026-09-30")
        for end in quarters:
            for metric, value in (("FIN_OPERATING_INCOME",10),("FIN_DEPRECIATION_AMORTIZATION",2),
                                  ("FIN_INTEREST_EXPENSE",1),("FIN_CFO",20),("FIN_CAPEX",10),
                                  ("FIN_DILUTED_SHARES",100)):
                n += 1; rows.append(self.row(metric,value,end,"Q",n))
        n += 1; rows.append(self.row("FIN_DILUTED_SHARES",100,"2025-09-30","Q",n))
        for end, cfo, capex in (("2024-12-31",80,40),("2025-12-31",90,45)):
            n += 1; rows.append(self.row("FIN_CFO",cfo,end,"FY",n))
            n += 1; rows.append(self.row("FIN_CAPEX",capex,end,"FY",n))
        return rows

    def eval(self, rows=None, etf=False):
        from health import evaluate_issuer
        return evaluate_issuer("ISSUER_MICRON", self.baseline() if rows is None else rows, RULES, self.CUTOFF, etf)

    def replace_values(self, rows, replacements):
        for row in rows:
            key = (row["metric_id"], row["period_end"], row["period_type"])
            if key in replacements: row["value"] = replacements[key]
        return rows

    def test_user_approved_thresholds_are_exact(self):
        h = RULES["health"]["thresholds"]
        self.assertEqual((h["H1"]["caution_below_quarters"], h["H1"]["warning_below_quarters"]), (8,4))
        self.assertEqual((h["H2"]["caution_above"], h["H2"]["warning_above"]), (3,4.5))
        self.assertEqual((h["H3"]["caution_below"], h["H3"]["warning_below"]), (3,1.5))
        self.assertEqual((h["H4"]["caution_above"], h["H4"]["warning_above"]), (1,2))
        self.assertEqual((h["H6"]["caution_below"], h["H7"]["caution_above_pct"], h["H7"]["warning_above_pct"]), (1,5,15))

    def test_net_cash_h2_is_healthy(self):
        got = self.eval()["indicators"]
        self.assertEqual(got["H2"]["status"], "穩健")
        self.assertIn("CROSS_MARKET_NOT_COMPARABLE", got["H2"]["limitations"])
        self.assertIn("CROSS_MARKET_NOT_COMPARABLE", got["H4"]["limitations"])

    def test_negative_ebitda_positive_net_debt_warns(self):
        rows = self.baseline()
        for row in rows:
            if row["metric_id"] == "FIN_CASH": row["value"] = 1
            if row["metric_id"] == "FIN_SHORT_TERM_INVESTMENTS": row["value"] = 0
            if row["metric_id"] == "FIN_DEBT_NONCURRENT": row["value"] = 200
            if row["metric_id"] == "FIN_OPERATING_INCOME": row["value"] = -10
            if row["metric_id"] == "FIN_DEPRECIATION_AMORTIZATION": row["value"] = 1
        self.assertEqual(self.eval(rows)["indicators"]["H2"]["status"], "警示")

    def test_explicit_zero_debt_makes_h3_h4_healthy_without_interest(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_INTEREST_EXPENSE"]
        for row in rows:
            if row["metric_id"] in {"FIN_DEBT_CURRENT","FIN_DEBT_NONCURRENT"}: row["value"] = 0
        got = self.eval(rows)["indicators"]
        self.assertEqual((got["H3"]["status"], got["H4"]["status"]), ("穩健","穩健"))

    def test_positive_fcf_makes_h1_not_applicable(self):
        self.assertEqual(self.eval()["indicators"]["H1"]["status"], "不適用")

    def test_missing_short_term_investment_uses_same_light_envelope(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_SHORT_TERM_INVESTMENTS"]
        got = self.eval(rows)
        self.assertEqual(got["status"], "穩健")
        self.assertEqual(got["short_term_investment_review_required"], [])
        for key in ("H1", "H2", "H4"):
            item = got["indicators"][key]
            self.assertIn("SHORT_TERM_INV_IRRELEVANT_TO_BAND", item["limitations"])
            self.assertIsNone(item["value"])
            self.assertEqual(item["short_term_investment_band"]["basis"], "ZERO_TO_CURRENT_ASSETS_MINUS_CASH")
        from health import short_term_investment_band_summary
        self.assertEqual(short_term_investment_band_summary([got]), {
            "evaluated_count": 3, "exempt_count": 3, "review_required_count": 0, "exempt_rate_pct": 100.0})

    def test_band_that_crosses_light_requires_ai_review(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_SHORT_TERM_INVESTMENTS"]
        self.replace_values(rows, {("FIN_DEBT_CURRENT", "2026-09-30", "INSTANT"): 150,
                                   ("FIN_DEBT_NONCURRENT", "2026-09-30", "INSTANT"): 0})
        got = self.eval(rows)
        h4 = got["indicators"]["H4"]
        self.assertEqual(h4["status"], "資料不足")
        self.assertIn("SHORT_TERM_INV_REVIEW_REQUIRED", h4["limitations"])
        self.assertEqual(got["short_term_investment_review_required"], [{
            "indicator_id": "H4", "lower_status": "留意", "upper_status": "穩健", "period_end": "2026-09-30"}])
        from health import render_section
        report = render_section([got], RULES)
        self.assertIn("短期投資待 AI 判讀附註", report)
        self.assertIn("| H4 | 2026-09-30 | 留意 | 穩健 |", report)

    def test_invalid_short_term_upper_bound_does_not_infer_zero(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_SHORT_TERM_INVESTMENTS"]
        self.replace_values(rows, {("FIN_CURRENT_ASSETS", "2026-09-30", "INSTANT"): 90})
        got = self.eval(rows)
        for key in ("H1", "H2", "H4"):
            self.assertEqual(got["indicators"][key]["status"], "資料不足")
            self.assertNotIn("short_term_investment_band", got["indicators"][key])

    def test_envelope_matches_existing_explicit_manual_value_when_light_is_invariant(self):
        exact = self.eval()["indicators"]
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_SHORT_TERM_INVESTMENTS"]
        enveloped = self.eval(rows)["indicators"]
        for key in ("H1", "H2", "H4"):
            self.assertEqual(enveloped[key]["status"], exact[key]["status"])

    def test_h5_negative_alone_only_caution(self):
        rows = self.baseline()
        for row in rows:
            if row["metric_id"] == "FIN_CAPEX" and row["period_type"] == "Q": row["value"] = 30
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H5"]["status"], "留意")

    def test_h5_two_negative_years_needs_h1_before_ruling_out_warning(self):
        rows = self.baseline()
        rows = [r for r in rows if r["metric_id"] != "FIN_CASH"]
        for row in rows:
            if row["metric_id"] == "FIN_CAPEX": row["value"] = float(row["value"]) + 200
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H5"]["status"], "資料不足")

    def test_required_missing_makes_overall_insufficient(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_DEPRECIATION_AMORTIZATION"]
        for row in rows:
            if row["metric_id"] == "FIN_CASH": row["value"] = 1
            if row["metric_id"] == "FIN_SHORT_TERM_INVESTMENTS": row["value"] = 0
        got = self.eval(rows)
        self.assertEqual(got["status"], "資料不足"); self.assertIn("H2", got["required_missing"])

    def test_missing_debt_tags_are_not_inferred_as_zero(self):
        rows = [r for r in self.baseline() if r["metric_id"] not in {"FIN_DEBT_CURRENT","FIN_DEBT_NONCURRENT"}]
        got = self.eval(rows)
        self.assertEqual(got["status"], "資料不足"); self.assertEqual(got["indicators"]["H4"]["status"], "資料不足")

    def test_stale_latest_quarter_is_insufficient(self):
        got = self.eval(self.baseline())
        self.assertEqual(got["status"], "穩健")
        from health import evaluate_issuer
        self.assertEqual(evaluate_issuer("ISSUER_MICRON", self.baseline(), RULES, "2027-11-15T23:59:59+00:00")["status"], "資料不足")

    def test_late_filing_is_excluded_by_all_three_times(self):
        rows = self.baseline()
        for row in rows:
            if row["metric_id"] == "FIN_DEPRECIATION_AMORTIZATION": row["available_at"] = "2026-12-01T00:00:00+00:00"
            if row["metric_id"] == "FIN_CASH": row["value"] = 1
            if row["metric_id"] == "FIN_SHORT_TERM_INVESTMENTS": row["value"] = 0
        self.assertEqual(self.eval(rows)["indicators"]["H2"]["status"], "資料不足")

    def test_etf_is_not_applicable(self):
        self.assertEqual(self.eval([], True)["status"], "不適用")

    def test_tsm_and_2330_share_issuer(self):
        from data_spine import resolve_security
        self.assertEqual(resolve_security("TSM")[0]["issuer_id"], resolve_security("2330")[0]["issuer_id"])

    def test_output_has_unvalidated_and_not_advice_disclaimers(self):
        from health import render_section
        text = render_section([self.eval()], RULES)
        self.assertIn("門檻尚未用本系統的歷史資料驗證", text)
        self.assertIn("不是投資建議，也不是買賣指令", text)
        self.assertIn("逐公司逐指標明細", text)
        for key in ("H1","H2","H3","H4","H5","H6","H7"):
            self.assertIn(f"| {key} |", text)

    def test_h130_03_ratio_quarters_must_align(self):
        rows = [r for r in self.baseline() if not (r["metric_id"] == "FIN_INTEREST_EXPENSE" and r["period_end"] == "2026-09-30")]
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H3"]["status"], "資料不足")

    def test_h130_04_freshness_is_per_indicator(self):
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_INTEREST_EXPENSE"]
        # H3 有完整但停在 2026-03-31 的四季；其他指標仍更新到 2026-09-30。
        n = 2000
        for end in ("2025-06-30","2025-09-30","2025-12-31","2026-03-31"):
            n += 1; rows.append(self.row("FIN_INTEREST_EXPENSE",1,end,"Q",n))
            if end in {"2025-06-30","2025-09-30"}:
                n += 1; rows.append(self.row("FIN_OPERATING_INCOME",10,end,"Q",n))
        got = self.eval(rows)
        self.assertEqual(got["latest_period_end"], "2026-09-30")
        self.assertEqual(got["indicators"]["H3"]["status"], "資料不足")
        self.assertIn("H3", got["stale_indicators"])

    def test_total_debt_shortcut_is_not_accepted(self):
        """1.3.2：只給「總負債」而沒有非流動負債時不可計算。1.3.1 曾把只含非流動部分的標籤當成總額，少算了負債。"""
        rows = [r for r in self.baseline() if r["metric_id"] != "FIN_DEBT_NONCURRENT"]
        rows.append(self.row("FIN_DEBT_TOTAL",30,"2026-09-30","INSTANT",2100))
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H2"]["status"], "資料不足")
        self.assertEqual(got["status"], "資料不足")

    def test_total_debt_is_current_plus_noncurrent(self):
        rows = self.replace_values(self.baseline(), {("FIN_DEBT_CURRENT","2026-09-30","INSTANT"): 60,
                                                     ("FIN_DEBT_NONCURRENT","2026-09-30","INSTANT"): 100})
        got = self.eval(rows)   # 現金 120、負債 160 → 淨負債 40；EBITDA 48 → 0.83 倍
        self.assertAlmostEqual(got["indicators"]["H2"]["value"], 40 / 48, places=6)

    def test_noncurrent_only_tag_is_never_mapped_as_total(self):
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_tag_map.json"), encoding="utf-8") as fh: tm = json.load(fh)
        for iid, cfg in tm["sec_issuers"].items():
            self.assertNotIn("FIN_DEBT_TOTAL", cfg["tags"], iid)
        with open(os.path.join(ROOT, "03_工具", "health.py"), encoding="utf-8") as fh: self.assertNotIn("FIN_DEBT_TOTAL", fh.read())

    def test_stale_optional_indicator_cannot_hide_a_breach(self):
        """H7 過期前是警示（稀釋 30%）時，整體不可顯示成穩健。"""
        rows = [r for r in self.baseline() if not (r["metric_id"] == "FIN_DILUTED_SHARES" and r["period_end"] in ("2026-09-30", "2026-06-30"))]
        rows = [r for r in rows if not (r["metric_id"] == "FIN_DILUTED_SHARES" and r["period_end"] == "2025-09-30")]
        rows.append(self.row("FIN_DILUTED_SHARES",100,"2025-03-31","Q",3001))
        for r in rows:
            if r["metric_id"] == "FIN_DILUTED_SHARES" and r["period_end"] == "2026-03-31": r["value"] = 130
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H7"]["status"], "資料不足")
        self.assertEqual(got["indicators"]["H7"]["status_before_stale"], "警示")
        self.assertEqual(got["status"], "留意"); self.assertEqual(got["stale_breaches"], ["H7"])
        self.assertTrue(any("待新財報確認" in x for x in got["reasons"]))

    def test_stale_optional_indicator_without_breach_stays_healthy(self):
        rows = [r for r in self.baseline() if not (r["metric_id"] == "FIN_DILUTED_SHARES" and r["period_end"] in ("2026-09-30", "2026-06-30", "2025-09-30"))]
        rows.append(self.row("FIN_DILUTED_SHARES",100,"2025-03-31","Q",3002))
        got = self.eval(rows)
        self.assertEqual(got["indicators"]["H7"]["status"], "資料不足")
        self.assertEqual((got["status"], got["stale_breaches"]), ("穩健", []))

    def test_zero_assertions_are_independently_reviewed(self):
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_zero_assertions.json"), encoding="utf-8") as fh: z = json.load(fh)
        for a in z["assertions"]:
            if a["review_status"] == "APPROVED_BY_INDEPENDENT_AI":
                self.assertNotEqual(a["checked_by"], a["reviewed_by"], a["assertion_id"])
                self.assertTrue(a.get("review_evidence"), a["assertion_id"])

    def test_h7_strict_boundaries(self):
        rows = self.baseline()
        for row in rows:
            if row["metric_id"] == "FIN_DILUTED_SHARES" and row["period_end"] == "2026-09-30": row["value"] = 105
        self.assertEqual(self.eval(rows)["indicators"]["H7"]["status"], "穩健")
        for row in rows:
            if row["metric_id"] == "FIN_DILUTED_SHARES" and row["period_end"] == "2026-09-30": row["value"] = 115.01
        self.assertEqual(self.eval(rows)["indicators"]["H7"]["status"], "警示")

    def test_tag_selection_uses_first_exact_allowlist_without_summing(self):
        from fetch_financials import choose_tag
        facts = {"facts":{"us-gaap":{"DebtCurrent":{"units":{"USD":[{"form":"10-Q","end":"2026-09-30","val":10,"filed":"2026-10-01","accn":"a"}]}},
                                           "LongTermDebtCurrent":{"units":{"USD":[{"form":"10-Q","end":"2026-09-30","val":10,"filed":"2026-10-01","accn":"a"}]}}}}}
        tag, rows = choose_tag(facts, "FIN_DEBT_CURRENT", ["DebtCurrent","LongTermDebtCurrent"])
        self.assertEqual(tag, "DebtCurrent"); self.assertEqual(len(rows), 1)

    def test_sec_ytd_is_quarterized_without_double_counting(self):
        from fetch_financials import normalize_duration
        def f(fp, start, end, val): return {"fp":fp,"fy":2026,"start":start,"end":end,"val":val,"filed":"2026-10-01","accn":fp}
        got = normalize_duration([f("Q1","2026-01-01","2026-03-31",10), f("Q2","2026-01-01","2026-06-30",25),
                                  f("Q3","2026-01-01","2026-09-30",45), f("FY","2026-01-01","2026-12-31",70)])
        quarters = {end:value for typ,end,_,value,_ in got if typ == "Q"}
        self.assertEqual(quarters, {"2026-03-31":10,"2026-06-30":15,"2026-09-30":20,"2026-12-31":25})
        starts = {end:fact.get("_normalized_period_start", fact.get("start"))
                  for typ,end,fact,_,_ in got if typ == "Q"}
        self.assertEqual(starts, {"2026-03-31":"2026-01-01", "2026-06-30":"2026-04-01",
                                  "2026-09-30":"2026-07-01", "2026-12-31":"2026-10-01"})

    def test_bounded_period_history_keeps_q_and_fy_independently(self):
        from fetch_financials import bounded_period_history
        q = [("Q", f"{year}-03-31", {}, float(year), "Q") for year in range(1990, 2027)]
        fy = [("FY", f"{year}-12-31", {}, float(year), "FY") for year in range(1990, 2027)]
        got = bounded_period_history(fy + q)
        self.assertEqual(sum(x[0] == "Q" for x in got), 28)
        self.assertEqual(sum(x[0] == "FY" for x in got), 8)
        self.assertEqual([x[1] for x in got if x[0] == "FY"][0], "2019-12-31")

    def test_weighted_average_shares_are_never_ytd_subtracted(self):
        from fetch_financials import normalize_shares
        rows = [{"fp":"Q2","fy":2026,"start":"2026-01-01","end":"2026-06-30","val":200,"filed":"2026-07-01","accn":"YTD"},
                {"fp":"Q2","fy":2026,"start":"2026-04-01","end":"2026-06-30","val":105,"filed":"2026-07-01","accn":"Q"}]
        got = normalize_shares(rows)
        self.assertEqual([(x[1], x[3]) for x in got], [("2026-06-30",105.0)])

    def test_weighted_average_shares_accept_full_year_without_subtraction(self):
        from fetch_financials import normalize_shares
        rows = [{"fp":"FY","fy":2025,"start":"2024-07-01","end":"2025-06-30","val":100,"filed":"2025-08-20","accn":"FY25"},
                {"fp":"FY","fy":2026,"start":"2025-07-01","end":"2026-06-30","val":107,"filed":"2026-08-20","accn":"FY26"}]
        got = normalize_shares(rows)
        self.assertEqual([(x[0], x[1], x[3], x[4]) for x in got],
                         [("FY","2025-06-30",100.0,"REPORTED_FY"),
                          ("FY","2026-06-30",107.0,"REPORTED_FY")])

    def test_h7_uses_newer_annual_pair_without_mixing_period_types(self):
        rows = self.baseline()
        rows += [self.row("FIN_DILUTED_SHARES",100,"2025-10-31","FY",4100),
                 self.row("FIN_DILUTED_SHARES",107,"2026-10-31","FY",4101)]
        got = self.eval(rows)["indicators"]["H7"]
        self.assertEqual(got["status"], "留意")
        self.assertAlmostEqual(got["value"], 7.0)
        self.assertIn("年度", got["reason"])

    def test_period_fallback_combines_equivalent_tags_before_quarterizing(self):
        from fetch_financials import normalize_period_fallback
        def concept(rows): return {"units":{"USD":rows}}
        facts = {"facts":{"us-gaap":{
            "PaymentsToAcquirePropertyPlantAndEquipment": concept([
                {"form":"10-Q","fp":"Q3","fy":2025,"start":"2025-01-01","end":"2025-09-30","val":82.5,"filed":"2025-11-01","accn":"Q3"}]),
            "PaymentsToAcquireProductiveAssets": concept([
                {"form":"10-K","fp":"FY","fy":2025,"start":"2025-01-01","end":"2025-12-31","val":119.5,"filed":"2026-02-01","accn":"FY"}])
        }}}
        got, coverage = normalize_period_fallback(
            facts, "FIN_CAPEX",
            [["PaymentsToAcquirePropertyPlantAndEquipment"], ["PaymentsToAcquireProductiveAssets"]])
        values = {(x[0], x[1]): x[3] for x in got}
        self.assertEqual(coverage["status"], "OK")
        self.assertAlmostEqual(values[("Q","2025-12-31")], 37.0)

    def test_gross_profit_difference_requires_same_period_components(self):
        from fetch_financials import normalize_difference
        def concept(rows): return {"units":{"USD":rows}}
        def fact(tag, end, value):
            return {"form":"10-Q","fp":"Q1","fy":2026,"start":"2026-01-01","end":end,
                    "val":value,"filed":"2026-05-01","accn":tag,"unit":"USD"}
        facts = {"facts":{"us-gaap":{
            "RevenueFromContractWithCustomerExcludingAssessedTax": concept([fact("REV","2026-03-31",100)]),
            "CostOfGoodsAndServicesSold": concept([fact("COST","2026-03-31",60)])}}}
        cfg = {"minuend":["RevenueFromContractWithCustomerExcludingAssessedTax"],
               "subtrahend":["CostOfGoodsAndServicesSold"]}
        got, coverage = normalize_difference(facts, "FIN_GROSS_PROFIT", cfg)
        self.assertEqual(coverage["status"], "OK")
        self.assertEqual([(x[0], x[1], x[3]) for x in got], [("Q","2026-03-31",40.0)])
        facts["facts"]["us-gaap"]["CostOfGoodsAndServicesSold"] = concept([fact("COST","2025-12-31",60)])
        self.assertEqual(normalize_difference(facts, "FIN_GROSS_PROFIT", cfg)[0], [])

    def test_health_help_is_non_mutating(self):
        ledger = os.path.join(ROOT, "04_回測", "財務體質紀錄.csv")
        if os.path.exists(ledger):
            with open(ledger, "rb") as handle: before = handle.read()
        else:
            before = None
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "03_工具", "health.py"), "--help"],
                              cwd=ROOT, capture_output=True)
        if os.path.exists(ledger):
            with open(ledger, "rb") as handle: after = handle.read()
        else:
            after = None
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"--no-ledger", proc.stdout)
        self.assertEqual(before, after)

    def test_h130_01_depreciation_and_amortization_components_require_both(self):
        from fetch_financials import normalize_composite
        def concept(value):
            return {"units":{"USD":[{"form":"10-Q","fp":"Q1","fy":2026,"start":"2026-01-01","end":"2026-03-31","val":value,"filed":"2026-05-01","accn":"A"}]}}
        facts = {"facts":{"us-gaap":{"Depreciation":concept(7),"AmortizationOfIntangibleAssets":concept(3)}}}
        got, coverage = normalize_composite(facts,"FIN_DEPRECIATION_AMORTIZATION",[["Depreciation"],["AmortizationOfIntangibleAssets"]])
        self.assertEqual(got[0][3], 10.0); self.assertEqual(coverage["status"], "OK")
        missing, coverage = normalize_composite({"facts":{"us-gaap":{"Depreciation":concept(7)}}},"FIN_DEPRECIATION_AMORTIZATION",[["Depreciation"],["AmortizationOfIntangibleAssets"]])
        self.assertEqual(missing, []); self.assertEqual(coverage["status"], "MISSING_COMPOSITE_COMPONENT")

    def test_h130_01_pending_zero_assertions_never_become_canonical(self):
        from fetch_financials import zero_assertion_observations
        base = {"assertion_id":"Z1","issuer_id":"ISSUER_ARISTA","metric_id":"FIN_DEBT_CURRENT",
                "value":0,"unit":"USD","period_end":"2026-06-30","period_type":"INSTANT",
                "accession_number":"A","filing_url":"https://www.sec.gov/A","filed_date":"2026-08-05",
                "checked_by":"Codex","checked_at":"2026-10-06","valid_through_period_end":"2026-06-30",
                "evidence":"read filing","review_status":"PENDING_INDEPENDENT_REVIEW"}
        rows, registry = zero_assertion_observations({"assertions":[base]},"2026-10-06T12:00:00+08:00")
        self.assertEqual(rows, []); self.assertEqual(registry[0]["review_status"], "PENDING_INDEPENDENT_REVIEW")
        approved = dict(base, review_status="APPROVED_BY_INDEPENDENT_AI", reviewed_by="Claude", reviewed_at="2026-10-07")
        rows, _ = zero_assertion_observations({"assertions":[approved]},"2026-10-06T12:00:00+08:00")
        self.assertEqual(rows[0]["value"], 0.0); self.assertEqual(rows[0]["zero_assertion_id"], "Z1")

    def test_tag_map_has_nine_sec_issuers_and_taiwan_pending(self):
        import json
        with open(os.path.join(ROOT,"00_規範","financial_tag_map.json"),encoding="utf-8") as f: tag_map=json.load(f)
        self.assertEqual(len(tag_map["sec_issuers"]), 9)
        self.assertEqual(tag_map["taiwan"]["status"], "ENABLED_AFTER_INDEPENDENT_REVIEW")   # 1.8.5 使用者授權啟用
        fallback_issuers = [iid for iid, cfg in tag_map["sec_issuers"].items() if cfg.get("period_fallbacks")]
        self.assertEqual(fallback_issuers, ["ISSUER_LUMENTUM", "ISSUER_ARISTA"])
        for cfg in tag_map["sec_issuers"].values():
            self.assertIn("FIN_REVENUE", cfg["tags"])
            self.assertIn("FIN_GROSS_PROFIT", cfg["tags"])

    def test_marvell_short_term_investment_zero_has_a_recorded_independent_review(self):
        """這筆聲明原本釘在「待複查」。Claude 於 2026-10-06 核對申報的資產負債表後核准。
        測試不釘死結果，只要求：不論核准或駁回，都要有不同於查證者的複查者與複查依據。"""
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_zero_assertions.json"), encoding="utf-8") as fh:
            assertions = {a["assertion_id"]: a for a in json.load(fh)["assertions"]}
        a = assertions["ZERO_MRVL_SHORT_TERM_INVESTMENTS_2027Q2"]
        if a["review_status"] != "PENDING_INDEPENDENT_REVIEW":
            self.assertTrue(a.get("reviewed_by") and a["reviewed_by"] != a["checked_by"])
            self.assertTrue(a.get("review_evidence"))

    def test_canonical_financial_write_is_enabled_after_130_review(self):
        self.assertEqual(RULES["health"]["canonical_write_status"], "ENABLED_AFTER_INDEPENDENT_REVIEW")

    def test_financial_corrections_append_and_supersede(self):
        import tempfile
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl
        path = os.path.join(tempfile.mkdtemp(), "observations.jsonl")
        first = self.row("FIN_CASH", 100, "2026-09-30", "INSTANT", 900, "2026-10-01T23:59:59+00:00")
        second = self.row("FIN_CASH", 101, "2026-09-30", "INSTANT", 901, "2026-10-02T23:59:59+00:00")
        self.assertEqual(append_financial_observations([first], path), 1)
        self.assertEqual(append_financial_observations([first, second], path), 1)
        rows = read_jsonl(path)
        self.assertEqual(len(rows), 2); self.assertEqual(rows[1]["supersedes_observation_id"], first["observation_id"])

    def test_same_filing_tag_mapping_correction_is_append_only(self):
        import tempfile
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl
        path = os.path.join(tempfile.mkdtemp(), "observations.jsonl")
        first = self.row("FIN_DEPRECIATION_AMORTIZATION", 10, "2026-09-30", "Q", 920, "2026-10-01T23:59:59+00:00")
        first.update(source_tag="OtherDepreciationAndAmortization", derivation="REPORTED_QUARTER")
        second = self.row("FIN_DEPRECIATION_AMORTIZATION", 14, "2026-09-30", "Q", 921, "2026-10-01T23:59:59+00:00")
        second.update(source_tag="OtherDepreciationAndAmortization+AmortizationOfIntangibleAssets",
                      derivation="COMPONENT_SUM", mapping_version="0.3.0")
        self.assertEqual(append_financial_observations([first], path), 1)
        self.assertEqual(append_financial_observations([second], path), 1)
        rows = read_jsonl(path)
        self.assertEqual(rows[1]["supersedes_observation_id"], first["observation_id"])
        self.assertEqual(rows[1]["correction_reason"], "TAG_MAPPING_RULE_CHANGE")

    def test_health_is_integrated_in_risk_report_but_not_verdict_rules(self):
        import risk
        text = risk.render("2026-11-15", [], None, RULES["risk"], health_results=[self.eval()], rules=RULES)
        self.assertIn("公司財務體質（影子模式）", text)
        self.assertNotIn("health", RULES["verdict"])

    def test_calendar_requires_seventy_future_sessions(self):
        import json
        with open(os.path.join(ROOT,"00_規範","data_spine_phase1.json"),encoding="utf-8") as f: cfg=json.load(f)
        self.assertEqual(cfg["market_calendar"]["coverage_sessions_after"], 20)
        self.assertEqual(cfg["market_calendar"]["coverage_sessions_after"],
                         max(RULES["verdict"]["event_block_days"], RULES["verdict"]["event_none_meaning_days"]))


class WriteSafety(unittest.TestCase):
    def test_file_lock_blocks_second_writer(self):
        from common import file_lock
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "x.csv")
        with file_lock(p):
            with self.assertRaises(TimeoutError):
                with file_lock(p, wait=0.5): pass
        with file_lock(p, wait=0.5): pass   # 放開後可以再拿
    def test_incomplete_month_is_refetched(self):
        with open(os.path.join(ROOT, "03_工具", "fetch_prices.py"), encoding="utf-8") as f: src = f.read()
        self.assertNotIn('in have:', src)
        from fetch_prices import month_done
        today = __import__("datetime").date(2026, 10, 5)
        full = {f"2026-09-{d:02d}" for d in range(1, 21)}
        self.assertTrue(month_done(2026, 9, full, full, today))
        self.assertFalse(month_done(2026, 9, full - {"2026-09-10"}, full, today))       # 同市場有交易而自己沒有 → 重抓
        self.assertFalse(month_done(2026, 9, {"2026-09-01"}, set(), today))             # 沒得比對時至少要 15 天
        self.assertFalse(month_done(2026, 10, full, full, today))                        # 當月還沒結束
    def test_completeness_uses_market_trading_days(self):
        from pricedata import completeness
        days = [f"2026-09-{d:02d}" for d in range(1, 11)]
        mk = lambda ds: [(d, 1.0, 1) for d in ds]
        prices = {"A": mk(days), "B": mk(days), "C": mk([d for d in days if d != "2026-09-05"]), "MU": mk(days[:3]), "D": mk(days[4:])}
        listing = {"A": "TW", "B": "TW", "C": "TW", "D": "TW", "MU": "US"}
        self.assertEqual(completeness(prices, listing), {"C": ["2026-09-05"]})           # 美股不拿台股日期比；晚上市的 D 不算缺
    def test_key_is_never_read_from_project_folder(self):
        from fetch_prices import find_key
        import tempfile
        root, home = tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as f: f.write("TIINGO_API_KEY=INSIDE\n")
        key, src, warns = find_key({}, home, root)
        self.assertEqual(key, ""); self.assertTrue(any("專案資料夾裡有 .env" in w for w in warns))
        key, _, warns = find_key({"AI_STOCK_ENV_FILE": os.path.join(root, ".env")}, home, root)
        self.assertEqual(key, ""); self.assertTrue(any("不讀取" in w for w in warns))
        os.makedirs(os.path.join(home, ".ai_stock"))
        with open(os.path.join(home, ".ai_stock", ".env"), "w", encoding="utf-8") as f: f.write("# 註解\nTIINGO_API_KEY = \"OUTSIDE\"\n")
        self.assertEqual(find_key({}, home, root)[0], "OUTSIDE")
        self.assertEqual(find_key({"TIINGO_API_KEY": "ENV"}, home, root)[0], "ENV")
        self.assertFalse(any("OUTSIDE" in w or "INSIDE" in w for w in find_key({}, home, root)[2]))

    def test_shared_key_lookup_supports_fred_without_changing_tiingo(self):
        from fetch_prices import find_key
        import tempfile
        root, home = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".ai_stock"))
        with open(os.path.join(home, ".ai_stock", ".env"), "w", encoding="utf-8") as f:
            f.write("TIINGO_API_KEY=TIINGO_VALUE\nFRED_API_KEY=FRED_VALUE\n")
        self.assertEqual(find_key({}, home, root)[0], "TIINGO_VALUE")
        self.assertEqual(find_key({}, home, root, key_name="FRED_API_KEY")[0], "FRED_VALUE")
        self.assertEqual(find_key({"FRED_API_KEY": "FRED_ENV"}, home, root, key_name="FRED_API_KEY")[0], "FRED_ENV")

    def test_fred_api_parser_skips_missing_and_preserves_realtime_start(self):
        from fetch_macro import parse_fred_api
        payload = json.dumps({"observations": [
            {"date": "2026-09-01", "value": "4.21", "realtime_start": "2026-10-13"},
            {"date": "2026-09-02", "value": ".", "realtime_start": "2026-10-13"},
        ]})
        rows, realtime = parse_fred_api(payload, "DGS10", "now")
        self.assertEqual([(r["date"], r["value"], r["source"]) for r in rows],
                         [("2026-09-01", 4.21, "FRED API")])
        self.assertEqual(realtime[0]["realtime_start"], "2026-10-13")

    def test_alfred_parser_preserves_every_vintage_interval(self):
        from fetch_macro import parse_alfred_api
        payload = json.dumps({"observations": [
            {"date":"2024-01-01", "value":"314.438", "realtime_start":"2024-02-13", "realtime_end":"2025-02-11"},
            {"date":"2024-01-01", "value":"314.389", "realtime_start":"2025-02-12", "realtime_end":"2026-02-12"},
            {"date":"2024-02-01", "value":".", "realtime_start":"2024-03-12", "realtime_end":"2025-02-11"},
        ]})
        rows = parse_alfred_api(payload, "CPILFESL", "now")
        self.assertEqual([(r["value"], r["realtime_start"], r["realtime_end"]) for r in rows],
                         [(314.438,"2024-02-13","2025-02-11"),
                          (314.389,"2025-02-12","2026-02-12")])
        self.assertTrue(all(r["source"] == "ALFRED API" for r in rows))

    def test_alfred_append_only_keeps_multiple_versions_of_same_month(self):
        from fetch_macro import write_vintage_rows, load_macro_vintages
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "macro_vintages.csv")
        rows = [
            {"date":"2024-01-01","series_id":"CPILFESL","value":314.438,
             "realtime_start":"2024-02-13","realtime_end":"2025-02-11","source":"ALFRED API","fetched_at":"a"},
            {"date":"2024-01-01","series_id":"CPILFESL","value":314.389,
             "realtime_start":"2025-02-12","realtime_end":"2026-02-12","source":"ALFRED API","fetched_at":"a"},
        ]
        self.assertEqual(write_vintage_rows(rows, path)[0], 2)
        self.assertEqual(write_vintage_rows(rows, path)[0], 0)
        self.assertEqual(len(load_macro_vintages(path)["CPILFESL"]), 2)
        corrected = [dict(rows[1], realtime_end="9999-12-31", fetched_at="b")]
        self.assertEqual(write_vintage_rows(corrected, path)[0], 1)
        active = load_macro_vintages(path)["CPILFESL"]
        self.assertEqual(len(active), 2)
        self.assertEqual([r for r in active if r["realtime_start"] == "2025-02-12"][0]["realtime_end"],
                         "9999-12-31")

    def test_fred_failure_never_leaks_key_to_exception_output_or_files(self):
        from fetch_macro import fetch_fred_api, fetch_alfred_api, write_rows
        from fetch_prices import append_fetch_record
        import tempfile
        secret = "0123456789abcdef0123456789abcdef"
        def fail(url):
            raise OSError("forced " + url)
        with self.assertRaises(RuntimeError) as caught:
            fetch_fred_api("DGS10", "2026-01-01", "now", secret, getter=fail)
        self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(RuntimeError) as alfred_caught:
            fetch_alfred_api("UNRATE", "2026-01-01", "2026-10-16", "now", secret, getter=fail)
        self.assertNotIn(secret, str(alfred_caught.exception))
        root = tempfile.mkdtemp(); ledger = os.path.join(root, "fetch.jsonl")
        append_fetch_record({"error": str(caught.exception), "url": "https://x/?api_key=" + secret},
                            ledger, secrets=[secret])
        macro = os.path.join(root, "macro.csv"); realtime = os.path.join(root, "realtime.csv")
        write_rows([{"date": "2026-09-01", "series_id": "DGS10", "value": 4.2,
                    "source": "FRED API", "fetched_at": "now"}], "now",
                   [{"date": "2026-09-01", "series_id": "DGS10", "realtime_start": "2026-10-13",
                     "source": "FRED API", "fetched_at": "now"}], macro, realtime,
                   os.path.join(root, "diff.csv"))
        combined = "".join(pathlib.Path(x).read_text(encoding="utf-8") for x in (ledger, macro, realtime))
        self.assertNotIn(secret, combined)
        self.assertIn("[REDACTED]", combined)

    def test_fetch_summary_is_append_only_and_failure_returns_nonzero(self):
        from fetch_prices import finalize_fetch_record
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "抓取紀錄.jsonl")
        ok = [{"source": "X", "item_id": "A", "requested": 1, "succeeded": 1,
               "failed": 0, "added": 2, "conflicted": 0, "error_type": None}]
        bad = [{"source": "X", "item_id": "B", "requested": 1, "succeeded": 0,
                "failed": 1, "added": 0, "conflicted": 0, "error_type": "TimeoutError"}]
        self.assertEqual(finalize_fetch_record("tool", "R1", "start", ok, path=path)[1], 0)
        first = pathlib.Path(path).read_bytes()
        self.assertEqual(finalize_fetch_record("tool", "R2", "start", bad, path=path)[1], 1)
        after = pathlib.Path(path).read_bytes()
        self.assertTrue(after.startswith(first))
        rows = [json.loads(x) for x in after.decode("utf-8").splitlines()]
        self.assertEqual(rows[1]["exit_status"], "PARTIAL_FAILURE")
        self.assertEqual(rows[1]["overall"]["failed"], 1)

    def test_macro_api_append_keeps_existing_macro_bytes_and_uses_sidecar(self):
        from fetch_macro import write_rows
        import tempfile
        root = tempfile.mkdtemp(); macro = os.path.join(root, "macro.csv")
        realtime = os.path.join(root, "macro_realtime.csv")
        original = b"date,series_id,value,source,fetched_at\r\n2026-01-01,DGS10,4.0,old,old\r\n"
        with open(macro, "wb") as f: f.write(original)
        rows = [{"date": "2026-01-02", "series_id": "DGS10", "value": 4.1,
                 "source": "FRED API", "fetched_at": "now"}]
        rt = [{"date": "2026-01-02", "series_id": "DGS10", "realtime_start": "2026-10-13",
               "source": "FRED API", "fetched_at": "now"}]
        self.assertEqual(write_rows(rows, "now", rt, macro, realtime, os.path.join(root, "diff.csv")), (1, 0))
        self.assertTrue(pathlib.Path(macro).read_bytes().startswith(original))
        self.assertIn("realtime_start", pathlib.Path(realtime).read_text(encoding="utf-8"))

class DropAttribution(unittest.TestCase):
    """下跌歸因：用已知答案的合成資料檢查拆解。"""
    def ctx(self, shock, n=260):
        import attribution, datetime, random
        rnd = random.Random(11); d0 = datetime.date(2025, 1, 1)
        days = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]
        mkt = [rnd.gauss(0, 0.01) for _ in range(n)]
        betas = {"SOXX": 1.0, "MU": 1.5, "AVGO": 1.0, "ANET": 1.0, "MRVL": 1.2}
        prices = {}
        for t, b in betas.items():
            px, ser = 100.0, []
            for i, d in enumerate(days):
                r = b * mkt[i] + (0 if t == "SOXX" else rnd.gauss(0, 0.002))
                if i == n - 1: r += shock(t, b)
                px *= 1 + r; ser.append((d, px, 1000))
            prices[t] = ser
        return attribution, attribution.build_context(RULES, None, prices, {}), days[-1]
    def test_company_specific_drop(self):
        at, ctx, end = self.ctx(lambda t, b: -0.15 if t == "MU" else 0.0)
        r = at.decompose("MU", end, 1, ctx, RULES["attribution"])
        self.assertEqual(r["status"], "OK"); self.assertEqual(r["classification"], "主要是公司自身")
        self.assertAlmostEqual(r["company_part"], -0.15, delta=0.03)
        self.assertAlmostEqual(r["market_part"] + (r["industry_part"] or 0) + r["company_part"], r["total_return"], places=9)
        self.assertTrue(at.triggered(r, RULES["attribution"], 1))
    def test_market_wide_drop(self):
        at, ctx, end = self.ctx(lambda t, b: -0.08 * b)
        r = at.decompose("MU", end, 1, ctx, RULES["attribution"])
        self.assertEqual(r["classification"], "主要是大盤")
        self.assertAlmostEqual(r["market_part"], r["total_return"], delta=0.03)
    def test_sector_drop_beyond_market(self):
        at, ctx, end = self.ctx(lambda t, b: 0.0 if t == "SOXX" else -0.10)
        r = at.decompose("MU", end, 1, ctx, RULES["attribution"])
        self.assertTrue(r["classification"].startswith("主要是同"), r["classification"])
        self.assertAlmostEqual(r["industry_part"], -0.10, delta=0.03)
    def test_small_move_does_not_trigger(self):
        at, ctx, end = self.ctx(lambda t, b: -0.01 if t == "MU" else 0.0)
        self.assertFalse(at.triggered(at.decompose("MU", end, 1, ctx, RULES["attribution"]), RULES["attribution"], 1))
    def test_insufficient_history_and_benchmark_itself(self):
        at, ctx, end = self.ctx(lambda t, b: 0.0, n=60)
        self.assertTrue(at.decompose("MU", end, 1, ctx, RULES["attribution"])["status"].startswith("資料不足"))
        at, ctx, end = self.ctx(lambda t, b: 0.0)
        self.assertTrue(at.decompose("SOXX", end, 1, ctx, RULES["attribution"])["status"].startswith("不適用"))
    def test_beta_uses_only_data_before_the_window(self):
        at, ctx, end = self.ctx(lambda t, b: -0.15 if t == "MU" else 0.0)
        r1 = at.decompose("MU", end, 1, ctx, RULES["attribution"])
        at2, ctx2, _ = self.ctx(lambda t, b: -0.50 if t == "MU" else 0.0)
        self.assertAlmostEqual(r1["beta"], at2.decompose("MU", end, 1, ctx2, RULES["attribution"])["beta"], places=9)
    def test_shadow_and_never_a_trade_instruction(self):
        at, ctx, end = self.ctx(lambda t, b: -0.15 if t == "MU" else 0.0)
        r = at.decompose("MU", end, 1, ctx, RULES["attribution"]); r["triggered"] = True
        text = at.render(end, [r], RULES, {})
        self.assertEqual(RULES["attribution"]["status"], "SHADOW")
        self.assertIn("不是買賣建議", text)
        for word in ("建議買進", "建議賣出", "應該買", "應該賣出"): self.assertNotIn(word, text)
        with open(os.path.join(ROOT, "03_工具", "verdict.py"), encoding="utf-8") as fh: self.assertNotIn("attribution", fh.read())
    def test_ledger_records_only_triggered_once(self):
        import tempfile
        at, ctx, end = self.ctx(lambda t, b: -0.15 if t == "MU" else 0.0)
        old = at.LEDGER; at.LEDGER = os.path.join(tempfile.mkdtemp(), "e.csv")
        try:
            ev = at.scan_latest(RULES, ctx)
            self.assertEqual([e["ticker"] for e in ev], ["MU"])
            self.assertEqual(at.write_ledger(ev, RULES, {}), 1); self.assertEqual(at.write_ledger(ev, RULES, {}), 0)
        finally:
            at.LEDGER = old
    def test_history_report_carries_selection_bias_warning(self):
        at, ctx, end = self.ctx(lambda t, b: 0.0)
        text = "\n".join(at.history(RULES, ctx)[0])
        self.assertIn("事後挑選", text); self.assertIn("不能當成買進依據", text)

class MarketEnvironment(unittest.TestCase):
    """市場環境：用已知答案的合成數列檢查七個指標與燈號。"""
    AS_OF = "2026-09-30"
    def daily(self, start_v, end_v, n=60):
        import datetime
        d = datetime.date.fromisoformat(self.AS_OF); out_ = []
        days = []
        while len(days) < n:
            if d.weekday() < 5: days.append(d.isoformat())
            d -= datetime.timedelta(days=1)
        days.reverse()
        for i, x in enumerate(days):
            out_.append((x, end_v if i >= n - 1 else (start_v if i <= n - 21 else start_v)))
        return out_
    def monthly(self, values):
        import datetime
        y, m = 2026, 8; out_ = []
        for v in reversed(values):
            out_.append((f"{y:04d}-{m:02d}-01", v)); m -= 1
            if m == 0: y, m = y - 1, 12
        return sorted(out_)
    def macro(self, bad=False):
        cpi = [100 * (1.03 ** (i / 12)) for i in range(20)]
        # 最近三個月：壞情境物價加速（年增率上升），好情境物價幾乎不動（年增率下降）
        step = 1.004 if bad else 1.0005
        cpi = cpi[:17] + [cpi[16] * step, cpi[16] * step ** 2, cpi[16] * step ** 3]
        return {"DFEDTARU": self.daily(4.00, 4.25 if bad else 3.75), "DGS10": self.daily(4.00, 4.40 if bad else 3.60),
                "DCOILBRENTEU": self.daily(80, 92 if bad else 70), "UNRATE": self.monthly([4.0, 4.0, 4.0, 4.0, 4.5 if bad else 3.8]),
                "CPILFESL": self.monthly(cpi), "BAMLH0A0HYM2": self.daily(3.00, 3.80 if bad else 2.60)}
    def idx(self, trend):
        import datetime
        d = datetime.date.fromisoformat(self.AS_OF); days = []
        while len(days) < 260:
            if d.weekday() < 5: days.append(d.isoformat())
            d -= datetime.timedelta(days=1)
        days.reverse()
        return [(x, 100 * (1 + trend) ** i) for i, x in enumerate(days)]
    def fomc(self, decision):
        return [{"statement_date":"2026-09-16", "decision":decision,
                 "target_upper_pct":4.0, "source_url":"https://www.federalreserve.gov/test"}]
    def test_all_bad_is_red_and_all_good_is_green(self):
        import market_env as me
        cfg = RULES["market_env"]
        bad = me.assess(me.compute(self.macro(True), self.idx(-0.002), self.AS_OF, cfg, point_in_time=False,
                                   fomc_decisions=self.fomc("升息")), RULES)
        self.assertEqual((bad["status"], bad["filled"]), ("紅", 7)); self.assertEqual(set(bad["scores"].values()), {-1})
        good = me.assess(me.compute(self.macro(False), self.idx(0.002), self.AS_OF, cfg, point_in_time=False,
                                    fomc_decisions=self.fomc("降息")), RULES)
        self.assertEqual((good["status"], good["filled"]), ("綠", 7)); self.assertEqual(set(good["scores"].values()), {1})
    def test_exact_values(self):
        import market_env as me
        v = me.compute(self.macro(True), self.idx(-0.002), self.AS_OF, RULES["market_env"], point_in_time=False,
                       fomc_decisions=self.fomc("升息"))
        self.assertEqual(v["M1"]["value"], "升息"); self.assertAlmostEqual(v["M2"]["value"], 40.0)
        self.assertAlmostEqual(v["M3"]["value"], 15.0); self.assertAlmostEqual(v["M4"]["value"], 0.5)
        self.assertAlmostEqual(v["M7"]["value"], 80.0); self.assertLess(v["M6"]["value"], -5)
    def test_alfred_initial_release_ignores_later_revision(self):
        import market_env as me
        rows = {"UNRATE": [
            {"date":"2026-05-01","value":4.0,"realtime_start":"2026-06-05","realtime_end":"2027-01-01"},
            {"date":"2026-05-01","value":9.0,"realtime_start":"2027-01-02","realtime_end":"9999-12-31"},
            {"date":"2026-06-01","value":4.1,"realtime_start":"2026-07-02","realtime_end":"9999-12-31"},
            {"date":"2026-07-01","value":4.2,"realtime_start":"2026-08-07","realtime_end":"9999-12-31"},
            {"date":"2026-08-01","value":4.5,"realtime_start":"2026-09-04","realtime_end":"9999-12-31"},
        ], "CPILFESL": []}
        got = me.compute_initial_m4_m5(rows, "2026-09-30", RULES["market_env"])
        self.assertEqual((got["M4"]["value"], got["M4"]["data_date"], got["M4"]["release_date"]),
                         (0.5, "2026-08-01", "2026-09-04"))
        self.assertEqual(got["M4"]["basis"], "ALFRED_INITIAL_RELEASE")
    def test_shadow_comparison_does_not_change_formal_assessment(self):
        import market_env as me
        values = me.compute(self.macro(True), self.idx(-0.002), self.AS_OF, RULES["market_env"],
                            point_in_time=False, fomc_decisions=self.fomc("升息"))
        before = me.assess(values, RULES)
        initial = {"M4":{"value":-1.0,"data_date":"2026-08-01","release_date":"2026-09-01","reason":"initial"},
                   "M5":{"value":-1.0,"data_date":"2026-08-01","release_date":"2026-09-01","reason":"initial"}}
        text = me.render(self.AS_OF, values, before, RULES, initial)
        self.assertEqual(me.assess(values, RULES), before)
        self.assertIn("正式欄位、分數與燈號仍使用上表修訂值", text)
        self.assertIn("ALFRED", me.compute_initial_m4_m5.__doc__)
    def test_missing_or_stale_data_is_insufficient_not_neutral(self):
        import market_env as me
        a = me.assess(me.compute({}, self.idx(0.002), self.AS_OF, RULES["market_env"], point_in_time=False), RULES)
        self.assertEqual((a["status"], a["filled"]), ("資料不足", 2))
        v = me.compute(self.macro(False), self.idx(0.002), "2026-12-31", RULES["market_env"], point_in_time=False)   # 三個月後才看：全部過期
        self.assertTrue(all(v[k]["value"] is None for k in ("M2","M3","M4","M5","M6","M7")))
        self.assertIn("過期", v["M2"]["reason"])
    def test_no_lookahead(self):
        import market_env as me
        m = self.macro(False); m2 = {k: v + [("2026-10-15", 999.0)] for k, v in m.items()}
        a = me.compute(m, self.idx(0.002), self.AS_OF, RULES["market_env"], point_in_time=False); b = me.compute(m2, self.idx(0.002) + [("2026-10-15", 1.0)], self.AS_OF, RULES["market_env"], point_in_time=False)
        self.assertEqual({k: x["value"] for k, x in a.items()}, {k: x["value"] for k, x in b.items()})
    def test_m1_changes_only_on_official_statement_dates(self):
        import market_env as me
        decisions = [{"statement_date":"2022-03-16", "decision":"升息", "target_upper_pct":0.5,
                      "source_url":"https://www.federalreserve.gov/a"},
                     {"statement_date":"2022-05-04", "decision":"升息", "target_upper_pct":1.0,
                      "source_url":"https://www.federalreserve.gov/b"},
                     {"statement_date":"2022-06-15", "decision":"升息", "target_upper_pct":1.75,
                      "source_url":"https://www.federalreserve.gov/c"}]
        for as_of in ("2022-03-16", "2022-03-31", "2022-04-30", "2022-05-03"):
            self.assertEqual(me.compute({}, [], as_of, RULES["market_env"], fomc_decisions=decisions)["M1"]["data_date"],
                             "2022-03-16")
        self.assertEqual(me.compute({}, [], "2022-05-04", RULES["market_env"], fomc_decisions=decisions)["M1"]["data_date"],
                         "2022-05-04")

    def test_official_fomc_table_is_stable_between_every_2022_2026_meeting(self):
        import market_env as me, datetime, csv
        path = os.path.join(ROOT, "01_共用資料", "fomc_decisions.csv")
        with open(path, encoding="utf-8-sig", newline="") as f:
            decisions = list(csv.DictReader(f))
        target = [r for r in decisions if "2022-01-01" <= r["statement_date"] <= "2026-12-31"]
        self.assertGreaterEqual(len(target), 35)
        for index, row in enumerate(target):
            start = datetime.date.fromisoformat(row["statement_date"])
            next_date = (datetime.date.fromisoformat(target[index + 1]["statement_date"])
                         if index + 1 < len(target) else datetime.date(2026, 12, 31))
            for date in {start, next_date - datetime.timedelta(days=1)}:
                got = me.compute({}, [], date.isoformat(), RULES["market_env"], fomc_decisions=decisions)["M1"]
                self.assertEqual((got["data_date"], got["value"]), (row["statement_date"], row["decision"]))
    def test_uses_same_thresholds_as_rules_market_layer(self):
        import market_env as me
        self.assertEqual(set(me.compute({}, [], self.AS_OF, RULES["market_env"], point_in_time=False)), set(RULES["market"]["indicators"]))
        self.assertEqual(RULES["market_env"]["status"], "SHADOW")
        with open(os.path.join(ROOT, "03_工具", "verdict.py"), encoding="utf-8") as fh: self.assertNotIn("market_env", fh.read())
    def test_fred_csv_parser(self):
        from fetch_macro import parse_fred_csv
        rows = parse_fred_csv("observation_date,DGS10\n2026-09-01,4.21\n2026-09-02,.\n2026-09-03,\n2026-09-04,4.30\n", "DGS10", "now")
        self.assertEqual([(r["date"], r["value"]) for r in rows], [("2026-09-01", 4.21), ("2026-09-04", 4.30)])
        self.assertEqual(len(parse_fred_csv("\ufeffDATE,UNRATE\n2026-08-01,4.2\n", "UNRATE", "now")), 1)
        for bad in ("<html>error</html>", "observation_date,DGS10\n", "observation_date,DGS10\nnot-a-date,1\n"):
            with self.assertRaises(ValueError): parse_fred_csv(bad, "DGS10", "now")
    def test_report_is_not_a_trade_instruction(self):
        import market_env as me
        v = me.compute(self.macro(True), self.idx(-0.002), self.AS_OF, RULES["market_env"], point_in_time=False); a = me.assess(v, RULES)
        text = me.render(self.AS_OF, v, a, RULES)
        self.assertIn("市場風險：高", text); self.assertIn("不是買賣建議", text)
        for w in ("建議賣出", "建議買進", "應該賣", "應該買"): self.assertNotIn(w, text)

class RevenueTrend(unittest.TestCase):
    CUTOFF = "2026-11-15T23:59:59+00:00"
    Q = ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
    def rows(self, revs, gps=None, issuer="ISSUER_MICRON"):
        out_ = []; n = 0
        for end, v in zip(self.Q, revs):
            for metric, val in (("FIN_REVENUE", v), ("FIN_GROSS_PROFIT", None if gps is None else gps[self.Q.index(end)])):
                if val is None: continue
                n += 1
                out_.append({"observation_id": f"{issuer}{n}", "issuer_id": issuer, "security_id": None, "metric_id": metric, "value": val,
                             "unit": "USD", "period_start": None, "period_end": end, "period_type": "Q", "source_url": "https://www.sec.gov/",
                             "source_published_at": "2026-10-31T23:59:59+00:00", "available_at": "2026-10-31T23:59:59+00:00",
                             "ingested_at": "2026-10-31T23:59:59+00:00", "availability_status": "POINT_IN_TIME_KNOWN", "supersedes_observation_id": None})
        return out_
    def ev(self, revs, gps=None):
        import trend
        return trend.evaluate_issuer("ISSUER_MICRON", self.rows(revs, gps), RULES, self.CUTOFF)
    def test_accelerating(self):
        # 去年 Q2 100、Q3 100；今年 Q2 120（+20%）、Q3 140（+40%）→ C1 = 40、C2 = +20
        r = self.ev([100, 100, 105, 110, 120, 140], [40, 40, 42, 44, 50, 63])
        self.assertAlmostEqual(r["C1"], 40.0); self.assertAlmostEqual(r["C2"], 20.0)
        self.assertAlmostEqual(r["C3"], 5.0)            # 毛利率 40% → 45%
        self.assertEqual((r["label"], r["scores"]), ("加速成長", {"C1": 1, "C2": 1, "C3": 1}))
    def test_decelerating_and_decline(self):
        r = self.ev([100, 100, 105, 110, 150, 125])     # +50% → +25%
        self.assertEqual(r["label"], "成長但減速"); self.assertEqual(r["scores"]["C2"], -1)
        r = self.ev([100, 100, 105, 110, 95, 90])
        self.assertEqual(r["label"], "衰退"); self.assertEqual(r["scores"]["C1"], -1)
    def test_missing_pieces_are_not_guessed(self):
        r = self.ev([None, None, None, None, 120, 140])
        self.assertEqual((r["status"], r["C1"]), ("資料不足", None))
        r = self.ev([None, 100, 105, 110, 120, 140])    # 有今年與去年 Q3，但去年 Q2 缺 → 算得出 C1，算不出 C2
        self.assertAlmostEqual(r["C1"], 40.0); self.assertIsNone(r["C2"]); self.assertEqual(r["status"], "部分計算")
        r = self.ev([100, 100, 105, 110, 120, 140])     # 沒有毛利
        self.assertIsNone(r["C3"]); self.assertIsNone(r["scores"]["C3"])
    def test_stale_and_cutoff(self):
        import trend
        rows = self.rows([100, 100, 105, 110, 120, 140])
        self.assertEqual(trend.evaluate_issuer("ISSUER_MICRON", rows, RULES, "2027-06-01T00:00:00+00:00")["status"], "資料不足")
        self.assertEqual(trend.evaluate_issuer("ISSUER_MICRON", rows, RULES, "2026-10-01T00:00:00+00:00")["status"], "資料不足")  # 當時還沒申報
    def test_theme_breadth(self):
        import trend
        from data_spine import load_registry
        reg = load_registry()
        obs = self.rows([100, 100, 105, 110, 120, 140], issuer="ISSUER_MICRON")
        res = trend.evaluate_all(obs, RULES, self.CUTOFF, reg)
        th = trend.theme_breadth(res, RULES, reg)
        self.assertIsNone(th["memory"]["value"])        # 記憶體名單 3 家只有 1 家有資料，不到一半
        self.assertIn("不到一半", th["memory"]["reason"])
    def test_shadow_isolation_and_wording(self):
        import trend
        from data_spine import load_registry
        reg = load_registry(); res = trend.evaluate_all(self.rows([100, 100, 105, 110, 120, 140]), RULES, self.CUTOFF, reg)
        text = trend.render("2026-11-15", res, trend.theme_breadth(res, RULES, reg), RULES, reg)
        self.assertEqual(RULES["trend"]["status"], "SHADOW"); self.assertIn("不是買賣建議", text)
        for w in ("建議買進", "建議賣出", "應該買", "應該賣"): self.assertNotIn(w, text)
        with open(os.path.join(ROOT, "03_工具", "verdict.py"), encoding="utf-8") as fh: self.assertNotIn("trend", fh.read())

    def test_52_53_week_period_matching_boundaries(self):
        import trend
        annual = {"2025-06-21": 100, "2026-06-27": 120}
        quarterly = {"2026-03-21": 100, "2026-06-27": 120}
        self.assertEqual(trend._same_quarter_last_year(annual, "2026-06-27"), "2025-06-21")  # 371 天
        self.assertEqual(trend._prev_quarter(quarterly, "2026-06-27"), "2026-03-21")          # 98 天

    def test_trend_ledger_appends_correction_chain_by_data_id(self):
        import trend, tempfile, csv
        reg = {"securities":[{"issuer_id":"ISSUER_MICRON","ticker":"MU"}]}
        old = trend.LEDGER; trend.LEDGER = os.path.join(tempfile.mkdtemp(), "trend.csv")
        try:
            first = self.ev([100,100,105,110,120,140], [40,40,42,44,50,63])
            corrected = self.ev([100,100,105,110,120,140], [40,40,42,44,50,70])
            self.assertEqual(trend.write_ledger("2026-11-15", {"ISSUER_MICRON":first}, reg, RULES), 1)
            self.assertEqual(trend.write_ledger("2026-11-15", {"ISSUER_MICRON":first}, reg, RULES), 0)
            self.assertEqual(trend.write_ledger("2026-11-15", {"ISSUER_MICRON":corrected}, reg, RULES), 1)
            with open(trend.LEDGER, encoding="utf-8-sig", newline="") as fh: rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["data_id"], rows[1]["data_id"])
            self.assertEqual(rows[1]["supersedes_record_id"], rows[0]["record_id"])
        finally:
            trend.LEDGER = old

class MarketEnvHistory(unittest.TestCase):
    def test_current_partial_month_is_not_a_month_end(self):
        import market_env as me, datetime
        d = datetime.date(2025, 1, 1); idx = []
        while d <= datetime.date(2026, 3, 10):
            if d.weekday() < 5: idx.append((d.isoformat(), 100.0 + len(idx)))
            d += datetime.timedelta(days=1)
        text = "\n".join(me.history(RULES, {}, idx))
        self.assertIn("資料不足", text)
        import re
        m = re.search(r"資料不足（資料不足） \| (\d+) ", text)
        self.assertEqual(int(m.group(1)), 14)   # 2025-01 到 2026-02 共 14 個完整月份；2026-03 還沒結束，不算

class Patch162(unittest.TestCase):
    """Codex 對 1.4.0／1.5.0 複查的四項：C150-01、C150-02、C140-01、C140-02。"""
    def test_c150_01_monthly_data_not_usable_before_publication(self):
        import market_env as me
        cfg = RULES["market_env"]
        un = [(f"2026-{m:02d}-01", v) for m, v in ((4, 4.0), (5, 4.0), (6, 4.0), (7, 4.0), (8, 4.0), (9, 4.6))]
        v = me.compute({"UNRATE": un}, [], "2026-09-30", cfg)             # 9 月的失業率 10 月才公布
        self.assertAlmostEqual(v["M4"]["value"], 0.0); self.assertEqual(v["M4"]["data_date"], "2026-08-01")
        v = me.compute({"UNRATE": un}, [], "2026-10-12", cfg)             # 公布延遲 40 天之後才看得到
        self.assertAlmostEqual(v["M4"]["value"], 0.6); self.assertEqual(v["M4"]["data_date"], "2026-09-01")
        live = me.compute({"UNRATE": un}, [], "2026-09-30", cfg, point_in_time=False)
        self.assertEqual(live["M4"]["data_date"], "2026-09-01")           # 只有「現在執行看最新」才不套延遲
        self.assertEqual(set(cfg["availability_lag_days"]), set(cfg["series"]))
    def test_c150_01_history_uses_point_in_time(self):
        with open(os.path.join(ROOT, "03_工具", "market_env.py"), encoding="utf-8") as fh: src = fh.read()
        self.assertIn("a = assess(compute(macro, idx, d, cfg), rules)", src)   # 回補統計走預設的 point_in_time=True
        self.assertIn("point_in_time=explicit", src)
    def test_c150_02_market_ledger_keeps_corrected_result(self):
        import market_env as me, tempfile, csv
        old = me.LEDGER; me.LEDGER = os.path.join(tempfile.mkdtemp(), "m.csv")
        try:
            empty = me.compute({}, [], "2026-09-30", RULES["market_env"]); a0 = me.assess(empty, RULES)
            self.assertEqual(me.write_ledger("2026-09-30", empty, a0, RULES), 1)
            self.assertEqual(me.write_ledger("2026-09-30", empty, a0, RULES), 0)        # 輸入相同不重複
            full = dict(empty); full["M4"] = {"value": 0.5, "data_date": "2026-08-01", "reason": ""}
            self.assertEqual(me.write_ledger("2026-09-30", full, me.assess(full, RULES), RULES), 1)   # 補資料後要留下新結果
            with open(me.LEDGER, encoding="utf-8") as f: rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2); self.assertEqual(rows[1]["supersedes_record_id"], rows[0]["record_id"])
            self.assertNotEqual(rows[0]["input_sha256"], rows[1]["input_sha256"])
        finally:
            me.LEDGER = old
    def test_c140_01_event_ledger_keeps_corrected_result(self):
        import tempfile, csv, attribution as at
        da = DropAttribution()
        _, ctx1, end = da.ctx(lambda t, b: -0.10 if t == "MU" else 0.0)
        _, ctx2, _ = da.ctx(lambda t, b: -0.20 if t == "MU" else 0.0)
        old = at.LEDGER; at.LEDGER = os.path.join(tempfile.mkdtemp(), "e.csv")
        try:
            e1 = at.scan_latest(RULES, ctx1); e2 = at.scan_latest(RULES, ctx2)
            self.assertNotEqual(e1[0]["data_id"], e2[0]["data_id"])
            self.assertEqual(at.write_ledger(e1, RULES, {}), 1); self.assertEqual(at.write_ledger(e1, RULES, {}), 0)
            self.assertEqual(at.write_ledger(e2, RULES, {}), 1)                          # 價格更正後要留下新拆解
            with open(at.LEDGER, encoding="utf-8") as f: rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2); self.assertEqual(rows[1]["supersedes_event_id"], rows[0]["event_id"])
            self.assertAlmostEqual(float(rows[0]["total_return"]), e1[0]["total_return"], places=5)   # 舊列沒被改寫
        finally:
            at.LEDGER = old
    def test_c140_02_event_health_ignores_filings_after_event(self):
        import attribution as at, health, data_spine
        seen = {}
        def fake_eval(issuer_id, rows, rules, cutoff, is_etf=False):
            seen["cutoff"] = cutoff; seen["avail"] = [r["available_at"] for r in rows]
            return {"status": "穩健", "indicators": {}}
        rows = [{"issuer_id": "ISSUER_MICRON", "available_at": "2026-09-25T23:59:59-05:00", "ingested_at": "2026-10-06T22:00:00+08:00"},
                {"issuer_id": "ISSUER_MICRON", "available_at": "2026-10-05T23:59:59-05:00", "ingested_at": "2026-10-06T22:00:00+08:00"}]
        o1, o2 = health.evaluate_issuer, data_spine.read_observations_checked
        health.evaluate_issuer = fake_eval; data_spine.read_observations_checked = lambda path=None: rows
        try:
            got = at.health_as_of_event(RULES, [{"status": "OK", "ticker": "MU", "end_date": "2026-10-02", "market": "US"}])
        finally:
            health.evaluate_issuer, data_spine.read_observations_checked = o1, o2
        self.assertEqual(seen["cutoff"], "2026-10-02T15:59:59-04:00")
        self.assertEqual(seen["avail"], ["2026-09-25T23:59:59-05:00"])                   # 10/5 才申報的不可用在 10/2 的事件
        self.assertEqual(got[("MU", "2026-10-02")][0], "穩健")
        with open(os.path.join(ROOT, "03_工具", "attribution.py"), encoding="utf-8") as fh: self.assertNotIn("live=", fh.read())

class Patch163(unittest.TestCase):
    """Codex 對 1.6.2 複查的兩項：C162-01 事件截止要用收盤時刻、C162-02 月資料新鮮度門檻不得暗改。"""
    def _run(self, avail, end, market="US"):
        import attribution as at, health, data_spine
        seen = {}
        def fake_eval(issuer_id, rows, rules, cutoff, is_etf=False):
            seen["avail"] = [r["available_at"] for r in rows]; return {"status": "穩健", "indicators": {}}
        rows = [{"issuer_id": "ISSUER_MICRON", "available_at": a, "ingested_at": a} for a in avail]
        o1, o2, o3 = health.evaluate_issuer, data_spine.read_observations_checked, at._session_close
        health.evaluate_issuer = fake_eval; data_spine.read_observations_checked = lambda path=None: rows; at._session_close = lambda d, m: None
        try:
            at.health_as_of_event(RULES, [{"status": "OK", "ticker": "MU", "end_date": end, "market": market}])
        finally:
            health.evaluate_issuer, data_spine.read_observations_checked, at._session_close = o1, o2, o3
        return seen["avail"]
    def test_c162_01_same_day_after_close_filing_excluded(self):
        pre, at_close, post, eod = ("2026-10-02T15:59:59-04:00", "2026-10-02T16:00:00-04:00",
                                    "2026-10-02T16:30:00-04:00", "2026-10-02T23:59:59-05:00")
        self.assertEqual(self._run([pre, at_close, post, eod], "2026-10-02"), [pre])
    def test_c162_01_dst_boundaries(self):
        import attribution as at
        old = at._session_close; at._session_close = lambda d, m: None
        try:
            self.assertEqual(at.event_cutoff("2026-10-02", "US"), "2026-10-02T15:59:59-04:00")   # 夏令
            self.assertEqual(at.event_cutoff("2026-12-01", "US"), "2026-12-01T15:59:59-05:00")   # 冬令
            self.assertEqual(at.event_cutoff("2026-11-02", "US"), "2026-11-02T15:59:59-05:00")   # 11/1 切換後第一個交易日
            self.assertEqual(at.event_cutoff("2026-10-02", "TW"), "2026-10-02T13:29:59+08:00")
            self.assertEqual(at.event_cutoff("2026-10-02", "??"), "2026-10-01T23:59:59+00:00")   # 不認得的市場從嚴
        finally:
            at._session_close = old
        self.assertEqual(self._run(["2026-12-01T15:59:59-05:00", "2026-12-01T16:00:00-05:00"], "2026-12-01"), ["2026-12-01T15:59:59-05:00"])
    def test_c162_01_calendar_close_takes_priority(self):
        import attribution as at
        from data_spine import parse_time
        old = at._session_close; at._session_close = lambda d, m: parse_time("2026-11-27T13:00:00-05:00", "close_at")   # 半日市
        try:
            self.assertEqual(at.event_cutoff("2026-11-27", "US"), "2026-11-27T12:59:59-05:00")
        finally:
            at._session_close = old
    def test_c162_02_monthly_stale_gate_unchanged_from_1_6_1(self):
        import market_env as me
        with open(os.path.join(ROOT, "00_規範", "rules_歷史", "rules_1.6.1.json"), encoding="utf-8") as fh: old = __import__("json").load(fh)
        cfg = RULES["market_env"]
        for k in ("monthly_stale_days", "daily_stale_days", "change_days", "fed_lookback_days"):
            self.assertEqual(cfg[k], old["market_env"][k], k)
        self.assertEqual(cfg["monthly_stale_days"], 75)
        un = [(f"2026-{m:02d}-01", 4.0) for m in (2, 3, 4, 5, 6)] + [("2026-07-02", 4.1)]
        v = me.compute({"UNRATE": un}, [], "2026-09-30", cfg)          # 距基準日 90 天，超過 75 天要算過期
        self.assertIsNone(v["M4"]["value"]); self.assertIn("過期", v["M4"]["reason"])

class CompanySummary(unittest.TestCase):
    """公司層彙整報告：只彙整，不新增門檻、不下結論、不寫帳本、不影響 verdict。"""
    def _src(self, name):
        with open(os.path.join(ROOT, "03_工具", name), encoding="utf-8") as fh: return fh.read()
    def test_isolated_from_verdict_and_ledgers(self):
        src = self._src("company.py")
        for bad in ("import verdict", "from verdict", "write_ledger", "rules[\"company\"]", "rules['company']"):
            self.assertNotIn(bad, src)
        self.assertNotIn("company", [l.split()[1].split(".")[0] for l in self._src("verdict.py").splitlines() if l.startswith(("import ", "from "))])
    def test_report_has_no_advice_wording_and_lists_gaps(self):
        import company
        rows = company.build(RULES)
        text = company.render("2026-10-07", rows, RULES)
        body = text.replace("不是投資建議，也不是買賣指令", "")
        for bad in ("買進", "賣出", "買賣", "目標價", "建議", "加碼", "減碼", "總分："):
            self.assertNotIn(bad, body, bad)
        self.assertIn("影子模式", text)
        self.assertTrue(rows); self.assertFalse([r for r in rows if r["ticker"] in ("SOXX", "0050")])
        for r in rows:
            self.assertTrue(any("估值" in m for m in r["missing"])); self.assertTrue(any("新聞" in m for m in r["missing"]))
    def test_missing_data_is_reported_not_guessed(self):
        import company
        reg = {"issuers": [{"issuer_id": "I_X", "issuer_name": "X"}],
               "securities": [{"security_id": "S", "issuer_id": "I_X", "ticker": "ZZZZ", "market": "US", "security_type": "COMMON_STOCK"}]}
        rows = company.build(RULES, registry=reg, observations=[], ctx={"tr": {}, "actions": [], "listing": {}, "peers": {}, "basket": {}, "bm": {}})
        self.assertEqual(len(rows), 1); miss = "；".join(rows[0]["missing"])
        for need in ("單季營收年增率", "營收年增率比上一季的變動", "毛利率與去年同季的變動", "財務體質", "價格"): self.assertIn(need, miss)
        one = company.render_one(rows[0])
        self.assertIn("營收趨勢：資料不足", one); self.assertIn("財務體質：資料不足", one); self.assertIn("近期價格：資料不足", one)
    def test_c170_01_c2_c3_labels_and_units(self):
        import company
        r = {"ticker": "X", "name": "X", "market": "US", "health": None, "moves": {}, "missing": [],
             "trend": {"status": "已計算", "label": "加速成長", "C1": 345.7, "C2": 149.4, "C3": 46.8, "gross_margin_pct": 84.6, "latest_period_end": "2026-05-28"}}
        one = company.render_one(r)
        self.assertIn("單季營收年增率 +345.7%", one)
        self.assertIn("年增率比上一季變動 +149.4 個百分點", one)
        self.assertIn("毛利率比去年同季變動 +46.8 個百分點", one)
        self.assertIn("本季毛利率 84.6%", one)
        for bad in ("季增", "上一季年增", "+149.4%", "+46.8%"): self.assertNotIn(bad, one, bad)
        r["trend"] = dict(r["trend"], C2=None, C3=None, gross_margin_pct=None)
        one = company.render_one(r)
        self.assertIn("年增率比上一季變動 缺", one); self.assertIn("毛利率比去年同季變動 缺", one)
        with open(os.path.join(ROOT, "03_工具", "trend.py"), encoding="utf-8") as fh: src = fh.read()
        self.assertIn('res["C2"] = c1 - prev_yoy', src)                       # 定義若改，這裡的文字也要跟著檢查
        self.assertIn("gp[end] / rev[end] - gp[ly] / rev[ly]", src)

class MopsFinancialAdapter(unittest.TestCase):
    def _xhtml(self):
        return b'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full">
 <body><div>2330 2025Q1 ci-cr</div>
  <xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="x">2330</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="d"><xbrli:entity><xbrli:identifier scheme="x">2330</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="x:y">x:z</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="q" unitRef="TWD" scale="3">123</ix:nonFraction>
  <ix:nonFraction name="ifrs-full:GrossProfit" contextRef="q" unitRef="TWD" sign="-">200</ix:nonFraction>
  <ix:nonFraction name="ifrs-full:ProfitLossFromOperatingActivities" contextRef="q" unitRef="TWD" xsi:nil="true" />
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="d" unitRef="TWD">999</ix:nonFraction>
 </body></html>'''

    def _filing(self, season, scope="CONSOLIDATED"):
        return {"ticker": "2330", "issuer_id": "ISSUER_TSMC", "year": 2025, "season": season,
                "report_scope": scope, "url": "https://example.com", "raw_sha256": "a" * 64,
                "mapping_version": "0.6.0", "facts": {},
                "manifest": {"fetched_at": "2026-10-07T12:00:00+08:00",
                             "manifest_record_id": f"M{season}", "revision_sequence": season,
                             "doc_upload_at": "2025-05-01T12:00:00+08:00",
                             "correction_listed": False, "first_seen_at": None}}

    def test_ixbrl_scale_sign_nil_and_dimension_are_handled(self):
        import fetch_mops_financials as m
        facts, _ = m.parse_facts(self._xhtml())
        ns = "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"
        self.assertEqual([f["value"] for f in facts[(ns, "Revenue")]], [123000.0])
        self.assertEqual(facts[(ns, "GrossProfit")][0]["value"], -200.0)
        self.assertNotIn((ns, "ProfitLossFromOperatingActivities"), facts)

    def test_saved_official_mops_fixtures_parse_offline(self):
        import fetch_mops_financials as m, json
        with open(os.path.join(ROOT, "00_規範", "financial_tag_map.json"), encoding="utf-8") as src:
            tag_map = json.load(src)
        p2330 = sorted(glob.glob(os.path.join(ROOT, "01_共用資料", "data_spine", "raw", "mops", "2330", "2024Q1_C_*.xhtml")))[0]
        p3081 = sorted(glob.glob(os.path.join(ROOT, "01_共用資料", "data_spine", "raw", "mops", "3081", "2026Q2_A_*.xhtml")))[0]
        with open(p2330, "rb") as f: d2330 = f.read()
        with open(p3081, "rb") as f: d3081 = f.read()
        self.assertEqual(m.validate_raw(d2330, "2330", 2024, 1, "CONSOLIDATED"), [])
        self.assertEqual(m.validate_raw(d3081, "3081", 2026, 2, "INDIVIDUAL"), [])
        facts, _ = m.parse_facts(d2330)
        revenue = m.choose_fact(facts, m._mapping_for(tag_map, "ISSUER_TSMC", "FIN_REVENUE"), 2024, 1, "quarter")
        self.assertIsNotNone(revenue); self.assertGreater(revenue["value"], 0)
        p2330q2 = sorted(glob.glob(os.path.join(ROOT, "01_共用資料", "data_spine", "raw", "mops", "2330", "2024Q2_C_*.xhtml")))[0]
        with open(p2330q2, "rb") as f: q2facts, _ = m.parse_facts(f.read())
        mapping = m._mapping_for(tag_map, "ISSUER_TSMC", "FIN_REVENUE")
        quarter = m.choose_fact(q2facts, mapping, 2024, 2, "quarter")
        ytd = m.choose_fact(q2facts, mapping, 2024, 2, "ytd")
        self.assertTrue(quarter and ytd); self.assertNotEqual(quarter["start"], ytd["start"])
        with open(os.path.join(ROOT, "03_工具", "trend.py"), encoding="utf-8") as src:
            trend_source = src.read()
        self.assertNotIn("台股與台積電的財報數字還沒有資料來源", trend_source)

    def test_namespace_is_exact_and_scope_mismatch_is_rejected(self):
        import fetch_mops_financials as m
        facts, _ = m.parse_facts(self._xhtml())
        self.assertIsNone(m.choose_fact(facts, [{"namespace": "https://wrong", "local_name": "Revenue"}], 2025, 1, "quarter"))
        self.assertIn("REPORT_SCOPE_MISMATCH", m.validate_raw(self._xhtml(), "2330", 2025, 1, "INDIVIDUAL"))

    def test_only_official_no_file_page_is_a_confirmed_absence(self):
        import fetch_mops_financials as m
        absent = "<html><body>下載檔名或路徑不正確，請檢查!!</body></html>".encode("big5")
        limited = b"<html><body>rate limit, retry later</body></html>"
        self.assertTrue(m.confirmed_no_filing_response(absent))
        self.assertFalse(m.confirmed_no_filing_response(limited))
        self.assertTrue(m.validate_raw(absent, "3081", 2026, 2, "CONSOLIDATED"))
        self.assertTrue(m.validate_raw(limited, "3081", 2026, 2, "CONSOLIDATED"))
        self.assertFalse(m.previous_poll_proves_change([{"poll_result": "ERROR"}], "a" * 64))
        self.assertFalse(m.previous_poll_proves_change([{"poll_result": "NOT_FOUND"}], "a" * 64))
        self.assertTrue(m.previous_poll_proves_change([{"poll_result": "NOT_FOUND", "absence_basis": "OFFICIAL_NO_FILE_MESSAGE"}], "a" * 64))
        self.assertTrue(m.previous_poll_proves_change([{"poll_result": "FOUND", "raw_sha256": "b" * 64}], "a" * 64))

    def test_correction_page_respects_declared_big5_encoding(self):
        import fetch_mops_financials as m
        body = '<meta charset="big5"><table><tr><td>3324</td><td>20260108</td><td>更正財務報告</td></tr></table>'.encode("big5")
        got = m.correction_info(body, "3324")
        self.assertTrue(got["listed"]); self.assertEqual(got["date"], "20260108")
        self.assertIn("更正財務報告", got["description"])

    def test_cumulative_quarterization_and_scope_boundary(self):
        import fetch_mops_financials as m
        q1, q2 = self._filing(1), self._filing(2)
        fact1 = {"namespace": "n", "local_name": "CFO", "start": "2025-01-01", "end": "2025-03-31", "value": 10}
        fact2 = {"namespace": "n", "local_name": "CFO", "start": "2025-01-01", "end": "2025-06-30", "value": 30}
        q1["cumulative"] = {"FIN_CFO": (fact1, 10)}; q2["cumulative"] = {"FIN_CFO": (fact2, 30)}
        got = m.quarterize([q1, q2], [])
        q2row = next(r for r in got if r["fiscal_period"] == "Q2")
        self.assertEqual(q2row["value"], 20); self.assertEqual(q2row["derivation"], "DERIVED_QUARTER_FROM_CUMULATIVE")
        q1["report_scope"] = "INDIVIDUAL"
        self.assertFalse([r for r in m.quarterize([q1, q2], []) if r["fiscal_period"] == "Q2"])

    def test_q4_is_fy_minus_q3_and_never_guessed_without_q3(self):
        import fetch_mops_financials as m
        q3, q4 = self._filing(3), self._filing(4)
        f3 = {"namespace": "n", "local_name": "Revenue", "start": "2025-01-01", "end": "2025-09-30", "value": 70}
        f4 = {"namespace": "n", "local_name": "Revenue", "start": "2025-01-01", "end": "2025-12-31", "value": 100}
        q3["cumulative_direct"] = {"FIN_REVENUE": (f3, 70)}; q4["cumulative_direct"] = {"FIN_REVENUE": (f4, 100)}
        got = m.quarterize([q3, q4], [])
        self.assertEqual(got[0]["value"], 30); self.assertEqual(got[0]["derivation"], "DERIVED_Q4_FROM_FY_MINUS_Q3")
        self.assertEqual(m.quarterize([q4], []), [])

    def test_mops_availability_contract_is_strict(self):
        from data_spine import validate_observation
        row = {"observation_id": "O", "issuer_id": "I", "security_id": None, "metric_id": "FIN_CASH",
               "unit": "TWD", "period_end": "2025-03-31", "source_url": "https://example.com",
               "source_published_at": "2025-05-01T12:00:00+08:00", "available_at": "2025-05-01T12:00:00+08:00",
               "ingested_at": "2026-10-07T12:00:00+08:00", "availability_status": "POINT_IN_TIME_KNOWN",
               "availability_basis": "OFFICIAL_TIMESTAMP", "time_precision": "EXACT_TIMESTAMP",
               "raw_sha256": "a" * 64, "manifest_record_id": "M", "report_scope": "CONSOLIDATED",
               "source_system": "MOPS_XBRL", "derivation": "REPORTED_INSTANT", "supersedes_observation_id": None}
        self.assertEqual(validate_observation(row), [])
        bad = dict(row, raw_sha256="z" * 64, available_at=None)
        errs = validate_observation(bad)
        self.assertTrue(any("SHA-256" in e for e in errs)); self.assertTrue(any("OFFICIAL_TIMESTAMP" in e for e in errs))

    def test_unknown_backfill_is_current_snapshot_only(self):
        import health
        row = {"observation_id": "O", "issuer_id": "I", "metric_id": "FIN_CASH",
               "availability_status": "BACKFILLED_UNKNOWN", "availability_basis": "UNKNOWN_BACKFILL",
               "available_at": None, "source_published_at": None, "ingested_at": "2026-10-07T12:00:00+08:00"}
        cutoff = "2026-10-07T13:00:00+08:00"
        self.assertEqual(health.point_in_time([row], "I", cutoff), [])
        self.assertEqual(health.current_snapshot([row], "I", cutoff), [row])

    def test_first_seen_is_not_usable_before_its_observation_time(self):
        import health
        row = {"observation_id": "F", "issuer_id": "I", "metric_id": "FIN_CASH",
               "availability_status": "POINT_IN_TIME_KNOWN", "availability_basis": "FIRST_SEEN_CONSERVATIVE",
               "source_published_at": None, "first_seen_at": "2026-10-07T12:00:00+08:00",
               "available_at": "2026-10-07T12:00:00+08:00", "ingested_at": "2026-10-07T12:00:00+08:00"}
        self.assertEqual(health.point_in_time([row], "I", "2026-10-07T11:59:59+08:00"), [])
        self.assertEqual(health.point_in_time([row], "I", "2026-10-07T12:00:00+08:00"), [row])

    def test_three_availability_states_are_distinct(self):
        import fetch_mops_financials as m
        official = m._availability({"correction_listed": False, "doc_upload_at": "2026-01-01T01:00:00+08:00", "first_seen_at": None})
        first = m._availability({"correction_listed": False, "doc_upload_at": None, "first_seen_at": "2026-01-02T01:00:00+08:00"})
        unknown = m._availability({"correction_listed": False, "doc_upload_at": None, "first_seen_at": None})
        self.assertEqual(official["availability_basis"], "OFFICIAL_TIMESTAMP")
        self.assertEqual(first["availability_basis"], "FIRST_SEEN_CONSERVATIVE")
        self.assertEqual(unknown["availability_basis"], "UNKNOWN_BACKFILL")
        self.assertIsNone(unknown["available_at"])

    def test_historical_mops_v1_uses_statutory_deadline_only_for_version_rows(self):
        import fetch_mops_financials as m
        filing = self._filing(2)
        filing["year"] = 2022
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=False)
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["availability_basis"], "STATUTORY_DEADLINE_CONSERVATIVE")
        self.assertEqual(got["available_at"], "2022-08-14T23:59:59+08:00")
        self.assertIsNone(got["source_published_at"])
        filing["season"] = 4
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["available_at"], "2023-03-31T23:59:59+08:00")

    def test_statutory_deadline_scope_includes_later_backfilled_unknown_filings(self):
        import fetch_mops_financials as m
        filing = self._filing(3)
        filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=False)
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["availability_basis"], "STATUTORY_DEADLINE_CONSERVATIVE")
        self.assertEqual(got["available_at"], "2025-11-14T23:59:59+08:00")

    def test_statutory_v1_row_binds_corrected_policy_version_and_identity(self):
        import fetch_mops_financials as m
        filing = self._filing(3)
        filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=False)
        filing["facts"] = {
            ("http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full", "ProfitLossAttributableToOwnersOfParent"):
                [{"namespace": "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full",
                  "local_name": "ProfitLossAttributableToOwnersOfParent", "start": "2025-01-01",
                  "end": "2025-09-30", "value": 10, "dimensions": {}, "instant": False}]
        }
        row = m.build_valuation_version_rows(filing)[0]
        self.assertEqual(row["availability_basis"], "STATUTORY_DEADLINE_CONSERVATIVE")
        self.assertTrue(row["mapping_version"].endswith("+G-S3-9-v2"))
        self.assertEqual(row["observation_id"], m._obs_id(row))

    def test_corrected_v1_row_binds_v3_policy_version_and_validates(self):
        import fetch_mops_financials as m
        from data_spine import validate_observation
        filing = self._filing(3); filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=True,
                                  correction_date="20260108")
        filing["facts"] = {
            ("http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full", "ProfitLossAttributableToOwnersOfParent"):
                [{"namespace": "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full",
                  "local_name": "ProfitLossAttributableToOwnersOfParent", "start": "2025-01-01",
                  "end": "2025-09-30", "value": 10, "dimensions": {}, "instant": False}]
        }
        row = m.build_valuation_version_rows(filing)[0]
        self.assertEqual(row["availability_basis"],
                         "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE")
        self.assertTrue(row["mapping_version"].endswith("+G-S3-9-v3"))
        self.assertEqual(validate_observation(row), [])

    def test_statutory_deadline_never_overrides_official_filing(self):
        import fetch_mops_financials as m
        filing = self._filing(1); filing["year"] = 2021
        filing["manifest"].update(doc_upload_at="2021-05-10T12:00:00+08:00", first_seen_at=None,
                                  correction_listed=False)
        self.assertEqual(m._statutory_deadline_v1_availability(filing)["availability_basis"],
                         "OFFICIAL_TIMESTAMP")

    def test_correction_date_is_later_bound_for_corrected_backfill(self):
        import fetch_mops_financials as m
        filing = self._filing(1); filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=True,
                                  correction_date="20260108")
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["availability_basis"],
                         "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE")
        self.assertEqual(got["available_at"], "2026-01-08T23:59:59+08:00")

    def test_correction_date_before_deadline_cannot_move_availability_earlier(self):
        import fetch_mops_financials as m
        filing = self._filing(2); filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=True,
                                  correction_date="20250701")
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["available_at"], "2025-08-14T23:59:59+08:00")

    def test_unknown_correction_date_is_ineligible_for_v1_history(self):
        import fetch_mops_financials as m
        filing = self._filing(3); filing["year"] = 2025
        filing["manifest"].update(doc_upload_at=None, first_seen_at=None, correction_listed=True,
                                  correction_date=None)
        got = m._statutory_deadline_v1_availability(filing)
        self.assertEqual(got["availability_basis"], "V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN")
        self.assertEqual(got["availability_status"], "BACKFILLED_UNKNOWN")
        self.assertIsNone(got["available_at"])

    def test_statutory_deadline_validation_and_g_s3_9b_general_fin(self):
        from data_spine import validate_observation
        base = {"observation_id": "V", "issuer_id": "I", "security_id": None,
                "metric_id": "VER_EQUITY_PARENT", "unit": "TWD", "period_end": "2022-06-30",
                "source_url": "https://example.com", "source_published_at": None,
                "available_at": "2022-08-14T23:59:59+08:00",
                "ingested_at": "2026-10-14T12:00:00+08:00", "availability_status": "POINT_IN_TIME_KNOWN",
                "availability_basis": "STATUTORY_DEADLINE_CONSERVATIVE", "time_precision": "DATE_ONLY",
                "raw_sha256": "a" * 64, "manifest_record_id": "M", "report_scope": "CONSOLIDATED",
                "source_system": "MOPS_XBRL", "derivation": "REPORTED_VERSIONED_FACT",
                "supersedes_observation_id": None, "first_seen_at": None}
        self.assertEqual(validate_observation(base), [])
        self.assertTrue(any("只允許 V1" in e for e in validate_observation(dict(base, metric_id="FIN_CASH"))))
        corrected = dict(base, availability_basis="STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE")
        self.assertEqual(validate_observation(corrected), [])
        general = dict(corrected, observation_id="F", metric_id="FIN_REVENUE",
                       fiscal_year=2025, fiscal_period="Q2",
                       period_end="2025-06-30", available_at="2026-01-08T23:59:59+08:00",
                       correction_date="20260108", correction_page_sha256="b" * 64)
        self.assertEqual(validate_observation(general), [])
        missing_sha = dict(general); missing_sha.pop("correction_page_sha256")
        self.assertTrue(any("correction_page_sha256" in e for e in validate_observation(missing_sha)))
        too_early = dict(general, available_at="2025-08-14T23:59:59+08:00")
        self.assertTrue(any("available_at 必須等於 2026-01-08" in e
                            for e in validate_observation(too_early)))
        annual = dict(general, fiscal_period="Q4", period_end="2025-12-31",
                      correction_date="20260318", available_at="2026-03-18T23:59:59+08:00")
        self.assertTrue(any("available_at 必須等於 2026-03-31" in e
                            for e in validate_observation(annual)))
        import health
        self.assertEqual(health.point_in_time([general], "I", "2026-01-09T00:00:00+08:00", {}), [])
        selected = health.point_in_time([general], "I", "2026-10-15T00:00:00+08:00", {})
        self.assertEqual([r["observation_id"] for r in selected], ["F"])
        memory_bound = dict(general, selection_written_at="2026-10-14T13:00:00+08:00")
        self.assertEqual([r["observation_id"] for r in health.point_in_time(
            [memory_bound], "I", "2026-10-15T00:00:00+08:00", {})], ["F"])
        self.assertEqual(health.point_in_time([missing_sha], "I", "2026-10-15T00:00:00+08:00", {}), [])
        ineligible = dict(base, availability_basis="V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN",
                          availability_status="BACKFILLED_UNKNOWN", available_at=None,
                          time_precision="UNKNOWN")
        self.assertEqual(validate_observation(ineligible), [])

    def test_liquidity_requires_independent_approved_review(self):
        import fetch_mops_financials as m, tempfile, json
        required = {"record_type": "CLASSIFICATION", "classification_id": "C", "issuer_id": "I",
                    "fiscal_year": 2025, "fiscal_period": "Q1", "period_end": "2025-03-31",
                    "raw_sha256": "a" * 64, "accession_number": "A", "namespace": "n", "local_name": "x",
                    "context": "c", "value": 1, "unit": "TWD", "instrument_type": "BOND",
                    "include_in_conservative_liquid_debt": True, "evidence_locator": "p1",
                    "checked_by": "same", "checked_at": "2026-10-07", "reviewed_by": "same",
                    "reviewed_at": "2026-10-07", "review_status": "APPROVED_BY_INDEPENDENT_AI"}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            with open(p, "w", encoding="utf-8") as f: f.write(json.dumps(required, ensure_ascii=False) + "\n")
            self.assertTrue(any("不得相同" in e for e in m.validate_liquidity_records(p)))

    def test_liquidity_approval_is_bound_to_exact_raw_hash(self):
        import fetch_mops_financials as m, tempfile, json
        f = self._filing(1); cash_fact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2025-03-31"}
        cash = m._base_observation(f, "FIN_CASH", cash_fact, 100, "INSTANT", "REPORTED_INSTANT")
        classification = {"record_type": "CLASSIFICATION", "classification_id": "C", "issuer_id": "ISSUER_TSMC",
                          "fiscal_year": 2025, "fiscal_period": "Q1", "period_end": "2025-03-31",
                          "raw_sha256": "a" * 64, "accession_number": "A", "namespace": "n", "local_name": "Bond",
                          "context": "c", "value": 10, "unit": "TWD", "instrument_type": "BOND",
                          "include_in_conservative_liquid_debt": True, "evidence_locator": "p1",
                          "checked_by": "claude", "checked_at": "2026-10-07", "reviewed_by": "codex",
                          "reviewed_at": "2026-10-07", "review_status": "APPROVED_BY_INDEPENDENT_AI"}
        coverage = {"record_type": "COVERAGE", "coverage_id": "V", "issuer_id": "ISSUER_TSMC",
                    "fiscal_year": 2025, "fiscal_period": "Q1", "period_end": "2025-03-31",
                    "raw_sha256": "a" * 64, "candidate_total": 1, "classification_ids": ["C"], "coverage_complete": True}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            with open(p, "w", encoding="utf-8") as outp:
                for x in (classification, coverage): outp.write(json.dumps(x) + "\n")
            old = m.LIQUIDITY_PATH; m.LIQUIDITY_PATH = p
            try:
                self.assertEqual(m.approved_short_term_observation(f, [cash])["value"], 10)
                f2 = dict(f, raw_sha256="b" * 64)
                self.assertIsNone(m.approved_short_term_observation(f2, [cash]))
            finally: m.LIQUIDITY_PATH = old

    def test_mops_append_is_idempotent_and_correction_is_chained(self):
        import tempfile
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl
        f1 = self._filing(1); f1["manifest"]["doc_upload_at"] = None
        basefact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2025-03-31"}
        import fetch_mops_financials as m
        r1 = m._base_observation(f1, "FIN_CASH", basefact, 100, "INSTANT", "REPORTED_INSTANT")
        r1.update(source_published_at=None, available_at=None, availability_status="BACKFILLED_UNKNOWN",
                  availability_basis="UNKNOWN_BACKFILL", time_precision="UNKNOWN", first_seen_at=None)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.jsonl")
            self.assertEqual(append_financial_observations([r1], p), 1)
            self.assertEqual(append_financial_observations([r1], p), 0)
            f2 = self._filing(1); f2["raw_sha256"] = "b" * 64; f2["manifest"].update(revision_sequence=2, manifest_record_id="M2", doc_upload_at=None)
            r2 = m._base_observation(f2, "FIN_CASH", basefact, 100, "INSTANT", "REPORTED_INSTANT")
            r2.update(source_published_at=None, available_at=None, availability_status="BACKFILLED_UNKNOWN",
                      availability_basis="UNKNOWN_BACKFILL", time_precision="UNKNOWN", first_seen_at=None)
            self.assertEqual(append_financial_observations([r2], p), 1)
            rows = read_jsonl(p)
            self.assertEqual(rows[-1]["supersedes_observation_id"], r1["observation_id"])
            self.assertEqual(rows[-1]["revision_reason"], "RAW_FILE_CORRECTION")
            f3 = copy.deepcopy(f2); f3["mapping_version"] = "0.7.0"
            r3 = m._base_observation(f3, "FIN_CASH", basefact, 100, "INSTANT", "REPORTED_INSTANT")
            r3.update(source_published_at=None, available_at=None, availability_status="BACKFILLED_UNKNOWN",
                      availability_basis="UNKNOWN_BACKFILL", time_precision="UNKNOWN", first_seen_at=None)
            self.assertEqual(append_financial_observations([r3], p), 1)
            self.assertEqual(read_jsonl(p)[-1]["revision_reason"], "TAG_MAPPING_RULE_CHANGE")
            stale = dict(r3, observation_id="STALE", supersedes_observation_id=r1["observation_id"], mapping_version="0.8.0")
            with self.assertRaisesRegex(ValueError, "STALE_OR_INVALID_SUPERSEDES"):
                append_financial_observations([stale], p)

    def test_components_change_observation_identity(self):
        import fetch_mops_financials as m
        f = self._filing(1); fact = {"namespace": "n", "local_name": "CFO", "start": "2025-01-01", "end": "2025-03-31"}
        a = m._base_observation(f, "FIN_CFO", fact, 10, "Q", "DERIVED_Q1_FROM_YTD", [{"value": 10, "operator": "+"}])
        b = m._base_observation(f, "FIN_CFO", fact, 10, "Q", "DERIVED_Q1_FROM_YTD", [{"value": 12, "operator": "+"}, {"value": 2, "operator": "-"}])
        self.assertNotEqual(a["observation_id"], b["observation_id"])

    def test_current_snapshot_and_as_of_are_mutually_exclusive(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "03_工具", "health.py"),
                               "--current-snapshot", "--as-of", "2026-01-01"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(proc.returncode, 0)

    def test_preexisting_sec_observation_prefix_is_byte_identical_and_valid(self):
        import hashlib
        from data_spine import read_jsonl, validate_observation, OBSERVATIONS_PATH
        with open(OBSERVATIONS_PATH, "rb") as f: prefix = f.read(1697454)
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), "87aefe23b859ed2f037f03367d4f10aaf877fb050b7a24c6787d0fa0e3983b4d")
        old = read_jsonl(OBSERVATIONS_PATH)[:1894]
        self.assertEqual(len(old), 1894)
        self.assertFalse([(r.get("observation_id"), validate_observation(r)) for r in old if validate_observation(r)])

    def test_3081_scope_is_individual_and_limitation_propagates(self):
        import fetch_mops_financials as m
        f = self._filing(1, "INDIVIDUAL"); fact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2025-03-31"}
        row = m._base_observation(f, "FIN_CASH", fact, 1, "INSTANT", "REPORTED_INSTANT")
        self.assertEqual(row["report_scope"], "INDIVIDUAL")
        self.assertIn("INDIVIDUAL_STATEMENTS_ONLY", row["limitations"])


class Patch181(unittest.TestCase):
    """1.8.1：C180-01 至 C180-05 的回歸測試。"""
    NS = "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"
    NS25 = "https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full"
    T0 = "2026-01-01T00:00:00+08:00"
    CUTOFF = "2026-10-08T12:00:00+08:00"

    # 1.8.5 起正式對照表已啟用；閘門測試改在「待啟用」的對照表副本上跑，正式檔不動。
    def setUp(self):
        import tempfile, health
        self._gate_dir = tempfile.mkdtemp()
        self._old_tag_map_path = health.TAG_MAP_PATH
        health.TAG_MAP_PATH = self._tag_map_copy(self._gate_dir, "DATA_INSUFFICIENT_MOPS_XBRL_ADAPTER_PENDING")
    def tearDown(self):
        import health, shutil
        health.TAG_MAP_PATH = self._old_tag_map_path
        shutil.rmtree(self._gate_dir, ignore_errors=True)

    # ---------- 共用的合成資料 ----------
    def _tag_map_copy(self, d, status):
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_tag_map.json"), encoding="utf-8") as f:
            data = json.load(f)
        data["taiwan"]["status"] = status
        p = os.path.join(d, "tag_map.json")
        with open(p, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)
        return p

    def _obs(self, issuer, metric, end, value, source="MOPS_XBRL", ptype="INSTANT", start=None, **extra):
        oid = f"{issuer}:{metric}:{end}:{source}:{ptype}"
        row = {"observation_id": oid, "issuer_id": issuer, "security_id": None, "metric_id": metric,
               "value": value, "unit": "TWD", "period_start": start, "period_end": end, "period_type": ptype,
               "source_system": source, "source_published_at": self.T0, "available_at": self.T0,
               "ingested_at": self.T0, "availability_status": "POINT_IN_TIME_KNOWN"}
        if source == "MOPS_XBRL":
            row.update(availability_basis="OFFICIAL_TIMESTAMP", time_precision="EXACT_TIMESTAMP",
                       report_scope="CONSOLIDATED", raw_sha256="a" * 64, manifest_record_id="M")
        row.update(extra)
        return row

    def _revenue_rows(self, issuer, source="MOPS_XBRL"):
        ends = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
                "2025-12-31", "2026-03-31", "2026-06-30"]
        rows = []
        for i, end in enumerate(ends):
            rows.append(self._obs(issuer, "FIN_REVENUE", end, 100.0 + 10 * i * i, source, "Q", end[:8] + "01"))
            rows.append(self._obs(issuer, "FIN_GROSS_PROFIT", end, 40.0 + 5 * i * i, source, "Q", end[:8] + "01"))
        return rows

    def _balance_rows(self, issuer, source="MOPS_XBRL", absent=None):
        import fetch_mops_financials as m
        end = "2026-06-30"
        rows = [self._obs(issuer, "FIN_CASH", end, 100.0, source),
                self._obs(issuer, "FIN_SHORT_TERM_INVESTMENTS", end, 0.0, source),
                self._obs(issuer, "FIN_CURRENT_ASSETS", end, 300.0, source),
                self._obs(issuer, "FIN_CURRENT_LIABILITIES", end, 150.0, source)]
        for metric, value in (("FIN_DEBT_CURRENT", 60.0), ("FIN_DEBT_NONCURRENT", 200.0)):
            row = self._obs(issuer, metric, end, value, source)
            if absent is not None:
                comps = [{"namespace": self.NS, "local_name": "LongtermBorrowings", "context_start": None,
                          "context_end": end, "value": value, "operator": "+"}]
                row.update(derivation="COMPOSITE_SUM", components=comps, absent_components=absent,
                           components_sha256=m.components_digest(comps, absent))
                if absent:
                    row["limitations"] = ["ABSENT_COMPONENTS_TREATED_AS_NONE"]
            rows.append(row)
        for i, end_q in enumerate(["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]):
            start = end_q[:8] + "01"
            rows.append(self._obs(issuer, "FIN_OPERATING_INCOME", end_q, 10.0, source, "Q", start))
            rows.append(self._obs(issuer, "FIN_DEPRECIATION_AMORTIZATION", end_q, 5.0, source, "Q", start))
        return rows

    def _filing(self, facts, season=2, year=2026):
        return {"ticker": "2330", "issuer_id": "ISSUER_TSMC", "year": year, "season": season,
                "report_scope": "CONSOLIDATED", "url": "https://example.com", "raw_sha256": "a" * 64,
                "mapping_version": "0.6.0", "facts": facts,
                "manifest": {"fetched_at": "2026-10-07T12:00:00+08:00", "manifest_record_id": "M1",
                             "revision_sequence": 1, "doc_upload_at": "2026-08-14T13:59:44+08:00",
                             "correction_listed": False, "first_seen_at": None}}

    def _fact(self, local, value, end="2026-06-30", start=None, ns=None):
        ns = ns or self.NS
        return {(ns, local): [{"namespace": ns, "local_name": local, "context_ref": "c", "start": start,
                               "end": end, "instant": start is None, "identifier": "2330", "value": value,
                               "unit_ref": "TWD", "decimals": "-3", "scale": "3", "sign": None}]}

    def _tag_map(self):
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_tag_map.json"), encoding="utf-8") as f:
            return json.load(f)

    # ---------- C180-01 啟用閘門 ----------
    def test_gate_status_is_read_from_tag_map_not_hardcoded(self):
        import health, tempfile
        self.assertFalse(health.mops_gate_enabled())   # 1.8.5 起正式對照表已啟用；這裡是 setUp 指向的待啟用副本
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(health.mops_gate_enabled(self._tag_map_copy(d, "DATA_INSUFFICIENT_MOPS_XBRL_ADAPTER_PENDING")))
            self.assertTrue(health.mops_gate_enabled(self._tag_map_copy(d, health.TAIWAN_ENABLED_STATUS)))
            self.assertFalse(health.mops_gate_enabled(os.path.join(d, "missing.json")))

    def test_official_tag_map_enabled_only_with_recorded_user_authorization(self):
        import health
        self.assertEqual(self._tag_map()["taiwan"]["status"], health.TAIWAN_ENABLED_STATUS)
        with open(os.path.join(ROOT, "00_規範", "使用者決定紀錄.md"), encoding="utf-8") as fh: log = fh.read()
        self.assertIn("同意啟用台股財報", log)
        with open(os.path.join(ROOT, "90_各AI工作區", "claude", "台股財報原文抽查_2026-10-08.md"), encoding="utf-8") as fh:
            self.assertIn("**抽查通過。**", fh.read())

    def test_health_default_excludes_mops_until_enabled(self):
        import health, tempfile
        rows = self._balance_rows("ISSUER_TW")
        r = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=False)
        self.assertEqual(r["status"], "資料不足")
        self.assertIn("台股財報待複查啟用", r["reasons"])
        self.assertFalse([oid for v in r["indicators"].values() for oid in v["observation_ids"]])
        self.assertEqual(r["mops_gate"]["excluded_observation_count"], len(rows))
        # 預設參數會讀正式對照表（pending）；換成暫存的已啟用副本後才取用。
        self.assertIn("台股財報待複查啟用", health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF)["reasons"])
        with tempfile.TemporaryDirectory() as d:
            old = health.TAG_MAP_PATH; health.TAG_MAP_PATH = self._tag_map_copy(d, health.TAIWAN_ENABLED_STATUS)
            try:
                enabled = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF)
            finally:
                health.TAG_MAP_PATH = old
        self.assertNotIn("台股財報待複查啟用", enabled["reasons"])
        self.assertEqual(enabled["indicators"]["H6"]["status"], "穩健")
        self.assertFalse(enabled["unreviewed_preview"])

    def test_health_preview_includes_mops_but_marks_unreviewed(self):
        import health
        rows = self._balance_rows("ISSUER_TW")
        r = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, preview_pending=True, mops_enabled=False)
        self.assertEqual(r["indicators"]["H6"]["status"], "穩健")
        self.assertTrue(r["unreviewed_preview"])
        self.assertFalse(r["historical_ledger_write_allowed"])
        self.assertTrue(any("未經抽查的預覽" in x for x in r["limitations"]))

    def test_gate_never_touches_sec_rows(self):
        import health
        rows = self._balance_rows("ISSUER_US", source="SEC_COMPANYFACTS")
        a = health.evaluate_issuer("ISSUER_US", rows, RULES, self.CUTOFF, mops_enabled=False)
        b = health.evaluate_issuer("ISSUER_US", rows, RULES, self.CUTOFF, mops_enabled=True)
        self.assertEqual(a["status"], b["status"])
        self.assertEqual({k: (v["status"], v["value"], v["observation_ids"]) for k, v in a["indicators"].items()},
                         {k: (v["status"], v["value"], v["observation_ids"]) for k, v in b["indicators"].items()})
        self.assertNotIn("台股財報待複查啟用", a["reasons"])

    def test_trend_default_excludes_mops_and_preview_includes(self):
        import trend
        rows = self._revenue_rows("ISSUER_TW")
        gated = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=False)
        self.assertIsNone(gated["C1"]); self.assertEqual(gated["reasons"], ["台股財報待複查啟用"])
        preview = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, preview_pending=True, mops_enabled=False)
        self.assertIsNotNone(preview["C2"]); self.assertTrue(preview["unreviewed_preview"])
        self.assertFalse(preview["historical_ledger_write_allowed"])
        enabled = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=True)
        self.assertEqual(enabled["C2"], preview["C2"]); self.assertFalse(enabled["unreviewed_preview"])

    def test_theme_breadth_excludes_pending_taiwan_issuers(self):
        import trend
        from data_spine import load_registry, read_jsonl, OBSERVATIONS_PATH
        reg = load_registry(); obs = read_jsonl(OBSERVATIONS_PATH)
        tw = {s["ticker"] for s in reg["securities"] if s.get("issuer_id") in {
            "ISSUER_TSMC", "ISSUER_WINBOND", "ISSUER_NANYA", "ISSUER_AVC", "ISSUER_AURAS", "ISSUER_LANDMARK", "ISSUER_TUC"}}
        gated = trend.theme_breadth(trend.evaluate_all(obs, RULES, self.CUTOFF, reg), RULES, reg)
        counted = {t for th in gated.values() for t in th.get("accelerating", []) + th.get("decelerating", [])}
        self.assertFalse(counted & tw)
        preview = trend.theme_breadth(trend.evaluate_all(obs, RULES, self.CUTOFF, reg, preview_pending=True), RULES, reg)
        counted = {t for th in preview.values() for t in th.get("accelerating", []) + th.get("decelerating", [])}
        self.assertTrue(counted & tw)

    def test_company_default_shows_pending_reason(self):
        import company, trend, health
        rows = self._revenue_rows("ISSUER_TW") + self._balance_rows("ISSUER_TW")
        t = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=False)
        h = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=False)
        item = {"ticker": "9999", "name": "x", "market": "TW", "trend": t, "health": h, "moves": {}, "missing": ["價格"],
                "pending_gate": True, "limitations": [], "unreviewed_preview": False}
        text = company.render_one(item)
        self.assertIn("營收趨勢：資料不足（台股財報待複查啟用）", text)
        self.assertIn("財務體質：資料不足（台股財報待複查啟用）", text)

    def test_company_build_gates_real_taiwan_rows(self):
        import company
        rows = {r["ticker"]: r for r in company.build(RULES, cutoff=self.CUTOFF)}
        for ticker in ("2330", "TSM", "2408", "3081"):
            self.assertTrue(rows[ticker]["pending_gate"], ticker)
            self.assertEqual(rows[ticker]["trend"]["label"], "資料不足")
            self.assertIn("台股財報待複查啟用", company.render_one(rows[ticker]))
        self.assertFalse(rows["MU"]["pending_gate"])
        preview = {r["ticker"]: r for r in company.build(RULES, cutoff=self.CUTOFF, preview_pending=True)}
        self.assertTrue(preview["3081"]["unreviewed_preview"])
        self.assertIn("僅個別財報，不與合併報表直接比較", company.render_one(preview["3081"]))
        self.assertIn("未經抽查的預覽", company.render_one(preview["3081"]))

    def test_attribution_health_as_of_event_never_uses_pending_mops(self):
        import attribution, health
        seen = []
        real = health.evaluate_issuer
        def spy(*a, **k):
            res = real(*a, **k); seen.append((k.get("mops_enabled"), k.get("preview_pending", False), res)); return res
        health.evaluate_issuer = spy
        try:
            out = attribution.health_as_of_event(RULES, [{"status": "OK", "ticker": "2408", "end_date": "2026-09-30", "market": "TW"}])
        finally:
            health.evaluate_issuer = real
        self.assertNotIn("__error__", out)
        self.assertTrue(seen)
        for enabled, preview, res in seen:
            self.assertIsNone(enabled); self.assertFalse(preview)   # 走預設路徑：由 setUp 指向的待啟用副本決定（正式對照表 1.8.5 起已啟用）
            self.assertIn("台股財報待複查啟用", res["reasons"])
            self.assertFalse([oid for v in res["indicators"].values() for oid in v["observation_ids"]])

    def test_preview_output_plan_never_writes_ledger(self):
        import health, trend, company
        for mod in (health, trend):
            plan = mod.output_plan("2026-10-08", False, True, False)
            self.assertFalse(plan["write_ledger"]); self.assertIn("未經抽查的預覽", plan["report_name"])
            snap = mod.output_plan("2026-10-08", True, True, False)
            self.assertFalse(snap["write_ledger"]); self.assertIn("_目前狀況_未經抽查的預覽", snap["report_name"])
            self.assertTrue(mod.output_plan("2026-10-08", False, False, False)["write_ledger"])
        self.assertIn("未經抽查的預覽", company.report_name("2026-10-08", True, True))

    def test_preview_pending_and_as_of_are_mutually_exclusive(self):
        for script in ("health.py", "trend.py", "company.py"):
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "03_工具", script),
                                   "--preview-pending", "--as-of", "2026-01-01", "--no-ledger", "--no-report"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(proc.returncode, 0, script)

    def test_preview_with_current_snapshot_includes_backfill(self):
        import health
        rows = self._balance_rows("ISSUER_TW")
        for r in rows:
            r.update(availability_status="BACKFILLED_UNKNOWN", availability_basis="UNKNOWN_BACKFILL",
                     available_at=None, source_published_at=None, time_precision="UNKNOWN")
        r = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, current_snapshot_mode=True,
                                   preview_pending=True, mops_enabled=False)
        self.assertEqual(r["indicators"]["H6"]["status"], "穩健")
        self.assertFalse(r["point_in_time_replay_allowed"]); self.assertFalse(r["historical_ledger_write_allowed"])

    # ---------- C180-02 負債缺席細項 ----------
    def test_absent_components_recorded_hashed_and_flagged(self):
        import fetch_mops_financials as m
        from data_spine import validate_observation
        facts = {}
        facts.update(self._fact("ShorttermBorrowings", 50.0))
        facts.update(self._fact("CurrentLiabilities", 400.0))
        rows = m.build_filing_rows(self._filing(facts), self._tag_map())
        debt = next(r for r in rows if r["metric_id"] == "FIN_DEBT_CURRENT")
        absent = {a["local_name"]: a for a in debt["absent_components"]}
        self.assertNotIn("ShorttermBorrowings", absent)
        self.assertIn("CurrentPortionOfLongtermBorrowings", absent)
        self.assertIn(self.NS, absent["CurrentPortionOfLongtermBorrowings"]["namespaces_checked"])
        self.assertIn(self.NS25, absent["CurrentPortionOfLongtermBorrowings"]["namespaces_checked"])
        self.assertIn("ABSENT_COMPONENTS_TREATED_AS_NONE", debt["limitations"])
        self.assertEqual(debt["derivation"], "COMPOSITE_SUM"); self.assertEqual(debt["value"], 50.0)
        self.assertEqual(validate_observation(debt), [])
        # 缺席清單參與雜湊與 ID
        other = m._base_observation(self._filing(facts), "FIN_DEBT_CURRENT", dict(debt["components"][0], start=None, end="2026-06-30"),
                                    50.0, "INSTANT", "COMPOSITE_SUM", debt["components"], absent_components=debt["absent_components"][:-1])
        self.assertNotEqual(other["components_sha256"], debt["components_sha256"])
        self.assertNotEqual(other["observation_id"], debt["observation_id"])

    def test_all_absent_or_missing_current_liabilities_produces_no_debt(self):
        import fetch_mops_financials as m
        only_cl = self._fact("CurrentLiabilities", 400.0)
        self.assertFalse([r for r in m.build_filing_rows(self._filing(only_cl), self._tag_map()) if r["metric_id"] in m.DEBT_METRICS])
        no_cl = self._fact("ShorttermBorrowings", 50.0)
        self.assertFalse([r for r in m.build_filing_rows(self._filing(no_cl), self._tag_map()) if r["metric_id"] in m.DEBT_METRICS])

    def test_same_concept_in_two_taxonomy_versions_is_not_double_counted(self):
        import fetch_mops_financials as m
        facts = {}
        facts.update(self._fact("ShorttermBorrowings", 50.0))
        facts.update(self._fact("ShorttermBorrowings", 50.0, ns=self.NS25))
        facts.update(self._fact("CurrentLiabilities", 400.0))
        debt = next(r for r in m.build_filing_rows(self._filing(facts), self._tag_map()) if r["metric_id"] == "FIN_DEBT_CURRENT")
        self.assertEqual(debt["value"], 50.0)

    def test_absent_validator_rejects_bad_hash_flag_and_overlap(self):
        import fetch_mops_financials as m
        from data_spine import validate_observation
        facts = {}
        facts.update(self._fact("ShorttermBorrowings", 50.0)); facts.update(self._fact("CurrentLiabilities", 400.0))
        debt = next(r for r in m.build_filing_rows(self._filing(facts), self._tag_map()) if r["metric_id"] == "FIN_DEBT_CURRENT")
        self.assertTrue(validate_observation(dict(debt, components_sha256="0" * 64)))
        self.assertTrue(validate_observation(dict(debt, limitations=[])))
        overlap = dict(debt, absent_components=debt["absent_components"] + [{"local_name": "ShorttermBorrowings", "namespaces_checked": [self.NS]}])
        self.assertTrue(validate_observation(overlap))
        self.assertTrue(validate_observation(dict(debt, metric_id="FIN_CASH")))

    def test_active_debt_rows_must_carry_absent_components(self):
        from data_spine import validate_active_mops_debt
        old = self._obs("ISSUER_TW", "FIN_DEBT_CURRENT", "2026-06-30", 1.0, observation_id="OLD")
        self.assertTrue(validate_active_mops_debt([old]))
        # 1.8.2（C181-01）：有效負債列必須是三類都明寫的格式；只有 absent_components 已不足。
        only_absent = dict(old, observation_id="MID", supersedes_observation_id="OLD", absent_components=[])
        self.assertTrue(validate_active_mops_debt([old, only_absent]))
        new = dict(only_absent, observation_id="NEW", supersedes_observation_id="MID", covered_by_aggregate=[],
                   excluded_overlapping_components=[], overlap_rules_version="TW_DEBT_OVERLAP_1")
        self.assertEqual(validate_active_mops_debt([old, only_absent, new]), [])

    def test_canonical_active_mops_debt_rows_have_absent_components(self):
        from data_spine import read_jsonl, validate_active_mops_debt, OBSERVATIONS_PATH
        self.assertEqual(validate_active_mops_debt(read_jsonl(OBSERVATIONS_PATH)), [])

    def test_absent_limitation_reaches_h2_h4_and_report_text(self):
        import health
        absent = [{"local_name": "ShorttermBorrowings", "namespaces_checked": [self.NS]}]
        rows = self._balance_rows("ISSUER_TW", absent=absent)
        r = health.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=True)
        for key in ("H2", "H4"):
            self.assertIsNotNone(r["indicators"][key]["value"], key)
            self.assertIn("ABSENT_COMPONENTS_TREATED_AS_NONE", r["indicators"][key]["limitations"])
            self.assertIn("ShorttermBorrowings", {a["local_name"] for a in r["indicators"][key]["absent_components"]})
        self.assertNotIn("ABSENT_COMPONENTS_TREATED_AS_NONE", r["indicators"]["H6"].get("limitations", []))
        text = health.render_section([r], RULES)
        self.assertIn("ABSENT_COMPONENTS_TREATED_AS_NONE", text)
        self.assertIn("FIN_DEBT_CURRENT 2026-06-30 ShorttermBorrowings", text)
        none = health.evaluate_issuer("ISSUER_TW", self._balance_rows("ISSUER_TW", absent=[]), RULES, self.CUTOFF, mops_enabled=True)
        self.assertNotIn("ABSENT_COMPONENTS_TREATED_AS_NONE", none["indicators"]["H2"].get("limitations", []))

    # ---------- C180-03 報告文字 ----------
    def test_cross_market_text_appears_when_h2_h4_have_values(self):
        import health
        r = health.evaluate_issuer("ISSUER_US", self._balance_rows("ISSUER_US", source="SEC_COMPANYFACTS"), RULES, self.CUTOFF)
        self.assertIsNotNone(r["indicators"]["H2"]["value"]); self.assertIsNotNone(r["indicators"]["H4"]["value"])
        lines = [l for l in health.render_section([r], RULES).splitlines() if "| H2 |" in l or "| H4 |" in l]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("CROSS_MARKET_NOT_COMPARABLE" in l for l in lines))
        empty = health.evaluate_issuer("ISSUER_X", [], RULES, self.CUTOFF)
        self.assertNotIn("CROSS_MARKET_NOT_COMPARABLE", health.render_section([empty], RULES))

    def test_real_us_health_report_shows_cross_market_text(self):
        import health
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        results = [r for r in health.evaluate_all(read_jsonl(OBSERVATIONS_PATH), RULES, "2026-10-08T23:59:59+00:00")
                   if r["issuer_id"] == "ISSUER_MICRON"]
        if results[0]["indicators"]["H4"]["value"] is not None:
            self.assertIn("CROSS_MARKET_NOT_COMPARABLE", health.render_section(results, RULES))

    def test_individual_statements_text_in_trend_and_company(self):
        import trend, company
        rows = [dict(r, report_scope="INDIVIDUAL", limitations=["INDIVIDUAL_STATEMENTS_ONLY"]) for r in self._revenue_rows("ISSUER_TW")]
        t = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=True)
        self.assertIn("INDIVIDUAL_STATEMENTS_ONLY", t["limitations"])
        self.assertIn("僅個別財報，不與合併報表直接比較", trend.explain(t))
        item = {"ticker": "3081", "name": "x", "market": "TW", "trend": t, "health": None, "moves": {}, "missing": [],
                "limitations": t["limitations"], "pending_gate": False, "unreviewed_preview": False}
        self.assertIn("僅個別財報，不與合併報表直接比較", company.render_one(item))

    # ---------- C180-04 context 精確比對 ----------
    def test_context_dates_must_match_exactly(self):
        import fetch_mops_financials as m
        cfg = [{"namespace": self.NS, "local_name": "Revenue"}]
        def facts(start, end, instant=False):
            return {(self.NS, "Revenue"): [{"namespace": self.NS, "local_name": "Revenue", "start": None if instant else start,
                                            "end": end, "instant": instant, "value": 1.0}]}
        self.assertIsNotNone(m.choose_fact(facts("2026-04-01", "2026-06-30"), cfg, 2026, 2, "quarter"))
        self.assertIsNone(m.choose_fact(facts("2026-04-02", "2026-06-30"), cfg, 2026, 2, "quarter"))
        self.assertIsNone(m.choose_fact(facts("2026-03-31", "2026-06-30"), cfg, 2026, 2, "quarter"))
        self.assertIsNone(m.choose_fact(facts("2026-04-01", "2026-06-29"), cfg, 2026, 2, "quarter"))
        self.assertIsNone(m.choose_fact(facts("2026-01-02", "2026-06-30"), cfg, 2026, 2, "ytd"))
        self.assertIsNotNone(m.choose_fact(facts("2026-01-01", "2026-06-30"), cfg, 2026, 2, "ytd"))
        self.assertIsNone(m.choose_fact(facts(None, "2026-06-29", True), cfg, 2026, 2, "instant"))
        self.assertIsNotNone(m.choose_fact(facts(None, "2026-06-30", True), cfg, 2026, 2, "instant"))
        self.assertIsNotNone(m.choose_fact(facts("2025-10-01", "2025-12-31"), cfg, 2025, 4, "quarter"))

    # ---------- C180-05 訊息與負值 ----------
    def test_backfill_exclusion_reason_is_explicit(self):
        import trend
        rows = self._revenue_rows("ISSUER_TW")
        for r in rows:
            if r["period_end"] == "2025-06-30":
                r.update(availability_status="BACKFILLED_UNKNOWN", availability_basis="UNKNOWN_BACKFILL",
                         available_at=None, source_published_at=None, time_precision="UNKNOWN")
        t = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, mops_enabled=True)
        self.assertIsNone(t["C1"])
        self.assertIn("該期財報列於官方更補正清單，可用時間未知，已排除；可用 --current-snapshot 檢視", t["reasons"][0])
        self.assertIn("2025-06-30", t["reasons"][0])
        snap = trend.evaluate_issuer("ISSUER_TW", rows, RULES, self.CUTOFF, current_snapshot_mode=True, mops_enabled=True)
        self.assertIsNotNone(snap["C1"])

    def test_negative_instant_values_raise_instead_of_abs(self):
        import fetch_mops_financials as m
        for local, value in (("CashAndCashEquivalents", -1.0), ("ShorttermBorrowings", -5.0), ("CurrentAssets", -2.0),
                             ("CurrentLiabilities", -3.0)):
            facts = self._fact("CurrentLiabilities", 400.0) if local != "CurrentLiabilities" else {}
            facts.update(self._fact(local, value))
            with self.assertRaisesRegex(ValueError, "NEGATIVE_INSTANT_VALUE"):
                m.build_filing_rows(self._filing(facts), self._tag_map())

    def test_positive_instant_value_is_kept_as_is(self):
        import fetch_mops_financials as m
        rows = m.build_filing_rows(self._filing(self._fact("CashAndCashEquivalents", 7.0)), self._tag_map())
        self.assertEqual(next(r for r in rows if r["metric_id"] == "FIN_CASH")["value"], 7.0)

    # ---------- 不變性 ----------
    def test_pre_181_observation_file_is_byte_identical_prefix(self):
        import hashlib
        from data_spine import OBSERVATIONS_PATH
        with open(OBSERVATIONS_PATH, "rb") as f: prefix = f.read(2898409)
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), "c113fdd04aadbe7bd0a5af49f135aefd353c4bfb46399ade80568bebc0f056ad")

    def test_181_debt_corrections_keep_values_and_reason(self):
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        rows = read_jsonl(OBSERVATIONS_PATH)
        by_id = {r["observation_id"]: r for r in rows[:2629]}
        new = rows[2629:2737]   # 1.8.2 之後檔尾還有 1.8.2 的更正列，這裡只看 1.8.1 那批
        self.assertEqual(len(new), 108)
        for r in new:
            parent = by_id[r["supersedes_observation_id"]]
            self.assertEqual(r["revision_reason"], "DERIVATION_COMPONENT_CHANGE")
            self.assertEqual((r["value"], r["metric_id"], r["period_end"], r["issuer_id"]),
                             (parent["value"], parent["metric_id"], parent["period_end"], parent["issuer_id"]))
            self.assertIn("absent_components", r)

    def test_us_issuers_unaffected_by_gate_on_real_data(self):
        import health, trend
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        obs = read_jsonl(OBSERVATIONS_PATH)
        us = ["ISSUER_MICRON", "ISSUER_LUMENTUM", "ISSUER_COHERENT", "ISSUER_MARVELL", "ISSUER_BROADCOM",
              "ISSUER_ARISTA", "ISSUER_VERTIV", "ISSUER_EATON", "ISSUER_GEV"]
        for issuer in us:
            a = health.evaluate_issuer(issuer, obs, RULES, self.CUTOFF, mops_enabled=False)
            b = health.evaluate_issuer(issuer, obs, RULES, self.CUTOFF, preview_pending=True, mops_enabled=False)
            self.assertEqual((a["status"], {k: (v["status"], v["value"]) for k, v in a["indicators"].items()}),
                             (b["status"], {k: (v["status"], v["value"]) for k, v in b["indicators"].items()}))
            ta = trend.evaluate_issuer(issuer, obs, RULES, self.CUTOFF, mops_enabled=False)
            tb = trend.evaluate_issuer(issuer, obs, RULES, self.CUTOFF, preview_pending=True, mops_enabled=False)
            self.assertEqual((ta["label"], ta["C1"], ta["C2"], ta["C3"]), (tb["label"], tb["C1"], tb["C2"], tb["C3"]))


class Patch182(unittest.TestCase):
    """1.8.2：C181-01（缺席分三類）與 C181-02（實際寫入時間與依截止時間選版本）。"""
    NS = "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"
    TIFRS = "http://www.xbrl.org/tifrs/bsci/ci/2025-06-30"
    T0 = "2026-01-01T00:00:00+08:00"

    def _tag_map(self):
        import json
        with open(os.path.join(ROOT, "00_規範", "financial_tag_map.json"), encoding="utf-8") as f:
            return json.load(f)

    def _filing(self, facts):
        return {"ticker": "2330", "issuer_id": "ISSUER_TSMC", "year": 2026, "season": 2,
                "report_scope": "CONSOLIDATED", "url": "https://example.com", "raw_sha256": "a" * 64,
                "mapping_version": "0.6.0", "facts": facts,
                "manifest": {"fetched_at": "2026-10-07T12:00:00+08:00", "manifest_record_id": "M1",
                             "revision_sequence": 1, "doc_upload_at": "2026-08-14T13:59:44+08:00",
                             "correction_listed": False, "first_seen_at": None}}

    def _facts(self, **values):
        facts = {}
        for local, value in values.items():
            ns = self.TIFRS if local == "LongtermLiabilitiesCurrentPortion" else self.NS
            facts[(ns, local)] = [{"namespace": ns, "local_name": local, "context_ref": "c", "start": None,
                                   "end": "2026-06-30", "instant": True, "identifier": "2330", "value": float(value),
                                   "unit_ref": "TWD", "decimals": "-3", "scale": "3", "sign": None}]
        return facts

    def _debt(self, metric, tag_map=None, **values):
        import fetch_mops_financials as m
        values.setdefault("CurrentLiabilities", 1000)
        rows = m.build_filing_rows(self._filing(self._facts(**values)), tag_map or self._tag_map())
        return next((r for r in rows if r["metric_id"] == metric), None)

    @staticmethod
    def _names(items):
        return sorted(x["local_name"] for x in items)

    # ---------- C181-01 ----------
    def test_aggregate_and_details_both_present_excludes_aggregate_with_value(self):
        from data_spine import validate_observation
        r = self._debt("FIN_DEBT_CURRENT", LongtermLiabilitiesCurrentPortion=100,
                       CurrentPortionOfLongtermBorrowings=60, CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued=40)
        self.assertEqual(r["value"], 100.0)
        self.assertEqual(self._names(r["components"]), ["CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
                                                        "CurrentPortionOfLongtermBorrowings"])
        ex = r["excluded_overlapping_components"]
        self.assertEqual([(x["local_name"], x["value"], x["excluded_because"]) for x in ex],
                         [("LongtermLiabilitiesCurrentPortion", 100.0, "DETAILS_PRESENT")])
        self.assertEqual(r["covered_by_aggregate"], [])
        self.assertEqual(self._names(r["absent_components"]),
                         ["CurrentCommercialPapersIssuedAndCurrentPortionOfNoncurrentCommercialPapersIssued", "ShorttermBorrowings"])
        self.assertIn("ABSENT_COMPONENTS_TREATED_AS_NONE", r["limitations"])
        self.assertEqual(r["overlap_rules_version"], "TW_DEBT_OVERLAP_1")
        self.assertEqual(validate_observation(r), [])

    def test_only_aggregate_marks_details_covered_not_absent(self):
        from data_spine import validate_observation
        r = self._debt("FIN_DEBT_CURRENT", LongtermLiabilitiesCurrentPortion=100, ShorttermBorrowings=5,
                       CurrentCommercialPapersIssuedAndCurrentPortionOfNoncurrentCommercialPapersIssued=1)
        self.assertEqual(r["value"], 106.0)
        self.assertEqual(r["absent_components"], [])
        self.assertNotIn("ABSENT_COMPONENTS_TREATED_AS_NONE", r.get("limitations") or [])
        self.assertEqual(self._names(r["covered_by_aggregate"]),
                         ["CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", "CurrentPortionOfLongtermBorrowings"])
        self.assertEqual({x["aggregate_local_name"] for x in r["covered_by_aggregate"]}, {"LongtermLiabilitiesCurrentPortion"})
        self.assertEqual(r["excluded_overlapping_components"], [])
        self.assertEqual(validate_observation(r), [])

    def test_only_details_does_not_mark_unreported_aggregate_absent(self):
        # 1.8.3（C182-01）：1.8.2 版本把錯誤行為寫成預期值；細項已採用時，未申報的彙總不算缺席。
        from data_spine import validate_observation
        r = self._debt("FIN_DEBT_CURRENT", CurrentPortionOfLongtermBorrowings=60,
                       CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued=40, ShorttermBorrowings=5,
                       CurrentCommercialPapersIssuedAndCurrentPortionOfNoncurrentCommercialPapersIssued=1)
        self.assertEqual(r["value"], 106.0)
        self.assertEqual(r["covered_by_aggregate"], []); self.assertEqual(r["excluded_overlapping_components"], [])
        self.assertEqual(r["absent_components"], [])
        self.assertNotIn("ABSENT_COMPONENTS_TREATED_AS_NONE", r.get("limitations") or [])
        self.assertEqual([(x["local_name"], x["details_used"]) for x in r["aggregate_not_reported_details_used"]],
                         [("LongtermLiabilitiesCurrentPortion", ["CurrentPortionOfLongtermBorrowings",
                                                                 "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued"])])
        self.assertEqual(validate_observation(r), [])
        # 只採用一個細項時：彙總仍不算缺席，但沒申報的另一個細項是真正缺席
        one = self._debt("FIN_DEBT_CURRENT", CurrentPortionOfLongtermBorrowings=60)
        self.assertNotIn("LongtermLiabilitiesCurrentPortion", self._names(one["absent_components"]))
        self.assertIn("CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", self._names(one["absent_components"]))
        self.assertIn("ABSENT_COMPONENTS_TREATED_AS_NONE", one["limitations"])

    def test_all_absent_still_produces_nothing(self):
        self.assertIsNone(self._debt("FIN_DEBT_CURRENT"))
        self.assertIsNone(self._debt("FIN_DEBT_NONCURRENT"))

    def test_aggregate_first_rule_for_noncurrent(self):
        both = self._debt("FIN_DEBT_NONCURRENT", LongtermBorrowings=500, NoncurrentPortionOfNoncurrentLoansReceived=500)
        self.assertEqual(both["value"], 500.0)
        self.assertEqual([(x["local_name"], x["excluded_because"], x["overlaps_with"]) for x in both["excluded_overlapping_components"]],
                         [("NoncurrentPortionOfNoncurrentLoansReceived", "AGGREGATE_PRESENT", ["LongtermBorrowings"])])
        only = self._debt("FIN_DEBT_NONCURRENT", LongtermBorrowings=500)
        self.assertEqual(self._names(only["covered_by_aggregate"]), ["NoncurrentPortionOfNoncurrentLoansReceived"])
        self.assertEqual(self._names(only["absent_components"]), ["NoncurrentPortionOfNoncurrentBondsIssued"])

    def test_overlap_rules_come_from_tag_map(self):
        import copy
        tag_map = self._tag_map()
        prof = tag_map["taiwan"]["profiles"]["GENERAL_INDUSTRY_2024_2026"]
        flipped = copy.deepcopy(tag_map)
        flipped["taiwan"]["profiles"]["GENERAL_INDUSTRY_2024_2026"]["overlap_rules"]["FIN_DEBT_CURRENT"][0]["precedence"] = "AGGREGATE_WHEN_PRESENT"
        r = self._debt("FIN_DEBT_CURRENT", flipped, LongtermLiabilitiesCurrentPortion=100, CurrentPortionOfLongtermBorrowings=60)
        self.assertEqual(r["value"], 100.0)
        self.assertEqual(self._names(r["excluded_overlapping_components"]), ["CurrentPortionOfLongtermBorrowings"])
        missing = copy.deepcopy(tag_map)
        del missing["taiwan"]["profiles"]["GENERAL_INDUSTRY_2024_2026"]["overlap_rules"]
        with self.assertRaisesRegex(ValueError, "OVERLAP_RULES_MISSING"):
            self._debt("FIN_DEBT_CURRENT", missing, ShorttermBorrowings=1)
        self.assertEqual(tag_map["taiwan"]["status"], "ENABLED_AFTER_INDEPENDENT_REVIEW")
        self.assertEqual(tag_map["spec_version"], "0.6.0")
        self.assertEqual(prof["overlap_rules_version"], "TW_DEBT_OVERLAP_1")

    def test_all_three_lists_and_version_feed_the_hash(self):
        import fetch_mops_financials as m
        base = self._debt("FIN_DEBT_CURRENT", LongtermLiabilitiesCurrentPortion=100, CurrentPortionOfLongtermBorrowings=60)
        f = self._filing({}); fact = dict(base["components"][0], start=None, end="2026-06-30")
        overlap = {k: base[k] for k in ("covered_by_aggregate", "excluded_overlapping_components", "overlap_rules_version")}
        same = m._base_observation(f, "FIN_DEBT_CURRENT", fact, base["value"], "INSTANT", "COMPOSITE_SUM",
                                   base["components"], base["absent_components"], overlap)
        self.assertEqual(same["observation_id"], base["observation_id"])
        for key, value in (("covered_by_aggregate", [{"local_name": "X", "namespaces_checked": [self.NS],
                                                       "aggregate_local_name": "CurrentPortionOfLongtermBorrowings"}]),
                           ("excluded_overlapping_components", []), ("overlap_rules_version", "TW_DEBT_OVERLAP_2")):
            other = m._base_observation(f, "FIN_DEBT_CURRENT", fact, base["value"], "INSTANT", "COMPOSITE_SUM",
                                        base["components"], base["absent_components"], dict(overlap, **{key: value}))
            self.assertNotEqual(other["components_sha256"], base["components_sha256"], key)
            self.assertNotEqual(other["observation_id"], base["observation_id"], key)

    def test_validator_rejects_bad_overlap_records(self):
        from data_spine import validate_observation
        r = self._debt("FIN_DEBT_CURRENT", LongtermLiabilitiesCurrentPortion=100, CurrentPortionOfLongtermBorrowings=60)
        self.assertEqual(validate_observation(r), [])
        bad_overlap = dict(r, excluded_overlapping_components=[dict(r["excluded_overlapping_components"][0], overlaps_with=["Nope"])])
        self.assertTrue(validate_observation(bad_overlap))
        both = dict(r, absent_components=r["absent_components"] + [{"local_name": "LongtermLiabilitiesCurrentPortion", "namespaces_checked": [self.NS]}])
        self.assertTrue(validate_observation(both))
        no_version = {k: v for k, v in r.items() if k != "overlap_rules_version"}
        self.assertTrue(validate_observation(no_version))
        bad_cover = dict(r, covered_by_aggregate=[{"local_name": "ShorttermBorrowings", "namespaces_checked": [self.NS],
                                                    "aggregate_local_name": "LongtermLiabilitiesCurrentPortion"}])
        self.assertTrue(validate_observation(bad_cover))   # 彙總沒有被採用，不能涵蓋
        negative = dict(r, excluded_overlapping_components=[dict(r["excluded_overlapping_components"][0], value=-1.0)])
        self.assertTrue(validate_observation(negative))

    def test_canonical_182_debt_corrections(self):
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        rows = read_jsonl(OBSERVATIONS_PATH)
        by_id = {r["observation_id"]: r for r in rows[:2737]}
        new = rows[2737:2845]
        self.assertEqual(len(new), 108)
        for r in new:
            parent = by_id[r["supersedes_observation_id"]]
            self.assertIn("absent_components", parent)                       # 父列是 1.8.1 列
            self.assertEqual(r["revision_reason"], "DERIVATION_COMPONENT_CHANGE")
            self.assertEqual((r["value"], r["components"]), (parent["value"], parent["components"]))
            old = {a["local_name"] for a in parent["absent_components"]}
            self.assertEqual(old, {a["local_name"] for a in r["absent_components"]} | {a["local_name"] for a in r["covered_by_aggregate"]})
        self.assertEqual(sum(1 for r in new if r["covered_by_aggregate"]), 68)
        self.assertEqual(sum(1 for r in new if r["excluded_overlapping_components"]), 28)
        tsmc = next(r for r in new if r["issuer_id"] == "ISSUER_TSMC" and r["metric_id"] == "FIN_DEBT_CURRENT" and r["period_end"] == "2026-06-30")
        self.assertEqual(self._names(tsmc["covered_by_aggregate"]),
                         ["CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", "CurrentPortionOfLongtermBorrowings"])
        winbond = next(r for r in new if r["issuer_id"] == "ISSUER_WINBOND" and r["metric_id"] == "FIN_DEBT_CURRENT" and r["period_end"] == "2026-06-30")
        self.assertEqual([(x["local_name"], x["value"]) for x in winbond["excluded_overlapping_components"]],
                         [("LongtermLiabilitiesCurrentPortion", 43085137000.0)])

    def test_pre_182_observation_file_is_byte_identical_prefix(self):
        import hashlib
        from data_spine import OBSERVATIONS_PATH
        with open(OBSERVATIONS_PATH, "rb") as f: prefix = f.read(3159327)
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), "ec2189743d8d400378e25e5d61b854e7db4489cf9f1af58819163b35391b94a5")

    # ---------- C181-02 ----------
    def _row(self, oid, ingested=T0, parent=None, value=1.0, **extra):
        row = {"observation_id": oid, "issuer_id": "I", "metric_id": "FIN_CASH", "value": value,
               "period_end": "2026-06-30", "period_type": "INSTANT", "source_published_at": self.T0,
               "available_at": self.T0, "ingested_at": ingested, "availability_status": "POINT_IN_TIME_KNOWN",
               "supersedes_observation_id": parent}
        row.update(extra)
        return row

    def _pick(self, rows, cutoff, times):
        import health
        return [r["observation_id"] for r in health.point_in_time(rows, "I", cutoff, times)]

    def test_cutoff_between_parent_and_child_write_selects_parent(self):
        rows = [self._row("A"), self._row("B", parent="A", value=2.0)]   # 子列 ingested_at 與父列相同（沿用原始檔）
        times = {"A": None, "B": "2026-10-08T01:09:01+08:00"}
        self.assertEqual(self._pick(rows, "2026-10-08T00:00:00+08:00", times), ["A"])
        self.assertEqual(self._pick(rows, "2026-10-08T01:09:01+08:00", times), ["B"])

    def test_three_level_supersedes_chain(self):
        rows = [self._row("A"), self._row("B", parent="A"), self._row("C", parent="B")]
        times = {"A": "2026-10-01T00:00:00+08:00", "B": "2026-10-02T00:00:00+08:00", "C": "2026-10-03T00:00:00+08:00"}
        self.assertEqual(self._pick(rows, "2026-10-01T12:00:00+08:00", times), ["A"])
        self.assertEqual(self._pick(rows, "2026-10-02T12:00:00+08:00", times), ["B"])
        self.assertEqual(self._pick(rows, "2026-10-03T12:00:00+08:00", times), ["C"])
        self.assertEqual(self._pick(rows, "2025-12-31T00:00:00+08:00", times), [])

    def test_rows_without_write_record_use_ingested_at(self):
        rows = [self._row("A", ingested="2026-10-01T00:00:00+08:00"),
                self._row("B", ingested="2026-10-05T00:00:00+08:00", parent="A")]
        self.assertEqual(self._pick(rows, "2026-10-03T00:00:00+08:00", {}), ["A"])
        self.assertEqual(self._pick(rows, "2026-10-06T00:00:00+08:00", {}), ["B"])
        # ASSUMED_EQUAL_TO_INGESTED_AT 批次在紀錄裡是 None，同樣退回 ingested_at
        self.assertEqual(self._pick(rows, "2026-10-03T00:00:00+08:00", {"A": None, "B": None}), ["A"])

    def test_selection_written_at_override_keeps_attribution_on_filing_time(self):
        from data_spine import validate_observation
        rows = [self._row("A"), self._row("B", parent="A", selection_written_at=self.T0)]
        self.assertEqual(self._pick(rows, "2026-02-01T00:00:00+08:00", {"B": "2026-10-08T11:00:00+08:00"}), ["B"])
        self.assertTrue(any("selection_written_at" in e for e in validate_observation(
            dict(rows[1], unit="TWD", source_url="https://example.com"))))

    def test_codex_case_tsmc_debt_at_midnight_selects_180_parent(self):
        import health
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        obs = read_jsonl(OBSERVATIONS_PATH)
        def pick(cutoff):
            got = [r for r in health.point_in_time(obs, "ISSUER_TSMC", cutoff)
                   if r["metric_id"] == "FIN_DEBT_CURRENT" and r["period_end"] == "2026-06-30"]
            self.assertEqual(len(got), 1)
            return got[0]
        midnight = pick("2026-10-08T00:00:00+08:00")
        self.assertNotEqual(midnight["observation_id"],
                            "FIN_5851ae40a0aade664a9160f463ccb36d15c091173c3118e3206f9c849df2e1db")
        self.assertNotIn("absent_components", midnight); self.assertIsNone(midnight.get("supersedes_observation_id"))
        self.assertEqual(pick("2026-10-08T02:00:00+08:00")["observation_id"],
                         "FIN_5851ae40a0aade664a9160f463ccb36d15c091173c3118e3206f9c849df2e1db")
        self.assertIn("covered_by_aggregate", pick("2026-10-09T00:00:00+08:00"))

    def test_current_cutoff_selection_matches_unconditional_active_chain(self):
        import health, datetime
        from data_spine import read_jsonl, OBSERVATIONS_PATH
        obs = read_jsonl(OBSERVATIONS_PATH)
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        for issuer in sorted({r.get("issuer_id") for r in obs if r.get("issuer_id")}):
            rows = [r for r in obs if r.get("issuer_id") == issuer]
            legacy = [r["observation_id"] for r in health._active(rows) if r in health.point_in_time([r], issuer, now, {})]
            self.assertEqual([r["observation_id"] for r in health.point_in_time(rows, issuer, now)], legacy, issuer)

    def test_append_writes_ledger_and_ledger_is_append_only(self):
        import tempfile, json
        import fetch_mops_financials as m
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl, validate_write_ledger, write_ledger_path_for
        f = self._filing({}); fact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2026-06-30"}
        r1 = m._base_observation(f, "FIN_CASH", fact, 100.0, "INSTANT", "REPORTED_INSTANT")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.jsonl"); ledger = write_ledger_path_for(p)
            self.assertEqual(append_financial_observations([r1], p), 1)
            first = read_jsonl(ledger)
            self.assertEqual(len(first), 1); self.assertEqual(first[0]["observation_ids"], [r1["observation_id"]])
            self.assertEqual(first[0]["file_byte_length_after"], os.path.getsize(p))
            self.assertEqual(first[0]["reason"], "APPEND_FINANCIAL_OBSERVATIONS")
            self.assertEqual(first[0]["written_at_basis"], "RECORDED_AT_WRITE")
            with open(ledger, "rb") as h: before = h.read()
            self.assertEqual(append_financial_observations([r1], p), 0)            # 沒有新增就不記
            f2 = dict(f, raw_sha256="b" * 64); f2["manifest"] = dict(f["manifest"], revision_sequence=2, manifest_record_id="M2")
            r2 = m._base_observation(f2, "FIN_CASH", fact, 100.0, "INSTANT", "REPORTED_INSTANT")
            self.assertEqual(append_financial_observations([r2], p, ledger_reason="TEST"), 1)
            with open(ledger, "rb") as h: after = h.read()
            self.assertTrue(after.startswith(before))
            self.assertEqual([e["reason"] for e in read_jsonl(ledger)], ["APPEND_FINANCIAL_OBSERVATIONS", "TEST"])
            self.assertEqual(validate_write_ledger(p, ledger), [])
            with open(p, "a", encoding="utf-8") as h: h.write(json.dumps(dict(r2, observation_id="ROGUE")) + "\n")
            with self.assertRaisesRegex(ValueError, "WRITE_LEDGER_OUT_OF_SYNC"):
                append_financial_observations([dict(r2, observation_id="NEXT")], p)
            self.assertTrue(validate_write_ledger(p, ledger))

    def test_write_ledger_rejects_dishonest_or_duplicate_entries(self):
        import tempfile
        from data_spine import make_write_batch, append_write_ledger
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.jsonl"); ledger = os.path.join(d, "l.jsonl")
            with open(p, "w", encoding="utf-8") as h: h.write('{"observation_id":"A"}\n')
            with self.assertRaisesRegex(ValueError, "INVALID_WRITE_BATCH"):
                append_write_ledger(make_write_batch(["A"], p, "R", "2026-10-08T00:00:00+08:00",
                                                     "ASSUMED_EQUAL_TO_INGESTED_AT", "ASSUMED_EQUAL_TO_INGESTED_AT"), ledger)
            with self.assertRaisesRegex(ValueError, "INVALID_WRITE_BATCH"):
                append_write_ledger(make_write_batch(["A"], p, "R", "2026-10-08T00:00:00+08:00", "BACKFILLED_FROM_EVIDENCE"), ledger)
            append_write_ledger(make_write_batch(["A"], p, "R"), ledger)
            with open(p, "a", encoding="utf-8") as h: h.write('{"observation_id":"B"}\n')
            with self.assertRaisesRegex(ValueError, "OBSERVATION_ALREADY_IN_WRITE_LEDGER"):
                append_write_ledger(make_write_batch(["A", "B"], p, "R"), ledger)

    def test_canonical_write_ledger_is_complete_and_honest(self):
        from data_spine import read_jsonl, validate_write_ledger, WRITE_LEDGER_PATH
        self.assertEqual(validate_write_ledger(), [])
        entries = read_jsonl(WRITE_LEDGER_PATH)
        self.assertEqual([e["observation_count"] for e in entries[:3]], [2629, 108, 108])
        self.assertEqual((entries[0]["written_at"], entries[0]["evidence"]), (None, "ASSUMED_EQUAL_TO_INGESTED_AT"))
        self.assertEqual(entries[1]["written_at"], "2026-10-08T01:09:01+08:00")
        self.assertEqual(entries[1]["written_at_basis"], "BACKFILLED_FROM_EVIDENCE")
        self.assertIn("事後補登", entries[1]["evidence"]); self.assertIn("Codex", entries[1]["evidence"])
        self.assertEqual(entries[2]["written_at_basis"], "RECORDED_AT_WRITE")


class SimulatedCrash(BaseException):
    """模擬程式在寫入途中被強制結束（不經過 except Exception 的復原）。"""


class Patch183(unittest.TestCase):
    """1.8.3：C182-01（只有細項時彙總不算缺席）與 C182-02（觀測值與寫入紀錄一起成功或一起失敗）。"""
    NS = "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"

    # ---------- C182-01 ----------
    def _debt(self, metric, **values):
        return Patch182._debt(self, metric, **values)

    _tag_map = Patch182._tag_map
    _filing = Patch182._filing
    _facts = Patch182._facts
    TIFRS = Patch182.TIFRS

    def test_noncurrent_detail_only_marks_aggregate_not_reported(self):
        r = self._debt("FIN_DEBT_NONCURRENT", NoncurrentPortionOfNoncurrentLoansReceived=300)
        self.assertEqual(r["value"], 300.0)
        names = [a["local_name"] for a in r["absent_components"]]
        self.assertNotIn("LongtermBorrowings", names)
        self.assertIn("NoncurrentPortionOfNoncurrentBondsIssued", names)
        self.assertEqual(r["aggregate_not_reported_details_used"][0]["local_name"], "LongtermBorrowings")
        self.assertEqual(r["aggregate_not_reported_details_used"][0]["details_used"], ["NoncurrentPortionOfNoncurrentLoansReceived"])

    def test_unreported_aggregate_without_used_details_is_still_absent(self):
        r = self._debt("FIN_DEBT_CURRENT", ShorttermBorrowings=5)
        self.assertIn("LongtermLiabilitiesCurrentPortion", [a["local_name"] for a in r["absent_components"]])
        self.assertNotIn("aggregate_not_reported_details_used", r)

    def test_represented_list_feeds_hash_and_validator(self):
        import fetch_mops_financials as m
        from data_spine import validate_observation
        r = self._debt("FIN_DEBT_CURRENT", CurrentPortionOfLongtermBorrowings=60)
        self.assertEqual(validate_observation(r), [])
        overlap = {k: r[k] for k in ("covered_by_aggregate", "excluded_overlapping_components", "overlap_rules_version")}
        f = self._filing({}); fact = dict(r["components"][0], start=None, end="2026-06-30")
        without = m._base_observation(f, "FIN_DEBT_CURRENT", fact, r["value"], "INSTANT", "COMPOSITE_SUM",
                                      r["components"], r["absent_components"], overlap)
        self.assertNotEqual(without["components_sha256"], r["components_sha256"])
        self.assertNotEqual(without["observation_id"], r["observation_id"])
        self.assertTrue(validate_observation(dict(r, aggregate_not_reported_details_used=[])))
        bad = [dict(r["aggregate_not_reported_details_used"][0], details_used=["Nope"])]
        self.assertTrue(validate_observation(dict(r, aggregate_not_reported_details_used=bad)))
        clash = dict(r, absent_components=r["absent_components"] + [{"local_name": "LongtermLiabilitiesCurrentPortion",
                                                                     "namespaces_checked": [self.TIFRS]}])
        self.assertTrue(validate_observation(clash))
        legacy = {k: v for k, v in r.items() if k not in ("covered_by_aggregate", "excluded_overlapping_components", "overlap_rules_version")}
        self.assertTrue(validate_observation(legacy))

    def test_offline_recompute_leaves_every_active_mops_row_unchanged(self):
        import json
        import fetch_mops_financials as m
        from data_spine import (read_jsonl, OBSERVATIONS_PATH, load_supersede_semantics,
                                resolve_visible_observation_versions)
        with open(m.TAG_MAP_PATH, encoding="utf-8") as h:   # 1.8.4：改用 with，消除 ResourceWarning
            tag_map = json.load(h)
        filings = m.filings_from_dir(m.RAW_ROOT, tag_map)
        rows = read_jsonl(OBSERVATIONS_PATH)
        semantics = load_supersede_semantics(observations=rows)
        active_rows = [r for r in resolve_visible_observation_versions(rows, semantics=semantics)
                       if r.get("source_system") == "MOPS_XBRL"]
        active = {r["observation_id"] for r in active_rows}

        # T31 的 12 筆 first-seen 嘗試列已用 AVAILABILITY_CORRECTION 撤回。
        # 移除該批嘗試與控制列後重建「T31 寫入前」視圖，正式 resolver
        # 所得的有效 MOPS ID／數值必須逐列完全相同。
        pre_rows = [r for r in rows if not (
            "+T31-FIRST-SEEN-v1" in str(r.get("mapping_version")) or
            (r.get("source_system") == "INTERNAL_CONTROL_RETRACTION" and
             str(r.get("source_tag", "")).startswith("T31_")))]
        pre_ids = {r.get("observation_id") for r in pre_rows}
        pre_semantics = [s for s in semantics
                         if s.get("superseding_observation_id") in pre_ids and
                         s.get("superseded_observation_id") in pre_ids]
        pre_active_rows = [r for r in resolve_visible_observation_versions(pre_rows, semantics=pre_semantics)
                           if r.get("source_system") == "MOPS_XBRL"]
        identity = lambda r: (r["observation_id"], r.get("value"))
        self.assertEqual(sorted(map(identity, active_rows)), sorted(map(identity, pre_active_rows)))

        # Active canonical rows now include the authorized 2021-2023 V1 history.
        # Rebuild exactly every period represented by an active row.  This keeps
        # the reproducibility gate complete without treating dependency-only raw
        # filings (for example a prior Q1 used to quarterize Q2) as publishable.
        active_periods = {(int(r["fiscal_year"]), int(str(r["fiscal_period"])[1:]))
                          for r in active_rows}
        new = m.prepare_rows(filings, tag_map, active_periods)
        new_ids = {r["observation_id"] for r in new}
        self.assertTrue(active <= new_ids)
        self.assertTrue(all(
            r["period_type"] == "FY" or r["metric_id"].startswith("VER_") or
            (r.get("availability_basis") == "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE" and
             r.get("availability_status") == "POINT_IN_TIME_KNOWN" and
             str(r.get("metric_id", "")).startswith("FIN_") and
             str(r.get("mapping_version", "")).endswith("+G-S3-9b-v1"))
            for r in new if r["observation_id"] not in active))
        self.assertFalse([r for r in new if "aggregate_not_reported_details_used" in r])

    # ---------- C182-02 ----------
    def _setup(self, d):
        import fetch_mops_financials as m
        from fetch_financials import append_financial_observations
        f = Patch182._filing(self, {}); fact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2026-06-30"}
        self.p = os.path.join(d, "observations.jsonl")
        base = m._base_observation(f, "FIN_CASH", fact, 100.0, "INSTANT", "REPORTED_INSTANT")
        self.assertEqual(append_financial_observations([base], self.p), 1)
        f2 = dict(f, raw_sha256="b" * 64); f2["manifest"] = dict(f["manifest"], revision_sequence=2, manifest_record_id="M2")
        self.next_row = m._base_observation(f2, "FIN_CASH", fact, 101.0, "INSTANT", "REPORTED_INSTANT")
        return append_financial_observations

    def _snapshot(self):
        from data_spine import write_ledger_path_for
        with open(self.p, "rb") as h: obs = h.read()
        with open(write_ledger_path_for(self.p), "rb") as h: led = h.read()
        return obs, led

    def _inject(self, stage, exc):
        import data_spine
        def hook(s):
            if s == stage:
                raise exc
        data_spine._FAULT_HOOK = hook

    def _clear(self):
        import data_spine
        data_spine._FAULT_HOOK = None

    def _assert_restored(self, before):
        from data_spine import journal_path_for, validate_write_ledger, read_observations_checked
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(os.path.exists(journal_path_for(self.p)))
        self.assertEqual(validate_write_ledger(self.p), [])
        self.assertEqual(len(read_observations_checked(self.p)), 1)

    def test_fault_ledger_write_fails_rolls_back_observations(self):
        import tempfile
        from data_spine import journal_path_for
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d); before = self._snapshot()
            for stage in ("ledger:before", "ledger:mid", "ledger:after", "before_commit"):
                self._inject(stage, OSError("模擬：" + stage))
                try:
                    with self.assertRaises(OSError):
                        append([self.next_row], self.p)
                finally:
                    self._clear()
                self._assert_restored(before)
                # 1.8.4（C183-03）：封存檔名含時間與雜湊，每次復原各留一份
                from data_spine import journal_archives
                self.assertTrue(journal_archives(self.p, "已復原"))
            self.assertEqual(append([self.next_row], self.p), 1)   # 故障排除後可以正常寫

    def test_fault_observation_append_half_written_rolls_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d); before = self._snapshot()
            for stage in ("journal_written", "observations:before", "observations:mid", "observations:after"):
                self._inject(stage, OSError("模擬：" + stage))
                try:
                    with self.assertRaises(OSError):
                        append([self.next_row], self.p)
                finally:
                    self._clear()
                self._assert_restored(before)

    def test_fault_crash_leaves_journal_and_next_start_recovers(self):
        import tempfile
        from data_spine import journal_path_for, read_observations_checked, validate_write_ledger, read_jsonl
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d); before = self._snapshot()
            self._inject("observations:mid", SimulatedCrash())
            try:
                with self.assertRaises(SimulatedCrash):
                    append([self.next_row], self.p)
            finally:
                self._clear()
            self.assertTrue(os.path.exists(journal_path_for(self.p)))            # 當掉：journal 與半行都留著
            self.assertGreater(len(self._snapshot()[0]), len(before[0]))
            with self.assertRaisesRegex(ValueError, "OBSERVATIONS_WRITE_PENDING"):
                read_observations_checked(self.p)                                # 讀取端拒絕
            self.assertEqual(append([self.next_row], self.p), 1)                 # 下次寫入先復原再做事
            self.assertEqual(validate_write_ledger(self.p), [])
            self.assertEqual([r["value"] for r in read_jsonl(self.p)], [100.0, 101.0])
            self.assertEqual(self._snapshot()[0][:len(before[0])], before[0])

    def test_fault_crash_after_both_writes_is_completed_not_rolled_back(self):
        import tempfile
        from data_spine import journal_path_for, recover_pending_write, validate_write_ledger, read_observations_checked
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d)
            self._inject("before_commit", SimulatedCrash())
            try:
                with self.assertRaises(SimulatedCrash):
                    append([self.next_row], self.p)
            finally:
                self._clear()
            self.assertTrue(os.path.exists(journal_path_for(self.p)))
            self.assertEqual(recover_pending_write(self.p), "COMPLETED")
            self.assertFalse(os.path.exists(journal_path_for(self.p)))
            self.assertEqual(validate_write_ledger(self.p), [])
            self.assertEqual(len(read_observations_checked(self.p)), 2)

    def test_recovery_never_truncates_changed_existing_content(self):
        import tempfile
        from data_spine import journal_path_for, recover_pending_write, WriteJournalError
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d)
            self._inject("observations:mid", SimulatedCrash())
            try:
                with self.assertRaises(SimulatedCrash):
                    append([self.next_row], self.p)
            finally:
                self._clear()
            with open(self.p, "r+b") as h:                                       # 有人改了既有內容的第一個位元組
                first = h.read(1); h.seek(0); h.write(b" " if first != b" " else b"{")
            with open(self.p, "rb") as h: tampered = h.read()
            with self.assertRaises(WriteJournalError):
                recover_pending_write(self.p)
            with open(self.p, "rb") as h: self.assertEqual(h.read(), tampered)
            self.assertTrue(os.path.exists(journal_path_for(self.p)))           # 留給人工處理

    def test_reader_rejects_unregistered_rows(self):
        import tempfile, json
        from data_spine import read_observations_checked, write_ledger_path_for
        with tempfile.TemporaryDirectory() as d:
            self._setup(d)
            self.assertEqual(len(read_observations_checked(self.p)), 1)
            with open(self.p, "a", encoding="utf-8") as h: h.write(json.dumps(dict(self.next_row)) + "\n")
            with self.assertRaisesRegex(ValueError, "OBSERVATIONS_NOT_IN_WRITE_LEDGER"):
                read_observations_checked(self.p)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "observations.jsonl")
            with open(p, "w", encoding="utf-8") as h: h.write('{"observation_id":"X"}\n')
            with self.assertRaisesRegex(ValueError, "OBSERVATIONS_WRITE_LEDGER_MISSING"):
                read_observations_checked(p)
            self.assertFalse(os.path.exists(write_ledger_path_for(p)))

    def test_all_reader_entry_points_stop_on_unregistered_rows(self):
        import tempfile, json, data_spine, health, trend, company, attribution
        with tempfile.TemporaryDirectory() as d:
            self._setup(d)
            with open(self.p, "a", encoding="utf-8") as h: h.write(json.dumps(dict(self.next_row)) + "\n")
            old_path, old_argv = data_spine.OBSERVATIONS_PATH, sys.argv
            data_spine.OBSERVATIONS_PATH = self.p
            try:
                with self.assertRaisesRegex(ValueError, "OBSERVATIONS_NOT_IN_WRITE_LEDGER"):
                    company.build(RULES, ctx={"tr": {}}, cutoff="2026-10-08T12:00:00+08:00")
                out = attribution.health_as_of_event(RULES, [{"status": "OK", "ticker": "MU", "end_date": "2026-10-02", "market": "US"}])
                self.assertIn("__error__", out); self.assertNotIn(("MU", "2026-10-02"), out)
                sys.argv = ["health.py", "--json", "--no-ledger", "--output", os.path.join(d, "h.json")]
                with self.assertRaisesRegex(ValueError, "OBSERVATIONS_NOT_IN_WRITE_LEDGER"):
                    health.main()
                self.assertFalse(os.path.exists(os.path.join(d, "h.json")))
                sys.argv = ["trend.py", "--no-ledger"]
                with self.assertRaisesRegex(ValueError, "OBSERVATIONS_NOT_IN_WRITE_LEDGER"):
                    trend.main()
                with self.assertRaisesRegex(ValueError, "OBSERVATIONS_NOT_IN_WRITE_LEDGER"):
                    data_spine.schema2_run_inputs({"run_schema_version": 2}, RULES, sessions=[], events=[])
            finally:
                data_spine.OBSERVATIONS_PATH, sys.argv = old_path, old_argv

    def test_canonical_observations_and_ledger_unchanged_and_in_sync(self):
        import hashlib
        from data_spine import OBSERVATIONS_PATH, WRITE_LEDGER_PATH, read_observations_checked, journal_path_for
        with open(OBSERVATIONS_PATH, "rb") as h:
            self.assertEqual(hashlib.sha256(h.read(3444475)).hexdigest(), "51d69bc49df3f7f1f4cfb7e606a7d3b25a52de45410c984f35dc2742ffb863f7")
        with open(WRITE_LEDGER_PATH, "rb") as h:   # 1.8.2 的 3 批寫入紀錄逐位元不變
            self.assertEqual(hashlib.sha256(h.read(206582)).hexdigest(), "85036694b416998ff8815ed6f8ecdb868ce66bcd4df0c213ae86ab597313b416")
        self.assertGreaterEqual(len(read_observations_checked()), 2845)
        self.assertFalse(os.path.exists(journal_path_for(OBSERVATIONS_PATH)))


class Patch184(unittest.TestCase):
    """1.8.4：C183-01（寫入紀錄半行時的硬當機復原）、C183-02（兩檔預檢後才截斷）、C183-03（journal 封存不覆寫）。
    全部在暫存目錄進行。"""

    _setup = Patch183._setup
    _snapshot = Patch183._snapshot
    _inject = Patch183._inject
    _clear = Patch183._clear

    def _crash_at(self, append, stage):
        self._inject(stage, SimulatedCrash())
        try:
            with self.assertRaises(SimulatedCrash):
                append([self.next_row], self.p)
        finally:
            self._clear()

    @staticmethod
    def _rb(path):
        with open(path, "rb") as h:
            return h.read()

    def _both_bytes(self):
        from data_spine import write_ledger_path_for
        out = []
        for path in (self.p, write_ledger_path_for(self.p)):
            out.append(self._rb(path) if os.path.exists(path) else None)
        return tuple(out)

    # ---------- C183-01 ----------
    def test_hard_crash_around_ledger_write_recovers_next_time(self):
        import tempfile
        from data_spine import journal_path_for, recover_pending_write, validate_write_ledger, read_observations_checked
        for stage, expected in (("ledger:before", "ROLLED_BACK"), ("ledger:mid", "ROLLED_BACK"), ("ledger:after", "COMPLETED")):
            with tempfile.TemporaryDirectory() as d:
                append = self._setup(d); before = self._snapshot()
                self._crash_at(append, stage)
                self.assertTrue(os.path.exists(journal_path_for(self.p)), stage)
                if stage == "ledger:mid":                                       # 寫入紀錄尾端確實是半行
                    self.assertFalse(self._snapshot()[1].endswith(b"\n"))
                self.assertEqual(recover_pending_write(self.p), expected, stage)
                self.assertFalse(os.path.exists(journal_path_for(self.p)))
                self.assertEqual(validate_write_ledger(self.p), [], stage)
                if expected == "ROLLED_BACK":
                    self.assertEqual(self._snapshot(), before, stage)
                    self.assertEqual(append([self.next_row], self.p), 1)        # 復原後可正常寫入
                self.assertEqual(len(read_observations_checked(self.p)), 2, stage)

    def test_next_append_auto_recovers_half_ledger_line(self):
        import tempfile
        from data_spine import validate_write_ledger, read_jsonl
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d)
            self._crash_at(append, "ledger:mid")
            self.assertEqual(append([self.next_row], self.p), 1)                # 不需人工，下一次寫入自動截回再寫
            self.assertEqual(validate_write_ledger(self.p), [])
            self.assertEqual([r["value"] for r in read_jsonl(self.p)], [100.0, 101.0])

    def test_real_process_exit_during_ledger_write(self):
        import tempfile, json
        from data_spine import recover_pending_write, journal_path_for, validate_write_ledger
        with tempfile.TemporaryDirectory() as d:
            self._setup(d); before = self._snapshot()
            row_path = os.path.join(d, "row.json")
            with open(row_path, "w", encoding="utf-8") as h: json.dump(self.next_row, h)
            script = (
                "import sys, os, json\n"
                f"sys.path.insert(0, {os.path.join(ROOT, '03_工具')!r})\n"
                "import data_spine\n"
                "from fetch_financials import append_financial_observations\n"
                "data_spine._FAULT_HOOK = lambda s: os._exit(9) if s == 'ledger:mid' else None\n"
                f"row = json.load(open({row_path!r}, encoding='utf-8'))\n"
                f"append_financial_observations([row], {self.p!r})\n")
            proc = subprocess.run([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 9, proc.stderr.decode("utf-8", "replace"))
            self.assertTrue(os.path.exists(journal_path_for(self.p)))
            self.assertGreater(len(self._snapshot()[1]), len(before[1]))        # 寫入紀錄留下半行
            self.assertEqual(recover_pending_write(self.p), "ROLLED_BACK")
            self.assertEqual(self._snapshot(), before)
            self.assertEqual(validate_write_ledger(self.p), [])

    # ---------- C183-02 ----------
    def _pending(self, d):
        """觀測值寫到一半就硬當機，留下 journal；回傳 append。"""
        append = self._setup(d)
        self._crash_at(append, "observations:mid")
        return append

    def _assert_untouched_after_failed_recovery(self, before):
        from data_spine import recover_pending_write, journal_path_for, WriteJournalError
        with self.assertRaises(WriteJournalError):
            recover_pending_write(self.p)
        self.assertEqual(self._both_bytes(), before)
        self.assertTrue(os.path.exists(journal_path_for(self.p)))

    def test_preflight_tampered_ledger_prefix_touches_nothing(self):
        import tempfile, json
        from data_spine import write_ledger_path_for
        with tempfile.TemporaryDirectory() as d:
            self._pending(d); ledger = write_ledger_path_for(self.p)
            raw = self._rb(ledger)
            tampered = raw.replace(b"APPEND_FINANCIAL_OBSERVATIONS", b"APPEND_FINANCIAL_OBSERVATIONX", 1)
            self.assertNotEqual(raw, tampered)
            with open(ledger, "wb") as h: h.write(tampered)
            json.loads(tampered.decode("utf-8").splitlines()[0])                # 仍是合法 JSON
            self._assert_untouched_after_failed_recovery(self._both_bytes())

    def test_preflight_tampered_observation_prefix_touches_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._pending(d)
            raw = self._rb(self.p)
            with open(self.p, "wb") as h: h.write(raw.replace(b'"TWD"', b'"TWX"', 1))
            self._assert_untouched_after_failed_recovery(self._both_bytes())

    def test_preflight_missing_file_touches_nothing(self):
        import tempfile
        from data_spine import write_ledger_path_for
        for which in ("observations", "ledger"):
            with tempfile.TemporaryDirectory() as d:
                self._pending(d)
                target = self.p if which == "observations" else write_ledger_path_for(self.p)
                os.rename(target, target + ".moved")
                self._assert_untouched_after_failed_recovery(self._both_bytes())
                self.assertTrue(os.path.exists(target + ".moved"))

    def test_preflight_file_shorter_than_before_touches_nothing(self):
        import tempfile
        from data_spine import write_ledger_path_for
        for which in ("observations", "ledger"):
            with tempfile.TemporaryDirectory() as d:
                self._pending(d)
                target = self.p if which == "observations" else write_ledger_path_for(self.p)
                pre = {"observations": len(self._snapshot()[0]), "ledger": len(self._snapshot()[1])}
                import json
                from data_spine import journal_path_for
                j = json.loads(self._rb(journal_path_for(self.p)).decode("utf-8"))
                length = j["observations_length_before"] if which == "observations" else j["ledger_length_before"]
                with open(target, "r+b") as h: h.truncate(length - 5)
                self._assert_untouched_after_failed_recovery(self._both_bytes())

    def test_in_process_failure_with_tampered_ledger_does_not_truncate_observations(self):
        import tempfile
        from data_spine import write_ledger_path_for, WriteJournalError, journal_path_for
        import data_spine
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d); ledger = write_ledger_path_for(self.p)
            def hook(stage):
                if stage == "observations:after":                              # 觀測值寫完後，有人動了寫入紀錄舊內容
                    raw = self._rb(ledger)
                    with open(ledger, "wb") as h: h.write(raw.replace(b"APPEND_FINANCIAL_OBSERVATIONS", b"APPEND_FINANCIAL_OBSERVATIONX", 1))
                    raise OSError("模擬")
            data_spine._FAULT_HOOK = hook
            try:
                with self.assertRaises(WriteJournalError):
                    append([self.next_row], self.p)
            finally:
                self._clear()
            obs = self._rb(self.p)
            self.assertIn(b'"value": 101.0', obs)                              # 沒有截斷觀測值
            self.assertTrue(os.path.exists(journal_path_for(self.p)))

    # ---------- C183-03 ----------
    def test_completed_and_rolled_back_journals_are_all_kept(self):
        import tempfile, json
        import fetch_mops_financials as m
        from data_spine import journal_archives, read_jsonl, write_ledger_path_for
        with tempfile.TemporaryDirectory() as d:
            append = self._setup(d)
            f = Patch182._filing(self, {}); fact = {"namespace": "n", "local_name": "Cash", "start": None, "end": "2026-06-30"}
            for seq, value in ((3, 102.0), (4, 103.0)):
                f2 = dict(f, raw_sha256=str(seq) * 64); f2["manifest"] = dict(f["manifest"], revision_sequence=seq, manifest_record_id=f"M{seq}")
                self.assertEqual(append([m._base_observation(f2, "FIN_CASH", fact, value, "INSTANT", "REPORTED_INSTANT")], self.p), 1)
            done = journal_archives(self.p, "完成")
            self.assertEqual(len(done), 3)                                     # 初始一批＋連續兩批
            ledger_ids = [e["observation_ids"] for e in read_jsonl(write_ledger_path_for(self.p))]
            archived_ids = sorted(json.loads(self._rb(x).decode("utf-8"))["observation_ids"] for x in done)
            self.assertEqual(archived_ids, sorted(ledger_ids))
            f5 = dict(f, raw_sha256="5" * 64); f5["manifest"] = dict(f["manifest"], revision_sequence=5, manifest_record_id="M5")
            newer = m._base_observation(f5, "FIN_CASH", fact, 105.0, "INSTANT", "REPORTED_INSTANT")   # 序號要比既有列新才會真的寫入
            for stage in ("observations:mid", "ledger:mid"):
                self._inject(stage, OSError("模擬"))
                try:
                    with self.assertRaises(OSError):
                        append([newer], self.p)
                finally:
                    self._clear()
            self.assertEqual(len(journal_archives(self.p, "已復原")), 2)
            self.assertEqual(len(journal_archives(self.p, "完成")), 3)

    def test_archive_never_overwrites_existing_journal(self):
        import tempfile
        from data_spine import _archive_journal, WriteJournalError
        with tempfile.TemporaryDirectory() as d:
            j = os.path.join(d, "observations.jsonl.write_journal.json")
            content = b'{"created_at": "2026-10-08T12:00:00.000001+08:00", "x": 1}'
            with open(j, "wb") as h: h.write(content)
            first = _archive_journal(j, "完成")
            with open(j, "wb") as h: h.write(content)                          # 完全相同的 journal → 相同封存名
            with self.assertRaises(WriteJournalError):
                _archive_journal(j, "完成")
            self.assertEqual(self._rb(first), content)
            self.assertTrue(os.path.exists(j))

    def test_canonical_spine_has_no_pending_journal_and_files_unchanged(self):
        import hashlib
        from data_spine import OBSERVATIONS_PATH, WRITE_LEDGER_PATH, journal_path_for, validate_write_ledger
        self.assertFalse(os.path.exists(journal_path_for(OBSERVATIONS_PATH)))
        with open(OBSERVATIONS_PATH, "rb") as h:   # 1.8.2 結束時的全檔，作為前綴逐位元不變
            self.assertEqual(hashlib.sha256(h.read(3444475)).hexdigest(), "51d69bc49df3f7f1f4cfb7e606a7d3b25a52de45410c984f35dc2742ffb863f7")
        with open(WRITE_LEDGER_PATH, "rb") as h:
            self.assertEqual(hashlib.sha256(h.read(206582)).hexdigest(), "85036694b416998ff8815ed6f8ecdb868ce66bcd4df0c213ae86ab597313b416")
        self.assertEqual(validate_write_ledger(), [])


class Patch186(unittest.TestCase):
    """1.8.6：C185-01 文字依狀態切換；3081 台股零值聲明格式。"""
    def test_c185_01_scope_note_follows_tag_map_status(self):
        import trend, health, tempfile
        self.assertIn("已通過獨立複查與原文抽查並正式取用", trend.taiwan_scope_note())
        with tempfile.TemporaryDirectory() as d:
            p = Patch181()._tag_map_copy(d, "DATA_INSUFFICIENT_MOPS_XBRL_ADAPTER_PENDING")
            self.assertIn("台股財報待複查啟用", trend.taiwan_scope_note(p))
            self.assertNotIn("正式取用", trend.taiwan_scope_note(p))
    def test_mops_zero_assertions_approved_and_well_formed(self):
        import fetch_financials as ff
        data = load_json(ff.ZERO_ASSERTIONS_PATH)
        mops = [a for a in data["assertions"] if a.get("source_system") == "MOPS_FILING_ZERO_ASSERTION"]
        self.assertEqual(len(mops), 39)
        for a in mops:
            self.assertEqual(ff.validate_zero_assertion(a), [], a["assertion_id"])
            self.assertEqual(a["review_status"], "APPROVED_BY_INDEPENDENT_AI")
        landmark_debt = [a for a in mops if a["issuer_id"] == "ISSUER_LANDMARK"]
        self.assertEqual(len(landmark_debt), 18)
        self.assertTrue(all(a["report_scope"] == "INDIVIDUAL" for a in landmark_debt))
        short_term = [a for a in mops if a["metric_id"] == "FIN_SHORT_TERM_INVESTMENTS"]
        self.assertEqual(len(short_term), 21)
        self.assertEqual({a["issuer_id"] for a in short_term}, {"ISSUER_WINBOND", "ISSUER_NANYA", "ISSUER_AVC"})
        self.assertTrue(all(a["report_scope"] == "CONSOLIDATED" for a in short_term))
        rows, _ = ff.zero_assertion_observations(data, "2026-10-08T15:00:00+08:00")
        mops_rows = [r for r in rows if r["source_system"] == "MOPS_FILING_ZERO_ASSERTION"]
        self.assertEqual(len(mops_rows), 39)
        rows, _ = ff.zero_assertion_observations(data, "2026-10-08T15:00:00+08:00", include_pending=True)
        lm = [r for r in rows if r["issuer_id"] == "ISSUER_LANDMARK"]
        self.assertEqual(len(lm), 18); self.assertTrue(all(r["source_system"] == "MOPS_FILING_ZERO_ASSERTION" for r in lm))
        sec = [r for r in rows if r["source_system"] == "SEC_FILING_ZERO_ASSERTION"]
        self.assertTrue(sec and all(r["source_system"] == "SEC_FILING_ZERO_ASSERTION" for r in sec))
        bad = dict(mops[0]); bad.pop("raw_sha256")
        self.assertIn("MOPS 零值聲明缺少 raw_sha256", ff.validate_zero_assertion(bad))
        bad = dict(mops[0], review_status="APPROVED_BY_INDEPENDENT_AI", reviewed_by="Claude", reviewed_at="x")
        self.assertIn("查證者與複查者不可相同", ff.validate_zero_assertion(bad))


class Patch187(unittest.TestCase):
    """1.8.7：Q4 年報產生完整年度觀測值；缺季或年度內報表範圍不一致時拒絕產生。"""
    def _filings(self, year=2025, scopes=None):
        scopes = scopes or ["CONSOLIDATED"] * 4
        values = [10, 30, 60, 100]
        filings = []
        for season, (scope, value) in enumerate(zip(scopes, values), 1):
            f = MopsFinancialAdapter()._filing(season, scope)
            f["year"] = year
            f["manifest"] = dict(f["manifest"], manifest_record_id=f"M{year}Q{season}")
            end = f"{year}-{season * 3:02d}-{31 if season in (1, 4) else 30:02d}"
            if season == 1:
                end = f"{year}-03-31"
            f["cumulative"] = {
                "FIN_CFO": ({"namespace": "n", "local_name": "CFO", "start": f"{year}-01-01", "end": end, "value": value}, value),
                "FIN_CAPEX": ({"namespace": "n", "local_name": "CAPEX", "start": f"{year}-01-01", "end": end, "value": value / 2}, value / 2),
            }
            f["cumulative_direct"] = {
                "FIN_REVENUE": ({"namespace": "n", "local_name": "Revenue", "start": f"{year}-01-01", "end": end, "value": value * 10}, value * 10)
            }
            filings.append(f)
        return filings

    def test_q4_annual_report_generates_fy_for_both_cumulative_paths(self):
        import fetch_mops_financials as m
        rows = m.quarterize(self._filings(), [])
        fy = {(r["metric_id"], r["value"], r["period_start"], r["derivation"])
              for r in rows if r["period_type"] == "FY"}
        self.assertIn(("FIN_CFO", 100, "2025-01-01", "REPORTED_FY_FROM_Q4_ANNUAL_REPORT"), fy)
        self.assertIn(("FIN_CAPEX", 50, "2025-01-01", "REPORTED_FY_FROM_Q4_ANNUAL_REPORT"), fy)
        self.assertIn(("FIN_REVENUE", 1000, "2025-01-01", "REPORTED_FY_FROM_Q4_ANNUAL_REPORT"), fy)

    def test_no_q4_or_scope_mismatch_produces_no_fy_and_records_reason(self):
        import fetch_mops_financials as m
        missing = self._filings()
        del missing[1]
        self.assertFalse([r for r in m.quarterize(missing, []) if r["period_type"] == "FY"])
        self.assertIn("FY_REQUIRES_Q1_TO_Q4_FILINGS", missing[-1]["fy_exclusion_reasons"])
        mismatch = self._filings(scopes=["CONSOLIDATED", "CONSOLIDATED", "INDIVIDUAL", "CONSOLIDATED"])
        self.assertFalse([r for r in m.quarterize(mismatch, []) if r["period_type"] == "FY"])
        self.assertIn("FY_REPORT_SCOPE_MISMATCH_WITHIN_YEAR", mismatch[-1]["fy_exclusion_reasons"])

    def test_fy_equals_sum_of_four_quarters(self):
        import fetch_mops_financials as m
        rows = m.quarterize(self._filings(), [])
        for metric in ("FIN_CFO", "FIN_CAPEX"):
            fy = next(r for r in rows if r["metric_id"] == metric and r["period_type"] == "FY")
            quarters = [r for r in rows if r["metric_id"] == metric and r["period_type"] == "Q"]
            self.assertEqual(fy["value"], sum(r["value"] for r in quarters), metric)

    def test_fy_observation_id_is_deterministic(self):
        import fetch_mops_financials as m
        first = [r["observation_id"] for r in m.quarterize(self._filings(), []) if r["period_type"] == "FY"]
        second = [r["observation_id"] for r in m.quarterize(self._filings(), []) if r["period_type"] == "FY"]
        self.assertEqual(first, second)

    def test_h5_reader_uses_two_complete_fiscal_years(self):
        import fetch_mops_financials as m, health
        rows = m.quarterize(self._filings(2024) + self._filings(2025), [])
        values, used = health._annual_fcf(rows)
        self.assertEqual(values, [50.0, 50.0])
        self.assertEqual(len(used), 4)

class MopsPdfDilutedShares(unittest.TestCase):
    def _row(self, oid, period_end="2025-06-30", value=100, seq=1, derivation="REPORTED_3M_FROM_MOPS_PDF_EPS_NOTE",
             published="2025-08-01T12:00:00+08:00"):
        status = "POINT_IN_TIME_KNOWN" if published else "BACKFILLED_UNKNOWN"
        basis = "OFFICIAL_TIMESTAMP" if published else "UNKNOWN_BACKFILL"
        return {"observation_id": oid, "issuer_id": "ISSUER_TEST", "security_id": None,
                "metric_id": "FIN_DILUTED_SHARES", "value": value, "unit": "shares",
                "period_start": period_end[:5] + "04-01", "period_end": period_end, "period_type": "Q",
                "fiscal_year": int(period_end[:4]), "fiscal_period": "Q2", "source_system": "MOPS_PDF",
                "source_url": "https://doc.twse.com.tw/server-java/t57sb01", "source_published_at": published,
                "available_at": published, "ingested_at": "2026-10-13T01:00:00+08:00",
                "availability_status": status, "availability_basis": basis,
                "time_precision": "EXACT_TIMESTAMP" if published else "UNKNOWN",
                "raw_sha256": "a" * 64, "source_page": 10,
                "source_pdf_table_title": "每股盈餘附註", "report_scope": "CONSOLIDATED",
                "review_status": "APPROVED_BY_INDEPENDENT_AI", "reviewer": "Claude",
                "reviewed_at": "2026-10-13", "checked_by": "Claude Code",
                "manifest_revision_sequence": seq, "derivation": derivation,
                "supersedes_observation_id": None}

    def test_validator_enforces_pdf_provenance_and_review(self):
        from data_spine import validate_observation
        row = self._row("PDF1")
        self.assertEqual(validate_observation(row), [])
        for field in ("raw_sha256", "source_page", "source_pdf_table_title", "review_status"):
            bad = dict(row); bad[field] = None
            self.assertTrue(validate_observation(bad), field)
        bad = dict(row, reviewer="Claude Code")
        self.assertTrue(any("不得相同" in x for x in validate_observation(bad)))

    def test_comparative_requires_period_and_valid_revision_reason(self):
        from data_spine import validate_observation
        row = self._row("PDF2", derivation="COMPARATIVE_3M_FROM_LATER_MOPS_PDF")
        self.assertTrue(any("comparative_of_period" in x for x in validate_observation(row)))
        row["comparative_of_period"] = "2024Q2"
        row["supersedes_observation_id"] = "PARENT"
        row["revision_reason"] = "WRONG"
        self.assertTrue(any("revision_reason" in x for x in validate_observation(row)))

    def test_pdf_append_sets_restated_reason_and_accepts_unknown_backfill(self):
        import tempfile
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "observations.jsonl")
            old = self._row("PDF_OLD", value=100, seq=1, published=None)
            new = self._row("PDF_NEW", value=110, seq=2, published=None,
                            derivation="COMPARATIVE_3M_FROM_LATER_MOPS_PDF")
            new["comparative_of_period"] = "2025Q2"
            self.assertEqual(append_financial_observations([old, new], path), 2)
            rows = read_jsonl(path)
            self.assertEqual(rows[-1]["supersedes_observation_id"], "PDF_OLD")
            self.assertEqual(rows[-1]["revision_reason"], "COMPARATIVE_RESTATED")

    def test_same_value_comparative_reason_is_confirmed(self):
        import tempfile
        from fetch_financials import append_financial_observations
        from data_spine import read_jsonl
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "observations.jsonl")
            old = self._row("PDF_OLD", value=100, seq=1)
            new = self._row("PDF_NEW", value=100, seq=2, published="2026-08-01T12:00:00+08:00",
                            derivation="COMPARATIVE_3M_FROM_LATER_MOPS_PDF")
            new["comparative_of_period"] = "2025Q2"
            self.assertEqual(append_financial_observations([old, new], path), 2)
            self.assertEqual(read_jsonl(path)[-1]["revision_reason"], "COMPARATIVE_CONFIRMED_UNCHANGED")

    def test_current_and_comparative_same_batch_produce_expected_h7(self):
        import health
        prior = self._row("PRIOR", value=92432000, seq=1, published="2025-07-30T16:05:11+08:00")
        prior["ingested_at"] = "2026-10-13T01:00:00+08:00"
        restated = self._row("RESTATED", value=101613000, seq=2, published="2026-08-12T17:18:12+08:00",
                             derivation="COMPARATIVE_3M_FROM_LATER_MOPS_PDF")
        restated["comparative_of_period"] = "2025Q2"
        restated["supersedes_observation_id"] = "PRIOR"
        restated["revision_reason"] = "COMPARATIVE_RESTATED"
        latest = self._row("LATEST", period_end="2026-06-30", value=101790000, seq=2,
                           published="2026-08-12T17:18:12+08:00")
        result = health.evaluate_issuer("ISSUER_TEST", [prior, restated, latest], RULES,
                                        "2026-10-13T23:59:59+08:00", current_snapshot_mode=True,
                                        mops_enabled=True)
        self.assertEqual(result["indicators"]["H7"]["status"], "穩健")
        self.assertAlmostEqual(result["indicators"]["H7"]["value"], 0.174, places=3)

    def test_pdf_rows_follow_taiwan_enable_gate_and_report_scope(self):
        import health
        consolidated = self._row("PDF_C", period_end="2025-06-30", value=100, seq=1)
        individual = self._row("PDF_I", period_end="2026-06-30", value=101, seq=2)
        individual["report_scope"] = "INDIVIDUAL"
        kept, blocked = health.gate_pending_sources([consolidated, individual], False)
        self.assertEqual(kept, []); self.assertEqual(blocked, 2)
        self.assertEqual(health.gate_pending_sources([consolidated], False, True)[0], [consolidated])
        scoped = health.consistent_report_scope([consolidated, individual])
        self.assertEqual([r["observation_id"] for r in scoped], ["PDF_I"])

class ResearchSnapshotS1A(unittest.TestCase):
    def setUp(self):
        import research_snapshot as rs
        self.rs = rs

    def test_canonical_bytes_are_order_independent(self):
        self.assertEqual(self.rs.canonical_bytes({"b": 2, "a": 1}), self.rs.canonical_bytes({"a": 1, "b": 2}))

    def test_manifest_binds_code_and_data(self):
        captured = self.rs.capture_inputs()
        hashes = self.rs.input_hashes(captured)
        self.assertIn("rules", hashes); self.assertIn("code:research_snapshot.py", hashes)
        self.assertEqual(len(hashes["rules"]["sha256"]), 64)

    def test_observation_ledger_is_bound_to_exact_file(self):
        captured = self.rs.capture_inputs()
        self.assertEqual(self.rs.verify_observation_ledger(captured)["file_sha256_after"],
                         self.rs.digest(captured["observations"]))

    def test_observation_ledger_mismatch_fails_closed(self):
        captured = self.rs.capture_inputs(); captured["observations"] += b"x"
        with self.assertRaisesRegex(ValueError, "LEDGER_SYNC"):
            self.rs.verify_observation_ledger(captured)

    def test_future_price_fetch_is_excluded(self):
        registry = {"securities": [{"security_id":"S","ticker":"X","aliases":[],"market":"US","currency":"USD"}]}
        captured = {"prices": b"date,ticker,close,volume,source,fetched_at\n2026-01-01,X,1,,s,2099-01-01T00:00:00+00:00\n"}
        rows, anchors, rejected = self.rs.frozen_prices(captured, registry, [], self.rs.parse_time("2026-01-02T00:00:00+00:00"))
        self.assertEqual(rows, []); self.assertEqual(len(rejected), 1); self.assertIsNone(anchors["US"])

    def test_anchor_requires_bound_open_session_and_price(self):
        registry = {"securities": [{"security_id":"S","ticker":"X","aliases":[],"market":"US","currency":"USD"}]}
        captured = {"prices": b"date,ticker,close,volume,source,fetched_at\n2026-01-01,X,1,,s,2026-01-01T22:00:00+00:00\n"}
        sessions = [{"market":"US","session_date":"2026-01-01"}]
        rows, anchors, _ = self.rs.frozen_prices(captured, registry, sessions, self.rs.parse_time("2026-01-02T00:00:00+00:00"))
        self.assertEqual(anchors["US"], "2026-01-01"); self.assertEqual(len(rows), 1)

    def test_action_after_cutoff_is_excluded(self):
        registry = {"securities": [{"security_id":"S","ticker":"X","aliases":[],"market":"US"}]}
        captured = {"actions": "date,ticker,type,value,source,fetched_at\n2026-01-01,X,現金股利,1,s,2099-01-01T00:00:00+00:00\n".encode("utf-8")}
        self.assertEqual(self.rs.frozen_actions(captured, registry, {"US":"2026-01-02"}, self.rs.parse_time("2026-01-02T00:00:00+00:00")), [])

    def test_macro_after_cutoff_is_excluded(self):
        captured = {"macro": b"date,series_id,value,source,fetched_at\n2026-01-01,A,1,s,2099-01-01T00:00:00+00:00\n"}
        self.assertEqual(self.rs.frozen_macro(captured, self.rs.parse_time("2026-01-02T00:00:00+00:00")), [])

    def test_price_structures_first_duplicate_wins_and_dates_are_sorted(self):
        rows = [{"ticker":"X","date":"2026-01-03","close":"3","volume":"30"},
                {"ticker":"X","date":"2026-01-01","close":"1","volume":"10"},
                {"ticker":"X","date":"2026-01-03","close":"999","volume":"999"},
                {"ticker":"X","date":"2026-01-02","close":"2","volume":"20"}]
        series, volumes, stats = self.rs.price_structures(rows, return_stats=True)
        self.assertEqual(series["X"], [("2026-01-01",1.0,10.0),("2026-01-02",2.0,20.0),("2026-01-03",3.0,30.0)])
        self.assertEqual(volumes["X"]["2026-01-03"], 30.0)
        self.assertEqual(stats, {"duplicate_rows":1,"duplicate_rows_by_ticker":{"X":1}})

    def test_actions_aggregate_like_pricedata_and_macro_first_duplicate_wins(self):
        actions = [{"ticker":"X","date":"2026-01-02","type":"現金股利","value":"1"},
                   {"ticker":"X","date":"2026-01-02","type":"現金股利","value":"2"},
                   {"ticker":"X","date":"2026-01-01","type":"分割","value":"4"}]
        result, stats = self.rs.action_structures(actions, return_stats=True)
        self.assertEqual(list(result["X"]), ["2026-01-01","2026-01-02"])
        self.assertEqual(result["X"]["2026-01-02"]["div"], 3.0)
        self.assertEqual(result["X"]["2026-01-01"]["split"], 4.0)
        self.assertEqual(stats["same_key_rows"], 1)
        macro, mstats = self.rs.macro_structure([
            {"series_id":"A","date":"2026-01-02","value":"2"},
            {"series_id":"A","date":"2026-01-01","value":"1"},
            {"series_id":"A","date":"2026-01-02","value":"9"}], return_stats=True)
        self.assertEqual(macro["A"], [("2026-01-01",1.0),("2026-01-02",2.0)])
        self.assertEqual(mstats["duplicate_rows"], 1)

    def _unordered_price_fixture(self):
        from datetime import date, timedelta
        rows=[]; sessions=[]; start=date(2025,1,1)
        for i in range(220):
            d=(start+timedelta(days=i)).isoformat()
            sessions.append({"session_id":f"TW_{i}","market":"TW","session_date":d,
                             "open_at":d+"T01:00:00+00:00","close_at":d+"T05:30:00+00:00","session_status":"OPEN"})
            rows += [{"ticker":"3081","date":d,"close":str(50+2*i+(i%7)),"volume":"100"},
                     {"ticker":"0050","date":d,"close":str(100+i+(i%5)),"volume":"100"}]
        unordered=list(reversed(rows)); unordered.append(dict(rows[-2],close="99999"))
        return unordered,sessions,sessions[-1]["session_date"]

    def test_unordered_prices_drive_attribution_and_outcomes_to_anchor(self):
        import attribution
        rows,sessions,anchor=self._unordered_price_fixture()
        series,_,stats=self.rs.price_structures(rows,return_stats=True)
        self.assertEqual(series["3081"][-1][0],anchor); self.assertEqual(stats["duplicate_rows"],1)
        ctx=attribution.build_context(RULES,as_of=anchor,prices=series,actions={})
        scanned=attribution.scan_latest(RULES,ctx,tickers=["3081"],force=True)
        self.assertTrue(any(r.get("status")=="OK" for r in scanned))
        self.assertTrue(all(r.get("end_date")==anchor for r in scanned if r.get("status")=="OK"))
        snap={"run_id":"R","snapshot_sha256":"s","calendar_version_id":"C","anchors":{"TW":anchor},
              "companies":[{"security_id":"S3081","benchmark_security_id":"S0050","ticker":"3081",
                            "benchmark_ticker":"0050","market":"TW","price_status":"OK"}]}
        outcomes=self.rs.compute_outcome_attempts(snap,"2024-12-01T00:00:00+00:00",sessions,series,{})
        self.assertEqual([r["horizon_sessions"] for r in outcomes],[20,60])
        expected=series["3081"][20][1]/series["3081"][0][1]-1
        self.assertAlmostEqual(outcomes[0]["security_total_return"],expected)

    def test_price_not_at_anchor_suppresses_price_outputs_and_outcomes(self):
        import csv, io
        captured=self.rs.capture_inputs(); cut=self.rs.parse_time("2026-10-13T04:00:00+00:00")
        original=self.rs.build_snapshot(captured,cut); anchor=original["anchors"]["TW"]
        rows=self.rs.csv_rows(captured["prices"]); kept=[r for r in rows if not (r.get("ticker")=="3081" and r.get("date")==anchor)]
        buf=io.StringIO(newline=""); writer=csv.DictWriter(buf,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(kept)
        altered=dict(captured); altered["prices"]=buf.getvalue().encode("utf-8")
        snapshot=self.rs.build_snapshot(altered,cut); company=next(c for c in snapshot["companies"] if c["ticker"]=="3081")
        self.assertEqual(company["price_status"],"PRICE_NOT_AT_ANCHOR")
        self.assertEqual(company["risk"]["status"],"PRICE_NOT_AT_ANCHOR")
        self.assertEqual(company["attribution"][0]["status"],"PRICE_NOT_AT_ANCHOR")
        self.assertIn("PRICE_NOT_AT_ANCHOR",self.rs.research_page(snapshot))
        snapshot["run_id"]="R"; snapshot["snapshot_sha256"]="S"
        series,_,_=self.rs.price_structures(self.rs.frozen_prices(altered,self.rs.json.loads(altered["registry"]),
                                      self.rs.bound_sessions(altered,cut)[0],cut)[0],return_stats=True)
        self.assertEqual(self.rs.compute_outcome_attempts(snapshot,"2025-01-01T00:00:00+00:00",[],series,{}),[])

    def test_security_scope_excludes_etf_and_disabled(self):
        captured = self.rs.capture_inputs(); reg = self.rs.json.loads(captured["registry"])
        scope = [s for s in reg["securities"] if s.get("price_series_enabled") and s.get("security_type") != "ETF"]
        self.assertNotIn("0050", [s["ticker"] for s in scope]); self.assertNotIn("2330", [s["ticker"] for s in scope])

    def test_research_page_rejects_formal_verdict_words(self):
        fake = {"data_cutoff_at":"x","anchors":{},"limitations":[],"theme_breadth":{},"market":{"assessment":{"status":"等待"}},"companies":[]}
        with self.assertRaisesRegex(ValueError, "FORBIDDEN_TEXT"):
            self.rs.research_page(fake)

    def test_research_page_has_no_forbidden_words(self):
        fake = {"data_cutoff_at":"x","anchors":{},"limitations":[],"theme_breadth":{},"market":{"assessment":{"status":"中性"}},"companies":[]}
        text = self.rs.research_page(fake)
        self.assertFalse(any(x in text for x in self.rs.FORBIDDEN_RESEARCH_TEXT))

    def test_research_page_is_compact_tabular_and_uses_snapshot_status(self):
        fake = {"data_cutoff_at":"x","anchors":{"TW":None,"US":None},"limitations":[],"theme_breadth":{},
                "market":{"values":{"M1":{"reason":"資料過期"}},"assessment":{"status":"中性","score":0,"filled":1,"scores":{"M1":0}}},
                "companies":[]}
        text = self.rs.research_page(fake)
        self.assertIn("STATUS：`SOURCE_STALE`", text)
        self.assertIn("| 指標 | 值 | 分數 | 資料日期 | 說明／proxy |", text)
        self.assertIn("| 代號 | 市場 | 價格 session | C1 | C2 | C3 |", text)
        self.assertLessEqual(len(text), 30000)
        self.assertNotIn('"market"', text)

    def test_dry_run_double_read_detects_change(self):
        import unittest.mock as mock
        base = self.rs.capture_inputs()
        altered = dict(base); altered["events"] = base["events"] + b"x"
        with mock.patch.object(self.rs, "capture_inputs", side_effect=[base, altered]), \
             mock.patch.object(self.rs, "build_snapshot", return_value={"anchors":{},"session_binding":{},"gates":{},"companies":[],"market":{"assessment":{"status":"x"}}}), \
             mock.patch.object(self.rs, "research_page", return_value="x"):
            with self.assertRaisesRegex(RuntimeError, "DOUBLE_READ"):
                self.rs.dry_run(now_fn=lambda: "2026-01-01T00:00:00+00:00")

    def test_formal_transaction_helpers_use_exclusive_create(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x")
            self.rs._atomic_write(p, b"a")
            with open(p, "rb") as stream:
                self.assertEqual(stream.read(), b"a")

    def test_formal_readiness_limitations_are_explicit(self):
        captured = self.rs.capture_inputs()
        snapshot = self.rs.build_snapshot(captured, self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        self.assertNotIn("C4_CROSS_COMPANY_COMPARABILITY_NOT_IMPLEMENTED", snapshot["limitations"])
        self.assertNotIn("D9_MRVL_DOUBLE_COUNT_FIX_NOT_IMPLEMENTED", snapshot["limitations"])
        self.assertTrue(all(x["formal_readiness"]["D6_C4_RULE_DECISION_IMPLEMENTED"] for x in snapshot["companies"]))
        mrvl = next(x for x in snapshot["companies"] if x["ticker"] == "MRVL")
        self.assertTrue(mrvl["formal_readiness"]["D9_MRVL_RULE_DECISION_IMPLEMENTED"])
        self.assertIn("D11_TW_ACTION_COVERAGE_UNVERIFIED", snapshot["limitations"])

    def test_full_dry_run_reports_zero_writes(self):
        result = self.rs.dry_run(now_fn=lambda: "2026-10-13T04:00:00+00:00")
        self.assertEqual(result["write_count"], 0); self.assertGreater(result["company_count"], 0)

    def test_exact_trend_and_health_provenance_present(self):
        c = self.rs.capture_inputs(); s = self.rs.build_snapshot(c, self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        self.assertTrue(any(x for co in s["companies"] for ids in co["trend_provenance"].values() for x in ids))
        self.assertTrue(any(x for co in s["companies"] for ids in co["health_provenance"].values() for x in ids))

    def test_manifest_records_prefix_and_cas(self):
        c = self.rs.capture_inputs(); cut = self.rs.parse_time("2026-10-13T04:00:00+00:00")
        m = self.rs.build_input_manifest(c, cut, self.rs.build_snapshot(c, cut))
        self.assertEqual(m["inputs"]["prices"]["storage"], "PREFIX_AND_CAS")
        self.assertEqual(m["inputs"]["rules"]["storage"], "CAS")
        self.assertEqual(m["inputs"]["prices"]["prefix_sha256"], self.rs.digest(c["prices"]))
        self.assertEqual(m["normalization"]["duplicate_price_rows"], m["gates"]["duplicate_price_rows"])

    def test_restore_uses_bound_prefix_after_append(self):
        import tempfile
        c = {"prices": b"a\n1\n"}; meta = {"inputs":{"prices":{"sha256":self.rs.digest(c["prices"]),"byte_length":4,
                "cas_sha256":self.rs.digest(c["prices"]),"prefix_byte_length":4,"prefix_sha256":self.rs.digest(c["prices"])}}}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.rs.restore_inputs(meta, d, {"prices":c["prices"] + b"2\n"})["prices"], c["prices"])

    def test_restore_uses_cas_and_rejects_tamper(self):
        import tempfile
        data=b"abc"; sha=self.rs.digest(data); meta={"inputs":{"x":{"sha256":sha,"byte_length":3,"cas_sha256":sha}}}
        with tempfile.TemporaryDirectory() as d:
            self.rs.Path(d,sha).write_bytes(data); self.assertEqual(self.rs.restore_inputs(meta,d)["x"],data)
            self.rs.Path(d,sha).write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError,"REPLAY_INPUT_HASH"): self.rs.restore_inputs(meta,d)

    def test_replay_snapshot_is_byte_identical(self):
        import tempfile
        c=self.rs.capture_inputs(); cut=self.rs.parse_time("2026-10-13T04:00:00+00:00"); s=self.rs.build_snapshot(c,cut)
        m=self.rs.build_input_manifest(c,cut,s)
        with tempfile.TemporaryDirectory() as d:
            for data in c.values(): self.rs.Path(d,self.rs.digest(data)).write_bytes(data)
            rebuilt=self.rs.replay_snapshot(m,d)
        self.assertEqual(self.rs.canonical_bytes(s),self.rs.canonical_bytes(rebuilt))

    def test_replay_environment_mismatch_fails(self):
        m={"environment":{"python":"different"}}
        with self.assertRaisesRegex(RuntimeError,"ENVIRONMENT_COMPATIBILITY"): self.rs.replay_snapshot(m,".")

    def test_replay_python_patch_and_tzdata_differences_warn_but_pass(self):
        from unittest import mock
        major, minor = sys.version_info[:2]
        m={"environment":{"python":f"{major}.{minor}.0 test-build","tzdata":"definitely-different"},
           "data_cutoff_at":"2026-01-01T00:00:00+00:00","inputs":{}}
        with mock.patch.object(self.rs,"restore_inputs",return_value={}), \
             mock.patch.object(self.rs,"build_snapshot",return_value={}):
            got=self.rs.replay_snapshot(m,".")
        self.assertIn("REPLAY_PYTHON_PATCH_DIFFERS",got["replay_warnings"])
        self.assertIn("REPLAY_TZDATA_DIFFERS",got["replay_warnings"])

    def test_replay_python_minor_difference_is_blocked(self):
        from unittest import mock
        major, minor = sys.version_info[:2]
        m={"environment":{"python":f"{major}.{minor + 1}.0 test-build","tzdata":"UNKNOWN"},
           "data_cutoff_at":"2026-01-01T00:00:00+00:00","inputs":{}}
        with mock.patch.object(self.rs,"restore_inputs",return_value={}), \
             mock.patch.object(self.rs,"build_snapshot",return_value={}):
            with self.assertRaisesRegex(RuntimeError,"ENVIRONMENT_COMPATIBILITY"):
                self.rs.replay_snapshot(m,".")

    def test_run_id_has_no_hash_cycle(self):
        cut=self.rs.parse_time("2026-01-01T00:00:00+00:00"); b=self.rs.canonical_bytes({"x":1})
        self.assertEqual(self.rs.make_run_id(cut,b),self.rs.make_run_id(cut,b)); self.assertNotIn("run_id", {"x":1})

    def test_committed_reader_rejects_payload_tamper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            payload=b"{}\n"; self.rs.Path(d,"snapshot.json").write_bytes(payload)
            self.rs.Path(d,"input_manifest.json").write_bytes(payload)
            man={"payloads":{"snapshot.json":{"sha256":self.rs.digest(payload)},"input_manifest.json":{"sha256":self.rs.digest(payload)}}}
            mb=self.rs.canonical_bytes(man); self.rs.Path(d,"manifest.json").write_bytes(mb)
            marker={"manifest_sha256":self.rs.digest(mb)}; self.rs.Path(d,"COMMITTED.json").write_bytes(self.rs.canonical_bytes(marker))
            self.rs.Path(d,"snapshot.json").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError,"PAYLOAD_HASH"): self.rs.read_committed(d)

    def test_difference_derivation_new_and_unknown(self):
        oldm={"inputs":{"rules":{"sha256":"a"}}}; newm={"inputs":{"rules":{"sha256":"b"}}}
        old={"anchors":{"US":"1"},"companies":[]}; same={"anchors":{"US":"1"},"companies":[]}
        self.assertEqual(self.rs.classify_difference(oldm,newm,old,same),"DERIVATION_CHANGE")
        self.assertEqual(self.rs.classify_difference(oldm,oldm,old,{"anchors":{"US":"2"},"companies":[]}),"NEW_DATA")
        self.assertEqual(self.rs.classify_difference(oldm,oldm,old,same),"UNKNOWN")

    def _outcome_fixture(self, market="US"):
        from datetime import datetime, timedelta, timezone
        base=datetime(2026,1,1,14,30,tzinfo=timezone.utc); sessions=[]; series={"X":[],"B":[]}
        for i in range(70):
            d=(base+timedelta(days=i)).date().isoformat(); op=(base+timedelta(days=i)).isoformat()
            sessions.append({"session_id":f"{market}_{i}","market":market,"session_date":d,"open_at":op,"close_at":op,"session_status":"OPEN"})
            series["X"].append((d,100+i,0)); series["B"].append((d,100,0))
        snap={"run_id":"R","snapshot_sha256":"s","calendar_version_id":"C","anchors":{market:sessions[-1]["session_date"]},
              "companies":[{"security_id":"SX","benchmark_security_id":"SB","ticker":"X","benchmark_ticker":"B","market":market}]}
        return snap,sessions,series

    def test_outcome_start_is_strictly_after_publication(self):
        snap,ss,series=self._outcome_fixture(); rows=self.rs.compute_outcome_attempts(snap,ss[0]["open_at"],ss,series,{})
        self.assertEqual(rows[0]["start_session_id"],"US_1")

    def test_outcome_horizon_off_by_one(self):
        snap,ss,series=self._outcome_fixture(); rows=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{})
        self.assertEqual([r["end_session_id"] for r in rows],["US_20","US_60"])

    def test_outcome_missing_benchmark_is_unavailable(self):
        snap,ss,series=self._outcome_fixture(); series.pop("B")
        rows=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{})
        self.assertTrue(all(r["status"]=="OUTCOME_UNAVAILABLE" for r in rows))

    def test_outcome_idempotent_and_revision_supersedes(self):
        snap,ss,series=self._outcome_fixture(); first=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{})
        self.assertEqual(self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{},first),[])
        series["X"][20]=(series["X"][20][0],999,0); series["X"][60]=(series["X"][60][0],999,0)
        later=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{},first)
        self.assertTrue(all(r["supersedes_outcome_id"] for r in later))

    def test_tw_outcome_is_excluded_from_statistics(self):
        snap,ss,series=self._outcome_fixture("TW"); rows=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{})
        self.assertFalse(rows[0]["statistical_eligibility"]); self.assertIn("TOTAL_RETURN_ACTION_COVERAGE_UNVERIFIED",rows[0]["limitations"])

    def test_outcome_code_does_not_import_verdict(self):
        import inspect
        self.assertNotIn("verdict",inspect.getsource(self.rs.compute_outcome_attempts))

    def test_snapshot_has_no_portfolio_payload(self):
        c=self.rs.capture_inputs(); s=self.rs.build_snapshot(c,self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        self.assertNotIn("portfolio",self.rs.json.dumps(s,ensure_ascii=False).lower())

    def test_missing_values_remain_null(self):
        c=self.rs.capture_inputs(); s=self.rs.build_snapshot(c,self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        missing=[v for co in s["companies"] for v in co["formal_readiness"].values() if v is None]
        self.assertTrue(missing); self.assertTrue(all(v is None for v in missing))

    def test_page_has_no_trade_target_or_allocation_instruction(self):
        c=self.rs.capture_inputs(); cut=self.rs.parse_time("2026-10-13T04:00:00+00:00"); page=self.rs.research_page(self.rs.build_snapshot(c,cut))
        for word in ("買進","賣出","目標價","配置比例"): self.assertNotIn(word,page)

    def test_cutoff_inclusive_for_macro_fetch(self):
        stamp="2026-01-02T00:00:00+00:00"
        captured={"macro":("date,series_id,value,source,fetched_at\n2026-01-01,A,1,s,"+stamp+"\n").encode()}
        self.assertEqual(len(self.rs.frozen_macro(captured,self.rs.parse_time(stamp))),1)

    def test_session_close_equal_cutoff_is_eligible(self):
        c=self.rs.capture_inputs(); sessions,_=self.rs.bound_sessions(c,self.rs.parse_time("2026-10-02T20:00:00+00:00"))
        self.assertTrue(any(s["market"]=="US" and s["session_date"]=="2026-10-02" for s in sessions))

    def test_bound_sessions_match_data_spine_sort_order(self):
        c=self.rs.capture_inputs(); sessions,_=self.rs.bound_sessions(c,self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        self.assertEqual([(s.get("market",""),s.get("open_at","")) for s in sessions],
                         sorted((s.get("market",""),s.get("open_at","")) for s in sessions))

    def test_canonical_observation_ids_are_unique_before_order_independent_selection(self):
        c=self.rs.capture_inputs(); rows=self.rs.frozen_observations(c)
        ids=[r.get("observation_id") for r in rows]
        self.assertEqual(len(ids),len(set(ids)))

    def test_tsm_and_2330_identity_remain_separate(self):
        reg=self.rs.json.loads(self.rs.capture_inputs()["registry"]); by={x["ticker"]:x for x in reg["securities"]}
        self.assertNotEqual(by["TSM"]["security_id"],by["2330"]["security_id"])
        self.assertNotEqual(by["TSM"]["currency"],by["2330"]["currency"])

    def test_s1a_never_calls_validate_or_judge(self):
        import inspect
        source=inspect.getsource(self.rs.build_snapshot)
        self.assertNotIn("validate(",source); self.assertNotIn("judge(",source)

    def test_incomplete_directory_is_quarantined(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p=self.rs.Path(d,"SNAP_X"); p.mkdir(); (p/"x").write_text("x")
            moved=self.rs._recover_staging(d)
            self.assertEqual(moved,["SNAP_X"]); self.assertTrue(self.rs.Path(d,"_中斷","SNAP_X").exists())

    def test_malformed_json_fails_closed(self):
        with self.assertRaises(self.rs.json.JSONDecodeError): self.rs.jsonl_rows(b"{bad}\n")

    def test_any_input_byte_change_changes_manifest_hash(self):
        c=self.rs.capture_inputs(); cut=self.rs.parse_time("2026-10-13T04:00:00+00:00"); s=self.rs.build_snapshot(c,cut)
        a=self.rs.digest(self.rs.canonical_bytes(self.rs.build_input_manifest(c,cut,s)))
        changed=dict(c); changed["events"]=c["events"]+b"\n"
        b=self.rs.digest(self.rs.canonical_bytes(self.rs.build_input_manifest(changed,cut,s)))
        self.assertNotEqual(a,b)

    def test_correction_difference_is_identified(self):
        oldm={"inputs":{}}; old={"anchors":{},"companies":[{"trend_provenance":{"C1":["OLD"]}}]}
        new={"anchors":{},"companies":[{"trend_provenance":{"C1":["NEW"]}}]}
        obs=[{"observation_id":"NEW","supersedes_observation_id":"OLD"}]
        self.assertEqual(self.rs.classify_difference(oldm,oldm,old,new,obs),"CORRECTION")

    def test_snapshot_status_distinguishes_stale_source(self):
        binding={"TW":{"session_id":"TW_X"},"US":{"session_id":"US_X"}}
        anchors={"TW":"2026-10-13","US":"2026-10-12"}
        fresh={"market":{"values":{}},"companies":[],"anchors":anchors,"session_binding":binding}
        stale={"market":{"values":{"M1":{"reason":"資料過期"}}},"companies":[],"anchors":anchors,"session_binding":binding}
        self.assertEqual(self.rs.snapshot_status(fresh),"OK")
        self.assertEqual(self.rs.snapshot_status(stale),"SOURCE_STALE")

    def test_issuer_stale_does_not_make_snapshot_source_stale(self):
        snapshot={"market":{"values":{}},"anchors":{"TW":"2026-10-13","US":"2026-10-12"},
                  "session_binding":{"TW":{"session_id":"TW_X"},"US":{"session_id":"US_X"}},
                  "companies":[{"ticker":"X","issuer_data_status":"ISSUER_DATA_STALE",
                                "issuer_data_stale_reasons":["health:H2"],"risk":{"stale":True}}]}
        self.assertEqual(self.rs.snapshot_status(snapshot),"OK")

    def test_real_snapshot_marks_issuer_stale_and_page_lists_it(self):
        captured=self.rs.capture_inputs(); snapshot=self.rs.build_snapshot(captured,self.rs.parse_time("2026-10-13T04:00:00+00:00"))
        stale=[c for c in snapshot["companies"] if c.get("issuer_data_status")=="ISSUER_DATA_STALE"]
        self.assertTrue(stale)
        self.assertEqual(self.rs.snapshot_status(snapshot),"OK")
        page=self.rs.research_page(snapshot)
        self.assertIn("ISSUER_DATA_STALE",page)

    def test_outcome_can_mature_after_signal_anchor(self):
        snap,ss,series=self._outcome_fixture(); snap["anchors"]["US"]=ss[0]["session_date"]
        rows=self.rs.compute_outcome_attempts(snap,"2025-12-31T00:00:00+00:00",ss,series,{})
        self.assertEqual([r["horizon_sessions"] for r in rows],[20,60])

    def _formal_fixture(self):
        from datetime import datetime, timedelta, timezone
        sessions=[]; prices=[]; base=datetime(2026,1,2,14,30,tzinfo=timezone.utc)
        for i in range(70):
            d=(base+timedelta(days=i)).date().isoformat(); op=(base+timedelta(days=i)).isoformat()
            sessions.append({"session_id":f"US_{i}","market":"US","session_date":d,"open_at":op,"close_at":op,"session_status":"OPEN"})
            for ticker, sid, close in (("X","SX",100+i),("B","SB",100)):
                raw={"date":d,"ticker":ticker,"close":str(close),"volume":"1","fetched_at":"2026-01-01T00:00:00+00:00"}
                prices.append(dict(raw,security_id=sid,row_sha256=self.rs.digest(self.rs.canonical_bytes(raw))))
        company={"security_id":"SX","issuer_id":"IX","ticker":"X","market":"US","currency":"USD",
                 "benchmark_security_id":"SB","benchmark_ticker":"B","anchor_session":sessions[0]["session_date"],
                 "trend":{},"trend_provenance":{},"health":{},"health_provenance":{},"attribution":[],"risk":{},
                 "formal_readiness":{"V1":False}}
        def snap(anchor):
            return {"schema_version":"S","derivation_version":"D","data_cutoff_at":"2026-01-01T00:00:00+00:00",
                    "anchors":{"TW":None,"US":anchor},"calendar_version_id":"C","session_binding":{},
                    "market":{"values":{},"assessment":{"status":"綠","score":0,"filled":0,"scores":{}}},
                    "theme_breadth":{},"companies":[dict(company,anchor_session=anchor)],"gates":{},"limitations":[]}
        captured={"rules":b"{}","registry":self.rs.canonical_bytes({"securities":[],"issuers":[]})}
        return sessions,prices,snap,captured

    def test_formal_run_twice_commits_cas_outcomes_diff_and_unlocks(self):
        import tempfile, unittest.mock as mock
        sessions,prices,snap,captured=self._formal_fixture()
        times=iter(["2026-01-01T00:00:00+00:00","2026-01-01T00:01:00+00:00",
                    "2026-04-01T00:00:00+00:00","2026-04-01T00:01:00+00:00"])
        with tempfile.TemporaryDirectory() as d:
            old=(self.rs.project_lock.LOCK,self.rs.project_lock.HIST)
            self.rs.project_lock.LOCK=os.path.join(d,"project.lock"); self.rs.project_lock.HIST=os.path.join(d,"lock_history")
            try:
                with mock.patch.object(self.rs,"capture_inputs",return_value=captured), \
                     mock.patch.object(self.rs,"build_snapshot",side_effect=[snap(sessions[0]["session_date"]),snap(sessions[-1]["session_date"])]), \
                     mock.patch.object(self.rs,"frozen_observations",return_value=[]), \
                     mock.patch.object(self.rs,"bound_sessions",return_value=(sessions,{"calendar_version_id":"C"})), \
                     mock.patch.object(self.rs,"frozen_prices",side_effect=[(prices[:2],{"US":sessions[0]["session_date"],"TW":None},[]),(prices,{"US":sessions[-1]["session_date"],"TW":None},[])]), \
                     mock.patch.object(self.rs,"frozen_actions",return_value=[]):
                    first=self.rs.formal_run(self.rs.Path(d),lambda:next(times)); second=self.rs.formal_run(self.rs.Path(d),lambda:next(times))
                base=self.rs.Path(d,"02_執行紀錄","影子快照")
                self.assertTrue(self.rs.Path(first["directory"],"COMMITTED.json").exists()); self.assertTrue((base/"_cas").is_dir())
                outcomes=self.rs.jsonl_rows(self.rs.Path(d,"04_回測","影子快照_outcomes.jsonl").read_bytes())
                self.assertEqual([r["horizon_sessions"] for r in outcomes],[20,60])
                self.assertEqual(self.rs.json.loads(self.rs.Path(second["directory"],"snapshot.json").read_bytes())["difference_reason"],"NEW_DATA")
                self.assertIsNone(self.rs.project_lock.read())
            finally: self.rs.project_lock.LOCK,self.rs.project_lock.HIST=old

    def test_formal_failure_leaves_failure_and_next_run_quarantines(self):
        import tempfile, unittest.mock as mock
        sessions,prices,snap,captured=self._formal_fixture(); real_atomic=self.rs._atomic_write; failed={"done":False}
        def break_once(path,data):
            if self.rs.Path(path).name=="snapshot.json" and not failed["done"]:
                failed["done"]=True; raise OSError("injected")
            return real_atomic(path,data)
        with tempfile.TemporaryDirectory() as d:
            old=(self.rs.project_lock.LOCK,self.rs.project_lock.HIST)
            self.rs.project_lock.LOCK=os.path.join(d,"project.lock"); self.rs.project_lock.HIST=os.path.join(d,"lock_history")
            common=[mock.patch.object(self.rs,"capture_inputs",return_value=captured),mock.patch.object(self.rs,"build_snapshot",return_value=snap(sessions[-1]["session_date"])),
                    mock.patch.object(self.rs,"frozen_observations",return_value=[]),mock.patch.object(self.rs,"bound_sessions",return_value=(sessions,{"calendar_version_id":"C"})),
                    mock.patch.object(self.rs,"frozen_prices",return_value=(prices,{"US":sessions[-1]["session_date"],"TW":None},[])),mock.patch.object(self.rs,"frozen_actions",return_value=[])]
            try:
                with common[0],common[1],common[2],common[3],common[4],common[5],mock.patch.object(self.rs,"_atomic_write",side_effect=break_once):
                    with self.assertRaisesRegex(OSError,"injected"): self.rs.formal_run(self.rs.Path(d),lambda:"2026-04-01T00:00:00+00:00")
                staging=list(self.rs.Path(d,"02_執行紀錄","影子快照").glob("*.staging")); self.assertTrue((staging[0]/"failure.json").exists())
                common=[mock.patch.object(self.rs,"capture_inputs",return_value=captured),mock.patch.object(self.rs,"build_snapshot",return_value=snap(sessions[-1]["session_date"])),
                        mock.patch.object(self.rs,"frozen_observations",return_value=[]),mock.patch.object(self.rs,"bound_sessions",return_value=(sessions,{"calendar_version_id":"C"})),
                        mock.patch.object(self.rs,"frozen_prices",return_value=(prices,{"US":sessions[-1]["session_date"],"TW":None},[])),mock.patch.object(self.rs,"frozen_actions",return_value=[])]
                with common[0],common[1],common[2],common[3],common[4],common[5]: self.rs.formal_run(self.rs.Path(d),lambda:"2026-04-02T00:00:00+00:00")
                self.assertTrue(any(self.rs.Path(d,"02_執行紀錄","影子快照","_中斷").iterdir())); self.assertIsNone(self.rs.project_lock.read())
            finally: self.rs.project_lock.LOCK,self.rs.project_lock.HIST=old


class ShadowPreviewS1B(unittest.TestCase):
    """S2-5：影子分類預覽。以真實輸入的暫存副本跑一次正式流程（暫存鎖、暫存根目錄，不碰專案 02）。"""
    @classmethod
    def setUpClass(cls):
        import tempfile, shutil
        import research_snapshot as rs
        cls.rs = rs; cls.tmp = tempfile.mkdtemp(); root = rs.Path(cls.tmp)
        for rel in rs.INPUT_PATHS.values():
            src = rs.ROOT / rel
            if src.exists():
                (root / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, root / rel)
        (root / "03_工具").mkdir(parents=True, exist_ok=True)
        for name in rs.CODE_PATHS: shutil.copyfile(rs.ROOT / "03_工具" / name, root / "03_工具" / name)
        old = (rs.project_lock.LOCK, rs.project_lock.HIST)
        rs.project_lock.LOCK = os.path.join(cls.tmp, "project.lock"); rs.project_lock.HIST = os.path.join(cls.tmp, "lock_history")
        try:
            # Use a cutoff after the S3c valuation inputs became available so
            # this temporary formal commit exercises PUBLIC_DATA_COMPUTED V1.
            cls.result = rs.formal_run(root, lambda: "2026-10-13T16:18:37+00:00")
            cls.lock_after = rs.project_lock.read()
        finally:
            rs.project_lock.LOCK, rs.project_lock.HIST = old
        cls.root = root; cls.run_dir = rs.Path(cls.result["directory"]); cls.base = cls.run_dir.parent

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def payload(self, name):
        return (self.run_dir / name).read_bytes()

    def shadow(self, market):
        return json.loads(self.payload(f"shadow_verdict_{market}.json"))

    def test_two_phase_commit_binds_shadow_files_to_committed_snapshot(self):
        self.assertEqual(self.result["shadow_status"], "SHADOW_COMMITTED")
        self.assertIsNone(self.lock_after)
        self.rs.read_committed(self.run_dir)
        marker = self.rs.read_shadow_committed(self.run_dir)
        committed = json.loads(self.payload("COMMITTED.json"))
        self.assertEqual(marker["snapshot_sha256"], committed["snapshot_sha256"])
        self.assertEqual(set(marker["payloads"]), set(self.rs.SHADOW_PAYLOADS))
        # 第二階段檔案不在第一階段 manifest 內，第一階段雜湊不變。
        manifest = json.loads(self.payload("manifest.json"))
        self.assertFalse(set(manifest["payloads"]) & set(self.rs.SHADOW_PAYLOADS))
        self.assertFalse((self.run_dir / self.rs.SHADOW_FAILURE).exists())

    def test_rerun_from_same_snapshot_is_byte_identical(self):
        again = self.rs.build_shadow_preview(self.run_dir, self.base)
        for name, data in again.items():
            self.assertEqual(data, self.payload(name), name)

    def test_shadow_reads_only_frozen_inputs(self):
        import unittest.mock as mock, data_spine, verdict, computed_provenance
        def live(*a, **k): raise AssertionError("LIVE_READ")
        computed_provenance._REPLAY_CACHE.clear()
        patches = [mock.patch.object(data_spine, name, side_effect=live) for name in
                   ("load_registry", "read_jsonl", "read_observations_checked", "load_json",
                    "verify_calendar_binding", "load_bound_sessions", "load_portfolio")]
        patches.append(mock.patch.object(verdict, "load_portfolio", side_effect=live))
        for p in patches: p.start()
        try:
            again = self.rs.build_shadow_preview(self.run_dir, self.base)
        finally:
            for p in patches: p.stop()
        self.assertEqual(again[self.rs.SHADOW_PAGE], self.payload(self.rs.SHADOW_PAGE))

    def test_shadow_never_writes_formal_run_path(self):
        formal = self.root / "02_執行紀錄"
        self.assertEqual(list(formal.glob("*.json")), [])
        for name in self.rs.SHADOW_PAYLOADS:
            self.assertTrue((self.run_dir / name).exists())
        with self.assertRaisesRegex(RuntimeError, "SHADOW_TARGET_GATE"):
            self.rs._assert_shadow_target(formal / "2026-10-13_shadow.json", formal)
        with self.assertRaisesRegex(RuntimeError, "SHADOW_TARGET_GATE"):
            self.rs._assert_shadow_target(self.run_dir / "sub" / "x.json", self.run_dir)

    def test_research_page_keeps_forbidden_word_gate_and_links_preview(self):
        page = self.payload("research.md").decode("utf-8")
        self.assertFalse(any(x in page for x in self.rs.FORBIDDEN_RESEARCH_TEXT))
        self.assertIn(f"]({self.rs.SHADOW_PAGE})", page)
        preview = self.payload(self.rs.SHADOW_PAGE).decode("utf-8").splitlines()
        self.assertEqual(preview[0], "# S1B 影子分類預覽")
        self.assertIn(self.rs.SHADOW_DISCLAIMER, preview[2])
        for word in ("買進", "賣出", "目標價", "配置比例"): self.assertNotIn(word, "\n".join(preview))

    def test_shadow_runs_are_single_market_with_shared_batch_and_expanded_computed_contract(self):
        tw, us = self.shadow("TW"), self.shadow("US")
        self.assertEqual(tw["run_batch_id"], us["run_batch_id"])
        reg = json.loads((self.root / self.rs.INPUT_PATHS["registry"]).read_bytes())
        market_of = {s["ticker"]: s["market"] for s in reg["securities"]}
        for m, data in (("TW", tw), ("US", us)):
            run = data["shadow_run"]
            self.assertTrue(run["analysis_session_id"].startswith(m + "_"))
            self.assertEqual({market_of[c["ticker"]] for c in run["companies"]}, {m})
            self.assertFalse(any("MIXED_MARKET_RUN" in e for e in data["validation"]["errors"]))
            self.assertTrue(all((f["value"] is None) or f["status"] == "PUBLIC_DATA_COMPUTED"
                                for f in run["market"].values()))
            self.assertTrue(all((f["value"] is None) or f["status"] in {"OFFICIAL_FILING_COMPUTED", "已開原文"}
                                for t in run["themes"].values() for f in t.values()))
            for c in run["companies"]:
                computed = [k for k, f in c["fields"].items() if f["status"] == "OFFICIAL_FILING_COMPUTED"]
                self.assertTrue(set(computed) <= {"C1", "C2", "C3"})
                opened = [k for k, f in c["fields"].items() if f["status"] == "已開原文"]
                self.assertTrue(set(opened) <= {"C5", "C6"})
                self.assertIn(c["maturity_tier"], (1, 2, 3))
            self.assertFalse(any("DERIVED_SOURCE_MISMATCH" in e for e in data["validation"]["errors"]))
            self.assertTrue(data["not_a_conclusion"])

    def test_tw_statutory_window_is_canonical_in_1120_and_still_visible_in_preview(self):
        tw=self.shadow("TW")
        self.assertTrue(all(row.get("tw_statutory_event",{}).get("evidence_method")=="STATUTORY_CALENDAR"
                            for row in tw["rows"]))
        self.assertTrue(all(row.get("tw_statutory_shadow_verdict") for row in tw["rows"]))
        self.assertTrue(all(not any(g["missing"].startswith("事件距離：")
                                    for g in (row.get("tw_statutory_remaining_gaps") or []))
                            for row in tw["rows"]))
        page=self.payload(self.rs.SHADOW_PAGE).decode("utf-8")
        self.assertIn("## 台股事件＝法定期限窗（影子並列）",page)
        self.assertFalse(any(any("EVENT_DERIVATION_FAILED:EVENT_COVERAGE_INCOMPLETE" in e
                                 for e in row.get("validation_errors",[])) for row in tw["judge"]["companies"]))
        # Canonical and shadow results remain separately visible.
        self.assertTrue(all("shadow_verdict" in row and "tw_statutory_shadow_verdict" in row for row in tw["rows"]))

    def test_rules_190_frozen_branch_still_populates_only_c1(self):
        restored = self.rs.restore_inputs(json.loads(self.payload("input_manifest.json")), self.base / "_cas")
        inputs = self.rs.frozen_schema2_inputs(restored, self.base)
        inputs["rules"] = load_rules("1.9.0")
        snapshot = json.loads(self.payload("snapshot.json")); committed = json.loads(self.payload("COMMITTED.json"))
        run1 = self.rs.build_shadow_run("US", snapshot, committed["run_id"], committed["snapshot_sha256"], inputs)
        run2 = self.rs.build_shadow_run("US", snapshot, committed["run_id"], committed["snapshot_sha256"], inputs)
        self.assertEqual(self.rs.canonical_bytes(run1), self.rs.canonical_bytes(run2))
        self.assertTrue(all(f["value"] is None for f in run1["market"].values()))
        self.assertTrue(all(f["value"] is None for t in run1["themes"].values() for f in t.values()))
        for company in run1["companies"]:
            computed = [k for k, f in company["fields"].items() if f["status"] == "OFFICIAL_FILING_COMPUTED"]
            self.assertIn(computed, ([], ["C1"]))

    def test_rules_191_frozen_branch_does_not_backport_v1(self):
        restored = self.rs.restore_inputs(json.loads(self.payload("input_manifest.json")), self.base / "_cas")
        inputs = self.rs.frozen_schema2_inputs(restored, self.base)
        inputs["rules"] = load_rules("1.9.1")
        snapshot = json.loads(self.payload("snapshot.json")); committed = json.loads(self.payload("COMMITTED.json"))
        run = self.rs.build_shadow_run("US", snapshot, committed["run_id"], committed["snapshot_sha256"], inputs)
        self.assertTrue(all(c["valuation"]["V1"]["value"] is None for c in run["companies"]))

    def test_v1_is_copied_from_snapshot_only_when_ok(self):
        us = self.shadow("US")["shadow_run"]
        snapshot = json.loads(self.payload("snapshot.json"))
        by_ticker = {c["ticker"]: c for c in snapshot["companies"]}
        for company in us["companies"]:
            v1 = company["valuation"]["V1"]
            source = by_ticker[company["ticker"]]["valuation_v1"]
            if source.get("status") == "OK":
                self.assertEqual(v1["status"], "PUBLIC_DATA_COMPUTED")
                self.assertEqual(v1["value"], source["value"])
                self.assertEqual(v1["provenance"]["field_path"],
                                 f"companies/{by_ticker[company['ticker']]['security_id']}/valuation/V1")
            else:
                self.assertIsNone(v1["value"])

    def _frozen_shadow_validation(self):
        restored = self.rs.restore_inputs(json.loads(self.payload("input_manifest.json")), self.base / "_cas")
        inputs = self.rs.frozen_schema2_inputs(restored, self.base)
        data = self.shadow("US")
        return copy.deepcopy(data["shadow_run"]), inputs

    def test_v1_public_data_computed_passes_end_to_end_shadow_validation(self):
        """A formal commit in this temporary project must feed a verified V1 into its shadow verdict."""
        data = self.shadow("US")
        for ticker in ("ANET", "AVGO", "ETN", "VRT"):
            company = next(c for c in data["shadow_run"]["companies"] if c["ticker"] == ticker)
            self.assertEqual(company["valuation"]["V1"]["status"], "PUBLIC_DATA_COMPUTED")
            self.assertFalse(any(f"({ticker})" in e and "V1_" in e for e in data["validation"]["errors"]), ticker)
            row = next(r for r in data["rows"] if r["ticker"] == ticker)
            self.assertNotIn("V1：DATA_INSUFFICIENT_CANONICAL_OBSERVATIONS",
                             [g["missing"] for g in row["gaps"]])

    def test_v1_public_data_computed_tamper_is_replay_mismatch(self):
        import computed_provenance
        run, inputs = self._frozen_shadow_validation()
        company = next(c for c in run["companies"] if c["ticker"] == "ANET")
        company["valuation"]["V1"]["value"] += 0.01
        computed_provenance._REPLAY_CACHE.clear()
        errors = validate(run, inputs["rules"], inputs)[0]
        self.assertTrue(any("V1_DERIVATION_FAILED:DERIVED_SOURCE_MISMATCH:REPLAY_VALUE_MISMATCH" in e
                            for e in errors))

    def test_v1_public_data_computed_without_provenance_fails(self):
        import computed_provenance
        run, inputs = self._frozen_shadow_validation()
        company = next(c for c in run["companies"] if c["ticker"] == "ANET")
        company["valuation"]["V1"].pop("provenance")
        computed_provenance._REPLAY_CACHE.clear()
        errors = validate(run, inputs["rules"], inputs)[0]
        self.assertTrue(any("V1_DERIVATION_FAILED:DERIVED_SOURCE_MISMATCH:PROVENANCE_MISSING" in e
                            for e in errors))

    def test_v1_legacy_without_provenance_keeps_observation_path(self):
        import computed_provenance
        run, inputs = self._frozen_shadow_validation()
        company = next(c for c in run["companies"] if c["ticker"] == "ANET")
        field = company["valuation"]["V1"]
        field.pop("provenance")
        field["status"] = "已開原文"
        computed_provenance._REPLAY_CACHE.clear()
        errors = validate(run, inputs["rules"], inputs)[0]
        self.assertTrue(any("V1_DERIVATION_FAILED:DATA_INSUFFICIENT_CANONICAL_OBSERVATIONS" in e
                            for e in errors))

    def test_missing_maturity_tier_is_data_insufficient(self):
        restored = self.rs.restore_inputs(json.loads(self.payload("input_manifest.json")), self.base / "_cas")
        inputs = self.rs.frozen_schema2_inputs(restored, self.base)
        sid = next(s["security_id"] for s in inputs["registry"]["securities"] if s["ticker"] == "ANET")
        inputs["rules"]["maturity_tier_by_security"].pop(sid)
        snapshot = json.loads(self.payload("snapshot.json")); committed = json.loads(self.payload("COMMITTED.json"))
        result = self.rs.shadow_market_result("US", snapshot, committed["run_id"], committed["snapshot_sha256"], inputs)
        row = next(r for r in result["rows"] if r["ticker"] == "ANET")
        self.assertTrue(row["shadow_verdict"].startswith("資料不足"))
        self.assertIn("maturity_tier", [g["missing"] for g in row["gaps"]])
        self.assertTrue(any("maturity_tier" in e for e in result["validation"]["errors"]))

    def test_tampered_shadow_payload_is_rejected(self):
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as d:
            copy = self.rs.Path(d, "影子快照", self.run_dir.name); shutil.copytree(self.run_dir, copy)
            (copy / "shadow_verdict_US.json").write_bytes(self.payload("shadow_verdict_US.json") + b" ")
            with self.assertRaisesRegex(ValueError, "SHADOW_PAYLOAD_HASH"): self.rs.read_shadow_committed(copy)

    def test_phase2_failure_writes_failure_and_keeps_phase1(self):
        import tempfile, shutil, unittest.mock as mock
        with tempfile.TemporaryDirectory() as d:
            base = self.rs.Path(d, "影子快照"); copy = base / self.run_dir.name
            shutil.copytree(self.run_dir, copy); shutil.copytree(self.base / "_cas", base / "_cas")
            for name in self.rs.SHADOW_PAYLOADS + (self.rs.SHADOW_MARKER,): (copy / name).unlink()
            with mock.patch.object(self.rs, "shadow_market_result", side_effect=RuntimeError("injected")):
                out = self.rs.commit_shadow_preview(copy, base)
            self.assertEqual(out["status"], "SHADOW_FAILED")
            self.rs.read_committed(copy)
            self.assertTrue((copy / self.rs.SHADOW_FAILURE).exists()); self.assertFalse((copy / self.rs.SHADOW_MARKER).exists())
            failure = json.loads((copy / self.rs.SHADOW_FAILURE).read_bytes())
            self.assertTrue(failure["phase1_unaffected"]); self.assertIn("injected", failure["detail"])

    def test_formal_run_survives_unexpected_phase2_exception(self):
        import tempfile, shutil, unittest.mock as mock
        with tempfile.TemporaryDirectory() as d:
            root = self.rs.Path(d)
            for rel in list(self.rs.INPUT_PATHS.values()) + [f"03_工具/{n}" for n in self.rs.CODE_PATHS]:
                if (self.root / rel).exists():
                    (root / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(self.root / rel, root / rel)
            old = (self.rs.project_lock.LOCK, self.rs.project_lock.HIST)
            self.rs.project_lock.LOCK = os.path.join(d, "project.lock"); self.rs.project_lock.HIST = os.path.join(d, "lock_history")
            try:
                with mock.patch.object(self.rs, "commit_shadow_preview", side_effect=KeyError("boom")):
                    out = self.rs.formal_run(root, lambda: "2026-10-13T04:00:00+00:00")
                self.assertIsNone(self.rs.project_lock.read())
            finally:
                self.rs.project_lock.LOCK, self.rs.project_lock.HIST = old
            self.assertEqual(out["shadow_status"], "SHADOW_FAILED")
            self.rs.read_committed(out["directory"])

    def test_h_hypothesis_only_downgrades_research_with_warning(self):
        h = self.rs._h_hypothesis
        warn = {"indicators": {"H1": {"status": "警示"}, "H2": {"status": "穩健"}}}
        missing = {"indicators": {"H1": {"status": "資料不足"}, "H2": {"status": "穩健"}}}
        ok = {"indicators": {"H1": {"status": "留意"}, "H2": {"status": "不適用"}}}
        self.assertEqual(h("可列入研究", warn)["hypothesis_verdict"], "等待"); self.assertTrue(h("可列入研究", warn)["changed"])
        self.assertEqual(h("可列入研究", missing)["hypothesis_verdict"], "可列入研究")
        self.assertEqual(h("可列入研究", missing)["h_gate"], "MISSING")
        self.assertEqual(h("可列入研究", None)["h_gate"], "MISSING")
        self.assertEqual(h("可列入研究", ok)["h_gate"], "PASS")
        self.assertEqual(h("等待", warn)["hypothesis_verdict"], "等待"); self.assertFalse(h("等待", warn)["changed"])
        self.assertEqual(h("資料不足", warn)["hypothesis_verdict"], "資料不足")
        self.assertFalse(h("可列入研究", warn)["is_conclusion"])

    def test_preview_has_two_zones_and_gap_stages(self):
        page = self.payload(self.rs.SHADOW_PAGE).decode("utf-8")
        self.assertIn("## 有評分資格", page); self.assertIn("## 資料不足／待補（工作清單，不代表最差）", page)
        self.assertIn("S3：V1 試行", page); self.assertIn("V1_ADVISORY", page)
        tw = self.shadow("TW")
        self.assertTrue(all(r["zone"] == "GAP" for r in tw["rows"]))
        self.assertTrue(all("company_score_min_1" in r and "V1_ADVISORY" in r for r in tw["rows"]))
        tw_by_ticker = {r["ticker"]: r for r in tw["rows"]}
        self.assertNotEqual(tw_by_ticker["3017"]["V1_ADVISORY"], "無 5 年基準")
        us_by_ticker = {r["ticker"]: r for r in self.shadow("US")["rows"]}
        self.assertNotEqual(us_by_ticker["TSM"]["V1_ADVISORY"], "無 5 年基準")
        self.assertEqual(us_by_ticker["GEV"]["V1_ADVISORY"], "無 5 年基準")


class ValuationV1PilotS3c(unittest.TestCase):
    """S3c：ANET V1 試行（版本保留申報、TTM EPS＝四季淨利÷最近一季稀釋股數、分割同基準、INVALID_MONTH）。"""
    @classmethod
    def setUpClass(cls):
        import research_snapshot as rs, valuation_v1 as v
        cls.rs, cls.v = rs, v
        cls.c = rs.capture_inputs(); cls.cut = rs.parse_time("2026-10-13T20:00:00+00:00")
        cls.reg = json.loads(cls.c["registry"]); cls.rules = json.loads(cls.c["rules"])
        cls.obs = rs.frozen_observations(cls.c)
        cls.sessions, ver = rs.bound_sessions(cls.c, cls.cut); cls.all_sessions = rs._all_bound_sessions(cls.c, ver)
        cls.prices, cls.anchors, _ = rs.frozen_prices(cls.c, cls.reg, cls.sessions, cls.cut)
        cls.acts = rs.frozen_actions(cls.c, cls.reg, cls.anchors, cls.cut)
        cls.sec = next(s for s in cls.reg["securities"] if s["ticker"] == "ANET")
        cls.anet = [p for p in cls.prices if p["ticker"] == "ANET"]
        cls.splits = sorted((a["date"], float(a["value"])) for a in cls.acts if a["ticker"] == "ANET" and a["type"] == "分割")

    def compute(self, prices=None, anchor=None):
        return self.v.compute(self.sec, self.obs, self.anet if prices is None else prices, self.acts, self.all_sessions,
                              self.anchors["US"] if anchor is None else anchor, self.cut, self.rules)

    def compute_ticker(self, ticker):
        sec = next(s for s in self.reg["securities"] if s["ticker"] == ticker)
        prices = [p for p in self.prices if p["ticker"] == ticker]
        return self.v.compute(sec, self.obs, prices, self.acts, self.all_sessions,
                              self.anchors.get(sec["market"]), self.cut, self.rules)

    def test_split_factor_only_for_versions_filed_before_split(self):
        f = self.v.split_factor
        self.assertEqual(f([("2021-11-18", 4.0)], "2021-11-02", "2021-11-30"), 4.0)
        self.assertEqual(f([("2021-11-18", 4.0)], "2022-02-15", "2022-02-28"), 1.0)
        self.assertEqual(f([("2021-11-18", 4.0)], "2021-08-03", "2021-10-29"), 1.0)
        self.assertEqual(f([("2021-11-18", 4.0), ("2024-12-04", 4.0)], "2021-11-02", "2024-12-31"), 16.0)
        self.assertIn(("2021-11-18", 4.0), self.splits); self.assertIn(("2024-12-04", 4.0), self.splits)

    def test_version_rows_keep_every_filing_and_exclude_proxy_statements(self):
        facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 840854000, "form": "10-K", "filed": "2022-02-15", "accn": "A"},
            {"start": "2021-01-01", "end": "2021-12-31", "val": 840854000, "form": "10-K", "filed": "2023-02-14", "accn": "B"},
            {"start": "2021-01-01", "end": "2021-12-31", "val": 841, "form": "DEF 14A", "filed": "2026-04-16", "accn": "C"}]}}}}}
        rows = self.v.version_rows(facts, "I", "0000000001", "2026-10-13T00:00:00+08:00")
        self.assertEqual(sorted(r["accession_number"] for r in rows), ["A", "B"])
        self.assertTrue(all(r["available_at"] == r["filed_date"] + "T23:59:59-05:00" for r in rows))
        import data_spine
        self.assertEqual([e for r in rows for e in data_spine.validate_observation(r)], [])

    def test_broadcom_uses_current_profitloss_tag_not_stale_netincomeloss(self):
        facts = {"facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [{"start":"2019-01-01","end":"2019-03-31","val":1,
                "form":"10-Q","filed":"2019-05-01","accn":"OLD"}]}},
            "ProfitLoss": {"units": {"USD": [{"start":"2026-01-01","end":"2026-03-31","val":2,
                "form":"10-Q","filed":"2026-05-01","accn":"NEW"}]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": []}}
        }}}
        rows = self.v.version_rows(facts, "ISSUER_BROADCOM", "0001730168", "2026-10-13T00:00:00+08:00")
        net = [r for r in rows if r["metric_id"] == "VER_NET_INCOME_PARENT"]
        self.assertEqual([(r["source_tag"], r["accession_number"]) for r in net], [("ProfitLoss", "NEW")])

    def test_point_in_time_uses_first_reported_shares_before_restatement(self):
        when = self.rs.parse_time("2022-01-31T21:00:00+00:00")
        sh = self.v._visible(self.obs, "VER_DILUTED_SHARES_WAVG", "ISSUER_ARISTA", when)
        self.assertEqual(sh[("2021-01-01", "2021-03-31")]["value"], 79623000.0)   # 10-Q 2021-05-06（分割前基準）
        later = self.v._visible(self.obs, "VER_DILUTED_SHARES_WAVG", "ISSUER_ARISTA", self.cut)
        self.assertEqual(later[("2021-01-01", "2021-03-31")]["value"], 318492000.0)  # 2022-05-03 重編版

    def test_ttm_eps_manual_reproduction_nov_2021(self):
        when = self.rs.parse_time("2021-11-30T21:00:00+00:00")
        e = self.v.ttm_eps(self.obs, "ISSUER_ARISTA", when, "2021-11-30", self.splits)
        ni = 182958000 + 180366000 + 196888000 + 224305000          # Q4'20（FY−9M）＋Q1＋Q2（H1−Q1）＋Q3（9M−H1）
        self.assertEqual(e["quarters"], ["2020-12-31", "2021-03-31", "2021-06-30", "2021-09-30"])
        self.assertAlmostEqual(e["net_income_ttm"], ni, places=0)
        self.assertEqual(e["split_factor"], 4.0)
        self.assertAlmostEqual(e["value"], ni / (79909000 * 4), places=9)

    def test_invalid_months_are_counted_not_dropped(self):
        full = self.compute()
        self.assertEqual(full["status"], "OK"); self.assertEqual(full["valid_months"] + full["invalid_months"], 60)
        drop = [p for p in self.anet if not p["date"].startswith("2022-03")]
        r = self.compute(drop)
        self.assertEqual(next(m for m in r["months"] if m["month"] == "2022-03")["status"], "INVALID_MONTH")
        self.assertEqual(r["valid_months"], full["valid_months"] - 1); self.assertEqual(r["status"], "OK")
        months = sorted({p["date"][:7] for p in self.anet if "2021-10" <= p["date"][:7] <= "2022-10"})
        r = self.compute([p for p in self.anet if p["date"][:7] not in months])
        self.assertEqual(r["status"], "DATA_INSUFFICIENT_VALID_MONTHS"); self.assertIsNone(r["value"])

    def test_current_multiple_is_bound_to_anchor_session(self):
        r = self.compute(anchor="2026-10-11")      # 週日，沒有價格
        self.assertEqual(r["current"]["status"], "CURRENT_PRICE_NOT_AT_ANCHOR"); self.assertIsNone(r["value"])

    def test_snapshot_stores_v1_without_writing_multiples_to_canonical(self):
        snap = self.rs.build_snapshot(self.c, self.cut)
        anet = next(x for x in snap["companies"] if x["ticker"] == "ANET")["valuation_v1"]
        self.assertEqual(anet["status"], "OK"); self.assertEqual(len(anet["months"]), 60)
        self.assertTrue(all(m.get("price_row_sha256") for m in anet["months"] if m["status"] == "VALID"))
        self.assertTrue(all(m["ttm"]["observation_ids"] for m in anet["months"] if m["status"] == "VALID"))
        self.assertFalse(any(r.get("metric_id") in ("TRAILING_PE", "TTM_EPS") for r in self.obs))
        mu = next(x for x in snap["companies"] if x["ticker"] == "MU")["valuation_v1"]
        self.assertIn(mu["status"], {"DATA_INSUFFICIENT_VERSIONED_FILINGS", "NOT_APPLICABLE_INSUFFICIENT_HISTORY",
                                     "DATA_INSUFFICIENT_VALID_MONTHS", "NOT_APPLICABLE_CURRENT_MULTIPLE"})
        self.assertIn("V1", self.rs.research_page(snap))

    def test_expanded_us_universe_and_explicit_exclusions(self):
        for ticker in ("AVGO", "VRT", "MRVL", "LITE", "COHR", "ETN"):
            result = self.compute_ticker(ticker)
            self.assertNotEqual(result["status"], "DATA_INSUFFICIENT_VERSIONED_FILINGS", ticker)
            self.assertEqual(result["valid_months"] + result["invalid_months"], 60, ticker)
        etn = self.compute_ticker("ETN")
        self.assertIn("ETN_MOBILITY_SPINOFF_ANNOUNCED_2026_01_WINDOW_COMPARABILITY_LIMITED", etn["limitations"])
        tsm = self.compute_ticker("TSM")
        self.assertNotEqual(tsm["status"], "DATA_INSUFFICIENT_USD_TWD_FX")
        self.assertEqual(tsm["valid_months"] + tsm["invalid_months"], 60)
        self.assertEqual(self.compute_ticker("GEV")["status"], "NOT_APPLICABLE_LISTED_UNDER_5Y")
        self.assertNotEqual(self.compute_ticker("MU")["status"], "NOT_IMPLEMENTED_TRAILING_PB")

    def test_pb_uses_point_in_time_equity_and_period_end_shares(self):
        when = self.rs.parse_time("2026-08-31T13:30:00+08:00")
        sec = next(s for s in self.reg["securities"] if s["ticker"] == "2408")
        base = {"issuer_id": sec["issuer_id"], "available_at": "2026-08-10T10:00:00+08:00",
                "period_end": "2026-06-30", "filed_date": "2026-08-10"}
        rows = [dict(base, metric_id="VER_EQUITY_PARENT", value=300_000_000_000, observation_id="E"),
                dict(base, metric_id="VER_ISSUED_CAPITAL", value=30_000_000_000, observation_id="C"),
                dict(base, metric_id="VER_TREASURY_SHARES", value=10_000_000, observation_id="T")]
        got = self.v.book_value_per_share(rows, sec, when, "2026-08-31", [], self.rules)
        self.assertEqual(got["shares_basis"], "ISSUED_CAPITAL_DIV_PAR_MINUS_TREASURY")
        self.assertAlmostEqual(got["value"], 300_000_000_000 / (3_000_000_000 - 10_000_000))

    def test_pb_missing_component_is_not_zero(self):
        when = self.rs.parse_time("2026-08-31T13:30:00+08:00")
        sec = next(s for s in self.reg["securities"] if s["ticker"] == "2344")
        rows = [{"issuer_id": sec["issuer_id"], "metric_id": "VER_EQUITY_PARENT", "value": 1,
                 "available_at": "2026-08-10T10:00:00+08:00", "period_end": "2026-06-30", "observation_id": "E"}]
        self.assertEqual(self.v.book_value_per_share(rows, sec, when, "2026-08-31", [], self.rules)["reason"],
                         "PERIOD_END_SHARES_COMPONENTS_UNAVAILABLE")

    def test_stock_dividend_is_an_explicit_share_factor(self):
        self.assertEqual(self.v.split_factor([("2026-07-01", 1.1)], "2026-05-01", "2026-08-31"), 1.1)

    def test_rows_ingested_after_cutoff_are_invisible(self):
        early = self.rs.parse_time("2026-10-13T00:00:00+00:00")    # 版本列寫入帳本之前
        r = self.v.compute(self.sec, self.obs, self.anet, self.acts, self.all_sessions, "2026-10-12", early, self.rules)
        self.assertEqual(r["status"], "DATA_INSUFFICIENT_VERSIONED_FILINGS")

    def test_fetch_prices_backfill_only_requests_before_existing(self):
        import fetch_prices as fp, unittest.mock as mock, datetime
        seen = {}
        def fake_us(t, s, e, now, key): seen["range"] = (s, e); return [], [], []
        existing = {"ANET": [("2024-01-02", 1.0, 1.0), ("2024-01-03", 1.0, 1.0)]}
        argv = ["fetch_prices.py", "--tickers", "ANET", "--start", "2021-08-01", "--backfill"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(fp, "fetch_us", side_effect=fake_us), \
             mock.patch.object(fp, "load_prices", return_value=existing), mock.patch.object(fp, "find_key", return_value=("k", "x", [])), \
             mock.patch.object(fp, "append_rows", return_value=(0, [])), mock.patch.object(fp, "report_gaps", return_value=0), \
             mock.patch.object(fp, "file_lock", side_effect=lambda *a, **k: __import__("contextlib").nullcontext()), \
             mock.patch.object(fp, "finalize_fetch_record", return_value=(None, 0)), mock.patch.object(fp, "out"):
            fp.main()
        self.assertEqual(seen["range"], (datetime.date(2021, 8, 1), datetime.date(2024, 1, 1)))


class GeminiDecisionRegression(unittest.TestCase):
    def test_theme_i3_is_memory_only_and_nonmemory_needs_two_of_three(self):
        from verdict import judge
        run = good_run()
        run["themes"] = {"optical": {"I1": F(70), "I2": F(1), "I3": F("下跌"), "I4": F(None)}}
        run["companies"][0]["theme"] = "optical"
        result = judge(run, RULES)
        theme = result["themes"]["optical"]
        self.assertTrue(theme["enough"])
        self.assertNotIn("I3", theme["detail"])
        self.assertEqual(theme["not_applicable"], ["I3"])
        self.assertEqual(theme["min_filled"], 2)

    def test_fetch_financials_retains_q_and_fy_independently(self):
        from fetch_financials import bounded_period_history
        rows = ([('Q', f"Q{i:02d}", {}, i, 'Q') for i in range(29)] +
                [('FY', f"FY{i:02d}", {}, i, 'FY') for i in range(10)])
        got = bounded_period_history(rows)
        self.assertEqual(sum(r[0] == "Q" for r in got), 28)
        self.assertEqual(sum(r[0] == "FY" for r in got), 8)

    def test_tsm_fx_missing_and_formula(self):
        import valuation_v1 as v
        when = v.parse_time("2026-08-31T16:00:00-04:00")
        self.assertIsNone(v._fx_usdtwd([], when, "2026-08-31"))
        got = v._fx_usdtwd([{"fx_pair":"USD/TWD", "rate":"32", "fixing_at":"2026-08-31T10:00:00+08:00",
                            "available_at":"2026-08-31T10:01:00+08:00", "fx_observation_id":"FX1"}], when, "2026-08-31")
        self.assertEqual(got["rate"], 32.0)
        self.assertAlmostEqual(10 * 5 / got["rate"], 1.5625)

    def test_share_shift_warning_only_above_five_percent(self):
        import valuation_v1 as v
        base = {"issuer_id":"I", "metric_id":"FIN_DILUTED_SHARES", "available_at":"2026-09-01T00:00:00+00:00"}
        rows = [dict(base, observation_id="A", period_start="2025-01-01", period_end="2025-06-30", value=100),
                dict(base, observation_id="B", period_start="2026-01-01", period_end="2026-06-30", value=106)]
        warning = v._share_shift_warning(rows, "I", v.parse_time("2026-10-01T00:00:00+00:00"))
        self.assertEqual(warning["code"], "SHARE_COUNT_SHIFT_DISTORTION_WARNING")


class S3dRegression(unittest.TestCase):
    def test_s3i_3324_june_2025_stale_ttm_and_may_grace_boundary(self):
        import valuation_v1 as v
        denominator = {"quarters": ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"],
                       "quarter_derivations": {"2024-12-31": "DERIVED_Q4_EPS_FY_MINUS_9M"}}
        policy = v.stale_ttm_after(denominator, "TW")
        self.assertEqual(policy["statutory_deadline"], "2025-05-15")
        self.assertEqual(policy["stale_after"], "2025-05-30")
        self.assertFalse(v._is_stale_denominator(denominator, "TW", "2025-05-30")[1])
        self.assertTrue(v._is_stale_denominator(denominator, "TW", "2025-06-30")[1])

    def test_s3i_normally_timely_company_is_not_stale(self):
        import valuation_v1 as v
        denominator = {"quarters": ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"],
                       "quarter_derivations": {"2025-03-31": "REPORTED_SINGLE_QUARTER_DILUTED_EPS"}}
        policy, stale = v._is_stale_denominator(denominator, "TW", "2025-06-30")
        self.assertEqual(policy["stale_after"], "2025-08-29")
        self.assertFalse(stale)

    def test_s3i_pb_uses_same_staleness_rule(self):
        import valuation_v1 as v
        policy, stale = v._is_stale_denominator(
            {"period_end": "2024-09-30", "fiscal_period": "Q3"}, "US", "2025-05-01")
        self.assertEqual(policy["basis"], "US_NEXT_QUARTER_90D_PLUS_15D")
        self.assertTrue(stale)

    def test_s3i_us_q3_uses_next_annual_deadline_and_preserves_month_end(self):
        import valuation_v1 as v
        denominator = {"quarters": ["2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30"],
                       "quarter_derivations": {"2023-09-30": "REPORTED_Q1"}, "fiscal_period": "Q3"}
        policy = v.stale_ttm_after(denominator, "US")
        self.assertEqual(policy["next_period_end"], "2023-12-31")
        self.assertEqual(policy["basis"], "US_NEXT_QUARTER_90D_PLUS_15D")
        self.assertFalse(v._is_stale_denominator(denominator, "US", "2024-02-29")[1])

    def test_tw_action_append_preview_allocates_by_source(self):
        import fetch_tw_actions as f
        old = [{"date":"2026-01-01","ticker":"3017","type":"現金股利","value":"1"}]
        twse = [dict(old[0]), {"date":"2026-02-01","ticker":"3017","type":"現金股利","value":"2"}]
        tpex = [{"date":"2026-03-01","ticker":"3081","type":"現金股利","value":"3"}]
        self.assertEqual(f.append_preview(old, twse), (1, 0))
        self.assertEqual(f.append_preview(old, tpex), (1, 0))

    def test_tw_eps_uses_reported_quarters_and_q4_fy_minus_9m(self):
        import valuation_v1 as v
        rows = []
        values = [("2025-03-31", "Q1", 1.0, "Q"), ("2025-06-30", "Q2", 2.0, "Q"),
                  ("2025-09-30", "Q3", 3.0, "Q"), ("2025-09-30", "Q3", 6.0, "YTD"),
                  ("2025-12-31", "Q4", 10.0, "YTD")]
        for i, (end, fp, value, kind) in enumerate(values):
            rows.append({"issuer_id":"I", "metric_id":"VER_DILUTED_EPS_" + kind,
                         "period_start":"2025-01-01", "period_end":end, "fiscal_year":2025,
                         "fiscal_period":fp, "value":value, "available_at":"2026-04-01T00:00:00+08:00",
                         "filed_date":"2026-03-31", "observation_id":f"E{i}"})
        got = v.tw_ttm_eps(rows, "I", v.parse_time("2026-05-01T00:00:00+08:00"), "2026-05-01", [])
        self.assertEqual(got["value"], 10.0)
        self.assertEqual(got["quarterly_diluted_eps"]["2025-12-31"], 4.0)
        self.assertEqual(got["quarter_derivations"]["2025-12-31"], "DERIVED_Q4_EPS_FY_MINUS_9M")

    def test_tw_eps_stock_dividend_adjusts_each_reported_basis(self):
        import valuation_v1 as v
        rows=[]
        for i,(end,fp,val,kind,filed) in enumerate([
                ("2025-03-31","Q1",1.0,"Q","2025-05-01"),
                ("2025-06-30","Q2",2.0,"Q","2025-08-01"),
                ("2025-09-30","Q3",3.0,"Q","2025-11-01"),
                ("2025-09-30","Q3",6.0,"YTD","2025-11-01"),
                ("2025-12-31","Q4",10.0,"YTD","2026-03-01")]):
            rows.append({"issuer_id":"I","metric_id":"VER_DILUTED_EPS_"+kind,
                         "period_start":"2025-01-01","period_end":end,"fiscal_year":2025,
                         "fiscal_period":fp,"value":val,"available_at":"2026-04-01T00:00:00+08:00",
                         "filed_date":filed,"observation_id":f"S{i}"})
        got=v.tw_ttm_eps(rows,"I",v.parse_time("2026-08-01T00:00:00+08:00"),"2026-08-01",[("2026-07-01",1.1)])
        self.assertAlmostEqual(got["value"],10.0/1.1)

    def test_fx_never_uses_future_fixing(self):
        import valuation_v1 as v
        when=v.parse_time("2026-09-02T16:00:00-04:00")
        rows=[{"fx_pair":"USD/TWD","rate":31,"fixing_at":"2026-08-31T12:00:00-04:00","available_at":"2026-08-31T12:00:00-04:00","fx_observation_id":"A"},
              {"fx_pair":"USD/TWD","rate":99,"fixing_at":"2026-09-01T12:00:00-04:00","available_at":"2026-09-01T12:00:00-04:00","fx_observation_id":"B"}]
        self.assertEqual(v._fx_usdtwd(rows,when,"2026-08-31")["rate"],31)

    def test_stock_dividend_is_a_split_for_total_return(self):
        import tempfile, csv, pricedata as p
        path=os.path.join(tempfile.mkdtemp(),"actions.csv")
        with open(path,"w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=p.ACTION_COLS);w.writeheader()
            w.writerow({"date":"2026-07-15","ticker":"3081","type":"股票股利","value":"1.1","source":"official","fetched_at":"2026-07-16"})
        actions=p.load_actions(RULES,path)
        self.assertEqual(actions["3081"]["2026-07-15"]["split"],1.1)

    def test_historical_event_filter_rejects_unknown_and_statutory_proxy(self):
        import signal_eval as s
        registry={"securities":[{"issuer_id":"I","ticker":"T","market":"US","price_series_enabled":True,"security_type":"COMMON_STOCK"}]}
        base={"issuer_id":"I","metric_id":"FIN_REVENUE","period_type":"Q","period_end":"2025-03-31","observation_id":"A"}
        rows=[dict(base,available_at="2025-05-01T00:00:00+00:00",availability_status="BACKFILLED_UNKNOWN",availability_basis="UNKNOWN_BACKFILL"),
              dict(base,observation_id="B",available_at="2025-05-15T23:59:59+08:00",availability_status="POINT_IN_TIME_KNOWN",availability_basis="STATUTORY_DEADLINE_CONSERVATIVE"),
              dict(base,observation_id="C",available_at="2025-05-02T23:59:59-04:00",availability_status="POINT_IN_TIME_KNOWN",availability_basis="OFFICIAL_DATE")]
        got=s._filing_events(rows,registry)
        self.assertEqual([x["event_observation_id"] for x in got],["C"])


class S3eRegression(unittest.TestCase):
    def test_mops_live_fetch_is_disabled_by_robots_policy(self):
        import fetch_mops_financials as m, unittest.mock as mock
        self.assertEqual(m.LIVE_FETCH_DISABLED_REASON, "MOPS_AUTOMATED_FETCH_DISABLED_ROBOTS_DISALLOW")
        with mock.patch.object(sys, "argv", ["fetch_mops_financials.py", "--all", "--dry-run"]), \
                mock.patch.object(m.Fetcher, "request", side_effect=AssertionError("network must not run")) as request:
            with self.assertRaisesRegex(RuntimeError, m.LIVE_FETCH_DISABLED_REASON):
                m.main()
            request.assert_not_called()

    def test_historical_group_stats_cluster_by_event_date(self):
        import signal_eval as s
        rows = [{"available_at":"2026-01-01T10:00:00+08:00","relative_return":0.1,"mae":-0.1,"value":1},
                {"available_at":"2026-01-01T11:00:00+08:00","relative_return":0.2,"mae":-0.2,"value":1},
                {"available_at":"2026-01-02T10:00:00+08:00","relative_return":-0.1,"mae":-0.3,"value":2}]
        got = s._descriptive_group(rows)
        self.assertEqual(got["n"], 3); self.assertEqual(got["event_date_clusters"], 2)
        self.assertAlmostEqual(got["median_relative_return"], 0.1)
        self.assertAlmostEqual(got["win_rate"], 2/3)
        self.assertIsNotNone(got["clustered_se"])
        self.assertEqual(sum(x["n"] for x in s._raw_quintiles(rows).values()), 3)


class ObservationSupersedeSemantics(unittest.TestCase):
    def _rows(self, kind):
        base = {"observation_id":"OLD", "issuer_id":"ISSUER_X", "security_id":"SEC_X",
                "metric_id":"FIN_REVENUE", "unit":"USD", "value":100.0,
                "period_start":"2025-01-01", "period_end":"2025-03-31", "period_type":"Q",
                "source_published_at":"2025-05-01T23:59:59+00:00",
                "available_at":"2025-05-01T23:59:59+00:00", "ingested_at":"2025-05-02T00:00:00+00:00",
                "availability_status":"POINT_IN_TIME_KNOWN"}
        child = dict(base, observation_id="NEW", value=110.0,
                     source_published_at="2026-01-08T23:59:59+00:00",
                     available_at="2026-01-08T23:59:59+00:00", ingested_at="2026-01-09T00:00:00+00:00",
                     supersedes_observation_id="OLD", _supersede_type=kind)
        return [base, child]

    def test_availability_correction_parent_is_never_visible(self):
        import data_spine as ds, health, valuation_v1 as v
        rows = self._rows("AVAILABILITY_CORRECTION")
        early = ds.parse_time("2025-06-01T00:00:00+00:00")
        self.assertEqual(ds.resolve_visible_observation_versions(rows, [rows[0]]), [])
        self.assertEqual(health.point_in_time(rows, "ISSUER_X", early.isoformat(),
                                             {"OLD":early, "NEW":ds.parse_time("2026-01-09T00:00:00+00:00")}), [])
        self.assertEqual(v._visible(rows, "FIN_REVENUE", "ISSUER_X", early), {})

    def test_value_revision_switches_only_when_new_row_is_visible(self):
        import data_spine as ds, health, valuation_v1 as v
        rows = self._rows("VALUE_REVISION")
        early = ds.parse_time("2025-06-01T00:00:00+00:00")
        late = ds.parse_time("2026-02-01T00:00:00+00:00")
        writes = {"OLD":ds.parse_time("2025-05-02T00:00:00+00:00"),
                  "NEW":ds.parse_time("2026-01-09T00:00:00+00:00")}
        self.assertEqual([r["observation_id"] for r in health.point_in_time(rows, "ISSUER_X", early.isoformat(), writes)], ["OLD"])
        self.assertEqual([r["observation_id"] for r in health.point_in_time(rows, "ISSUER_X", late.isoformat(), writes)], ["NEW"])
        self.assertEqual(next(iter(v._visible(rows, "FIN_REVENUE", "ISSUER_X", early).values()))["observation_id"], "OLD")
        self.assertEqual(next(iter(v._visible(rows, "FIN_REVENUE", "ISSUER_X", late).values()))["observation_id"], "NEW")

    def _relation(self):
        return {"relation_id":"REL1", "superseded_observation_id":"OLD",
                "superseding_observation_id":"NEW", "supersede_type":"AVAILABILITY_CORRECTION",
                "reason":"test edge", "recorded_at":"2026-01-09T00:00:00+00:00"}

    def _retraction(self, restores_all=False):
        return {"record_type":"SUPERSEDE_EDGE_RETRACTION", "retraction_id":"RET1",
                "target_relation_id":"REL1", "superseded_observation_id":"OLD",
                "superseding_observation_id":"NEW", "original_supersede_type":"AVAILABILITY_CORRECTION",
                "reason":"edge registered by mistake", "run_id":"TEST_G_S3_13", "lock_holder":"tester",
                "recorded_at":"2026-02-01T00:00:00+00:00", "available_at":"2026-02-01T00:00:00+00:00",
                "restores_all_as_of":restores_all,
                "retraction_basis":"ERRONEOUS_REGISTRATION" if restores_all else "POLICY_CHANGE"}

    def test_edge_retraction_obeys_as_of(self):
        import data_spine as ds
        rows = self._rows("AVAILABILITY_CORRECTION")
        semantics = [self._relation(), self._retraction(False)]
        early = ds.resolve_visible_observation_versions(rows, [rows[0]], semantics,
                                                        as_of="2026-01-15T00:00:00+00:00")
        late = ds.resolve_visible_observation_versions(rows, [rows[0]], semantics,
                                                       as_of="2026-02-02T00:00:00+00:00")
        self.assertEqual(early, [])
        self.assertEqual([r["observation_id"] for r in late], ["OLD"])

    def test_erroneous_availability_edge_retraction_restores_all_as_of(self):
        import data_spine as ds
        rows = self._rows("AVAILABILITY_CORRECTION")
        semantics = [self._relation(), self._retraction(True)]
        early = ds.resolve_visible_observation_versions(rows, [rows[0]], semantics,
                                                        as_of="2025-06-01T00:00:00+00:00")
        self.assertEqual([r["observation_id"] for r in early], ["OLD"])

    def test_edge_retraction_rejects_missing_or_duplicate_target(self):
        import data_spine as ds, tempfile
        rows = self._rows("AVAILABILITY_CORRECTION")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "semantics.jsonl")
            with open(p, "w", encoding="utf-8") as h:
                h.write(json.dumps(self._relation(), ensure_ascii=False) + "\n")
                h.write(json.dumps(self._retraction(True), ensure_ascii=False) + "\n")
            self.assertEqual(len(ds.load_supersede_semantics(p, rows)), 2)
            duplicate = dict(self._retraction(True), retraction_id="RET2")
            with open(p, "a", encoding="utf-8") as h:
                h.write(json.dumps(duplicate, ensure_ascii=False) + "\n")
            with self.assertRaisesRegex(ValueError, "不可重複撤回"):
                ds.load_supersede_semantics(p, rows)
            missing = dict(self._retraction(True), retraction_id="RET3", target_relation_id="NOPE")
            self.assertTrue(ds.validate_supersede_retraction(
                missing, {"REL1":self._relation()}, {"OLD", "NEW"}))


class LockTool(unittest.TestCase):
    def test_writer_identity_must_match_live_lock_holder(self):
        import lock, ai_fields, tempfile
        d = tempfile.mkdtemp(); old = (lock.LOCK, lock.HIST)
        lock.LOCK, lock.HIST = os.path.join(d, "x.lock"), os.path.join(d, "hist")
        ledger = os.path.join(d, "ledger.jsonl")
        try:
            self.assertTrue(lock.acquire("owner", "T13 test")[0])
            with self.assertRaisesRegex(PermissionError, "PROJECT_LOCK_HOLDER_REQUIRED"):
                lock.require_holder(None)
            with self.assertRaisesRegex(PermissionError, "PROJECT_LOCK_HOLDER_MISMATCH"):
                ai_fields.append_row(ledger, {"id": "WRONG"}, "id", lock_holder="other")
            self.assertFalse(os.path.exists(ledger))
            written = ai_fields.append_row(ledger, {"id": "OK"}, "id", lock_holder="owner")
            self.assertEqual(written["id"], "OK")
            self.assertEqual([r["id"] for r in ai_fields.read_jsonl(ledger)], ["OK"])
        finally:
            if lock.read() and lock.read().get("who") == "owner": lock.release("owner")
            lock.LOCK, lock.HIST = old

    def test_exclusive_and_archived(self):
        import lock, tempfile
        d = tempfile.mkdtemp(); old = (lock.LOCK, lock.HIST)
        lock.LOCK, lock.HIST = os.path.join(d, "x.lock"), os.path.join(d, "hist")
        try:
            self.assertTrue(lock.acquire("a", "測試")[0])
            ok, holder = lock.acquire("b", "搶")
            self.assertFalse(ok); self.assertEqual(holder["who"], "a")
            self.assertFalse(lock.release("b")[0])
            self.assertFalse(lock.force_release("b", "未逾時")[0])
            self.assertTrue(lock.release("a")[0])
            self.assertIsNone(lock.read()); self.assertEqual(len(os.listdir(lock.HIST)), 1)
            self.assertTrue(lock.acquire("b", "逾時測試", hours=-1)[0])
            self.assertTrue(lock.force_release("b", "使用者同意解除逾時鎖")[0])
        finally:
            lock.LOCK, lock.HIST = old

    def test_distinct_scopes_can_coexist_and_same_scope_is_exclusive(self):
        import lock, tempfile
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        try:
            self.assertTrue(lock.acquire("a","AI",scopes="ai")[0])
            self.assertTrue(lock.acquire("b","EVENT",scopes="events")[0])
            self.assertFalse(lock.acquire("c","AI2",scopes="ai")[0])
            self.assertEqual({h["who"] for h in lock.holders()}, {"a","b"})
            self.assertTrue(lock.release("a")[0]); self.assertTrue(lock.holder_present("b"))
            self.assertTrue(lock.release("b")[0]); self.assertIsNone(lock.read())
        finally: lock.LOCK,lock.HIST=old

    def test_rules_snapshot_and_legacy_calls_are_global(self):
        import lock, tempfile
        for requested in ("rules","snapshot",None):
            d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
            try:
                self.assertTrue(lock.acquire("global","g",scopes=requested)[0])
                self.assertEqual(set(lock.holders()[0]["scopes"]), set(lock.SCOPES))
                self.assertFalse(lock.acquire("other","o",scopes="ai")[0])
                self.assertTrue(lock.release("global")[0])
            finally: lock.LOCK,lock.HIST=old

    def test_expired_scope_is_not_automatically_stolen(self):
        import lock, tempfile
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        try:
            self.assertTrue(lock.acquire("old","expired",hours=-1,scopes="docs")[0])
            self.assertFalse(lock.acquire("new","must not steal",scopes="docs")[0])
            self.assertTrue(lock.holder_present("old")); self.assertFalse(lock.holder_present("new"))
            self.assertTrue(lock.release("old")[0])
        finally: lock.LOCK,lock.HIST=old

    def test_path_writer_checks_corresponding_scope(self):
        import lock, tempfile
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        try:
            self.assertTrue(lock.acquire("ai_writer","a",scopes="ai")[0])
            lock.require_holder_for_path("ai_writer", os.path.join(ROOT,"01_共用資料","data_spine","ai_reviews.jsonl"))
            with self.assertRaisesRegex(PermissionError,"PROJECT_LOCK_SCOPE_MISMATCH"):
                lock.require_holder_for_path("ai_writer", os.path.join(ROOT,"01_共用資料","data_spine","events.jsonl"))
            self.assertTrue(lock.release("ai_writer")[0])
        finally: lock.LOCK,lock.HIST=old

    def test_mutex_cleanup_failure_preserves_acquire_rejection_and_status_reports_age(self):
        import lock, tempfile
        from unittest import mock
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        try:
            self.assertTrue(lock.acquire("owner","holds ai",scopes="ai")[0])
            real_unlink = lock.os.unlink
            def fail_mutex(path, *args, **kwargs):
                if path == lock.LOCK + ".更新中": raise PermissionError("simulated cleanup denial")
                return real_unlink(path, *args, **kwargs)
            messages=[]
            with mock.patch.object(lock.os,"unlink",side_effect=fail_mutex), mock.patch.object(lock,"out",side_effect=messages.append):
                ok, info = lock.acquire("other","must be rejected",scopes="ai")
            self.assertFalse(ok); self.assertEqual(lock.holders(info)[0]["who"],"owner")
            self.assertTrue(any("LOCK_UPDATE_MUTEX_CLEANUP_FAILED:PermissionError" in x for x in messages))
            self.assertTrue(os.path.exists(lock.LOCK+".更新中"))
            status=[]
            with mock.patch.object(sys,"argv",["lock.py","status"]), mock.patch.object(lock,"out",side_effect=status.append):
                self.assertEqual(lock.main(),0)
            self.assertTrue(any("互斥暫存檔：使用中" in x and "存在 " in x for x in status))
            real_unlink(lock.LOCK+".更新中")
            self.assertTrue(lock.release("owner")[0])
        finally:
            try: os.unlink(lock.LOCK+".更新中")
            except FileNotFoundError: pass
            lock.LOCK,lock.HIST=old

    def test_stale_mutex_is_renamed_archived_and_holder_file_is_untouched(self):
        import lock, tempfile, datetime
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        try:
            self.assertTrue(lock.acquire("owner","holds ai",scopes="ai")[0])
            owner_before=next(h.copy() for h in lock.holders() if h["who"]=="owner")
            mutex=lock.LOCK+".更新中"
            created=(lock.now()-datetime.timedelta(seconds=lock.MUTEX_STALE_SECONDS+5)).isoformat()
            with open(mutex,"w",encoding="utf-8") as f: json.dump({"pid":123,"hostname":"stale-host","created_at":created},f)
            self.assertTrue(lock.acquire("docs","non-conflicting",scopes="docs")[0])
            self.assertEqual(next(h for h in lock.holders() if h["who"]=="owner"),owner_before)
            self.assertTrue(lock.holder_present("owner")); self.assertTrue(lock.holder_present("docs"))
            stale=[x for x in os.listdir(d) if ".更新中.stale." in x]
            self.assertEqual(len(stale),1)
            recovery=[]
            for name in os.listdir(lock.HIST):
                with open(os.path.join(lock.HIST,name),encoding="utf-8") as f:
                    item=json.load(f)
                if item.get("event")=="LOCK_UPDATE_MUTEX_STALE_RECOVERED": recovery.append(item)
            self.assertEqual(len(recovery),1)
            self.assertTrue(recovery[0]["holder_lock_unchanged"])
            self.assertEqual(recovery[0]["stale_mutex"]["hostname"],"stale-host")
            self.assertTrue(lock.release("owner")[0]); self.assertTrue(lock.release("docs")[0])
        finally: lock.LOCK,lock.HIST=old

    def test_parallel_same_scope_acquire_has_exactly_one_winner(self):
        import lock, tempfile, threading
        d=tempfile.mkdtemp(); old=(lock.LOCK,lock.HIST); lock.LOCK,lock.HIST=os.path.join(d,"x.lock"),os.path.join(d,"hist")
        barrier=threading.Barrier(3); results=[]; errors=[]
        def compete(who):
            try:
                barrier.wait(); results.append((who,lock.acquire(who,"parallel",scopes="events")[0]))
            except Exception as e: errors.append(e)
        try:
            threads=[threading.Thread(target=compete,args=(who,)) for who in ("a","b")]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join(12)
            self.assertFalse(errors); self.assertTrue(all(not t.is_alive() for t in threads))
            self.assertEqual(sum(1 for _,ok in results if ok),1)
            winner=next(who for who,ok in results if ok)
            self.assertEqual([h["who"] for h in lock.holders()],[winner])
            self.assertTrue(lock.release(winner)[0])
        finally: lock.LOCK,lock.HIST=old


def _formal_ledger_state():
    """整個回歸流程的正式資料防污染快照（存在性、內容及所屬鎖範圍）。"""
    data = os.path.join(ROOT, "01_共用資料")
    spine = os.path.join(data, "data_spine")
    exact = [
        os.path.join(spine, "observations.jsonl"),
        os.path.join(spine, "observation_write_ledger.jsonl"),
        os.path.join(spine, "observation_supersede_semantics.jsonl"),
        os.path.join(spine, "events.jsonl"),
        os.path.join(spine, "event_write_ledger.jsonl"),
        os.path.join(data, "prices.csv"),
        os.path.join(data, "corporate_actions.csv"),
        os.path.join(data, "macro_vintages.csv"),
        os.path.join(spine, "docs_ledger.jsonl"),
        os.path.join(spine, "ai_extractions.jsonl"),
        os.path.join(spine, "ai_field_values.jsonl"),
        os.path.join(spine, "ai_reviews.jsonl"),
        os.path.join(spine, "ai_field_value_voids.jsonl"),   # T18
    ]
    # 其他正式 ledger/event 檔也納入，避免新增帳本名稱後防護出現盲區。
    discovered = (glob.glob(os.path.join(spine, "*ledger*.jsonl")) +
                  glob.glob(os.path.join(spine, "*event*.jsonl")) +
                  glob.glob(os.path.join(spine, "staging_write_audit_*.jsonl")))
    exact.extend([
        os.path.join(ROOT, "04_回測", "classification_history.jsonl"),
        os.path.join(spine, "staging_write_audit_snapshot.jsonl"),
    ])
    files = sorted(set(os.path.abspath(p) for p in exact + discovered))
    def scope_of(rel):
        name = os.path.basename(rel).lower()
        if rel.startswith("02_執行紀錄/影子快照"):
            return "snapshot"
        if name in {"ai_extractions.jsonl", "ai_field_values.jsonl", "ai_reviews.jsonl",
                    "ai_field_value_voids.jsonl"}:
            return "ai"
        if name == "docs_ledger.jsonl":
            return "docs"
        if name in {"observations.jsonl", "observation_write_ledger.jsonl",
                    "observation_supersede_semantics.jsonl"}:
            return "observations"
        if name in {"events.jsonl", "event_write_ledger.jsonl"}:
            return "events"
        if name in {"prices.csv", "corporate_actions.csv", "macro.csv", "macro_realtime.csv",
                    "macro_vintages.csv", "macro_差異.csv", "prices_差異.csv", "抓取紀錄.jsonl",
                    "market_sessions.csv", "calendar_versions.jsonl", "fx_rates.csv",
                    "fomc_decisions.csv", "staging_write_audit_market.jsonl"}:
            return "market"
        if name in {"classification_history.jsonl", "staging_write_audit_snapshot.jsonl"}:
            return "snapshot"
        if name.startswith("staging_write_audit_"):
            suffix = name.removeprefix("staging_write_audit_").removesuffix(".jsonl")
            return suffix if suffix in {"observations", "events", "ai", "docs", "market", "snapshot"} else "all"
        return "all"

    state = {}
    for path in files:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if not os.path.exists(path):
            state[rel] = {"exists": False, "sha256": None, "content": None,
                          "is_dir": False, "scope": scope_of(rel)}
            continue
        with open(path, "rb") as handle:
            content = handle.read()
        state[rel] = {"exists": True, "sha256": hashlib.sha256(content).hexdigest(),
                      "content": content, "is_dir": False, "scope": scope_of(rel)}
    snapshots = os.path.join(ROOT, "02_執行紀錄", "影子快照")
    prefix = os.path.relpath(snapshots, ROOT).replace("\\", "/")
    state[prefix] = {"exists": os.path.isdir(snapshots), "sha256": None, "content": None,
                     "is_dir": True, "scope": "snapshot"}
    if os.path.isdir(snapshots):
        for base, dirs, names in os.walk(snapshots):
            dirs.sort(); names.sort()
            for name in names:
                path = os.path.join(base, name)
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                with open(path, "rb") as handle:
                    content = handle.read()
                state[rel] = {"exists": True, "sha256": hashlib.sha256(content).hexdigest(),
                              "content": content, "is_dir": False, "scope": "snapshot"}
    return state


def _formal_guard_test_marker(content):
    """Return the first explicit unit-test marker found in appended JSON/bytes."""
    if not content:
        return None
    markers = ("ISSUER_TEST", "SECURITY_TEST", "SNAP_TEST", "TEST_RUN", "RUN_TEST",
               "UNIT_TEST", "TEST_FIXTURE", "TMP_TEST")
    text = content.decode("utf-8", errors="replace")
    upper = text.upper()
    return next((marker for marker in markers if marker in upper), None)


def _formal_ledger_guard_violations(before, after, concurrent_scopes):
    """Allow another lock holder's append/new-file writes, never rewrites or test fixtures."""
    violations = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        scope = (new or old or {}).get("scope", "all")
        if scope not in concurrent_scopes:
            violations.append(f"{path}:CHANGED_WITHOUT_CONCURRENT_{scope.upper()}_LOCK")
            continue
        if old and old.get("exists") and (not new or not new.get("exists")):
            violations.append(f"{path}:REMOVED_DURING_CONCURRENT_WRITE")
            continue
        if (old or {}).get("is_dir") or (new or {}).get("is_dir"):
            # A concurrently held snapshot scope may add files below the
            # existing directory, but may not remove/replace the directory.
            continue
        old_bytes = (old or {}).get("content") or b""
        new_bytes = (new or {}).get("content") or b""
        if not new_bytes.startswith(old_bytes):
            violations.append(f"{path}:NON_APPEND_CHANGE_DURING_CONCURRENT_WRITE")
            continue
        marker = _formal_guard_test_marker(new_bytes[len(old_bytes):])
        if marker:
            violations.append(f"{path}:TEST_WRITE_MARKER:{marker}")
    return violations


class FormalLedgerConcurrentGuard(unittest.TestCase):
    @staticmethod
    def _item(content, scope="ai"):
        return {"exists": True, "sha256": hashlib.sha256(content).hexdigest(),
                "content": content, "is_dir": False, "scope": scope}

    def test_other_holder_append_is_allowed(self):
        before = {"ledger.jsonl": self._item(b'{"value_id":"AIV_1"}\n')}
        after = {"ledger.jsonl": self._item(
            b'{"value_id":"AIV_1"}\n{"value_id":"AIV_2","run_id":"T30D"}\n')}
        self.assertEqual(_formal_ledger_guard_violations(before, after, {"ai"}), [])

    def test_test_process_append_is_rejected_even_when_scope_is_held(self):
        before = {"ledger.jsonl": self._item(b'{"value_id":"AIV_1"}\n')}
        after = {"ledger.jsonl": self._item(
            b'{"value_id":"AIV_1"}\n{"issuer_id":"ISSUER_TEST","run_id":"TEST_RUN"}\n')}
        got = _formal_ledger_guard_violations(before, after, {"ai"})
        self.assertTrue(any("TEST_WRITE_MARKER" in item for item in got), got)

    def test_concurrent_rewrite_is_rejected(self):
        before = {"ledger.jsonl": self._item(b'{"value_id":"AIV_1"}\n')}
        after = {"ledger.jsonl": self._item(b'{"value_id":"AIV_REWRITTEN"}\n')}
        got = _formal_ledger_guard_violations(before, after, {"ai"})
        self.assertTrue(any("NON_APPEND_CHANGE" in item for item in got), got)


class _FormalLedgerGuardSuite(unittest.TestSuite):
    def run(self, result, debug=False):
        import lock as project_lock
        before = _formal_ledger_state()
        holders_before = project_lock.holders()
        concurrent_scopes = {scope for holder in holders_before for scope in (holder.get("scopes") or [])}
        super().run(result, debug)
        # 定義在 run 內，避免 unittest module loader 把 guard 本身當成一個沒有 baseline 的一般測試。
        class GuardCase(unittest.TestCase):
            def runTest(self):
                after = _formal_ledger_state()
                violations = _formal_ledger_guard_violations(before, after, concurrent_scopes)
                self.assertEqual(violations, [], "正式帳本／影子快照防污染失敗：" + "、".join(violations))
        GuardCase().run(result)
        return result


class I4ActualCapexT21(unittest.TestCase):
    def _fact(self, fy, fp, start, end, value, accn=None):
        return {"fy": fy, "fp": fp, "start": start, "end": end, "val": value, "filed": end,
                "accn": accn or f"{fy}-{fp}", "form": "10-K" if fp == "FY" else "10-Q", "unit": "USD"}

    def test_ytd_is_converted_to_single_quarters_and_q4(self):
        from fetch_financials import normalize_duration
        rows = [self._fact(2025, "Q1", "2025-01-01", "2025-03-31", 10),
                self._fact(2025, "Q2", "2025-01-01", "2025-06-30", 25),
                self._fact(2025, "Q3", "2025-01-01", "2025-09-30", 45),
                self._fact(2025, "FY", "2025-01-01", "2025-12-31", 70)]
        got = {(k, e): (v, d) for k, e, _f, v, d in normalize_duration(rows)}
        self.assertEqual(got[("Q", "2025-06-30")], (15.0, "YTD_Q2_MINUS_Q1"))
        self.assertEqual(got[("Q", "2025-09-30")], (20.0, "YTD_Q3_MINUS_YTD_Q2"))
        self.assertEqual(got[("Q", "2025-12-31")], (25.0, "FY_MINUS_YTD_Q3"))

    def test_non_calendar_fiscal_year_microsoft(self):
        from fetch_financials import normalize_duration
        rows = [self._fact(2026, "Q1", "2025-07-01", "2025-09-30", 10),
                self._fact(2026, "Q2", "2025-07-01", "2025-12-31", 30),
                self._fact(2026, "Q3", "2025-07-01", "2026-03-31", 60),
                self._fact(2026, "FY", "2025-07-01", "2026-06-30", 100)]
        got = {(k, e): v for k, e, _f, v, _d in normalize_duration(rows)}
        self.assertEqual(got[("Q", "2026-06-30")], 40.0)

    def test_tag_switch_is_null(self):
        import i4_capex
        facts = {"facts": {"us-gaap": {
            "A": {"units": {"USD": [self._fact(2024, "Q1", "2024-01-01", "2024-03-31", 1)]}},
            "B": {"units": {"USD": [self._fact(2025, "Q1", "2025-01-01", "2025-03-31", 2)]}},
        }}}
        self.assertEqual(i4_capex.select_history_tag(facts, ["A", "B"])["status"], "TAG_SWITCH")

    def _obs(self, issuer, end, value, fp, available="2023-01-01T00:00:00+00:00"):
        import i4_capex
        return {"observation_id": "O" + end + issuer, "issuer_id": issuer, "security_id": None,
                "metric_id": i4_capex.METRIC, "value": value, "unit": "USD", "period_start": end,
                "period_end": end, "period_type": "Q", "fiscal_year": int(end[:4]), "fiscal_period": fp,
                "source_url": "https://www.sec.gov/x", "source_published_at": available, "available_at": available,
                "ingested_at": available, "availability_status": "POINT_IN_TIME_KNOWN",
                "source_system": i4_capex.SOURCE_SYSTEM, "taxonomy": "us-gaap", "source_tag": "A",
                "accession_number": end, "derivation": "TEST", "mapping_version": i4_capex.MAPPING_VERSION,
                "supersedes_observation_id": None}

    def test_ttm_growth_and_acceleration(self):
        import i4_capex
        ends = [f"{y}-{m:02d}-30" for y, m in [(2023,3),(2023,6),(2023,9),(2023,12),(2024,3),(2024,6),(2024,9),(2024,12),(2025,3)]]
        rows = [self._obs("X", e, v, ["Q1","Q2","Q3","FY"][i % 4]) for i, (e, v) in enumerate(zip(ends, [10,10,10,10,12,12,12,12,15]))]
        got = i4_capex.company_result(rows, "X", "2025-05-01T00:00:00+00:00")
        self.assertEqual(got["value"], "擴張")
        # current growth = 51/42-1; previous-quarter growth = 48/40-1.
        self.assertAlmostEqual(got["acceleration"], 51 / 42 - 48 / 40)

    def test_non_contiguous_quarters_are_null(self):
        import i4_capex
        ends = ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
                "2024-03-31", "2024-06-30", "2024-12-31", "2025-03-31"]
        rows = [self._obs("X", end, 10, "Q1") for end in ends]
        got = i4_capex.company_result(rows, "X", "2025-05-01T00:00:00+00:00")
        self.assertEqual(got["status"], "NON_CONTIGUOUS_QUARTERS")
        self.assertIsNone(got["value"])

    def _continuity_facts(self, mismatch=0.0, overlap_count=2):
        def fact(tag, fy, fp, start, end, value, filed, accn):
            return {"fy": fy, "fp": fp, "start": start, "end": end, "val": value,
                    "filed": filed, "accn": accn, "form": "10-K" if fp == "FY" else "10-Q", "unit": "USD"}
        a = [fact("A", 2023, "Q1", "2023-01-01", "2023-03-31", 10, "2023-04-20", "A1"),
             fact("A", 2023, "FY", "2023-01-01", "2023-12-31", 40, "2024-02-01", "A2")]
        b = [fact("B", 2024, "Q1", "2023-01-01", "2023-03-31", 10 * (1 + mismatch), "2024-04-20", "B1"),
             fact("B", 2024, "FY", "2023-01-01", "2023-12-31", 40, "2025-02-01", "B2")]
        if overlap_count == 1:
            b = b[:1]
        return {"facts": {"us-gaap": {"A": {"units": {"USD": a}}, "B": {"units": {"USD": b}}}}}

    def test_tag_overlap_allows_continuity_and_earliest_filing(self):
        import i4_capex
        facts = self._continuity_facts()
        accepted = {"A1": "2023-04-20T16:00:00-04:00", "A2": "2024-02-01T16:00:00-05:00",
                    "B1": "2024-04-20T16:00:00-04:00", "B2": "2025-02-01T16:00:00-05:00"}
        got = i4_capex.select_history_tag(facts, ["A", "B"], accepted)
        self.assertEqual(got["status"], "VERIFIED_OVERLAP")
        selected = {(x[0], x[1]): (x[2]["accn"], x[5]) for x in got["normalized_rows"]}
        self.assertEqual(selected[("Q", "2023-03-31")], ("A1", "A"))
        self.assertEqual(selected[("FY", "2023-12-31")], ("A2", "A"))

    def test_tag_overlap_one_percent_is_rejected(self):
        import i4_capex
        got = i4_capex.select_history_tag(self._continuity_facts(mismatch=0.01), ["A", "B"])
        self.assertEqual(got["status"], "TAG_SWITCH")
        self.assertEqual(got["continuity_reason"], "OVERLAP_VALUE_MISMATCH")

    def test_tag_overlap_only_one_period_is_rejected(self):
        import i4_capex
        got = i4_capex.select_history_tag(self._continuity_facts(overlap_count=1), ["A", "B"])
        self.assertEqual(got["status"], "TAG_SWITCH")
        self.assertEqual(got["continuity_reason"], "OVERLAP_TOO_FEW")

    def test_old_tag_only_before_used_window_is_ignored(self):
        import i4_capex
        old = self._fact(2016, "FY", "2016-01-01", "2016-12-31", 10, "OLD")
        new = self._fact(2020, "FY", "2020-01-01", "2020-12-31", 20, "NEW")
        facts = {"facts": {"us-gaap": {"A": {"units": {"USD": [old]}}, "B": {"units": {"USD": [new]}}}}}
        got = i4_capex.select_history_tag(facts, ["A", "B"])
        self.assertEqual(got["status"], "OK")
        self.assertEqual(got["tag"], "B")
        self.assertEqual(got["tag_period_ranges"]["A"]["used_period_count"], 0)

    def test_both_tags_inside_used_window_apply_overlap_gate(self):
        import i4_capex
        got = i4_capex.select_history_tag(self._continuity_facts(), ["A", "B"])
        self.assertEqual(got["status"], "VERIFIED_OVERLAP")

    def test_old_tag_at_used_window_boundary_applies_overlap_gate(self):
        import i4_capex
        a = self._fact(2017, "FY", "2017-01-01", "2017-12-31", 10, "A")
        b = self._fact(2018, "FY", "2017-01-01", "2017-12-31", 10, "B")
        facts = {"facts": {"us-gaap": {"A": {"units": {"USD": [a]}}, "B": {"units": {"USD": [b]}}}}}
        got = i4_capex.select_history_tag(facts, ["A", "B"])
        self.assertEqual(got["status"], "TAG_SWITCH")
        self.assertEqual(got["continuity_reason"], "OVERLAP_TOO_FEW")

    def test_stale_latest_quarter_is_invalid(self):
        import i4_capex
        ends = [f"{y}-{m:02d}-30" for y, m in [(2023,3),(2023,6),(2023,9),(2023,12),(2024,3),(2024,6),(2024,9),(2024,12)]]
        rows = [self._obs("X", e, 10, ["Q1","Q2","Q3","FY"][i % 4]) for i, e in enumerate(ends)]
        self.assertEqual(i4_capex.company_result(rows, "X", "2026-01-01T00:00:00+00:00")["status"], "STALE")

    def test_fewer_than_three_companies_is_null(self):
        import i4_capex
        got = i4_capex.aggregate({"A": {"value": "擴張"}, "B": {"value": "持平"}, "C": {"value": None}})
        self.assertIsNone(got["value"])
        self.assertEqual(got["status"], "FEWER_THAN_THREE_COMPANIES")


class Rules1120ActivationT35d(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import research_snapshot as rs
        cls.rs = rs
        cls.captured = rs.capture_inputs(rs.Path(ROOT))
        cls.cutoff = rs.parse_time("2026-10-18T08:30:00+00:00")
        cls.snapshot = rs.build_snapshot(cls.captured, cls.cutoff)
        cls.inputs = rs.frozen_schema2_inputs(cls.captured, os.path.join(ROOT, "02_執行紀錄", "影子快照"))

    def test_rules_version_contract_and_sec_i4_source(self):
        rules = json.loads(self.captured["rules"])
        live = pathlib.Path(ROOT, "00_規範", "rules.json")
        archived_live = pathlib.Path(ROOT, "00_規範", "rules_歷史",
                                     f"rules_{rules['protocol_version']}.json")
        self.assertEqual(live.read_bytes(), archived_live.read_bytes())
        with open(os.path.join(ROOT, "00_規範", "rules_歷史", "rules_1.12.0.json"), encoding="utf-8") as handle:
            archived = json.load(handle)
        for versioned in (archived, rules):
            self.assertEqual(versioned["verdict"]["ai_fields_accepted"]["company"]["fields"], ["C5", "C6"])
            self.assertEqual(versioned["verdict"]["ai_fields_accepted"]["theme"]["fields"], ["I2", "I4"])
            self.assertEqual(versioned["verdict"]["ai_fields_accepted"]["theme"]["I4_source"],
                             "SEC_COMPANYFACTS_I4_CAPEX")
            self.assertEqual(versioned["verdict"]["ai_fields_accepted"]["theme"]["I2_source"],
                             "S4_VERIFIED_DUAL_EXTRACTION_ANNUAL_FIRST_THEN_I2Q")
            self.assertEqual(versioned["verdict"]["ai_fields_accepted"]["theme"]["I2_company_priority"],
                             ["I2", "I2Q"])
            self.assertTrue(versioned["verdict"]["tw_statutory_event_window"]["enabled"])
            self.assertEqual(versioned["theme"]["indicators"]["I4"]["acceleration_mode"],
                             "SHADOW_DISPLAY_ONLY_NOT_SCORED")
        policy = rules["verdict"]["us_official_earnings_date_coverage"]
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["policy_id"], "G-S3b-US2")
        self.assertNotIn("us_official_earnings_date_coverage", archived["verdict"])

    def test_numeric_thresholds_are_identical_to_1100(self):
        current = json.loads(self.captured["rules"])
        old = json.load(open(os.path.join(ROOT, "00_規範", "rules_歷史", "rules_1.10.0.json"), encoding="utf-8"))
        for layer in ("market", "company", "theme", "valuation"):
            self.assertEqual(current[layer].get("min_filled"), old[layer].get("min_filled"))
            for key, spec in old[layer]["indicators"].items():
                self.assertEqual(current[layer]["indicators"][key]["rule"], spec["rule"], f"{layer}.{key}")
        for key in ("company_score_min", "theme_score_min", "valuation_score_min", "event_block_days",
                    "event_none_meaning_days"):
            self.assertEqual(current["verdict"].get(key), old["verdict"].get(key), key)

    def test_snapshot_uses_report_reader_and_preserves_partial_annotation(self):
        activated = self.snapshot["activated_fields"]
        self.assertEqual(activated["reader"], "ai_fields.report_field_values")
        i2 = activated["themes"]["power_cooling"]["I2"]
        self.assertTrue(i2["independence_partial"])
        self.assertTrue(any("RUN_20261017T224629" in x for x in i2["review_basis"]))
        self.assertEqual(sum(a.get("independence") == "INDEPENDENCE_PARTIAL" for a in i2["annotations"]), 3)

    def test_shadow_run_scores_sec_i4_and_keeps_acceleration_shadow_only(self):
        run_id = "T12_TEST"
        snapshot_sha = self.rs.digest(self.rs.canonical_bytes(self.snapshot))
        run = self.rs.build_shadow_run("US", self.snapshot, run_id, snapshot_sha, self.inputs)
        field = run["themes"]["power_cooling"]["I4"]
        self.assertEqual(field["source"], "SEC_COMPANYFACTS_I4_CAPEX")
        self.assertEqual(field["value"], "上修")
        self.assertIn("acceleration_shadow", field)
        self.assertNotIn("acceleration", json.loads(self.captured["rules"])["theme"]["indicators"]["I4"]["rule"])

    def test_precommit_candidate_replays_its_own_frozen_snapshot(self):
        run_id = "SNAP_PRECOMMIT_T22_TEST_PASS"
        inputs, snapshot_sha = self.rs.bind_precommit_candidate(
            self.inputs, self.captured, self.snapshot, run_id)
        result = self.rs.shadow_market_result("US", self.snapshot, run_id, snapshot_sha, inputs)
        provenance_errors = [e for e in result["validation"]["errors"]
                             if "DERIVED_SOURCE_MISMATCH" in e]
        self.assertEqual(provenance_errors, [])
        self.assertTrue(self.rs.precommit_comparison_gate({"US": result}))

    def test_precommit_rule_upgrade_replays_candidate_rules(self):
        candidate = dict(self.captured)
        candidate_path = pathlib.Path(ROOT) / "90_各AI工作區" / "codex" / "rules_1.12.0_candidate_blocked_2026-10-19.json"
        candidate["rules"] = candidate_path.read_bytes()
        snapshot = self.rs.build_snapshot(candidate, self.cutoff)
        inputs = self.rs.frozen_schema2_inputs(candidate, os.path.join(ROOT, "02_執行紀錄", "影子快照"))
        inputs, snapshot_sha = self.rs.bind_precommit_candidate(
            inputs, candidate, snapshot, "SNAP_PRECOMMIT_T35C_UPGRADE")
        result = self.rs.shadow_market_result("US", snapshot, "SNAP_PRECOMMIT_T35C_UPGRADE", snapshot_sha, inputs)
        provenance_errors = [e for e in result["validation"]["errors"] if "DERIVED_SOURCE_MISMATCH" in e]
        self.assertEqual(provenance_errors, [])
        field = result["shadow_run"]["market"]["M1"]
        self.assertEqual(field["provenance"]["rules_version"], "1.12.0")
        self.assertEqual(field["provenance"]["rules_sha256"], hashlib.sha256(candidate["rules"]).hexdigest())

    def test_precommit_candidate_provenance_tamper_is_rejected(self):
        candidate = dict(self.captured)
        candidate_path = pathlib.Path(ROOT) / "90_各AI工作區" / "codex" / "rules_1.12.0_candidate_blocked_2026-10-19.json"
        candidate["rules"] = candidate_path.read_bytes()
        snapshot = self.rs.build_snapshot(candidate, self.cutoff)
        inputs = self.rs.frozen_schema2_inputs(candidate, os.path.join(ROOT, "02_執行紀錄", "影子快照"))
        inputs, snapshot_sha = self.rs.bind_precommit_candidate(
            inputs, candidate, snapshot, "SNAP_PRECOMMIT_T35C_TAMPER")
        run = self.rs.build_shadow_run("US", snapshot, "SNAP_PRECOMMIT_T35C_TAMPER", snapshot_sha, inputs)
        run["market"]["M1"]["provenance"]["rules_sha256"] = "0" * 64
        errors, _ = validate(run, inputs["rules"], inputs)
        self.assertTrue(any("PROVENANCE_RULES_BINDING_MISMATCH" in e for e in errors))

    def test_candidate_i2_uses_annual_first_then_i2q(self):
        candidate = dict(self.captured)
        candidate_path = pathlib.Path(ROOT) / "90_各AI工作區" / "codex" / "rules_1.12.0_candidate_blocked_2026-10-19.json"
        candidate["rules"] = candidate_path.read_bytes()
        snapshot = self.rs.build_snapshot(candidate, self.rs.parse_time("2026-10-19T06:45:00+00:00"))
        for theme in ("memory", "foundry", "network_asic", "optical"):
            detail = snapshot["activated_fields"]["themes"][theme]["I2"]["detail"]
            self.assertEqual(detail["field"], "I2+I2Q")
        sources = snapshot["activated_fields"]["themes"]["power_cooling"]["I2"]["detail"]["sources"]
        self.assertEqual(sources.get("VRT"), "I2")

    def test_unbound_precommit_candidate_fails_explicitly(self):
        run_id = "SNAP_PRECOMMIT_T22_TEST_UNBOUND"
        snapshot_sha = self.rs.digest(self.rs.canonical_bytes(self.snapshot))
        result = self.rs.shadow_market_result("US", self.snapshot, run_id, snapshot_sha, self.inputs)
        joined = "\n".join(result["validation"]["errors"])
        self.assertIn("PROVENANCE_UNVERIFIABLE_PRECOMMIT", joined)

    def test_all_same_provenance_failure_invalidates_comparison(self):
        broken = {"US": {"judge": {"companies": [
            {"ticker": "ANET", "verdict": "待查證"},
            {"ticker": "AVGO", "verdict": "待查證"}]},
            "validation": {"errors": [
                "companies[0](ANET).fields.C1：DERIVED_SOURCE_MISMATCH:PROVENANCE_UNVERIFIABLE_PRECOMMIT",
                "companies[1](AVGO).fields.C1：DERIVED_SOURCE_MISMATCH:PROVENANCE_UNVERIFIABLE_PRECOMMIT"]}}}
        with self.assertRaisesRegex(ValueError, "PROVENANCE_UNVERIFIABLE_PRECOMMIT:US"):
            self.rs.precommit_comparison_gate(broken)


class T36StagingWeeklyAndClassification(unittest.TestCase):
    def test_t41_sec_scan_status_and_nearest_event_selection(self):
        import weekly_refresh as wr
        events=[{"event_id":"LATE","record_type":"EVENT","event_status":"SCHEDULED","issuer_id":"I",
                 "event_local_date":"2026-11-10","available_at":"2026-10-01T00:00:00+00:00"},
                {"event_id":"NEAR","record_type":"EVENT","event_status":"SCHEDULED","issuer_id":"I",
                 "event_local_date":"2026-11-03","available_at":"2026-10-02T00:00:00+00:00"}]
        self.assertEqual(wr._nearest_upcoming_by_issuer(events,"2026-10-20")["I"]["event_id"],"NEAR")
        self.assertEqual(wr._sec_scan_overall_status([{"status":"NO_DATE_CHANGE_FOUND"}]),"PASS")
        self.assertEqual(wr._sec_scan_overall_status([{"status":"UNKNOWN","ticker":"ANET"}]),"NEEDS_MANUAL")
        self.assertEqual(wr._sec_scan_overall_status([{"status":"MANUAL_REQUIRED","ticker":"VRT"}]),"NEEDS_MANUAL")

    def test_t41_weekly_markdown_lists_sec_manual_companies(self):
        import weekly_refresh as wr
        report={"run_id":"R","mode":"DRY_RUN","events":{"companies":[]},
                "sec_event_scan":{"status":"NEEDS_MANUAL","companies":[{"ticker":"VRT","status":"MANUAL_REQUIRED",
                    "reason":"AMBIGUOUS_SEC_DATES","url":"https://www.sec.gov/test"}]},
                "preview":{"classification_changes":[],"gaps":[]}}
        page=wr._markdown(report)
        self.assertIn("`VRT`：AMBIGUOUS_SEC_DATES",page)

    def test_staging_validation_failure_leaves_target_byte_identical(self):
        import tempfile
        from staging_write import append_bytes
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "ledger.jsonl")
            pathlib.Path(path).write_bytes(b'{"id":"old"}\n')
            before = pathlib.Path(path).read_bytes()
            def reject(_candidate): raise ValueError("EXPECTED_VALIDATION_FAILURE")
            with self.assertRaisesRegex(ValueError, "EXPECTED_VALIDATION_FAILURE"):
                append_bytes(path, b'{"id":"new"}\n', writer="test", validator=reject)
            self.assertEqual(pathlib.Path(path).read_bytes(), before)

    def test_staging_success_commits_exact_candidate_bytes(self):
        import tempfile
        from staging_write import append_bytes, validate_jsonl_bytes
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "ledger.jsonl")
            before = b'{"id":"old"}\n'; payload = b'{"id":"new"}\n'
            pathlib.Path(path).write_bytes(before)
            report = append_bytes(path, payload, writer="test", validator=validate_jsonl_bytes)
            self.assertEqual(pathlib.Path(path).read_bytes(), before + payload)
            self.assertEqual(report["candidate_sha256"], hashlib.sha256(before + payload).hexdigest())
            self.assertEqual(report["payload_sha256"], hashlib.sha256(payload).hexdigest())

    def test_classification_history_backfills_legacy_and_shadow_snapshots(self):
        import tempfile
        import classification_history as ch
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); ledger = root / "history.jsonl"
            for name, with_shadow in (("SNAP_A", False), ("SNAP_B", True)):
                target = root / name; target.mkdir()
                snapshot = {"companies": [], "market": {"assessment": {"light": "黃"}}}
                raw = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
                (target / "snapshot.json").write_bytes(raw)
                manifest = {"payloads":{"snapshot.json":{"sha256":hashlib.sha256(raw).hexdigest(),"byte_length":len(raw)}}}
                manifest_raw = json.dumps(manifest).encode(); (target / "manifest.json").write_bytes(manifest_raw)
                (target / "COMMITTED.json").write_text(json.dumps({"snapshot_sha256": hashlib.sha256(raw).hexdigest(),
                    "manifest_sha256":hashlib.sha256(manifest_raw).hexdigest(),
                    "published_at": "2026-10-19T00:00:00+00:00"}), encoding="utf-8")
                if with_shadow:
                    payload = {"rows": [], "judge": {"protocol_version": "1.11.0", "companies": [],
                               "themes": {}, "market": {"score": 0, "light": "黃"}}}
                    shadow_payloads = {}
                    for market in ("TW", "US"):
                        path = target / f"shadow_verdict_{market}.json"; path.write_text(json.dumps(payload), encoding="utf-8")
                        shadow_payloads[path.name] = {"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                    (target / "SHADOW_COMMITTED.json").write_text(json.dumps({"payloads":shadow_payloads}), encoding="utf-8")
            result = ch.backfill(root, ledger=ledger)
            rows = ch._existing(ledger)
            self.assertEqual(result["added"], 2)
            self.assertEqual([r["classification_status"] for r in rows],
                             ["LEGACY_NO_SHADOW_CLASSIFICATION", "OK"])
            self.assertEqual(ch.backfill(root, ledger=ledger)["added"], 0)

    def test_classification_eval_uses_null_not_zero_when_horizon_missing(self):
        import tempfile
        import classification_eval as ce
        with tempfile.TemporaryDirectory() as folder:
            ledger = pathlib.Path(folder) / "history.jsonl"
            ledger.write_text(json.dumps({"classification_id":"C1","snapshot_id":"S1",
                "published_at":"2026-01-02T20:00:00+00:00","protocol_version":"1.12.1","companies":[{
                "ticker":"ANET","market":"US","anchor_session":"2026-01-02","classification":"等待"}]})+"\n")
            rules = copy.deepcopy(RULES); rules["benchmarks"]["default_us"] = "SOXX"
            result = ce.evaluate(ledger=ledger, rules=rules,
                                 prices={"ANET":[("2026-01-02",1.0,0)],"SOXX":[("2026-01-02",1.0,0)]}, actions={})
            self.assertTrue(all(row["return"] is None and row["excess_return"] is None for row in result["samples"]))
            self.assertEqual(result["summary"]["20"]["等待"]["conclusion"], "樣本不足，不得下結論")

    def test_weekly_dry_run_does_not_execute_fetchers_or_write_reports(self):
        import weekly_refresh as wr
        from unittest import mock
        with mock.patch.object(wr, "_run_tool", side_effect=AssertionError("fetch must not run")), \
             mock.patch.object(wr, "reconfirm_us_events", return_value={"companies": []}), \
             mock.patch.object(wr, "scan_sec_8k_date_changes", return_value={"status":"DISABLED_CANDIDATE_NOT_ACTIVE","companies":[]}), \
             mock.patch.object(wr, "local_preview", return_value={"candidate_run_id":"C","classification_changes": [], "gaps": []}):
            self.assertEqual(wr.main(["--dry-run"]), 0)


class T42DataIntegrityAndEvaluation(unittest.TestCase):
    def test_ai_candidate_latest_unusable_does_not_fall_back(self):
        import datetime as dt
        import research_snapshot as rs
        from unittest import mock
        rows = [{"field":"C5","scope":"I","value":1,"value_id":"OLD","status":rs.ai_fields.STATUS_VERIFIED,
                 "available_at":"2026-01-01T00:00:00+00:00","doc_ids":["D1"]},
                {"field":"C5","scope":"I","value":None,"value_id":"NEW","status":rs.ai_fields.STATUS_VERIFIED,
                 "available_at":"2026-02-01T00:00:00+00:00","doc_ids":["D2"]}]
        with mock.patch.object(rs.ai_fields, "is_current", return_value=True):
            got = rs._candidate_latest_current_ai_rows(
                rows, dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc), [{"doc_id":"D2"}], [])
        self.assertNotIn(("C5", "I"), got)

    def test_common_forward_uses_next_common_close_and_aligned_calendar(self):
        import evaluation_samples as es
        stock = [("2026-01-02",1.0),("2026-01-05",1.1),("2026-01-06",1.2),("2026-01-07",1.3)]
        benchmark = [("2026-01-02",1.0),("2026-01-06",1.1),("2026-01-07",1.21)]
        got = es.common_forward(stock, benchmark, "2026-01-02T01:00:00+00:00", 1)
        self.assertEqual((got["entry_session"], got["end_session"]), ("2026-01-06", "2026-01-07"))
        self.assertAlmostEqual(got["excess_return"], 1.3/1.2 - 1.21/1.1)

    def test_signal_mae_is_daily_relative_path(self):
        import signal_eval as se
        tri = {"T":[("2026-01-02",1.0),("2026-01-05",1.0),("2026-01-06",1.1)],
               "P":[("2026-01-02",1.0),("2026-01-05",0.8),("2026-01-06",1.2)]}
        got = se._relative_path("T", "2026-01-02", 2, tri, ["P"])
        self.assertAlmostEqual(got["mae"], -0.1)
        self.assertAlmostEqual(got["relative_return"], -0.1)

    def test_append_rejects_nonfinite_nonpositive_and_overprecision(self):
        import tempfile
        import pricedata as pd
        base = {"date":"2026-01-01","ticker":"MU","security_id":"S","currency":"USD",
                "close":1,"volume":1,"source":"x","fetched_at":"2026-01-01T00:00:00+00:00"}
        for field, value in (("close", float("nan")), ("close", 0),
                             ("fetched_at", "2026-01-01T00:00:00.1234567+00:00")):
            row = dict(base); row[field] = value
            with self.assertRaisesRegex(ValueError, "INVALID_APPEND_ROW"):
                pd.append_rows(os.path.join(tempfile.mkdtemp(), "x.csv"), pd.PRICE_COLS, [row])

    def test_reconfirmation_and_metadata_completion_are_not_conflicts(self):
        import tempfile, csv
        import pricedata as pd
        path = os.path.join(tempfile.mkdtemp(), "prices.csv")
        with open(path,"w",encoding="utf-8",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=pd.PRICE_COLS);writer.writeheader()
            writer.writerow({"date":"2026-01-01","ticker":"MU","security_id":"","currency":"",
                             "close":100,"volume":1,"source":"old","fetched_at":"2026-01-01T00:00:00+00:00"})
        row={"date":"2026-01-01","ticker":"MU","security_id":"SEC_MU","currency":"USD",
             "close":100,"volume":2,"source":"new","fetched_at":"2026-01-02T00:00:00+00:00"}
        added, conflicts, details = pd.append_rows(path,pd.PRICE_COLS,[row],return_details=True)
        self.assertEqual((added,conflicts),(0,[])); self.assertEqual(details[0]["status"],"METADATA_COMPLETION")

    def test_weekly_status_propagates_manual_and_warn(self):
        import weekly_refresh as wr
        self.assertEqual(wr._event_step_status({"companies":[{"status":"MANUAL_REQUIRED"}]},{}),"NEEDS_MANUAL")
        self.assertEqual(wr._preview_step_status({"classification_changes":[1],"gaps":[]}),"WARN")
        self.assertEqual(wr._worst_status(["PASS","WARN","NEEDS_MANUAL"]),"NEEDS_MANUAL")

    def test_small_sample_binomial_is_exact_and_insufficient_is_unreported(self):
        import risk
        p, method = risk.proportion_p(2, 2, 0.05)
        self.assertEqual(method, "EXACT_BINOMIAL"); self.assertAlmostEqual(p, 0.0025)
        self.assertEqual(risk.proportion_p(0, 0, 0.05), (None, "INSUFFICIENT_SAMPLE"))

    def test_document_program_matrix_covers_t42_required_semantics(self):
        text = pathlib.Path(ROOT, "00_規範", "文件程式對照表.md").read_text(encoding="utf-8")
        for required in ("待查證", "觀望：事件前", "X_ZERO_NO_DISCLOSURE", "家數廣度",
                         "I2Q", "雙抽取", "MU", "PB"):
            self.assertIn(required, text)

    def test_report_only_eps_audit_is_bounded_and_complete(self):
        path = pathlib.Path(ROOT, "90_各AI工作區", "codex", "T42_EPS股數敏感度_2026-10-19.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["tracked_company_count"], 16)
        self.assertEqual(report["threshold"], 0.02)
        self.assertTrue(report["findings"])
        self.assertTrue(all(x["share_count_range_pct"] > report["threshold"] for x in report["findings"]))
        self.assertTrue(all(x["v1_classification_impact"] == "NOT_COMPUTED_NO_RULE_CHANGE_AUTHORITY"
                            for x in report["findings"]))

    def test_t43_user_decision_was_recorded_verbatim(self):
        text = pathlib.Path(ROOT, "00_規範", "使用者決定紀錄.md").read_text(encoding="utf-8")
        self.assertIn("## 2026-10-19　外部審查分流：使用者採 Claude 建議", text)
        self.assertIn("細節授權 Claude 代決；正式啟用仍須 Claude 依實際差異複查＋hash 綁定。", text)

    def test_t43_alternative_score_is_conservatively_capped(self):
        import t43_candidates as c
        self.assertEqual(c.conservative_alt_score(0.5), 0)
        self.assertEqual(c.conservative_alt_score(1.0), 0)
        self.assertEqual(c.conservative_alt_score(1.51), -1)

    def test_t43_short_history_requires_and_uses_36_months(self):
        import t43_candidates as c
        months = [{"month": f"2023-{i%12+1:02d}-{i:02d}", "status": "VALID", "multiple": i + 1}
                  for i in range(36)]
        got = c.short_history_pe({"current": {"denominator": {"value": 2}, "multiple": 20}, "months": months})
        self.assertEqual((got["status"], got["used_months"]), ("OK", 36))
        self.assertLessEqual(got["score"], 0)

    def test_t43_ev_sales_never_uses_a_shares_proxy(self):
        import t43_candidates as c
        got = c.ev_sales_eligibility(revenue_quarters=12, shares_history=0, debt_history=12, cash_history=12)
        self.assertFalse(got["eligible"])
        self.assertIn("SHARES_OUTSTANDING_HISTORY_UNDER_12_QUARTERS", got["missing"])

    def test_t43_tsm_adr_conversion_formula(self):
        import t43_candidates as c
        self.assertAlmostEqual(c.tsm_adr_eps(86.26819537315828, 31.94, 5), 13.504726889974682)

    def test_t43_leave_one_out_insufficient_is_null(self):
        import t43_candidates as c
        got = c.leave_one_out({"TSM": 1, "GFS": None, "2303": None}, "TSM", minimum_comparable=1)
        self.assertIsNone(got["value"])
        self.assertEqual(got["status"], "DATA_INSUFFICIENT_LEAVE_ONE_OUT")

    def test_t43_monthly_available_at_uses_later_timestamp(self):
        import t43_candidates as c
        self.assertEqual(c.monthly_available_at("2026-09", "2026-10-01T00:00:00+08:00"),
                         "2026-10-10T23:59:59+08:00")
        self.assertEqual(c.monthly_available_at("2026-09", "2026-10-20T00:00:00+08:00"),
                         "2026-10-20T00:00:00+08:00")

    def test_t43_c1m_requires_three_consecutive_months(self):
        import t43_candidates as c
        base = [{"data_month": "2026-07", "revenue": 110, "revenue_prior_year": 100,
                 "available_at": "2026-08-10T23:59:59+08:00"},
                {"data_month": "2026-08", "revenue": 120, "revenue_prior_year": 100,
                 "available_at": "2026-09-10T23:59:59+08:00"},
                {"data_month": "2026-09", "revenue": 130, "revenue_prior_year": 100,
                 "available_at": "2026-10-10T23:59:59+08:00"}]
        got = c.c1m_three_month_yoy(base)
        self.assertEqual(got["status"], "OK")
        self.assertAlmostEqual(got["value"], 20.0)

    def test_t43_c6_same_family_warning_is_nonblocking(self):
        import t43_candidates as c
        a = {"field": "C6", "target_period": "2026-09-30", "extractor_model_family": "OPENAI"}
        self.assertEqual(c.heterogeneity_warnings(a, dict(a)), ["HETEROGENEITY_WARNING"])
        b = dict(a, extractor_model_family="ANTHROPIC")
        self.assertEqual(c.heterogeneity_warnings(a, b), [])

    def test_t43_candidate_artifacts_are_explicitly_nonformal(self):
        out = pathlib.Path(ROOT, "90_各AI工作區", "codex")
        candidate = json.loads((out / "T43_rules_1.13.0_candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate["protocol_version"], "1.13.0")
        self.assertTrue(candidate["candidate_only"])
        self.assertFalse(candidate["activated"])
        for gate in ("G-V1-ALT", "G-T-SELF", "G-C1-TWM", "G-S4-HET"):
            files = list(out.glob(f"T43_{gate}_影子差異_2026-10-19.json"))
            self.assertEqual(len(files), 1)
            self.assertTrue(json.loads(files[0].read_text(encoding="utf-8"))["candidate_only"])

    def test_t43_monthly_shadow_rows_never_change_classification(self):
        path = pathlib.Path(ROOT, "04_回測", "weekly_refresh", "t43_monthly_revenue_shadow.jsonl")
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertTrue(rows)
        self.assertTrue(all(x["classification_effect"] is None and x["candidate_only"] for x in rows))

    def test_t44_formal_rules_activate_only_approved_gates(self):
        live = pathlib.Path(ROOT, "00_規範", "rules.json")
        rules = json.loads(live.read_text(encoding="utf-8"))
        archived_live = pathlib.Path(ROOT, "00_規範", "rules_歷史",
                                     f"rules_{rules['protocol_version']}.json")
        self.assertEqual(live.read_bytes(), archived_live.read_bytes())
        self.assertTrue(rules["v1_alternative"]["enabled"])
        self.assertEqual(rules["v1_alternative"]["score_cap"], 0)
        self.assertTrue(rules["c6_heterogeneous_extraction"]["enabled"])
        self.assertFalse(rules["theme_leave_one_out"]["enabled"])
        self.assertFalse(rules["c1_monthly_revenue_shadow"]["enabled"])
        comparison = json.loads(pathlib.Path(
            ROOT, "90_各AI工作區", "codex", "T44_rules_1.13.0_frozen_comparison_2026-10-20.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(comparison["gate"], "PASS")
        self.assertEqual({(x["ticker"], x["before"], x["after"]) for x in comparison["classification_changes"]},
                         {("3081", "資料不足", "等待"), ("COHR", "資料不足", "可列入研究")})
        self.assertEqual(comparison["theme_changes"], [])
        self.assertEqual(comparison["event_distance_changes"], [])


class T45RulesActivationAndEVSalesDiagnostic(unittest.TestCase):
    def test_rules_1131_activation_is_zero_semantic_change(self):
        rules_path = pathlib.Path(ROOT, "00_規範", "rules.json")
        version = json.loads(rules_path.read_text(encoding="utf-8"))["protocol_version"]
        archive_path = pathlib.Path(ROOT, "00_規範", "rules_歷史", f"rules_{version}.json")
        self.assertEqual(rules_path.read_bytes(), archive_path.read_bytes())
        comparison = json.loads(pathlib.Path(
            ROOT, "90_各AI工作區", "codex", "T45_rules_1.13.1_frozen_comparison_2026-10-20.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(comparison["gate"], "PASS")
        self.assertEqual(comparison["new_null_fields"], [])
        self.assertEqual(comparison["classification_changes"], [])
        self.assertEqual(comparison["theme_changes"], [])
        self.assertEqual(comparison["event_distance_changes"], [])

    def test_ladder_attempts_ev_sales_only_after_short_pe_fails(self):
        import t43_candidates as c
        primary = {"current": {"denominator": {"value": -1}}, "months": []}
        ev = {"value": 1.8, "status": "OK", "method": "V1_ALT_EV_SALES",
              "score": -1, "valid_months": 46}
        got = c.diagnose_v1_ladder(primary, ev)
        self.assertEqual(got["method"], "V1_ALT_EV_SALES")
        self.assertEqual([x["method"] for x in got["attempts"]],
                         ["V1_ALT_SHORT_HISTORY", "V1_ALT_EV_SALES"])
        primary_ok = {"current": {"denominator": {"value": 2}, "multiple": 20},
                      "months": [{"month": str(i), "status": "VALID", "multiple": i + 1}
                                 for i in range(36)]}
        got = c.diagnose_v1_ladder(primary_ok, ev)
        self.assertEqual(got["method"], "V1_ALT_SHORT_HISTORY")
        self.assertEqual(len(got["attempts"]), 1)

    def test_missing_balance_inputs_are_not_zero(self):
        import datetime as dt
        import valuation_v1 as v
        from unittest import mock
        revenue = {str(i): {"period_type": "Q", "period_end": f"2025-0{i}-28",
                            "value": 100, "observation_id": f"R{i}"}
                   for i in range(1, 5)}
        shares = {"period_end": "2025-04-30", "value": 10, "observation_id": "S",
                  "filed_date": "2025-04-30"}
        def latest(_rows, metric, *_args):
            return shares if metric == "VER_SHARES_OUTSTANDING" else None
        with mock.patch.object(v, "_visible", return_value=revenue), \
             mock.patch.object(v, "_latest_instant", side_effect=latest):
            got = v._ev_sales_components([], "ISSUER_X",
                                         dt.datetime(2025, 5, 1, tzinfo=dt.timezone.utc),
                                         "2025-04-30", 10, [])
        self.assertIsNone(got["value"])
        self.assertIn("CASH_UNAVAILABLE", got["reason"])
        self.assertIn("DEBT_UNAVAILABLE", got["reason"])

    def test_ev_sales_is_wired_only_through_approved_ladder(self):
        source = pathlib.Path(ROOT, "03_工具", "research_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("compute_ev_sales_alternative", source)
        report = json.loads(pathlib.Path(
            ROOT, "90_各AI工作區", "codex", "T45b_EV-Sales正式路徑凍結比較_2026-10-20.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(report["gate"], "PASS")
        self.assertEqual({x["ticker"] for x in report["valuation_changes"]}, {"MRVL", "LITE"})
        verdict_changes = {(x["ticker"], x["before"]["verdict"], x["after"]["verdict"])
                           for x in report["classification_changes"]
                           if x["before"]["verdict"] != x["after"]["verdict"]}
        self.assertEqual(verdict_changes, {("LITE", "資料不足", "等待")})
        self.assertEqual(report["theme_changes"], [])
        self.assertEqual(report["event_distance_changes"], [])


class T44CurrentnessAndCoverageReason(unittest.TestCase):
    def _doc(self, doc_id, issuer, period, doc_type="10-Q"):
        return {"doc_id": doc_id, "issuer_id": issuer, "doc_type": doc_type,
                "report_period_end": period, "available_at": period + "T23:59:59+00:00"}

    def test_field_specific_currentness(self):
        import ai_fields
        docs = [self._doc("OLD", "ISSUER_X", "2026-03-31"),
                self._doc("NEW", "ISSUER_X", "2026-06-30")]
        c5 = {"value_id": "V1", "field": "C5", "scope": "ISSUER_X", "doc_ids": ["NEW"]}
        self.assertTrue(ai_fields.is_current_for_field(c5, docs, []))
        self.assertFalse(ai_fields.is_current_for_field(dict(c5, doc_ids=["OLD"]), docs, []))
        i2 = dict(c5, field="I2")
        self.assertTrue(ai_fields.is_current_for_field(i2, docs, []))
        self.assertTrue(ai_fields.is_current_for_field(dict(i2, doc_ids=["OLD", "NEW"]), docs, []))

    def test_c6_keeps_two_report_window_semantics(self):
        import ai_fields
        docs = [self._doc("OLD", "ISSUER_X", "2026-03-31"),
                self._doc("NEW", "ISSUER_X", "2026-06-30")]
        c6 = {"value_id": "V2", "field": "C6", "scope": "ISSUER_X", "doc_ids": ["OLD", "NEW"]}
        self.assertEqual(ai_fields.is_current_for_field(c6, docs, []), ai_fields.is_current(c6, docs, []))

    def test_bad_coverage_timestamp_returns_explicit_reason(self):
        import data_spine
        sessions = [{"session_id": "US_2026-10-19", "market": "US", "session_date": "2026-10-19",
                     "open_at": "2026-10-19T09:30:00-04:00", "close_at": "2026-10-19T16:00:00-04:00",
                     "session_status": "OPEN", "calendar_version_id": "T"},
                    {"session_id": "US_2026-10-20", "market": "US", "session_date": "2026-10-20",
                     "open_at": "2026-10-20T09:30:00-04:00", "close_at": "2026-10-20T16:00:00-04:00",
                     "session_status": "OPEN", "calendar_version_id": "T"}]
        record = {"event_id": "COV_BAD", "record_type": "COVERAGE", "checked_at": "not-a-time",
                  "coverage_start_session_id": "US_2026-10-19", "coverage_end_session_id": "US_2026-10-20"}
        covered, reason = data_spine._coverage_record_result(
            record, "US_2026-10-19", "US_2026-10-20", "US", sessions,
            data_spine.parse_time("2026-10-20T20:00:00+00:00"), 7)
        self.assertFalse(covered)
        self.assertIn("EVENT_TIMESTAMP_INVALID:COV_BAD", reason)

    def test_t44_sec_gap_fill_artifact_and_postdata_results(self):
        root = pathlib.Path(ROOT)
        sec_report = json.loads((root / "90_各AI工作區" / "codex" /
                                 "T44_SEC資料補洞_2026-10-20.json").read_text(encoding="utf-8"))
        self.assertEqual(sec_report["gate"], "PASS")
        self.assertEqual(sec_report["total_new_rows"], 85)
        self.assertEqual(sec_report["tsm"]["new_rows"], 16)
        for row in sec_report["tsm"]["overlaps"]:
            self.assertLessEqual(abs(float(row["difference_pct"])), 0.5)

        v1_report = json.loads((root / "90_各AI工作區" / "codex" /
                                "T44_V1資料補洞結果_2026-10-20.json").read_text(encoding="utf-8"))
        rows = v1_report["rows"]
        self.assertEqual(rows["TSM"]["status"], "OK")
        self.assertGreaterEqual(rows["TSM"]["valid_months"], 48)
        self.assertEqual(rows["3081"]["method"], "V1_ALT_SHORT_HISTORY")
        self.assertLessEqual(rows["3081"]["score_cap"], 0)

    def test_t44_sec_pit_share_overrides(self):
        import valuation_v1
        for issuer_id in ("ISSUER_MARVELL", "ISSUER_LUMENTUM"):
            tag = valuation_v1.ISSUER_METRIC_OVERRIDES[issuer_id]["VER_SHARES_OUTSTANDING"]
            self.assertEqual(tag, ("dei", "EntityCommonStockSharesOutstanding", "shares", "INSTANT"))


def load_tests(loader, tests, pattern):
    """納入 S4 與自動迴圈測試，並以外層 suite 防止整個流程污染任何正式帳本。"""
    tests.addTests(loader.loadTestsFromName("test_ai_fields"))
    tests.addTests(loader.loadTestsFromName("test_auto_loop"))
    tests.addTests(loader.loadTestsFromName("test_earnings_docs_watch"))
    return _FormalLedgerGuardSuite([tests])

if __name__ == "__main__":
    unittest.main(verbosity=1)
