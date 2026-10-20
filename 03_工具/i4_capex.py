"""T21/T21b：以 SEC 實際資本支出年增率計算影子 I4（G-S4-5c/d）。

AI 不判方向；本模組只使用 Company Facts 的現金流量表標籤，將 YTD 正規化
為單季，再由固定門檻計算 TTM 年增率、加速度與四家公司聚合結果。
"""
from __future__ import annotations

import argparse
import calendar
import copy
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from common import ROOT, load_json
from data_spine import OBSERVATIONS_PATH, parse_time, read_jsonl, resolve_visible_observation_versions
from fetch_financials import (_facts, append_financial_observations, normalize_duration,
                              bounded_period_history, _source_url)
from fetch_prices import http_get

TAG_MAP = Path(ROOT) / "00_規範" / "financial_tag_map.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SUBMISSIONS_FILE = "https://data.sec.gov/submissions/{name}"
SEC_USER_AGENT = "AI-stock-research local research zent960628@gmail.com"
METRIC = "FIN_CAPEX"
SOURCE_SYSTEM = "SEC_COMPANYFACTS_I4_CAPEX"
MAPPING_VERSION = "I4_CAPEX_G-S4-5cd_v2"
START_PERIOD = "2020-01-01"
# G-S4-5c clarification: only tags used by the calculation window matter.
# Nine quarters before 2020-01-01 begins on 2017-10-01.
TAG_CONSISTENCY_START = "2017-10-01"
UP, DOWN = 0.10, -0.10


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _get_json(url: str) -> dict:
    data = json.loads(http_get(url, {"User-Agent": SEC_USER_AGENT}))
    time.sleep(0.12)  # SEC published ceiling is 10 requests/second; stay below it.
    return data


def _submission_rows(payload: dict) -> list[dict]:
    recent = (payload.get("filings") or {}).get("recent") or {}
    keys = list(recent)
    return [dict(zip(keys, values)) for values in zip(*(recent.get(k, []) for k in keys))] if keys else []


def acceptance_map(cik: str, submissions: dict | None = None) -> dict[str, str]:
    """Accession -> SEC acceptance timestamp, including older submission shards."""
    submissions = submissions or _get_json(SEC_SUBMISSIONS.format(cik=cik))
    rows = _submission_rows(submissions)
    for item in (submissions.get("filings") or {}).get("files", []):
        name = item.get("name")
        if name:
            rows.extend(_submission_rows({"filings": {"recent": _get_json(SEC_SUBMISSIONS_FILE.format(name=name))}}))
    out = {}
    for row in rows:
        accn, accepted = row.get("accessionNumber"), row.get("acceptanceDateTime")
        if accn and accepted:
            text = str(accepted)
            if len(text) == 14 and text.isdigit():
                local = dt.datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("America/New_York"))
                text = local.isoformat()
            out[accn] = text
    return out


def _available_sort_key(fact: dict, accepted: dict[str, str] | None = None) -> tuple:
    available = (accepted or {}).get(fact.get("accn"))
    return (parse_time(available).isoformat() if available else fact.get("filed", ""), fact.get("accn", ""))


def _normalize_at_first_availability(rows: list[dict], accepted: dict[str, str] | None = None) -> list[tuple]:
    """逐 filing 累積正規化，固定每個單季／年度第一次可得的原始申報。"""
    accumulated, first = [], {}
    for fact in sorted(rows, key=lambda r: _available_sort_key(r, accepted)):
        accumulated.append(fact)
        for item in _normalize_capex_duration(accumulated):
            first.setdefault((item[0], item[1]), item)
    return [first[key] for key in sorted(first)]


def _fact_days(row: dict) -> int | None:
    if not row.get("start") or not row.get("end"):
        return None
    return (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(row["start"])).days + 1


def _latest_fact(rows: list[dict]) -> dict | None:
    # A filing can carry both current and comparative contexts under the same
    # fy/fp.  Prefer the later economic period, then the later filing.
    return max(rows, key=lambda r: (r.get("end", ""), r.get("filed", ""), r.get("accn", ""))) if rows else None


def _normalize_capex_duration(rows: list[dict]) -> list[tuple]:
    """I4 專用 YTD 正規化；排除同一 fp 下的 trailing-12-month comparative context。"""
    by_fy = {}
    for row in rows:
        if row.get("fy") is not None and row.get("fp") in {"Q1", "Q2", "Q3", "FY"}:
            by_fy.setdefault(str(row["fy"]), []).append(row)
    result = []
    for group in by_fy.values():
        q1 = _latest_fact([r for r in group if r.get("fp") == "Q1" and 60 <= (_fact_days(r) or 0) <= 120])
        q2 = _latest_fact([r for r in group if r.get("fp") == "Q2" and 121 <= (_fact_days(r) or 0) <= 220
                           and (not q1 or r.get("start") == q1.get("start"))])
        q3 = _latest_fact([r for r in group if r.get("fp") == "Q3" and 221 <= (_fact_days(r) or 0) <= 310
                           and (not q1 or r.get("start") == q1.get("start"))])
        annual = _latest_fact([r for r in group if r.get("fp") == "FY" and 300 <= (_fact_days(r) or 0) <= 400])
        if q1:
            result.append(("Q", q1["end"], q1, float(q1["val"]), "REPORTED_QUARTER"))
        if q2 and q1:
            fact = dict(q2, _normalized_period_start=(dt.date.fromisoformat(q1["end"]) + dt.timedelta(days=1)).isoformat())
            result.append(("Q", q2["end"], fact, float(q2["val"]) - float(q1["val"]), "YTD_Q2_MINUS_Q1"))
        if q3 and q2:
            fact = dict(q3, _normalized_period_start=(dt.date.fromisoformat(q2["end"]) + dt.timedelta(days=1)).isoformat())
            result.append(("Q", q3["end"], fact, float(q3["val"]) - float(q2["val"]), "YTD_Q3_MINUS_YTD_Q2"))
        if annual:
            result.append(("FY", annual["end"], annual, float(annual["val"]), "REPORTED_FY"))
            if q3 and annual.get("start") == q3.get("start"):
                fact = dict(annual, _normalized_period_start=(dt.date.fromisoformat(q3["end"]) + dt.timedelta(days=1)).isoformat())
                result.append(("Q", annual["end"], fact, float(annual["val"]) - float(q3["val"]), "FY_MINUS_YTD_Q3"))
    dedup = {}
    for item in result:
        key = (item[0], item[1])
        if key not in dedup or _available_sort_key(item[2]) > _available_sort_key(dedup[key][2]):
            dedup[key] = item
    return [dedup[key] for key in sorted(dedup)]


def _relative_difference(a: float, b: float) -> float:
    if a == b == 0:
        return 0.0
    denominator = max(abs(a), abs(b))
    return float("inf") if denominator == 0 else abs(a - b) / denominator


def select_history_tag(companyfacts: dict, priority: list[str], accepted: dict[str, str] | None = None) -> dict:
    concepts = (companyfacts.get("facts") or {}).get("us-gaap", {})
    by_tag = {tag: _facts(concepts.get(tag, {}), METRIC) for tag in priority}
    normalized = {tag: _normalize_at_first_availability(rows, accepted) for tag, rows in by_tag.items() if rows}
    used = {tag: [item for item in items if item[1] >= TAG_CONSISTENCY_START]
            for tag, items in normalized.items()}
    ranges = {tag: {"first_period_end": min((item[1] for item in items), default=None),
                    "last_period_end": max((item[1] for item in items), default=None),
                    "used_period_count": len(used.get(tag, []))}
              for tag, items in normalized.items()}
    present = [tag for tag in priority if used.get(tag)]
    if not present:
        return {"status": "MISSING_STANDARD_TAG", "tag": None, "rows": [], "tag_counts": {k: 0 for k in priority}}
    if len(present) == 1:
        tag = present[0]
        return {"status": "OK", "tag": tag,
                "normalized_rows": [item + (tag,) for item in normalized[tag]],
                "tag_counts": {k: len(v) for k, v in by_tag.items()}, "tag_period_ranges": ranges,
                "overlaps": [], "consistency_window_start": TAG_CONSISTENCY_START}

    maps = {tag: {(item[0], item[1]): item for item in used[tag]} for tag in present}
    overlap_keys = set(maps[present[0]])
    for tag in present[1:]:
        overlap_keys &= set(maps[tag])
    overlaps = []
    for key in sorted(overlap_keys):
        values = {tag: float(maps[tag][key][3]) for tag in present}
        differences = [_relative_difference(values[present[0]], values[tag]) for tag in present[1:]]
        overlaps.append({"period_type": key[0], "period_end": key[1], "values": values,
                         "max_relative_difference": max(differences, default=0.0)})
    if len(overlaps) < 2 or any(item["max_relative_difference"] > 0.005 for item in overlaps):
        return {"status": "TAG_SWITCH", "tag": None, "normalized_rows": [],
                "tag_counts": {k: len(v) for k, v in by_tag.items()}, "overlaps": overlaps,
                "tag_period_ranges": ranges, "consistency_window_start": TAG_CONSISTENCY_START,
                "continuity_reason": "OVERLAP_TOO_FEW" if len(overlaps) < 2 else "OVERLAP_VALUE_MISMATCH"}

    combined = {}
    for tag in present:
        for item in normalized[tag]:
            key = (item[0], item[1])
            candidate = item + (tag,)
            old = combined.get(key)
            if old is None or _available_sort_key(item[2], accepted) < _available_sort_key(old[2], accepted):
                combined[key] = candidate
    return {"status": "VERIFIED_OVERLAP", "tag": None,
            "normalized_rows": [combined[key] for key in sorted(combined)],
            "tag_counts": {k: len(v) for k, v in by_tag.items()}, "overlaps": overlaps,
            "tag_period_ranges": ranges, "consistency_window_start": TAG_CONSISTENCY_START,
            "continuity_reason": "ALL_OVERLAPS_WITHIN_0.5_PERCENT"}


def build_capex_observations(issuer_id: str, cfg: dict, companyfacts: dict, accepted: dict[str, str],
                             ingested_at: str) -> tuple[list[dict], dict]:
    picked = select_history_tag(companyfacts, cfg["tag_priority"], accepted)
    coverage = {k: v for k, v in picked.items() if k != "normalized_rows"}
    if picked["status"] not in {"OK", "VERIFIED_OVERLAP"}:
        return [], coverage
    missing_acceptance, rows = [], []
    for period_type, period_end, fact, value, derivation, source_tag in bounded_period_history(picked["normalized_rows"], 40, 12):
        if period_end < START_PERIOD:
            continue
        available = accepted.get(fact["accn"])
        if not available:
            missing_acceptance.append(fact["accn"])
            continue
        normalized_start = fact.get("_normalized_period_start") or fact.get("start")
        raw = "|".join([issuer_id, METRIC, period_type, period_end, fact["accn"], source_tag,
                        f"{float(value):.12g}", available, "I4_ACTUAL_CAPEX"])
        row = {
            "observation_id": "FIN_" + hashlib.sha256(raw.encode()).hexdigest(),
            "issuer_id": issuer_id, "security_id": None, "metric_id": METRIC,
            "value": float(value), "unit": fact["unit"], "period_start": normalized_start,
            "period_end": period_end, "period_type": period_type, "fiscal_year": fact.get("fy"),
            "fiscal_period": fact.get("fp"), "source_url": _source_url(cfg["cik"], fact["accn"]),
            "source_published_at": available, "available_at": available, "ingested_at": ingested_at,
            "availability_status": "POINT_IN_TIME_KNOWN", "source_system": SOURCE_SYSTEM,
            "taxonomy": "us-gaap", "source_tag": source_tag, "accession_number": fact["accn"],
            "derivation": derivation, "mapping_version": MAPPING_VERSION,
            "tag_continuity": "VERIFIED_OVERLAP" if picked["status"] == "VERIFIED_OVERLAP" else None,
            "provenance": {"policy": "G-S4-5c_G-S4-5d", "companyfacts_tag": source_tag,
                           "acceptance_timestamp_source": "SEC_SUBMISSIONS",
                           "tag_continuity": picked["status"], "overlap_validation": picked.get("overlaps", [])},
            "supersedes_observation_id": None,
        }
        rows.append(row)
    coverage.update(observation_count=len(rows), missing_acceptance=sorted(set(missing_acceptance)))
    if missing_acceptance:
        coverage["status"] = "MISSING_SEC_ACCEPTANCE_TIMESTAMP"
    return rows, coverage


def fetch_all(write: bool = False, companyfacts_dir: str | None = None, lock_holder: str | None = None,
              issuer_ids: set[str] | None = None) -> dict:
    config = load_json(str(TAG_MAP))["i4_capex_issuers"]
    ingested, all_rows, results = utc_now(), [], {}
    for issuer_id, cfg in config.items():
        if issuer_ids and issuer_id not in issuer_ids:
            continue
        if companyfacts_dir:
            companyfacts = json.loads((Path(companyfacts_dir) / f"CIK{cfg['cik']}.json").read_text(encoding="utf-8"))
            submissions_path = Path(companyfacts_dir) / f"CIK{cfg['cik']}_submissions.json"
            submissions = json.loads(submissions_path.read_text(encoding="utf-8"))
            accepted = acceptance_map(cfg["cik"], submissions)
        else:
            companyfacts = _get_json(SEC_FACTS.format(cik=cfg["cik"]))
            accepted = acceptance_map(cfg["cik"])
        rows, coverage = build_capex_observations(issuer_id, cfg, companyfacts, accepted, ingested)
        all_rows.extend(rows)
        results[issuer_id] = {"ticker": cfg["ticker"], **coverage}
    written = append_financial_observations(all_rows, ledger_reason="T21_I4_SEC_ACTUAL_CAPEX",
                                             lock_holder=lock_holder) if write else 0
    return {"source": "SEC_COMPANYFACTS_AND_SUBMISSIONS", "write": write, "preview_rows": len(all_rows),
            "written": written, "issuers": results}


def _add_months(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def stale_after(row: dict) -> dt.datetime:
    end = dt.date.fromisoformat(row["period_end"])
    next_end = _add_months(end, 3)
    annual_next = str(row.get("fiscal_period")) == "Q3"
    days = (90 if annual_next else 45) + 15
    return dt.datetime.combine(next_end + dt.timedelta(days=days), dt.time.max, dt.timezone.utc)


def company_result(rows: list[dict], issuer_id: str, cutoff: str | dt.datetime) -> dict:
    cutoff_time = parse_time(cutoff) if isinstance(cutoff, str) else cutoff
    candidates = [r for r in rows if r.get("issuer_id") == issuer_id and r.get("metric_id") == METRIC
                  and r.get("source_system") == SOURCE_SYSTEM and r.get("period_type") == "Q"
                  and parse_time(r.get("available_at")) <= cutoff_time]
    visible = resolve_visible_observation_versions(rows, candidates)
    latest_by_end = {}
    for row in visible:
        old = latest_by_end.get(row["period_end"])
        if not old or (row.get("available_at", ""), row.get("observation_id", "")) > (old.get("available_at", ""), old.get("observation_id", "")):
            latest_by_end[row["period_end"]] = row
    seq = [latest_by_end[k] for k in sorted(latest_by_end)]
    tags = sorted({r.get("source_tag") for r in seq})
    if len(tags) > 1 and not all(r.get("tag_continuity") == "VERIFIED_OVERLAP" for r in seq):
        return {"value": None, "status": "TAG_SWITCH", "tags": tags}
    if len(seq) < 8:
        return {"value": None, "status": "INSUFFICIENT_QUARTERS", "quarter_count": len(seq), "tag": tags[0] if tags else None}
    recent = seq[-8:]
    gaps = [(a["period_end"], b["period_end"],
             (dt.date.fromisoformat(b["period_end"]) - dt.date.fromisoformat(a["period_end"])).days)
            for a, b in zip(recent, recent[1:])]
    if any(not 80 <= days <= 100 for _a, _b, days in gaps):
        return {"value": None, "status": "NON_CONTIGUOUS_QUARTERS", "gaps": gaps,
                "quarter_count": len(seq), "tags": tags}
    latest = seq[-1]
    if cutoff_time > stale_after(latest):
        return {"value": None, "status": "STALE", "latest_period_end": latest["period_end"],
                "stale_after": stale_after(latest).isoformat(), "tag": tags[0]}
    current, prior = sum(float(r["value"]) for r in seq[-4:]), sum(float(r["value"]) for r in seq[-8:-4])
    if prior <= 0:
        return {"value": None, "status": "PRIOR_TTM_NOT_POSITIVE", "tag": tags[0]}
    growth = current / prior - 1.0
    value = "擴張" if growth >= UP else "收縮" if growth <= DOWN else "持平"
    prior_growth = None
    if len(seq) >= 9:
        acceleration_seq = seq[-9:]
        acceleration_gaps = [(dt.date.fromisoformat(b["period_end"]) - dt.date.fromisoformat(a["period_end"])).days
                             for a, b in zip(acceleration_seq, acceleration_seq[1:])]
        if all(80 <= days <= 100 for days in acceleration_gaps):
            older = sum(float(r["value"]) for r in seq[-9:-5])
            previous_ttm = sum(float(r["value"]) for r in seq[-5:-1])
            if older > 0:
                prior_growth = previous_ttm / older - 1.0
    return {"value": value, "status": "OK", "growth": growth,
            "acceleration": None if prior_growth is None else growth - prior_growth,
            "ttm": current, "prior_ttm": prior, "unit": latest["unit"],
            "tag": tags[0] if len(tags) == 1 else "VERIFIED_OVERLAP:" + "+".join(tags),
            "latest_period_end": latest["period_end"], "available_at": latest["available_at"],
            "observation_ids": [r["observation_id"] for r in seq[-9:]]}


def aggregate(results: dict[str, dict]) -> dict:
    valid = {k: v for k, v in results.items() if v.get("value") in {"擴張", "持平", "收縮"}}
    if len(valid) < 3:
        return {"value": None, "score": None, "status": "FEWER_THAN_THREE_COMPANIES",
                "n_valid": len(valid), "companies": {k: v.get("value") for k, v in results.items()}}
    counts = {x: sum(v["value"] == x for v in valid.values()) for x in ("擴張", "持平", "收縮")}
    if counts["收縮"] >= 2: value, score = "多家收縮", -1
    elif counts["收縮"] == 1: value, score = "一家收縮", 0
    elif counts["擴張"] >= 1: value, score = "擴張", 1
    else: value, score = "持平", 0
    return {"value": value, "score": score, "status": "OK", "n_valid": len(valid), "counts": counts,
            "companies": {k: v["value"] for k, v in valid.items()}}


def current_snapshot(cutoff: str | None = None, observations: list[dict] | None = None) -> dict:
    cutoff = cutoff or utc_now()
    rows = observations if observations is not None else read_jsonl(OBSERVATIONS_PATH)
    config = load_json(str(TAG_MAP))["i4_capex_issuers"]
    companies = {issuer: ({"value": None, "status": "TAG_SWITCH", "tag": None}
                          if cfg.get("tag_status") == "TAG_SWITCH" else company_result(rows, issuer, cutoff))
                 for issuer, cfg in config.items()}
    return {"mode": "SHADOW", "policy": "G-S4-5c_G-S4-5d", "cutoff": cutoff, "companies": companies,
            "aggregate": aggregate(companies), "not_a_conclusion": True}


def candidate_comparison() -> dict:
    """Rules 1.10.0 vs 1.11.0 on the same frozen inputs; no formal write."""
    import research_snapshot
    cutoff = parse_time(utc_now())
    source = research_snapshot.capture_inputs(Path(ROOT))
    versions = {}
    root = Path(ROOT) / "02_執行紀錄/影子快照"
    for version in ("1.10.0", "1.11.0"):
        captured = dict(source)
        captured["rules"] = (Path(ROOT) / "00_規範" / "rules_歷史" / f"rules_{version}.json").read_bytes()
        snap = research_snapshot.build_snapshot(captured, cutoff)
        frozen = research_snapshot.frozen_schema2_inputs(captured, root)
        run_id = "SNAP_PRECOMMIT_T22_" + version.replace(".", "_") + "_" + research_snapshot.digest(
            research_snapshot.canonical_bytes(snap))[:12]
        frozen, snap_sha = research_snapshot.bind_precommit_candidate(frozen, captured, snap, run_id)
        results = {market: research_snapshot.shadow_market_result(market, snap, run_id, snap_sha, frozen)
                   for market in research_snapshot.SHADOW_MARKETS}
        research_snapshot.precommit_comparison_gate(results)
        versions[version] = {"captured": captured, "snapshot": snap, "inputs": frozen,
                             "run_id": run_id, "snapshot_sha256": snap_sha, "results": results}
    actual_i4 = versions["1.11.0"]["snapshot"]["activated_fields"].get("i4_actual_capex") or current_snapshot(
        cutoff.isoformat(), versions["1.11.0"]["inputs"]["observations"])
    markets = {}
    for market in research_snapshot.SHADOW_MARKETS:
        base = versions["1.10.0"]["results"][market]
        candidate = versions["1.11.0"]["results"][market]
        base_companies = {c["ticker"]: c for c in base["judge"]["companies"]}
        cand_companies = {c["ticker"]: c for c in candidate["judge"]["companies"]}
        base_themes, cand_themes = base["judge"]["themes"], candidate["judge"]["themes"]
        markets[market] = {
            "themes": {t: {"current_score": (base_themes.get(t) or {}).get("score"),
                            "candidate_score": (cand_themes.get(t) or {}).get("score"),
                            "current_missing": (base_themes.get(t) or {}).get("missing"),
                            "candidate_missing": (cand_themes.get(t) or {}).get("missing")}
                       for t in sorted(set(base_themes) | set(cand_themes))},
            "companies": {t: {"current": base_companies[t]["verdict"], "candidate": cand_companies[t]["verdict"],
                               "changed": base_companies[t]["verdict"] != cand_companies[t]["verdict"]}
                          for t in sorted(base_companies)},
        }
    return {"mode": "SHADOW_CANDIDATE_COMPARISON", "not_a_conclusion": True,
            "current_rules": "1.10.0", "candidate_rules": "1.11.0",
            "candidate_inputs": {"I4_actual_capex": actual_i4,
                                 "precommit_run_ids": {v: versions[v]["run_id"] for v in versions}},
            "markets": markets}


def comparison_markdown(data: dict) -> str:
    lines = ["# T21 I4 SEC 實際資本支出影子比較", "", "**影子候選，不是正式結論；rules 1.11.0 尚未啟用。**", "",
             f"- 現行規則：`{data['current_rules']}`", f"- 候選規則：`{data['candidate_rules']}`", "",
             "## I4 實際資本支出", "", "| 發行人 | 狀態 | TTM 年增 | 加速度 | 判定 | 標籤 |", "|---|---|---:|---:|---|---|"]
    for issuer, item in data["candidate_inputs"]["I4_actual_capex"]["companies"].items():
        growth = "—" if item.get("growth") is None else f"{item['growth']*100:.1f}%"
        accel = "—" if item.get("acceleration") is None else f"{item['acceleration']*100:.1f}pp"
        lines.append(f"| {issuer} | {item.get('status')} | {growth} | {accel} | {item.get('value') or '—'} | {item.get('tag') or '—'} |")
    agg = data["candidate_inputs"]["I4_actual_capex"]["aggregate"]
    lines += ["", f"聚合：`{agg.get('value')}`，分數 `{agg.get('score')}`，有效公司 `{agg.get('n_valid')}`。", "",
              "## 主線分數變化", "", "| 市場 | 主線 | 現行分數 | 候選分數 | 現行缺漏 | 候選缺漏 |", "|---|---|---:|---:|---|---|"]
    for market, section in data["markets"].items():
        for theme, item in section["themes"].items():
            lines.append(f"| {market} | {theme} | {item.get('current_score')} | {item.get('candidate_score')} | {', '.join(item.get('current_missing') or []) or '—'} | {', '.join(item.get('candidate_missing') or []) or '—'} |")
    lines += ["", "## 研究分類變化", "", "| 市場 | 代號 | 現行 | 候選 | 是否改變 |", "|---|---|---|---|---|"]
    for market, section in data["markets"].items():
        for ticker, item in section["companies"].items():
            lines.append(f"| {market} | {ticker} | {item['current']} | {item['candidate']} | {'是' if item['changed'] else '否'} |")
    lines += ["", "財測版 I4 保留在 AI 欄位帳本，只作參考，未納入本候選分數。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--write", action="store_true")
    p.add_argument("--companyfacts-dir")
    p.add_argument("--lock-holder")
    p.add_argument("--issuers", help="Comma-separated issuer IDs; omitted means all configured issuers")
    p.add_argument("--output")
    p.add_argument("--candidate-report")
    args = p.parse_args(argv)
    if args.write and not args.fetch:
        p.error("--write requires --fetch")
    issuer_ids = {x.strip() for x in (args.issuers or "").split(",") if x.strip()} or None
    result = candidate_comparison() if args.candidate_report else (fetch_all(args.write, args.companyfacts_dir, args.lock_holder, issuer_ids) if args.fetch else current_snapshot())
    if args.fetch:
        result["shadow"] = current_snapshot() if args.write else None
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.candidate_report:
        Path(args.candidate_report).write_text(comparison_markdown(result), encoding="utf-8")
    print(text)
    handled = {"OK", "TAG_SWITCH", "MISSING_STANDARD_TAG"}
    return 0 if all(v.get("status") in handled for v in result.get("issuers", {}).values()) else (0 if not args.fetch else 2)


if __name__ == "__main__":
    raise SystemExit(main())
