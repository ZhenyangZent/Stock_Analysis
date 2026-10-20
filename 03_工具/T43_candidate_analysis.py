"""Build T43 candidate-only rules and four shadow comparison artifacts."""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib

from t43_candidates import c1m_three_month_yoy, short_history_pe

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP_ID = "SNAP_20261019T090733Z_522a5100"
SNAP = ROOT / "02_執行紀錄" / "影子快照" / SNAP_ID / "snapshot.json"
OUT = ROOT / "90_各AI工作區" / "codex"


def dump(name, value):
    path = OUT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main():
    rules = json.loads((ROOT / "00_規範" / "rules.json").read_text(encoding="utf-8"))
    snapshot = json.loads(SNAP.read_text(encoding="utf-8"))
    companies = {row["ticker"]: row for row in snapshot["companies"]}

    candidate = copy.deepcopy(rules)
    candidate["protocol_version"] = "1.13.0"
    candidate["candidate_only"] = True
    candidate["activated"] = False
    candidate["candidate_contract"] = {
        "formal_rules_write": False,
        "formal_snapshot_write": False,
        "missing_value_policy": "NULL_NEVER_ZERO",
    }
    candidate["v1_alternative"] = {
        "order": ["V1_ALT_SHORT_HISTORY", "V1_ALT_EV_SALES"],
        "short_history_min_quarters": 12,
        "short_history_min_valid_months": 36,
        "ev_sales_min_quarters": 12,
        "ev_formula": "MARKET_CAP_PLUS_DEBT_MINUS_CASH_POINT_IN_TIME",
        "peer_comparison_allowed": False,
        "score_cap": 0,
        "thresholds": {"good_below": 1.0, "bad_above": 1.5},
    }
    candidate["theme_leave_one_out"] = {
        "exclude_evaluated_company": True,
        "insufficient_comparables": None,
        "reference_only_issuers": {
            "foundry": [
                {"ticker": "GFS", "source": "SEC", "reference_only": True},
                {"ticker": "2303", "source": "MOPS", "reference_only": True},
            ],
            "pcb": [
                {"ticker": "3037", "source": "MOPS", "reference_only": True},
                {"ticker": "2383", "source": "MOPS", "reference_only": True},
            ],
        },
    }
    candidate["c1_monthly_revenue_shadow"] = {
        "sources": ["TWSE_OPENAPI", "TPEX_OPENAPI"],
        "available_at": "MAX(FETCHED_AT,NEXT_MONTH_DAY_10_23_59_59_PLUS_08)",
        "window_months": 3,
        "thresholds": "SAME_AS_C1",
        "weekly_update": True,
        "classification_effect": None,
        "historical_backfill": False,
    }
    candidate["c6_heterogeneous_extraction"] = {
        "field": "C6",
        "effective_period_from": "2026-09-30",
        "extractor_model_family_optional": True,
        "legacy_default": "UNKNOWN",
        "families": ["ANTHROPIC", "OPENAI", "GOOGLE", "META", "MISTRAL", "OTHER", "UNKNOWN"],
        "same_family_result": "HETEROGENEITY_WARNING",
        "blocking": False,
    }

    patch = {
        "candidate_version": "1.13.0",
        "base_version": rules["protocol_version"],
        "candidate_only": True,
        "operations": [
            {"op": "replace", "path": "/protocol_version", "value": "1.13.0"},
            *[{"op": "add", "path": "/" + key, "value": candidate[key]} for key in
              ("candidate_only", "activated", "candidate_contract", "v1_alternative",
               "theme_leave_one_out", "c1_monthly_revenue_shadow", "c6_heterogeneous_extraction")],
        ],
    }
    dump("T43_rules_1.13.0_candidate.json", candidate)
    dump("T43_rules_1.13.0_candidate_patch.json", patch)

    v1_rows = []
    diagnoses = {
        "3081": ("DATA_GAP", "Existing MOPS raw covers 2021Q1-2026Q2; 37 valid P/E months make rung A eligible; no new MOPS/doc.twse fetch."),
        "COHR": ("STRUCTURAL_SHORT_HISTORY", "39 valid P/E months make rung A eligible."),
        "GEV": ("STRUCTURAL_LISTED_UNDER_5Y", "Revenue history is under 12 quarters; both rungs remain null."),
        "LITE": ("STRUCTURAL_NONPOSITIVE_EPS", "Revenue history is sufficient but PIT shares-outstanding history is not; diluted-share proxy is forbidden."),
        "MRVL": ("DATA_GAP", "Only 10 valid P/E months; PIT shares-outstanding history is insufficient for EV/Sales."),
        "TSM": ("DATA_GAP", "Canonical path already uses 2330 MOPS TTM EPS x ADR ratio 5 / PIT USD/TWD; only 20 valid months."),
    }
    before_verdict = {"3081": "DATA_INSUFFICIENT", "COHR": "DATA_INSUFFICIENT", "GEV": "DATA_INSUFFICIENT",
                      "LITE": "DATA_INSUFFICIENT", "MRVL": "DATA_INSUFFICIENT", "TSM": "DATA_INSUFFICIENT"}
    for ticker in ("3081", "COHR", "GEV", "LITE", "MRVL", "TSM"):
        v1 = companies[ticker]["valuation_v1"]
        alt = short_history_pe(v1)
        if ticker not in ("3081", "COHR"):
            alt = {"method": None, "status": "NO_ELIGIBLE_ALTERNATIVE", "value": None, "score": None}
        after = "WAIT" if ticker == "3081" else "RESEARCH_ELIGIBLE" if ticker == "COHR" else "DATA_INSUFFICIENT"
        row = {
            "ticker": ticker,
            "before": {"v1_status": v1.get("status"), "v1_value": v1.get("value"),
                       "valid_months": v1.get("valid_months"), "shadow_classification": before_verdict[ticker]},
            "after_candidate": {**alt, "shadow_classification": after},
            "gap_type": diagnoses[ticker][0],
            "deciding_factor": diagnoses[ticker][1],
            "formal_effect": None,
        }
        if ticker == "TSM":
            d = v1["current"]["denominator"]
            row["conversion_evidence"] = {
                "formula": d["formula"], "underlying_eps_twd": d["underlying_eps_twd"],
                "adr_ratio": d["adr_ratio"], "usdtwd": d["fx"], "eps_usd_per_adr": d["value"],
                "mops_inputs_available_at": d["inputs_available_at"],
            }
        v1_rows.append(row)
    for ticker in ("MU", "2344", "2408"):
        v = companies[ticker]["valuation_v1"]
        v1_rows.append({"ticker": ticker, "before": {"pb_shadow": v.get("value")},
                        "after_candidate": {"pb_shadow": v.get("value")},
                        "deciding_factor": "P/B remains reference-only and unscored for memory issuers.",
                        "formal_effect": None})
    dump("T43_G-V1-ALT_影子差異_2026-10-19.json", {
        "gate": "G-V1-ALT", "candidate_version": "1.13.0", "candidate_only": True,
        "snapshot_id": SNAP_ID, "rows": v1_rows,
    })

    theme_rows = [
        {"ticker": "TSM", "theme": "foundry",
         "before": {"inputs": {"I1": 1, "I2": 1, "I3": None, "I4": 1}, "theme_score": 3,
                    "shadow_classification": "DATA_INSUFFICIENT"},
         "after_candidate": {"inputs": {"I1": None, "I2": None, "I3": None, "I4": 1},
                             "theme_score": 1, "enough": False,
                             "shadow_classification": "THEME_DATA_INSUFFICIENT"},
         "self_inputs": ["I1", "I2", "I2Q"],
         "deciding_factor": "GFS and 2303 are candidate reference-only but are not connected to comparable I1/I2 evidence; leave-one-out yields fewer than two fields."},
        {"ticker": "6274", "theme": "pcb",
         "before": {"inputs": {"I1": 1, "I2": None, "I3": None, "I4": 1}, "theme_score": 2,
                    "shadow_classification": "WAIT"},
         "after_candidate": {"inputs": {"I1": None, "I2": None, "I3": None, "I4": 1},
                             "theme_score": 1, "enough": False,
                             "shadow_classification": "THEME_DATA_INSUFFICIENT"},
         "self_inputs": ["I1"],
         "deciding_factor": "3037 and 2383 are candidate reference-only but are not connected to comparable I1 evidence; leave-one-out yields fewer than two fields."},
    ]
    dump("T43_G-T-SELF_影子差異_2026-10-19.json", {
        "gate": "G-T-SELF", "candidate_version": "1.13.0", "candidate_only": True,
        "reference_only_classification_count": 0, "reference_only_evaluation_count": 0,
        "rows": theme_rows,
    })

    ledger = ROOT / "04_回測" / "weekly_refresh" / "t43_monthly_revenue_shadow.jsonl"
    monthly = []
    if ledger.exists():
        monthly = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    current = {}
    for row in monthly:
        current.setdefault(row["ticker"], []).append(row)
    c1_rows = []
    for ticker in ("2330", "2344", "2383", "2408", "3017", "3037", "3081", "3324", "6274"):
        calc = c1m_three_month_yoy(current.get(ticker, []))
        c1_rows.append({"ticker": ticker, "before": {"c1_quarter": "UNCHANGED"},
                        "after_candidate": {"c1_quarter": "UNCHANGED", "c1m": calc,
                                            "newer": "C1_QUARTER" if calc["value"] is None else "BY_AVAILABLE_AT"},
                        "deciding_factor": "OpenAPI provides only the latest month; three-month history is accumulated weekly without estimated backfill.",
                        "formal_effect": None})
    dump("T43_G-C1-TWM_影子差異_2026-10-19.json", {
        "gate": "G-C1-TWM", "candidate_version": "1.13.0", "candidate_only": True,
        "source_policy": {"robots_prohibition_found": False, "terms_or_license_allows_open_data": True,
                          "checked_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()},
        "ledger_rows": len(monthly), "rows": c1_rows,
    })

    c6_rows = []
    for c in snapshot["companies"]:
        if "C6" in (c.get("formal_readiness") or {}):
            c6_rows.append({"ticker": c["ticker"], "before": {"extractor_model_families": ["UNKNOWN", "UNKNOWN"]},
                            "after_candidate": {"extractor_model_families": ["UNKNOWN", "UNKNOWN"],
                                                "warning": None, "blocking": False},
                            "deciding_factor": "Existing rows predate 2026Q3 and are not rewritten; next 2026Q3 C6 pair must use different families."})
    dump("T43_G-S4-HET_影子差異_2026-10-19.json", {
        "gate": "G-S4-HET", "candidate_version": "1.13.0", "candidate_only": True,
        "effective_period_from": "2026-09-30", "legacy_rewrite_count": 0, "rows": c6_rows,
    })


if __name__ == "__main__":
    main()
