"""T46 earnings-season document detector and extraction queue builder.

Live mode is explicit (``--now``).  The detector registers only documents
already published by SEC or already present in the approved MOPS document
flow.  TSM 6-K earnings material is deliberately isolated as a candidate and
never appended to ``docs_ledger.jsonl``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import ai_fields
from common import ROOT

SPINE = Path(ROOT) / "01_共用資料" / "data_spine"
QUEUE_PATH = Path(ROOT) / "02_執行紀錄" / "待抽取清單.json"
WORK = Path(ROOT) / "90_各AI工作區" / "codex"
TAG_MAP = Path(ROOT) / "00_規範" / "financial_tag_map.json"
UA = "AI-stock-research earnings-docs-watch zent960628@gmail.com"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _install_truststore() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _json(url: str) -> dict:
    return json.loads(_request(url).decode("utf-8"))


def issuer_specs() -> list[dict]:
    cfg = json.loads(TAG_MAP.read_text(encoding="utf-8"))
    specs = []
    for group in (cfg.get("sec_issuers") or {}, cfg.get("i4_capex_issuers") or {},
                  cfg.get("foreign_issuer_probe") or {}):
        for issuer_id, item in group.items():
            if item.get("cik"):
                specs.append({"issuer_id": issuer_id, "ticker": item.get("ticker") or
                              ("TSM" if issuer_id == "ISSUER_TSMC" else issuer_id),
                              "cik": str(item["cik"]).zfill(10)})
    return sorted({x["issuer_id"]: x for x in specs}.values(), key=lambda x: x["issuer_id"])


def _recent_rows(payload: dict) -> list[dict]:
    recent = ((payload.get("filings") or {}).get("recent") or {})
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime",
            "primaryDocument", "items")
    count = max((len(recent.get(key) or []) for key in keys), default=0)
    rows = []
    for index in range(count):
        row = {}
        for key in keys:
            values = recent.get(key) or []
            row[key] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


_DOC_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.I | re.S)


def parse_submission_documents(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    out = []
    for block in _DOC_RE.findall(text):
        def value(tag):
            match = re.search(rf"<{tag}>\s*([^\r\n<]+)", block, re.I)
            return match.group(1).strip() if match else None
        filename, doc_type = value("FILENAME"), value("TYPE")
        body = re.search(r"<TEXT>(.*)</TEXT>", block, re.I | re.S)
        if filename and doc_type and body:
            out.append({"filename": filename, "type": doc_type.upper(),
                        "description": value("DESCRIPTION"), "raw": body.group(1).encode("utf-8")})
    return out


def _accession_base(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def _submission(cik: str, accession: str) -> tuple[str, bytes, list[dict]]:
    base = _accession_base(cik, accession)
    url = f"{base}/{accession}.txt"
    raw = _request(url)
    return base, raw, parse_submission_documents(raw)


def _accepted(row: dict) -> str:
    value = row.get("acceptanceDateTime")
    if value and re.fullmatch(r"\d{14}", str(value)):
        parsed = dt.datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
        return parsed.isoformat()
    if value:
        return str(value)
    return str(row.get("filingDate")) + "T23:59:59-04:00"


def _write_candidate_raw(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _earnings_exhibits(documents: list[dict]) -> list[dict]:
    exhibits = [d for d in documents if d["type"].startswith("EX-99")]
    if len(exhibits) == 1:
        return exhibits
    hits = []
    for doc in exhibits:
        plain = re.sub(r"<[^>]+>", " ", doc["raw"].decode("utf-8", errors="replace"))
        plain = html.unescape(plain).casefold()
        if any(term in plain for term in ("financial results", "quarterly results", "net revenue", "net sales")):
            hits.append(doc)
    return hits


def _registered_accessions(docs: list[dict]) -> set[str]:
    out = set()
    for doc in docs:
        match = re.search(r"accession\s+([0-9-]{18,20})", str(doc.get("notes") or ""), re.I)
        if match:
            out.add(match.group(1))
        match = re.search(r"/([0-9]{18})/", str(doc.get("url") or ""))
        if match:
            raw = match.group(1)
            out.add(raw[:10] + "-" + raw[10:12] + "-" + raw[12:])
    return out


def _latest_report_available_at(issuer_id: str, docs: list[dict]) -> dt.datetime | None:
    values = []
    for doc in docs:
        if doc.get("issuer_id") != issuer_id or doc.get("doc_type") not in ai_fields.REPORT_DOC_TYPES:
            continue
        try:
            values.append(ai_fields.parse_time(doc.get("official_published_at") or doc.get("available_at")))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _infer_period_end(raw: bytes, fallback: str | None) -> str | None:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace")))
    pattern = re.compile(r"(?:three\s+months|quarter)\s+ended\s+"
                         r"(March|June|September|December)\s+(\d{1,2}),\s+(20\d{2})", re.I)
    hits = []
    months = {"march": 3, "june": 6, "september": 9, "december": 12}
    for match in pattern.finditer(plain):
        try:
            hits.append(dt.date(int(match.group(3)), months[match.group(1).casefold()], int(match.group(2))))
        except ValueError:
            pass
    return max(hits).isoformat() if hits else (fallback or None)


def _report_rows(spec: dict, recent: list[dict], docs: list[dict], fetched_at: str,
                 lock_holder: str, live: bool) -> tuple[list[dict], list[dict]]:
    registered = _registered_accessions(docs)
    latest_report = _latest_report_available_at(spec["issuer_id"], docs)
    additions, statuses = [], []
    for row in recent:
        form = str(row.get("form") or "").upper()
        accession = str(row.get("accessionNumber") or "")
        if not accession or accession in registered:
            continue
        try:
            accepted_at = ai_fields.parse_time(_accepted(row))
        except (TypeError, ValueError):
            continue
        # T46 is a forward earnings-season watch, not a historical backfill.
        if latest_report is not None and accepted_at <= latest_report:
            continue
        if form not in {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}:
            continue
        if form.startswith("8-K"):
            item_tokens = {x.strip() for x in re.split(r"[,;]", str(row.get("items") or ""))}
            if "2.02" not in item_tokens:
                continue
        if not live:
            statuses.append({"accession": accession, "form": form, "status": "WOULD_FETCH"})
            continue
        base, _, documents = _submission(spec["cik"], accession)
        accepted = _accepted(row)
        selected = []
        if form.startswith("8-K"):
            selected = [(doc, "8-K-EX99-EARNINGS") for doc in _earnings_exhibits(documents)]
        else:
            primary = str(row.get("primaryDocument") or "")
            match = next((d for d in documents if d["filename"] == primary), None)
            if match:
                selected = [(match, form.split("/")[0])]
        if not selected:
            statuses.append({"accession": accession, "form": form, "status": "NO_ELIGIBLE_DOCUMENT"})
            continue
        for document, doc_type in selected:
            url = base + "/" + document["filename"]
            period_end = _infer_period_end(document["raw"], row.get("reportDate") or None)
            saved = ai_fields.register_doc(
                spec["issuer_id"], doc_type, url, document["raw"], document["filename"],
                official_published_at=accepted, period_end=period_end,
                report_period_end=period_end,
                robots_status="SEC_DECLARED_UA", fetched_at=fetched_at,
                notes=f"accession {accession}; form {form}; items {row.get('items') or ''}; T46",
                lock_holder=lock_holder)
            additions.append(saved)
        statuses.append({"accession": accession, "form": form, "status": "REGISTERED",
                         "doc_ids": [x["doc_id"] for x in additions if accession in str(x.get("notes"))]})
    return additions, statuses


def _tsm_candidate(spec: dict, recent: list[dict], docs: list[dict], fetched_at: str, live: bool) -> dict | None:
    candidates = [row for row in recent if str(row.get("form")).upper() in {"6-K", "6-K/A"}]
    for row in candidates:
        period = row.get("reportDate") or ""
        if period and period < "2026-09-30":
            continue
        accession = row["accessionNumber"]
        if not live:
            return {"status": "WOULD_FETCH", "accession": accession}
        base, _, documents = _submission(spec["cik"], accession)
        exhibits = _earnings_exhibits(documents)
        if not exhibits:
            # TSM often files the earnings release as an untyped HTML attachment.
            exhibits = [d for d in documents if "withguidance" in d["filename"].casefold()]
        if not exhibits:
            continue
        doc = exhibits[0]
        raw_sha = hashlib.sha256(doc["raw"]).hexdigest()
        candidate = {
            "status": "CANDIDATE_NOT_ACTIVE", "issuer_id": spec["issuer_id"],
            "candidate_doc_type": "6-K-EX99-EARNINGS", "equivalent_report_type_for_test": "8-K-EX99-EARNINGS",
            "accession": accession, "url": base + "/" + doc["filename"], "filename": doc["filename"],
            "official_published_at": _accepted(row), "report_period_end": row.get("reportDate") or "2026-09-30",
            "raw_sha256": raw_sha, "fetched_at": fetched_at,
        }
        before = [v for v in ai_fields.active_field_values()
                  if v.get("scope") == spec["issuer_id"] and v.get("field") in {"C5", "C6", "I2", "I2Q"}]
        pseudo = dict(candidate, doc_id="CANDIDATE_" + raw_sha[:20], doc_type="8-K-EX99-EARNINGS",
                      available_at=candidate["official_published_at"])
        after_docs = docs + [pseudo]
        candidate["field_currentness_diff"] = [{
            "field": value["field"], "value_id": value["value_id"],
            "before_current": ai_fields.is_current_for_field(value, docs),
            "candidate_current": ai_fields.is_current_for_field(value, after_docs),
        } for value in before]
        raw_path = WORK / f"T46_TSM_6K_candidate_{raw_sha[:12]}_{doc['filename']}"
        saved_sha = _write_candidate_raw(raw_path, doc["raw"])
        candidate["isolated_raw_path"] = os.path.relpath(raw_path, ROOT).replace("\\", "/")
        candidate["isolated_raw_sha256"] = saved_sha
        return candidate
    return None


def _queue(new_docs: list[dict], created_at: str) -> dict:
    by_issuer = {}
    for doc in new_docs:
        by_issuer.setdefault(doc["issuer_id"], []).append(doc)
    return {"schema_version": "EARNINGS_EXTRACTION_QUEUE_1", "generated_at": created_at,
            "policy": "ONLY_REGISTERED_OFFICIAL_DOCUMENTS;NO_FUTURE_OR_UNPUBLISHED",
            "companies": [{"issuer_id": issuer, "new_report_doc_ids": sorted(d["doc_id"] for d in items),
                           "target_period": max(d.get("report_period_end") or "" for d in items) or None,
                           "required_fields": ["C5", "C6", "I2", "I2Q_MEMBER"],
                           "a_window": {"model_family": "ANTHROPIC", "status": "NOT_STARTED"},
                           "b_window": {"model_family": "OPENAI", "status": "NOT_STARTED"}}
                          for issuer, items in sorted(by_issuer.items())]}


def _write_queue(queue: dict, lock_holder: str) -> dict:
    from staging_write import commit_candidate
    payload = (json.dumps(queue, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    def validate(data):
        parsed = json.loads(data.decode("utf-8"))
        if any(not row.get("new_report_doc_ids") for row in parsed.get("companies", [])):
            raise ValueError("QUEUE_COMPANY_WITHOUT_REGISTERED_DOC")
        return {"format": "JSON", "company_count": len(parsed.get("companies", []))}
    return commit_candidate(str(QUEUE_PATH), payload, writer="earnings_docs_watch.queue",
                            validator=validate, lock_holder=lock_holder, append_only=False)


def run(*, live: bool, lock_holder: str) -> dict:
    _install_truststore()
    started = now_iso(); docs_before = ai_fields.read_jsonl(ai_fields.DOCS_LEDGER)
    additions, companies = [], []
    tsm_candidate = None
    for spec in issuer_specs():
        try:
            payload = _json(f"https://data.sec.gov/submissions/CIK{spec['cik']}.json") if live else {"filings": {"recent": {}}}
            recent = _recent_rows(payload)
            if spec["issuer_id"] == "ISSUER_TSMC":
                tsm_candidate = _tsm_candidate(spec, recent, docs_before + additions, started, live)
                companies.append({"issuer_id": spec["issuer_id"], "ticker": spec["ticker"],
                                  "status": "PASS", "registered": 0,
                                  "tsm_candidate": (tsm_candidate or {}).get("status")})
                continue
            new, statuses = _report_rows(spec, recent, docs_before + additions, started, lock_holder, live)
            additions.extend(new)
            companies.append({"issuer_id": spec["issuer_id"], "ticker": spec["ticker"],
                              "status": "PASS", "registered": len(new), "filings": statuses})
        except Exception as exc:
            companies.append({"issuer_id": spec["issuer_id"], "ticker": spec["ticker"],
                              "status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)[:500]})
    queue = _queue(additions, started)
    queue_write = _write_queue(queue, lock_holder) if live else None
    failed = [x for x in companies if x["status"] == "FAILED"]
    return {"run_id": "EDW_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8],
            "mode": "LIVE" if live else "DRY_RUN", "started_at": started, "ended_at": now_iso(),
            "status": "FAIL" if failed else "PASS", "registered_doc_count": len(additions),
            "registered_docs": [{k: d.get(k) for k in ("doc_id", "issuer_id", "doc_type", "report_period_end", "url", "raw_sha256")} for d in additions],
            "companies": companies, "queue": queue, "queue_write": queue_write,
            "tsm_6k_candidate": tsm_candidate, "formal_snapshot_created": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="perform live official-source scan and writes")
    parser.add_argument("--lock-holder")
    args = parser.parse_args(argv)
    holder, owned = args.lock_holder or "earnings_docs_watch", False
    if args.now and not args.lock_holder:
        import lock as project_lock
        ok, state = project_lock.acquire(holder, "T46 earnings documents watch", hours=3, scopes="docs,ai")
        if not ok:
            raise RuntimeError("EARNINGS_DOCS_LOCK_FAILED:" + json.dumps(state, ensure_ascii=False))
        owned = True
    try:
        result = run(live=args.now, lock_holder=holder)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result["status"] == "PASS" else 1
    finally:
        if owned:
            import lock as project_lock
            project_lock.release(holder)


if __name__ == "__main__":
    raise SystemExit(main())
