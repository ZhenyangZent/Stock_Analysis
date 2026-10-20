"""財務體質影子模組：只根據 canonical 財報 observation 亮警示，不下買賣結論。

用法：
  python 03_工具/health.py
  python 03_工具/health.py --as-of 2026-10-06 --no-ledger
  python 03_工具/health.py --json --no-ledger

損益與現金流 observation 必須是已正規化的單季（period_type=Q）或完整年度
（period_type=FY）數字。程式不把 YTD 數字當單季，也不把缺漏推論為零。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys

from common import ROOT, RULES_PATH, file_lock, load_rules, out
from data_spine import (OBSERVATIONS_PATH, load_registry, parse_time, read_jsonl, load_write_times, observation_written_at,
                        validate_observation,
                        read_observations_checked, resolve_visible_observation_versions)

LEDGER = os.path.join(ROOT, "04_回測", "財務體質紀錄.csv")
TAG_MAP_PATH = os.path.join(ROOT, "00_規範", "financial_tag_map.json")
# 1.8.1（C180-01）：台股 MOPS 財報的啟用閘門。狀態只從 financial_tag_map.json 讀，不寫死在程式。
# 只有這個值代表「複查與原文抽查都通過、另經授權啟用」；其他任何值（含檔案讀不到）一律視為待啟用。
TAIWAN_ENABLED_STATUS = "ENABLED_AFTER_INDEPENDENT_REVIEW"
PENDING_SOURCE = "MOPS_XBRL"  # 舊名稱保留；實際閘門涵蓋所有正式 MOPS 財報來源。
MOPS_SOURCES = {"MOPS_XBRL", "MOPS_PDF"}
PENDING_REASON = "台股財報待複查啟用"
PREVIEW_LABEL = "未經抽查的預覽"
BACKFILL_EXCLUDED_REASON = "該期財報列於官方更補正清單，可用時間未知，已排除；可用 --current-snapshot 檢視"
LIMITATION_TEXT = {
    "CROSS_MARKET_NOT_COMPARABLE": "負債口徑台美不同（台股不含租賃負債），只做同一家公司前後期比較，不跨市場比較",
    "INDIVIDUAL_STATEMENTS_ONLY": "僅個別財報，不與合併報表直接比較",
    "ABSENT_COMPONENTS_TREATED_AS_NONE": "有息負債白名單中缺席的細項依使用者 2026-10-08 規則視為無此項目",
    "SHORT_TERM_INV_IRRELEVANT_TO_BAND": "短期投資雖缺精確值，但以 0 到流動資產扣除現金的上下界計算，燈號相同",
    "SHORT_TERM_INV_REVIEW_REQUIRED": "短期投資上下界會改變燈號，需 AI 判讀財報附註後才能定案",
}
LEDGER_COLS = [
    "assessment_id", "as_of", "issuer_id", "status", "latest_period_end",
    "required_missing", "indicators_json", "input_sha256", "protocol_version",
    "module_status", "written_at",
]

REQUIRED = ("H2", "H3", "H4", "H5")
STATUS_RANK = {"不適用": -1, "穩健": 0, "留意": 1, "警示": 2, "資料不足": 3}
DISCLAIMER_LINES = (
    "這些門檻尚未用本系統的歷史資料驗證。",
    "這不是投資建議，也不是買賣指令。",
    "資本支出密集公司可能因擴產使自由現金流轉負；循環股在景氣低谷時獲利分母會讓 H2、H3 惡化，需搭配 H1、H4 解讀。",
    "本模組只看已申報財報，無法看見表外負債、客戶集中、訂單取消等風險。",
)


def _iso_date(value):
    if not value:
        return None
    return dt.date.fromisoformat(str(value)[:10])


def _active(rows):
    return resolve_visible_observation_versions(rows)


def _active_as_of(rows, cutoff_dt, write_times=None):
    """1.8.2（C181-02）：只看截止時間前已實際寫入 canonical 的列，再取各修訂鏈當時最新的一列。

    子列的寫入時間晚於截止時間時，父列仍是當時的有效列。寫入時間見 data_spine.observation_written_at。
    """
    write_times = load_write_times() if write_times is None else write_times
    visible = []
    for row in rows:
        written = observation_written_at(row, write_times)
        if written is None or written <= cutoff_dt:
            visible.append(row)
    return resolve_visible_observation_versions(rows, visible)


def point_in_time(rows, issuer_id, cutoff, write_times=None):
    """只保留截止時間前已公布、已可得且已收錄的發行人財報事實；更正鏈依截止時間選當時已寫入的最新列。"""
    cutoff_dt = parse_time(cutoff, "run_cutoff_at")
    write_times = load_write_times() if write_times is None else write_times
    selected = []
    for row in rows:
        if row.get("issuer_id") != issuer_id or not str(row.get("metric_id", "")).startswith("FIN_"):
            continue
        written = observation_written_at(row, write_times)
        if written is not None and written > cutoff_dt:
            continue
        basis = row.get("availability_basis")
        status = row.get("availability_status")
        try:
            if not basis:  # 舊 SEC 列：維持 1.7.1 的三時間規則。
                times = [parse_time(row.get(k), k) for k in ("source_published_at", "available_at", "ingested_at")]
                if any(t is None or t > cutoff_dt for t in times): continue
            elif basis in {"OFFICIAL_TIMESTAMP", "OFFICIAL_DATE"}:
                if status != "POINT_IN_TIME_KNOWN": continue
                times = [parse_time(row.get(k), k) for k in ("source_published_at", "available_at", "ingested_at")]
                if any(t is None or t > cutoff_dt for t in times): continue
            elif basis == "FIRST_SEEN_CONSERVATIVE":
                if status != "POINT_IN_TIME_KNOWN" or row.get("source_published_at") is not None: continue
                times = [parse_time(row.get(k), k) for k in ("available_at", "ingested_at")]
                if any(t is None or t > cutoff_dt for t in times): continue
            elif basis == "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE":
                # G-S3-9b：一般 FIN_* 的官方更正版本可在證據完整且 schema 驗證
                # 通過後依保守時點取用。寫入時間與 ingested_at gate 仍保留，避免一筆
                # 事後才進系統的資料回頭改寫既有歷史 run。
                if (status != "POINT_IN_TIME_KNOWN" or row.get("source_published_at") is not None or
                        not str(row.get("metric_id") or "").startswith("FIN_") or
                        validate_observation({k: v for k, v in row.items() if k != "selection_written_at"},
                                             historical_replay=True)):
                    continue
                times = [parse_time(row.get(k), k) for k in ("available_at", "ingested_at")]
                if any(t is None or t > cutoff_dt for t in times): continue
            else:  # UNKNOWN_BACKFILL / LEGACY_UNKNOWN 在嚴格取用永遠排除。
                continue
        except ValueError:
            continue
        selected.append(row)
    return resolve_visible_observation_versions(rows, selected)


def current_snapshot(rows, issuer_id, now, write_times=None):
    """目前狀況模式：可納入已補抓、但不可回溯的資料。"""
    now_dt = parse_time(now, "current_snapshot_at")
    write_times = load_write_times() if write_times is None else write_times
    selected = []
    for row in rows:
        if row.get("issuer_id") != issuer_id or not str(row.get("metric_id", "")).startswith("FIN_"):
            continue
        written = observation_written_at(row, write_times)
        if written is not None and written > now_dt:
            continue
        try:
            ingested = parse_time(row.get("ingested_at"), "ingested_at")
        except ValueError:
            continue
        if ingested is None or ingested > now_dt:
            continue
        if row.get("availability_status") == "BACKFILLED_UNKNOWN" and row.get("availability_basis") == "UNKNOWN_BACKFILL":
            selected.append(row)
        elif point_in_time([row], issuer_id, now, {}):
            selected.append(row)
    return resolve_visible_observation_versions(rows, selected)


def taiwan_gate_status(tag_map_path=None):
    """回傳 financial_tag_map.json 的 taiwan.status；讀不到時回 None（視為待啟用）。"""
    try:
        with open(tag_map_path or TAG_MAP_PATH, encoding="utf-8") as handle:
            return (json.load(handle).get("taiwan") or {}).get("status")
    except (OSError, ValueError):
        return None


def mops_gate_enabled(tag_map_path=None):
    return taiwan_gate_status(tag_map_path) == TAIWAN_ENABLED_STATUS


def gate_pending_sources(rows, enabled, preview_pending=False):
    """未啟用且不是明確預覽時，拿掉所有 MOPS 財報觀測值。回傳（保留的列, 被擋下的列數）。"""
    if enabled or preview_pending:
        return rows, 0
    kept = [r for r in rows if r.get("source_system") not in MOPS_SOURCES]
    return kept, len(rows) - len(kept)


def excluded_backfill_periods(rows, issuer_id, metric_ids=None):
    """嚴格取用下被排除的回補（BACKFILLED_UNKNOWN）期末，供原因說明使用。"""
    return sorted({r.get("period_end") for r in _active(rows)
                   if r.get("issuer_id") == issuer_id and r.get("period_end")
                   and r.get("availability_status") == "BACKFILLED_UNKNOWN"
                   and (metric_ids is None or r.get("metric_id") in metric_ids)})


def scope_limitations(rows):
    return ["INDIVIDUAL_STATEMENTS_ONLY"] if any(
        r.get("report_scope") == "INDIVIDUAL" or "INDIVIDUAL_STATEMENTS_ONLY" in (r.get("limitations") or [])
        for r in rows) else []


def consistent_report_scope(rows):
    """MOPS 口徑切換後只使用最新口徑，不跨個別／合併邊界做變動。"""
    mops = [r for r in rows if r.get("source_system") in MOPS_SOURCES and r.get("report_scope")]
    if not mops:
        return rows
    latest = max(mops, key=lambda r: (r.get("period_end", ""), int(r.get("manifest_revision_sequence", 0))))
    scope = latest["report_scope"]
    return [r for r in rows if r.get("source_system") not in MOPS_SOURCES or r.get("report_scope") == scope]


def _latest_by_period(rows, metric_id, period_type=None):
    found = {}
    for row in rows:
        if row.get("metric_id") != metric_id:
            continue
        if period_type and row.get("period_type") != period_type:
            continue
        period = row.get("period_end")
        if not period or row.get("value") is None:
            continue
        key = (period, row.get("period_type"))
        old = found.get(key)
        if old is None or row.get("ingested_at", "") > old.get("ingested_at", ""):
            found[key] = row
    return sorted(found.values(), key=lambda r: r["period_end"], reverse=True)


def _instant(rows, metric_id, period_end=None):
    values = _latest_by_period(rows, metric_id, "INSTANT")
    if period_end is not None:
        values = [r for r in values if r.get("period_end") == period_end]
    return values[0] if values else None


def _common_period(rows, metric_ids):
    periods = None
    for metric in metric_ids:
        current = {r["period_end"] for r in _latest_by_period(rows, metric, "INSTANT")}
        periods = current if periods is None else periods & current
    return max(periods) if periods else None


def _aligned_quarter_sums(rows, metric_ids, count=4):
    """同一比率的各項目只能用完全相同的四個季末；不能各自抓最近四季。"""
    by_metric = {
        metric: {r["period_end"]: r for r in _latest_by_period(rows, metric, "Q")}
        for metric in metric_ids
    }
    common = None
    for values in by_metric.values():
        common = set(values) if common is None else common & set(values)
    periods = sorted(common or (), reverse=True)[:count]
    if len(periods) < count:
        return None
    oldest, newest = _iso_date(periods[-1]), _iso_date(periods[0])
    if not oldest or not newest or (newest - oldest).days > 330:
        return None
    return {
        metric: (sum(float(by_metric[metric][p]["value"]) for p in periods),
                 [by_metric[metric][p] for p in periods])
        for metric in metric_ids
    }


def _sum_quarters(rows, metric_id, count=4):
    aligned = _aligned_quarter_sums(rows, (metric_id,), count)
    return aligned[metric_id] if aligned else (None, [])


def _annual_fcf(rows):
    cfo = {r["period_end"]: r for r in _latest_by_period(rows, "FIN_CFO", "FY")}
    capex = {r["period_end"]: r for r in _latest_by_period(rows, "FIN_CAPEX", "FY")}
    periods = sorted(set(cfo) & set(capex), reverse=True)
    if len(periods) < 2:
        return None, []
    chosen = periods[:2]
    return [float(cfo[p]["value"]) - float(capex[p]["value"]) for p in chosen], [cfo[p] for p in chosen] + [capex[p] for p in chosen]


def _result(indicator_id, status, value=None, unit=None, reason="", period_end=None, inputs=None,
            freshness_period_end=None):
    used = list(inputs or [])
    zero_ids = sorted({r.get("zero_assertion_id") for r in used if r.get("zero_assertion_id")})
    if zero_ids:
        suffix = "依查證聲明視為零：" + "、".join(zero_ids)
        reason = f"{reason}；{suffix}" if reason else suffix
    return {
        "indicator_id": indicator_id,
        "status": status,
        "value": value,
        "unit": unit,
        "reason": reason,
        "period_end": period_end,
        "freshness_period_end": freshness_period_end or period_end,
        "observation_ids": [r.get("observation_id") for r in used if r.get("observation_id")],
        "zero_assertion_ids": zero_ids,
    }


def _threshold(value, caution, warning, high_bad=True, warning_allowed=True):
    eps = 1e-12
    if high_bad:
        if warning_allowed and value > warning + eps:
            return "警示"
        if value > caution + eps:
            return "留意"
    else:
        if warning_allowed and value < warning - eps:
            return "警示"
        if value < caution - eps:
            return "留意"
    return "穩健"


def _short_term_investment_band(rows, period_end):
    """短期投資缺值時的保守包絡：0 <= 短期投資 <= 流動資產 - 現金。

    上界只是資產負債表恆等關係給出的寬鬆界線，不是對短期投資餘額的估計。
    現金或流動資產缺漏、不同期、或流動資產小於現金時，不建立包絡。
    """
    if not period_end:
        return None
    cash = _instant(rows, "FIN_CASH", period_end)
    current_assets = _instant(rows, "FIN_CURRENT_ASSETS", period_end)
    if not cash or not current_assets:
        return None
    cash_value = float(cash["value"])
    upper = float(current_assets["value"]) - cash_value
    if cash_value < 0 or upper < -1e-12:
        return None
    return {
        "period_end": period_end,
        "lower": 0.0,
        "upper": max(0.0, upper),
        "unit": cash.get("unit") or current_assets.get("unit"),
        "basis": "ZERO_TO_CURRENT_ASSETS_MINUS_CASH",
        "observation_ids": [r.get("observation_id") for r in (cash, current_assets) if r.get("observation_id")],
        "inputs": [cash, current_assets],
    }


def _merge_band_results(indicator_id, lower, upper, band, inputs):
    """只有上下界燈號相同才採用；否則維持資料不足並要求判讀附註。"""
    same = lower["status"] == upper["status"]
    status = lower["status"] if same else "資料不足"
    limitation = "SHORT_TERM_INV_IRRELEVANT_TO_BAND" if same else "SHORT_TERM_INV_REVIEW_REQUIRED"
    if same:
        reason = f"短期投資介於 {band['lower']:.2f} 與 {band['upper']:.2f}，上下界燈號皆為{status}"
    else:
        reason = (f"短期投資介於 {band['lower']:.2f} 與 {band['upper']:.2f}，"
                  f"上下界燈號分別為{lower['status']}與{upper['status']}，需 AI 判讀附註")
    result = _result(indicator_id, status, reason=reason,
                     period_end=lower.get("period_end") or upper.get("period_end"), inputs=inputs,
                     freshness_period_end=lower.get("freshness_period_end") or upper.get("freshness_period_end"))
    result["limitations"] = [limitation]
    result["short_term_investment_band"] = {k: v for k, v in band.items() if k != "inputs"}
    result["band_outcomes"] = {
        "lower": {k: lower.get(k) for k in ("status", "value", "unit")},
        "upper": {k: upper.get(k) for k in ("status", "value", "unit")},
    }
    return result


def evaluate_issuer(issuer_id, observations, rules, cutoff, is_etf=False, current_snapshot_mode=False,
                    preview_pending=False, mops_enabled=None):
    cfg = rules["health"]
    if is_etf:
        return {"issuer_id": issuer_id, "status": "不適用", "latest_period_end": None,
                "required_missing": [], "indicators": {}, "limitations": list(DISCLAIMER_LINES)}
    if mops_enabled is None:
        mops_enabled = mops_gate_enabled()
    issuer_rows = [r for r in observations if r.get("issuer_id") == issuer_id]
    issuer_rows, gated_count = gate_pending_sources(issuer_rows, mops_enabled, preview_pending)
    rows = current_snapshot(issuer_rows, issuer_id, cutoff) if current_snapshot_mode else point_in_time(issuer_rows, issuer_id, cutoff)
    rows = consistent_report_scope(rows)
    backfill_excluded = [] if current_snapshot_mode else excluded_backfill_periods(issuer_rows, issuer_id)
    latest_period = max((r.get("period_end") for r in rows if r.get("period_end")), default=None)
    indicators = {}

    # 1.3.2：總負債一律是「一年內到期」加「非流動」兩項相加。
    # 1.3.1 曾把只含非流動部分的標籤當成總負債，使負債少算；現在不接受任何「總負債」捷徑。
    base_balance_metrics = ("FIN_CASH", "FIN_DEBT_CURRENT", "FIN_DEBT_NONCURRENT")
    balance_period = _common_period(rows, base_balance_metrics)
    balance = {}
    if balance_period:
        for metric in base_balance_metrics:
            balance[metric] = _instant(rows, metric, balance_period)
        short_term = _instant(rows, "FIN_SHORT_TERM_INVESTMENTS", balance_period)
        if short_term:
            balance["FIN_SHORT_TERM_INVESTMENTS"] = short_term
    short_term_band = None if not balance or "FIN_SHORT_TERM_INVESTMENTS" in balance else _short_term_investment_band(rows, balance_period)
    cash_pool = (None if not balance or "FIN_SHORT_TERM_INVESTMENTS" not in balance else
                 float(balance["FIN_CASH"]["value"]) + float(balance["FIN_SHORT_TERM_INVESTMENTS"]["value"]))
    debt_current = None if not balance else float(balance["FIN_DEBT_CURRENT"]["value"])
    debt_noncurrent = None if not balance else float(balance["FIN_DEBT_NONCURRENT"]["value"])
    total_debt = None if not balance else debt_current + debt_noncurrent

    h2_ttm = _aligned_quarter_sums(rows, ("FIN_OPERATING_INCOME", "FIN_DEPRECIATION_AMORTIZATION"))
    h3_ttm = _aligned_quarter_sums(rows, ("FIN_OPERATING_INCOME", "FIN_INTEREST_EXPENSE"))
    fcf_ttm = _aligned_quarter_sums(rows, ("FIN_CFO", "FIN_CAPEX"))
    op_income, op_rows = h2_ttm["FIN_OPERATING_INCOME"] if h2_ttm else (None, [])
    da, da_rows = h2_ttm["FIN_DEPRECIATION_AMORTIZATION"] if h2_ttm else (None, [])
    h3_op_income, h3_op_rows = h3_ttm["FIN_OPERATING_INCOME"] if h3_ttm else (None, [])
    interest, interest_rows = h3_ttm["FIN_INTEREST_EXPENSE"] if h3_ttm else (None, [])
    cfo, cfo_rows = fcf_ttm["FIN_CFO"] if fcf_ttm else (None, [])
    capex, capex_rows = fcf_ttm["FIN_CAPEX"] if fcf_ttm else (None, [])
    ttm_fcf = None if cfo is None or capex is None else cfo - capex
    fcf_period = cfo_rows[0]["period_end"] if cfo_rows else None
    h2_period = op_rows[0]["period_end"] if op_rows else None
    h3_period = h3_op_rows[0]["period_end"] if h3_op_rows else None

    def calculate_h1(pool, pool_inputs):
        if ttm_fcf is None or pool is None:
            return _result("H1", "資料不足", reason="缺現金、短期投資或四季自由現金流")
        if ttm_fcf >= 0:
            freshness = min(x for x in (balance_period, fcf_period) if x)
            return _result("H1", "不適用", reason="TTM 自由現金流非負", period_end=fcf_period,
                           inputs=pool_inputs + cfo_rows + capex_rows, freshness_period_end=freshness)
        runway = pool / (-ttm_fcf / 4) if -ttm_fcf > 0 else None
        status = "警示" if runway < cfg["thresholds"]["H1"]["warning_below_quarters"] - 1e-12 else ("留意" if runway < cfg["thresholds"]["H1"]["caution_below_quarters"] - 1e-12 else "穩健")
        freshness = min(x for x in (balance_period, fcf_period) if x)
        return _result("H1", status, runway, "季", "現金與短期投資可支應的平均季度流出", fcf_period,
                       pool_inputs + cfo_rows + capex_rows, freshness)

    # H1
    if cash_pool is not None:
        indicators["H1"] = calculate_h1(cash_pool, list(balance.values()))
    elif short_term_band:
        cash_value = float(balance["FIN_CASH"]["value"])
        band_inputs = list(balance.values()) + short_term_band["inputs"]
        indicators["H1"] = _merge_band_results(
            "H1", calculate_h1(cash_value + short_term_band["lower"], band_inputs),
            calculate_h1(cash_value + short_term_band["upper"], band_inputs), short_term_band, band_inputs)
    else:
        indicators["H1"] = calculate_h1(None, list(balance.values()))

    def calculate_h2(pool, pool_inputs):
        if total_debt is None or pool is None:
            return _result("H2", "資料不足", reason="缺完整債務或現金與短期投資")
        net_debt = total_debt - pool
        inputs = list(pool_inputs)
        if net_debt <= 0:
            return _result("H2", "穩健", net_debt, "原幣", "淨現金，H2 直接穩健", balance_period, inputs)
        if op_income is None or da is None:
            return _result("H2", "資料不足", reason="淨負債為正，但缺 TTM 營業利益或折舊攤銷")
        ebitda = op_income + da
        inputs += op_rows + da_rows
        if ebitda < 0:
            freshness = min(x for x in (balance_period, h2_period) if x)
            return _result("H2", "警示", None, "倍", "EBITDA 為負且淨負債為正", h2_period, inputs, freshness)
        if ebitda == 0:
            return _result("H2", "資料不足", reason="EBITDA 為零，倍數無法定義", period_end=h2_period, inputs=inputs)
        ratio = net_debt / ebitda
        h = cfg["thresholds"]["H2"]
        freshness = min(x for x in (balance_period, h2_period) if x)
        return _result("H2", _threshold(ratio, h["caution_above"], h["warning_above"]), ratio, "倍", "淨負債／EBITDA", h2_period, inputs, freshness)

    # H2
    if cash_pool is not None:
        indicators["H2"] = calculate_h2(cash_pool, list(balance.values()))
    elif short_term_band:
        cash_value = float(balance["FIN_CASH"]["value"])
        band_inputs = list(balance.values()) + short_term_band["inputs"]
        indicators["H2"] = _merge_band_results(
            "H2", calculate_h2(cash_value + short_term_band["lower"], band_inputs),
            calculate_h2(cash_value + short_term_band["upper"], band_inputs), short_term_band, band_inputs)
    else:
        indicators["H2"] = calculate_h2(None, list(balance.values()))

    # H3
    if total_debt == 0:
        indicators["H3"] = _result("H3", "穩健", None, "倍", "明確無有息負債", balance_period, list(balance.values()))
    elif h3_op_income is None or interest is None:
        indicators["H3"] = _result("H3", "資料不足", reason="缺 TTM 營業利益或利息費用")
    elif interest == 0:
        indicators["H3"] = _result("H3", "穩健", None, "倍", "申報利息費用為零", h3_period, h3_op_rows + interest_rows)
    elif interest < 0:
        indicators["H3"] = _result("H3", "資料不足", reason="利息費用為負，無法依本規格解讀", period_end=h3_period, inputs=h3_op_rows + interest_rows)
    else:
        ratio = h3_op_income / interest
        h = cfg["thresholds"]["H3"]
        status = "警示" if ratio < h["warning_below"] - 1e-12 else ("留意" if ratio < h["caution_below"] - 1e-12 else "穩健")
        indicators["H3"] = _result("H3", status, ratio, "倍", "TTM 營業利益／利息費用", h3_period, h3_op_rows + interest_rows)

    def calculate_h4(pool, pool_inputs):
        if total_debt == 0:
            return _result("H4", "穩健", 0.0, "倍", "明確無有息負債", balance_period, pool_inputs)
        if debt_current is None or pool is None or pool <= 0:
            return _result("H4", "資料不足", reason="缺一年內到期債務或現金與短期投資；分母不得為零")
        ratio = debt_current / pool
        h = cfg["thresholds"]["H4"]
        return _result("H4", _threshold(ratio, h["caution_above"], h["warning_above"]), ratio, "倍", "一年內到期有息負債／現金與短期投資", balance_period, pool_inputs)

    # H4
    if cash_pool is not None:
        indicators["H4"] = calculate_h4(cash_pool, list(balance.values()))
    elif short_term_band:
        cash_value = float(balance["FIN_CASH"]["value"])
        band_inputs = list(balance.values()) + short_term_band["inputs"]
        indicators["H4"] = _merge_band_results(
            "H4", calculate_h4(cash_value + short_term_band["lower"], band_inputs),
            calculate_h4(cash_value + short_term_band["upper"], band_inputs), short_term_band, band_inputs)
    else:
        indicators["H4"] = calculate_h4(None, list(balance.values()))

    # H5
    if ttm_fcf is None:
        indicators["H5"] = _result("H5", "資料不足", reason="缺四季 CFO 或資本支出")
    elif ttm_fcf >= 0:
        indicators["H5"] = _result("H5", "穩健", ttm_fcf, "原幣", "TTM 自由現金流非負", fcf_period, cfo_rows + capex_rows)
    else:
        annual, annual_rows = _annual_fcf(rows)
        h1 = indicators["H1"]
        if annual is None:
            indicators["H5"] = _result("H5", "資料不足", ttm_fcf, "原幣", "TTM 自由現金流為負，但缺兩個完整年度資料，無法排除警示", fcf_period, cfo_rows + capex_rows)
        else:
            two_negative = all(v < 0 for v in annual)
            if two_negative and h1.get("value") is None:
                indicators["H5"] = _result("H5", "資料不足", ttm_fcf, "原幣", "連續兩年度自由現金流為負，但 H1 缺漏，無法排除警示", fcf_period, cfo_rows + capex_rows + annual_rows)
            else:
                warning = two_negative and h1["status"] in {"警示", "留意"}
                indicators["H5"] = _result("H5", "警示" if warning else "留意", ttm_fcf, "原幣", "TTM 自由現金流為負" + ("，且連續兩年度為負、H1 少於 8 季" if warning else ""), fcf_period, cfo_rows + capex_rows + annual_rows)

    # H6
    ca_period = _common_period(rows, ("FIN_CURRENT_ASSETS", "FIN_CURRENT_LIABILITIES"))
    ca = _instant(rows, "FIN_CURRENT_ASSETS", ca_period) if ca_period else None
    cl = _instant(rows, "FIN_CURRENT_LIABILITIES", ca_period) if ca_period else None
    if not ca or not cl or float(cl["value"]) <= 0:
        indicators["H6"] = _result("H6", "資料不足", reason="缺流動資產或流動負債")
    else:
        ratio = float(ca["value"]) / float(cl["value"])
        indicators["H6"] = _result("H6", "留意" if ratio < cfg["thresholds"]["H6"]["caution_below"] - 1e-12 else "穩健", ratio, "倍", "流動資產／流動負債", ca_period, [ca, cl])

    # H7
    quarterly_shares = _latest_by_period(rows, "FIN_DILUTED_SHARES", "Q")
    annual_shares = _latest_by_period(rows, "FIN_DILUTED_SHARES", "FY")
    latest_quarter = quarterly_shares[0] if quarterly_shares else None
    latest_annual = annual_shares[0] if annual_shares else None
    use_annual = bool(latest_annual and (not latest_quarter or
                      _iso_date(latest_annual["period_end"]) > _iso_date(latest_quarter["period_end"])))
    shares = annual_shares if use_annual else quarterly_shares
    latest_share = shares[0] if shares else None
    prior_share = None
    if latest_share:
        latest_date = _iso_date(latest_share["period_end"])
        candidates = [r for r in shares[1:] if 300 <= (latest_date - _iso_date(r["period_end"])).days <= 430]
        prior_share = min(candidates, key=lambda r: abs((latest_date - _iso_date(r["period_end"])).days - 365)) if candidates else None
    if not latest_share or not prior_share or float(prior_share["value"]) <= 0:
        indicators["H7"] = _result("H7", "資料不足", reason="缺最近單季或去年同期稀釋後加權平均流通股數")
    else:
        growth = (float(latest_share["value"]) / float(prior_share["value"]) - 1) * 100
        h = cfg["thresholds"]["H7"]
        basis = "年度" if use_annual else "單季"
        indicators["H7"] = _result("H7", _threshold(growth, h["caution_above_pct"], h["warning_above_pct"]), growth, "%", f"{basis}稀釋後加權平均流通股數年增率", latest_share["period_end"], [latest_share, prior_share])

    stale_indicators = []
    for key, item in indicators.items():
        fresh_end = item.get("freshness_period_end")
        if item["status"] not in {"資料不足", "不適用"} and fresh_end and (_iso_date(cutoff) - _iso_date(fresh_end)).days > cfg["stale_after_days"]:
            stale_indicators.append(key)
            item["status_before_stale"] = item["status"]   # 過期前算出來的燈號，留著判斷是否藏了警示
            item["status"] = "資料不足"
            item["reason"] = f"{item['reason']}；實際使用資料截至 {fresh_end}，距分析日超過 {cfg['stale_after_days']} 天"
    required_missing = [k for k in REQUIRED if indicators[k]["status"] == "資料不足"]
    if not latest_period or required_missing:
        overall = "資料不足"
    elif any(v["status"] == "警示" for v in indicators.values()):
        overall = "警示"
    elif any(v["status"] == "留意" for v in indicators.values()):
        overall = "留意"
    else:
        overall = "穩健"
    # 1.3.2：非必要指標過期時，如果它最後一次算出來是留意或警示，整體不可顯示成穩健。
    stale_breaches = [k for k in stale_indicators
                      if k not in REQUIRED and indicators[k].get("status_before_stale") in {"留意", "警示"}]
    if stale_breaches and overall == "穩健":
        overall = "留意"
    reasons = []
    if gated_count:
        reasons.append(PENDING_REASON)
    if backfill_excluded:
        reasons.append("、".join(backfill_excluded) + " " + BACKFILL_EXCLUDED_REASON)
    for k in stale_breaches:
        it = indicators[k]
        reasons.append(f"{k} 最後一次可算的數值（{_fmt(it)}，截至 {it.get('freshness_period_end')}）落在{it['status_before_stale']}區，資料已過期，待新財報確認")
    if not latest_period and not gated_count:
        reasons.append("沒有可用的已申報財報 observation")
    if stale_indicators:
        reasons.append("指標資料過期：" + "、".join(stale_indicators))
    if required_missing:
        reasons.append("必要指標缺漏：" + "、".join(required_missing))
    input_ids = sorted({oid for item in indicators.values() for oid in item["observation_ids"]})
    digest = hashlib.sha256("\n".join(input_ids).encode()).hexdigest()
    unknown_ids = sorted(r.get("observation_id") for r in rows if r.get("availability_status") == "BACKFILLED_UNKNOWN")
    scope_limits = scope_limitations(rows)
    for item in indicators.values():
        for flag in scope_limits:
            item.setdefault("limitations", []).append(flag)
    for key in ("H2", "H4"):
        indicators[key].setdefault("limitations", []).append("CROSS_MARKET_NOT_COMPARABLE")
    # 1.8.1（C180-02）：用到「缺席細項視為無」的負債觀測值時，結果帶機器可讀限制與缺席清單。
    for key in ("H2", "H3", "H4"):
        used_ids = set(indicators[key]["observation_ids"])
        used = [r for r in rows if r.get("observation_id") in used_ids and r.get("absent_components")]
        if used:
            indicators[key].setdefault("limitations", []).append("ABSENT_COMPONENTS_TREATED_AS_NONE")
            indicators[key]["absent_components"] = [
                {"observation_id": r.get("observation_id"), "metric_id": r.get("metric_id"),
                 "period_end": r.get("period_end"), "local_name": a.get("local_name"),
                 "namespaces_checked": a.get("namespaces_checked")}
                for r in used for a in r["absent_components"]]
    preview_used = bool(preview_pending and not mops_enabled and
                        any(r.get("source_system") in MOPS_SOURCES for r in rows))
    limitations = list(DISCLAIMER_LINES)
    if unknown_ids:
        limitations.append("僅反映目前已補抓的財報，不能用於歷史重建。")
    limitations += scope_limits
    if preview_used:
        limitations.append(f"{PREVIEW_LABEL}：台股財報尚未通過複查與原文抽查，不可寫入帳本。")
    short_term_review = [
        {"indicator_id": key,
         "lower_status": item["band_outcomes"]["lower"]["status"],
         "upper_status": item["band_outcomes"]["upper"]["status"],
         "period_end": item["short_term_investment_band"]["period_end"]}
        for key, item in indicators.items()
        if "SHORT_TERM_INV_REVIEW_REQUIRED" in (item.get("limitations") or [])
    ]
    return {"issuer_id": issuer_id, "status": overall, "latest_period_end": latest_period,
            "required_missing": required_missing, "stale_indicators": stale_indicators,
            "stale_breaches": stale_breaches,
            "reasons": reasons, "indicators": indicators,
            "input_sha256": digest, "limitations": limitations,
            "point_in_time_replay_allowed": not bool(unknown_ids),
            "historical_ledger_write_allowed": not bool(unknown_ids) and not preview_used,
            "unknown_observation_ids": unknown_ids,
            "mops_gate": {"enabled": bool(mops_enabled), "preview_pending": bool(preview_pending),
                          "excluded_observation_count": gated_count},
            "unreviewed_preview": preview_used,
            "backfill_excluded_periods": backfill_excluded,
            "short_term_investment_review_required": short_term_review}


def evaluate_all(observations, rules, cutoff, registry=None, current_snapshot_mode=False, preview_pending=False,
                 mops_enabled=None):
    registry = registry or load_registry()
    if mops_enabled is None:
        mops_enabled = mops_gate_enabled()
    etf_issuers = {s["issuer_id"] for s in registry["securities"] if s.get("security_type") == "ETF"}
    return [evaluate_issuer(i["issuer_id"], observations, rules, cutoff, i["issuer_id"] in etf_issuers, current_snapshot_mode,
                            preview_pending, mops_enabled)
            for i in registry["issuers"]]


def limitation_note(item):
    """把指標的機器可讀限制轉成報告文字。CROSS_MARKET 只在 H2、H4 有值時顯示。"""
    notes = []
    for flag in item.get("limitations") or []:
        if flag == "CROSS_MARKET_NOT_COMPARABLE" and item.get("value") is None:
            continue
        if flag == "ABSENT_COMPONENTS_TREATED_AS_NONE":
            names = sorted({f"{a['metric_id']} {a['period_end']} {a['local_name']}" for a in item.get("absent_components") or []})
            notes.append(f"{flag}：{LIMITATION_TEXT[flag]}（缺席：{'、'.join(names)}）")
        elif flag in LIMITATION_TEXT:
            notes.append(f"{flag}：{LIMITATION_TEXT[flag]}")
    return "；".join(dict.fromkeys(notes))


def _fmt(item):
    value = item.get("value")
    outcomes = item.get("band_outcomes")
    if value is None and outcomes:
        lower, upper = outcomes["lower"], outcomes["upper"]
        if lower.get("value") is not None and upper.get("value") is not None and lower.get("unit") == upper.get("unit"):
            lo, hi = sorted((float(lower["value"]), float(upper["value"])))
            return f"{lo:.2f}～{hi:.2f}{lower.get('unit') or ''}"
        return f"下界{lower['status']}／上界{upper['status']}"
    if value is None:
        return "—"
    unit = item.get("unit") or ""
    return f"{value:.2f}{unit}" if isinstance(value, (int, float)) else f"{value}{unit}"


def short_term_investment_band_summary(results):
    evaluated = exempt = review = 0
    for result in results:
        for item in result.get("indicators", {}).values():
            flags = item.get("limitations") or []
            if "SHORT_TERM_INV_IRRELEVANT_TO_BAND" in flags:
                evaluated += 1
                exempt += 1
            elif "SHORT_TERM_INV_REVIEW_REQUIRED" in flags:
                evaluated += 1
                review += 1
    return {"evaluated_count": evaluated, "exempt_count": exempt, "review_required_count": review,
            "exempt_rate_pct": (exempt / evaluated * 100.0) if evaluated else None}


def render_section(results, rules):
    names = {i["issuer_id"]: i["issuer_name"] for i in load_registry()["issuers"]}
    band_summary = short_term_investment_band_summary(results)
    lines = ["## 公司財務體質（影子模式）", "",
             "目前多數公司可能因資料對應不完整而顯示資料不足，不代表這些公司有問題或沒問題。",
             "本節只根據已申報財報亮警示，不影響研究分類。缺資料不會顯示為穩健。", ""]
    if band_summary["evaluated_count"]:
        lines += [f"短期投資包絡可免除 AI 判讀：{band_summary['exempt_count']}/{band_summary['evaluated_count']}（{band_summary['exempt_rate_pct']:.2f}%）。", ""]
    review_rows = [(result["issuer_id"], item) for result in results
                   for item in result.get("short_term_investment_review_required") or []]
    if review_rows:
        lines += ["### 短期投資待 AI 判讀附註", "",
                  "| 發行人 | 指標 | 期末 | 下界燈號 | 上界燈號 |", "|---|---|---|---|---|"]
        for issuer_id, item in review_rows:
            lines.append(f"| {names.get(issuer_id, issuer_id)} | {item['indicator_id']} | {item['period_end']} | {item['lower_status']} | {item['upper_status']} |")
        lines += [""]
    lines += [
             "| 發行人 | 燈號 | 最新季末 | 必要缺漏 | 觸發／原因 |", "|---|---|---|---|---|"]
    for result in results:
        triggers = [f"{k} {v['status']} {_fmt(v)}" for k, v in result.get("indicators", {}).items() if v["status"] in {"留意", "警示"}]
        # 1.4.0：整體是資料不足時，已經算出來的留意與警示也要列出，不可被「資料不足」蓋掉
        causes = list(result.get("reasons") or [])
        if triggers: causes.append("已可計算的指標中觸發：" + "、".join(triggers) if causes else "、".join(triggers))
        causes = causes or ["未觸發留意或警示"]
        lines.append(f"| {names.get(result['issuer_id'], result['issuer_id'])} | {result['status']} | {result.get('latest_period_end') or '—'} | {'、'.join(result.get('required_missing') or []) or '—'} | {'；'.join(causes)} |")
    lines += ["", "### 逐公司逐指標明細", "",
              "| 發行人 | 指標 | 狀態 | 數值 | 指標期末 | 新鮮度期末 | 觸發／原因 | 零值聲明 |",
              "|---|---|---|---:|---|---|---|---|"]
    for result in results:
        name = names.get(result["issuer_id"], result["issuer_id"])
        for key in ("H1", "H2", "H3", "H4", "H5", "H6", "H7"):
            item = result.get("indicators", {}).get(key)
            if not item:
                lines.append(f"| {name} | {key} | 不適用 | — | — | — | ETF／無公司財報指標 | — |")
                continue
            zero_note = "、".join(item.get("zero_assertion_ids") or []) or "—"
            reason = str(item.get("reason") or "—")
            note = limitation_note(item)
            if note:
                reason = f"{reason}；{note}"
            reason = reason.replace("|", "／")
            lines.append(f"| {name} | {key} | {item['status']} | {_fmt(item)} | {item.get('period_end') or '—'} | {item.get('freshness_period_end') or '—'} | {reason} | {zero_note} |")
    lines += [""] + list(DISCLAIMER_LINES)
    return "\n".join(lines)


def write_ledger(results, rules, as_of, path=LEDGER, lock_holder=None):
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    with file_lock(path):
        existing = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                existing = {r.get("assessment_id") for r in csv.DictReader(f)}
        rows = []
        for result in results:
            aid = hashlib.sha256(f"{as_of}|{result['issuer_id']}|{result.get('input_sha256')}|{rules['protocol_version']}".encode()).hexdigest()
            if aid in existing:
                continue
            rows.append([aid, as_of, result["issuer_id"], result["status"], result.get("latest_period_end") or "",
                         "|".join(result.get("required_missing") or []), json.dumps(result.get("indicators") or {}, ensure_ascii=False, sort_keys=True),
                         result.get("input_sha256") or "", rules["protocol_version"], rules["health"]["status"], now])
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(LEDGER_COLS)
            writer.writerows(rows)
    return len(rows)


def output_plan(as_of, current_snapshot=False, preview_pending=False, no_ledger=False):
    """決定是否寫帳本與報告檔名。--preview-pending 一律不寫帳本（沒加 --no-ledger 也一樣）。"""
    if preview_pending:
        suffix = ("_目前狀況" if current_snapshot else "") + "_" + PREVIEW_LABEL
        return {"write_ledger": False, "report_name": f"財務體質報告_{as_of}{suffix}.md"}
    if current_snapshot or no_ledger:
        return {"write_ledger": False, "report_name": None}
    return {"write_ledger": True, "report_name": f"財務體質報告_{as_of}.md"}


def main():
    parser = argparse.ArgumentParser(description="產生財務體質影子報告；預設會 append assessment，純檢視請加 --no-ledger。")
    parser.add_argument("--as-of")
    parser.add_argument("--issuers")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--include-pending-zero-assertions", action="store_true")
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--current-snapshot", action="store_true")
    parser.add_argument("--preview-pending", action="store_true",
                        help="納入尚未啟用的台股 MOPS 財報做未經抽查的預覽；強制不寫帳本")
    parser.add_argument("--lock-holder")
    args = parser.parse_args()
    if args.current_snapshot and args.as_of:
        parser.error("--current-snapshot 與 --as-of 互斥")
    if args.preview_pending and args.as_of:
        parser.error("--preview-pending 與 --as-of 互斥")
    plan = output_plan(args.as_of or dt.date.today().isoformat(), args.current_snapshot, args.preview_pending, args.no_ledger)
    rules = load_rules()
    as_of = args.as_of or dt.date.today().isoformat()
    cutoff = as_of + "T23:59:59+00:00"
    observations = read_observations_checked()   # 1.8.3：與寫入紀錄不同步就停止
    pending_preview = args.include_pending_zero_assertions
    if pending_preview:
        from fetch_financials import ZERO_ASSERTIONS_PATH, zero_assertion_observations
        with open(ZERO_ASSERTIONS_PATH, encoding="utf-8") as handle:
            zero_data = json.load(handle)
        preview_rows, _ = zero_assertion_observations(zero_data, dt.datetime.now().astimezone().isoformat(timespec="seconds"), include_pending=True)
        observations += preview_rows
    results = evaluate_all(observations, rules, cutoff, current_snapshot_mode=args.current_snapshot,
                           preview_pending=args.preview_pending)
    if args.issuers:
        wanted = {x.strip() for x in args.issuers.split(",") if x.strip()}
        results = [x for x in results if x["issuer_id"] in wanted]
    if args.json:
        unknown_ids = sorted({oid for r in results for oid in r.get("unknown_observation_ids", [])})
        text = json.dumps({"as_of": as_of, "status": rules["health"]["status"],
                           "zero_assertion_preview": pending_preview, "issuers": results,
                           "short_term_investment_band_summary": short_term_investment_band_summary(results),
                           "POINT_IN_TIME_REPLAY_ALLOWED": not bool(unknown_ids),
                           "HISTORICAL_LEDGER_WRITE_ALLOWED": not bool(unknown_ids) and not args.preview_pending,
                           "unknown_observation_ids": unknown_ids,
                           "preview_pending": args.preview_pending,
                           "taiwan_mops_status": taiwan_gate_status(),
                           "report_label": PREVIEW_LABEL if args.preview_pending else None,
                           "limitations": DISCLAIMER_LINES}, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle: handle.write(text + "\n")
        else:
            out(text)
        return
    title = "# 財務體質報告　" + as_of + ("（目前狀況）" if args.current_snapshot else "")
    if args.preview_pending:
        title += f"（{PREVIEW_LABEL}）"
    if pending_preview:
        title += "（待複查零值試算；非 canonical）"
    text = title + "\n\n" + render_section(results, rules)
    if pending_preview:
        text += "\n\n待複查的零值聲明只用於本試算，未寫入 canonical observations，也不得建立正式 assessment。"
    unknown_ids = sorted({oid for r in results for oid in r.get("unknown_observation_ids", [])})
    if args.current_snapshot:
        text += "\n\nPOINT_IN_TIME_REPLAY_ALLOWED=false\n\nHISTORICAL_LEDGER_WRITE_ALLOWED=false\n\n僅反映目前已補抓的財報，不能用於歷史重建。\n\nunknown_observation_ids: " + json.dumps(unknown_ids)
    if args.preview_pending:
        text = (f"**{PREVIEW_LABEL}**：本報告納入台股財報轉接器的待啟用資料（taiwan.status = {taiwan_gate_status()}），"
                "尚未通過 Codex 複查與 Claude 原文抽查，只供複查者與使用者檢視；不寫任何帳本，不可當作正式結果。\n\n"
                + text + "\n\nHISTORICAL_LEDGER_WRITE_ALLOWED=false\n\n" + PREVIEW_LABEL + "；不寫帳本。")
        if not args.output:
            report = os.path.join(ROOT, "04_回測", plan["report_name"])
            with open(report, "w", encoding="utf-8") as f:
                f.write(text)
            text += f"\n\n預覽報告已存為 04_回測/{plan['report_name']}。"
    if plan["write_ledger"]:
        if pending_preview:
            raise PermissionError("PENDING_ZERO_ASSERTION_PREVIEW_CANNOT_WRITE_LEDGER")
        count = write_ledger(results, rules, as_of, lock_holder=args.lock_holder)
        report = os.path.join(ROOT, "04_回測", plan["report_name"])
        with open(report, "w", encoding="utf-8") as f:
            f.write(text)
        text += f"\n\n已新增 {count} 筆財務體質紀錄；既有 assessment 不改寫。"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle: handle.write(text + "\n")
    else:
        out(text)


if __name__ == "__main__":
    main()
