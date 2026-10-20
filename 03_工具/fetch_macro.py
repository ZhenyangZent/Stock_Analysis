"""抓市場環境用的公開總經數列，寫入 01_共用資料/macro.csv（只新增，不改寫）。請在自己的電腦上執行，需要網路。

  python 03_工具/fetch_macro.py                 抓 rules.json 的 market_env.series 列出的所有數列
  python 03_工具/fetch_macro.py --start 2018-01-01
  python 03_工具/fetch_macro.py --import-csv 檔案.csv --series DGS10   匯入自己下載的 CSV（第一欄日期、第二欄數值）

來源：優先使用聖路易聯邦準備銀行 FRED 官方 API；沒有 FRED_API_KEY 時退回公開 CSV。
一般數列保存目前版本；UNRATE、CPILFESL 另由 ALFRED real-time period API 保存每個公布版本，
供影子報告並列初值與修訂值。所有檔案只新增、不改寫。
"""
import os, sys, csv, io, json, datetime, urllib.error, urllib.parse, uuid
from common import ROOT, load_rules, out, file_lock
from pricedata import append_rows

MACRO = os.path.join(ROOT, "01_共用資料", "macro.csv")
MACRO_COLS = ["date", "series_id", "value", "source", "fetched_at"]
MACRO_REALTIME = os.path.join(ROOT, "01_共用資料", "macro_realtime.csv")
MACRO_REALTIME_COLS = ["date", "series_id", "realtime_start", "source", "fetched_at"]
MACRO_VINTAGES = os.path.join(ROOT, "01_共用資料", "macro_vintages.csv")
MACRO_VINTAGE_COLS = ["date", "series_id", "value", "realtime_start", "realtime_end", "source", "fetched_at"]
ALFRED_SERIES = ("UNRATE", "CPILFESL")
FRED_API = "https://api.stlouisfed.org/fred/series/observations"

def parse_fred_csv(text, series_id, now, source="FRED fredgraph.csv"):
    """第一欄是日期（欄名可能是 observation_date 或 DATE），第二欄是數值；'.' 或空白代表沒有資料，跳過。"""
    rows = []
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"{series_id}：回傳內容不是預期的兩欄 CSV")
    for line in reader:
        if len(line) < 2: continue
        d, v = line[0].strip(), line[1].strip()
        if v in ("", "."): continue
        try:
            datetime.date.fromisoformat(d); val = float(v)
        except ValueError:
            raise ValueError(f"{series_id}：無法解析這一列 {line!r}")
        rows.append({"date": d, "series_id": series_id, "value": val, "source": source, "fetched_at": now})
    if not rows:
        raise ValueError(f"{series_id}：沒有任何有效數值")
    return rows

def parse_fred_api(text, series_id, now):
    """解析 FRED 官方 API JSON；'.' 或空白是缺漏，絕不轉成 0。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{series_id}：FRED API 回傳不是 JSON（{exc.msg}）") from None
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"{series_id}：FRED API 回傳缺少 observations")
    rows, realtime_rows = [], []
    for obs in observations:
        d, raw = str(obs.get("date", "")).strip(), str(obs.get("value", "")).strip()
        if raw in ("", "."):
            continue
        try:
            datetime.date.fromisoformat(d); value = float(raw)
        except ValueError:
            raise ValueError(f"{series_id}：FRED API 有無法解析的日期或數值") from None
        rows.append({"date": d, "series_id": series_id, "value": value,
                     "source": "FRED API", "fetched_at": now})
        realtime_rows.append({"date": d, "series_id": series_id,
                              "realtime_start": str(obs.get("realtime_start", "")).strip(),
                              "source": "FRED API", "fetched_at": now})
    if not rows:
        raise ValueError(f"{series_id}：FRED API 沒有任何有效數值")
    return rows, realtime_rows

def fetch_fred_api(series_id, start, now, api_key, getter=None):
    """官方 series/observations；例外訊息會先移除 api_key。"""
    from fetch_prices import http_get, redact_secret
    getter = getter or http_get
    query = urllib.parse.urlencode({"series_id": series_id, "api_key": api_key,
                                    "file_type": "json", "observation_start": start})
    try:
        text = getter(FRED_API + "?" + query)
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {redact_secret(exc, [api_key])}") from None
    return parse_fred_api(text, series_id, now)


def parse_alfred_api(text, series_id, now):
    """解析 output_type=1 的 real-time period 列；同一觀測月可有多個修訂區間。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{series_id}：ALFRED API 回傳不是 JSON（{exc.msg}）") from None
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"{series_id}：ALFRED API 回傳缺少 observations")
    rows = []
    for obs in observations:
        d = str(obs.get("date", "")).strip()
        raw = str(obs.get("value", "")).strip()
        realtime_start = str(obs.get("realtime_start", "")).strip()
        realtime_end = str(obs.get("realtime_end", "")).strip()
        if raw in ("", "."):
            continue
        try:
            datetime.date.fromisoformat(d)
            datetime.date.fromisoformat(realtime_start)
            datetime.date.fromisoformat(realtime_end)
            value = float(raw)
        except ValueError:
            raise ValueError(f"{series_id}：ALFRED API 有無法解析的日期、real-time period 或數值") from None
        if realtime_end < realtime_start:
            raise ValueError(f"{series_id}：ALFRED realtime_end 早於 realtime_start")
        rows.append({"date": d, "series_id": series_id, "value": value,
                     "realtime_start": realtime_start, "realtime_end": realtime_end,
                     "source": "ALFRED API", "fetched_at": now})
    if not rows:
        raise ValueError(f"{series_id}：ALFRED API 沒有任何有效數值")
    return rows


def fetch_alfred_api(series_id, start, realtime_end, now, api_key, getter=None):
    """官方 series/observations real-time period；output_type=1 保留每個值的有效區間。"""
    from fetch_prices import http_get, redact_secret
    getter = getter or http_get
    query = urllib.parse.urlencode({"series_id": series_id, "api_key": api_key,
                                    "file_type": "json", "observation_start": start,
                                    "realtime_start": start, "realtime_end": realtime_end,
                                    "output_type": 1})
    try:
        text = getter(FRED_API + "?" + query)
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {redact_secret(exc, [api_key])}") from None
    return parse_alfred_api(text, series_id, now)

def load_macro(path=MACRO):
    """回傳 {數列代號: [(日期, 數值), ...]}，依日期排序；同一天重複時保留最先寫入的。"""
    data = {}
    if not os.path.exists(path): return data
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try: d, s, v = r["date"].strip(), r["series_id"].strip(), float(r["value"])
            except (KeyError, ValueError, AttributeError): continue
            data.setdefault(s, {}).setdefault(d, v)
    return {s: sorted(m.items()) for s, m in data.items()}


def load_macro_vintages(path=MACRO_VINTAGES):
    """回傳 {series_id: [vintage rows]}。

    同一資料月份與 realtime_start 若後續查到更精確的 realtime_end，append-only
    帳本會多一列；讀取時採最後寫入列，不改寫舊資料。
    """
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                d = row["date"].strip(); sid = row["series_id"].strip()
                rs = row["realtime_start"].strip(); re_ = row["realtime_end"].strip()
                datetime.date.fromisoformat(d); datetime.date.fromisoformat(rs); datetime.date.fromisoformat(re_)
                value = float(row["value"])
            except (KeyError, ValueError, AttributeError):
                continue
            key = (d, rs)
            data.setdefault(sid, {})[key] = dict(row, value=value)
    return {sid: [rows[key] for key in sorted(rows)] for sid, rows in data.items()}


def write_vintage_rows(rows, path=MACRO_VINTAGES, lock_holder=None):
    """ALFRED vintage append-only writer；同一觀測日可保存多個 real-time period。"""
    if not rows:
        return 0, 0
    return append_rows(path, MACRO_VINTAGE_COLS, rows,
                       key=("date", "series_id", "realtime_start", "realtime_end"),
                       lock_holder=lock_holder)

def write_rows(rows, now, realtime_rows=None, macro_path=MACRO, realtime_path=MACRO_REALTIME, diff_path=None,
               lock_holder=None):
    diff_path = diff_path or os.path.join(ROOT, "01_共用資料", "macro_差異.csv")
    n, conflicts = append_rows(macro_path, MACRO_COLS, rows, key=("date", "series_id"), lock_holder=lock_holder)
    if realtime_rows:
        append_rows(realtime_path, MACRO_REALTIME_COLS, realtime_rows,
                    key=("date", "series_id", "realtime_start"), lock_holder=lock_holder)
    if conflicts:
        from pricedata import append_csv_log
        append_csv_log(diff_path, ["found_at", "key", "old", "new", "source"],
                       [[now, c["key"], json.dumps(c["old"]), json.dumps(c["new"]), c["source"]]
                        for c in conflicts], lock_holder=lock_holder,
                       writer="fetch_macro.macro_conflicts")
    return n, len(conflicts)

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def main():
    from fetch_prices import (http_get, SSL_HINT, CTX_NAME, find_key, redact_secret,
                              utc_now, finalize_fetch_record)
    started = utc_now(); run_id = "FETCH_MACRO_" + uuid.uuid4().hex
    lock_holder = arg("--lock-holder")
    rules = load_rules(); cfg = rules["market_env"]; items = []; secrets = []
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if "--import-csv" in sys.argv:
        sid = arg("--series")
        if not sid:
            out("--import-csv 需要同時給 --series")
            items.append({"source": "MANUAL_CSV", "item_id": "UNKNOWN", "requested": 1, "succeeded": 0,
                          "failed": 1, "added": 0, "conflicted": 0, "error_type": "MISSING_SERIES"})
            return finalize_fetch_record("fetch_macro", run_id, started, items, mode="IMPORT_CSV",
                                         lock_holder=lock_holder)[1]
        try:
            with open(arg("--import-csv"), encoding="utf-8-sig") as f:
                rows = parse_fred_csv(f.read(), sid, now, "手動匯入 " + os.path.basename(arg("--import-csv")))
            n, c = write_rows(rows, now, lock_holder=lock_holder)
            out(f"{sid}：新增 {n} 筆，{c} 筆與既有不同（已記在 macro_差異.csv）。")
            items.append({"source": "MANUAL_CSV", "item_id": sid, "requested": 1, "succeeded": 1,
                          "failed": 0, "added": n, "conflicted": c, "error_type": None})
        except Exception as exc:
            out(f"{sid}：{type(exc).__name__}: {redact_secret(exc)}")
            items.append({"source": "MANUAL_CSV", "item_id": sid, "requested": 1, "succeeded": 0,
                          "failed": 1, "added": 0, "conflicted": 0, "error_type": type(exc).__name__})
        return finalize_fetch_record("fetch_macro", run_id, started, items, mode="IMPORT_CSV",
                                     lock_holder=lock_holder)[1]
    key, key_source, key_warns = find_key(key_name="FRED_API_KEY")
    secrets = [key]
    for warning in key_warns:
        out("注意　" + redact_secret(warning, secrets))
    mode = "FRED_API" if key else "FRED_CSV_FALLBACK_NO_KEY"
    out(f"FRED 來源模式：{mode}" + (f"（金鑰來源：{key_source}）" if key else ""))
    start = arg("--start", cfg["default_start"]); total = 0; failed = []
    for sid in cfg["series"]:
        item = {"source": "FRED_API" if key else "FRED_GRAPH_CSV", "item_id": sid, "requested": 1,
                "succeeded": 0, "failed": 0, "added": 0, "conflicted": 0, "error_type": None}
        items.append(item)
        try:
            if key:
                rows, realtime_rows = fetch_fred_api(sid, start, now, key)
            else:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
                rows = parse_fred_csv(http_get(url), sid, now); realtime_rows = []
        except Exception as e:   # 單一數列失敗不影響其他數列
            safe = redact_secret(e, secrets)
            failed.append(f"{sid}：{type(e).__name__}: {safe}")
            item.update(failed=1, error_type=type(e).__name__); continue
        try:
            n, c = write_rows(rows, now, realtime_rows, lock_holder=lock_holder); total += n
            item.update(succeeded=1, added=n, conflicted=c)
            out(f"{sid}：取得 {len(rows)} 筆，新增 {n} 筆" + (f"，{c} 筆與既有不同（修訂，已記在 macro_差異.csv）" if c else ""))
        except Exception as e:
            safe = redact_secret(e, secrets)
            failed.append(f"{sid}：{type(e).__name__}: {safe}")
            item.update(failed=1, error_type=type(e).__name__)
    # 9999-12-31 讓 API 回傳未結束版本的穩定開放區間；若日後修訂，新的
    # realtime_end 以 append-only 更正列保存，load_macro_vintages 採最後列。
    realtime_end = "9999-12-31"
    for sid in ALFRED_SERIES:
        item = {"source": "ALFRED_API", "item_id": sid + ":VINTAGES", "requested": 1,
                "succeeded": 0, "failed": 0, "added": 0, "conflicted": 0, "error_type": None}
        items.append(item)
        if not key:
            failed.append(f"{sid} vintages：FRED_API_KEY_MISSING")
            item.update(failed=1, error_type="FRED_API_KEY_MISSING")
            continue
        try:
            vintage_rows = fetch_alfred_api(sid, start, realtime_end, now, key)
            n, conflicts = write_vintage_rows(vintage_rows, lock_holder=lock_holder)
            total += n
            item.update(succeeded=1, added=n, conflicted=len(conflicts))
            out(f"{sid} vintages：取得 {len(vintage_rows)} 個公布版本，新增 {n} 筆"
                + (f"，{len(conflicts)} 筆既有鍵值衝突（保留舊列）" if conflicts else ""))
        except Exception as exc:
            safe = redact_secret(exc, secrets)
            failed.append(f"{sid} vintages：{type(exc).__name__}: {safe}")
            item.update(failed=1, error_type=type(exc).__name__)
    out(f"\n共新增 {total} 筆。")
    for f in failed: out("注意　" + f)
    if any("CERTIFICATE_VERIFY_FAILED" in f for f in failed):
        out(f"原因　目前用的憑證來源是「{CTX_NAME}」。" + SSL_HINT)
    return finalize_fetch_record("fetch_macro", run_id, started, items, secrets=secrets, mode=mode,
                                 lock_holder=lock_holder)[1]

if __name__ == "__main__":
    sys.exit(main())
