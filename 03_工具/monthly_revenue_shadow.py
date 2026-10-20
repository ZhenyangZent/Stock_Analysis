"""T43 Taiwan monthly-revenue shadow collector.

Writes an append-only research ledger.  It never changes formal classifications.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request

from common import ROOT
from staging_write import append_bytes, validate_jsonl_bytes
from t43_candidates import monthly_available_at

LEDGER = os.path.join(ROOT, "04_回測", "weekly_refresh", "t43_monthly_revenue_shadow.jsonl")
ENDPOINTS = {
    "TWSE_OPENAPI": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "TPEX_OPENAPI": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
}
TRACKED = {"2330", "2344", "2383", "2408", "3017", "3037", "3081", "3324", "6274"}


def _roc_month(value):
    text = str(value).strip()
    if len(text) != 5 or not text.isdigit():
        raise ValueError("MONTHLY_REVENUE_DATA_MONTH_INVALID")
    return f"{int(text[:3]) + 1911:04d}-{text[3:]}"


def normalize(row, source, fetched_at):
    ticker = str(row.get("公司代號", "")).strip()
    if ticker not in TRACKED:
        return None
    month = _roc_month(row.get("資料年月"))
    return {
        "record_type": "T43_C1M_SHADOW",
        "ticker": ticker,
        "data_month": month,
        "revenue": float(row["營業收入-當月營收"]),
        "revenue_prior_year": float(row["營業收入-去年當月營收"]),
        "source": source,
        "source_url": ENDPOINTS[source],
        "fetched_at": fetched_at,
        "available_at": monthly_available_at(month, fetched_at),
        "classification_effect": None,
        "candidate_only": True,
    }


def fetch_source(source, opener=urllib.request.urlopen):
    request = urllib.request.Request(ENDPOINTS[source], headers={"User-Agent": "research-shadow/1.0"})
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def update(lock_holder, *, fetched_at=None, fetcher=fetch_source, path=LEDGER):
    fetched_at = fetched_at or dt.datetime.now(dt.timezone.utc).isoformat()
    existing = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            existing = [json.loads(line) for line in handle if line.strip()]
    keys = {(r.get("ticker"), r.get("data_month"), r.get("source")) for r in existing}
    rows, statuses = [], []
    for source in ENDPOINTS:
        try:
            raw = fetcher(source)
            selected = [x for item in raw if (x := normalize(item, source, fetched_at))]
            added = [x for x in selected if (x["ticker"], x["data_month"], x["source"]) not in keys]
            rows.extend(added); keys.update((x["ticker"], x["data_month"], x["source"]) for x in added)
            statuses.append({"source": source, "status": "PASS", "selected": len(selected), "new": len(added)})
        except Exception as exc:
            statuses.append({"source": source, "status": "WARN", "reason": f"{type(exc).__name__}:{exc}"})
    if rows:
        payload = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for r in rows)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        append_bytes(path, payload, writer="monthly_revenue_shadow.update", validator=validate_jsonl_bytes,
                     lock_holder=lock_holder, extra={"candidate_version": "1.13.0", "row_count": len(rows)})
    overall = "PASS" if all(x["status"] == "PASS" for x in statuses) else "WARN"
    return {"status": overall, "rows_added": len(rows), "sources": statuses,
            "formal_classification_changed": False, "formal_snapshot_created": False}
