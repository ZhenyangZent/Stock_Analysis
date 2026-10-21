"""T36 weekly refresh: data fetch, official-date reconfirmation, shadow diff.

This command never creates a formal snapshot.  ``--dry-run`` performs no
network access and no writes; it only validates the plan and computes the
current statutory/shadow preview from local data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.error
import urllib.request
import urllib.robotparser
import uuid
from pathlib import Path

import data_spine
import research_snapshot
from common import ROOT, load_rules

REPORT_ROOT = Path(ROOT) / "04_回測" / "weekly_refresh"
PYTHON = sys.executable


def _now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def _normalized(text): return re.sub(r"\s+", " ", html.unescape(str(text))).strip().casefold()


def _run_tool(script, holder):
    command = [PYTHON, "-X", "utf8", str(Path(ROOT) / "03_工具" / script), "--lock-holder", holder]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                            capture_output=True)
    return {"script": script, "exit_code": result.returncode,
            "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:]}


def _official_user_agent():
    email = "zent960628@gmail.com"
    return f"AI-stock-research weekly-refresh {email}"


def _fetch_allowed(url):
    parsed = urllib.parse.urlparse(url); robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    ua = _official_user_agent(); parser = urllib.robotparser.RobotFileParser(); parser.set_url(robots_url)
    try:
        parser.read()
        if not parser.can_fetch(ua, url):
            return None, {"status": "MANUAL_REQUIRED", "reason": "ROBOTS_DISALLOW", "url": url,
                          "robots_url": robots_url}
        request = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return None, {"status": "MANUAL_REQUIRED", "reason": f"HTTP_{exc.code}", "url": url,
                      "robots_url": robots_url}
    except Exception as exc:
        return None, {"status": "MANUAL_REQUIRED", "reason": type(exc).__name__, "url": url,
                      "robots_url": robots_url}


def _sec_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": _official_user_agent()})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _sec_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": _official_user_agent()})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def _sec_8k_candidates(cik, acquired_at, fetch_json=_sec_json):
    """Return relevant 8-K filings accepted after the official date was acquired."""
    cutoff = data_spine.parse_time(acquired_at, "event.available_at")
    payload = fetch_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    recent = ((payload.get("filings") or {}).get("recent") or {})
    keys = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "primaryDocument", "items")
    count = max((len(recent.get(key) or []) for key in keys), default=0)
    rows = []
    for index in range(count):
        row = {key: ((recent.get(key) or [None] * count)[index]
                     if index < len(recent.get(key) or []) else None) for key in keys}
        if row.get("form") not in {"8-K", "8-K/A"}:
            continue
        accepted_raw = row.get("acceptanceDateTime") or (str(row.get("filingDate")) + "T23:59:59-04:00")
        try:
            accepted = data_spine.parse_time(accepted_raw, "SEC acceptanceDateTime")
        except ValueError:
            continue
        item_tokens = {part.strip() for part in re.split(r"[,;]", str(row.get("items") or "")) if part.strip()}
        if accepted <= cutoff or not item_tokens.intersection({"2.02", "7.01", "8.01"}):
            continue
        row["accepted_at"] = accepted.isoformat()
        rows.append(row)
    return sorted(rows, key=lambda row: row["accepted_at"])


def _sec_filing_documents(cik, filing, fetch_json=_sec_json, fetch_text=_sec_text):
    accession = str(filing["accessionNumber"]); accn = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}"
    index = fetch_json(base + "/index.json")
    items = ((index.get("directory") or {}).get("item") or [])
    names = [filing.get("primaryDocument")] + [item.get("name") for item in items
                                                if str(item.get("type") or "").upper().startswith("EX-99")]
    documents = []
    for name in dict.fromkeys(name for name in names if name):
        url = base + "/" + urllib.parse.quote(str(name))
        try:
            raw = fetch_text(url)
        except Exception:
            continue
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        documents.append({"url": url, "text": text})
    return documents


_MONTHS = {name.casefold(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}


def _extract_sec_earnings_date(text):
    """Conservatively extract one explicitly announced earnings-results date."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
    hits = []
    date_re = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", re.I)
    for sentence in sentences:
        lower = sentence.casefold()
        if not any(token in lower for token in ("financial results", "earnings results", "quarter results", "report its")):
            continue
        for match in date_re.finditer(sentence):
            try:
                value = dt.date(int(match.group(3)), _MONTHS[match.group(1).casefold()], int(match.group(2))).isoformat()
            except ValueError:
                continue
            hits.append((value, sentence.strip()))
    unique = {value: quote for value, quote in hits}
    if len(unique) == 1:
        value = next(iter(unique)); return {"status": "ONE_DATE", "date": value, "quote": unique[value]}
    if len(unique) > 1:
        return {"status": "AMBIGUOUS", "dates": sorted(unique)}
    return {"status": "NO_DATE"}


def _build_sec_reschedule_event(old_event, filing, document, detection, ingested_at):
    identity = "|".join((old_event["event_id"], filing["accessionNumber"], detection["date"], ingested_at))
    return {"event_id": "EVT_EARNINGS_" + hashlib.sha256(identity.encode()).hexdigest(),
            "record_type": "EVENT", "issuer_id": old_event["issuer_id"],
            "event_type": "EARNINGS_RESULTS", "event_status": "RESCHEDULED",
            "event_local_date": detection["date"], "event_local_time": None,
            "event_timezone": old_event.get("event_timezone", "America/New_York"),
            "time_precision": "DATE_ONLY", "supersedes_event_id": old_event["event_id"],
            "supersede_type": "VALUE_REVISION", "availability_basis": "SEC_ACCEPTANCE_TIMESTAMP",
            "source_system": "SEC_EDGAR_8K", "evidence_method": "SEC_8K",
            "source_url": document["url"], "source_quote": detection["quote"],
            "source_checked_at": ingested_at, "source_published_date": filing.get("filingDate"),
            "source_excerpt_sha256": hashlib.sha256(document["text"].encode()).hexdigest(),
            "available_at": filing["accepted_at"], "ingested_at": ingested_at}


def _nearest_upcoming_by_issuer(active, today):
    latest = {}
    for row in active:
        if (row.get("record_type") != "EVENT" or row.get("event_status") not in {"SCHEDULED", "RESCHEDULED"}
                or row.get("event_local_date", "") < today):
            continue
        issuer = row.get("issuer_id")
        latest[issuer] = min(latest.get(issuer, row), row,
                             key=lambda item: (item.get("event_local_date", "9999-12-31"),
                                               item.get("available_at", ""), item.get("event_id", "")))
    return latest


def _sec_scan_overall_status(statuses):
    if any(row.get("status") in {"MANUAL_REQUIRED", "UNKNOWN"} for row in statuses):
        return "NEEDS_MANUAL"
    return "PASS"


STATUS_RANK = {"PASS": 0, "NOT_APPLICABLE": 0, "WARN": 1, "NEEDS_MANUAL": 2, "FAIL": 3}


def _worst_status(statuses):
    values = [status for status in statuses if status in STATUS_RANK]
    return max(values, key=lambda status: STATUS_RANK[status]) if values else "PASS"


def _event_step_status(events, sec_scan):
    company_states = [row.get("status") for row in (events or {}).get("companies", [])]
    company_states += [row.get("status") for row in (sec_scan or {}).get("companies", [])]
    if any(state in {"UNKNOWN", "MANUAL_REQUIRED"} for state in company_states):
        return "NEEDS_MANUAL"
    return "PASS"


def _preview_step_status(preview):
    if not preview:
        return "FAIL"
    if preview.get("gaps"):
        return "NEEDS_MANUAL"
    if preview.get("classification_changes"):
        return "WARN"
    return "PASS"


def scan_sec_8k_date_changes(dry_run=False, lock_holder=None, now=None,
                             fetch_json=_sec_json, fetch_text=_sec_text):
    """Scan official SEC 8-K filings for date changes without estimating missing dates."""
    now = now or _now(); policy = (load_rules().get("verdict") or {}).get("us_official_earnings_date_coverage") or {}
    if policy.get("enabled") is not True:
        return {"status": "DISABLED_CANDIDATE_NOT_ACTIVE", "events_added": 0, "companies": []}
    events = data_spine.read_jsonl(data_spine.EVENTS_PATH)
    active, chain_error = data_spine._active_event_versions(events)
    if chain_error:
        raise ValueError("EVENT_CHAIN_INVALID:" + chain_error)
    upcoming = _nearest_upcoming_by_issuer(active, now[:10])
    registry = data_spine.load_registry()
    tag_map = data_spine.load_json(os.path.join(ROOT, "00_規範", "financial_tag_map.json"))
    issuer_map = tag_map.get("sec_issuers") or {}
    additions, statuses = [], []
    tracked = sorted((row for row in registry.get("securities", [])
                      if row.get("market") == "US" and row.get("price_series_enabled")),
                     key=lambda row: row.get("ticker", ""))
    for security in tracked:
        if security.get("security_type") == "ETF":
            statuses.append({"ticker": security.get("ticker"), "status": "NOT_APPLICABLE_ETF"})
            continue
        issuer_id = security.get("issuer_id")
        event = upcoming.get(issuer_id)
        spec = issuer_map.get(issuer_id) or {}; ticker = spec.get("ticker") or issuer_id
        if not event:
            statuses.append({"ticker": security.get("ticker") or ticker, "status": "UNKNOWN",
                             "reason": "NO_OFFICIAL_UPCOMING_EVENT_IN_LEDGER"})
            continue
        if not spec.get("cik"):
            statuses.append({"ticker": ticker, "status": "UNKNOWN", "reason": "CIK_MISSING"}); continue
        if dry_run:
            statuses.append({"ticker": ticker, "status": "PLANNED_SEC_SCAN", "since": event["available_at"]}); continue
        try:
            filings = _sec_8k_candidates(spec["cik"], event["available_at"], fetch_json=fetch_json)
        except Exception as exc:
            statuses.append({"ticker": ticker, "status": "MANUAL_REQUIRED", "reason": "SEC_SCAN_" + type(exc).__name__}); continue
        issuer_result = {"ticker": ticker, "status": "NO_DATE_CHANGE_FOUND", "filings_checked": len(filings)}
        for filing in filings:
            detections = []
            for document in _sec_filing_documents(spec["cik"], filing, fetch_json=fetch_json, fetch_text=fetch_text):
                detection = _extract_sec_earnings_date(document["text"])
                if detection["status"] == "AMBIGUOUS":
                    issuer_result = {"ticker": ticker, "status": "MANUAL_REQUIRED", "reason": "AMBIGUOUS_SEC_DATES",
                                     "accession": filing["accessionNumber"]}; detections = []; break
                if detection["status"] == "ONE_DATE": detections.append((document, detection))
            if issuer_result.get("status") == "MANUAL_REQUIRED": break
            dates = {d[1]["date"] for d in detections}
            if len(dates) > 1:
                issuer_result = {"ticker": ticker, "status": "MANUAL_REQUIRED", "reason": "CONFLICTING_SEC_DOCUMENT_DATES",
                                 "accession": filing["accessionNumber"]}; break
            if len(dates) == 1:
                document, detection = detections[0]
                if detection["date"] != event["event_local_date"]:
                    replacement = _build_sec_reschedule_event(event, filing, document, detection, now)
                    errors = data_spine.validate_event_record(replacement)
                    if errors: raise ValueError("SEC_RESCHEDULE_INVALID:" + "|".join(errors))
                    additions.append(replacement); event = replacement
                    issuer_result = {"ticker": ticker, "status": "RESCHEDULED", "date": detection["date"],
                                     "accession": filing["accessionNumber"], "url": document["url"]}
                else:
                    issuer_result = {"ticker": ticker, "status": "CONFIRMED_NO_CHANGE", "date": detection["date"],
                                     "accession": filing["accessionNumber"]}
        statuses.append(issuer_result)
    if additions:
        data_spine.append_event_batch(additions, "G-S3b-US2 SEC 8-K earnings-date revision scan",
                                      written_at=now, lock_holder=lock_holder)
    return {"status": _sec_scan_overall_status(statuses), "events_added": len(additions), "companies": statuses}


def reconfirm_us_events(dry_run=False, lock_holder=None, now=None):
    now = now or _now(); registry = data_spine.load_registry(); events = data_spine.read_jsonl(data_spine.EVENTS_PATH)
    sessions = data_spine.load_sessions(); today = now[:10]
    us_sessions = [s for s in sessions if s.get("market") == "US" and s.get("session_status", "OPEN") == "OPEN"
                   and s.get("session_date") <= today]
    coverage_start = max(us_sessions, key=lambda s: s["session_date"])["session_id"] if us_sessions else None
    # Retain scheduled records directly and select the next non-cancelled event per issuer.
    cancelled = {r.get("supersedes_event_id") for r in events if r.get("event_status") in {"CANCELLED", "RESCHEDULED"}}
    scheduled = [r for r in events if r.get("record_type") == "EVENT" and r.get("event_status") == "SCHEDULED"
                 and r.get("event_id") not in cancelled and r.get("event_local_date", "") >= today]
    latest = {}
    for row in scheduled:
        latest[row["issuer_id"]] = min(latest.get(row["issuer_id"], row), row,
                                       key=lambda x: (x.get("event_local_date", "9999"), x.get("available_at", "")))
    rows, statuses = [], []
    for sec in sorted((s for s in registry["securities"] if s.get("market") == "US" and s.get("price_series_enabled")),
                      key=lambda x: x["ticker"]):
        if sec.get("security_type") == "ETF":
            statuses.append({"ticker": sec["ticker"], "status": "NOT_APPLICABLE_ETF"})
            continue
        event = latest.get(sec["issuer_id"])
        if not event:
            statuses.append({"ticker": sec["ticker"], "status": "UNKNOWN", "date": None,
                             "reason": "NO_OFFICIAL_UPCOMING_EVENT_IN_LEDGER"}); continue
        if dry_run:
            statuses.append({"ticker": sec["ticker"], "status": "PLANNED_RECONFIRM",
                             "date": event["event_local_date"], "url": event["source_url"]}); continue
        text, error = _fetch_allowed(event["source_url"])
        if error:
            statuses.append({"ticker": sec["ticker"], "date": event["event_local_date"], **error}); continue
        if _normalized(event.get("source_quote")) not in _normalized(text):
            statuses.append({"ticker": sec["ticker"], "status": "MANUAL_REQUIRED",
                             "date": event["event_local_date"], "url": event["source_url"],
                             "reason": "SOURCE_QUOTE_NOT_FOUND_OR_SCRIPT_RENDERED"}); continue
        effective = data_spine.event_effective_sessions(event, sec, sessions)
        if effective.get("status") != "OK" or not coverage_start:
            statuses.append({"ticker": sec["ticker"], "status": "MANUAL_REQUIRED",
                             "date": event["event_local_date"], "url": event["source_url"],
                             "reason": effective.get("reason") or "NO_COVERAGE_START_SESSION"}); continue
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        identity = "|".join((event["event_id"], coverage_start, effective["block_effective_session_id"], now, source_hash))
        coverage = {"event_id": "COV_WEEKLY_" + hashlib.sha256(identity.encode()).hexdigest(),
                    "record_type": "COVERAGE", "issuer_id": sec["issuer_id"],
                    "coverage_start_session_id": coverage_start,
                    "coverage_end_session_id": effective["block_effective_session_id"],
                    "checked_at": now, "source_system": "OFFICIAL_COMPANY_IR",
                    "source_url": event["source_url"], "source_quote": event["source_quote"],
                    "source_checked_at": now, "available_at": now, "ingested_at": now,
                    "source_excerpt_sha256": source_hash, "evidence_method": "OFFICIAL_IR_WEEKLY_RECONFIRM"}
        errors = data_spine.validate_event_record(coverage)
        if errors: raise ValueError("WEEKLY_COVERAGE_INVALID:" + "|".join(errors))
        rows.append(coverage); statuses.append({"ticker": sec["ticker"], "status": "RECONFIRMED",
                                               "date": event["event_local_date"], "url": event["source_url"]})
    if rows:
        data_spine.append_event_batch(rows, "T36 weekly official IR earnings-date reconfirmation",
                                      written_at=now, lock_holder=lock_holder)
    return {"checked_at": now, "coverage_rows_added": len(rows), "companies": statuses}


def local_preview():
    project_root = Path(ROOT)
    captured = research_snapshot.capture_inputs(project_root); cutoff = research_snapshot.parse_time(_now())
    snapshot = research_snapshot.build_snapshot(captured, cutoff)
    input_manifest = research_snapshot.build_input_manifest(captured, cutoff, snapshot)
    input_bytes = research_snapshot.canonical_bytes(input_manifest)
    run_id = research_snapshot.make_run_id(cutoff, input_bytes)
    snapshot["run_id"] = run_id
    snapshot_bytes = research_snapshot.canonical_bytes(snapshot)
    snap_sha = research_snapshot.digest(snapshot_bytes)
    # Build a short-lived committed candidate outside the project.  This uses
    # the normal strict replay/cache path, but dry-run leaves no project file
    # and cannot be mistaken for a formal snapshot.
    with tempfile.TemporaryDirectory(prefix="weekly-shadow-") as temp:
        base = Path(temp) / "影子快照"; final = base / run_id; cas = base / "_cas"
        final.mkdir(parents=True); cas.mkdir(parents=True)
        for data in captured.values():
            (cas / research_snapshot.digest(data)).write_bytes(data)
        payloads = {"input_manifest.json": input_bytes, "snapshot.json": snapshot_bytes}
        for name, data in payloads.items(): (final / name).write_bytes(data)
        manifest = {"run_id": run_id, "input_manifest_sha256": research_snapshot.digest(input_bytes),
                    "status": research_snapshot.snapshot_status(snapshot),
                    "payloads": {name: {"sha256": research_snapshot.digest(data), "byte_length": len(data)}
                                 for name, data in payloads.items()}, "errors": []}
        manifest_bytes = research_snapshot.canonical_bytes(manifest)
        (final / "manifest.json").write_bytes(manifest_bytes)
        marker = {"run_id": run_id, "committed_at": cutoff.isoformat(), "published_at": cutoff.isoformat(),
                  "manifest_sha256": research_snapshot.digest(manifest_bytes), "snapshot_sha256": snap_sha}
        (final / "COMMITTED.json").write_bytes(research_snapshot.canonical_bytes(marker))
        shadow_payloads = research_snapshot.build_shadow_preview(final, base, captured)
        result = {market: json.loads(shadow_payloads[f"shadow_verdict_{market}.json"])
                  for market in research_snapshot.SHADOW_MARKETS}
    base = Path(ROOT) / "02_執行紀錄" / "影子快照"
    prior = sorted(p for p in base.glob("SNAP_*") if (p / "COMMITTED.json").exists())[-1]
    changes = []
    for market in research_snapshot.SHADOW_MARKETS:
        previous_path = prior / f"shadow_verdict_{market}.json"
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {"rows": []}
        old = {r["ticker"]: r for r in previous["rows"]}; new = {r["ticker"]: r for r in result[market]["rows"]}
        for ticker in sorted(set(old) | set(new)):
            before, after = old.get(ticker, {}), new.get(ticker, {})
            if before.get("shadow_verdict") != after.get("shadow_verdict"):
                changes.append({"market": market, "ticker": ticker,
                                "before": before.get("shadow_verdict"), "after": after.get("shadow_verdict"),
                                "reason": after.get("why"), "gaps": after.get("gaps", [])})
    tw = result.get("TW", {})
    statutory = [{"ticker": r["ticker"], "event": r.get("tw_statutory_event"),
                  "classification": r.get("tw_statutory_shadow_verdict")}
                 for r in tw.get("rows", [])]
    gaps = [{"market": market, "ticker": row["ticker"], "gaps": row.get("gaps", [])}
            for market in research_snapshot.SHADOW_MARKETS for row in result[market]["rows"] if row.get("gaps")]
    return {"candidate_run_id": run_id, "latest_formal_snapshot": prior.name,
            "classification_changes": changes, "gaps": gaps, "tw_statutory_windows": statutory}


def _markdown(report):
    lines = ["# 每週研究更新摘要", "", "本流程不建立正式快照，也不輸出買賣指令。", "",
             f"- RUN_ID：`{report['run_id']}`", f"- 模式：`{report['mode']}`", ""]
    if report.get("stopped_at"):
        lines += [f"- 已在步驟 `{report['stopped_at']}` 停止；後續未執行。", ""]
    lines += ["## 需人工處理", ""]
    event_companies = list((report.get("events") or {}).get("companies", []))
    event_companies += list((report.get("sec_event_scan") or {}).get("companies", []))
    manual = [x for x in event_companies if x.get("status") in {"UNKNOWN", "MANUAL_REQUIRED"}]
    lines += ([f"- `{x['ticker']}`：{x.get('reason')}（{x.get('url','無官方 URL')}）" for x in manual] or ["- 無。"])
    lines += ["", "## 分類變化", ""]
    changes = (report.get("preview") or {}).get("classification_changes", [])
    lines += ([f"- `{x['ticker']}`：{x.get('before')} → {x.get('after')}；{x.get('reason')}" for x in changes] or ["- 無。"])
    lines += ["", "## 缺口", ""]
    gaps = (report.get("preview") or {}).get("gaps", [])
    lines += ([f"- `{x['ticker']}`：" + "；".join(g.get("missing", "") for g in x["gaps"]) for x in gaps] or ["- 無。"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv); run_id = "WEEKLY_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    report = {"run_id": run_id, "started_at": _now(), "mode": "DRY_RUN" if args.dry_run else "LIVE",
              "formal_snapshot_created": False, "steps": []}
    holder = "weekly_refresh"
    if not args.dry_run:
        import lock as project_lock
        ok, state = project_lock.acquire(holder, "T36 weekly refresh", hours=3, scopes="ai,docs,market,events,snapshot")
        if not ok: raise RuntimeError("WEEKLY_LOCK_FAILED:" + json.dumps(state, ensure_ascii=False))
    try:
        if args.dry_run:
            report["fetches"] = [{"script": x, "status": "NOT_RUN_DRY_RUN"}
                                 for x in ("fetch_prices.py", "fetch_fx.py", "fetch_macro.py")]
            report["monthly_revenue_shadow"] = {"status": "NOT_RUN_DRY_RUN", "formal_classification_changed": False,
                                                "formal_snapshot_created": False}
        else:
            report["fetches"] = []
            for script in ("fetch_prices.py", "fetch_fx.py", "fetch_macro.py"):
                result = _run_tool(script, holder); report["fetches"].append(result)
                if result["exit_code"] != 0:
                    report["stopped_at"] = script; break
            if not report.get("stopped_at"):
                from monthly_revenue_shadow import update as update_monthly_revenue_shadow
                report["monthly_revenue_shadow"] = update_monthly_revenue_shadow(holder)
        step1 = "FAIL" if report.get("stopped_at") else (
            "WARN" if (report.get("monthly_revenue_shadow") or {}).get("status") == "WARN" else "PASS")
        report["steps"].append({"step": 1, "status": step1})
        if not report.get("stopped_at"):
            if args.dry_run:
                # A dry-run must never cross the process boundary into the live
                # document watcher.  The watcher treats ``--now`` as authority
                # to contact SEC and may write raw/candidate files before a
                # later ledger lock check, so even a failing child is unsafe.
                report["earnings_docs"] = {
                    "script": "earnings_docs_watch.py", "status": "SKIPPED_DRY_RUN",
                    "exit_code": None, "stdout": "", "stderr": ""}
            else:
                # earnings_docs_watch is explicitly live only with --now.
                command = [PYTHON, "-X", "utf8", str(Path(ROOT) / "03_工具" / "earnings_docs_watch.py"),
                           "--now", "--lock-holder", holder]
                completed = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                                           capture_output=True)
                earnings = {"script": "earnings_docs_watch.py", "exit_code": completed.returncode,
                            "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]}
                report["earnings_docs"] = earnings
                if earnings["exit_code"] != 0:
                    report["stopped_at"] = "earnings_docs_watch.py"
        if not report.get("stopped_at"):
            report["events"] = reconfirm_us_events(dry_run=args.dry_run, lock_holder=holder if not args.dry_run else None)
            report["sec_event_scan"] = scan_sec_8k_date_changes(
                dry_run=args.dry_run, lock_holder=holder if not args.dry_run else None)
            report["steps"].append({"step": 2, "status": _event_step_status(report["events"], report["sec_event_scan"])})
            report["preview"] = local_preview()
            preview_status = _preview_step_status(report["preview"])
            report["steps"] += [{"step": 3, "status": preview_status},
                                {"step": 4, "status": "WARN" if report["preview"].get("classification_changes") else "PASS"},
                                {"step": 5, "status": "PASS" if report["preview"].get("candidate_run_id") else "FAIL"}]
        report["status"] = _worst_status([row["status"] for row in report["steps"]])
        report["ended_at"] = _now()
        if not args.dry_run:
            folder = REPORT_ROOT / run_id; folder.mkdir(parents=True, exist_ok=False)
            (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
            (folder / "摘要.md").write_text(_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 1 if report.get("status") == "FAIL" else 0
    finally:
        if not args.dry_run:
            import lock as project_lock
            project_lock.release(holder)


if __name__ == "__main__":
    raise SystemExit(main())

