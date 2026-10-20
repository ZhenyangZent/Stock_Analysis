"""補抓台股官方現金股利／股票股利，append-only 寫入 corporate_actions.csv。"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid

from common import ROOT, file_lock, out
from fetch_prices import CTX, UA, finalize_fetch_record, http_get, roc_date, utc_now
from pricedata import ACTION_COLS, ACTIONS, _rows, append_rows

TWSE = {"0050", "2344", "2408", "3017"}
TPEX = {"3081", "3324", "6274"}


def append_preview(existing_rows, source_rows, key=("date", "ticker", "type")):
    """以 append_rows 相同語意，預先計算各來源的新增與衝突筆數。"""
    old = {tuple(str(r.get(k, "")).strip() for k in key): r for r in existing_rows}
    added = conflicted = 0
    seen = set()
    for row in source_rows:
        row_key = tuple(str(row.get(k, "")).strip() for k in key)
        if row_key in seen:
            continue
        seen.add(row_key)
        if row_key not in old:
            added += 1
            continue
        comparable = [c for c in ACTION_COLS if c not in key and c not in ("source", "fetched_at", "volume")]
        def normalized(value):
            try:
                return round(float(value), 6)
            except (TypeError, ValueError):
                return str(value).strip()
        if any(c in old[row_key] and normalized(old[row_key][c]) != normalized(row.get(c, ""))
               for c in comparable):
            conflicted += 1
    return added, conflicted


def _number(value):
    m = re.search(r"-?[0-9]+(?:\.[0-9]+)?", str(value).replace(",", ""))
    return float(m.group()) if m else 0.0


def _roc(value):
    text = str(value).strip().replace("年", "/").replace("月", "/").replace("日", "")
    return roc_date(text)


def twse_rows(start_year, end_year, now, getter=http_get):
    rows = []
    for year in range(start_year, end_year + 1):
        base = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&"
                f"startDate={year}0101&endDate={year}1231")
        payload = json.loads(getter(base)); fields = payload.get("fields") or []
        idx = {str(name).replace(" ", ""): i for i, name in enumerate(fields)}
        di, ti = idx["資料日期"], idx["股票代號"]
        for raw in payload.get("data") or []:
            ticker = str(raw[ti]).strip()
            if ticker not in TWSE:
                continue
            date = _roc(raw[di])
            detail_url = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail?response=json&"
                          f"STK_NO={ticker}&T1={date.replace('-', '')}")
            detail = json.loads(getter(detail_url)); dfields = detail.get("fields") or []
            didx = {str(name).replace(" ", ""): i for i, name in enumerate(dfields)}
            for rec in detail.get("data") or []:
                cash = _number(rec[didx["(每股配發現金股利)除息"]])
                free = _number(rec[didx["A.按普通股股東持股比例每千股無償配股"]])
                if cash > 0:
                    rows.append({"date": date, "ticker": ticker, "type": "現金股利", "value": cash,
                                 "source": detail_url, "fetched_at": now})
                if free > 0:
                    rows.append({"date": date, "ticker": ticker, "type": "股票股利",
                                 "value": 1 + free / 1000.0, "source": detail_url, "fetched_at": now})
    return rows


def _post_json(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    headers = {**UA, "Referer": "https://www.tpex.org.tw/zh-tw/announce/market/ex/cal.html",
               "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                timeout=30, context=CTX) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def tpex_rows(start_year, end_year, now, poster=_post_json):
    rows, endpoint = [], "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
    for year in range(start_year, end_year + 1):
        payload = poster(endpoint, {"startDate": f"{year}/01/01", "endDate": f"{year}/12/31",
                                    "response": "json"})
        table = (payload.get("tables") or [{}])[0]; fields = table.get("fields") or []
        idx = {str(name).replace(" ", ""): i for i, name in enumerate(fields)}
        for rec in table.get("data") or []:
            ticker = str(rec[idx["代號"]]).strip()
            if ticker not in TPEX:
                continue
            date, cash = _roc(rec[idx["除權息日期"]]), _number(rec[idx["現金股利"]])
            free = _number(rec[idx["每仟股無償配股"]])
            source = ("https://www.tpex.org.tw/zh-tw/announce/market/ex/cal.html?"
                      f"startDate={year}%2F01%2F01&endDate={year}%2F12%2F31")
            if cash > 0:
                rows.append({"date": date, "ticker": ticker, "type": "現金股利", "value": cash,
                             "source": source, "fetched_at": now})
            if free > 0:
                rows.append({"date": date, "ticker": ticker, "type": "股票股利",
                             "value": 1 + free / 1000.0, "source": source, "fetched_at": now})
    return rows


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    start = int(argv[argv.index("--start-year") + 1]) if "--start-year" in argv else 2021
    end = int(argv[argv.index("--end-year") + 1]) if "--end-year" in argv else dt.date.today().year
    lock_holder = argv[argv.index("--lock-holder") + 1] if "--lock-holder" in argv else None
    started = utc_now(); run_id = "FETCH_TW_ACTIONS_" + uuid.uuid4().hex
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    items, all_rows, rows_by_source = [], [], {}
    for name, fn, count in (("TWSE_TWT49U", twse_rows, len(TWSE)),
                            ("TPEX_EXRIGHT", tpex_rows, len(TPEX))):
        item = {"source": name, "item_id": f"{start}-{end}", "requested": count,
                "succeeded": 0, "failed": 0, "added": 0, "conflicted": 0, "error_type": None}
        items.append(item)
        try:
            got = fn(start, end, now); all_rows.extend(got); rows_by_source[name] = got; item["succeeded"] = count
            item["official_rows"] = len(got)
        except Exception as exc:
            item.update(failed=count, error_type=type(exc).__name__)
            out(f"{name}：{type(exc).__name__}: {exc}")
    if not any(x["failed"] for x in items):
        existing = _rows(ACTIONS)
        for item in items:
            item["added"], item["conflicted"] = append_preview(existing, rows_by_source[item["source"]])
        with file_lock(ACTIONS):
            added, conflicts = append_rows(ACTIONS, ACTION_COLS, all_rows,
                                            key=("date", "ticker", "type"), lock_holder=lock_holder)
        if sum(x["added"] for x in items) != added or sum(x["conflicted"] for x in items) != len(conflicts):
            raise RuntimeError("SOURCE_ALLOCATION_MISMATCH")
        out(f"官方除權息：取得 {len(all_rows)} 列，新增 {added} 列，衝突 {len(conflicts)} 列。")
    return finalize_fetch_record("fetch_tw_actions", run_id, started, items,
                                 mode="OFFICIAL_TWSE_TPEX", lock_holder=lock_holder)[1]


if __name__ == "__main__":
    raise SystemExit(main())
