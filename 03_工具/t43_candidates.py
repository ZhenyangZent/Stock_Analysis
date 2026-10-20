"""T43 candidate-only helpers for rules 1.13.0.

Nothing in this module activates rules or writes a formal snapshot.  Missing
candidate evidence is represented as None; it is never filled with zero.
"""
from __future__ import annotations

import calendar
import datetime as dt
import statistics

CANDIDATE_VERSION = "1.13.0"
ALLOWED_MODEL_FAMILIES = {"ANTHROPIC", "OPENAI", "GOOGLE", "META", "MISTRAL", "OTHER", "UNKNOWN"}


def conservative_alt_score(relative_multiple, *, good_below=1.0, bad_above=1.5):
    """Alternative V1 can penalize expensive valuation but never award +1."""
    if not isinstance(relative_multiple, (int, float)) or isinstance(relative_multiple, bool):
        return None
    if relative_multiple > bad_above:
        return -1
    return 0


def short_history_pe(v1_detail: dict, *, minimum_valid_months=36):
    """Use the newest 36 valid PIT P/E months when current TTM EPS is positive."""
    current = v1_detail.get("current") or {}
    denominator = current.get("denominator") or current.get("ttm") or {}
    if not isinstance(denominator.get("value"), (int, float)) or denominator["value"] <= 0:
        return {"value": None, "status": "NOT_APPLICABLE_EARNINGS", "method": "V1_ALT_SHORT_HISTORY"}
    valid = [m for m in v1_detail.get("months", [])
             if m.get("status") == "VALID" and isinstance(m.get("multiple"), (int, float)) and m["multiple"] > 0]
    valid = sorted(valid, key=lambda x: x.get("month", ""))
    if len(valid) < minimum_valid_months:
        return {"value": None, "status": "DATA_INSUFFICIENT_SHORT_HISTORY",
                "method": "V1_ALT_SHORT_HISTORY", "valid_months": len(valid)}
    used = valid[-minimum_valid_months:]
    current_multiple = current.get("multiple")
    if not isinstance(current_multiple, (int, float)) or current_multiple <= 0:
        return {"value": None, "status": "NOT_APPLICABLE_CURRENT_MULTIPLE", "method": "V1_ALT_SHORT_HISTORY"}
    median = statistics.median(x["multiple"] for x in used)
    value = current_multiple / median
    return {"value": value, "status": "OK", "method": "V1_ALT_SHORT_HISTORY",
            "score": conservative_alt_score(value), "valid_months": len(valid),
            "used_months": minimum_valid_months, "median": median, "current_multiple": current_multiple}


def ev_sales_eligibility(*, revenue_quarters, shares_history, debt_history, cash_history):
    """Evidence gate for the EV/Sales rung; no proxy for shares outstanding is allowed."""
    missing = []
    if revenue_quarters < 12: missing.append("REVENUE_HISTORY_UNDER_12_QUARTERS")
    if shares_history < 12: missing.append("SHARES_OUTSTANDING_HISTORY_UNDER_12_QUARTERS")
    if debt_history < 12: missing.append("DEBT_HISTORY_UNDER_12_QUARTERS")
    if cash_history < 12: missing.append("CASH_HISTORY_UNDER_12_QUARTERS")
    return {"eligible": not missing, "missing": missing, "method": "V1_ALT_EV_SALES"}


def diagnose_v1_ladder(primary_v1: dict, ev_sales_result: dict, *, minimum_valid_months=36):
    """T45 diagnostic-only ladder. It does not activate or score EV/Sales in verdict paths."""
    rung_a = short_history_pe(primary_v1, minimum_valid_months=minimum_valid_months)
    attempts = [{k: rung_a.get(k) for k in ("method", "status", "valid_months")}]
    if rung_a.get("status") == "OK":
        return dict(rung_a, attempts=attempts)
    rung_b = dict(ev_sales_result or {"value": None, "status": "EV_SALES_NOT_COMPUTED", "method": "V1_ALT_EV_SALES"})
    attempts.append({k: rung_b.get(k) for k in ("method", "status", "valid_months")})
    return dict(rung_b, attempts=attempts)


def tsm_adr_eps(underlying_eps_twd, usdtwd_rate, adr_ratio=5):
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
               for x in (underlying_eps_twd, usdtwd_rate, adr_ratio)):
        return None
    return underlying_eps_twd * adr_ratio / usdtwd_rate


def leave_one_out(values: dict, excluded_ticker: str, *, minimum_comparable: int):
    kept = {k: v for k, v in values.items() if k != excluded_ticker and v is not None}
    if len(kept) < minimum_comparable:
        return {"value": None, "n_comparable": len(kept), "status": "DATA_INSUFFICIENT_LEAVE_ONE_OUT"}
    numeric = [v for v in kept.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(numeric) != len(kept):
        return {"value": None, "n_comparable": len(kept), "status": "UNSUPPORTED_INPUT_TYPE"}
    return {"value": sum(numeric), "n_comparable": len(kept), "status": "OK"}


def monthly_available_at(data_month: str, fetched_at: str):
    """Later of actual retrieval and next-month day 10 23:59:59+08:00."""
    year, month = map(int, data_month.split("-"))
    if month == 12: next_year, next_month = year + 1, 1
    else: next_year, next_month = year, month + 1
    deadline = dt.datetime(next_year, next_month, 10, 23, 59, 59,
                           tzinfo=dt.timezone(dt.timedelta(hours=8)))
    fetched = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return max(deadline, fetched).isoformat()


def c1m_three_month_yoy(rows: list):
    """Three consecutive disclosed months, current sum versus prior-year sum."""
    usable = {}
    for row in rows:
        try:
            month = row["data_month"]
            current, prior = float(row["revenue"]), float(row["revenue_prior_year"])
        except (KeyError, TypeError, ValueError):
            continue
        if current >= 0 and prior > 0:
            usable[month] = (current, prior, row)
    if len(usable) < 3:
        return {"value": None, "status": "DATA_INSUFFICIENT_MONTHS", "month_count": len(usable)}
    months = sorted(usable)[-3:]
    parsed = [dt.date.fromisoformat(m + "-01") for m in months]
    expected = []
    cursor = parsed[-1]
    for offset in (2, 1, 0):
        total = cursor.year * 12 + cursor.month - 1 - offset
        expected.append(f"{total // 12:04d}-{total % 12 + 1:02d}")
    if months != expected:
        return {"value": None, "status": "DATA_INSUFFICIENT_CONSECUTIVE_MONTHS", "months": months}
    current_sum = sum(usable[m][0] for m in months); prior_sum = sum(usable[m][1] for m in months)
    return {"value": (current_sum / prior_sum - 1) * 100, "status": "OK", "months": months,
            "current_sum": current_sum, "prior_year_sum": prior_sum,
            "available_at": max(usable[m][2]["available_at"] for m in months)}


def extractor_family(extraction: dict):
    family = extraction.get("extractor_model_family", "UNKNOWN")
    return family if family in ALLOWED_MODEL_FAMILIES else "UNKNOWN"


def heterogeneity_warnings(ex1: dict, ex2: dict):
    if ex1.get("field") != "C6" or ex2.get("field") != "C6":
        return []
    period = max(str(ex1.get("target_period") or ""), str(ex2.get("target_period") or ""))
    if period < "2026-09-30":
        return []
    first, second = extractor_family(ex1), extractor_family(ex2)
    return ["HETEROGENEITY_WARNING"] if first == second else []
