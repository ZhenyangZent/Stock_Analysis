#!/usr/bin/env python3
"""Build deterministic MARKET-78 receipts with source-semantic bindings."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
FIXTURES = PAYLOAD / "FIXTURES"
EVIDENCE = PAYLOAD / "EVIDENCE_BYTES"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha(value) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def write_json(relative: str, value: dict) -> dict:
    path = EVIDENCE / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return {"path": relative.replace("\\", "/"), "sha256": sha_bytes(raw), "bytes": len(raw)}


def observation(key, value, unit, source_id, source_receipt_id, schedule_receipt_id, source_sha, schedule_sha, published, available, fetched, policy="N/A"):
    return {
        "observation_key": key,
        "observation_date": key if len(key) == 10 else f"{key}-01",
        "calendar_key": key[:7],
        "value": value,
        "unit": unit,
        "vintage_policy": policy,
        "source_id": source_id,
        "source_receipt_id": source_receipt_id,
        "schedule_receipt_id": schedule_receipt_id,
        "source_payload_sha256": source_sha,
        "schedule_payload_sha256": schedule_sha,
        "source_published_at": published,
        "available_at": available,
        "fetched_at": fetched,
    }


def make_bundle() -> tuple[dict, dict[str, dict], dict[str, dict]]:
    cutoff = "2026-02-21T00:00:00Z"
    fetched = "2026-02-20T23:30:00Z"
    specs = {
        "M1": ("FED_FOMC", "FED_FOMC_MEETING_CALENDAR", "2026-01-28", "2026-01-28T19:00:00Z", "2026-01-28T19:00:05Z", "不變"),
        "M2": ("FRED_DGS10", "FED_H15_RELEASE_CALENDAR", "2026-02-19", "2026-02-20T21:15:00Z", "2026-02-20T21:20:00Z", 4.25),
        "M3": ("FRED_BRENT", "EIA_SPOT_PRICE_RELEASE_CALENDAR", "2026-02-18", "2026-02-20T18:00:00Z", "2026-02-20T18:05:00Z", 73.2),
        "M4": ("FRED_UNRATE_REVISED", "BLS_EMPLOYMENT_SITUATION_CALENDAR", "2026-01", "2026-02-06T13:30:00Z", "2026-02-06T13:35:00Z", 4.1),
        "M5": ("FRED_CPILFESL_REVISED", "BLS_CPI_RELEASE_CALENDAR", "2026-01", "2026-02-11T13:30:00Z", "2026-02-11T13:35:00Z", 340.1),
        "M6": ("TIINGO_SOXX", "XNYS_XNAS_COMPLETE_SESSION_CALENDAR", "2026-02-20", "2026-02-20T21:00:00Z", "2026-02-20T22:00:00Z", 510.0),
        "M7": ("FRED_HY_OAS", "ICE_BOFA_DAILY_CLOSE_RELEASE_CALENDAR", "2026-02-19", "2026-02-20T15:00:00Z", "2026-02-20T15:05:00Z", 3.1),
    }
    units = {
        "M1": "DECISION_ENUM",
        "M2": "PERCENT",
        "M3": "USD_PER_BARREL",
        "M4": "PERCENT",
        "M5": "INDEX_1982_1984_100",
        "M6": "USD_INDEX_LEVEL",
        "M7": "PERCENTAGE_POINTS",
    }
    source_values = {
        "M1": [("2026-01-28", "不變", "N/A")],
        "M2": [("2026-02-19", 4.25, "N/A")],
        "M3": [("2026-02-18", 73.2, "N/A")],
        "M4": [("2026-01", 4.1, "REVISED"), ("2025-10", 4.2, "REVISED")],
        "M5": [("2026-01", 340.1, "REVISED"), ("2025-01", 330.0, "REVISED"), ("2025-10", 338.0, "REVISED"), ("2024-10", 328.5, "REVISED")],
        "M6": [("2026-02-20", 510.0, "N/A")],
        "M7": [("2026-02-19", 3.1, "N/A")],
    }
    source_response_refs = {}
    schedule_payload_refs = {}
    schedule_receipt_refs = {}
    source_receipt_refs = {}
    indicators = []
    source_receipts = {}
    schedule_receipts = {}

    for indicator_id, (source_id, calendar_id, expected, published, available, value) in specs.items():
        source_rows = [{
            "observation_key": key,
            "observation_date": key if len(key) == 10 else f"{key}-01",
            "calendar_key": key[:7],
            "value": row_value,
            "unit": units[indicator_id],
            "source_id": source_id,
            "vintage_policy": policy,
        } for key, row_value, policy in source_values[indicator_id]]
        response_ref = write_json(f"raw_sources/{source_id}.json", {
            "adapter_id": "SYNTHETIC_MARKET_RECORDS_V1",
            "schema_version": "1.0.0",
            "source_id": source_id,
            "synthetic": True,
            "records": source_rows,
        })
        calendar_ref = write_json(f"raw_schedules/{indicator_id}.json", {
            "calendar_id": calendar_id,
            "indicator_id": indicator_id,
            "source_id": source_id,
            "latest_expected_period": expected,
            "latest_expected_published_at": published,
            "synthetic": True,
        })
        source_response_refs[source_id] = response_ref
        schedule_payload_refs[indicator_id] = calendar_ref
        schedule_receipt_id = f"SCHEDULE-{indicator_id}-SYNTHETIC"
        schedule_receipt = {
            "receipt_id": schedule_receipt_id,
            "status": "COMPLETE",
            "indicator_id": indicator_id,
            "source_id": source_id,
            "calendar_id": calendar_id,
            "latest_expected_period": expected,
            "latest_expected_published_at": published,
            "derived_at": fetched,
            "calendar_artifact": calendar_ref,
        }
        schedule_ref = write_json(f"schedule_receipts/{indicator_id}.json", schedule_receipt)
        schedule_ref["schedule_receipt_id"] = schedule_receipt_id
        schedule_receipt_refs[indicator_id] = schedule_ref
        schedule_receipts[indicator_id] = schedule_receipt

    observation_map = {}
    for indicator_id, (source_id, _, expected, published, available, value) in specs.items():
        source_receipt_id = f"SOURCE-{source_id}-SYNTHETIC"
        schedule_receipt_id = f"SCHEDULE-{indicator_id}-SYNTHETIC"
        source_sha = source_response_refs[source_id]["sha256"]
        schedule_sha = schedule_payload_refs[indicator_id]["sha256"]
        rows = [observation(key, row_value, units[indicator_id], source_id, source_receipt_id, schedule_receipt_id, source_sha, schedule_sha, published, available, fetched, policy)
                for key, row_value, policy in source_values[indicator_id]]
        observation_map[indicator_id] = rows
        source_payload = json.loads((EVIDENCE / source_response_refs[source_id]["path"]).read_text(encoding="utf-8"))
        source_receipt = {
            "receipt_id": source_receipt_id,
            "source_id": source_id,
            "adapter_id": "SYNTHETIC_MARKET_RECORDS_V1",
            "requested_items": len(rows),
            "succeeded_items": len(rows),
            "failed_items": 0,
            "source_published_at": published,
            "available_at": available,
            "fetched_at": fetched,
            "response_artifact": source_response_refs[source_id],
            "rowset_sha256": canonical_sha(rows),
            "canonical_source_rowset_sha256": canonical_sha(source_payload["records"]),
        }
        source_ref = write_json(f"source_receipts/{source_id}.json", source_receipt)
        source_ref["source_id"] = source_id
        source_receipt_refs[source_id] = source_ref
        source_receipts[source_id] = source_receipt

    for indicator_id, (source_id, _, expected, _, _, _) in specs.items():
        item = {
            "indicator_id": indicator_id,
            "source_id": source_id,
            "latest_expected_period": expected,
            "latest_observed_period": expected,
            "missing_expected_periods": [],
            "schedule_receipt_ref": schedule_receipt_refs[indicator_id],
            "observations": observation_map[indicator_id],
        }
        if indicator_id == "M1":
            item["publication_receipt_present"] = True
        if indicator_id == "M4":
            item["vintage_policy"] = "REVISED"
            item["required_calendar_keys"] = {"L": "2026-01", "L_MINUS_3": "2025-10"}
        if indicator_id == "M5":
            item["vintage_policy"] = "REVISED"
            item["required_calendar_keys"] = {"L": "2026-01", "L_MINUS_12": "2025-01", "L_MINUS_3": "2025-10", "L_MINUS_15": "2024-10"}
        if indicator_id == "M6":
            item["price_anchors"] = {"latest_complete_market_session": expected, "price_anchor": expected, "corporate_action_anchor": expected, "total_return_anchor": expected}
        indicators.append(item)

    receipt = {
        "schema_version": "MARKET_SOURCE_SEMANTICS_3.0.0",
        "receipt_id": "SYNTHETIC-SOURCE-SEMANTICS-CURRENT-MARKET-ONLY",
        "receipt_kind": "MARKET_ONLY",
        "synthetic": True,
        "run_cutoff": cutoff,
        "identity": {
            "input_market72_zip_sha256": "6451f00f94d2eb0025eef776edf94dd259aebdfaa4dab5c27f5d2cab579f33ad",
            "rules_sha256": "fe072304ff62092b355d1c9c844227eb3bee1a7abc1475d0888d50291885e4d1"
        },
        "scope": {"dependency_domains": ["MARKET_ONLY"], "t46_document_ledger_required": False},
        "source_receipt_refs": [source_receipt_refs[specs[indicator_id][0]] for indicator_id in specs],
        "indicators": indicators,
    }
    return receipt, source_receipts, schedule_receipts


def by_id(receipt, indicator_id):
    return next(row for row in receipt["indicators"] if row["indicator_id"] == indicator_id)


def source_ref(receipt, source_id):
    return next(row for row in receipt["source_receipt_refs"] if row["source_id"] == source_id)


def build_cases(base, source_receipts, schedule_receipts):
    cases = {"complete_current_market_only.json": base}

    def clone(name, receipt_id):
        value = copy.deepcopy(base)
        value["receipt_id"] = receipt_id
        cases[name] = value
        return value

    case = clone("missing_m5_2025_10.json", "HARDENED-M5-MISSING-2025-10")
    by_id(case, "M5")["observations"] = [row for row in by_id(case, "M5")["observations"] if row["calendar_key"] != "2025-10"]

    case = clone("source_time_after_cutoff.json", "HARDENED-SOURCE-TIME-AFTER-CUTOFF")
    variant = copy.deepcopy(source_receipts["FRED_DGS10"])
    variant["source_published_at"] = "2026-02-21T00:00:01Z"
    ref = write_json("variants/source_time_after_cutoff.json", variant); ref["source_id"] = "FRED_DGS10"
    source_ref(case, "FRED_DGS10").update(ref)

    case = clone("duplicate_observation.json", "HARDENED-DUPLICATE-OBSERVATION")
    by_id(case, "M2")["observations"].append(copy.deepcopy(by_id(case, "M2")["observations"][0]))

    case = clone("source_sha_mismatch.json", "HARDENED-SOURCE-SHA-MISMATCH")
    source_ref(case, "FRED_BRENT")["sha256"] = hashlib.sha256(b"wrong").hexdigest()

    case = clone("missing_trading_day.json", "HARDENED-MISSING-TRADING-DAY")
    by_id(case, "M6")["latest_observed_period"] = "2026-02-19"
    by_id(case, "M6")["missing_expected_periods"] = ["2026-02-20"]
    by_id(case, "M6")["observations"] = []

    case = clone("partial_source_failure.json", "HARDENED-PARTIAL-SOURCE-FAILURE")
    variant = copy.deepcopy(source_receipts["FRED_HY_OAS"]); variant.update(succeeded_items=0, failed_items=1)
    ref = write_json("variants/partial_source_failure.json", variant); ref["source_id"] = "FRED_HY_OAS"
    source_ref(case, "FRED_HY_OAS").update(ref)

    case = clone("price_anchor_mismatch.json", "HARDENED-PRICE-ANCHOR-MISMATCH")
    by_id(case, "M6")["price_anchors"]["corporate_action_anchor"] = "2026-02-19"

    case = clone("missing_m1_event_calendar.json", "HARDENED-M1-EVENT-CALENDAR-MISSING")
    variant = copy.deepcopy(schedule_receipts["M1"]); variant["calendar_id"] = ""
    ref = write_json("variants/missing_m1_event_calendar.json", variant); ref["schedule_receipt_id"] = variant["receipt_id"]
    by_id(case, "M1")["schedule_receipt_ref"] = ref

    case = clone("missing_m1_publication_receipt.json", "HARDENED-M1-PUBLICATION-MISSING")
    by_id(case, "M1")["publication_receipt_present"] = False

    case = clone("conflicting_m4_month.json", "HARDENED-M4-CONFLICTING-MONTH")
    row = copy.deepcopy(by_id(case, "M4")["observations"][1]); row["value"] = 9.9; row["observation_key"] = "2025-10-conflict"
    by_id(case, "M4")["observations"].append(row)

    case = clone("mixed_alfred_revised.json", "HARDENED-MIXED-ALFRED-REVISED")
    by_id(case, "M5")["observations"][3]["vintage_policy"] = "ALFRED_INITIAL"

    case = clone("provider_period_lag.json", "HARDENED-PROVIDER-PERIOD-LAG")
    by_id(case, "M3")["latest_observed_period"] = "2026-02-17"
    by_id(case, "M3")["observations"] = []

    case = clone("t46_dependency_forbidden.json", "HARDENED-T46-DEPENDENCY")
    case["scope"] = {"dependency_domains": ["MARKET_ONLY", "T46_DOCS_LEDGER"], "t46_document_ledger_required": True}

    for indicator_id in (f"M{i}" for i in range(1, 8)):
        case = clone(f"empty_observations_{indicator_id.lower()}.json", f"HARDENED-EMPTY-{indicator_id}")
        by_id(case, indicator_id)["observations"] = []

    case = clone("observation_available_before_published.json", "HARDENED-OBS-TIME-ORDER")
    by_id(case, "M2")["observations"][0]["available_at"] = "2026-02-20T20:00:00Z"

    case = clone("observation_source_time_cross_mismatch.json", "HARDENED-OBS-SOURCE-TIME-CROSS-MISMATCH")
    by_id(case, "M2")["observations"][0]["fetched_at"] = "2026-02-20T23:29:59Z"

    case = clone("observation_source_id_mismatch.json", "HARDENED-OBS-SOURCE-ID-MISMATCH")
    by_id(case, "M2")["observations"][0]["source_id"] = "FRED_BRENT"

    case = clone("synthetic_self_authority.json", "HARDENED-SYNTHETIC-SELF-AUTHORITY")
    case["governance"] = {"formal_rules_code_integrated": True, "pit_vintage_policy_status": "APPROVED_REVISED", "formal_market_data_write_authorized": True, "formal_snapshot_authorized": True, "formal_publication_authorized": True}

    case = clone("unapproved_alfred_policy.json", "HARDENED-UNAPPROVED-ALFRED")
    m5 = by_id(case, "M5"); m5["vintage_policy"] = "ALFRED_INITIAL"
    for row in m5["observations"]: row["vintage_policy"] = "ALFRED_INITIAL"

    return cases


def main():
    if EVIDENCE.exists():
        raise FileExistsError(f"Refusing to replace existing evidence directory: {EVIDENCE}")
    EVIDENCE.mkdir(parents=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    base, source_receipts, schedule_receipts = make_bundle()
    cases = build_cases(base, source_receipts, schedule_receipts)
    for name, value in cases.items():
        (FIXTURES / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"fixture_count": len(cases), "evidence_file_count": sum(1 for path in EVIDENCE.rglob('*') if path.is_file())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
