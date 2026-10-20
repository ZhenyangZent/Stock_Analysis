"""抓 FRED DEXTAUS（TWD per USD）並 append-only 寫入 data_spine/fx_rates.csv。

官方序列定義為紐約市中午買入匯率；fixing_at／available_at 因此採該日
America/New_York 12:00。月底只會由 valuation_v1 取當日或之前的 fixing。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import uuid
from zoneinfo import ZoneInfo

from common import ROOT, file_lock, out
from fetch_macro import fetch_fred_api
from fetch_prices import find_key, finalize_fetch_record, redact_secret, utc_now
from pricedata import append_rows

FX_PATH = os.path.join(ROOT, "01_共用資料", "data_spine", "fx_rates.csv")
FX_COLS = ["fx_observation_id", "fx_pair", "rate", "fixing_at", "source_url",
           "source_published_at", "available_at", "ingested_at", "availability_status"]
SERIES_ID = "DEXTAUS"
SOURCE_URL = "https://fred.stlouisfed.org/series/DEXTAUS"


def fx_rows(fred_rows, ingested_at):
    rows = []
    for row in fred_rows:
        fixing = dt.datetime.combine(dt.date.fromisoformat(row["date"]), dt.time(12),
                                     ZoneInfo("America/New_York")).isoformat()
        identity = f"{SERIES_ID}|{row['date']}|{float(row['value']):.12g}"
        rows.append({
            "fx_observation_id": "FX_" + hashlib.sha256(identity.encode()).hexdigest(),
            "fx_pair": "USD/TWD", "rate": float(row["value"]), "fixing_at": fixing,
            "source_url": SOURCE_URL, "source_published_at": fixing, "available_at": fixing,
            "ingested_at": ingested_at, "availability_status": "POINT_IN_TIME_KNOWN_H10_NOON",
        })
    return rows


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    started = utc_now(); run_id = "FETCH_FX_" + uuid.uuid4().hex
    start = argv[argv.index("--start") + 1] if "--start" in argv else "2021-01-01"
    lock_holder = argv[argv.index("--lock-holder") + 1] if "--lock-holder" in argv else None
    key, source, warnings = find_key(key_name="FRED_API_KEY")
    for warning in warnings:
        out("注意　" + redact_secret(warning, [key]))
    item = {"source": "FRED_API", "item_id": SERIES_ID, "requested": 1,
            "succeeded": 0, "failed": 0, "added": 0, "conflicted": 0, "error_type": None}
    if not key:
        item.update(failed=1, error_type="FRED_API_KEY_MISSING")
        out("DEXTAUS：FRED_API_KEY_MISSING")
        return finalize_fetch_record("fetch_fx", run_id, started, [item], secrets=(),
                                     mode="FRED_API", lock_holder=lock_holder)[1]
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        fred, _ = fetch_fred_api(SERIES_ID, start, now, key)
        rows = fx_rows(fred, now)
        added, conflicts, reconciliations = append_rows(
            FX_PATH, FX_COLS, rows, key=("fx_pair", "fixing_at"), lock_holder=lock_holder,
            return_details=True)
        reconfirmed = sum(x["status"] == "RECONFIRMED_NO_VALUE_CHANGE" for x in reconciliations)
        item.update(succeeded=1, added=added, conflicted=len(conflicts), reconfirmed=reconfirmed)
        out(f"DEXTAUS：取得 {len(rows)} 筆，新增 {added} 筆，衝突 {len(conflicts)} 筆，無值變化再確認 {reconfirmed} 筆。")
    except Exception as exc:
        item.update(failed=1, error_type=type(exc).__name__)
        out("DEXTAUS：" + type(exc).__name__ + ": " + redact_secret(exc, [key]))
    return finalize_fetch_record("fetch_fx", run_id, started, [item], secrets=[key],
                                 mode="FRED_API", lock_holder=lock_holder)[1]


if __name__ == "__main__":
    raise SystemExit(main())
