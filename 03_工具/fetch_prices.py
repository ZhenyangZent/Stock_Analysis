"""抓股價寫入 01_共用資料/prices.csv（只新增，不改寫既有列）。請在自己的電腦上執行，需要網路。

  python 03_工具/fetch_prices.py                         抓 rules.json 裡所有追蹤名單與基準指數，從預設起始日到今天
  python 03_工具/fetch_prices.py --tickers 2408,0050 --start 2025-01-01
  python 03_工具/fetch_prices.py --tickers ANET --start 2021-08-01 --backfill   # 美股：只回補既有最早日之前
  python 03_工具/fetch_prices.py --import-csv 檔案.csv --ticker MU    匯入券商或看盤軟體匯出的 CSV（欄位要有 Date 與 Close）

資料來源
  台股：臺灣證券交易所 STOCK_DAY；查無資料時改查櫃買中心。都是未還原收盤價，除權息要另外記在 corporate_actions.csv。
  美股：Tiingo 日線 API，需要免費金鑰。配息與分割會自動寫入 corporate_actions.csv。沒有金鑰時跳過美股，可改用 --import-csv。

金鑰放哪裡（依序尋找，找到就用）
  1. 環境變數 TIINGO_API_KEY
  2. 環境變數 AI_STOCK_ENV_FILE 指向的檔案（內容一行：TIINGO_API_KEY=你的金鑰）
  3. 使用者家目錄下的 .ai_stock/.env
  金鑰不可以放在這個專案資料夾裡：其他 AI 都讀得到，資料夾打包分享時也會一起帶走。

  python 03_工具/fetch_prices.py --check                  只檢查既有資料有沒有缺交易日，不連網
"""
import os, sys, csv, json, time, datetime, collections, urllib.request, urllib.parse, urllib.error, re, uuid
from common import ROOT, load_rules, canonical, out, file_lock
from pricedata import PRICES, ACTIONS, PRICE_COLS, ACTION_COLS, append_rows, load_prices, completeness, validate_price_identity
from data_spine import load_registry, resolve_security

UA = {"User-Agent": "Mozilla/5.0 (research; personal use)"}
FETCH_RUNS = os.path.join(ROOT, "01_共用資料", "抓取紀錄.jsonl")

def ssl_context():
    """憑證驗證一律保留。優先用作業系統的憑證庫（pip install truststore），其次 certifi，最後才是 Python 內建。
    有些 Windows 的 Python 找不到根憑證，或防毒軟體會檢查加密連線，這時只有作業系統憑證庫能通過驗證。"""
    import ssl
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "作業系統憑證庫（truststore）"
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where()), "certifi"
    except ImportError:
        return ssl.create_default_context(), "Python 內建"

CTX, CTX_NAME = ssl_context()
SSL_HINT = ("憑證驗證失敗。請先執行  python -m pip install truststore  再重跑。"
            "這會讓 Python 改用 Windows 的憑證庫；程式不會關閉憑證驗證。")

def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "replace")

def read_env_file(path):
    vals = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1); vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    except UnicodeDecodeError:
        raise ValueError(f"金鑰檔 {path} 不是 UTF-8 編碼，請用 UTF-8 重新存檔")
    return vals

def is_inside(path, folder):
    try: return os.path.commonpath([os.path.realpath(path), os.path.realpath(folder)]) == os.path.realpath(folder)
    except ValueError: return False

def find_key(environ=None, home=None, root=ROOT, key_name="TIINGO_API_KEY"):
    """回傳 (金鑰或空字串, 來源說明, 警告清單)。絕不印出金鑰本身。"""
    environ = os.environ if environ is None else environ
    home = os.path.expanduser("~") if home is None else home
    warns = []
    if os.path.exists(os.path.join(root, ".env")):
        warns.append("專案資料夾裡有 .env。金鑰放在這裡，其他 AI 和拿到資料夾的人都讀得到；程式不會讀它。請搬到 "
                     + os.path.join(home, ".ai_stock", ".env") + "，或用 AI_STOCK_ENV_FILE 指到專案外的位置。")
    if environ.get(key_name, "").strip():
        return environ[key_name].strip(), f"環境變數 {key_name}", warns
    cands = []
    if environ.get("AI_STOCK_ENV_FILE", "").strip(): cands.append((environ["AI_STOCK_ENV_FILE"].strip(), "AI_STOCK_ENV_FILE 指向的檔案"))
    cands.append((os.path.join(home, ".ai_stock", ".env"), "家目錄的 .ai_stock/.env"))
    for path, name in cands:
        if is_inside(path, root):
            warns.append(f"{name} 位在專案資料夾內，不讀取。"); continue
        k = read_env_file(path).get(key_name, "")
        if k: return k, name, warns
    return "", "", warns

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def redact_secret(value, secrets=()):
    """遮蔽已知金鑰與 URL query 中的 api_key；供 stdout、例外與抓取紀錄共用。"""
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    return re.sub(r"(?i)(api_key=)[^&\s\"']+", r"\1[REDACTED]", text)

def _sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): _sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v, secrets) for v in value]
    return redact_secret(value, secrets) if isinstance(value, str) else value

def append_fetch_record(record, path=FETCH_RUNS, secrets=(), lock_holder=None):
    """每次執行追加一筆機器可讀摘要；寫入前遞迴遮蔽秘密。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    clean = _sanitize(record, secrets)
    from staging_write import append_bytes, validate_jsonl_bytes
    payload = (json.dumps(clean, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    append_bytes(path, payload, writer="fetch_prices.append_fetch_record",
                 validator=validate_jsonl_bytes, lock_holder=lock_holder,
                 extra={"run_id": clean.get("run_id"), "tool": clean.get("tool")})
    return clean

def finalize_fetch_record(tool, run_id, started_at, items, path=FETCH_RUNS, secrets=(), mode=None, lock_holder=None):
    failed = sum(int(x.get("failed", 0)) for x in items)
    record = {
        "run_id": run_id, "tool": tool, "started_at_utc": started_at, "ended_at_utc": utc_now(),
        "mode": mode, "items": items,
        "overall": {
            "requested": sum(int(x.get("requested", 0)) for x in items),
            "succeeded": sum(int(x.get("succeeded", 0)) for x in items),
            "failed": failed,
            "added": sum(int(x.get("added", 0)) for x in items),
            "conflicted": sum(int(x.get("conflicted", 0)) for x in items),
        },
        "exit_status": "SUCCESS" if failed == 0 else "PARTIAL_FAILURE",
    }
    append_fetch_record(record, path, secrets, lock_holder=lock_holder)
    return record, (0 if failed == 0 else 1)

def roc_date(s):
    """民國日期 115/09/01 → 2026-09-01。"""
    y, m, d = [int(x) for x in s.strip().replace("＊", "").replace("*", "").split("/")]
    return datetime.date(y + 1911, m, d).isoformat()

def num(s):
    s = str(s).replace(",", "").strip()
    return float(s) if s not in ("", "--", "---", "X") else None

def parse_tw(fields, data, ticker, source, now, vol_mult=1):
    """交易所與櫃買的月表格式相近：用欄位名稱找日期、收盤、成交量，找不到就丟錯，不猜欄位位置。"""
    names = [str(f).replace(" ", "") for f in fields]
    def col(*cands):
        for c in cands:
            for i, n in enumerate(names):
                if n == c: return i
        raise ValueError(f"{source} 回傳的欄位裡找不到 {cands}，實際欄位：{fields}")
    di, ci = col("日期"), col("收盤價", "收盤")
    try: vi = col("成交股數", "成交張數", "成交仟股")
    except ValueError: vi = None
    rows = []
    for r in data:
        c = num(r[ci])
        if c is None: continue
        v = num(r[vi]) if vi is not None else None
        rows.append({"date": roc_date(r[di]), "ticker": ticker, "close": c, "volume": "" if v is None else v * vol_mult,
                     "source": source, "fetched_at": now})
    return rows

def months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

def fetch_twse(ticker, y, m, now):
    j = json.loads(http_get(f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={y}{m:02d}01&stockNo={ticker}"))
    if j.get("stat") != "OK" or not j.get("data"):
        return None
    return parse_tw(j["fields"], j["data"], ticker, "TWSE STOCK_DAY", now)

def fetch_tpex(ticker, y, m, now):
    q = urllib.parse.urlencode({"code": ticker, "date": f"{y}/{m:02d}/01", "response": "json"})
    j = json.loads(http_get(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?{q}"))
    tables = j.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    names = [str(f).replace(" ", "") for f in tables[0]["fields"]]
    mult = 1000 if any(n in ("成交張數", "成交仟股") for n in names) else 1
    return parse_tw(tables[0]["fields"], tables[0]["data"], ticker, "TPEx tradingStock", now, mult)

def month_done(y, m, have_dates, market_dates, today=None):
    """這個月要不要跳過：該月已結束，而且同市場其他代號有交易的日子這個代號都有。
    沒有其他代號可比對時，至少要有 15 個交易日才算完整。"""
    today = today or datetime.date.today()
    last = datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)
    if last >= today.replace(day=1): return False
    key = f"{y}-{m:02d}"
    mine = {d for d in have_dates if d.startswith(key)}
    ref = {d for d in market_dates if d.startswith(key)}
    if ref - mine: return False
    return len(mine) >= (1 if ref else 15)

def fetch_tw(ticker, start, end, now, have, market_dates=(), pause=3.0):
    rows, market, log = [], None, []
    for y, m in months(start, end):
        if month_done(y, m, have, market_dates):
            continue  # 已完整的月份不重抓；有缺交易日的月份會重抓補齊（重複的列不會寫入）
        got = None
        for name, fn in ([("TWSE", fetch_twse), ("TPEX", fetch_tpex)] if market is None else [(market, fetch_twse if market == "TWSE" else fetch_tpex)]):
            try:
                got = fn(ticker, y, m, now)
            except (urllib.error.URLError, ValueError, json.JSONDecodeError, TimeoutError, OSError) as e:
                log.append(f"{ticker} {y}-{m:02d} {name} 失敗：{e}")
                got = None
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    return rows, [], log
            time.sleep(pause)
            if got:
                market = name; break
        if got:
            rows += [r for r in got if start.isoformat() <= r["date"] <= end.isoformat()]
        else:
            log.append(f"{ticker} {y}-{m:02d} 查無資料")
    return rows, [], log

def fetch_us(ticker, start, end, now, key):
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={start}&endDate={end}&format=json"
    data = json.loads(http_get(url, {"Authorization": f"Token {key}", "Content-Type": "application/json"}))
    rows, acts = [], []
    for r in data:
        d = str(r["date"])[:10]
        rows.append({"date": d, "ticker": ticker, "close": float(r["close"]), "volume": r.get("volume", ""), "source": "Tiingo EOD", "fetched_at": now})
        if float(r.get("divCash") or 0) > 0:
            acts.append({"date": d, "ticker": ticker, "type": "現金股利", "value": float(r["divCash"]), "source": "Tiingo EOD", "fetched_at": now})
        if float(r.get("splitFactor") or 1) != 1:
            acts.append({"date": d, "ticker": ticker, "type": "分割", "value": float(r["splitFactor"]), "source": "Tiingo EOD", "fetched_at": now})
    return rows, acts, []

def import_csv(path, ticker, now):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            low = {k.strip().lower(): v for k, v in r.items() if k}
            d = low.get("date") or low.get("日期"); c = low.get("close") or low.get("收盤價") or low.get("收盤")
            if not d or num(c) is None: continue
            d = d.strip().replace("/", "-")[:10]
            datetime.date.fromisoformat(d)  # 格式不對就直接報錯
            rows.append({"date": d, "ticker": ticker, "close": num(c), "volume": num(low.get("volume") or low.get("成交量") or "") or "",
                         "source": f"手動匯入 {os.path.basename(path)}", "fetched_at": now})
    return rows

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def report_gaps(rules):
    prices, identity_issues = load_prices(rules, return_issues=True)
    for issue in identity_issues:
        out(f"身分檢查錯誤　prices.csv 第 {issue['line']} 行 {issue['date']} {issue['ticker']}：{'、'.join(issue['errors'])}")
    gaps = completeness(prices, rules["prices"]["listing"])
    if not gaps:
        out("完整性檢查：每個代號在同市場的交易日都有資料。")
        return len(identity_issues)
    for t, ds in sorted(gaps.items()):
        out(f"完整性檢查　{t} 缺 {len(ds)} 個交易日：{'、'.join(ds[:8])}{' …' if len(ds) > 8 else ''}")
    out("以上日期同市場至少有兩個代號有交易。可能是停牌、暫停交易，或抓取時漏掉；重跑抓價程式會嘗試補齊。")
    return len(gaps) + len(identity_issues)

def main():
    started = utc_now(); run_id = "FETCH_PRICES_" + uuid.uuid4().hex
    lock_holder = arg("--lock-holder")
    rules = load_rules(); cfg = rules["prices"]; items = []; secrets = []
    if "--check" in sys.argv:
        issues = report_gaps(rules)
        items.append({"source": "LOCAL_PRICE_FILES", "item_id": "COMPLETENESS", "requested": 1,
                      "succeeded": 1, "failed": 0, "added": 0, "conflicted": issues, "error_type": None})
        return finalize_fetch_record("fetch_prices", run_id, started, items, mode="CHECK_ONLY", lock_holder=lock_holder)[1]
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    all_rows, all_acts, log = [], [], []
    if "--import-csv" in sys.argv:
        t = canonical(arg("--ticker"), rules)
        if not t:
            out("--import-csv 需要同時給 --ticker")
            items.append({"source": "MANUAL_CSV", "item_id": "UNKNOWN", "requested": 1, "succeeded": 0,
                          "failed": 1, "added": 0, "conflicted": 0, "error_type": "MISSING_TICKER"})
            return finalize_fetch_record("fetch_prices", run_id, started, items, mode="IMPORT_CSV", lock_holder=lock_holder)[1]
        try:
            all_rows = import_csv(arg("--import-csv"), t, now)
            items.append({"source": "MANUAL_CSV", "item_id": t, "requested": 1, "succeeded": 1,
                          "failed": 0, "added": 0, "conflicted": 0, "error_type": None})
        except Exception as e:
            out(f"{t}：匯入失敗，{type(e).__name__}: {redact_secret(e)}")
            items.append({"source": "MANUAL_CSV", "item_id": t, "requested": 1, "succeeded": 0,
                          "failed": 1, "added": 0, "conflicted": 0, "error_type": type(e).__name__})
            return finalize_fetch_record("fetch_prices", run_id, started, items, mode="IMPORT_CSV", lock_holder=lock_holder)[1]
    else:
        start = datetime.date.fromisoformat(arg("--start", cfg["default_start"]))
        end = datetime.date.fromisoformat(arg("--end", datetime.date.today().isoformat()))
        tickers = [canonical(x.strip(), rules) for x in arg("--tickers", "").split(",") if x.strip()] or sorted(cfg["listing"])
        key, key_src, key_warns = find_key()
        secrets = [key]
        log += key_warns
        if key: out(f"美股金鑰來源：{key_src}")
        existing = load_prices(rules)
        mkt_dates = collections.defaultdict(set)
        for tt, ss in existing.items():
            for d, _, _ in ss: mkt_dates[(cfg["listing"].get(tt), tt)].add(d)
        for t in tickers:
            mkt = cfg["listing"].get(t)
            item = {"source": "TW_OFFICIAL" if mkt == "TW" else "TIINGO" if mkt == "US" else "UNKNOWN",
                    "item_id": t, "requested": 1, "succeeded": 0, "failed": 0,
                    "added": 0, "conflicted": 0, "error_type": None}
            items.append(item)
            have = {d for d, _, _ in existing.get(t, [])}
            others = set().union(*[v for (mk, tt), v in mkt_dates.items() if mk == mkt and tt != t] or [set()])
            try:
                if mkt == "TW":
                    out(f"抓 {t}（台股，每個月一次請求，會比較慢）…")
                    r, a, l = fetch_tw(t, start, end, now, have, {d for d in others if not have or d >= min(have)})
                elif mkt == "US":
                    if not key:
                        log.append(f"{t}：找不到 Tiingo 金鑰，跳過。金鑰放置位置見本程式開頭說明，或改用 --import-csv")
                        item.update(failed=1, error_type="MISSING_API_KEY"); continue
                    out(f"抓 {t}（美股）…")
                    last = existing.get(t, [])[-1][0] if existing.get(t) else None
                    gap = sorted(d for d in others if have and min(have) < d < max(have) and d not in have)
                    if gap: last = None; start_us = datetime.date.fromisoformat(gap[0])   # 中間有缺就從缺的那天重抓
                    else: start_us = start
                    if "--backfill" in sys.argv and have:
                        # 回補既有最早一天之前的期間（只追加；既有列不重抓、不改寫）
                        last, end_us = None, min(end, datetime.date.fromisoformat(min(have)) - datetime.timedelta(days=1))
                    else:
                        end_us = end
                    s = max(start_us, datetime.date.fromisoformat(last) + datetime.timedelta(days=1)) if last else start_us
                    r, a, l = fetch_us(t, s, end_us, now, key) if s <= end_us else ([], [], [])
                else:
                    log.append(f"{t}：rules.json 的 prices.listing 沒有這個代號，跳過")
                    item.update(failed=1, error_type="UNKNOWN_LISTING"); continue
            except Exception as e:  # 單一代號失敗不影響其他代號
                log.append(f"{t}：失敗，{type(e).__name__}: {redact_secret(e, secrets)}")
                item.update(failed=1, error_type=type(e).__name__); continue
            if not r and any("查無資料" in x or "失敗" in x for x in l):
                item.update(failed=1, error_type="SOURCE_NO_DATA_OR_ERROR")
            else:
                item["succeeded"] = 1
            all_rows += r; all_acts += a; log += l
    try:
        registry = load_registry()
        for row in all_rows:
            sec, state = resolve_security(canonical(row.get("ticker"), rules), registry)
            if state != "OK":
                raise ValueError(f"{row.get('ticker')}：{state}")
            row["security_id"], row["currency"] = sec["security_id"], sec["currency"]
            identity_errors = validate_price_identity(row, rules, registry)
            if identity_errors:
                raise ValueError(f"{row.get('ticker')} 價格身分不符：{identity_errors}")
        pre_write_prices = load_prices(rules)
        existing_price_keys = {(d, t) for t, series in pre_write_prices.items() for d, _, _ in series}
        n1, c1, r1 = append_rows(PRICES, PRICE_COLS, all_rows, lock_holder=lock_holder, return_details=True)
        n2, c2, r2 = append_rows(ACTIONS, ACTION_COLS, all_acts, key=("date", "ticker", "type"),
                                 lock_holder=lock_holder, return_details=True)
    except Exception as e:
        out(f"寫入失敗，{type(e).__name__}: {redact_secret(e, secrets)}")
        items.append({"source": "LOCAL_PRICE_FILES", "item_id": "WRITE", "requested": 1,
                      "succeeded": 0, "failed": 1, "added": 0, "conflicted": 0,
                      "error_type": type(e).__name__})
        mode = "IMPORT_CSV" if "--import-csv" in sys.argv else "LIVE_FETCH"
        return finalize_fetch_record("fetch_prices", run_id, started, items, secrets=secrets, mode=mode,
                                     lock_holder=lock_holder)[1]
    # append_rows 已完成後，用本批候選鍵與實際 conflict 精確分攤摘要；既有鍵不重複計入 added。
    for item in items:
        t = item["item_id"]
        candidate = {(r["date"], r["ticker"]) for r in all_rows if r["ticker"] == t}
        item["added"] = sum(1 for k in candidate if k not in existing_price_keys)
        item["conflicted"] = sum(1 for c in c1 + c2 if c["key"].split("|")[1] == t)
        item["metadata_completion"] = sum(1 for row in r1 if row["key"].split("|")[1] == t
                                          and row["status"] == "METADATA_COMPLETION")
        item["reconfirmed"] = sum(1 for row in r1 + r2 if row["key"].split("|")[1] == t
                                  and row["status"] == "RECONFIRMED_NO_VALUE_CHANGE")
    out(f"\n新增股價 {n1} 筆、公司行動 {n2} 筆。")
    if c1 or c2:
        p = os.path.join(ROOT, "01_共用資料", "prices_差異.csv")
        from pricedata import append_csv_log
        append_csv_log(p, ["found_at", "key", "old", "new", "source"],
                       [[now, c["key"], json.dumps(c["old"], ensure_ascii=False),
                         json.dumps(c["new"], ensure_ascii=False), c["source"]] for c in c1 + c2],
                       lock_holder=lock_holder, writer="fetch_prices.price_conflicts")
        out(f"有 {len(c1) + len(c2)} 筆與既有資料數值不同，既有資料沒有被改寫，差異記在 01_共用資料/prices_差異.csv，請人工確認。")
    for l in log: out("注意　" + l)
    report_gaps(rules)
    if any("CERTIFICATE_VERIFY_FAILED" in l for l in log):
        out(f"\n原因　目前用的憑證來源是「{CTX_NAME}」。" + SSL_HINT)
    tw_no_action = [t for t in sorted(cfg["listing"]) if cfg["listing"][t] == "TW"]
    out("提醒　台股來源是未還原價。請把 " + "、".join(tw_no_action) + " 的除權息記在 01_共用資料/corporate_actions.csv，否則除息日會被算成下跌。")
    mode = "IMPORT_CSV" if "--import-csv" in sys.argv else "LIVE_FETCH"
    return finalize_fetch_record("fetch_prices", run_id, started, items, secrets=secrets, mode=mode,
                                 lock_holder=lock_holder)[1]

if __name__ == "__main__":
    sys.exit(main())
