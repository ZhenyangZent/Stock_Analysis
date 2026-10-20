"""S3b: validate AI-extracted official IR evidence and append upcoming earnings events."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

from common import ROOT
from data_spine import (EVENTS_PATH, EVENT_WRITE_LEDGER_PATH, append_event_batch,
                        event_effective_sessions, load_registry, load_sessions,
                        read_jsonl, resolve_security, validate_event_record)

RAW_DIR = os.path.join(ROOT, "01_共用資料", "data_spine", "raw", "events")


def normalized(text):
    return re.sub(r"\s+", " ", str(text).replace("’", "'").replace("–", "-")).strip().casefold()


def prepare(payload, checked_at, existing_events=None):
    registry = load_registry()
    sessions = load_sessions()
    existing_events = read_jsonl(EVENTS_PATH) if existing_events is None else existing_events
    rows, statuses, evidence = [], [], []
    for item in payload["companies"]:
        ticker = item["ticker"]
        sec, state = resolve_security(ticker, registry)
        if state != "OK" or sec["market"] != "US":
            raise ValueError(f"{ticker}: SECURITY_NOT_ENABLED_US")
        status = item["status"]
        if status == "UNKNOWN":
            statuses.append({"ticker": ticker, "status": "UNKNOWN", "date": None,
                             "quote": None, "source_url": item["source_url"]})
            continue
        if status == "RECONFIRMED":
            quote, source_text = item["source_quote"], item["source_text"]
            if normalized(quote) not in normalized(source_text):
                raise ValueError(f"{ticker}: SOURCE_QUOTE_NOT_FOUND")
            event_id = item.get("reconfirm_event_id")
            event = next((row for row in existing_events
                          if row.get("event_id") == event_id and row.get("record_type") == "EVENT"), None)
            if not event or event.get("issuer_id") != sec.get("issuer_id"):
                raise ValueError(f"{ticker}: RECONFIRM_EVENT_NOT_FOUND")
            if item.get("event_local_date") and item["event_local_date"] != event.get("event_local_date"):
                raise ValueError(f"{ticker}: RECONFIRM_DATE_MISMATCH")
            effective = event_effective_sessions(event, sec, sessions)
            if effective["status"] != "OK":
                raise ValueError(f"{ticker}: EVENT_EFFECTIVE_FAILED:{effective['reason']}")
            source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            cov_identity = "|".join((event_id, payload["coverage_start_session_id"],
                                     effective["block_effective_session_id"], checked_at, source_hash))
            coverage = {"event_id": "COV_EARNINGS_" + hashlib.sha256(cov_identity.encode()).hexdigest(),
                        "record_type": "COVERAGE", "issuer_id": sec["issuer_id"],
                        "coverage_start_session_id": payload["coverage_start_session_id"],
                        "coverage_end_session_id": effective["block_effective_session_id"],
                        "checked_at": checked_at, "source_system": "USER_BROWSER_CAPTURE",
                        "source_url": item["source_url"], "source_quote": quote,
                        "source_checked_at": checked_at, "available_at": checked_at, "ingested_at": checked_at,
                        "source_excerpt_sha256": source_hash, "evidence_method": item["evidence_method"]}
            errors = validate_event_record(coverage)
            if errors: raise ValueError(f"{ticker}: " + "|".join(errors))
            rows.append(coverage)
            evidence.append((source_hash, {"source_url": item["source_url"], "source_quote": quote,
                                           "source_checked_at": checked_at, "evidence_method": item["evidence_method"],
                                           "source_text": source_text}))
            statuses.append({"ticker": ticker, "status": "RECONFIRMED", "date": event["event_local_date"],
                             "quote": quote, "source_url": item["source_url"], "event_id": event_id,
                             "coverage_id": coverage["event_id"],
                             "block_session_id": effective["block_effective_session_id"]})
            continue
        if status != "KNOWN_UPCOMING":
            raise ValueError(f"{ticker}: STATUS_INVALID")
        quote, source_text = item["source_quote"], item["source_text"]
        if normalized(quote) not in normalized(source_text):
            raise ValueError(f"{ticker}: SOURCE_QUOTE_NOT_FOUND")
        source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        identity = json.dumps({"issuer_id": sec["issuer_id"], "date": item["event_local_date"],
                               "url": item["source_url"], "quote": quote},
                              ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event_id = "EVT_EARNINGS_" + hashlib.sha256(identity.encode()).hexdigest()
        event = {"event_id": event_id, "record_type": "EVENT", "issuer_id": sec["issuer_id"],
                 "event_type": "EARNINGS_RESULTS", "event_status": "SCHEDULED",
                 "event_local_date": item["event_local_date"],
                 "event_local_time": item.get("event_local_time"),
                 "event_timezone": item["event_timezone"], "time_precision": item["time_precision"],
                 "source_system": "OFFICIAL_COMPANY_IR", "source_url": item["source_url"],
                 "source_quote": quote, "source_published_date": item.get("source_published_date"),
                 "source_checked_at": checked_at, "available_at": checked_at, "ingested_at": checked_at,
                 "availability_basis": "FIRST_SEEN_CONSERVATIVE", "source_excerpt_sha256": source_hash,
                 "evidence_method": item["evidence_method"]}
        effective = event_effective_sessions(event, sec, sessions)
        if effective["status"] != "OK":
            raise ValueError(f"{ticker}: EVENT_EFFECTIVE_FAILED:{effective['reason']}")
        cov_identity = event_id + "|" + payload["coverage_start_session_id"] + "|" + effective["block_effective_session_id"]
        coverage = {"event_id": "COV_EARNINGS_" + hashlib.sha256(cov_identity.encode()).hexdigest(),
                    "record_type": "COVERAGE", "issuer_id": sec["issuer_id"],
                    "coverage_start_session_id": payload["coverage_start_session_id"],
                    "coverage_end_session_id": effective["block_effective_session_id"],
                    "checked_at": checked_at, "source_system": "OFFICIAL_COMPANY_IR",
                    "source_url": item["source_url"], "source_quote": quote,
                    "source_checked_at": checked_at, "available_at": checked_at, "ingested_at": checked_at,
                    "source_excerpt_sha256": source_hash, "evidence_method": item["evidence_method"]}
        for row in (event, coverage):
            errors = validate_event_record(row)
            if errors:
                raise ValueError(f"{ticker}: " + "|".join(errors))
        rows.extend((event, coverage))
        evidence.append((source_hash, {"source_url": item["source_url"], "source_quote": quote,
                                       "source_checked_at": checked_at, "evidence_method": item["evidence_method"],
                                       "source_text": source_text}))
        statuses.append({"ticker": ticker, "status": "KNOWN_UPCOMING", "date": item["event_local_date"],
                         "quote": quote, "source_url": item["source_url"], "event_id": event_id,
                         "block_session_id": effective["block_effective_session_id"]})
    return rows, statuses, evidence


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lock-holder")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as handle:
        payload = json.load(handle)
    checked_at = payload["source_checked_at"]
    rows, statuses, evidence = prepare(payload, checked_at)
    old_ids = {row.get("event_id") for row in read_jsonl(EVENTS_PATH)}
    new_rows = [row for row in rows if row["event_id"] not in old_ids]
    if not args.dry_run and new_rows:
        import lock as project_lock
        project_lock.require_holder(args.lock_holder, "events")
        os.makedirs(RAW_DIR, exist_ok=True)
        for digest, record in evidence:
            path = os.path.join(RAW_DIR, digest + ".json")
            if not os.path.exists(path):
                with open(path, "x", encoding="utf-8", newline="") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        batch = append_event_batch(new_rows, "S3b official company IR upcoming earnings dates",
                                   written_at=checked_at, lock_holder=args.lock_holder)
    else:
        batch = None
    print(json.dumps({"status": "DRY_RUN" if args.dry_run else "OK", "companies": statuses,
                      "rows_prepared": len(rows), "rows_added": len(new_rows) if not args.dry_run else 0,
                      "batch": batch}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
