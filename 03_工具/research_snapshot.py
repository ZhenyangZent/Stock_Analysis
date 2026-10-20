"""S1A deterministic, point-in-time research snapshot.

Dry-run is deliberately zero-write.  A normal run uses the project lock and a
staging directory, but it is not enabled by this module's initial deployment.
The snapshot is descriptive only: it contains no verdict or trading command.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import attribution
import ai_fields
import data_spine
import health
import i4_capex
import lock as project_lock
import market_env
import pricedata
import risk
import trend
import valuation_v1
from t43_candidates import short_history_pe

SCHEMA_VERSION = "S1A_RESEARCH_SNAPSHOT_0.2"
DERIVATION_VERSION = "S1A_DERIVATION_0.2"
FORBIDDEN_RESEARCH_TEXT = ("可列入研究", "等待", "不行")
INPUT_PATHS = {
    "rules": "00_規範/rules.json",
    "data_spine": "00_規範/data_spine_phase1.json",
    "tag_map": "00_規範/financial_tag_map.json",
    "registry": "01_共用資料/data_spine/entity_registry.json",
    "observations": "01_共用資料/data_spine/observations.jsonl",
    "observation_ledger": "01_共用資料/data_spine/observation_write_ledger.jsonl",
    "observation_supersede_semantics": "01_共用資料/data_spine/observation_supersede_semantics.jsonl",
    "sessions": "01_共用資料/data_spine/market_sessions.csv",
    "calendar_versions": "01_共用資料/data_spine/calendar_versions.jsonl",
    "events": "01_共用資料/data_spine/events.jsonl",
    "event_ledger": "01_共用資料/data_spine/event_write_ledger.jsonl",
    "prices": "01_共用資料/prices.csv",
    "actions": "01_共用資料/corporate_actions.csv",
    "macro": "01_共用資料/macro.csv",
    "fomc_decisions": "01_共用資料/fomc_decisions.csv",
    "fx_rates": "01_共用資料/data_spine/fx_rates.csv",
    "ai_field_values": "01_共用資料/data_spine/ai_field_values.jsonl",
    "ai_field_value_voids": "01_共用資料/data_spine/ai_field_value_voids.jsonl",
    "ai_reviews": "01_共用資料/data_spine/ai_reviews.jsonl",
}
CODE_PATHS = ("common.py", "computed_provenance.py", "data_spine.py", "pricedata.py", "market_env.py", "trend.py", "health.py",
              "attribution.py", "risk.py", "fetch_macro.py", "research_snapshot.py", "valuation_v1.py", "ai_fields.py", "i4_capex.py",
              "t43_candidates.py")
APPEND_INPUTS = {"observations", "observation_ledger", "observation_supersede_semantics",
                 "events", "event_ledger", "prices", "macro", "sessions", "calendar_versions",
                 "ai_field_values", "ai_field_value_voids", "ai_reviews"}


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def csv_rows(data):
    if not data:
        return []
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def jsonl_rows(data):
    return [json.loads(x) for x in data.decode("utf-8-sig").splitlines() if x.strip()]


def capture_inputs(root=ROOT):
    out = {}
    for name, rel in INPUT_PATHS.items():
        p = root / rel
        out[name] = p.read_bytes() if p.exists() else b""
    for name in CODE_PATHS:
        out["code:" + name] = (root / "03_工具" / name).read_bytes()
    return out


def input_hashes(captured):
    return {k: {"sha256": digest(v), "byte_length": len(v)} for k, v in sorted(captured.items())}


def _last_ledger(ledger):
    rows = jsonl_rows(ledger)
    return rows[-1] if rows else None


def verify_observation_ledger(captured):
    obs = captured["observations"]
    last = _last_ledger(captured["observation_ledger"])
    if not last or int(last.get("file_byte_length_after", -1)) != len(obs) or last.get("file_sha256_after") != digest(obs):
        raise ValueError("OBSERVATION_WRITE_LEDGER_SYNC_GATE failed")
    pending = ROOT / "01_共用資料/data_spine/observations.jsonl.write_journal.json"
    if pending.exists():
        raise ValueError("OBSERVATION_PENDING_JOURNAL_GATE failed")
    return last


def _written_times(captured):
    result = {}
    for batch in jsonl_rows(captured["observation_ledger"]):
        when = batch.get("written_at") or batch.get("recorded_at")
        for oid in batch.get("observation_ids", []):
            result[oid] = when
    return result


def frozen_observations(captured):
    verify_observation_ledger(captured)
    times = _written_times(captured)
    rows = jsonl_rows(captured["observations"])
    for row in rows:
        row["selection_written_at"] = times.get(row.get("observation_id"), row.get("ingested_at"))
    semantics = jsonl_rows(captured.get("observation_supersede_semantics", b""))
    for item in semantics:
        errors = data_spine.validate_supersede_semantic(item, {r.get("observation_id") for r in rows})
        if errors:
            raise ValueError("SUPERSEDE_SEMANTICS_GATE failed: " + "|".join(errors))
    return data_spine.apply_supersede_semantics(rows, semantics)


def bound_sessions(captured, cutoff):
    versions = jsonl_rows(captured["calendar_versions"])
    if not versions:
        raise ValueError("CALENDAR_VERSION_GATE failed")
    version = versions[-1]
    rows = csv_rows(captured["sessions"])
    count = int(version["row_count"])
    if len(rows) < count:
        raise ValueError("CALENDAR_PREFIX_ROW_GATE failed")
    # The ledger binds the complete current CSV in this version.
    if count == len(rows) and digest(captured["sessions"]) != version["calendar_sha256"]:
        raise ValueError("CALENDAR_PREFIX_HASH_GATE failed")
    rows = rows[:count]
    superseded = {r.get("supersedes_session_id") for r in rows if r.get("supersedes_session_id")}
    active = [r for r in rows if r.get("session_id") not in superseded]
    active = [r for r in active if r.get("session_status") == "OPEN" and parse_time(r.get("close_at")) <= cutoff]
    # Match data_spine.load_sessions: append order is not chronological.
    active.sort(key=lambda r: (r.get("market", ""), r.get("open_at", "")))
    return active, version


def _registry_maps(registry):
    by_ticker = {}
    for sec in registry["securities"]:
        by_ticker[sec["ticker"]] = sec
        for alias in sec.get("aliases", []):
            by_ticker[alias] = sec
    return by_ticker


def frozen_prices(captured, registry, sessions, cutoff):
    by_ticker = _registry_maps(registry)
    rows, rejected = [], []
    for raw in csv_rows(captured["prices"]):
        sec = by_ticker.get(raw.get("ticker"))
        try:
            ok = sec and float(raw["close"]) > 0 and parse_time(raw.get("fetched_at")) <= cutoff
        except Exception:
            ok = False
        if not ok:
            rejected.append(raw)
            continue
        row = dict(raw); row["ticker"] = sec["ticker"]; row["security_id"] = sec["security_id"]
        row["row_sha256"] = digest(canonical_bytes(raw)); rows.append(row)
    anchors = {}
    for market in ("TW", "US"):
        priced_dates = {r["date"] for r in rows if by_ticker[r["ticker"]]["market"] == market}
        dates = [s["session_date"] for s in sessions if s["market"] == market and s["session_date"] in priced_dates]
        anchors[market] = max(dates) if dates else None
    rows = [r for r in rows if r["date"] <= (anchors[by_ticker[r["ticker"]]["market"]] or "")]
    return rows, anchors, rejected


def frozen_actions(captured, registry, anchors, cutoff):
    by_ticker = _registry_maps(registry); out = []
    for raw in csv_rows(captured["actions"]):
        sec = by_ticker.get(raw.get("ticker"))
        try:
            ok = sec and parse_time(raw.get("fetched_at")) <= cutoff and raw["date"] <= anchors.get(sec["market"], "")
        except Exception:
            ok = False
        if ok:
            r = dict(raw); r["ticker"] = sec["ticker"]; r["row_sha256"] = digest(canonical_bytes(raw)); out.append(r)
    return out


def frozen_macro(captured, cutoff):
    out = []
    for raw in csv_rows(captured["macro"]):
        try: ok = parse_time(raw.get("fetched_at")) <= cutoff
        except Exception: ok = False
        if ok:
            r = dict(raw); r["row_sha256"] = digest(canonical_bytes(raw)); out.append(r)
    return out


def frozen_fx(captured, cutoff):
    out = []
    for raw in csv_rows(captured.get("fx_rates", b"")):
        try: ok = parse_time(raw.get("available_at")) <= cutoff
        except Exception: ok = False
        if ok: out.append(dict(raw))
    return out


def price_structures(rows, return_stats=False):
    """Match pricedata.load_prices: first (ticker, date) wins, then sort by date."""
    by_date, duplicates = {}, {}
    for r in rows:
        ticker, date = r["ticker"], r["date"]
        target = by_date.setdefault(ticker, {})
        if date in target:
            duplicates[ticker] = duplicates.get(ticker, 0) + 1
            continue
        try: volume = float(r.get("volume") or "")
        except (TypeError, ValueError): volume = None
        target[date] = (float(r["close"]), volume)
    series = {ticker: [(date, close, volume) for date, (close, volume) in sorted(values.items())]
              for ticker, values in sorted(by_date.items())}
    volumes = {ticker: {date: volume for date, _, volume in values if volume is not None}
               for ticker, values in series.items()}
    stats = {"duplicate_rows": sum(duplicates.values()),
             "duplicate_rows_by_ticker": dict(sorted(duplicates.items()))}
    return (series, volumes, stats) if return_stats else (series, volumes)


def action_structures(rows, return_stats=False):
    """Match pricedata.load_actions: aggregate every valid action; do not de-duplicate payouts."""
    raw, seen, repeated = {}, set(), {}
    for r in rows:
        try: value = float(r["value"])
        except (KeyError, TypeError, ValueError): continue
        ticker, date, kind = r.get("ticker"), r.get("date"), r.get("type")
        if not ticker or not date or kind not in {"現金股利", "分割", "股票股利"}: continue
        key = (ticker, date, kind)
        if key in seen: repeated[ticker] = repeated.get(ticker, 0) + 1
        seen.add(key)
        d = raw.setdefault(ticker, {}).setdefault(date, {"div": 0.0, "split": 1.0})
        if kind == "現金股利": d["div"] += value
        elif kind in {"分割", "股票股利"} and value > 0: d["split"] *= value
    out = {ticker: {date: values for date, values in sorted(dates.items())}
           for ticker, dates in sorted(raw.items())}
    stats = {"same_key_rows": sum(repeated.values()),
             "same_key_rows_by_ticker": dict(sorted(repeated.items())),
             "semantics": "AGGREGATE_ALL_VALID_ROWS"}
    return (out, stats) if return_stats else out


def macro_structure(rows, return_stats=False):
    """Match fetch_macro.load_macro: first (series_id, date) wins, then sort by date."""
    out, duplicates = {}, {}
    for r in rows:
        series_id, date = r["series_id"], r["date"]
        target = out.setdefault(series_id, {})
        if date in target:
            duplicates[series_id] = duplicates.get(series_id, 0) + 1
            continue
        target[date] = float(r["value"])
    result = {series_id: sorted(values.items()) for series_id, values in sorted(out.items())}
    stats = {"duplicate_rows": sum(duplicates.values()),
             "duplicate_rows_by_series": dict(sorted(duplicates.items()))}
    return (result, stats) if return_stats else result


def fomc_decision_structure(data):
    rows = csv_rows(data)
    return [dict(row, target_upper_pct=float(row["target_upper_pct"]))
            for row in sorted(rows, key=lambda item: item["statement_date"])]


def price_anchor_status(series, anchors, registry):
    """Every enabled series, including benchmarks, must end at its market anchor."""
    status = {}
    for sec in registry.get("securities", []):
        if not sec.get("price_series_enabled"): continue
        ticker, market = sec["ticker"], sec["market"]
        last = series.get(ticker, [])[-1][0] if series.get(ticker) else None
        anchor = anchors.get(market)
        status[ticker] = {"status": "OK" if anchor and last == anchor else "PRICE_NOT_AT_ANCHOR",
                          "last_price_date": last, "market_anchor": anchor}
    return status


def _mops_enabled(tag_map):
    value = tag_map.get("taiwan", {}).get("status")
    return value in ("ENABLED", "ENABLED_AFTER_INDEPENDENT_REVIEW")


def _observation_ids(result):
    ids = set()
    for indicator in (result or {}).get("indicators", {}).values():
        ids.update(x for x in indicator.get("observation_ids", []) if x)
    return sorted(ids)


def trend_provenance(issuer_id, observations, cutoff, mops_enabled):
    """Reproduce trend.py's exact selected rows and expose IDs per derived C value."""
    issuer = [r for r in observations if r.get("issuer_id") == issuer_id]
    issuer, _ = health.gate_pending_sources(issuer, mops_enabled, False)
    rows = health.consistent_report_scope(health.point_in_time(issuer, issuer_id, cutoff))
    rev_rows = health._latest_by_period(rows, "FIN_REVENUE", "Q")
    gp_rows = health._latest_by_period(rows, "FIN_GROSS_PROFIT", "Q")
    rev = {r["period_end"]: r for r in rev_rows}; gp = {r["period_end"]: r for r in gp_rows}
    if not rev: return {"C1": [], "C2": [], "C3": []}
    end = max(rev); ly = trend._same_quarter_last_year(rev, end); prev = trend._prev_quarter(rev, end)
    prev_ly = trend._same_quarter_last_year(rev, prev) if prev else None
    def ids(*items): return sorted({x["observation_id"] for x in items if x and x.get("observation_id")})
    return {"C1": ids(rev.get(end), rev.get(ly)),
            "C2": ids(rev.get(end), rev.get(ly), rev.get(prev), rev.get(prev_ly)),
            "C3": ids(rev.get(end), rev.get(ly), gp.get(end), gp.get(ly))}


def _all_bound_sessions(captured, version):
    """綁定版本內全部 OPEN session（含 cutoff 之後；月底判斷只用到 cutoff 前的月份）。"""
    rows = csv_rows(captured["sessions"])[:int(version["row_count"])]
    superseded = {r.get("supersedes_session_id") for r in rows if r.get("supersedes_session_id")}
    return [r for r in rows if r.get("session_id") not in superseded and r.get("session_status") == "OPEN"]


def _candidate_latest_current_ai_rows(values, cutoff, documents, voids):
    """1.13.1 candidate selection: newest row wins, with field-specific currentness."""
    latest = {}
    for value in values:
        if not value.get("available_at") or parse_time(value["available_at"]) > cutoff:
            continue
        key = (value.get("field"), value.get("scope"))
        rank = (value.get("available_at", ""), value.get("value_id", ""))
        if key not in latest or rank > latest[key][0]:
            latest[key] = (rank, value)
    allowed = {ai_fields.STATUS_VERIFIED, ai_fields.STATUS_DISAGREE}
    return {key: value for key, (_, value) in latest.items()
            if value.get("status") in allowed and value.get("value") is not None
            and ai_fields.is_current_for_field(value, documents, voids)}


def _activated_ai_fields(captured, cutoff, rules, registry, observations,
                         enforce_current=False, documents=None):
    """rules 1.11.0：以凍結帳本啟用 C5／C6／I2，I4 僅取 SEC 實際資本支出。

    欄位值只經 report_field_values() 讀取，使 void 與所有標註語意集中在同一入口。
    回傳值會存進 snapshot.json，讓第二階段重播不再讀現場帳本。
    """
    report = ai_fields.report_field_values(
        jsonl_rows(captured["ai_field_values"]),
        jsonl_rows(captured["ai_field_value_voids"]),
        jsonl_rows(captured["ai_reviews"]),
    )
    allowed_status = {ai_fields.STATUS_VERIFIED, ai_fields.STATUS_DISAGREE}
    voids = jsonl_rows(captured["ai_field_value_voids"])
    if enforce_current:
        # 1.13.1 candidate: rank every as-of row first.  A newest null,
        # disallowed, voided, or stale row makes the field absent; it must not
        # expose an older quarter merely because that older value was usable.
        if documents is None:
            raise ValueError("AI_CURRENTNESS_DOCUMENTS_REQUIRED")
        latest = _candidate_latest_current_ai_rows(report["values"], cutoff, documents, voids)
        visible = []
    else:
        visible = [v for v in report["values"]
                   if v.get("status") in allowed_status and v.get("value") is not None
                   and parse_time(v.get("available_at")) <= cutoff]
    if not enforce_current:
        latest = {}
        for value in visible:
            key = (value.get("field"), value.get("scope"))
            if key not in latest or value.get("available_at", "") > latest[key].get("available_at", ""):
                latest[key] = value

    def payload(row, source):
        if not row:
            return None
        return {"value": row["value"], "status": "已開原文", "type": "實績", "source": source,
                "as_of": row["available_at"][:10], "value_id": row.get("value_id"),
                "annotations": row.get("annotations", []),
                "extraction_annotations": row.get("extraction_annotations", [])}

    issuers = {s.get("ticker"): s.get("issuer_id") for s in registry.get("securities", [])}
    companies = {}
    for issuer in sorted({s.get("issuer_id") for s in registry.get("securities", []) if s.get("issuer_id")}):
        fields = {}
        for field in ("C5", "C6"):
            item = payload(latest.get((field, issuer)), "S4 verified dual extraction")
            if item:
                fields[field] = item
        companies[issuer] = fields

    actual_i4 = i4_capex.current_snapshot(cutoff.isoformat(), observations)
    i4_value = {"擴張": "上修", "多家收縮": "多家下修", "一家收縮": "一家下修", "持平": "持平"}.get(
        actual_i4["aggregate"].get("value"))
    themes = {}
    i2_policy = (((rules.get("verdict") or {}).get("ai_fields_accepted") or {}).get("theme") or {})
    i2_priority = i2_policy.get("I2_company_priority") or ["I2"]
    for theme_id, theme in sorted(rules.get("themes", {}).items()):
        company_i2, company_i2q, used_by_id = {}, {}, {}
        for ticker in theme.get("watchlist", []):
            row = latest.get(("I2", issuers.get(ticker)))
            if row:
                company_i2[ticker] = {"value": row["value"]}
                used_by_id[row.get("value_id") or id(row)] = row
            if "I2Q" in i2_priority:
                rowq = latest.get(("I2Q", issuers.get(ticker)))
                if rowq:
                    company_i2q[ticker] = {"value": rowq["value"]}
        if "I2Q" in i2_priority:
            i2 = ai_fields.aggregate_i2_combined(company_i2, company_i2q)
            for ticker, source in (i2.get("sources") or {}).items():
                field = "I2" if source == "I2" else "I2Q"
                row = latest.get((field, issuers.get(ticker)))
                if row:
                    used_by_id[row.get("value_id") or id(row)] = row
        else:
            i2 = ai_fields.aggregate_i2(company_i2)
        used = list(used_by_id.values())
        annotations = [a for row in used for a in row.get("annotations", [])]
        basis = sorted({b for a in annotations for b in a.get("basis", [])})
        fields = {}
        if i2.get("value") is not None:
            fields["I2"] = {"value": i2["value"], "status": "已開原文", "type": "實績",
                            "source": "S4 verified dual extraction aggregate", "as_of": cutoff.date().isoformat(),
                            "detail": i2, "annotations": annotations, "review_basis": basis,
                            "independence_partial": any(a.get("independence") == "INDEPENDENCE_PARTIAL" for a in annotations)}
        if i4_value is not None:
            fields["I4"] = {"value": i4_value, "status": "已開原文", "type": "實績",
                            "source": i4_capex.SOURCE_SYSTEM, "as_of": cutoff.date().isoformat(),
                            "policy": actual_i4.get("policy"),
                            "acceleration_shadow": {issuer: item.get("acceleration")
                                                    for issuer, item in actual_i4.get("companies", {}).items()},
                            "aggregate_detail": actual_i4.get("aggregate")}
        themes[theme_id] = fields
    return {"reader": "ai_fields.report_field_values", "companies": companies, "themes": themes,
            "i4_actual_capex": actual_i4, "unattached_annotations": report.get("unattached_annotations", [])}


def build_snapshot(captured, cutoff):
    rules = json.loads(captured["rules"]); registry = json.loads(captured["registry"])
    c4_disabled = ((rules.get("company", {}).get("indicators", {}).get("C4", {})
                    .get("status")) == "DISABLED_BY_USER_DECISION")
    optical = rules.get("themes", {}).get("optical", {})
    mrvl_single_scored_theme = ("MRVL" not in optical.get("watchlist", [])
                                and "MRVL" in optical.get("description_only", [])
                                and "MRVL" in rules.get("themes", {}).get("network_asic", {}).get("watchlist", []))
    tag_map = json.loads(captured["tag_map"]); observations = frozen_observations(captured)
    sessions, cal_version = bound_sessions(captured, cutoff)
    sessions_all = _all_bound_sessions(captured, cal_version)
    prices, anchors, rejected_prices = frozen_prices(captured, registry, sessions, cutoff)
    actions = frozen_actions(captured, registry, anchors, cutoff); macros = frozen_macro(captured, cutoff)
    fx_rows = frozen_fx(captured, cutoff)
    series, volumes, price_stats = price_structures(prices, return_stats=True)
    acts, action_stats = action_structures(actions, return_stats=True)
    macro, macro_stats = macro_structure(macros, return_stats=True)
    price_status = price_anchor_status(series, anchors, registry)
    soxx = pricedata.tr_index(series.get("SOXX", []), acts.get("SOXX", {}))
    fomc_decisions = fomc_decision_structure(captured["fomc_decisions"])
    market_values = market_env.compute(macro, soxx, anchors.get("US"), rules["market_env"],
                                       point_in_time=False, fomc_decisions=fomc_decisions)
    market_result = market_env.assess(market_values, rules)
    enabled = [s for s in registry["securities"] if s.get("price_series_enabled") and s.get("security_type") != "ETF"]
    tickers = [s["ticker"] for s in enabled]
    tr = trend.evaluate_all(observations, rules, cutoff.isoformat(), registry=registry,
                            current_snapshot_mode=False, preview_pending=False, mops_enabled=_mops_enabled(tag_map))
    hr_list = health.evaluate_all(observations, rules, cutoff.isoformat(), registry=registry,
                                  current_snapshot_mode=False, preview_pending=False, mops_enabled=_mops_enabled(tag_map))
    hr = {r["issuer_id"]: r for r in hr_list}
    breadth = trend.theme_breadth(tr, rules, registry)
    activated_fields = _activated_ai_fields(captured, cutoff, rules, registry, observations)
    session_binding = {m: next((dict(s, row_sha256=digest(canonical_bytes(s))) for s in reversed(sessions)
                               if s["market"] == m and s["session_date"] == anchors.get(m)), None)
                       for m in ("TW", "US")}
    attribution_rows = {}
    for market in ("TW", "US"):
        benchmark = rules["benchmarks"]["default_tw" if market == "TW" else "default_us"]
        market_tickers = [s["ticker"] for s in enabled if s["market"] == market
                          and price_status.get(s["ticker"], {}).get("status") == "OK"
                          and price_status.get(benchmark, {}).get("status") == "OK"]
        ctx = attribution.build_context(rules, as_of=anchors.get(market), prices=series, actions=acts)
        for row in attribution.scan_latest(rules, ctx, tickers=market_tickers, force=True):
            attribution_rows.setdefault(row.get("ticker"), []).append(row)
    companies = []
    for sec in sorted(enabled, key=lambda x: x["security_id"]):
        ticker = sec["ticker"]; benchmark = rules["benchmarks"]["default_tw" if sec["market"] == "TW" else "default_us"]
        price_problems = [x for x in (ticker, benchmark) if price_status.get(x, {}).get("status") != "OK"]
        company_price_status = "OK" if not price_problems else "PRICE_NOT_AT_ANCHOR"
        price_reason = None if not price_problems else "PRICE_NOT_AT_ANCHOR:" + ",".join(price_problems)
        if company_price_status == "OK":
            bench_ret = pricedata.total_returns(series.get(benchmark, []), acts.get(benchmark, {}))
            rr = risk.asset_report(ticker, series.get(ticker, []), acts.get(ticker, {}), bench_ret,
                                   rules["risk"], anchors.get(sec["market"]), sec.get("market"), anchors.get(sec["market"]))
            company_attribution = attribution_rows.get(ticker)
        else:
            rr = {"ticker": ticker, "market": sec.get("market"), "status": "PRICE_NOT_AT_ANCHOR",
                  "reason": price_reason, "data_cutoff": None}
            company_attribution = [{"ticker": ticker, "market": sec.get("market"),
                                    "status": "PRICE_NOT_AT_ANCHOR", "reason": price_reason}]
        trend_result = tr.get(sec["issuer_id"]); health_result = hr.get(sec["issuer_id"])
        issuer_stale_reasons = []
        for indicator in (health_result or {}).get("stale_indicators", []):
            issuer_stale_reasons.append("health:" + indicator)
        if any("距基準日超過" in str(reason) for reason in (trend_result or {}).get("reasons", [])):
            issuer_stale_reasons.append("trend:latest_quarter")
        cprov = trend_provenance(sec["issuer_id"], observations, cutoff.isoformat(), _mops_enabled(tag_map))
        price_hashes = [r["row_sha256"] for r in prices if r["ticker"] in {ticker, benchmark}]
        # S3c：V1 由版本保留申報＋未調整月底收盤價即時計算；倍數不寫 observations，明細與輸入雜湊存快照。
        v1 = valuation_v1.compute(sec, observations, [r for r in prices if r["ticker"] == ticker], actions,
                                  sessions_all, anchors.get(sec["market"]), cutoff, rules, fx_rows=fx_rows)
        # G-V1-ALT: walk the approved ladder only when the primary V1 is not
        # usable.  Each rung retains the primary result for replay; alternative
        # valuation may penalize but can never award a positive score.
        alt_policy = rules.get("v1_alternative") or {}
        if alt_policy.get("enabled") is True and v1.get("status") != "OK":
            alt = short_history_pe(
                v1, minimum_valid_months=int(alt_policy.get("short_history_min_valid_months", 36)))
            if alt.get("status") != "OK" and "V1_ALT_EV_SALES" in alt_policy.get("order", []):
                alt = valuation_v1.compute_ev_sales_alternative(
                    sec, observations, [r for r in prices if r["ticker"] == ticker], actions,
                    sessions_all, anchors.get(sec["market"]), cutoff, rules,
                    minimum_valid_months=int(alt_policy.get("short_history_min_valid_months", 36)))
            if alt.get("status") == "OK":
                alt["primary"] = v1
                alt["score_cap"] = int(alt_policy.get("score_cap", 0))
                v1 = alt
        action_hashes = [r["row_sha256"] for r in actions if r["ticker"] in {ticker, benchmark}]
        theme_id = _theme_of(ticker, rules)
        activated_company = activated_fields["companies"].get(sec["issuer_id"], {})
        activated_theme = activated_fields["themes"].get(theme_id, {})
        companies.append({
            "security_id": sec["security_id"], "issuer_id": sec["issuer_id"], "ticker": ticker,
            "market": sec["market"], "currency": sec["currency"],
            "benchmark_security_id": "SEC_0050_TW" if sec["market"] == "TW" else "SEC_SOXX_US",
            "benchmark_ticker": benchmark, "anchor_session": anchors.get(sec["market"]),
            "price_status": company_price_status,
            "price_status_detail": {x: price_status.get(x) for x in (ticker, benchmark)},
            "trend": trend_result, "trend_provenance": cprov,
            "health": health_result,
            "health_provenance": {k: v.get("observation_ids", []) for k, v in (health_result or {}).get("indicators", {}).items()},
            "issuer_data_status": "ISSUER_DATA_STALE" if issuer_stale_reasons else "OK",
            "issuer_data_stale_reasons": issuer_stale_reasons,
            "attribution": company_attribution, "risk": rr, "valuation_v1": v1,
            "price_row_sha256": price_hashes, "action_row_sha256": action_hashes,
            "formal_readiness": {"V1": v1.get("status") == "OK", "EVENT_DISTANCE": False,
                                 "C5": activated_company.get("C5") is not None,
                                 "C6": activated_company.get("C6") is not None,
                                 "I2": activated_theme.get("I2") is not None, "I3": False,
                                 "I4": activated_theme.get("I4") is not None,
                                 "D6_C4_RULE_DECISION_IMPLEMENTED": c4_disabled,
                                 "D9_MRVL_RULE_DECISION_IMPLEMENTED": mrvl_single_scored_theme if ticker == "MRVL" else None,
                                 "D11_TW_ACTION_COVERAGE_VERIFIED": False if sec["market"] == "TW" else None}
        })
    return {
        "schema_version": SCHEMA_VERSION, "derivation_version": DERIVATION_VERSION,
        "data_cutoff_at": cutoff.isoformat(), "anchors": anchors,
        "calendar_version_id": cal_version["calendar_version_id"], "session_binding": session_binding,
        "market": {"values": market_values, "assessment": market_result,
                   "macro_row_sha256": [r["row_sha256"] for r in macros],
                   "soxx_price_row_sha256": [r["row_sha256"] for r in prices if r["ticker"] == "SOXX"]},
        "theme_breadth": breadth, "activated_fields": activated_fields, "companies": companies,
        "gates": {"observation_ledger_sync": "PASS", "calendar_binding": "PASS",
                  "rejected_price_rows": len(rejected_prices), "company_count": len(companies),
                  "duplicate_price_rows": price_stats["duplicate_rows"],
                  "duplicate_price_rows_by_ticker": price_stats["duplicate_rows_by_ticker"],
                  "action_same_key_rows": action_stats["same_key_rows"],
                  "action_semantics": action_stats["semantics"],
                  "duplicate_macro_rows": macro_stats["duplicate_rows"],
                  "price_not_at_anchor": sorted(t for t, item in price_status.items()
                                                if item["status"] != "OK")},
        "limitations": (["D6_RETROSPECTIVE_UNIVERSE", "D11_TW_ACTION_COVERAGE_UNVERIFIED"]
                        + ([] if c4_disabled else ["C4_CROSS_COMPANY_COMPARABILITY_NOT_IMPLEMENTED"])
                        + ([] if mrvl_single_scored_theme else ["D9_MRVL_DOUBLE_COUNT_FIX_NOT_IMPLEMENTED"]))
    }


def build_input_manifest(captured, cutoff, snapshot):
    try:
        import importlib.metadata as md
        tzdata = md.version("tzdata")
    except Exception:
        tzdata = "UNKNOWN"
    inputs = {}
    for name, data in sorted(captured.items()):
        item = {"sha256": digest(data), "byte_length": len(data), "cas_sha256": digest(data)}
        if name in APPEND_INPUTS:
            item.update(storage="PREFIX_AND_CAS", prefix_byte_length=len(data),
                        prefix_sha256=digest(data), row_count=max(0, len(data.decode("utf-8-sig").splitlines()) - (1 if data else 0)))
            if name == "observation_ledger":
                last = _last_ledger(data); item["last_batch_id"] = last.get("batch_id") if last else None
        else:
            item["storage"] = "CAS"
        inputs[name] = item
    return {"schema_version": SCHEMA_VERSION, "derivation_version": DERIVATION_VERSION,
            "serializer": "CANONICAL_JSON_UTF8_SORTED_COMPACT_LF_V1",
            "data_cutoff_at": cutoff.isoformat(), "cli": ["research_snapshot.py"],
            "environment": {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "tzdata": tzdata},
            "inputs": inputs, "anchors": snapshot["anchors"], "session_binding": snapshot["session_binding"],
            "normalization": {"duplicate_price_rows": snapshot["gates"].get("duplicate_price_rows", 0),
                              "duplicate_price_rows_by_ticker": snapshot["gates"].get("duplicate_price_rows_by_ticker", {}),
                              "action_same_key_rows": snapshot["gates"].get("action_same_key_rows", 0),
                              "action_semantics": snapshot["gates"].get("action_semantics"),
                              "duplicate_macro_rows": snapshot["gates"].get("duplicate_macro_rows", 0)},
            "macro_revision_vintage_available": False, "macro_limitation": "MACRO_PUBLICATION_TIME_NOT_CAPTURED",
            "gates": snapshot["gates"]}


def make_run_id(cutoff, input_manifest_bytes):
    stamp = cutoff.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "SNAP_" + stamp + "_" + digest(input_manifest_bytes)[:8]


def restore_inputs(input_manifest, cas_dir, current=None):
    """Restore exact input bytes; a matching live prefix may substitute for CAS."""
    restored = {}
    for name, meta in input_manifest["inputs"].items():
        live = (current or {}).get(name)
        n = int(meta.get("prefix_byte_length", meta["byte_length"]))
        if live is not None and len(live) >= n and digest(live[:n]) == meta.get("prefix_sha256", meta["sha256"]):
            data = live[:n]
        else:
            data = (Path(cas_dir) / meta["cas_sha256"]).read_bytes()
        if digest(data) != meta["sha256"] or len(data) != int(meta["byte_length"]):
            raise ValueError("REPLAY_INPUT_HASH_GATE failed: " + name)
        restored[name] = data
    return restored


def replay_snapshot(input_manifest, cas_dir, current=None):
    expected_env = input_manifest["environment"]
    def py_mm(value):
        m = re.match(r"^(\d+)\.(\d+)", str(value or ""))
        return tuple(map(int, m.groups())) if m else None
    if py_mm(expected_env.get("python")) != py_mm(sys.version):
        raise RuntimeError("REPLAY_ENVIRONMENT_COMPATIBILITY_GATE failed")
    warnings = []
    if expected_env.get("python") != sys.version:
        warnings.append("REPLAY_PYTHON_PATCH_DIFFERS")
    try:
        import importlib.metadata as md
        current_tzdata = md.version("tzdata")
    except Exception:
        current_tzdata = "UNKNOWN"
    if expected_env.get("tzdata", "UNKNOWN") != current_tzdata:
        warnings.append("REPLAY_TZDATA_DIFFERS")
    captured = restore_inputs(input_manifest, cas_dir, current)
    snapshot = build_snapshot(captured, parse_time(input_manifest["data_cutoff_at"]))
    if warnings:
        snapshot["replay_warnings"] = warnings
    return snapshot


def classify_difference(previous_manifest, current_manifest, previous_snapshot, current_snapshot, current_observations=()):
    if previous_snapshot is None: return "NEW_DATA"
    old_inputs, new_inputs = previous_manifest.get("inputs", {}), current_manifest.get("inputs", {})
    derivation = any(old_inputs.get(k, {}).get("sha256") != new_inputs.get(k, {}).get("sha256")
                     for k in new_inputs if k.startswith("code:") or k in {"rules", "data_spine", "tag_map", "registry"})
    old_ids = {oid for c in previous_snapshot.get("companies", []) for v in c.get("trend_provenance", {}).values() for oid in v}
    new_ids = {oid for c in current_snapshot.get("companies", []) for v in c.get("trend_provenance", {}).values() for oid in v}
    correction = bool(new_ids - old_ids) and any(r.get("observation_id") in new_ids and r.get("supersedes_observation_id") in old_ids
                                                  for r in current_observations)
    new_data = previous_snapshot.get("anchors") != current_snapshot.get("anchors") or new_ids != old_ids
    if derivation and (correction or new_data): return "MIXED"
    if derivation: return "DERIVATION_CHANGE"
    if correction: return "CORRECTION"
    if new_data: return "NEW_DATA"
    return "UNKNOWN"


def _display(value, digits=1):
    if value is None: return "—"
    if isinstance(value, float): return f"{value:.{digits}f}"
    return str(value)


def _health_triggers(company):
    indicators = (company.get("health") or {}).get("indicators", {})
    return [(key, item) for key, item in indicators.items() if item.get("status") in {"留意", "警示"}]


def _v1_cell(company):
    v1 = company.get("valuation_v1") or {}
    if v1.get("status") != "OK": return v1.get("status") or "—"
    detail = v1.get("primary") or v1
    method_labels = {"V1_ALT_SHORT_HISTORY": "；替代短歷史",
                     "V1_ALT_EV_SALES": "；替代 EV/Sales"}
    method = method_labels.get(v1.get("method"), "")
    return (f"{v1['value']:.2f}（有效 {detail.get('valid_months', '—')}／"
            f"無效 {detail.get('invalid_months', '—')}{method}）")


def _attribution_summary(company):
    rows = [r for r in company.get("attribution") or [] if r.get("status") == "OK"]
    if not rows: return "資料不足"
    row = next((r for r in rows if r.get("window_days") == 20), rows[-1])
    return f"{row.get('window_days')}日 {_display(row.get('total_return') * 100 if row.get('total_return') is not None else None)}%；{row.get('classification') or '未分類'}"


def research_page(snapshot, run_id=None, status=None, difference="NEW_DATA"):
    status = status or snapshot_status(snapshot)
    lines = ["# S1A 確定性研究快照", "", f"RUN_ID：`{run_id or 'DRY_RUN'}`", f"STATUS：`{status}`",
             f"資訊截止：`{snapshot['data_cutoff_at']}`", f"TW 價格 session：`{snapshot['anchors'].get('TW')}`",
             f"US 價格 session：`{snapshot['anchors'].get('US')}`", "",
             "本頁是研究參考，非投資建議；只整理凍結資料與影子模組結果，不含交易指令、價格目標或個人部位安排。", "",
             f"影子分類預覽（非正式、影子）：同目錄 [{SHADOW_PAGE}]({SHADOW_PAGE})；第二階段失敗時見 `{SHADOW_FAILURE}`。", "",
             "## 資料健康", "", f"差異原因：`{difference}`", "", "限制：" + "；".join(snapshot["limitations"]), "",
             "事件日曆規則：基準日後須涵蓋 20 個已公布交易日；已登記事件若落在日曆末端之外，仍回報 `CALENDAR_COVERAGE_MISSING`。不外推未公布日曆。", "",
             "## 市場環境", "",
             f"燈號：`{snapshot['market']['assessment'].get('status')}`；分數：`{snapshot['market']['assessment'].get('score')}`；覆蓋：`{snapshot['market']['assessment'].get('filled')}/7`", "",
             "| 指標 | 值 | 分數 | 資料日期 | 說明／proxy |", "|---|---:|---:|---|---|"]
    issuer_stale = [c for c in snapshot.get("companies", []) if c.get("issuer_data_status") == "ISSUER_DATA_STALE"]
    if issuer_stale:
        stale_lines = [f"- {c['ticker']}：`ISSUER_DATA_STALE`（{', '.join(c.get('issuer_data_stale_reasons', []))}）"
                       for c in issuer_stale]
        insert_at = lines.index("## 市場環境")
        lines[insert_at:insert_at] = ["個別公司資料過期（不改變整體 STATUS）：", ""] + stale_lines + [""]
    proxies = {"M1": "官方 FOMC 最近一次會議決定", "M6": "SOXX 含息"}
    scores = snapshot["market"]["assessment"].get("scores", {})
    for key in ("M1", "M2", "M3", "M4", "M5", "M6", "M7"):
        item = snapshot["market"].get("values", {}).get(key, {})
        note = "；".join(x for x in (proxies.get(key), item.get("reason")) if x)
        lines.append(f"| {key} | {_display(item.get('value'))} | {_display(scores.get(key), 0)} | {item.get('data_date') or '—'} | {note or '—'} |")
    lines += ["", "## 主線", "", "| 主線 | I1 | 覆蓋 | 加速 | 減速 | 缺漏 |", "|---|---:|---:|---|---|---|"]
    for key, item in sorted(snapshot["theme_breadth"].items()):
        lines.append(f"| {key} {item.get('name','')} | {_display(item.get('value'))} | {item.get('n_have',0)}/{item.get('n_total',0)} | "
                     f"{', '.join(item.get('accelerating',[])) or '—'} | {', '.join(item.get('decelerating',[])) or '—'} | {', '.join(item.get('missing',[])) or '—'} |")
    lines += ["", "## 公司總表", "", "| 代號 | 市場 | 價格 session | C1 | C2 | C3 | V1 | 財務體質 | 觸發項 | 價格拆解 | 缺漏 |",
              "|---|---|---|---:|---:|---:|---|---|---|---|---|"]
    for c in snapshot["companies"]:
        tr = c.get("trend") or {}; health_result = c.get("health") or {}
        triggers = _health_triggers(c); missing = [k for k, v in c.get("formal_readiness", {}).items() if v is False]
        if c.get("price_status", "OK") != "OK": missing.insert(0, "PRICE_NOT_AT_ANCHOR")
        lines.append(f"| {c['ticker']} | {c['market']} | {c['anchor_session'] or '—'} | {_display(tr.get('C1'))} | {_display(tr.get('C2'))} | {_display(tr.get('C3'))} | {_v1_cell(c)} | "
                     f"{health_result.get('status') or '資料不足'} | {', '.join(k for k,_ in triggers) or '—'} | {_attribution_summary(c)} | {', '.join(missing) or '—'} |")
    alerts = [(c, _health_triggers(c)) for c in snapshot["companies"] if _health_triggers(c)]
    lines += ["", "## 財務體質重點", ""]
    if not alerts:
        lines.append("目前沒有留意或警示項。")
    for company, triggers in alerts:
        lines += [f"### {company['ticker']} — {(company.get('health') or {}).get('status')}", ""]
        for key, item in triggers:
            lines.append(f"- **{key} {item.get('status')}**：{_display(item.get('value'))} {item.get('unit') or ''}；{item.get('reason') or '未提供原因'}")
        lines.append("")
    text = "\n".join(lines) + "\n"
    if any(x in text for x in FORBIDDEN_RESEARCH_TEXT):
        raise ValueError("RESEARCH_PAGE_FORBIDDEN_TEXT_GATE failed")
    if len(text) > 30000: raise ValueError("RESEARCH_PAGE_LENGTH_GATE failed")
    return text


def snapshot_status(snapshot):
    market_stale = any("過期" in str(v.get("reason", "")) for v in snapshot.get("market", {}).get("values", {}).values())
    shared_anchor_stale = any(not snapshot.get("anchors", {}).get(market) for market in ("TW", "US"))
    shared_calendar_stale = any(not snapshot.get("session_binding", {}).get(market) for market in ("TW", "US"))
    return "SOURCE_STALE" if market_stale or shared_anchor_stale or shared_calendar_stale else "OK"


def _compound_return(ticker, start_date, end_date, series, actions):
    window = [x for x in series.get(ticker, []) if start_date <= x[0] <= end_date]
    if len(window) < 2 or window[0][0] != start_date or window[-1][0] != end_date: return None
    value = 1.0
    for _, ret in pricedata.total_returns(window, actions.get(ticker, {})): value *= 1 + ret
    return value - 1


def compute_outcome_attempts(snapshot, published_at, sessions, series, actions, existing=()):
    """Pure immutable outcome derivation; never reads rules or classification logic."""
    published = parse_time(published_at); prior = list(existing); out = []
    for company in snapshot["companies"]:
        if company.get("price_status", "OK") != "OK":
            continue
        market = company["market"]
        ss = sorted([s for s in sessions if s["market"] == market and s["session_status"] == "OPEN"], key=lambda x: x["open_at"])
        start_i = next((i for i, s in enumerate(ss) if parse_time(s["open_at"]) > published), None)
        if start_i is None: continue
        for horizon in (20, 60):
            end_i = start_i + horizon
            market_tickers = {c["ticker"] for c in snapshot["companies"] if c["market"] == market}
            market_tickers.add(company["benchmark_ticker"])
            latest_available = max((d for t in market_tickers for d, *_ in series.get(t, [])), default=None)
            if end_i >= len(ss) or not latest_available or ss[end_i]["session_date"] > latest_available: continue
            start, end = ss[start_i], ss[end_i]; ticker = company["ticker"]; benchmark = company["benchmark_ticker"]
            sr = _compound_return(ticker, start["session_date"], end["session_date"], series, actions)
            br = _compound_return(benchmark, start["session_date"], end["session_date"], series, actions)
            status = "OK" if sr is not None and br is not None else "OUTCOME_UNAVAILABLE"
            key = f"{snapshot.get('run_id','')}|{company['security_id']}|{horizon}"
            basis = {"key": key, "status": status, "start": start["session_id"], "end": end["session_id"],
                     "security_return": sr, "benchmark_return": br,
                     "price_data_sha256": digest(canonical_bytes(series)), "action_data_sha256": digest(canonical_bytes(actions))}
            attempt = "OUTCOME_ATTEMPT_" + digest(canonical_bytes(basis))
            if any(r.get("outcome_attempt_id") == attempt for r in prior + out): continue
            active = [r for r in prior + out if r.get("outcome_key") == key and not any(x.get("supersedes_outcome_id") == r.get("outcome_id") for x in prior + out)]
            row = {"outcome_id": "OUTCOME_" + digest(attempt.encode()), "outcome_attempt_id": attempt,
                   "outcome_key": key, "signal_snapshot_sha256": snapshot.get("snapshot_sha256"),
                   "security_id": company["security_id"], "benchmark_security_id": company["benchmark_security_id"],
                   "market": market, "horizon_sessions": horizon, "start_session_id": start["session_id"],
                   "end_session_id": end["session_id"], "calendar_version_id": snapshot["calendar_version_id"],
                   "status": status, "security_total_return": sr, "benchmark_total_return": br,
                   "excess_return": None if sr is None or br is None else sr - br,
                   "statistical_eligibility": market != "TW",
                   "limitations": ["CLOSE_TO_CLOSE_NO_COSTS"] + (["TOTAL_RETURN_ACTION_COVERAGE_UNVERIFIED"] if market == "TW" else []),
                   "supersedes_outcome_id": active[-1]["outcome_id"] if active else None}
            out.append(row)
    return out


def collect_outcome_attempts(base, sessions, series, actions, existing=()):
    """Evaluate every committed frozen signal; this is what lets old signals mature."""
    accumulated = list(existing); added = []
    for run_dir in sorted(Path(base).glob("SNAP_*")):
        if not run_dir.is_dir() or not (run_dir / "COMMITTED.json").exists(): continue
        marker, _, _, snapshot = read_committed(run_dir)
        snapshot["snapshot_sha256"] = marker["snapshot_sha256"]
        rows = compute_outcome_attempts(snapshot, marker["published_at"], sessions, series, actions, accumulated)
        accumulated.extend(rows); added.extend(rows)
    return added


def dry_run(root=ROOT, now_fn=utc_now):
    lock_state = project_lock.read()
    first = capture_inputs(root); cutoff = parse_time(now_fn())
    snapshot = build_snapshot(first, cutoff); manifest = build_input_manifest(first, cutoff, snapshot)
    second = capture_inputs(root)
    if input_hashes(first) != input_hashes(second):
        raise RuntimeError("DRY_RUN_DOUBLE_READ_STABILITY_GATE failed")
    page = research_page(snapshot)
    return {"mode": "DRY_RUN", "write_count": 0, "lock_state": lock_state, "data_cutoff_at": cutoff.isoformat(),
            "input_manifest_sha256": digest(canonical_bytes(manifest)),
            "snapshot_sha256": digest(canonical_bytes(snapshot)), "research_page_sha256": digest(page.encode("utf-8")),
            "anchors": snapshot["anchors"], "company_count": len(snapshot["companies"]),
            "market_status": snapshot["market"]["assessment"].get("status"), "gates": snapshot["gates"]}


def _atomic_write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "xb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _recover_staging(base):
    """Quarantine incomplete prior transactions; never publish them."""
    base = Path(base); interrupted = base / "_中斷"
    moved = []
    candidates = [p for p in base.glob("*.staging") if p.is_dir()]
    candidates += [p for p in base.iterdir() if p.is_dir() and p.name.startswith("SNAP_") and not (p / "COMMITTED.json").exists()] if base.exists() else []
    for child in sorted(set(candidates)):
        if child.is_dir():
            interrupted.mkdir(parents=True, exist_ok=True)
            target = interrupted / child.name
            if target.exists(): target = interrupted / (child.name + "_" + digest(str(child).encode())[:8])
            os.replace(child, target); moved.append(target.name)
    return moved


def read_committed(run_dir):
    run_dir = Path(run_dir); marker = json.loads((run_dir / "COMMITTED.json").read_text(encoding="utf-8"))
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    if digest(manifest_bytes) != marker["manifest_sha256"]: raise ValueError("COMMITTED_MANIFEST_HASH_GATE failed")
    manifest = json.loads(manifest_bytes)
    for name, meta in manifest["payloads"].items():
        if digest((run_dir / name).read_bytes()) != meta["sha256"]: raise ValueError("COMMITTED_PAYLOAD_HASH_GATE failed")
    return marker, manifest, json.loads((run_dir / "input_manifest.json").read_bytes()), json.loads((run_dir / "snapshot.json").read_bytes())


# ---------------------------------------------------------------------------
# S1B 影子分類預覽（S2-5）：第二階段，只讀第一階段凍結輸入；結果不是結論。
# ---------------------------------------------------------------------------
SHADOW_SCHEMA_VERSION = "S1B_SHADOW_PREVIEW_0.2"
SHADOW_MODEL = "S1B_SHADOW_PREVIEW"
SHADOW_PAGE = "影子分類預覽.md"
SHADOW_MARKETS = ("TW", "US")
SHADOW_PAYLOADS = tuple(f"shadow_verdict_{m}.json" for m in SHADOW_MARKETS) + (SHADOW_PAGE,)
SHADOW_MARKER = "SHADOW_COMMITTED.json"
SHADOW_FAILURE = "shadow_failure.json"
SHADOW_DISCLAIMER = "非正式、影子、研究參考，非投資建議"
SHADOW_PORTFOLIO = {"portfolio_state": "NOT_PROVIDED", "positions": [], "authority": "USER_OWNED_READ_ONLY_TO_AGENTS"}
SHADOW_GAP_STATUS = ("資料不足", "待查證", "無法判定")
# 依凍結 rules 決定可用的計算欄位；未列入契約者維持缺漏。
SHADOW_CONTRACT_GAP = "待程式計算來源契約或人工查證補齊"
SHADOW_STAGE = {
    "V1": "S3：V1 試行（point-in-time 估值資料）",
    "EVENT": "S3：前瞻事件表與涵蓋紀錄",
    "CALENDAR_COVERAGE_MISSING": "D-11：證交所公布 2027 年開休市日後補入正式日曆",
    "C1": "官方申報資料補齊（S3）",
    "C1_SOURCE": "來源驗證失敗，需查",
    "C5": "S4：AI 判讀（併購／一次性項目）",
    "C6": "S4：AI 判讀（C6 判定表 v0.2）",
    "I2": "S4：AI 抽取財測數字＋程式判方向",
    "I3": "S4 前查證報價來源（D-08）",
    "I4": "S4：AI 抽取雲端資本支出預估＋程式判方向",
    "MATURITY_TIER": "rules maturity_tier_by_security 未列",
}


def frozen_schema2_inputs(captured, snapshot_root):
    """把第一階段 captured 位元組轉成 validate／judge 的注入輸入；不讀任何現場檔案。"""
    verify_observation_ledger(captured)
    versions = jsonl_rows(captured["calendar_versions"])
    if not versions:
        raise ValueError("SHADOW_CALENDAR_VERSION_GATE failed")
    version = versions[-1]
    count = int(version["row_count"])
    rows = csv_rows(captured["sessions"])[:count]
    superseded = {r.get("supersedes_session_id") for r in rows if r.get("supersedes_session_id")}
    # 與 data_spine.load_sessions 相同：保留未來 session（事件距離與日曆涵蓋需要），依市場與開盤時間排序。
    sessions = sorted([r for r in rows if r.get("session_id") not in superseded],
                      key=lambda r: (r.get("market", ""), r.get("open_at", "")))
    prefix = b"".join(io.BytesIO(captured["sessions"]).readlines()[:1 + count])

    def calendar_check(version_id, sha):
        hit = next((x for x in versions if x.get("calendar_version_id") == version_id), None)
        if not hit: return False, "CALENDAR_VERSION_NOT_REGISTERED"
        if hit.get("calendar_sha256") != sha: return False, "CALENDAR_HASH_NOT_BOUND_TO_VERSION"
        hit_prefix = b"".join(io.BytesIO(captured["sessions"]).readlines()[:1 + int(hit.get("row_count", 0))])
        if digest(hit_prefix) != sha: return False, "CALENDAR_PREFIX_INTEGRITY_FAILED"
        return True, None

    if digest(prefix) != version.get("calendar_sha256"):
        raise ValueError("SHADOW_CALENDAR_PREFIX_GATE failed")
    return {"rules": json.loads(captured["rules"]), "registry": json.loads(captured["registry"]),
            "config": json.loads(captured["data_spine"]), "sessions": sessions, "calendar_version": version,
            "calendar_check": calendar_check, "events": jsonl_rows(captured.get("events", b"")),
            "event_ledger": jsonl_rows(captured.get("event_ledger", b"")), "observations": jsonl_rows(captured["observations"]),
            "portfolio": dict(SHADOW_PORTFOLIO), "snapshot_root": Path(snapshot_root),
            "canonical_inputs": captured}


def bind_precommit_candidate(inputs, captured, snapshot, run_id):
    """Bind an in-memory snapshot to frozen inputs for fail-closed comparison.

    This does not create a formal snapshot or CAS payload.  computed_provenance
    independently rebuilds the candidate from these frozen bytes before a
    value may enter the comparison verdict.
    """
    if not re.fullmatch(r"SNAP_PRECOMMIT_[A-Za-z0-9_-]+", str(run_id or "")):
        raise ValueError("PRECOMMIT_RUN_ID_GATE failed")
    snapshot_sha = digest(canonical_bytes(snapshot))
    cutoff = parse_time(snapshot.get("data_cutoff_at"))
    manifest = build_input_manifest(captured, cutoff, snapshot)
    bound = dict(inputs)
    canonical = dict(inputs.get("canonical_inputs") or {})
    rules_bytes = captured.get("rules")
    if not isinstance(rules_bytes, bytes):
        raise ValueError("PRECOMMIT_RULES_BYTES_GATE failed")
    rules_version = json.loads(rules_bytes).get("protocol_version")
    canonical["__precommit_candidate__"] = {
        "run_id": run_id, "snapshot_sha256": snapshot_sha,
        "snapshot": snapshot, "captured": captured, "input_manifest": manifest,
        "rules_version": rules_version, "rules_sha256": digest(rules_bytes),
    }
    bound["canonical_inputs"] = canonical
    return bound, snapshot_sha


def precommit_comparison_gate(results):
    """Reject the old false-negative shape instead of accepting a fake diff."""
    for market, section in sorted((results or {}).items()):
        rows = ((section or {}).get("judge") or {}).get("companies") or []
        if not rows:
            continue
        verdicts = {row.get("verdict") for row in rows}
        validation = ((section or {}).get("validation") or {}).get("errors") or []
        provenance_errors = [e for e in validation if "DERIVED_SOURCE_MISMATCH" in str(e)]
        affected = set()
        for error in provenance_errors:
            match = re.search(r"companies\[\d+\]\(([^)]+)\)", str(error))
            if match:
                affected.add(match.group(1))
        tickers = {row.get("ticker") for row in rows}
        if len(verdicts) == 1 and tickers and affected == tickers:
            raise ValueError(f"PROVENANCE_UNVERIFIABLE_PRECOMMIT:{market}")
    return True


def _null_field():
    return {"value": None, "status": "未查", "type": None, "source": None, "as_of": None}


def _active_keys(layer):
    from common import is_disabled
    return [k for k, spec in layer["indicators"].items() if not is_disabled(spec)]


def _theme_of(ticker, rules):
    from common import canonical
    hits = [tid for tid, th in sorted(rules.get("themes", {}).items())
            if ticker in [canonical(x, rules) for x in th.get("watchlist", [])]]
    return hits[0] if len(hits) == 1 else None


def build_shadow_run(market, snapshot, snapshot_run_id, snapshot_sha256, inputs):
    """組一份單一市場 schema 2 影子 run；依凍結規則決定可放行的計算欄位。"""
    from data_spine import _visible_event_versions, parse_time as spine_time
    rules, registry = inputs["rules"], inputs["registry"]
    rules_bytes = (inputs.get("canonical_inputs") or {}).get("rules")
    rules_sha256 = digest(rules_bytes) if isinstance(rules_bytes, bytes) else None
    def provenance(path):
        return {"adapter": "S1A_ADAPTER_V1", "snapshot_run_id": snapshot_run_id,
                "snapshot_sha256": snapshot_sha256, "field_path": path,
                "rules_version": rules.get("protocol_version"), "rules_sha256": rules_sha256}
    issuers = {i["issuer_id"]: i.get("issuer_name") for i in registry.get("issuers", [])}
    verdict_rules = rules.get("verdict") or {}
    expanded = verdict_rules.get("computed_fields_accepted")
    company_contract = (expanded or {}).get("company") if isinstance(expanded, dict) else verdict_rules.get("computed_revenue_accepted")
    market_contract = (expanded or {}).get("market") if isinstance(expanded, dict) else None
    theme_contract = (expanded or {}).get("theme") if isinstance(expanded, dict) else None
    valuation_contract = (expanded or {}).get("valuation") if isinstance(expanded, dict) else None
    company_contract = company_contract or {}
    ai_contract = verdict_rules.get("ai_fields_accepted") or {}
    cutoff = snapshot["data_cutoff_at"]; cutoff_time = spine_time(cutoff, "data_cutoff_at")
    benchmark = rules["benchmarks"]["default_tw" if market == "TW" else "default_us"]
    tiers = rules.get("maturity_tier_by_security") or {}
    companies, theme_ids = [], set()
    for co in snapshot["companies"]:
        if co["market"] != market: continue
        trend_result = co.get("trend") or {}
        fields = {k: _null_field() for k in _active_keys(rules["company"])}
        for key in company_contract.get("fields", []):
            if key in fields and trend_result.get(key) is not None:
                fields[key] = {"value": trend_result[key], "status": company_contract["status_code"], "type": "實績",
                               "source": f"S1A 快照 {snapshot_run_id}（程式計算，官方申報）",
                               "as_of": trend_result.get("latest_period_end"),
                               "provenance": provenance(f"companies/{co['security_id']}/trend/{key}")}
        activated_company = ((snapshot.get("activated_fields") or {}).get("companies") or {}).get(co["issuer_id"], {})
        for key in ai_contract.get("company", {}).get("fields", []):
            item = activated_company.get(key)
            if key in fields and item and item.get("value") is not None:
                fields[key] = json.loads(json.dumps(item))
        theme = _theme_of(co["ticker"], rules)
        if theme: theme_ids.add(theme)
        try:
            visible = _visible_event_versions(inputs["events"], inputs["event_ledger"], co["issuer_id"], cutoff_time)
        except ValueError:
            # data_spine.derive_event_distance will surface EVENT_TIMESTAMP_INVALID
            # as the canonical UNKNOWN reason; do not silently trust the bad row.
            visible = []
        valuation = {k: _null_field() for k in _active_keys(rules["valuation"])}
        v1 = co.get("valuation_v1") or {}
        if (valuation_contract and "V1" in valuation_contract.get("fields", []) and
                v1.get("status") == "OK" and v1.get("value") is not None):
            v1_current = v1.get("current") or ((v1.get("primary") or {}).get("current")) or {}
            valuation["V1"] = {"value": v1["value"], "status": valuation_contract["status_code"], "type": "實績",
                               "source": f"S1A 快照 {snapshot_run_id}（官方申報＋公開價格程式計算）",
                               "as_of": v1_current.get("anchor_date"),
                               "calculation_method": v1.get("method"),
                               "score_cap": v1.get("score_cap"),
                               "provenance": provenance(f"companies/{co['security_id']}/valuation/V1")}
        companies.append({"ticker": co["ticker"], "name": issuers.get(co["issuer_id"]), "theme": theme,
                          "maturity_tier": tiers.get(co["security_id"]), "benchmark": benchmark,
                          "event_ids": sorted(e["event_id"] for e in visible), "fields": fields,
                          "valuation": valuation})
    binding = (snapshot.get("session_binding") or {}).get(market) or {}
    version = inputs["calendar_version"]
    market_fields = {k: _null_field() for k in _active_keys(rules["market"])}
    for key in (market_contract or {}).get("fields", []):
        item = ((snapshot.get("market") or {}).get("values") or {}).get(key) or {}
        if key in market_fields and item.get("value") is not None:
            market_fields[key] = {"value": item["value"], "status": market_contract["status_code"], "type": "實績",
                                  "source": f"S1A 快照 {snapshot_run_id}（程式計算，公開資料）",
                                  "as_of": item.get("data_date"),
                                  "note": "proxy" if key in {"M1", "M6"} else "",
                                  "provenance": provenance(f"market/values/{key}")}
    theme_fields = {tid: {k: _null_field() for k in _active_keys(rules["theme"])} for tid in sorted(theme_ids)}
    for tid, fields in theme_fields.items():
        item = (snapshot.get("theme_breadth") or {}).get(tid) or {}
        if theme_contract and "I1" in theme_contract.get("fields", []) and item.get("value") is not None:
            fields["I1"] = {"value": item["value"], "status": theme_contract["status_code"], "type": "實績",
                            "source": f"S1A 快照 {snapshot_run_id}（程式計算，官方申報）", "as_of": cutoff[:10],
                            "provenance": provenance(f"themes/{tid}/I1")}
        activated_theme = ((snapshot.get("activated_fields") or {}).get("themes") or {}).get(tid, {})
        for key in ai_contract.get("theme", {}).get("fields", []):
            activated = activated_theme.get(key)
            if key in fields and activated and activated.get("value") is not None:
                fields[key] = json.loads(json.dumps(activated))
    return {"protocol_version": rules["protocol_version"], "run_schema_version": 2, "mode": "SHADOW_PREVIEW",
            "not_a_conclusion": True, "model": SHADOW_MODEL, "run_date": cutoff[:10], "shadow_market": market,
            "run_batch_id": "S1B_" + snapshot_run_id, "analysis_session_id": binding.get("session_id"),
            "run_cutoff_at": cutoff, "calendar_version_id": version.get("calendar_version_id"),
            "calendar_sha256": version.get("calendar_sha256"),
            "market": market_fields, "themes": theme_fields,
            "companies": companies}


def _h_hypothesis(base_verdict, health_result):
    """D-05：只有基準「可列入研究」且 H 任一警示時改為等待；H 缺值不視為通過。"""
    indicators = (health_result or {}).get("indicators") or {}
    warns = sorted(k for k, v in indicators.items() if v.get("status") == "警示")
    missing = sorted(k for k, v in indicators.items() if v.get("status") == "資料不足")
    gate = "WARN" if warns else ("MISSING" if not indicators or missing else "PASS")
    hypothesis = "等待" if base_verdict == "可列入研究" and gate == "WARN" else base_verdict
    return {"base_verdict": base_verdict, "h_gate": gate, "h_warn": warns, "h_missing": missing,
            "hypothesis_verdict": hypothesis, "changed": hypothesis != base_verdict, "is_conclusion": False}


def _company_gaps(run_company, judged, rules, theme_result, market_result, trend_status=None):
    gaps = []
    errors = judged.get("validation_errors", [])
    for e in errors:
        if "EVENT_DERIVATION_FAILED:" in e:
            reason = e.split("EVENT_DERIVATION_FAILED:", 1)[1]
            gaps.append(("事件距離：" + reason, SHADOW_STAGE["CALENDAR_COVERAGE_MISSING" if reason == "CALENDAR_COVERAGE_MISSING" else "EVENT"]))
        elif "V1_DERIVATION_FAILED:" in e:
            gaps.append(("V1：" + e.split("V1_DERIVATION_FAILED:", 1)[1], SHADOW_STAGE["V1"]))
        elif "DERIVED_SOURCE_MISMATCH" in e:
            gaps.append(("計算欄位來源驗證：" + e.split("DERIVED_SOURCE_MISMATCH", 1)[1].lstrip(":"), SHADOW_STAGE["C1_SOURCE"]))
    if run_company.get("maturity_tier") not in (1, 2, 3):
        gaps.append(("maturity_tier", SHADOW_STAGE["MATURITY_TIER"]))
    if not run_company.get("theme"):
        gaps.append(("主線歸屬", "rules themes 名單"))
    for key, field in (run_company.get("fields") or {}).items():
        if field.get("value") is None:
            label = f"C1（趨勢模組：{trend_status or '無結果'}）" if key == "C1" else key
            gaps.append((label, SHADOW_STAGE.get(key, SHADOW_CONTRACT_GAP)))
    for key in (theme_result or {}).get("missing", []):
        gaps.append(("主線 " + key, SHADOW_STAGE.get(key, SHADOW_CONTRACT_GAP)))
    if market_result.get("missing"):
        gaps.append(("市場 " + "、".join(market_result["missing"]), SHADOW_CONTRACT_GAP))
    seen, out = set(), []
    for item in gaps:
        if item not in seen: seen.add(item); out.append({"missing": item[0], "filled_by": item[1]})
    return out


def shadow_market_result(market, snapshot, snapshot_run_id, snapshot_sha256, inputs):
    """validate → judge；驗證錯誤照實保存。回傳可序列化的結果（不含時間戳，同輸入位元組相同）。"""
    from validate import validate
    from verdict import judge
    rules = inputs["rules"]
    run = build_shadow_run(market, snapshot, snapshot_run_id, snapshot_sha256, inputs)
    errors, warnings = validate(run, rules, inputs)
    result = judge(run, rules, inputs)
    min1_rules = json.loads(json.dumps(rules))
    min1_rules["verdict"]["company_score_min"] = 1
    min1_result = judge(run, min1_rules, inputs)
    v1_relaxed_rules = json.loads(json.dumps(rules))
    v1_relaxed_rules["verdict"]["valuation_score_min"] = -1
    v1_relaxed_result = judge(run, v1_relaxed_rules, inputs)
    statutory_by_ticker, statutory_result, statutory_run = {}, None, None
    if market == "TW":
        # G-S3b-TW remains shadow-only: calculate statutory windows beside the
        # canonical event ledger without changing the canonical verdict.
        statutory_run = copy.deepcopy(run)
        statutory_run["run_schema_version"] = 1
        statutory_run["info_cutoff"] = statutory_run["run_cutoff_at"]
        for company in statutory_run["companies"]:
            company.pop("event_ids", None)
            item = data_spine.derive_tw_statutory_event_window(
                company["ticker"], statutory_run["analysis_session_id"], inputs["observations"],
                inputs["sessions"], inputs["registry"])
            statutory_by_ticker[company["ticker"]] = item
            company["event_within_days"] = item.get("distance") if item.get("status") == "KNOWN_UPCOMING" else None
        statutory_result = judge(statutory_run, rules, inputs)
    health_by_ticker = {c["ticker"]: c.get("health") for c in snapshot["companies"]}
    trend_status = {c["ticker"]: (c.get("trend") or {}).get("status") for c in snapshot["companies"]}
    snapshot_company = {c["ticker"]: c for c in snapshot["companies"]}
    rows = []
    for index, (run_company, judged) in enumerate(zip(run["companies"], result["companies"])):
        zone = "GAP" if judged["verdict"].startswith(SHADOW_GAP_STATUS) else "ELIGIBLE"
        source_v1 = (snapshot_company.get(run_company["ticker"], {}).get("valuation_v1") or {})
        v1_score = (judged.get("valuation_detail") or {}).get("V1")
        months = source_v1.get("months") or []
        invalid_reasons = {m.get("reason") for m in months if m.get("status") == "INVALID_MONTH"}
        complete_but_structural = (len(months) >= 60 and int(source_v1.get("valid_months") or 0) < 48 and
                                   invalid_reasons and invalid_reasons <= {"TTM_EPS_NON_POSITIVE", "BVPS_NON_POSITIVE"})
        structurally_no_baseline = (str(source_v1.get("status", "")).startswith("NOT_APPLICABLE_") or
                                    complete_but_structural)
        if v1_score == -1 and v1_relaxed_result["companies"][index]["verdict"] == "可列入研究":
            v1_advisory = "可列入研究（提醒：歷史估值偏高）"
        elif structurally_no_baseline:
            v1_advisory = "無 5 年基準"
        else:
            v1_advisory = judged["verdict"]
        statutory = statutory_by_ticker.get(run_company["ticker"])
        statutory_judged = statutory_result and statutory_result["companies"][index]
        statutory_gaps = None
        if statutory_judged:
            statutory_gaps = _company_gaps(
                statutory_run["companies"][index], statutory_judged, rules,
                statutory_result["themes"].get(run_company["theme"]), statutory_result["market"],
                trend_status.get(run_company["ticker"]))
        rows.append({"ticker": run_company["ticker"], "theme": run_company["theme"], "zone": zone,
                     "shadow_verdict": judged["verdict"], "why": judged["why"],
                     "V1_ADVISORY": v1_advisory,
                     "company_score_min_1": min1_result["companies"][index]["verdict"],
                     "gaps": _company_gaps(run_company, judged, rules, result["themes"].get(run_company["theme"]), result["market"],
                                          trend_status.get(run_company["ticker"])),
                     "tw_statutory_event": statutory,
                     "tw_statutory_shadow_verdict": statutory_judged and statutory_judged["verdict"],
                     "tw_statutory_shadow_why": statutory_judged and statutory_judged["why"],
                     "tw_statutory_remaining_gaps": statutory_gaps,
                     "h_hypothesis": _h_hypothesis(judged["verdict"], health_by_ticker.get(run_company["ticker"]))})
    return {"shadow_schema_version": SHADOW_SCHEMA_VERSION, "mode": "SHADOW_PREVIEW", "not_a_conclusion": True,
            "disclaimer": SHADOW_DISCLAIMER, "market": market, "snapshot_run_id": snapshot_run_id,
            "snapshot_sha256": snapshot_sha256, "run_batch_id": run["run_batch_id"],
            "portfolio_input": "NOT_READ_SHADOW_CONSTANT_NOT_PROVIDED",
            "shadow_run": run, "validation": {"errors": errors, "warnings": warnings},
            "judge": result, "rows": rows}


def _layer_cell(total, detail):
    detail = detail or {}
    filled = sum(1 for v in detail.values() if v is not None)
    return f"{_fmt_score(total) if filled else '—'}（{filled}/{len(detail)}）"


def _fmt_score(value):
    return "—" if value is None else (f"{value:+d}" if isinstance(value, int) else _display(value))


def shadow_preview_page(results, snapshot, snapshot_run_id, snapshot_sha256):
    expanded = any(f.get("value") is not None for f in results["US"]["shadow_run"]["market"].values())
    v1_enabled = any((c.get("valuation") or {}).get("V1", {}).get("value") is not None
                     for c in results["US"]["shadow_run"]["companies"])
    contract_note = ("程式計算契約已放行 C1–C3、M1–M7、I1"
                     + (" 與 V1" if v1_enabled else "") + "；M1、M6 為 proxy。"
                     if expanded else
                     "程式計算狀態目前只放行 C1；C2、C3、M1–M7、I1 在影子 run 中為缺漏，程式值另列於「參考」節，未計入分類。")
    if results["US"]["shadow_run"].get("protocol_version") == "1.11.0":
        contract_note += " rules 1.11.0 另正式放行 C5、C6、I2 與 SEC 實際資本支出 I4；I4 加速度只並列、不計分。"
    lines = ["# S1B 影子分類預覽", "", f"**{SHADOW_DISCLAIMER}。**", "",
             "本頁由程式依規則自動產生，是影子預覽，不是正式結論；不含交易指令、價格目標或資金配置安排。"
             "財務體質（H）假設結果另列，不改變分類。", "",
             f"- 快照：`{snapshot_run_id}`（snapshot SHA-256 `{snapshot_sha256}`）",
             f"- 批次：`{results['US']['run_batch_id']}`；資訊截止：`{snapshot['data_cutoff_at']}`",
             f"- 規則版本：`{results['US']['shadow_run']['protocol_version']}`；分析 session："
             + "、".join(f"{m} `{results[m]['shadow_run']['analysis_session_id']}`" for m in SHADOW_MARKETS),
             "- " + contract_note, ""]
    eligible = [(m, r) for m in SHADOW_MARKETS for r in results[m]["rows"] if r["zone"] == "ELIGIBLE"]
    gaps = [(m, r) for m in SHADOW_MARKETS for r in results[m]["rows"] if r["zone"] == "GAP"]
    lines += ["## 有評分資格", ""]
    if not eligible:
        lines += ["目前沒有公司具評分資格（全部列在下方工作清單）。", ""]
    else:
        lines += ["| 市場 | 代號 | 主線 | 正式門檻影子分類 | V1_ADVISORY | company_score_min=1 | 公司分 | 主線分 | 估值分 | 市場燈號 |", "|---|---|---|---|---|---|---:|---:|---:|---|"]
        for m, r in eligible:
            judged = next(c for c in results[m]["judge"]["companies"] if c["ticker"] == r["ticker"])
            theme = results[m]["judge"]["themes"].get(r["theme"]) or {}
            lines.append(f"| {m} | {r['ticker']} | {r['theme']} | {r['shadow_verdict']} | {r['V1_ADVISORY']} | {r['company_score_min_1']} | {_fmt_score(judged['company_score'])} | "
                         f"{_fmt_score(theme.get('score'))} | {_fmt_score(judged['valuation_score'])} | {results[m]['judge']['market']['light']} |")
        lines.append("")
    lines += ["## 資料不足／待補（工作清單，不代表最差）", ""]
    common = []
    if gaps:
        keyed = [{(g["missing"], g["filled_by"]) for g in r["gaps"]} for _, r in gaps]
        shared = set.intersection(*keyed)
        common = [(g["missing"], g["filled_by"]) for g in gaps[0][1]["gaps"] if (g["missing"], g["filled_by"]) in shared]
    if common:
        lines += [f"以下缺口 {len(gaps)} 家全部相同，只列一次：", "", "| 缺什麼 | 由哪項補 |", "|---|---|"]
        lines += [f"| {item} | {stage} |" for item, stage in common]
        lines.append("")
    if gaps:
        lines += ["各家另外的缺口：", "", "| 市場 | 代號 | 正式門檻影子分類 | V1_ADVISORY | company_score_min=1 | 另外缺什麼 | 由哪項補 |", "|---|---|---|---|---|---|---|"]
        for m, r in gaps:
            extra = [g for g in r["gaps"] if (g["missing"], g["filled_by"]) not in set(common)]
            missing = "<br>".join(g["missing"] for g in extra) or "—"
            stages = "<br>".join(g["filled_by"] for g in extra) or "—"
            lines.append(f"| {m} | {r['ticker']} | {r['shadow_verdict'].split('：')[0]} | {r['V1_ADVISORY']} | {r['company_score_min_1']} | {missing} | {stages} |")
    else:
        lines.append("目前沒有資料不足或待補的公司。")
    lines += ["", "## 各層分數向量（影子 run 實際計分）", "",
              "分數後括號為「已填／應計項數」；已填項數未達規則最少項數時，該層分數不足以判斷。", "",
              "| 市場 | 代號 | 公司層 | 公司層缺漏 | 估值層 | 主線 | 主線層 | 市場層 |", "|---|---|---|---|---|---|---|---|"]
    for m in SHADOW_MARKETS:
        jr = results[m]["judge"]
        market_layer = f"{jr['market']['light']}（{jr['market']['filled']}/{len(jr['market']['detail'])}）"
        for c in jr["companies"]:
            theme = jr["themes"].get(c["theme"]) or {}
            missing = [k for k, v in c["company_detail"].items() if v is None]
            lines.append(f"| {m} | {c['ticker']} | {_layer_cell(c['company_score'], c['company_detail'])} | {', '.join(missing) or '—'} | "
                         f"{_layer_cell(c['valuation_score'], c['valuation_detail'])} | {c['theme'] or '—'} | "
                         f"{_layer_cell(theme.get('score'), theme.get('detail'))} | {market_layer} |")
    assessment = snapshot["market"]["assessment"]
    lines += ["", "## 參考：程式值（未計入分類）", "",
              f"市場環境（影子模組）：燈號 `{assessment.get('status')}`、分數 `{assessment.get('score')}`、覆蓋 `{assessment.get('filled')}/7`。", "",
              "| 主線 | I1 | 分數 |", "|---|---:|---:|"]
    for key, item in sorted(snapshot["theme_breadth"].items()):
        lines.append(f"| {key} | {_display(item.get('value'))} | {_fmt_score(item.get('score'))} |")
    lines += ["", "| 代號 | C1 | C2 | C3 |", "|---|---:|---:|---:|"]
    for co in snapshot["companies"]:
        tr = co.get("trend") or {}
        lines.append(f"| {co['ticker']} | {_display(tr.get('C1'))} | {_display(tr.get('C2'))} | {_display(tr.get('C3'))} |")
    lines += ["", "## 財務體質 H 假設（D-05，不作結論）", "",
              "規則：只有影子分類為「可列入研究」且 H 任一「警示」時，假設分類改為「等待」；H 缺值不視為通過。", "",
              "| 市場 | 代號 | 影子分類 | H 狀態 | 警示項 | 缺值項 | 假設分類 |", "|---|---|---|---|---|---|---|"]
    for m in SHADOW_MARKETS:
        for r in results[m]["rows"]:
            h = r["h_hypothesis"]
            lines.append(f"| {m} | {r['ticker']} | {r['shadow_verdict'].split('：')[0]} | {h['h_gate']} | {', '.join(h['h_warn']) or '—'} | "
                         f"{', '.join(h['h_missing']) or '—'} | {h['hypothesis_verdict'].split('：')[0]}{'（改變）' if h['changed'] else ''} |")
    lines += ["", "## rules 1.11.0 AI 閱讀欄位與 SEC I4", "",
              "I2 標註只說明證據獨立性，不會把值改成缺漏，也不會改變分數。I4 加速度為影子並列，不計分。", "",
              "| 市場 | 主線 | I2 | I2 標註 | 複查依據 | I4 | I4 來源 | I4 加速度（影子） |",
              "|---|---|---:|---|---|---|---|---|"]
    for m in SHADOW_MARKETS:
        for theme_id, fields in sorted(results[m]["shadow_run"]["themes"].items()):
            i2, i4 = fields.get("I2") or {}, fields.get("I4") or {}
            partial = "INDEPENDENCE_PARTIAL" if i2.get("independence_partial") else "—"
            basis = "; ".join(i2.get("review_basis") or []) or "—"
            accel = i4.get("acceleration_shadow") or {}
            accel_text = "; ".join(f"{k}={v * 100:.1f}pp" for k, v in sorted(accel.items()) if v is not None) or "—"
            lines.append(f"| {m} | {theme_id} | {_display(i2.get('value'))} | {partial} | {basis} | "
                         f"{_display(i4.get('value'))} | {i4.get('source') or '—'} | {accel_text} |")
    lines += ["", "| 市場 | 代號 | C5 | C6 |", "|---|---|---|---|"]
    for m in SHADOW_MARKETS:
        for company in results[m]["shadow_run"]["companies"]:
            fields = company.get("fields") or {}
            lines.append(f"| {m} | {company['ticker']} | {_display((fields.get('C5') or {}).get('value'))} | "
                         f"{_display((fields.get('C6') or {}).get('value'))} |")
    tw_rows = results.get("TW", {}).get("rows") or []
    if tw_rows:
        changed = [r["ticker"] for r in tw_rows
                   if str(r.get("shadow_verdict", "")).startswith("資料不足")
                   and not str(r.get("tw_statutory_shadow_verdict", "")).startswith("資料不足")]
        lines += ["", "## 台股事件＝法定期限窗（影子並列）", "",
                  ("此欄只替換事件距離；正式分類與 canonical 事件距離完全不變。"
                   "法定窗涵蓋不需每 7 天重查；若已有非保守法定時點的實際申報時間，窗口於其後下一個交易日結束。"), "",
                  f"因本欄脫離「資料不足」：{', '.join(changed) if changed else '無'}。", "",
                  "| 代號 | 期間 | 事件窗 | 距離 | 狀態 | 法定窗影子分類 | 仍缺什麼 |",
                  "|---|---|---|---:|---|---|---|"]
        for row in tw_rows:
            event = row.get("tw_statutory_event") or {}
            period = f"{event.get('fiscal_year', '—')} {event.get('fiscal_period', '—')}"
            window = f"{event.get('window_start', '—')}～{event.get('statutory_deadline', '—')}"
            distance = event.get("distance")
            gaps = "<br>".join(x["missing"] for x in (row.get("tw_statutory_remaining_gaps") or [])) or "—"
            lines.append(f"| {row['ticker']} | {period} | {window} | {distance if distance is not None else '—'} | "
                         f"{event.get('classification') or event.get('reason') or '—'} | "
                         f"{row.get('tw_statutory_shadow_verdict') or '—'} | {gaps} |")
    lines += ["", "## 驗證錯誤（照實保存）", ""]
    for m in SHADOW_MARKETS:
        errors = results[m]["validation"]["errors"]
        lines.append(f"- {m}：{len(errors)} 個錯誤、{len(results[m]['validation']['warnings'])} 個提醒；完整內容見 `shadow_verdict_{m}.json`。")
    lines += ["", f"**{SHADOW_DISCLAIMER}。**", ""]
    return "\n".join(lines)


def build_shadow_preview(run_dir, base, captured=None):
    """由已提交（COMMITTED）的第一階段快照產生第二階段內容；純計算、不寫檔。
    captured 為 None 時從 CAS 還原第一階段輸入；同一快照重跑位元組相同。"""
    run_dir = Path(run_dir)
    marker, _, input_manifest, snapshot = read_committed(run_dir)
    run_id, snapshot_sha = marker["run_id"], marker["snapshot_sha256"]
    if digest((run_dir / "snapshot.json").read_bytes()) != snapshot_sha:
        raise ValueError("SHADOW_SNAPSHOT_HASH_GATE failed")
    frozen = restore_inputs(input_manifest, Path(base) / "_cas", captured)
    inputs = frozen_schema2_inputs(frozen, base)
    results = {m: shadow_market_result(m, snapshot, run_id, snapshot_sha, inputs) for m in SHADOW_MARKETS}
    payloads = {f"shadow_verdict_{m}.json": canonical_bytes(results[m]) for m in SHADOW_MARKETS}
    payloads[SHADOW_PAGE] = shadow_preview_page(results, snapshot, run_id, snapshot_sha).encode("utf-8")
    return payloads


def _assert_shadow_target(path, run_dir):
    """影子檔只能寫在該快照目錄內，絕不寫 02_執行紀錄 正式 run 路徑。"""
    path, run_dir = Path(path).resolve(), Path(run_dir).resolve()
    if path.parent != run_dir or not run_dir.name.startswith("SNAP_") or run_dir.parent.name != "影子快照":
        raise RuntimeError("SHADOW_TARGET_GATE failed: " + str(path))


def commit_shadow_preview(run_dir, base, captured=None, clock=utc_now):
    """第二階段提交：寫三個檔，最後寫 SHADOW_COMMITTED.json 綁定雜湊。失敗寫 shadow_failure.json，不影響第一階段。"""
    run_dir = Path(run_dir)
    try:
        payloads = build_shadow_preview(run_dir, base, captured)
        for name, data in payloads.items():
            _assert_shadow_target(run_dir / name, run_dir); _atomic_write(run_dir / name, data)
        for name, data in payloads.items():
            if digest((run_dir / name).read_bytes()) != digest(data): raise RuntimeError("SHADOW_STAGING_HASH_GATE failed")
        marker, _, _, _ = read_committed(run_dir)
        shadow_marker = {"run_id": marker["run_id"], "snapshot_sha256": marker["snapshot_sha256"],
                         "shadow_schema_version": SHADOW_SCHEMA_VERSION, "committed_at": clock(),
                         "not_a_conclusion": True,
                         "payloads": {name: {"sha256": digest(data), "byte_length": len(data)} for name, data in payloads.items()}}
        _assert_shadow_target(run_dir / SHADOW_MARKER, run_dir)
        _atomic_write(run_dir / SHADOW_MARKER, canonical_bytes(shadow_marker))
        read_shadow_committed(run_dir)
        return {"status": "SHADOW_COMMITTED", **{k: v for k, v in shadow_marker.items() if k != "payloads"}}
    except Exception as exc:
        try:
            failure = {"stage": "SHADOW_PHASE2", "error": type(exc).__name__, "detail": str(exc),
                       "failed_at": clock(), "phase1_unaffected": True}
            _assert_shadow_target(run_dir / SHADOW_FAILURE, run_dir)
            _atomic_write(run_dir / SHADOW_FAILURE, canonical_bytes(failure))
        except Exception:
            pass
        return {"status": "SHADOW_FAILED", "error": type(exc).__name__, "detail": str(exc)}


def read_shadow_committed(run_dir):
    run_dir = Path(run_dir)
    marker = json.loads((run_dir / SHADOW_MARKER).read_bytes())
    committed, _, _, _ = read_committed(run_dir)
    if marker.get("snapshot_sha256") != committed["snapshot_sha256"] or marker.get("run_id") != committed["run_id"]:
        raise ValueError("SHADOW_SNAPSHOT_BINDING_GATE failed")
    if set(marker.get("payloads", {})) != set(SHADOW_PAYLOADS):
        raise ValueError("SHADOW_PAYLOAD_SET_GATE failed")
    for name, meta in marker["payloads"].items():
        if digest((run_dir / name).read_bytes()) != meta["sha256"]:
            raise ValueError("SHADOW_PAYLOAD_HASH_GATE failed")
    return marker


def _previous_committed(base):
    dirs = sorted([p for p in Path(base).glob("SNAP_*") if p.is_dir() and (p / "COMMITTED.json").exists()])
    return read_committed(dirs[-1]) if dirs else (None, None, None, None)


def _append_jsonl(path, rows, lock_holder=None):
    if not rows: return 0
    project_lock.require_holder(lock_holder, "snapshot")
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        for row in rows: f.write(canonical_bytes(row))
        f.flush(); os.fsync(f.fileno())
    return len(rows)


def formal_run(root=ROOT, now_fn=utc_now):
    """Create one transactional snapshot. Caller must possess separate authority."""
    ok, holder = project_lock.acquire("research_snapshot", "S1A formal snapshot", scopes="snapshot")
    if not ok:
        raise RuntimeError("PROJECT_LOCK_GATE failed: " + json.dumps(holder, ensure_ascii=False))
    base = Path(root) / "02_執行紀錄" / "影子快照"
    stage = None
    try:
        recovered = _recover_staging(base)
        cutoff = parse_time(now_fn())
        captured = capture_inputs(root)
        snapshot = build_snapshot(captured, cutoff)
        input_manifest = build_input_manifest(captured, cutoff, snapshot)
        input_bytes = canonical_bytes(input_manifest); run_id = make_run_id(cutoff, input_bytes)
        snapshot["run_id"] = run_id
        prior_marker, prior_final, prior_input, prior_snapshot = _previous_committed(base)
        difference = classify_difference(prior_input or {}, input_manifest, prior_snapshot, snapshot, frozen_observations(captured))
        snapshot["difference_reason"] = difference
        snapshot_bytes = canonical_bytes(snapshot)
        status = snapshot_status(snapshot)
        page_bytes = research_page(snapshot, run_id=run_id, status=status, difference=difference).encode("utf-8")
        final = base / run_id
        if final.exists(): raise RuntimeError("RUN_ID_COLLISION_GATE failed")
        stage = base / (run_id + ".staging"); stage.mkdir(parents=True, exist_ok=False)
        cas = base / "_cas"
        for data in captured.values():
            target = cas / digest(data)
            if not target.exists(): _atomic_write(target, data)
            if digest(target.read_bytes()) != target.name: raise RuntimeError("CAS_HASH_GATE failed")
        payloads = {"input_manifest.json": input_bytes, "snapshot.json": snapshot_bytes, "research.md": page_bytes}
        for name, data in payloads.items(): _atomic_write(stage / name, data)
        manifest = {"run_id": run_id, "input_manifest_sha256": digest(input_bytes), "status": status, "payloads":
                    {name: {"sha256": digest(data), "byte_length": len(data)} for name, data in payloads.items()},
                    "recovered_interrupted": recovered, "errors": []}
        manifest_bytes = canonical_bytes(manifest); _atomic_write(stage / "manifest.json", manifest_bytes)
        for name, meta in manifest["payloads"].items():
            if digest((stage / name).read_bytes()) != meta["sha256"]: raise RuntimeError("STAGING_HASH_GATE failed")
        final.parent.mkdir(parents=True, exist_ok=True); os.replace(stage, final); stage = None
        committed_at = now_fn()
        marker = {"run_id": run_id, "committed_at": committed_at, "published_at": committed_at,
                  "manifest_sha256": digest(manifest_bytes), "snapshot_sha256": digest(snapshot_bytes),
                  "research_page_sha256": digest(page_bytes)}
        _atomic_write(final / "COMMITTED.json", canonical_bytes(marker))
        read_committed(final)
        sessions, _ = bound_sessions(captured, parse_time(cutoff.isoformat()))
        price_rows, anchors, _ = frozen_prices(captured, json.loads(captured["registry"]), sessions, cutoff)
        series, _ = price_structures(price_rows); action_rows = frozen_actions(captured, json.loads(captured["registry"]), anchors, cutoff)
        actions = action_structures(action_rows)
        outcome_path = Path(root) / "04_回測/影子快照_outcomes.jsonl"
        existing = jsonl_rows(outcome_path.read_bytes()) if outcome_path.exists() else []
        attempts = collect_outcome_attempts(base, sessions, series, actions, existing)
        _append_jsonl(outcome_path, attempts, lock_holder="research_snapshot")
        # 第二階段（S1B）：同一把鎖內、只讀第一階段凍結輸入；失敗只寫 shadow_failure.json，不影響第一階段。
        try:
            shadow = commit_shadow_preview(final, base, captured)
        except Exception as exc:  # 防線：第二階段任何錯誤都不得讓第一階段失敗
            shadow = {"status": "SHADOW_FAILED", "error": type(exc).__name__, "detail": str(exc)}
        classification_added = 0
        if shadow.get("status") == "SHADOW_COMMITTED":
            import classification_history
            classification_added, _ = classification_history.append_snapshot(
                final, ledger=Path(root) / "04_回測" / "classification_history.jsonl",
                lock_holder="research_snapshot")
        return {"run_id": run_id, "directory": str(final), **marker, "shadow_status": shadow["status"],
                "classification_history_added": classification_added}
    except Exception as exc:
        if stage and stage.exists():
            try: _atomic_write(stage / "failure.json", canonical_bytes({"error": type(exc).__name__, "detail": str(exc)}))
            except Exception: pass
        raise
    finally:
        project_lock.release("research_snapshot")


def main(argv=None):
    parser = argparse.ArgumentParser(description="S1A deterministic research snapshot")
    parser.add_argument("--dry-run", action="store_true", help="compute and print hashes; write nothing")
    args = parser.parse_args(argv)
    result = dry_run() if args.dry_run else formal_run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
