"""S3c：V1 即時計算（版本保留的官方申報＋未調整月底收盤價）。

- 申報資料：SEC companyfacts 的 NetIncomeLoss（歸屬母公司淨利）與 WeightedAverageNumberOfDilutedSharesOutstanding，
  每個申報版本各存一列（metric VER_*；filed 日＝available_at），不覆蓋、不 supersede。fetch_financials 既有行為不變。
- TTM EPS（G-S3-5）：最近四季歸屬母公司淨利合計（季度值由 YTD 相減）÷ 最近一季稀釋加權平均股數；
  股數版本申報日早於分割日、且分割日不晚於價格日時，依 corporate_actions 分割倍數調整股數。不以 YTD 相減推算每股數字。
- 本益比不寫 observations；月度明細與輸入 observation_id／價格列雜湊存在快照。
- 無效月份記 INVALID_MONTH 並計入 60 個月；目前倍數綁該市場分析 session（價格日≠anchor 即缺漏）。
- 規則（60 月、≥48 有效月、最近 4 季為正）沿用 data_spine.calculate_v1，不改門檻。

用法（需持有專案鎖才可 --write）：
  python 03_工具/valuation_v1.py --versions ANET --companyfacts PATH [--write]
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import statistics
import sys
from zoneinfo import ZoneInfo

import data_spine
from data_spine import parse_time, resolve_visible_observation_versions

VERSION = "V1_S3D_0.4"
NOT_APPLICABLE = {
    "GEV": ("NOT_APPLICABLE_LISTED_UNDER_5Y", "GEV 上市未滿五年，無法形成完整 60 個月窗口。"),
}
METRICS = {"VER_NET_INCOME_PARENT": ("us-gaap", "NetIncomeLoss", "USD", "DURATION"),
           "VER_DILUTED_SHARES_WAVG": ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", "DURATION"),
           "VER_EQUITY_PARENT": ("us-gaap", "StockholdersEquity", "USD", "INSTANT"),
           "VER_SHARES_OUTSTANDING": ("us-gaap", "CommonStockSharesOutstanding", "shares", "INSTANT")}
ISSUER_METRIC_OVERRIDES = {
    # AVGO 自 2025 財年起以 ProfitLoss 提供合併淨利；NetIncomeLoss 的季度鏈停在舊期。
    "ISSUER_BROADCOM": {"VER_NET_INCOME_PARENT": ("us-gaap", "ProfitLoss", "USD", "DURATION")},
    # T44-B：封面日期的流通股數是 EV/Sales 所需的 point-in-time 股數；
    # 不得以期間加權平均稀釋股數代替。
    "ISSUER_MARVELL": {"VER_SHARES_OUTSTANDING": ("dei", "EntityCommonStockSharesOutstanding", "shares", "INSTANT")},
    "ISSUER_LUMENTUM": {"VER_SHARES_OUTSTANDING": ("dei", "EntityCommonStockSharesOutstanding", "shares", "INSTANT")},
}
FILING_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
QUARTER_DAYS, H1_DAYS, M9_DAYS, FY_DAYS = (80, 100), (170, 190), (260, 285), (350, 380)
MARKET_CLOSE = {"US": (dt.time(16, 0), "America/New_York"), "TW": (dt.time(13, 30), "Asia/Taipei")}


def _available_at(filed):
    # 與 fetch_financials 相同：SEC companyfacts 只有 filed 日期，用美東當日結束作保守可得時間。
    return filed + "T23:59:59-05:00"


def _days(start, end):
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


def version_rows(companyfacts, issuer_id, cik, ingested_at):
    """companyfacts → 每個申報版本一列 observation。只收 10-Q／10-K（含修正），排除 DEF 14A 等以百萬計的摘錄。"""
    namespaces = companyfacts.get("facts") or {}
    rows = []
    metrics = dict(METRICS); metrics.update(ISSUER_METRIC_OVERRIDES.get(issuer_id, {}))
    for metric_id, (namespace, tag, unit, period_type) in metrics.items():
        facts = namespaces.get(namespace) or {}
        for fact in ((facts.get(tag) or {}).get("units") or {}).get(unit, []):
            if (fact.get("form") not in FILING_FORMS or fact.get("val") is None or
                    not fact.get("end") or (period_type == "DURATION" and not fact.get("start"))):
                continue
            accn = fact["accn"]
            identity = json.dumps([cik, namespace, tag, accn, fact.get("start"), fact["end"], fact["val"]], separators=(",", ":"))
            available = _available_at(fact["filed"])
            rows.append({
                "observation_id": "VER_" + hashlib.sha256(identity.encode()).hexdigest(),
                "issuer_id": issuer_id, "security_id": None, "metric_id": metric_id, "unit": unit,
                "value": float(fact["val"]), "period_start": fact.get("start"), "period_end": fact["end"],
                "period_type": period_type,
                "duration_days": _days(fact["start"], fact["end"]) if fact.get("start") else None,
                "fiscal_year": fact.get("fy"), "fiscal_period": fact.get("fp"), "form": fact["form"],
                "filed_date": fact["filed"], "accession_number": accn,
                "source_system": "SEC_COMPANYFACTS", "source_tag": tag, "taxonomy": namespace,
                "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{accn}-index.html",
                "source_published_at": available, "available_at": available, "ingested_at": ingested_at,
                "availability_status": "POINT_IN_TIME_KNOWN", "time_precision": "DATE_ONLY",
                "version_semantics": "ALL_FILED_VERSIONS_RETAINED", "supersedes_observation_id": None})
    rows.sort(key=lambda r: (r["metric_id"], r["period_end"], r["period_start"], r["filed_date"], r["observation_id"]))
    return rows


def _visible(rows, metric_id, issuer_id, when):
    """when 時點已可得的版本；同一 (start, end) 取申報最新者。"""
    candidates = []
    for r in rows:
        if r.get("issuer_id") != issuer_id or r.get("metric_id") != metric_id: continue
        try:
            if parse_time(r["available_at"], "available_at") > when: continue
        except (KeyError, ValueError):
            continue
        candidates.append(r)
    visible = resolve_visible_observation_versions(rows, candidates)
    best = {}
    for r in visible:
        if r.get("issuer_id") != issuer_id or r.get("metric_id") != metric_id: continue
        key = (r.get("period_start"), r["period_end"])
        rank = (r.get("filed_date") or str(r.get("available_at")), r["observation_id"])
        old_rank = ((best[key].get("filed_date") or str(best[key].get("available_at"))), best[key]["observation_id"]) if key in best else None
        if key not in best or rank > old_rank:
            best[key] = r
    return best


def _in(days, bounds):
    return bounds[0] <= days <= bounds[1]


def quarterly_net_income(ni):
    """{quarter_end: (value, [observation_ids], derivation)}；Q1 直接，Q2–Q4 由同起日的 YTD 相減。"""
    by_start = {}
    for (start, end), r in ni.items():
        by_start.setdefault(start, []).append(r)
    out = {}
    for start, group in by_start.items():
        def row_days(r):
            return r.get("duration_days") if r.get("duration_days") is not None else _days(r["period_start"], r["period_end"])
        pick = lambda b: next((r for r in sorted(group, key=lambda x: x["period_end"]) if _in(row_days(r), b)), None)
        q1, h1, m9, fy = pick(QUARTER_DAYS), pick(H1_DAYS), pick(M9_DAYS), pick(FY_DAYS)
        chain = [(q1, None, "REPORTED_Q1"), (h1, q1, "YTD_H1_MINUS_Q1"), (m9, h1, "YTD_9M_MINUS_H1"), (fy, m9, "FY_MINUS_YTD_9M")]
        for cur, prev, how in chain:
            if cur is None: continue
            if prev is None and how != "REPORTED_Q1": continue
            value = cur["value"] - (prev["value"] if prev else 0.0)
            out[cur["period_end"]] = (value, [cur["observation_id"]] + ([prev["observation_id"]] if prev else []), how)
    return out


def _latest_quarter_shares(sh, quarter_end):
    row_days = lambda r: r.get("duration_days") if r.get("duration_days") is not None else _days(r["period_start"], r["period_end"])
    q = [r for (s, e), r in sh.items() if e == quarter_end and _in(row_days(r), QUARTER_DAYS)]
    rank = lambda r: r.get("filed_date") or str(r.get("available_at"))
    if q: return max(q, key=rank), "REPORTED_QUARTER"
    fy = [r for (s, e), r in sh.items() if e == quarter_end and _in(row_days(r), FY_DAYS)]
    if fy: return max(fy, key=rank), "Q4_SHARES_FY_PROXY"
    return None, "SHARES_UNAVAILABLE"


def split_factor(splits, filed_date, price_date):
    """股數版本申報於分割前、且分割日不晚於價格日 → 股數乘上分割倍數（與未調整價同基準）。"""
    f = 1.0
    for date, value in splits:
        if filed_date < date <= price_date: f *= value
    return f


def ttm_eps(observations, issuer_id, when, price_date, splits, quarter_end=None):
    """回傳 dict：value 或 reason；附輸入 observation_id 與算式元件。"""
    ni = _visible(observations, "VER_NET_INCOME_PARENT", issuer_id, when)
    sh = _visible(observations, "VER_DILUTED_SHARES_WAVG", issuer_id, when)
    # 台股稀釋股數已由官方 PDF 抽取並經雙 AI 複核；缺值不得以股本替代。
    for key, row in _visible(observations, "FIN_DILUTED_SHARES", issuer_id, when).items():
        old = sh.get(key)
        if old is None or str(row.get("available_at")) > str(old.get("available_at")):
            sh[key] = row
    quarters = quarterly_net_income(ni)
    ends = sorted(e for e in quarters if quarter_end is None or e <= quarter_end)
    if len(ends) < 4:
        return {"value": None, "reason": "EPS_QUARTERS_UNAVAILABLE"}
    last4 = ends[-4:]
    for a, b in zip(last4, last4[1:]):
        if not 70 <= _days(a, b) <= 110:
            return {"value": None, "reason": "EPS_QUARTERS_NOT_CONSECUTIVE", "quarters": last4}
    shares_row, shares_basis = _latest_quarter_shares(sh, last4[-1])
    if shares_row is None:
        return {"value": None, "reason": shares_basis, "quarters": last4}
    filed_date = shares_row.get("filed_date") or str(shares_row["available_at"])[:10]
    factor = split_factor(splits, filed_date, price_date)
    ni_sum = sum(quarters[e][0] for e in last4)
    shares = shares_row["value"] * factor
    ids = sorted({x for e in last4 for x in quarters[e][1]} | {shares_row["observation_id"]})
    available = max(parse_time(r["available_at"], "available_at") for r in (list(ni.values()) + list(sh.values()))
                    if r["observation_id"] in ids)
    return {"value": ni_sum / shares, "reason": None, "quarters": last4,
            "quarterly_net_income": {e: quarters[e][0] for e in last4},
            "quarter_derivations": {e: quarters[e][2] for e in last4},
            "net_income_ttm": ni_sum, "shares_reported": shares_row["value"], "shares_basis": shares_basis,
            "shares_period_end": last4[-1], "shares_filed_date": filed_date,
            "fiscal_period": shares_row.get("fiscal_period"),
            "split_factor": factor, "shares_adjusted": shares, "observation_ids": ids,
            "inputs_available_at": available.isoformat()}


def tw_quarterly_diluted_eps(observations, issuer_id, when, price_date, splits):
    """G-S3-10：台股官方單季稀釋 EPS；Q4＝FY 累計 EPS－9M 累計 EPS。

    股票股利若在申報版本之後、價格月份之前生效，個別申報值先除以累積倍數再相減，
    使四季 EPS 與未還原價格保持同一每股基準。美股路徑不呼叫本函式。
    """
    direct = _visible(observations, "VER_DILUTED_EPS_Q", issuer_id, when)
    ytd = _visible(observations, "VER_DILUTED_EPS_YTD", issuer_id, when)
    by_period = {}
    for (_, end), row in direct.items():
        filed = row.get("filed_date") or str(row["available_at"])[:10]
        factor = split_factor(splits, filed, price_date)
        by_period[end] = {"value": float(row["value"]) / factor,
                          "observation_ids": [row["observation_id"]],
                          "derivation": "REPORTED_SINGLE_QUARTER_DILUTED_EPS",
                          "split_factor": factor, "available_at": row["available_at"]}
    # 年報沒有 Q4 單季 context；同一年度以 FY 累計減 9M 累計。
    by_year = {}
    for (_, end), row in ytd.items():
        by_year.setdefault(str(row.get("fiscal_year") or end[:4]), {})[str(row.get("fiscal_period"))] = row
    for year, periods in by_year.items():
        fy, m9 = periods.get("Q4"), periods.get("Q3")
        if not fy or not m9:
            continue
        fy_filed = fy.get("filed_date") or str(fy["available_at"])[:10]
        m9_filed = m9.get("filed_date") or str(m9["available_at"])[:10]
        fy_factor = split_factor(splits, fy_filed, price_date)
        m9_factor = split_factor(splits, m9_filed, price_date)
        by_period[fy["period_end"]] = {
            "value": float(fy["value"]) / fy_factor - float(m9["value"]) / m9_factor,
            "observation_ids": [fy["observation_id"], m9["observation_id"]],
            "derivation": "DERIVED_Q4_EPS_FY_MINUS_9M",
            "split_factor": {"fy": fy_factor, "nine_month": m9_factor},
            "available_at": max(str(fy["available_at"]), str(m9["available_at"]))}
    return by_period


def tw_ttm_eps(observations, issuer_id, when, price_date, splits, quarter_end=None):
    quarters = tw_quarterly_diluted_eps(observations, issuer_id, when, price_date, splits)
    ends = sorted(e for e in quarters if quarter_end is None or e <= quarter_end)
    if len(ends) < 4:
        return {"value": None, "reason": "EPS_QUARTERS_UNAVAILABLE"}
    last4 = ends[-4:]
    for a, b in zip(last4, last4[1:]):
        if not 70 <= _days(a, b) <= 110:
            return {"value": None, "reason": "EPS_QUARTERS_NOT_CONSECUTIVE", "quarters": last4}
    selected = [quarters[e] for e in last4]
    value = sum(x["value"] for x in selected)
    return {"value": value, "reason": None, "quarters": last4,
            "quarterly_diluted_eps": {e: quarters[e]["value"] for e in last4},
            "quarter_derivations": {e: quarters[e]["derivation"] for e in last4},
            "split_factors": {e: quarters[e]["split_factor"] for e in last4},
            "observation_ids": sorted({oid for x in selected for oid in x["observation_ids"]}),
            "inputs_available_at": max(x["available_at"] for x in selected),
            "formula": "SUM_LAST_4_SINGLE_QUARTER_DILUTED_EPS"}


def _close_time(date, market):
    t, tz = MARKET_CLOSE[market]
    return dt.datetime.combine(dt.date.fromisoformat(date), t, ZoneInfo(tz))


def _add_months(value, months):
    """Calendar-month shift used only to identify the next reporting quarter end."""
    total = value.year * 12 + value.month - 1 + months
    year, month = divmod(total, 12)
    import calendar
    source_last = calendar.monthrange(value.year, value.month)[1]
    target_last = calendar.monthrange(year, month + 1)[1]
    day = target_last if value.day == source_last else min(value.day, target_last)
    return dt.date(year, month + 1, day)


def stale_ttm_after(denominator, market):
    """G-S3-12: final date on which the latest TTM/BVPS input may still be used.

    The grace period is 15 calendar days after the next filing's conservative
    statutory deadline.  A month-end price strictly later than this date is
    stale.  The function derives only from frozen denominator provenance.
    """
    periods = denominator.get("quarters") or ([denominator.get("period_end")]
                                               if denominator.get("period_end") else [])
    if not periods:
        return None
    latest_end = dt.date.fromisoformat(max(periods))
    next_end = _add_months(latest_end, 3)
    if market == "TW":
        deadlines = {
            3: dt.date(next_end.year, 5, 15),
            6: dt.date(next_end.year, 8, 14),
            9: dt.date(next_end.year, 11, 14),
            12: dt.date(next_end.year + 1, 3, 31),
        }
        statutory = deadlines.get(next_end.month)
        if statutory is None:
            return None
        basis = "TW_NEXT_QUARTER_STATUTORY_DEADLINE_PLUS_15D"
    else:
        derivations = denominator.get("quarter_derivations") or {}
        latest_derivation = derivations.get(max(periods))
        fiscal_period = str(denominator.get("fiscal_period") or "").upper()
        next_is_annual = latest_derivation == "YTD_9M_MINUS_H1" or fiscal_period == "Q3"
        statutory = next_end + dt.timedelta(days=90 if next_is_annual else 45)
        basis = "US_NEXT_QUARTER_90D_PLUS_15D" if next_is_annual else "US_NEXT_QUARTER_45D_PLUS_15D"
    return {"latest_period_end": latest_end.isoformat(), "next_period_end": next_end.isoformat(),
            "statutory_deadline": statutory.isoformat(),
            "stale_after": (statutory + dt.timedelta(days=15)).isoformat(), "basis": basis}


def _is_stale_denominator(denominator, market, price_date):
    policy = stale_ttm_after(denominator, market)
    return policy, bool(policy and dt.date.fromisoformat(price_date) > dt.date.fromisoformat(policy["stale_after"]))


def _latest_instant(rows, metric_id, issuer_id, when, price_date, period_end=None, source_tag=None):
    candidates = []
    for r in rows:
        if r.get("issuer_id") != issuer_id or r.get("metric_id") != metric_id: continue
        if r.get("period_end", "") > price_date: continue
        if period_end is not None and r.get("period_end") != period_end: continue
        if source_tag is not None and r.get("source_tag") != source_tag: continue
        try:
            available = parse_time(r["available_at"], "available_at")
        except (KeyError, ValueError):
            continue
        if available <= when:
            candidates.append((r["period_end"], available, r["observation_id"], r))
    return max(candidates)[-1] if candidates else None


def book_value_per_share(observations, security, when, price_date, splits, rules):
    """月底 PIT BVPS；美股用直接流通股數，台股用股本／面額減庫藏股。"""
    issuer, market, sid = security["issuer_id"], security["market"], security["security_id"]
    equity = _latest_instant(observations, "VER_EQUITY_PARENT", issuer, when, price_date)
    if equity is None:
        return {"value": None, "reason": "EQUITY_UNAVAILABLE"}
    if market == "US":
        shares_row = _latest_instant(observations, "VER_SHARES_OUTSTANDING", issuer, when, price_date,
                                     equity["period_end"], "CommonStockSharesOutstanding")
        if shares_row is None:
            return {"value": None, "reason": "OUTSTANDING_SHARES_UNAVAILABLE"}
        shares = float(shares_row["value"])
        ids = [equity["observation_id"], shares_row["observation_id"]]
        basis = "REPORTED_OUTSTANDING_SHARES"
    else:
        capital = _latest_instant(observations, "VER_ISSUED_CAPITAL", issuer, when, price_date, equity["period_end"])
        treasury = _latest_instant(observations, "VER_TREASURY_SHARES", issuer, when, price_date, equity["period_end"])
        if capital is None or treasury is None:
            return {"value": None, "reason": "PERIOD_END_SHARES_COMPONENTS_UNAVAILABLE"}
        par = float((rules["valuation"]["v1_rules"].get("tw_par_value_twd_by_security") or {}).get(sid, 0))
        if par <= 0:
            return {"value": None, "reason": "TW_PAR_VALUE_RULE_MISSING"}
        shares = float(capital["value"]) / par - float(treasury["value"])
        shares_row = capital
        ids = [equity["observation_id"], capital["observation_id"], treasury["observation_id"]]
        basis = "ISSUED_CAPITAL_DIV_PAR_MINUS_TREASURY"
    if shares <= 0:
        return {"value": None, "reason": "OUTSTANDING_SHARES_NON_POSITIVE"}
    filed = shares_row.get("filed_date") or str(shares_row["available_at"])[:10]
    factor = split_factor(splits, filed, price_date)
    shares_adjusted = shares * factor
    available = max(parse_time(r["available_at"], "available_at") for r in observations if r.get("observation_id") in ids)
    return {"value": float(equity["value"]) / shares_adjusted, "reason": None,
            "equity": float(equity["value"]), "shares_reported": shares, "shares_adjusted": shares_adjusted,
            "shares_basis": basis, "split_factor": factor, "observation_ids": sorted(ids),
            "inputs_available_at": available.isoformat(), "period_end": equity["period_end"],
            "fiscal_period": equity.get("fiscal_period")}


def _fx_usdtwd(fx_rows, when, fixing_date):
    """Point-in-time USD/TWD (TWD per USD); missing data is never filled or inferred."""
    candidates = []
    for row in fx_rows or []:
        if row.get("fx_pair") != "USD/TWD": continue
        try:
            fixed = parse_time(row.get("fixing_at"), "fixing_at")
            available = parse_time(row.get("available_at"), "available_at")
            rate = float(row.get("rate"))
        except (TypeError, ValueError):
            continue
        if fixed.date().isoformat() <= fixing_date and available <= when and rate > 0:
            candidates.append((fixed, available, row.get("fx_observation_id", ""), rate))
    if not candidates:
        return None
    fixed, available, oid, rate = max(candidates)
    return {"rate": rate, "fixing_at": fixed.isoformat(), "available_at": available.isoformat(),
            "fx_observation_id": oid, "pair": "USD/TWD"}


def _share_shift_warning(observations, issuer_id, when):
    rows = []
    for metric in ("FIN_DILUTED_SHARES", "VER_DILUTED_SHARES_WAVG"):
        rows.extend(_visible(observations, metric, issuer_id, when).values())
    if not rows: return None
    by_end = {}
    for row in rows:
        end = row.get("period_end")
        if end and end not in by_end: by_end[end] = row
    latest_end = max(by_end)
    target = (dt.date.fromisoformat(latest_end) - dt.timedelta(days=365)).isoformat()
    prior = min((e for e in by_end if 330 <= _days(e, latest_end) <= 400),
                key=lambda e: abs(_days(e, target)), default=None)
    if not prior or float(by_end[prior]["value"]) <= 0: return None
    pct = (float(by_end[latest_end]["value"]) / float(by_end[prior]["value"]) - 1) * 100
    if abs(pct) <= 5: return None
    return {"code": "SHARE_COUNT_SHIFT_DISTORTION_WARNING", "change_pct": pct,
            "latest_period_end": latest_end, "comparison_period_end": prior,
            "observation_ids": [by_end[latest_end]["observation_id"], by_end[prior]["observation_id"]]}


def _ev_sales_components(observations, issuer_id, when, price_date, close, splits):
    """Point-in-time EV/TTM sales inputs. Missing debt components are never treated as zero."""
    revenue = [r for r in _visible(observations, "FIN_REVENUE", issuer_id, when).values()
               if r.get("period_type") == "Q" and r.get("period_end", "") <= price_date]
    by_end = {}
    for row in revenue:
        by_end[row["period_end"]] = row
    quarters = [by_end[k] for k in sorted(by_end)[-4:]]
    if len(quarters) < 4 or any(float(r["value"]) <= 0 for r in quarters):
        return {"value": None, "reason": "TTM_REVENUE_UNAVAILABLE", "revenue_quarters": len(by_end)}
    ttm_revenue = sum(float(r["value"]) for r in quarters)
    shares = _latest_instant(observations, "VER_SHARES_OUTSTANDING", issuer_id, when, price_date)
    cash = _latest_instant(observations, "FIN_CASH", issuer_id, when, price_date)
    total = _latest_instant(observations, "FIN_DEBT_TOTAL", issuer_id, when, price_date)
    current = _latest_instant(observations, "FIN_DEBT_CURRENT", issuer_id, when, price_date)
    noncurrent = _latest_instant(observations, "FIN_DEBT_NONCURRENT", issuer_id, when, price_date)
    debt_candidates = []
    if total is not None:
        debt_candidates.append((total["period_end"], float(total["value"]), [total["observation_id"]], "REPORTED_LONG_TERM_DEBT_TOTAL"))
    if current is not None and noncurrent is not None and current["period_end"] == noncurrent["period_end"]:
        debt_candidates.append((current["period_end"], float(current["value"]) + float(noncurrent["value"]),
                                [current["observation_id"], noncurrent["observation_id"]], "CURRENT_PLUS_NONCURRENT"))
    debt = max(debt_candidates, default=None)
    missing = []
    if shares is None: missing.append("SHARES_OUTSTANDING_UNAVAILABLE")
    if cash is None: missing.append("CASH_UNAVAILABLE")
    if debt is None: missing.append("DEBT_UNAVAILABLE")
    if missing:
        return {"value": None, "reason": "+".join(missing), "revenue_quarters": len(by_end)}
    reported_shares = float(shares["value"])
    if reported_shares <= 0:
        return {"value": None, "reason": "SHARES_OUTSTANDING_NON_POSITIVE", "revenue_quarters": len(by_end)}
    filed = shares.get("filed_date") or str(shares.get("available_at"))[:10]
    factor = split_factor(splits, filed, price_date)
    adjusted_shares = reported_shares * factor
    market_cap = float(close) * adjusted_shares
    enterprise_value = market_cap + debt[1] - float(cash["value"])
    if enterprise_value <= 0:
        return {"value": None, "reason": "ENTERPRISE_VALUE_NON_POSITIVE", "revenue_quarters": len(by_end)}
    ids = [r["observation_id"] for r in quarters] + [shares["observation_id"], cash["observation_id"]] + debt[2]
    return {"value": enterprise_value / ttm_revenue, "reason": None, "enterprise_value": enterprise_value,
            "market_cap": market_cap, "debt": debt[1], "cash": float(cash["value"]), "ttm_revenue": ttm_revenue,
            "shares_reported": reported_shares, "shares_adjusted": adjusted_shares, "split_factor": factor,
            "debt_basis": debt[3], "revenue_quarters": len(by_end), "observation_ids": sorted(ids)}


def compute_ev_sales_alternative(security, observations, price_rows, actions, sessions, anchor_date, cutoff, rules,
                                 minimum_valid_months=36):
    """V1 rung B: PIT EV/Sales relative to the issuer's own recent history."""
    from data_spine import required_complete_months
    issuer, ticker, market = security["issuer_id"], security["ticker"], security["market"]
    cutoff_dt = parse_time(cutoff, "cutoff") if isinstance(cutoff, str) else cutoff
    rows = [r for r in observations if r.get("issuer_id") == issuer and
            parse_time(r.get("selection_written_at") or r["ingested_at"], "ingested_at") <= cutoff_dt]
    splits = sorted((a["date"], float(a["value"])) for a in actions
                    if a.get("ticker") == ticker and a.get("type") in {"分割", "股票股利"} and float(a.get("value") or 0) > 0)
    first = {}
    for r in sorted(price_rows, key=lambda x: x["date"]): first.setdefault(r["date"], r)
    by_month = {}
    for date, row in first.items(): by_month.setdefault(date[:7], []).append(row)
    local_date = cutoff_dt.astimezone(ZoneInfo(security["market_timezone"])).date()
    months = required_complete_months(local_date, rules["valuation"]["v1_rules"]["history_months_required"])
    detail = []
    for month in months:
        last = (by_month.get(month) or [None])[-1]
        entry = {"month": month}
        if last is None:
            entry.update(status="INVALID_MONTH", reason="PRICE_MISSING")
        else:
            when = min(_close_time(last["date"], market), cutoff_dt)
            components = _ev_sales_components(rows, issuer, when, last["date"], float(last["close"]), splits)
            entry.update(price_date=last["date"], close=float(last["close"]), price_row_sha256=last["row_sha256"],
                         components=components)
            if components["value"] is None: entry.update(status="INVALID_MONTH", reason=components["reason"])
            else: entry.update(status="VALID", multiple=components["value"])
        detail.append(entry)
    anchor = first.get(anchor_date) if anchor_date else None
    if anchor is None:
        current = {"value": None, "reason": "CURRENT_PRICE_NOT_AT_ANCHOR", "anchor_date": anchor_date}
    else:
        current = _ev_sales_components(rows, issuer, min(_close_time(anchor_date, market), cutoff_dt), anchor_date,
                                       float(anchor["close"]), splits)
        current.update(anchor_date=anchor_date, close=float(anchor["close"]), price_row_sha256=anchor["row_sha256"])
    valid = [r for r in detail if r.get("status") == "VALID"]
    if current.get("value") is None:
        return {"value": None, "status": current.get("reason") or "EV_SALES_CURRENT_UNAVAILABLE",
                "method": "V1_ALT_EV_SALES", "valid_months": len(valid), "months": detail, "current": current}
    if len(valid) < minimum_valid_months:
        return {"value": None, "status": "DATA_INSUFFICIENT_EV_SALES_HISTORY", "method": "V1_ALT_EV_SALES",
                "valid_months": len(valid), "months": detail, "current": current}
    used = valid[-minimum_valid_months:]
    median = statistics.median(r["multiple"] for r in used)
    relative = current["value"] / median
    percentile = 100 * sum(r["multiple"] <= current["value"] for r in used) / len(used)
    from t43_candidates import conservative_alt_score
    thresholds = (rules.get("v1_alternative") or {}).get("thresholds") or {}
    return {"value": relative, "status": "OK", "method": "V1_ALT_EV_SALES", "score": conservative_alt_score(
                relative, good_below=float(thresholds.get("good_below", 1.0)), bad_above=float(thresholds.get("bad_above", 1.5))),
            "valid_months": len(valid), "used_months": minimum_valid_months, "median": median,
            "current_multiple": current["value"], "percentile": percentile, "months": detail, "current": current}


def compute(security, observations, price_rows, actions, sessions, anchor_date, cutoff, rules, fx_rows=None):
    """price_rows：該證券凍結價格列（含 row_sha256）；actions：凍結公司行動列。回傳可存快照的 dict。"""
    from data_spine import calculate_v1, required_complete_months, valuation_method
    sid, market, issuer = security["security_id"], security["market"], security["issuer_id"]
    method = valuation_method(sid, rules)
    base = {"version": VERSION, "security_id": sid, "method": method}
    if security.get("ticker") in NOT_APPLICABLE:
        status, reason = NOT_APPLICABLE[security["ticker"]]
        return dict(base, value=None, status=status, reason=reason)
    if method not in {"TRAILING_PE_RELATIVE_TO_5Y_MEDIAN", "TRAILING_PB_RELATIVE_TO_5Y_MEDIAN"}:
        return dict(base, value=None, status="UNSUPPORTED_VALUATION_METHOD")
    cutoff_dt = parse_time(cutoff, "cutoff") if isinstance(cutoff, str) else cutoff
    # 只用 cutoff 前已收錄的列（selection_written_at 為寫入帳本時間；沒有時退回 ingested_at）
    observations = [r for r in observations if (str(r.get("metric_id", "")).startswith("VER_") or
                                                  r.get("metric_id") == "FIN_DILUTED_SHARES")
                    and parse_time(r.get("selection_written_at") or r["ingested_at"], "ingested_at") <= cutoff_dt]
    override = ISSUER_METRIC_OVERRIDES.get(issuer, {})
    if "VER_NET_INCOME_PARENT" in override:
        preferred_tag = override["VER_NET_INCOME_PARENT"][1]
        observations = [r for r in observations
                        if r.get("metric_id") != "VER_NET_INCOME_PARENT" or r.get("source_tag") == preferred_tag]
    if not any(r.get("issuer_id") == issuer and str(r.get("metric_id", "")).startswith("VER_") for r in observations):
        return dict(base, value=None, status="DATA_INSUFFICIENT_VERSIONED_FILINGS")
    ticker = security["ticker"]
    splits = sorted((a["date"], float(a["value"])) for a in actions
                    if a.get("ticker") == ticker and a.get("type") in {"分割", "股票股利"} and float(a.get("value") or 0) > 0)
    first = {}
    for r in sorted(price_rows, key=lambda x: x["date"]):
        first.setdefault(r["date"], r)  # 與 price_structures 相同：同日第一列有效
    by_month = {}
    for date, r in first.items():
        by_month.setdefault(date[:7], []).append(r)
    session_month_end = {}
    for s in sessions:
        if s.get("market") == market and s.get("session_status", "OPEN") == "OPEN":
            m = s["session_date"][:7]
            if s["session_date"] > session_month_end.get(m, ""): session_month_end[m] = s["session_date"]
    local_date = cutoff_dt.astimezone(ZoneInfo(security["market_timezone"])).date()
    months = required_complete_months(local_date, rules["valuation"]["v1_rules"]["history_months_required"])
    detail, history = [], []
    for month in months:
        rows = by_month.get(month) or []
        last = rows[-1] if rows else None
        expected = session_month_end.get(month)
        entry = {"month": month}
        if last is None:
            entry.update(status="INVALID_MONTH", reason="PRICE_MISSING")
        elif expected and last["date"] != expected:
            entry.update(status="INVALID_MONTH", reason="PRICE_NOT_AT_MONTH_END_SESSION", price_date=last["date"])
        else:
            when = _close_time(last["date"], market)
            denominator = ((tw_ttm_eps(observations, issuer, when, last["date"], splits)
                            if market == "TW" or ticker == "TSM" else
                            ttm_eps(observations, issuer, when, last["date"], splits))
                           if method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN" else
                           book_value_per_share(observations, security, when, last["date"], splits, rules))
            if ticker == "TSM" and method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN" and denominator.get("value"):
                fx = _fx_usdtwd(fx_rows, when, last["date"])
                if fx is None:
                    denominator = {"value": None, "reason": "USD_TWD_FX_UNAVAILABLE"}
                else:
                    denominator = dict(denominator, underlying_eps_twd=denominator["value"], adr_ratio=5,
                                       fx=fx, value=denominator["value"] * 5 / fx["rate"],
                                       formula="MOPS_TTM_EPS_TWD*5/USD_TWD")
            entry.update(price_date=last["date"], close=float(last["close"]), price_row_sha256=last["row_sha256"],
                         calendar_verified=bool(expected), denominator=denominator)
            if method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN":
                entry["ttm"] = denominator  # 1.9.2 payload compatibility
            stale_policy, is_stale = _is_stale_denominator(denominator, market, last["date"])
            if stale_policy:
                entry["stale_ttm_policy"] = stale_policy
            if is_stale:
                entry.update(status="INVALID_STALE_TTM", reason="INVALID_STALE_TTM")
            elif denominator["value"] is None:
                entry.update(status="INVALID_MONTH", reason=denominator["reason"])
            elif denominator["value"] <= 0:
                entry.update(status="INVALID_MONTH", reason="TTM_EPS_NON_POSITIVE" if method.startswith("TRAILING_PE") else "BVPS_NON_POSITIVE")
            else:
                entry.update(status="VALID", multiple=float(last["close"]) / denominator["value"])
        detail.append(entry)
        history.append({"month_end": (entry.get("price_date") or month + "-28"),
                        "value": entry.get("multiple"), "available_at": cutoff_dt.isoformat()})
    # 目前倍數：綁分析 session 收盤價
    current = {"anchor_date": anchor_date}
    anchor_row = first.get(anchor_date) if anchor_date else None
    current_multiple = None
    if anchor_row is None:
        current.update(status="CURRENT_PRICE_NOT_AT_ANCHOR")
    else:
        when = min(_close_time(anchor_date, market), cutoff_dt)
        denominator = ((tw_ttm_eps(observations, issuer, when, anchor_date, splits)
                        if market == "TW" or ticker == "TSM" else
                        ttm_eps(observations, issuer, when, anchor_date, splits))
                       if method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN" else
                       book_value_per_share(observations, security, when, anchor_date, splits, rules))
        if ticker == "TSM" and method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN" and denominator.get("value"):
            fx = _fx_usdtwd(fx_rows, when, anchor_date)
            if fx is None:
                denominator = {"value": None, "reason": "USD_TWD_FX_UNAVAILABLE"}
            else:
                denominator = dict(denominator, underlying_eps_twd=denominator["value"], adr_ratio=5,
                                   fx=fx, value=denominator["value"] * 5 / fx["rate"],
                                   formula="MOPS_TTM_EPS_TWD*5/USD_TWD")
        current.update(close=float(anchor_row["close"]), price_row_sha256=anchor_row["row_sha256"], denominator=denominator)
        if method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN":
            current["ttm"] = denominator
        stale_policy, is_stale = _is_stale_denominator(denominator, market, anchor_date)
        if stale_policy:
            current["stale_ttm_policy"] = stale_policy
        if is_stale:
            current.update(status="CURRENT_STALE_TTM", reason="INVALID_STALE_TTM")
        elif denominator["value"] and denominator["value"] > 0:
            current_multiple = float(anchor_row["close"]) / denominator["value"]
            current.update(status="OK", multiple=current_multiple)
        else:
            current.update(status="CURRENT_TTM_EPS_INVALID")
    # 最近 4 季 TTM EPS（為正檢查用）
    quarters_seen = (sorted(tw_quarterly_diluted_eps(observations, issuer, cutoff_dt,
                                                     anchor_date or local_date.isoformat(), splits))
                     if market == "TW" and method.startswith("TRAILING_PE") else
                     sorted(quarterly_net_income(_visible(observations, "VER_NET_INCOME_PARENT", issuer, cutoff_dt)))
                     if method.startswith("TRAILING_PE") else [])
    ttm_rows = []
    for q in quarters_seen[-6:]:
        e = (tw_ttm_eps(observations, issuer, cutoff_dt, anchor_date or local_date.isoformat(), splits, quarter_end=q)
             if market == "TW" else
             ttm_eps(observations, issuer, cutoff_dt, anchor_date or local_date.isoformat(), splits, quarter_end=q))
        if e["value"] is not None:
            ttm_rows.append({"period_end": q, "value": e["value"], "available_at": e["inputs_available_at"]})
    result = calculate_v1(sid, cutoff_dt.isoformat(), current_multiple, history, ttm_rows, rules, security["market_timezone"])
    if ticker == "TSM" and (current.get("denominator") or {}).get("reason") == "USD_TWD_FX_UNAVAILABLE":
        result = dict(result, value=None, status="DATA_INSUFFICIENT_USD_TWD_FX")
    valid = sum(1 for d in detail if d["status"] == "VALID")
    limitations = ["PRE_2024_MONTH_END_NOT_CALENDAR_VERIFIED"]
    if market == "US": limitations.append("SEC_FILED_DATE_USED_AS_CONSERVATIVE_AVAILABILITY")
    if method.startswith("TRAILING_PE") and market == "US":
        limitations += ["Q4_SHARES_FY_WEIGHTED_AVERAGE_PROXY_WHEN_QUARTER_ABSENT"]
    if method.startswith("TRAILING_PE") and market == "TW":
        limitations += ["TW_DILUTED_EPS_SUM_OF_FOUR_QUARTERS", "DERIVED_Q4_EPS_FY_MINUS_9M"]
    if ticker == "ETN":
        limitations.append("ETN_MOBILITY_SPINOFF_ANNOUNCED_2026_01_WINDOW_COMPARABILITY_LIMITED")
    warning = _share_shift_warning(observations, issuer, cutoff_dt)
    if warning: limitations.append("SHARE_COUNT_SHIFT_DISTORTION_WARNING")
    return dict(base, value=result.get("value"), status=result.get("status"),
                median_60m=result.get("median_60m"), current=current,
                valid_months=valid, invalid_months=len(detail) - valid, months=detail,
                recent_ttm_eps=ttm_rows, splits_used=[{"date": d, "factor": f} for d, f in splits],
                limitations=limitations, share_count_shift_warning=warning)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from common import out, file_lock
    import lock as project_lock
    ticker = argv[argv.index("--versions") + 1]
    lock_holder = argv[argv.index("--lock-holder") + 1] if "--lock-holder" in argv else None
    path = argv[argv.index("--companyfacts") + 1]
    registry = data_spine.load_registry()
    sec, state = data_spine.resolve_security(ticker, registry)
    if state != "OK": raise SystemExit(f"{ticker}：{state}")
    tag_map = data_spine.load_json(os.path.join(data_spine.ROOT, "00_規範", "financial_tag_map.json"))
    cik = tag_map["sec_issuers"][sec["issuer_id"]]["cik"]
    with open(path, encoding="utf-8") as f: facts = json.load(f)
    if str(facts.get("cik", "")).lstrip("0") != cik.lstrip("0"): raise SystemExit("companyfacts CIK 不符")
    ingested = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rows = version_rows(facts, sec["issuer_id"], cik, ingested)
    errors = [e for r in rows for e in data_spine.validate_observation(r)]
    if errors: raise SystemExit("observation 驗證失敗：" + "；".join(errors[:5]))
    existing = {r.get("observation_id") for r in data_spine.read_observations_checked()}
    new = [r for r in rows if r["observation_id"] not in existing]
    out(f"{ticker}：版本列 {len(rows)} 筆，其中新列 {len(new)} 筆。")
    if "--write" not in argv or not new:
        return 0
    project_lock.require_holder(lock_holder, "observations")
    with file_lock(data_spine.OBSERVATIONS_PATH):
        entry = data_spine.transactional_append_observations(data_spine.OBSERVATIONS_PATH, new,
                                                             f"S3C_V1_VERSIONED_FILINGS:{ticker}",
                                                             lock_holder=lock_holder)
    out(f"已寫入 {len(new)} 筆；batch {entry['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
