# -*- coding: utf-8 -*-
"""公開資訊觀測站 iXBRL 財報轉接器（1.8.0；1.8.1 修補 C180-02、C180-04、C180-05；1.8.2 修補 C181-01；1.8.3 修補 C182-01；1.8.7 新增受控 FY 候選列）。

用法：
  python 03_工具/fetch_mops_financials.py --all --dry-run
  python 03_工具/fetch_mops_financials.py --all --write
  python 03_工具/fetch_mops_financials.py --ticker 2330 --year 2026 --season 2 --dry-run
  python 03_工具/fetch_mops_financials.py --from-dir 01_共用資料/data_spine/raw/mops --dry-run

只接受規格白名單中 namespace URI + local name 的精確配對。缺值不當零。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

from common import ROOT, file_lock, load_rules, out
from data_spine import OBSERVATIONS_PATH, read_jsonl, validate_observation
from fetch_financials import append_financial_observations
from fetch_prices import CTX

TAG_MAP_PATH = os.path.join(ROOT, "00_規範", "financial_tag_map.json")
RAW_ROOT = os.path.join(ROOT, "01_共用資料", "data_spine", "raw", "mops")
LIVE_FETCH_DISABLED_REASON = "MOPS_AUTOMATED_FETCH_DISABLED_ROBOTS_DISALLOW"
MANIFEST_PATH = os.path.join(RAW_ROOT, "manifest.jsonl")
LIQUIDITY_PATH = os.path.join(ROOT, "01_共用資料", "data_spine", "mops_liquidity_classifications.jsonl")
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ISSUERS = {
    "2330": ("ISSUER_TSMC", "sii", "CONSOLIDATED"),
    "2344": ("ISSUER_WINBOND", "sii", "CONSOLIDATED"),
    "2408": ("ISSUER_NANYA", "sii", "CONSOLIDATED"),
    "3017": ("ISSUER_AVC", "sii", "CONSOLIDATED"),
    "3081": ("ISSUER_LANDMARK", "otc", "INDIVIDUAL"),
    "3324": ("ISSUER_AURAS", "otc", "CONSOLIDATED"),
    "6274": ("ISSUER_TUC", "otc", "CONSOLIDATED"),
}

INSTANT_METRICS = {
    "FIN_CASH", "FIN_DEBT_CURRENT", "FIN_DEBT_NONCURRENT",
    "FIN_CURRENT_ASSETS", "FIN_CURRENT_LIABILITIES",
}
DIRECT_QUARTER_METRICS = {
    "FIN_REVENUE", "FIN_GROSS_PROFIT", "FIN_OPERATING_INCOME", "FIN_INTEREST_EXPENSE",
}
CUMULATIVE_METRICS = {"FIN_CFO", "FIN_CAPEX"}
DEBT_METRICS = {"FIN_DEBT_CURRENT", "FIN_DEBT_NONCURRENT"}
ABSENT_LIMITATION = "ABSENT_COMPONENTS_TREATED_AS_NONE"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _local(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _ns_map(data):
    found = {}
    for _, item in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        prefix, uri = item
        found[prefix or ""] = uri
    return found


def _parse_number(node):
    if node.attrib.get("{%s}nil" % XSI_NS, "").lower() == "true":
        return None
    raw = "".join(node.itertext()).strip().replace("\u00a0", "").replace(",", "")
    raw = re.sub(r"\s+", "", raw)
    if raw in {"", "-", "--", "—"}:
        return None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        value = float(raw)
    except ValueError:
        return None
    scale = int(node.attrib.get("scale", "0") or 0)
    value *= 10 ** scale
    if node.attrib.get("sign") == "-" or negative_parentheses:
        value = -abs(value)
    return value


def validate_raw(data, ticker, year, season, expected_scope):
    errors = []
    if not data.lstrip().startswith(b"<?xml"):
        errors.append("NO_XML_DECLARATION")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return [f"XML_PARSE_ERROR:{exc}"]
    if _local(root.tag).lower() != "html":
        errors.append("ROOT_NOT_XHTML_HTML")
    facts = root.findall(".//{%s}nonFraction" % IX_NS)
    if not facts:
        errors.append("NO_IX_NONFRACTION")
    text = data.decode("utf-8", errors="ignore")
    if ticker not in text:
        errors.append("ISSUER_MISMATCH")
    if f"{year}Q{season}" not in text and str(year) not in text:
        errors.append("PERIOD_MISMATCH")
    expected_token = "ci-cr" if expected_scope == "CONSOLIDATED" else "ci-ir"
    other_token = "ci-ir" if expected_scope == "CONSOLIDATED" else "ci-cr"
    schema_hrefs = re.findall(r"schemaRef\b[^>]*\b(?:xlink:)?href=[\"']([^\"']+)[\"']", text, flags=re.I)
    scope_hrefs = [href for href in schema_hrefs if "ci-cr" in href or "ci-ir" in href]
    if not any(expected_token in href for href in scope_hrefs) or any(other_token in href for href in scope_hrefs):
        errors.append("REPORT_SCOPE_MISMATCH")
    return errors


def confirmed_no_filing_response(data):
    """只把 MOPS 明確的「檔名或路徑不正確」頁視為該報表不存在。

    限流、維護、登入或其他錯誤頁都不得建立 NOT_FOUND 證據，也不得成為
    FIRST_SEEN_CONSERVATIVE 的前一筆成功輪詢。
    """
    for encoding in ("utf-8", "big5"):
        text = data.decode(encoding, errors="ignore")
        if "下載檔名或路徑不正確" in text:
            return True
    return False


def contexts_from_root(root):
    contexts = {}
    for node in root.findall(".//{%s}context" % XBRLI_NS):
        cid = node.attrib.get("id")
        start = node.findtext(".//{%s}startDate" % XBRLI_NS)
        end = node.findtext(".//{%s}endDate" % XBRLI_NS)
        instant = node.findtext(".//{%s}instant" % XBRLI_NS)
        dims = [x for x in node.iter() if _local(x.tag) in {"explicitMember", "typedMember"}]
        identifier = node.findtext(".//{%s}identifier" % XBRLI_NS)
        contexts[cid] = {"start": start, "end": end or instant, "instant": bool(instant),
                         "has_dimensions": bool(dims), "identifier": identifier}
    return contexts


def _tag_key(node, namespaces):
    qname = node.attrib.get("name", "")
    if ":" not in qname:
        return None
    prefix, local = qname.split(":", 1)
    uri = namespaces.get(prefix)
    return (uri, local) if uri else None


def _fact_component(fact, operator="+"):
    return {"namespace": fact["namespace"], "local_name": fact["local_name"],
            "context_start": fact.get("start"), "context_end": fact.get("end"),
            "value": fact["value"], "operator": operator}


def parse_facts(data):
    root = ET.fromstring(data)
    namespaces = _ns_map(data)
    contexts = contexts_from_root(root)
    facts = defaultdict(list)
    for node in root.findall(".//{%s}nonFraction" % IX_NS):
        key = _tag_key(node, namespaces)
        ctx = contexts.get(node.attrib.get("contextRef"))
        value = _parse_number(node)
        if not key or not ctx or ctx["has_dimensions"] or value is None:
            continue
        facts[key].append({"namespace": key[0], "local_name": key[1], "context_ref": node.attrib.get("contextRef"),
                           "start": ctx["start"], "end": ctx["end"], "instant": ctx["instant"],
                           "identifier": ctx["identifier"], "value": value,
                           "unit_ref": node.attrib.get("unitRef"), "decimals": node.attrib.get("decimals"),
                           "scale": node.attrib.get("scale", "0"), "sign": node.attrib.get("sign")})
    return facts, namespaces


def _quarter_start(year, season):
    return dt.date(year, 1 + (season - 1) * 3, 1)


def _date(value):
    return dt.date.fromisoformat(value) if value else None


def _quarter_end(year, season):
    return (dt.date(year, season * 3 + 1, 1) - dt.timedelta(days=1)) if season < 4 else dt.date(year, 12, 31)


def choose_fact(facts, allowlist, year, season, mode):
    """1.8.1（C180-04）：context 起訖日必須與曆年制季別精確相等，不容忍差幾天。"""
    candidates = []
    for cfg in allowlist:
        candidates.extend(facts.get((cfg["namespace"], cfg["local_name"]), []))
    period_end = _quarter_end(year, season)
    candidates = [f for f in candidates if f.get("end") and _date(f["end"]) == period_end]
    if mode == "instant":
        candidates = [f for f in candidates if f["instant"]]
    else:
        candidates = [f for f in candidates if not f["instant"] and f.get("start")]
        if mode == "quarter":
            qs = _quarter_start(year, season)
            candidates = [f for f in candidates if _date(f["start"]) == qs]
        elif mode == "ytd":
            candidates = [f for f in candidates if _date(f["start"]) == dt.date(year, 1, 1)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (f["end"], f.get("start") or "", f["local_name"]))[-1]


def _roc_timestamp(text):
    m = re.search(r"(\d{3})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})", text)
    if not m:
        return None
    year = int(m.group(1)) + 1911
    return f"{year:04d}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}+08:00"


def parse_doc_upload(data, ticker, year, season, scope):
    text = data.decode("big5", errors="ignore")
    suffix = "AI1" if scope == "CONSOLIDATED" else "AI2"
    filename = f"{year}{season:02d}_{ticker}_{suffix}.pdf"
    pos = text.find(filename)
    if pos < 0:
        return None
    return _roc_timestamp(text[pos:pos + 600])


class Fetcher:
    def __init__(self, min_interval=10.0, timeout=45, retries=3):
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.last_request = 0.0

    def request(self, url, data=None):
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "StockResearchMOPSAdapter/1.8.0"})
            try:
                self.last_request = time.monotonic()
                with urllib.request.urlopen(req, timeout=self.timeout, context=CTX) as resp:
                    body = resp.read()
                    return body, resp.status, resp.headers.get_content_type()
            except Exception:
                if attempt + 1 >= self.retries:
                    raise
                time.sleep(min(40, 5 * (2 ** attempt)))


def download_url(ticker, year, season, scope):
    rid = "C" if scope == "CONSOLIDATED" else "A"
    params = urllib.parse.urlencode({"functionName": "t164sb01", "step": 9, "co_id": ticker,
                                     "year": year, "season": season, "report_id": rid})
    return "https://mopsov.twse.com.tw/server-java/FileDownLoad?" + params


def doc_url(ticker, year):
    roc = year - 1911
    return ("https://doc.twse.com.tw/server-java/t57sb01?" +
            urllib.parse.urlencode({"step": 1, "colorchg": 1, "co_id": ticker,
                                    "year": roc, "seamon": "", "mtype": "A"}))


def correction_url_and_body(market, year, season):
    body = urllib.parse.urlencode({"step": 1, "firstin": "ture", "off": 1, "isQuery": "Y",
                                  "TYPEK": market, "year": year - 1911, "season": f"{season:02d}"}).encode()
    return "https://mopsov.twse.com.tw/mops/web/ajax_t56sb31_q1", body


def correction_info(data, ticker):
    # MOPS 有頁面宣告 Big5 但實際回傳 UTF-8 的情況；以解碼替代字元數判斷，平手優先 UTF-8。
    candidates = [data.decode("utf-8", errors="replace"), data.decode("big5", errors="replace")]
    text = min(enumerate(candidates), key=lambda item: (item[1].count("\ufffd"), item[0]))[1]
    pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
    for row in pat.findall(text):
        clean = re.sub(r"<[^>]+>", " ", row)
        clean = re.sub(r"\s+", " ", clean).strip()
        if re.search(rf"\b{re.escape(ticker)}\b", clean):
            date_match = re.search(r"20\d{6}", clean)
            return {"listed": True, "date": date_match.group(0) if date_match else None, "description": clean}
    return {"listed": False, "date": None, "description": None}


def load_manifest(path=MANIFEST_PATH):
    return read_jsonl(path)


def append_manifest(row, path=MANIFEST_PATH, lock_holder=None):
    import lock as project_lock
    from staging_write import append_bytes, validate_jsonl_bytes
    project_lock.require_holder_for_path(lock_holder, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = read_jsonl(path)
    row = dict(row)
    row["revision_sequence"] = max([int(x.get("revision_sequence", 0)) for x in existing] or [0]) + 1
    identity = canonical_json({k: row.get(k) for k in ("revision_sequence", "ticker", "fiscal_year", "fiscal_period", "raw_sha256")})
    row["manifest_record_id"] = "MOPS_MANIFEST_" + hashlib.sha256(identity.encode()).hexdigest()
    payload = (canonical_json(row) + "\n").encode("utf-8")
    append_bytes(path, payload, writer="fetch_mops_financials.append_manifest",
                 validator=validate_jsonl_bytes, lock_holder=lock_holder,
                 extra={"manifest_record_id": row["manifest_record_id"], "ticker": row.get("ticker")})
    return row, True


def _availability(manifest):
    if manifest.get("correction_listed"):
        if manifest.get("first_seen_at"):
            stamp = manifest["first_seen_at"]
            return {"source_published_at": None, "available_at": stamp,
                    "availability_status": "POINT_IN_TIME_KNOWN", "availability_basis": "FIRST_SEEN_CONSERVATIVE",
                    "time_precision": "EXACT_TIMESTAMP", "first_seen_at": stamp}
        return {"source_published_at": None, "available_at": None,
                "availability_status": "BACKFILLED_UNKNOWN", "availability_basis": "UNKNOWN_BACKFILL",
                "time_precision": "UNKNOWN", "first_seen_at": None}
    if manifest.get("doc_upload_at"):
        stamp = manifest["doc_upload_at"]
        return {"source_published_at": stamp, "available_at": stamp,
                "availability_status": "POINT_IN_TIME_KNOWN", "availability_basis": "OFFICIAL_TIMESTAMP",
                "time_precision": "EXACT_TIMESTAMP", "first_seen_at": None}
    if manifest.get("first_seen_at"):
        stamp = manifest["first_seen_at"]
        return {"source_published_at": None, "available_at": stamp,
                "availability_status": "POINT_IN_TIME_KNOWN", "availability_basis": "FIRST_SEEN_CONSERVATIVE",
                "time_precision": "EXACT_TIMESTAMP", "first_seen_at": stamp}
    return {"source_published_at": None, "available_at": None,
            "availability_status": "BACKFILLED_UNKNOWN", "availability_basis": "UNKNOWN_BACKFILL",
            "time_precision": "UNKNOWN", "first_seen_at": None}


def _statutory_deadline_v1_availability(filing):
    """G-S3-9／S3g：供所有年份的 V1 版本列使用的保守代理時點。

    這不是實際公布時間，因此 source_published_at 保持空白。有官方時間戳或 first-seen
    時點時仍由 _availability 優先。更正清單期別必須再受更正日期約束；更正日期未知時
    不得用於 V1 歷史月份。
    """
    manifest = filing["manifest"]
    current = _availability(manifest)
    if current["availability_basis"] != "UNKNOWN_BACKFILL":
        return current
    year, season = int(filing["year"]), int(filing["season"])
    if season == 4:
        deadline = dt.date(year + 1, 3, 31)
    else:
        quarter_end = {1: dt.date(year, 3, 31), 2: dt.date(year, 6, 30),
                       3: dt.date(year, 9, 30)}[season]
        deadline = quarter_end + dt.timedelta(days=45)
    if manifest.get("correction_listed"):
        raw_date = str(manifest.get("correction_date") or "")
        if not re.fullmatch(r"20\d{6}", raw_date):
            return {"source_published_at": None, "available_at": None,
                    "availability_status": "BACKFILLED_UNKNOWN",
                    "availability_basis": "V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN",
                    "time_precision": "UNKNOWN", "first_seen_at": None}
        correction_date = dt.datetime.strptime(raw_date, "%Y%m%d").date()
        deadline = max(deadline, correction_date)
        basis = "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE"
    else:
        basis = "STATUTORY_DEADLINE_CONSERVATIVE"
    # 日期型規則取台北日終；假日不前移或順延。
    available = deadline.isoformat() + "T23:59:59+08:00"
    return {"source_published_at": None, "available_at": available,
            "availability_status": "POINT_IN_TIME_KNOWN",
            "availability_basis": basis,
            "time_precision": "DATE_ONLY", "first_seen_at": None}


def previous_poll_proves_change(old_manifests, new_hash):
    return any((m.get("poll_result") == "NOT_FOUND" and
                m.get("absence_basis") == "OFFICIAL_NO_FILE_MESSAGE") or
               (m.get("poll_result") == "FOUND" and m.get("raw_sha256") != new_hash)
               for m in old_manifests)


def _obs_id(payload):
    keys = {k: payload.get(k) for k in (
        "issuer_id", "metric_id", "period_start", "period_end", "period_type", "value", "unit",
        "accession_number", "raw_sha256", "mapping_version", "report_scope", "components_sha256")}
    return "FIN_" + hashlib.sha256(canonical_json(keys).encode()).hexdigest()


def components_digest(components, absent_components=None, overlap=None):
    """組成雜湊。

    - 一般推導：只對 components 雜湊。
    - 1.8.1 負債格式：components＋absent_components。
    - 1.8.2 負債格式（overlap 不是 None）：再加 covered_by_aggregate、excluded_overlapping_components
      與 overlap_rules_version，三類清單都參與雜湊。
    - 1.8.3（C182-01）：aggregate_not_reported_details_used 只在非空時出現，出現時一併參與雜湊；
      空的時候不放進雜湊，所以既有 1.8.2 列的 ID 不變。
    """
    if overlap is not None:
        payload = {"components": components, "absent_components": absent_components,
                   "covered_by_aggregate": overlap["covered_by_aggregate"],
                   "excluded_overlapping_components": overlap["excluded_overlapping_components"],
                   "overlap_rules_version": overlap["overlap_rules_version"]}
        if "aggregate_not_reported_details_used" in overlap:
            payload["aggregate_not_reported_details_used"] = overlap["aggregate_not_reported_details_used"]
    elif absent_components is not None:
        payload = {"components": components, "absent_components": absent_components}
    else:
        payload = components
    return sha256_bytes(canonical_json(payload).encode())


def _base_observation(filing, metric, fact, value, period_type, derivation, components=None, absent_components=None,
                      overlap=None, unit="TWD"):
    manifest = filing["manifest"]
    scope_letter = "C" if filing["report_scope"] == "CONSOLIDATED" else "A"
    row = {
        "issuer_id": filing["issuer_id"], "security_id": None, "metric_id": metric,
        "value": value, "unit": unit, "period_start": fact.get("start"), "period_end": fact.get("end"),
        "period_type": period_type, "fiscal_year": filing["year"], "fiscal_period": f"Q{filing['season']}",
        "taxonomy": fact.get("namespace") or "COMPOSITE", "source_tag": fact.get("local_name") or derivation,
        "source_system": "MOPS_XBRL", "source_url": filing["url"],
        "accession_number": f"MOPS:{filing['ticker']}:{filing['year']}Q{filing['season']}:{scope_letter}:{filing['raw_sha256']}",
        "ingested_at": manifest["fetched_at"], "derivation": derivation,
        "raw_sha256": filing["raw_sha256"], "manifest_record_id": manifest["manifest_record_id"],
        "manifest_revision_sequence": manifest["revision_sequence"],
        "mapping_version": filing["mapping_version"], "report_scope": filing["report_scope"],
        "supersedes_observation_id": None,
    }
    row.update(_availability(manifest))
    if filing["report_scope"] == "INDIVIDUAL":
        row["limitations"] = ["INDIVIDUAL_STATEMENTS_ONLY"]
    if components:
        row["components"] = components
        row["components_sha256"] = components_digest(components, absent_components, overlap)
    if overlap is not None:
        row.update(overlap)
    if absent_components is not None:
        row["absent_components"] = absent_components
        if absent_components:
            row["limitations"] = list(row.get("limitations", [])) + [ABSENT_LIMITATION]
    row["observation_id"] = _obs_id(row)
    return row


def _mapping_for(tag_map, issuer_id, metric):
    issuer = tag_map["taiwan"]["issuers"][issuer_id]
    profile = tag_map["taiwan"]["profiles"][issuer["metrics_profile"]]
    return (issuer.get("metrics") or {}).get(metric, profile["metrics"].get(metric, []))


OVERLAP_PRECEDENCES = {"DETAILS_WHEN_ANY_PRESENT", "AGGREGATE_WHEN_PRESENT"}


def _overlap_config(tag_map, issuer_id, metric):
    """彙總／細項涵蓋關係只從對照表讀，不寫死在程式；負債指標沒有設定就報錯。"""
    issuer = tag_map["taiwan"]["issuers"][issuer_id]
    profile = tag_map["taiwan"]["profiles"][issuer["metrics_profile"]]
    version = profile.get("overlap_rules_version")
    rules = (profile.get("overlap_rules") or {}).get(metric)
    if not version or rules is None:
        raise ValueError(f"OVERLAP_RULES_MISSING:{issuer['metrics_profile']}:{metric}")
    for rule in rules:
        if rule.get("precedence") not in OVERLAP_PRECEDENCES or not rule.get("aggregate") or not rule.get("covers"):
            raise ValueError(f"OVERLAP_RULE_INVALID:{metric}:{rule}")
    return version, rules


def apply_overlap_rules(components, absent, rules):
    """1.8.2（C181-01）：把白名單科目分成實際相加、真正缺席、被已用彙總涵蓋、為避免重複而排除四類。

    components：有申報的事實（依白名單順序）；absent：沒有申報的科目。
    回傳 (used, absent, covered_by_aggregate, excluded_overlapping_components, aggregate_not_reported_details_used)。
    1.8.3（C182-01）：彙總沒有申報、但同群組至少一個細項已被採用時，彙總不算缺席，
    改列 aggregate_not_reported_details_used（記下採用了哪些細項）。
    """
    present = {x["local_name"]: x for x in components}
    absent_by_name = {a["local_name"]: a for a in absent}
    dropped, covered, excluded, represented = set(), [], [], []
    for rule in rules:
        aggregate, covers = rule["aggregate"], list(rule["covers"])
        details_present = [c for c in covers if c in present]
        if aggregate not in present:
            # 彙總沒有申報：細項照常相加；缺席的細項仍是真正缺席。
            if details_present and aggregate in absent_by_name:
                represented.append(dict(absent_by_name.pop(aggregate), details_used=details_present))
            continue
        if rule["precedence"] == "DETAILS_WHEN_ANY_PRESENT" and details_present:
            dropped.add(aggregate)
            excluded.append(dict(_fact_component(present[aggregate]), operator=None,
                                 excluded_because="DETAILS_PRESENT", overlaps_with=details_present))
            continue
        # 彙總被採用：有申報的細項為重疊排除，沒申報的細項由彙總涵蓋。
        for c in details_present:
            dropped.add(c)
            excluded.append(dict(_fact_component(present[c]), operator=None,
                                 excluded_because="AGGREGATE_PRESENT", overlaps_with=[aggregate]))
        for c in covers:
            if c in absent_by_name:
                covered.append(dict(absent_by_name.pop(c), aggregate_local_name=aggregate))
    used = [x for x in components if x["local_name"] not in dropped]
    remaining = [a for a in absent if a["local_name"] in absent_by_name]
    return used, remaining, covered, excluded, represented


def build_filing_rows(filing, tag_map):
    facts = filing["facts"]
    rows = []
    for metric in sorted(DIRECT_QUARTER_METRICS):
        mapping = _mapping_for(tag_map, filing["issuer_id"], metric)
        fact = choose_fact(facts, mapping, filing["year"], filing["season"], "quarter")
        if fact:
            rows.append(_base_observation(filing, metric, fact, fact["value"], "Q", "REPORTED_QUARTER"))
        ytd = choose_fact(facts, mapping, filing["year"], filing["season"], "ytd")
        if ytd:
            filing.setdefault("cumulative_direct", {})[metric] = (ytd, ytd["value"])
    current_liabilities = choose_fact(facts, _mapping_for(tag_map, filing["issuer_id"], "FIN_CURRENT_LIABILITIES"),
                                      filing["year"], filing["season"], "instant")
    for metric in sorted(INSTANT_METRICS):
        mappings = _mapping_for(tag_map, filing["issuer_id"], metric)
        # 同一科目的不同分類標準版本視為同一個組成，最多取一筆，避免跨版本重複相加。
        groups = {}
        for cfg in mappings:
            groups.setdefault(cfg["local_name"], []).append(cfg)
        components, absent = [], []
        for local_name, cfgs in groups.items():
            fact = choose_fact(facts, cfgs, filing["year"], filing["season"], "instant")
            if fact:
                components.append(fact)
            else:
                absent.append({"local_name": local_name,
                               "namespaces_checked": sorted({c["namespace"] for c in cfgs})})
        # 1.8.1（C180-05）：時點科目不取 abs()；負值直接報錯、不產生，不默默轉正。
        for x in components:
            if x["value"] < 0:
                raise ValueError(f"NEGATIVE_INSTANT_VALUE:{filing['ticker']} {filing['year']}Q{filing['season']} "
                                 f"{metric} {x['namespace']}:{x['local_name']}={x['value']}")
        if not components:
            continue
        if metric in DEBT_METRICS:
            version, overlap_rules = _overlap_config(tag_map, filing["issuer_id"], metric)
            components, absent, covered, excluded, represented = apply_overlap_rules(components, absent, overlap_rules)
            # 1.8.1（C180-02，使用者 2026-10-08 選乙）：至少一個組成存在、且同期有流動負債合計，
            # 缺席的白名單細項才視為無此項目；1.8.2 起只有未被已用彙總涵蓋的才算缺席。
            if not current_liabilities:
                continue
            comp = [_fact_component(x) for x in components]
            overlap = {"covered_by_aggregate": covered, "excluded_overlapping_components": excluded,
                       "overlap_rules_version": version}
            if represented:
                overlap["aggregate_not_reported_details_used"] = represented
            rows.append(_base_observation(filing, metric, dict(components[0]), sum(x["value"] for x in components),
                                          "INSTANT", "COMPOSITE_SUM", comp, absent_components=absent, overlap=overlap))
        elif len(components) == 1:
            fact = components[0]
            rows.append(_base_observation(filing, metric, fact, fact["value"], "INSTANT", "REPORTED_INSTANT"))
        else:
            fact = dict(components[0])
            value = sum(x["value"] for x in components)
            comp = [_fact_component(x) for x in components]
            rows.append(_base_observation(filing, metric, fact, value, "INSTANT", "COMPOSITE_SUM", comp))
    for metric in sorted(CUMULATIVE_METRICS):
        fact = choose_fact(facts, _mapping_for(tag_map, filing["issuer_id"], metric), filing["year"], filing["season"], "ytd")
        if fact:
            value = abs(fact["value"]) if metric == "FIN_CAPEX" else fact["value"]
            filing.setdefault("cumulative", {})[metric] = (fact, value)
    dep = choose_fact(facts, _mapping_for(tag_map, filing["issuer_id"], "FIN_DEPRECIATION"), filing["year"], filing["season"], "ytd")
    amort = choose_fact(facts, _mapping_for(tag_map, filing["issuer_id"], "FIN_AMORTIZATION"), filing["year"], filing["season"], "ytd")
    if dep and amort:
        filing.setdefault("cumulative", {})["FIN_DEPRECIATION_AMORTIZATION"] = (
            dict(dep, namespace="COMPOSITE", local_name="DepreciationPlusAmortization"),
            abs(dep["value"]) + abs(amort["value"]), [_fact_component(dep), _fact_component(amort)])
    return rows


def _exact_ifrs_fact(filing, local_name, mode):
    """S3c-2：只接受官方 IFRS／TIFRS namespace 的指定 local name。"""
    allow = [{"namespace": ns, "local_name": local}
             for ns, local in filing["facts"]
             if local == local_name and ("xbrl.ifrs.org/taxonomy/" in ns or "xbrl.org/tifrs/bsci/" in ns)]
    return choose_fact(filing["facts"], allow, filing["year"], filing["season"], mode) if allow else None


def build_valuation_version_rows(filing):
    """建立 PE／PB 所需的版本保留原始值；不反推、不把缺值當零。"""
    individual = filing["report_scope"] == "INDIVIDUAL"
    ni_name = "ProfitLoss" if individual else "ProfitLossAttributableToOwnersOfParent"
    equity_name = "Equity" if individual else "EquityAttributableToOwnersOfParent"
    specs = (("VER_NET_INCOME_PARENT", ni_name, "ytd", "TWD", "DURATION"),
             ("VER_EQUITY_PARENT", equity_name, "instant", "TWD", "INSTANT"),
             ("VER_ISSUED_CAPITAL", "IssuedCapital", "instant", "TWD", "INSTANT"),
             ("VER_TREASURY_SHARES", "NumberOfSharesInEntityHeldByEntityAndByItsSubsidiaries", "instant", "shares", "INSTANT"),
             # G-S3-10：台股 PE 直接使用官方每股盈餘，不再以缺漏的稀釋股數反推。
             ("VER_DILUTED_EPS_Q", "DilutedEarningsLossPerShare", "quarter", "TWD/share", "Q"),
             ("VER_DILUTED_EPS_YTD", "DilutedEarningsLossPerShare", "ytd", "TWD/share", "YTD"))
    rows = []
    for metric, local_name, mode, unit, period_type in specs:
        fact = _exact_ifrs_fact(filing, local_name, mode)
        if fact is None:
            continue
        row = _base_observation(filing, metric, fact, fact["value"], period_type,
                                "REPORTED_VERSIONED_FACT", unit=unit)
        availability = _statutory_deadline_v1_availability(filing)
        for key in ("source_published_at", "available_at", "availability_status",
                    "availability_basis", "time_precision", "first_seen_at"):
            row[key] = availability[key]
        # availability 不在 observation_id 的一般鍵集合中；範圍更正若沿用原 mapping_version，
        # append-only writer 會把同一原始事實誤判為已存在，無法留下新版本。只對採法定期限的
        # V1 列綁定政策版號，使新列可追溯地 supersede 舊 UNKNOWN_BACKFILL／舊政策列；
        # 一般財務觀測值及具有官方／first-seen 時點的 V1 列不變。
        if availability["availability_basis"] in {
                "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE",
                "V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN"}:
            row["mapping_version"] = f"{filing['mapping_version']}+G-S3-9-v3"
            row["observation_id"] = _obs_id(row)
        elif availability["availability_basis"] == "STATUTORY_DEADLINE_CONSERVATIVE":
            row["mapping_version"] = f"{filing['mapping_version']}+G-S3-9-v2"
            row["observation_id"] = _obs_id(row)
        rows.append(row)
    return rows


def build_general_correction_timing_versions(filings, rows):
    """G-S3-9b：由既存官方更正證據重建一般 FIN_* 的保守時點版本。

    一般財務列原本若為 UNKNOWN_BACKFILL，不可直接供 PIT 使用。當 manifest 同時
    保存官方更正日期與更正頁雜湊時，建立新的 append-only 候選版本；舊列不改寫，
    是否正式寫入仍由受控寫入流程決定。這個函式刻意放在離線重算路徑中，確保正式
    active 列仍能由原始 iXBRL 與 manifest 完整重播，而不是只靠手工複製。
    """
    filing_by_key = {
        (f["issuer_id"], int(f["year"]), int(f["season"]), f["report_scope"], f["raw_sha256"]): f
        for f in filings
    }
    result = []
    for row in rows:
        metric = str(row.get("metric_id") or "")
        if (row.get("source_system") != "MOPS_XBRL" or not metric.startswith("FIN_") or
                row.get("availability_basis") != "UNKNOWN_BACKFILL"):
            continue
        period = str(row.get("fiscal_period") or "")
        if not re.fullmatch(r"Q[1-4]", period):
            continue
        key = (row.get("issuer_id"), int(row.get("fiscal_year")), int(period[1:]),
               row.get("report_scope"), row.get("raw_sha256"))
        filing = filing_by_key.get(key)
        if filing is None:
            continue
        manifest = filing["manifest"]
        raw_date = str(manifest.get("correction_date") or "")
        page_sha = str(manifest.get("correction_page_sha256") or "")
        manifest_id = str(row.get("manifest_record_id") or "")
        if (not manifest.get("correction_listed") or not re.fullmatch(r"20\d{6}", raw_date) or
                not re.fullmatch(r"[0-9a-f]{64}", page_sha) or not manifest_id):
            continue
        year, season = int(filing["year"]), int(filing["season"])
        if season == 4:
            statutory = dt.date(year + 1, 3, 31)
        else:
            quarter_end = {1: dt.date(year, 3, 31), 2: dt.date(year, 6, 30),
                           3: dt.date(year, 9, 30)}[season]
            statutory = quarter_end + dt.timedelta(days=45)
        correction = dt.datetime.strptime(raw_date, "%Y%m%d").date()
        available_date = max(statutory, correction)
        candidate = dict(row)
        candidate.update({
            "availability_basis": "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE",
            "availability_status": "POINT_IN_TIME_KNOWN",
            "available_at": available_date.isoformat() + "T23:59:59+08:00",
            "source_published_at": None,
            "first_seen_at": None,
            "time_precision": "DATE_ONLY",
            "correction_date": raw_date,
            "correction_page_sha256": page_sha,
            "mapping_version": f"{filing['mapping_version']}+G-S3-9b-v1",
            "provenance_note": ("G-S3-9b offline replay from existing official corrected MOPS iXBRL; "
                                "available_at=max(statutory deadline, official correction date)."),
            "supersedes_observation_id": None,
        })
        candidate["observation_id"] = _obs_id(candidate)
        result.append(candidate)
    return result


def quarterize(filings, direct_rows):
    rows = list(direct_rows)
    by_key = {(f["issuer_id"], f["year"], f["season"]): f for f in filings}
    for filing in sorted(filings, key=lambda x: (x["issuer_id"], x["year"], x["season"])):
        fy_allowed = False
        if filing["season"] == 4:
            year_filings = [by_key.get((filing["issuer_id"], filing["year"], season)) for season in range(1, 5)]
            if any(item is None for item in year_filings):
                filing.setdefault("fy_exclusion_reasons", []).append("FY_REQUIRES_Q1_TO_Q4_FILINGS")
            elif any(item["report_scope"] != filing["report_scope"] for item in year_filings):
                filing.setdefault("fy_exclusion_reasons", []).append("FY_REPORT_SCOPE_MISMATCH_WITHIN_YEAR")
            else:
                fy_allowed = True
        for metric, item in filing.get("cumulative", {}).items():
            fact, cumulative = item[0], item[1]
            own_components = item[2] if len(item) > 2 else [_fact_component(dict(fact, value=cumulative))]
            if filing["season"] == 4 and fy_allowed:
                fyfact = dict(fact, start=dt.date(filing["year"], 1, 1).isoformat())
                rows.append(_base_observation(filing, metric, fyfact, cumulative, "FY",
                                              "REPORTED_FY_FROM_Q4_ANNUAL_REPORT"))
            if filing["season"] == 1:
                value = cumulative
                comps = own_components
                derivation = "DERIVED_Q1_FROM_YTD"
            else:
                prev = by_key.get((filing["issuer_id"], filing["year"], filing["season"] - 1))
                prev_item = prev.get("cumulative", {}).get(metric) if prev else None
                if not prev_item or prev["report_scope"] != filing["report_scope"]:
                    continue
                previous = prev_item[1]
                value = cumulative - previous
                prev_fact = prev_item[0]
                if len(prev_item) > 2:
                    previous_components = [dict(x, operator="-") for x in prev_item[2]]
                else:
                    previous_components = [_fact_component(dict(prev_fact, value=previous), "-")]
                comps = own_components + previous_components
                derivation = "DERIVED_QUARTER_FROM_CUMULATIVE"
            qfact = dict(fact, start=_quarter_start(filing["year"], filing["season"]).isoformat())
            rows.append(_base_observation(filing, metric, qfact, value, "Q", derivation, comps))
        if filing["season"] == 4:
            previous = by_key.get((filing["issuer_id"], filing["year"], 3))
            for metric, item in filing.get("cumulative_direct", {}).items():
                fact, annual = item
                if fy_allowed:
                    fyfact = dict(fact, start=dt.date(filing["year"], 1, 1).isoformat())
                    rows.append(_base_observation(
                        filing, metric, fyfact, annual, "FY", "REPORTED_FY_FROM_Q4_ANNUAL_REPORT"))
                if any(r["issuer_id"] == filing["issuer_id"] and r["metric_id"] == metric and
                       r["fiscal_year"] == filing["year"] and r["fiscal_period"] == "Q4" and
                       r["period_type"] == "Q" for r in rows):
                    continue
                prev_item = previous.get("cumulative_direct", {}).get(metric) if previous else None
                if not prev_item or previous["report_scope"] != filing["report_scope"]:
                    continue
                prev_fact, ytd3 = prev_item
                comps = [_fact_component(dict(fact, value=annual)), _fact_component(dict(prev_fact, value=ytd3), "-")]
                qfact = dict(fact, start=dt.date(filing["year"], 10, 1).isoformat())
                rows.append(_base_observation(filing, metric, qfact, annual - ytd3, "Q",
                                              "DERIVED_Q4_FROM_FY_MINUS_Q3", comps))
    return rows


def validate_liquidity_records(path=LIQUIDITY_PATH):
    errors = []
    for i, row in enumerate(read_jsonl(path), 1):
        kind = row.get("record_type")
        if kind == "CLASSIFICATION":
            required = ("classification_id", "issuer_id", "fiscal_year", "fiscal_period", "period_end",
                        "raw_sha256", "accession_number", "namespace", "local_name", "context", "value", "unit",
                        "instrument_type", "include_in_conservative_liquid_debt", "evidence_locator", "checked_by",
                        "checked_at", "reviewed_by", "reviewed_at", "review_status")
            for key in required:
                if key not in row or row[key] in (None, ""):
                    errors.append(f"line {i}: CLASSIFICATION 缺少 {key}")
            if row.get("checked_by") == row.get("reviewed_by"):
                errors.append(f"line {i}: 判讀者與複核者不得相同")
            if row.get("review_status") not in {"PENDING", "APPROVED_BY_INDEPENDENT_AI", "REJECTED"}:
                errors.append(f"line {i}: review_status 無效")
            if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("raw_sha256", ""))):
                errors.append(f"line {i}: raw_sha256 無效")
            if not isinstance(row.get("include_in_conservative_liquid_debt"), bool):
                errors.append(f"line {i}: include_in_conservative_liquid_debt 必須是 boolean")
            if not isinstance(row.get("value"), (int, float)) or isinstance(row.get("value"), bool):
                errors.append(f"line {i}: value 必須是數值")
        elif kind == "COVERAGE":
            for key in ("coverage_id", "issuer_id", "fiscal_year", "fiscal_period", "period_end", "raw_sha256",
                        "candidate_total", "classification_ids", "coverage_complete"):
                if key not in row:
                    errors.append(f"line {i}: COVERAGE 缺少 {key}")
            ids = row.get("classification_ids")
            if not isinstance(ids, list) or len(ids) != len(set(ids)):
                errors.append(f"line {i}: classification_ids 必須是無重複清單")
            if not isinstance(row.get("candidate_total"), int) or row.get("candidate_total", -1) < 0:
                errors.append(f"line {i}: candidate_total 必須是非負整數")
            elif isinstance(ids, list) and row.get("candidate_total") != len(ids):
                errors.append(f"line {i}: candidate_total 必須等於 classification_ids 筆數")
            if not isinstance(row.get("coverage_complete"), bool):
                errors.append(f"line {i}: coverage_complete 必須是 boolean")
            if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("raw_sha256", ""))):
                errors.append(f"line {i}: raw_sha256 無效")
        else:
            errors.append(f"line {i}: record_type 無效")
    return errors


def approved_short_term_observation(filing, rows):
    records = read_jsonl(LIQUIDITY_PATH)
    cover = [r for r in records if r.get("record_type") == "COVERAGE" and
             r.get("issuer_id") == filing["issuer_id"] and r.get("fiscal_year") == filing["year"] and
             r.get("fiscal_period") == f"Q{filing['season']}" and r.get("raw_sha256") == filing["raw_sha256"]]
    if not cover or not cover[-1].get("coverage_complete"):
        return None
    ids = set(cover[-1].get("classification_ids") or [])
    classes = {r.get("classification_id"): r for r in records if r.get("record_type") == "CLASSIFICATION" and r.get("classification_id") in ids}
    expected = (filing["issuer_id"], filing["year"], f"Q{filing['season']}", filing["raw_sha256"],
                cover[-1].get("period_end"))
    if (cover[-1].get("candidate_total") != len(ids) or len(classes) != len(ids) or
            any((r.get("issuer_id"), r.get("fiscal_year"), r.get("fiscal_period"), r.get("raw_sha256"),
                 r.get("period_end")) != expected or
                r.get("review_status") != "APPROVED_BY_INDEPENDENT_AI" or
                r.get("checked_by") == r.get("reviewed_by") for r in classes.values())):
        return None
    included = [r for r in classes.values() if r.get("include_in_conservative_liquid_debt") is True]
    value = sum(float(r["value"]) for r in included)
    cash = next((r for r in rows if r["issuer_id"] == filing["issuer_id"] and r["metric_id"] == "FIN_CASH" and
                 r["period_end"] == cover[-1]["period_end"]), None)
    if not cash:
        return None
    components = [{"classification_id": r["classification_id"], "value": r["value"], "operator": "+"} for r in included]
    if not components:
        return None  # 零值仍須另走雙 AI 查證，不在轉接器內自動建立。
    fact = {"namespace": "MANUAL_CLASSIFICATION", "local_name": "ConservativeLiquidDebtOnly",
            "start": None, "end": cover[-1]["period_end"]}
    return _base_observation(filing, "FIN_SHORT_TERM_INVESTMENTS", fact, value, "INSTANT", "COMPOSITE_LIQUIDITY_CLASSIFICATION", components)


def make_manifest(ticker, issuer_id, year, season, scope, url, data, fetched_at, content_type,
                  doc_upload_at, doc_sha, correction, first_seen_at=None):
    return {"ticker": ticker, "issuer_id": issuer_id, "fiscal_year": year, "fiscal_period": f"Q{season}",
            "report_scope": scope, "url": url, "fetched_at": fetched_at, "raw_sha256": sha256_bytes(data),
            "http_status": 200, "content_type": content_type, "byte_count": len(data), "poll_result": "FOUND",
            "doc_upload_at": doc_upload_at, "doc_page_sha256": doc_sha,
            "first_seen_at": first_seen_at,
            "correction_listed": correction["listed"], "correction_date": correction["date"],
            "correction_description": correction["description"],
            "correction_page_sha256": correction.get("page_sha256")}


def periods_latest_n(n=9, today=None):
    today = today or dt.date.today()
    candidates = []
    for year in range(today.year - 2, today.year + 1):
        for season, end in ((1, dt.date(year, 3, 31)), (2, dt.date(year, 6, 30)),
                            (3, dt.date(year, 9, 30)), (4, dt.date(year, 12, 31))):
            lag = 90 if season == 4 else 50
            if end + dt.timedelta(days=lag) <= today:
                candidates.append((end, year, season))
    if not candidates:
        raise ValueError("NO_EXPECTED_FILED_QUARTER")
    _, year, season = max(candidates)
    result = []
    for _ in range(n):
        result.append((year, season))
        season -= 1
        if season == 0:
            year -= 1; season = 4
    return list(reversed(result))


def _raw_path(ticker, year, season, scope, digest):
    letter = "C" if scope == "CONSOLIDATED" else "A"
    return os.path.join(RAW_ROOT, ticker, f"{year}Q{season}_{letter}_{digest[:16]}.xhtml")


def acquire_filings(tickers, periods, fetcher, tag_map, dry_run=True):
    filings = []
    doc_cache, correction_cache = {}, {}
    for ticker in tickers:
        issuer_id, market, configured_scope = ISSUERS[ticker]
        for year, season in periods:
            # R1：每季先嘗試合併；只有設定為 INDIVIDUAL_ONLY 且合併不存在才改 A。
            scopes = ["CONSOLIDATED"] + (["INDIVIDUAL"] if configured_scope == "INDIVIDUAL" else [])
            selected = None
            for scope in scopes:
                url = download_url(ticker, year, season, scope)
                data, status, ctype = fetcher.request(url)
                errors = validate_raw(data, ticker, year, season, scope)
                if not errors:
                    selected = (scope, url, data, status, ctype)
                    break
                confirmed_absent = confirmed_no_filing_response(data)
                missing_manifest = {"ticker": ticker, "issuer_id": issuer_id, "fiscal_year": year,
                                    "fiscal_period": f"Q{season}", "report_scope": scope, "url": url,
                                    "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                    "raw_sha256": sha256_bytes(data), "http_status": status,
                                    "content_type": ctype, "byte_count": len(data),
                                    "poll_result": "NOT_FOUND" if confirmed_absent else "ERROR",
                                    "absence_basis": "OFFICIAL_NO_FILE_MESSAGE" if confirmed_absent else None,
                                    "validation_errors": errors}
                append_manifest(missing_manifest, lock_holder=args.lock_holder)
                if not confirmed_absent:
                    raise ValueError(f"{ticker} {year}Q{season} POLL_ERROR:{'|'.join(errors)}")
                if scope == configured_scope or scope == "INDIVIDUAL":
                    raise ValueError(f"{ticker} {year}Q{season} RAW_INVALID:{'|'.join(errors)}")
            if not selected:
                raise ValueError(f"{ticker} {year}Q{season} NO_VALID_REPORT")
            scope, url, data, _, ctype = selected
            if configured_scope == "CONSOLIDATED" and scope != "CONSOLIDATED":
                raise ValueError(f"{ticker} {year}Q{season} CONSOLIDATED_REQUIRED")
            dkey = (ticker, year)
            if dkey not in doc_cache:
                doc_data, _, _ = fetcher.request(doc_url(ticker, year))
                doc_cache[dkey] = doc_data
            doc_data = doc_cache[dkey]
            upload = parse_doc_upload(doc_data, ticker, year, season, scope)
            ckey = (market, year, season)
            if ckey not in correction_cache:
                curl, body = correction_url_and_body(market, year, season)
                correction_cache[ckey] = fetcher.request(curl, body)[0]
            correction = correction_info(correction_cache[ckey], ticker)
            correction["page_sha256"] = sha256_bytes(correction_cache[ckey])
            now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            old = [m for m in load_manifest() if m.get("ticker") == ticker and
                   m.get("fiscal_year") == year and m.get("fiscal_period") == f"Q{season}" and
                   m.get("report_scope") == scope]
            new_hash = sha256_bytes(data)
            first_seen = now if previous_poll_proves_change(old, new_hash) else None
            manifest = make_manifest(ticker, issuer_id, year, season, scope, url, data, now, ctype,
                                     upload, sha256_bytes(doc_data), correction, first_seen)
            stored_manifest, _ = append_manifest(manifest, lock_holder=args.lock_holder)
            digest = manifest["raw_sha256"]
            path = _raw_path(ticker, year, season, scope, digest)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, "wb") as handle:
                    handle.write(data)
            facts, _ = parse_facts(data)
            filings.append({"ticker": ticker, "issuer_id": issuer_id, "year": year, "season": season,
                            "report_scope": scope, "url": url, "raw_sha256": digest, "manifest": stored_manifest,
                            "mapping_version": tag_map["spec_version"], "facts": facts, "raw_path": path})
    return filings


def filings_from_dir(path, tag_map):
    manifests = load_manifest()
    by_hash = {m.get("raw_sha256"): m for m in manifests}
    filings = []
    for base, _, names in os.walk(path):
        for name in names:
            m = re.match(r"(\d{4})Q([1-4])_([CA])_([0-9a-f]{16})\.xhtml$", name)
            if not m:
                continue
            ticker = os.path.basename(base)
            if ticker not in ISSUERS:
                continue
            full = os.path.join(base, name)
            with open(full, "rb") as handle: data = handle.read()
            digest = sha256_bytes(data)
            manifest = by_hash.get(digest)
            if not manifest:
                continue
            year, season = int(m.group(1)), int(m.group(2))
            scope = "CONSOLIDATED" if m.group(3) == "C" else "INDIVIDUAL"
            errors = validate_raw(data, ticker, year, season, scope)
            if errors:
                raise ValueError(f"{name} RAW_INVALID:{'|'.join(errors)}")
            facts, _ = parse_facts(data)
            filings.append({"ticker": ticker, "issuer_id": ISSUERS[ticker][0], "year": year, "season": season,
                            "report_scope": scope, "url": manifest["url"], "raw_sha256": digest,
                            "manifest": manifest, "mapping_version": tag_map["spec_version"], "facts": facts,
                            "raw_path": full})
    return filings


def prepare_rows(filings, tag_map, target_periods=None):
    direct = []
    for filing in filings:
        direct.extend(build_filing_rows(filing, tag_map))
        direct.extend(build_valuation_version_rows(filing))
    rows = quarterize(filings, direct)
    for filing in filings:
        st = approved_short_term_observation(filing, rows)
        if st:
            rows.append(st)
    rows.extend(build_general_correction_timing_versions(filings, rows))
    if target_periods is not None:
        targets = set(target_periods)
        rows = [r for r in rows if (r.get("fiscal_year"), int(str(r.get("fiscal_period", "Q0"))[1:])) in targets]
    return rows


def latest_periods_from_filings(filings, count=9):
    return sorted({(f["year"], f["season"]) for f in filings})[-count:]


def _period_summary(filings, rows):
    all_metrics = sorted(DIRECT_QUARTER_METRICS | INSTANT_METRICS | CUMULATIVE_METRICS |
                         {"FIN_DEPRECIATION_AMORTIZATION", "FIN_SHORT_TERM_INVESTMENTS", "FIN_DILUTED_SHARES"})
    outp = []
    for f in sorted(filings, key=lambda x: (x["ticker"], x["year"], x["season"])):
        found = sorted({r["metric_id"] for r in rows if r["issuer_id"] == f["issuer_id"] and
                        r["fiscal_year"] == f["year"] and r["fiscal_period"] == f"Q{f['season']}"})
        missing = [m for m in all_metrics if m not in found]
        reasons = {}
        for metric in missing:
            if metric == "FIN_SHORT_TERM_INVESTMENTS":
                reasons[metric] = "尚無逐季完整候選清單與獨立 AI 複核；不得當零"
            elif metric == "FIN_DILUTED_SHARES":
                reasons[metric] = "實測 XBRL 未找到稀釋加權平均股數；不得反推"
            elif metric in CUMULATIVE_METRICS | {"FIN_DEPRECIATION_AMORTIZATION"}:
                reasons[metric] = "缺少精確白名單事實，或同年度同報表範圍的前期累計組成"
            else:
                reasons[metric] = "沒有符合精確 namespace、科目、期間與無維度 context 的事實；不得當零"
        outp.append({"ticker": f["ticker"], "issuer_id": f["issuer_id"], "period": f"{f['year']}Q{f['season']}",
                     "report_scope": f["report_scope"], "raw_sha256": f["raw_sha256"],
                     "availability_status": _availability(f["manifest"])["availability_status"],
                      "metrics": found, "missing": missing, "missing_reasons": reasons,
                      "fy_exclusion_reasons": list(f.get("fy_exclusion_reasons", []))})
    return outp


def render_report(mode, filings, rows, written):
    summary = _period_summary(filings, rows)
    lines = [f"# MOPS 台股財報轉接器 {mode}", "",
             f"FILINGS: `{len(filings)}`", f"OBSERVATIONS_PREPARED: `{len(rows)}`", f"OBSERVATIONS_WRITTEN: `{written}`", "",
             "| 代號 | 期別 | 範圍 | 時間狀態 | 已產生指標 | 缺少 |", "|---|---|---|---|---|---|"]
    for item in summary:
        missing_text = "、".join(f"{metric}（{item['missing_reasons'][metric]}）" for metric in item["missing"])
        lines.append(f"| {item['ticker']} | {item['period']} | {item['report_scope']} | {item['availability_status']} | "
                     f"{'、'.join(item['metrics']) or '—'} | {missing_text or '—'} |")
    lines += ["", "`FIN_SHORT_TERM_INVESTMENTS` 在沒有逐季判讀及獨立複核前不產生；缺值不當零。",
              "`FIN_DILUTED_SHARES` 未在實測 XBRL 中找到，不以 EPS 或股本反推。"]
    return "\n".join(lines), summary


def main():
    parser = argparse.ArgumentParser(description="MOPS iXBRL 台股財報轉接器")
    parser.add_argument("--ticker")
    parser.add_argument("--year", type=int)
    parser.add_argument("--season", type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--from-dir")
    parser.add_argument("--output")
    parser.add_argument("--min-interval", type=float, default=10.0)
    parser.add_argument("--lock-holder")
    args = parser.parse_args()
    if args.dry_run == args.write:
        parser.error("必須且只能選 --dry-run 或 --write")
    if bool(args.start_year) != bool(args.end_year):
        parser.error("--start-year 與 --end-year 必須一起指定")
    if args.start_year and (args.year or args.season or args.start_year > args.end_year):
        parser.error("年度範圍不得與 --year/--season 混用，且起年不得晚於迄年")
    if args.write:
        import lock as project_lock
        project_lock.require_holder(args.lock_holder, "observations")
        project_lock.require_holder(args.lock_holder, "docs")
    with open(TAG_MAP_PATH, encoding="utf-8") as handle:
        tag_map = json.load(handle)
    liq_errors = validate_liquidity_records()
    if liq_errors:
        raise ValueError("INVALID_LIQUIDITY_LEDGER:" + "|".join(liq_errors))
    if args.from_dir:
        filings = filings_from_dir(args.from_dir, tag_map)
    else:
        # mopsov.twse.com.tw/robots.txt 明示 User-agent: * / Disallow: /；
        # 新站 mops.twse.com.tw/robots.txt 目前回安全攔截頁，不能證明允許。
        # 保留既有離線解析能力，但禁止此 adapter 發出自動網路請求。
        raise RuntimeError(LIVE_FETCH_DISABLED_REASON + ":請先由人工下載官方檔，再用 --from-dir 離線匯入")
        tickers = sorted(ISSUERS) if args.all else [x.strip() for x in (args.ticker or "").split(",") if x.strip()]
        if not tickers or any(t not in ISSUERS for t in tickers):
            parser.error("請用 --all 或 --ticker 指定白名單內代號")
        # 最新 9 季的 Q2/Q3/Q4 現金流單季值需要前一季累計數，因此多抓 1 季作差分依賴。
        if args.start_year:
            periods = [(year, season) for year in range(args.start_year, args.end_year + 1)
                       for season in range(1, 5)]
        else:
            periods = [(args.year, args.season)] if args.year and args.season else periods_latest_n(10)
        filings = acquire_filings(tickers, periods, Fetcher(args.min_interval), tag_map, args.dry_run)
    explicit_single = bool(args.year and args.season)
    explicit_range = bool(args.start_year)
    if explicit_single:
        target_periods = [(args.year, args.season)]
    elif explicit_range:
        target_periods = [(year, season) for year in range(args.start_year, args.end_year + 1)
                          for season in range(1, 5)]
    else:
        target_periods = latest_periods_from_filings(filings, 9)
    rows = prepare_rows(filings, tag_map, target_periods)
    report_filings = [f for f in filings if (f["year"], f["season"]) in set(target_periods)]
    errors = [(r.get("observation_id"), validate_observation(r)) for r in rows]
    errors = [(oid, e) for oid, e in errors if e]
    if errors:
        raise ValueError("INVALID_OBSERVATIONS:" + canonical_json(errors[:10]))
    written = append_financial_observations(rows, lock_holder=args.lock_holder) if args.write else 0
    mode = "WRITE" if args.write else "DRY_RUN"
    text, summary = render_report(mode, report_filings, rows, written)
    output = args.output or os.path.join(ROOT, "04_回測", f"MOPS台股財報_{mode}_{dt.date.today().isoformat()}.md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    json_path = os.path.splitext(output)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump({"mode": mode, "filings": summary, "observation_count": len(rows), "written": written},
                  handle, ensure_ascii=False, indent=2)
    out(text)


if __name__ == "__main__":
    main()
