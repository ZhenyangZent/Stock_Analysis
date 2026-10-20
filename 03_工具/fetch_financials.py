"""從官方來源預覽或匯入財報資料；已查證為零必須另有逐筆聲明與獨立複查。

  python 03_工具/fetch_financials.py --preview
  python 03_工具/fetch_financials.py --tickers MU,MRVL --preview
  python 03_工具/fetch_financials.py --preview --include-pending-zero-assertions
  python 03_工具/fetch_financials.py --write
  python 03_工具/fetch_financials.py --probe-taiwan 2330

SEC 只接受 financial_tag_map.json 的精確白名單標籤。缺標籤就是缺資料，不用文字
相似度猜測。臺股只用官方 OpenAPI 做來源能力探測；完整 H1-H7 仍等待 MOPS XBRL adapter。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

from common import ROOT, file_lock, load_json, load_rules, out
from data_spine import (OBSERVATIONS_PATH, parse_time, read_jsonl, validate_observation,
                        assert_write_ledger_in_sync, recover_pending_write, transactional_append_observations)
from fetch_prices import CTX_NAME, SSL_HINT, http_get

TAG_MAP_PATH = os.path.join(ROOT, "00_規範", "financial_tag_map.json")
ZERO_ASSERTIONS_PATH = os.path.join(ROOT, "00_規範", "financial_zero_assertions.json")
SEC_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
INSTANT_METRICS = {"FIN_CASH", "FIN_SHORT_TERM_INVESTMENTS", "FIN_DEBT_CURRENT", "FIN_DEBT_NONCURRENT",
                   "FIN_DEBT_TOTAL", "FIN_CURRENT_ASSETS", "FIN_CURRENT_LIABILITIES"}
SHARE_METRICS = {"FIN_DILUTED_SHARES"}
ZERO_REVIEW_STATUSES = {"PENDING_INDEPENDENT_REVIEW", "APPROVED_BY_INDEPENDENT_AI", "REJECTED"}


def _facts(concept, metric_id):
    units = concept.get("units", {})
    preferred = "shares" if metric_id in SHARE_METRICS else "USD"
    rows = units.get(preferred, [])
    keep = []
    for row in rows:
        if row.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
            continue
        if not row.get("end") or row.get("val") is None or not row.get("filed") or not row.get("accn"):
            continue
        keep.append(dict(row, unit=preferred))
    return keep


def choose_tag(companyfacts, metric_id, allowlist):
    concepts = (companyfacts.get("facts") or {}).get("us-gaap", {})
    for tag in allowlist:
        rows = _facts(concepts.get(tag, {}), metric_id)
        if rows:
            return tag, rows
    return None, []


def _duration_days(row):
    if not row.get("start"):
        return None
    return (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(row["start"])).days + 1


def _latest(rows):
    return max(rows, key=lambda r: (r.get("end", ""), r.get("filed", ""), r.get("accn", ""))) if rows else None


def normalize_instant(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["end"], []).append(row)
    return [("INSTANT", end, _latest(group)) for end, group in sorted(grouped.items())]


def _pick_period(rows, fp, prefer_long=False):
    candidates = [r for r in rows if r.get("fp") == fp and _duration_days(r) is not None]
    if not candidates:
        return None
    if prefer_long:
        return max(candidates, key=lambda r: (r.get("end", ""), _duration_days(r), r.get("filed", "")))
    direct = [r for r in candidates if 60 <= _duration_days(r) <= 120]
    return _latest(direct) or _latest(candidates)


def normalize_duration(rows):
    """把 SEC YTD flow 正規化成單季；保留完整年度。無法可靠相減就不產生。"""
    by_fy = {}
    for row in rows:
        if row.get("fy") is not None and row.get("fp") in {"Q1", "Q2", "Q3", "FY"}:
            by_fy.setdefault(str(row["fy"]), []).append(row)
    result = []
    for fy, group in sorted(by_fy.items()):
        q1 = _pick_period(group, "Q1")
        q2y = _pick_period(group, "Q2", True)
        q3y = _pick_period(group, "Q3", True)
        annual = _pick_period(group, "FY", True)
        if q1:
            result.append(("Q", q1["end"], q1, float(q1["val"]), "REPORTED_QUARTER"))
        q2_value = None
        if q2y:
            if _duration_days(q2y) > 120 and q1 and q2y["start"] == q1.get("start"):
                q2_value = float(q2y["val"]) - float(q1["val"])
                derivation = "YTD_Q2_MINUS_Q1"
            elif _duration_days(q2y) <= 120:
                q2_value, derivation = float(q2y["val"]), "REPORTED_QUARTER"
            if q2_value is not None:
                normalized_fact = dict(q2y)
                if derivation == "YTD_Q2_MINUS_Q1":
                    normalized_fact["_normalized_period_start"] = (
                        dt.date.fromisoformat(q1["end"]) + dt.timedelta(days=1)).isoformat()
                result.append(("Q", q2y["end"], normalized_fact, q2_value, derivation))
        q3_value = None
        if q3y:
            if _duration_days(q3y) > 180 and q2y and q3y["start"] == q2y.get("start"):
                q3_value = float(q3y["val"]) - float(q2y["val"])
                derivation = "YTD_Q3_MINUS_YTD_Q2"
            elif _duration_days(q3y) <= 120:
                q3_value, derivation = float(q3y["val"]), "REPORTED_QUARTER"
            if q3_value is not None:
                normalized_fact = dict(q3y)
                if derivation == "YTD_Q3_MINUS_YTD_Q2":
                    normalized_fact["_normalized_period_start"] = (
                        dt.date.fromisoformat(q2y["end"]) + dt.timedelta(days=1)).isoformat()
                result.append(("Q", q3y["end"], normalized_fact, q3_value, derivation))
        if annual:
            result.append(("FY", annual["end"], annual, float(annual["val"]), "REPORTED_FY"))
            if q3y and annual["start"] == q3y.get("start"):
                normalized_fact = dict(annual)
                normalized_fact["_normalized_period_start"] = (
                    dt.date.fromisoformat(q3y["end"]) + dt.timedelta(days=1)).isoformat()
                result.append(("Q", annual["end"], normalized_fact,
                               float(annual["val"]) - float(q3y["val"]), "FY_MINUS_YTD_Q3"))
    dedup = {}
    for item in result:
        key = (item[0], item[1])
        if key not in dedup or item[2].get("filed", "") > dedup[key][2].get("filed", ""):
            dedup[key] = item
    return [dedup[k] for k in sorted(dedup)]


def normalize_shares(rows):
    """加權平均股數不可用 YTD 相減；接受直接申報的單季與完整年度 context。"""
    grouped = {}
    annual = {}
    for row in rows:
        days = _duration_days(row)
        if days is None:
            continue
        if 60 <= days <= 120:
            grouped.setdefault(row["end"], []).append(row)
        elif row.get("fp") == "FY" and 300 <= days <= 400:
            annual.setdefault(row["end"], []).append(row)
    result = [("Q", end, _latest(group), float(_latest(group)["val"]), "REPORTED_QUARTER")
              for end, group in sorted(grouped.items())]
    result += [("FY", end, _latest(group), float(_latest(group)["val"]), "REPORTED_FY")
               for end, group in sorted(annual.items())]
    return result


def bounded_period_history(normalized, quarter_limit=28, fy_limit=8):
    """Retain bounded Q and FY histories independently."""
    quarterly = [item for item in normalized if item[0] == "Q"][-quarter_limit:]
    fiscal_year = [item for item in normalized if item[0] == "FY"][-fy_limit:]
    return quarterly + fiscal_year


def normalize_period_fallback(companyfacts, metric_id, fallback_groups):
    """把明確等義但分年度更換的標籤合併後再正規化；同一 filing/context 依宣告順序取第一個。"""
    combined, selected = [], []
    seen_contexts = set()
    for allowlist in fallback_groups:
        tag, rows = choose_tag(companyfacts, metric_id, allowlist)
        if not tag:
            selected.append({"tag": None, "allowlist": allowlist})
            continue
        selected.append({"tag": tag, "allowlist": allowlist})
        for source in rows:
            key = (source.get("accn"), source.get("form"), source.get("fp"), source.get("fy"),
                   source.get("start"), source.get("end"))
            if key in seen_contexts:
                continue
            seen_contexts.add(key)
            combined.append(dict(source, _source_tag=tag))
    normalized = []
    for item in normalize_duration(combined):
        normalized.append(item + (item[2].get("_source_tag"),))
    status = "OK" if normalized else "NO_FALLBACK_PERIODS"
    return normalized, {"status": status, "components": selected, "fact_count": len(normalized)}


def normalize_composite(companyfacts, metric_id, components):
    """把兩個明確不重疊的流量項目在同期間相加；任一 component 缺漏就不產生。"""
    normalized = []
    selected = []
    for allowlist in components:
        tag, rows = choose_tag(companyfacts, metric_id, allowlist)
        if not tag:
            return [], {"status": "MISSING_COMPOSITE_COMPONENT", "components": selected + [{"tag": None, "allowlist": allowlist}]}
        selected.append({"tag": tag, "allowlist": allowlist})
        normalized.append({(x[0], x[1]): x for x in normalize_duration(rows)})
    common = set(normalized[0])
    for values in normalized[1:]:
        common &= set(values)
    result = []
    for key in sorted(common):
        parts = [values[key] for values in normalized]
        facts = [part[2] for part in parts]
        result.append((key[0], key[1], facts, sum(float(part[3]) for part in parts),
                       "COMPONENT_SUM:" + "+".join(x["tag"] for x in selected)))
    return result, {"status": "OK" if result else "NO_COMMON_COMPONENT_PERIOD", "components": selected}


def normalize_difference(companyfacts, metric_id, components):
    """以兩個明確項目相減產生衍生流量；兩者必須同期間，任一缺漏就不產生。"""
    selected = []
    normalized = []
    for role in ("minuend", "subtrahend"):
        allowlist = components.get(role, [])
        tag, rows = choose_tag(companyfacts, metric_id, allowlist)
        if not tag:
            return [], {"status": "MISSING_DIFFERENCE_COMPONENT",
                        "components": selected + [{"role": role, "tag": None, "allowlist": allowlist}]}
        selected.append({"role": role, "tag": tag, "allowlist": allowlist})
        normalized.append({(x[0], x[1]): x for x in normalize_duration(rows)})
    common = set(normalized[0]) & set(normalized[1])
    result = []
    for key in sorted(common):
        minuend, subtrahend = normalized[0][key], normalized[1][key]
        facts = [minuend[2], subtrahend[2]]
        result.append((key[0], key[1], facts, float(minuend[3]) - float(subtrahend[3]),
                       "COMPONENT_DIFFERENCE:" + selected[0]["tag"] + "-" + selected[1]["tag"]))
    return result, {"status": "OK" if result else "NO_COMMON_DIFFERENCE_PERIOD", "components": selected}


def _available_at(filed):
    # SEC companyfacts 只有 filed 日期；用美東當日結束作保守可得時間。
    return filed + "T23:59:59-05:00"


def _source_url(cik, accn):
    folder = accn.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{accn}-index.html"


def build_observations(issuer_id, issuer_cfg, companyfacts, ingested_at, mapping_version=None):
    observations, coverage = [], {}
    metric_ids = list(issuer_cfg["tags"])
    for metric_id in issuer_cfg.get("composites", {}):
        if metric_id not in metric_ids:
            metric_ids.append(metric_id)
    for metric_id in issuer_cfg.get("differences", {}):
        if metric_id not in metric_ids:
            metric_ids.append(metric_id)
    for metric_id in metric_ids:
        allowlist = issuer_cfg["tags"].get(metric_id, [])
        composite = issuer_cfg.get("composites", {}).get(metric_id)
        difference = issuer_cfg.get("differences", {}).get(metric_id)
        period_fallback = issuer_cfg.get("period_fallbacks", {}).get(metric_id)
        if composite:
            normalized, composite_coverage = normalize_composite(companyfacts, metric_id, composite)
            tag, rows = None, []
            coverage[metric_id] = composite_coverage
            coverage[metric_id]["fact_count"] = len(normalized)
        elif difference:
            normalized, difference_coverage = normalize_difference(companyfacts, metric_id, difference)
            tag, rows = None, []
            coverage[metric_id] = difference_coverage
            coverage[metric_id]["fact_count"] = len(normalized)
        elif period_fallback:
            normalized, fallback_coverage = normalize_period_fallback(companyfacts, metric_id, period_fallback)
            tag, rows = None, []
            coverage[metric_id] = fallback_coverage
        else:
            tag, rows = choose_tag(companyfacts, metric_id, allowlist)
            coverage[metric_id] = {"tag": tag, "fact_count": len(rows), "status": "OK" if tag else "MISSING_STANDARD_TAG"}
            if not tag:
                continue
            normalized = normalize_instant(rows) if metric_id in INSTANT_METRICS else (normalize_shares(rows) if metric_id in SHARE_METRICS else normalize_duration(rows))
        # Q and FY are sorted as separate period-type groups.  A single tail
        # slice therefore dropped every FY row once a tag had enough Q rows.
        # Preserve bounded histories independently: recent 28 quarters and
        # recent 8 fiscal years.
        for item in bounded_period_history(normalized):
            component_facts = None
            if composite or difference:
                period_type, period_end, component_facts, value, derivation = item
                fact = max(component_facts, key=lambda x: (x.get("filed", ""), x.get("accn", "")))
                source_tags = [x["tag"] for x in coverage[metric_id]["components"]]
                accession_numbers = sorted({x["accn"] for x in component_facts})
            elif period_fallback:
                period_type, period_end, fact, value, derivation, fallback_tag = item
            elif metric_id in INSTANT_METRICS:
                period_type, period_end, fact = item
                value, derivation = float(fact["val"]), "REPORTED_INSTANT"
            else:
                period_type, period_end, fact, value, derivation = item
            available = _available_at(fact["filed"])
            source_tag = (("+" if composite else "-").join(source_tags)
                          if composite or difference else (fallback_tag if period_fallback else tag))
            accession_number = "|".join(accession_numbers) if composite or difference else fact["accn"]
            normalized_period_start = fact.get("_normalized_period_start") or fact.get("start")
            raw_parts = [issuer_id, metric_id, period_type, period_end, accession_number, source_tag, f"{value:.12g}"]
            if normalized_period_start != fact.get("start"):
                raw_parts.append("NORMALIZED_PERIOD_START=" + str(normalized_period_start))
            raw_id = "|".join(raw_parts)
            row = {
                "observation_id": "FIN_" + hashlib.sha256(raw_id.encode()).hexdigest(),
                "issuer_id": issuer_id,
                "security_id": None,
                "metric_id": metric_id,
                "value": value,
                "unit": fact["unit"],
                "period_start": normalized_period_start,
                "period_end": period_end,
                "period_type": period_type,
                "fiscal_year": fact.get("fy"),
                "fiscal_period": fact.get("fp"),
                "source_url": _source_url(issuer_cfg["cik"], fact["accn"]),
                "source_published_at": available,
                "available_at": available,
                "ingested_at": ingested_at,
                "availability_status": "POINT_IN_TIME_KNOWN",
                "source_system": "SEC_COMPANYFACTS",
                "taxonomy": "us-gaap",
                "source_tag": source_tag,
                "accession_number": accession_number,
                "derivation": derivation,
                "mapping_version": mapping_version,
                "supersedes_observation_id": None,
            }
            if component_facts:
                row["component_source_urls"] = [_source_url(issuer_cfg["cik"], x["accn"]) for x in component_facts]
                row["component_source_tags"] = source_tags
            observations.append(row)
    return observations, coverage


def validate_zero_assertion(assertion):
    required = ("assertion_id", "issuer_id", "metric_id", "value", "unit", "period_end", "period_type",
                "accession_number", "filing_url", "checked_by", "checked_at", "valid_through_period_end",
                "filed_date", "evidence", "review_status")
    errors = [f"缺少 {key}" for key in required if key not in assertion]
    if assertion.get("value") != 0: errors.append("value 必須是數字 0")
    if assertion.get("period_type") != "INSTANT": errors.append("period_type 必須是 INSTANT")
    if assertion.get("period_end") != assertion.get("valid_through_period_end"): errors.append("只允許單一申報期；valid_through_period_end 必須等於 period_end")
    if assertion.get("review_status") not in ZERO_REVIEW_STATUSES: errors.append("review_status 無效")
    if assertion.get("source_system", "SEC_FILING_ZERO_ASSERTION") not in ("SEC_FILING_ZERO_ASSERTION", "MOPS_FILING_ZERO_ASSERTION"):
        errors.append("source_system 無效")
    if assertion.get("source_system") == "MOPS_FILING_ZERO_ASSERTION":
        for key in ("raw_sha256", "report_scope", "source_published_at"):
            if not assertion.get(key): errors.append(f"MOPS 零值聲明缺少 {key}")
    if assertion.get("review_status") == "APPROVED_BY_INDEPENDENT_AI":
        for key in ("reviewed_by", "reviewed_at"):
            if not assertion.get(key): errors.append(f"核准聲明缺少 {key}")
        if assertion.get("reviewed_by") == assertion.get("checked_by"): errors.append("查證者與複查者不可相同")
    return errors


def zero_assertion_observations(data, ingested_at, include_pending=False):
    rows, registry = [], []
    for assertion in data.get("assertions", []):
        errors = validate_zero_assertion(assertion)
        if errors:
            raise ValueError(f"INVALID_ZERO_ASSERTION:{assertion.get('assertion_id')}:{'|'.join(errors)}")
        registry.append({"assertion_id": assertion["assertion_id"], "issuer_id": assertion["issuer_id"],
                         "metric_id": assertion["metric_id"], "period_end": assertion["period_end"],
                         "review_status": assertion["review_status"], "filing_url": assertion["filing_url"]})
        approved = assertion["review_status"] == "APPROVED_BY_INDEPENDENT_AI"
        if not approved and not include_pending:
            continue
        available = assertion.get("source_published_at") or _available_at(assertion["filed_date"])
        raw_id = "|".join([assertion["assertion_id"], assertion["review_status"], assertion["period_end"]])
        rows.append({
            "observation_id": "FIN_ZERO_" + hashlib.sha256(raw_id.encode()).hexdigest(),
            "issuer_id": assertion["issuer_id"], "security_id": None,
            "metric_id": assertion["metric_id"], "value": 0.0, "unit": assertion["unit"],
            "period_start": None, "period_end": assertion["period_end"], "period_type": "INSTANT",
            "fiscal_year": assertion.get("fiscal_year"), "fiscal_period": assertion.get("fiscal_period"),
            "source_url": assertion["filing_url"], "source_published_at": available,
            "available_at": available, "ingested_at": ingested_at,
            "availability_status": "POINT_IN_TIME_KNOWN", "source_system": assertion.get("source_system", "SEC_FILING_ZERO_ASSERTION"),
            "taxonomy": "human_verified_filing", "source_tag": "VERIFIED_ZERO_ASSERTION",
            "accession_number": assertion["accession_number"], "derivation": "VERIFIED_ZERO_ASSERTION",
            "zero_assertion_id": assertion["assertion_id"], "zero_review_status": assertion["review_status"],
            "checked_by": assertion["checked_by"], "checked_at": assertion["checked_at"],
            "reviewed_by": assertion.get("reviewed_by"), "reviewed_at": assertion.get("reviewed_at"),
            "supersedes_observation_id": None,
        })
    return rows, registry


def fetch_sec(issuer_id, cfg, ingested_at, companyfacts_dir=None, mapping_version=None):
    url = SEC_URL.format(cik=cfg["cik"])
    if companyfacts_dir:
        snapshot = os.path.join(companyfacts_dir, f"CIK{cfg['cik']}.json")
        with open(snapshot, encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = json.loads(http_get(url, {"User-Agent": "AI-stock-research/1.3.3 personal local use"}))
    observations, coverage = build_observations(issuer_id, cfg, data, ingested_at, mapping_version)
    return {"issuer_id": issuer_id, "ticker": cfg["ticker"], "source_url": url,
            "entity_name": data.get("entityName"), "coverage": coverage,
            "preview_observation_count": len(observations), "observations": observations}


def probe_taiwan(ticker):
    urls = {
        "balance_sheet": "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
        "income_statement": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    }
    result = {"ticker": ticker, "status": "DATA_INSUFFICIENT_MOPS_XBRL_ADAPTER_PENDING", "official_openapi": {}}
    for name, url in urls.items():
        rows = json.loads(http_get(url))
        hit = next((row for row in rows if str(row.get("公司代號", "")).strip() == ticker), None)
        result["official_openapi"][name] = {"url": url, "found": bool(hit), "year": (hit or {}).get("年度"),
                                                   "quarter": (hit or {}).get("季別"), "published_date": (hit or {}).get("出表日期"),
                                                   "fields": sorted((hit or {}).keys())}
    result["reason"] = "官方 OpenAPI 無現金流量表與完整債務／利息細項；完整 H1-H7 必須等待 MOPS XBRL adapter。"
    return result


def append_financial_observations(rows, path=OBSERVATIONS_PATH, ledger_reason="APPEND_FINANCIAL_OBSERVATIONS",
                                  lock_holder=None):
    """批次 append-only；較新的 filing 才能 supersede 同一發行人／指標／期間的舊列。

    1.8.2（C181-02）：每次有新增列，同時在同目錄的 observation_write_ledger.jsonl 追加一筆寫入紀錄
    （實際寫入時間、observation_id、寫入後檔案長度與 SHA-256）。紀錄檔與檔案不同步時拒絕寫入。
    1.8.3（C182-02）：經 data_spine.transactional_append_observations 寫入，觀測值與紀錄一起成功或一起失敗；
    開始前先處理殘留的 journal。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with file_lock(path):
        recover_pending_write(path, lock_holder=lock_holder)
        if os.path.exists(path):
            assert_write_ledger_in_sync(path)
        existing = read_jsonl(path)
        ids = {r.get("observation_id") for r in existing}
        superseded = {r.get("supersedes_observation_id") for r in existing if r.get("supersedes_observation_id")}
        active = {}
        for row in existing:
            if row.get("observation_id") in superseded:
                continue
            key = (row.get("issuer_id"), row.get("security_id"), row.get("metric_id"), row.get("period_type"), row.get("period_end"))
            active[key] = row
        additions = []
        for original in sorted(rows, key=lambda r: (int(r.get("manifest_revision_sequence", 0)),
                                                     r.get("source_published_at") or "",
                                                     r.get("issuer_id", ""), r.get("metric_id", ""),
                                                     r.get("period_end", ""))):
            row = dict(original)
            if row.get("observation_id") in ids:
                continue
            key = (row.get("issuer_id"), row.get("security_id"), row.get("metric_id"), row.get("period_type"), row.get("period_end"))
            parent = active.get(key)
            declared_parent = row.get("supersedes_observation_id")
            if declared_parent and (not parent or declared_parent != parent.get("observation_id")):
                raise ValueError(f"STALE_OR_INVALID_SUPERSEDES:{row.get('observation_id')}:{declared_parent}")
            if parent:
                if row.get("source_system") == "MOPS_XBRL":
                    new_seq = int(row.get("manifest_revision_sequence", 0))
                    old_seq = int(parent.get("manifest_revision_sequence", 0))
                    raw_changed = row.get("raw_sha256") != parent.get("raw_sha256")
                    mapping_changed = row.get("mapping_version") != parent.get("mapping_version")
                    components_changed = row.get("components_sha256") != parent.get("components_sha256")
                    if parent.get("source_system") == "MOPS_XBRL":
                        if new_seq < old_seq or (raw_changed and new_seq <= old_seq):
                            continue
                    if raw_changed:
                        reason = "RAW_FILE_CORRECTION"
                    elif mapping_changed:
                        reason = "TAG_MAPPING_RULE_CHANGE"
                    elif components_changed:
                        reason = "DERIVATION_COMPONENT_CHANGE"
                    else:
                        continue
                    row["supersedes_observation_id"] = parent["observation_id"]
                    row["revision_reason"] = reason
                elif row.get("source_system") == "MOPS_PDF":
                    new_seq = int(row.get("manifest_revision_sequence", 0))
                    old_seq = int(parent.get("manifest_revision_sequence", 0))
                    new_order = parse_time(row.get("source_published_at") or row.get("available_at") or row.get("ingested_at"))
                    old_order = parse_time(parent.get("source_published_at") or parent.get("available_at") or parent.get("ingested_at"))
                    if ((new_seq and old_seq and new_seq < old_seq) or
                            (not (new_seq and old_seq) and new_order < old_order)):
                        continue
                    if new_seq == old_seq and new_order == old_order:
                        same_fact = all(row.get(k) == parent.get(k) for k in
                                        ("value", "raw_sha256", "derivation"))
                        if same_fact:
                            continue
                        raise ValueError(f"MOPS_PDF_SAME_FILING_CONFLICT:{row.get('observation_id')}:{parent.get('observation_id')}")
                    row["supersedes_observation_id"] = parent["observation_id"]
                    row["revision_reason"] = ("COMPARATIVE_CONFIRMED_UNCHANGED"
                                              if row.get("value") == parent.get("value")
                                              else "COMPARATIVE_RESTATED")
                else:
                    new_published = parse_time(row["source_published_at"])
                    old_published = parse_time(parent["source_published_at"])
                    same_filing_mapping_correction = (
                        new_published == old_published
                        and row.get("mapping_version")
                        and (row.get("source_tag"), row.get("derivation"), row.get("value"), row.get("period_start"))
                        != (parent.get("source_tag"), parent.get("derivation"), parent.get("value"), parent.get("period_start"))
                    )
                    if new_published < old_published or (new_published == old_published and not same_filing_mapping_correction):
                        continue
                    row["supersedes_observation_id"] = parent["observation_id"]
                    if same_filing_mapping_correction:
                        row["correction_reason"] = "TAG_MAPPING_RULE_CHANGE"
            errors = validate_observation(row)
            if errors:
                raise ValueError(f"INVALID_FINANCIAL_OBSERVATION:{row.get('observation_id')}:{'|'.join(errors)}")
            additions.append(row)
            ids.add(row["observation_id"])
            active[key] = row
        if additions:
            transactional_append_observations(path, additions, ledger_reason, lock_holder=lock_holder)
    return len(additions)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        out("用法：python 03_工具/fetch_financials.py [--write] [--tickers T1,T2] "
            "[--companyfacts-dir PATH] [--probe-taiwan TICKER] "
            "[--include-pending-zero-assertions]\n"
            "預設為預覽；只有 --write 會附加 canonical observations。待複查零值只可預覽。")
        return
    rules = load_rules()
    tags = load_json(TAG_MAP_PATH)
    zero_data = load_json(ZERO_ASSERTIONS_PATH)
    write_mode = "--write" in sys.argv
    lock_holder = (sys.argv[sys.argv.index("--lock-holder") + 1]
                   if "--lock-holder" in sys.argv else None)
    if write_mode:
        if rules["health"]["canonical_write_status"] != "ENABLED_AFTER_INDEPENDENT_REVIEW":
            raise PermissionError("CANONICAL_WRITE_BLOCKED_PENDING_CLAUDE_REVIEW：1.3.0 複查通過前只能 --preview")
    if "--probe-taiwan" in sys.argv:
        ticker = sys.argv[sys.argv.index("--probe-taiwan") + 1]
        out(json.dumps(probe_taiwan(ticker), ensure_ascii=False, indent=2))
        return
    wanted = None
    if "--tickers" in sys.argv:
        wanted = {x.strip().upper() for x in sys.argv[sys.argv.index("--tickers") + 1].split(",") if x.strip()}
    ingested = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    companyfacts_dir = sys.argv[sys.argv.index("--companyfacts-dir") + 1] if "--companyfacts-dir" in sys.argv else None
    results, errors = [], []
    for issuer_id, cfg in tags["sec_issuers"].items():
        if wanted and cfg["ticker"] not in wanted:
            continue
        try:
            results.append(fetch_sec(issuer_id, cfg, ingested, companyfacts_dir, tags.get("spec_version")))
        except Exception as exc:
            errors.append({"issuer_id": issuer_id, "ticker": cfg["ticker"], "error": f"{type(exc).__name__}: {exc}",
                           "hint": SSL_HINT if "CERTIFICATE_VERIFY_FAILED" in str(exc) else ""})
    include_pending = "--include-pending-zero-assertions" in sys.argv
    zero_rows, zero_registry = zero_assertion_observations(zero_data, ingested, include_pending=include_pending and not write_mode)
    approved_zero_rows, _ = zero_assertion_observations(zero_data, ingested, include_pending=False)
    official_rows = [row for result in results for row in result["observations"]]
    written = append_financial_observations(official_rows + approved_zero_rows,
                                             lock_holder=lock_holder) if write_mode else 0
    summary = {"mode": "WRITE" if write_mode else "PREVIEW_ONLY", "protocol_version": rules["protocol_version"], "tls": CTX_NAME,
               "input_mode": "OFFICIAL_SEC_SNAPSHOT" if companyfacts_dir else "LIVE_SEC_API",
               "results": [{k: v for k, v in r.items() if k != "observations"} for r in results],
               "errors": errors, "canonical_rows_written": written,
               "zero_assertions": zero_registry,
               "zero_observations_in_preview": len(zero_rows) if not write_mode else 0,
               "approved_zero_observations_written_or_existing": len(approved_zero_rows) if write_mode else 0,
               "taiwan_status": tags["taiwan"]["status"]}
    out(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
