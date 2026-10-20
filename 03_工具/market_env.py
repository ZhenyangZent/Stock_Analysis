"""市場環境（影子模式）：由程式從公開數列與股價算出 M1 到 M7，給出「市場風險」燈號。
它回答「現在的環境比平常危險嗎」，不預測什麼時候跌，也不影響 verdict.py 的結論。

  python 03_工具/market_env.py                    用最新資料計算，存報告並記錄
  python 03_工具/market_env.py --as-of 2026-09-30  指定基準日（只用當天與之前的資料）
  python 03_工具/market_env.py --no-ledger         只看結果
  python 03_工具/market_env.py --history           對過去每個月底算一次燈號，看之後半導體指數的表現（回補）

需要先執行 python 03_工具/fetch_macro.py 與 python 03_工具/fetch_prices.py。
"""
import os, sys, csv, json, hashlib, datetime, collections
from common import ROOT, load_rules, score_one, light, out, file_lock
from fetch_macro import load_macro, load_macro_vintages
from pricedata import load_prices, load_actions, tr_index

LEDGER_V1 = os.path.join(ROOT, "04_回測", "市場環境紀錄.csv")      # 1.5.0 的舊紀錄，保留不動
LEDGER = os.path.join(ROOT, "04_回測", "市場環境紀錄_v2.csv")       # 1.6.2 起寫這裡：有輸入指紋與更正鏈
FOMC_DECISIONS = os.path.join(ROOT, "01_共用資料", "fomc_decisions.csv")
LEDGER_COLS = ["record_id", "as_of", "mode", "status", "score", "filled", "values_json", "scores_json", "data_dates_json",
               "input_sha256", "supersedes_record_id", "protocol_version", "written_at"]
LABEL = {"綠": "正常", "黃": "升高", "紅": "高", "資料不足": "資料不足"}
DISCLAIMER = ("這是影子模式：只描述目前的環境，不預測下跌的時間點，也不是買賣建議。"
              "這類指標通常在下跌開始之後才轉壞，有時轉壞了卻沒有下跌。門檻未經驗證。")

def _upto(series, as_of):
    return [(d, v) for d, v in series if d <= as_of]

def _days(a, b):
    return (datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days

def _months_back(series, k):
    """月資料：回傳最新一筆與往前第 k 筆。"""
    if len(series) <= k: return None, None
    return series[-1], series[-1 - k]

def available(series, as_of, lag_days):
    """當時已公布的資料：資料日期加上公布延遲不晚於基準日。"""
    return [(d, v) for d, v in (series or []) if _days(as_of, d) >= lag_days]


def initial_release_series(vintages, series_id, as_of):
    """取每個資料月份最早公布的值；只納入 realtime_start 不晚於基準日者。"""
    first = {}
    for row in vintages.get(series_id, []):
        release = row.get("realtime_start", "")
        date = row.get("date", "")
        if not release or release > as_of:
            continue
        current = first.get(date)
        if current is None or release < current[0]:
            first[date] = (release, float(row["value"]))
    series = [(date, value) for date, (_, value) in sorted(first.items())]
    releases = {date: release for date, (release, _) in first.items()}
    return series, releases


def compute_initial_m4_m5(vintages, as_of, cfg):
    """ALFRED 初值影子欄；不取代 compute() 的正式 M4/M5。"""
    macro, releases = {}, {}
    for sid in ("UNRATE", "CPILFESL"):
        macro[sid], releases[sid] = initial_release_series(vintages, sid, as_of)
    calculated = compute(macro, [], as_of, cfg, point_in_time=False, fomc_decisions=[])
    result = {}
    for key, sid in (("M4", "UNRATE"), ("M5", "CPILFESL")):
        row = dict(calculated[key])
        row["release_date"] = releases[sid].get(row.get("data_date"))
        row["basis"] = "ALFRED_INITIAL_RELEASE"
        result[key] = row
    return result

def load_fomc_decisions(path=FOMC_DECISIONS):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    allowed = {"升息", "降息", "不變"}
    out_rows = []
    for row in rows:
        if row.get("decision") not in allowed or not row.get("statement_date") or not row.get("source_url"):
            raise ValueError("INVALID_FOMC_DECISION_ROW")
        out_rows.append(dict(row, target_upper_pct=float(row["target_upper_pct"])))
    return sorted(out_rows, key=lambda row: row["statement_date"])


def compute(macro, soxx_index, as_of, cfg, point_in_time=True, fomc_decisions=None):
    """回傳 {指標: {'value','data_date','reason'}}；算不出來時 value 為 None 並說明原因。

    point_in_time=True（預設，歷史基準日與回補統計一律用這個）：每個數列只用當時已公布的數值，
    也就是資料日期加上 availability_lag_days 不晚於 as_of。
    point_in_time=False 只用於「現在執行、看最新狀況」：手上已經抓到的數值全部可用。"""
    if point_in_time:
        lags = cfg.get("availability_lag_days", {})
        macro = {k: available(v, as_of, lags.get(k, 0)) for k, v in macro.items()}
    res = {}
    def put(k, value=None, date=None, reason=""):
        res[k] = {"value": value, "data_date": date, "reason": reason}
    def fresh(series, limit, k):
        s = _upto(series or [], as_of)
        if not s: put(k, reason="沒有資料"); return None
        if _days(as_of, s[-1][0]) > limit:
            put(k, date=s[-1][0], reason=f"資料過期（最後一筆 {s[-1][0]}）"); return None
        return s
    dl, ml, n = cfg["daily_stale_days"], cfg["monthly_stale_days"], cfg["change_days"]
    # M1 聯準會：以官方 FOMC statement 日期表的最近一次會議決定為準。
    decisions = load_fomc_decisions() if fomc_decisions is None else fomc_decisions
    visible = [row for row in decisions if row["statement_date"] <= as_of]
    if not visible:
        put("M1", reason="沒有已公布的 FOMC 會議決定")
    else:
        latest = dict(visible[-1], target_upper_pct=float(visible[-1]["target_upper_pct"]))
        put("M1", latest["decision"], latest["statement_date"],
            f"FOMC {latest['statement_date']}：{latest['decision']}；目標區間上限 {latest['target_upper_pct']:.2f}%")
    # M2 十年期公債殖利率 20 個交易日變化（基點）
    s = fresh(macro.get("DGS10"), dl, "M2")
    if s:
        if len(s) <= n: put("M2", date=s[-1][0], reason="歷史不足")
        else: put("M2", round((s[-1][1] - s[-1 - n][1]) * 100, 1), s[-1][0], f"{s[-1 - n][1]:.2f}% → {s[-1][1]:.2f}%")
    # M3 布蘭特原油 20 個交易日變化（%）
    s = fresh(macro.get("DCOILBRENTEU"), dl + 5, "M3")
    if s:
        if len(s) <= n or s[-1 - n][1] <= 0: put("M3", date=s[-1][0], reason="歷史不足")
        else: put("M3", round((s[-1][1] / s[-1 - n][1] - 1) * 100, 1), s[-1][0], f"{s[-1 - n][1]:.1f} → {s[-1][1]:.1f} 美元")
    # M4 失業率 3 個月變化（百分點）
    s = fresh(macro.get("UNRATE"), ml, "M4")
    if s:
        a, b = _months_back(s, 3)
        if not a: put("M4", date=s[-1][0], reason="歷史不足")
        else: put("M4", round(a[1] - b[1], 2), a[0], f"{b[1]:.1f}% → {a[1]:.1f}%")
    # M5 核心 CPI 年增率的 3 個月變化（百分點）
    s = fresh(macro.get("CPILFESL"), ml, "M5")
    if s:
        if len(s) < 16: put("M5", date=s[-1][0], reason="歷史不足（需要 16 個月）")
        else:
            yoy_now = (s[-1][1] / s[-13][1] - 1) * 100; yoy_old = (s[-4][1] / s[-16][1] - 1) * 100
            put("M5", round(yoy_now - yoy_old, 2), s[-1][0], f"年增率 {yoy_old:.2f}% → {yoy_now:.2f}%")
    # M6 半導體指數相對 200 日均線（%）。用 SOXX 含息指數代替費城半導體指數。
    idx = [(d, v) for d, v in (soxx_index or []) if d <= as_of]
    if not idx: put("M6", reason="沒有 SOXX 價格")
    elif _days(as_of, idx[-1][0]) > dl: put("M6", date=idx[-1][0], reason=f"資料過期（最後一筆 {idx[-1][0]}）")
    elif len(idx) < 200: put("M6", date=idx[-1][0], reason="歷史不足 200 個交易日")
    else:
        ma = sum(v for _, v in idx[-200:]) / 200
        put("M6", round((idx[-1][1] / ma - 1) * 100, 1), idx[-1][0], "以 SOXX（含息）代替費城半導體指數")
    # M7 高收益債信用利差 20 個交易日變化（基點）
    s = fresh(macro.get("BAMLH0A0HYM2"), dl, "M7")
    if s:
        if len(s) <= n: put("M7", date=s[-1][0], reason="歷史不足")
        else: put("M7", round((s[-1][1] - s[-1 - n][1]) * 100, 1), s[-1][0], f"{s[-1 - n][1]:.2f}% → {s[-1][1]:.2f}%")
    return res

def assess(values, rules):
    layer = rules["market"]; scores = {}
    for k, spec in layer["indicators"].items():
        scores[k] = score_one(values.get(k, {}).get("value"), spec["rule"])
    filled = sum(1 for s in scores.values() if s is not None)
    total = sum(s for s in scores.values() if s is not None)
    return {"status": light(total, filled, layer), "score": total, "filled": filled, "scores": scores}

def soxx_index(rules, prices=None, actions=None):
    prices = load_prices(rules) if prices is None else prices
    actions = load_actions(rules) if actions is None else actions
    b = rules["benchmarks"]["default_us"]
    return tr_index(prices.get(b, []), actions.get(b))

def render(as_of, values, a, rules, initial_values=None):
    ind = rules["market"]["indicators"]
    L = [f"# 市場環境報告　基準日 {as_of}　狀態：影子模式", "", DISCLAIMER, "",
         f"## 市場風險：{LABEL[a['status']]}（{a['status']}；分數 {a['score']}，已算出 {a['filled']}/{len(ind)} 項）", "",
         "分數越低代表環境越不利。合計 ≥ +2 為正常，≤ −2 為高，其餘為升高；七項至少要算出五項。", "",
         "| 編號 | 指標 | 數值 | 分數 | 資料日期 | 說明 |", "|---|---|---|---|---|---|"]
    for k, spec in ind.items():
        v = values.get(k, {}); s = a["scores"].get(k)
        L.append(f"| {k} | {spec['name']} | {'—' if v.get('value') is None else v['value']} | {'缺' if s is None else format(s, '+d')} | {v.get('data_date') or '—'} | {v.get('reason', '')} |")
    if initial_values is not None:
        L += ["", "## M4／M5 初值與修訂值（影子並列）", "",
              "正式欄位、分數與燈號仍使用上表修訂值；初值欄只供觀察修訂影響，不改變任何正式結果。", "",
              "| 編號 | 修訂值 | 初值 | 初值分數 | 資料月份 | 初值公布日 | 初值說明 |",
              "|---|---:|---:|---:|---|---|---|"]
        for key in ("M4", "M5"):
            revised = values.get(key, {})
            initial = initial_values.get(key, {})
            initial_score = score_one(initial.get("value"), ind[key]["rule"])
            L.append(f"| {key} | {'—' if revised.get('value') is None else revised['value']} | "
                     f"{'—' if initial.get('value') is None else initial['value']} | "
                     f"{'缺' if initial_score is None else format(initial_score, '+d')} | "
                     f"{initial.get('data_date') or '—'} | {initial.get('release_date') or '—'} | "
                     f"{initial.get('reason', '')} |")
    bad = [f"{k} {ind[k]['name']}" for k, s in a["scores"].items() if s == -1]
    if bad: L += ["", "目前不利的項目：" + "；".join(bad) + "。"]
    L += ["", "## 這個燈號的限制", "",
          "- 七個指標和門檻是一開始憑經驗選的，沒有用歷史資料驗證過它們和之後的跌幅有沒有關係。",
          "- 失業率與物價是月資料，公布時已經是一個多月前的狀況，而且事後會修訂。",
          "- 半導體指數相對均線本身就是價格的函數：跌了才會轉壞，不是領先指標。",
            "- 聯準會動作依官方 FOMC statement 日期表；兩次會議之間沿用最近一次決定，不由每日利率數列自行變號。"]
    return "\n".join(L)

def input_fingerprint(values):
    payload = json.dumps({k: [v.get("value"), v.get("data_date")] for k, v in sorted(values.items())}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()

def write_ledger(as_of, values, a, rules, mode="即時", lock_holder=None):
    """同一基準日、同一版規則、同一份輸入只記一次。輸入不同（補資料或更正）時新增一筆並指向被取代的那一筆，舊的保留。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, LEDGER)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    fp = input_fingerprint(values)
    with file_lock(LEDGER):
        rows = []
        if os.path.exists(LEDGER):
            with open(LEDGER, encoding="utf-8-sig", newline="") as f: rows = list(csv.DictReader(f))
        superseded = {r["supersedes_record_id"] for r in rows if r.get("supersedes_record_id")}
        active = [r for r in rows if r["as_of"] == as_of and r["mode"] == mode and r["record_id"] not in superseded]
        if any(r["input_sha256"] == fp and r["protocol_version"] == rules["protocol_version"] for r in active): return 0
        rid = "MENV_" + hashlib.sha256("|".join([as_of, mode, rules["protocol_version"], fp]).encode()).hexdigest()
        if any(r["record_id"] == rid for r in rows): return 0
        new = not os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(LEDGER_COLS)
            w.writerow([rid, as_of, mode, a["status"], a["score"], a["filled"],
                        json.dumps({k: v["value"] for k, v in values.items()}, ensure_ascii=False),
                        json.dumps(a["scores"]), json.dumps({k: v["data_date"] for k, v in values.items()}),
                        fp, active[-1]["record_id"] if active else "", rules["protocol_version"],
                        datetime.datetime.now().astimezone().isoformat(timespec="seconds")])
    return 1

def history(rules, macro, idx):
    """每個月最後一個交易日算一次燈號，看之後 20、60 個交易日 SOXX 的報酬。"""
    cfg = rules["market_env"]; dates = [d for d, _ in idx]; pos = {d: i for i, d in enumerate(dates)}
    # 只取「下一筆資料已經是下個月」的日期。最新一天所在的月份還沒結束，不算月底。
    month_ends = [d for i, d in enumerate(dates) if i + 1 < len(dates) and dates[i + 1][:7] != d[:7]]
    g = collections.defaultdict(lambda: collections.defaultdict(list)); n_ok = 0
    for d in month_ends:
        a = assess(compute(macro, idx, d, cfg), rules)
        if a["status"] == "資料不足": g["資料不足"]["count"].append(1); continue
        n_ok += 1; g[a["status"]]["count"].append(1)
        for f in cfg["follow_up_days"]:
            i = pos[d]
            if i + f < len(idx): g[a["status"]][f].append(idx[i + f][1] / idx[i][1] - 1)
    L = ["這是回補統計：對過去每個月底算一次燈號，再看之後半導體指數（SOXX）的表現。", "",
         "每個月底只用當時已公布的數列（資料日期加上保守的公布延遲）。但數值是修訂後的版本，不是當時公布的初值，",
         "所以這仍然不是嚴格的當時資料重播。", "",
         "**解讀時必須記得：**資料只涵蓋有限的期間；總經數列用的是修訂後的數字，不是當時公布的版本；",
         "月底樣本很少，而且相鄰月份的燈號高度相關。這張表只能看出燈號有沒有明顯反向，不能證明它有預測力。", "",
         "| 燈號 | 月底數 | " + " | ".join(f"之後 {f} 日平均 | 之後 {f} 日上漲比例 | 之後 {f} 日最差" for f in cfg["follow_up_days"]) + " |",
         "|---|---|" + "---|---|---|" * len(cfg["follow_up_days"])]
    for st in ("綠", "黃", "紅", "資料不足"):
        if st not in g: continue
        cells = []
        for f in cfg["follow_up_days"]:
            v = g[st].get(f, [])
            cells += ["—", "—", "—"] if not v else [f"{sum(v) / len(v):+.1%}", f"{sum(1 for x in v if x > 0) / len(v):.0%}（{len(v)} 筆）", f"{min(v):+.1%}"]
        L.append(f"| {LABEL[st]}（{st}） | {len(g[st]['count'])} | " + " | ".join(cells) + " |")
    L += ["", f"可判定的月底共 {n_ok} 個。每組少於 {cfg['min_months_for_reading']} 個月底不要解讀。"]
    return L

def main():
    rules = load_rules(); cfg = rules["market_env"]
    lock_holder = sys.argv[sys.argv.index("--lock-holder") + 1] if "--lock-holder" in sys.argv else None
    macro = load_macro(); vintages = load_macro_vintages(); idx = soxx_index(rules)
    today = datetime.date.today().isoformat()
    if "--history" in sys.argv:
        text = "\n".join([f"# 市場環境回補統計　{today}", ""] + history(rules, macro, idx))
        with open(os.path.join(ROOT, "04_回測", f"市場環境回補統計_{today}.md"), "w", encoding="utf-8") as f: f.write(text)
        out(text); return
    explicit = "--as-of" in sys.argv
    as_of = sys.argv[sys.argv.index("--as-of") + 1] if explicit else today
    # 指定歷史基準日：只用當時已公布的數值。現在執行看最新狀況：手上已抓到的都可用。
    values = compute(macro, idx, as_of, cfg, point_in_time=explicit); a = assess(values, rules)
    initial_values = compute_initial_m4_m5(vintages, as_of, cfg)
    text = render(as_of, values, a, rules, initial_values)
    if not macro:
        text += "\n\n01_共用資料/macro.csv 還沒有資料。請先在自己的電腦上執行 python 03_工具/fetch_macro.py。"
    if "--no-ledger" not in sys.argv:
        n = write_ledger(as_of, values, a, rules, mode="歷史基準日" if explicit else "即時",
                         lock_holder=lock_holder)
        text += f"\n\n已記錄 {n} 筆到 04_回測/市場環境紀錄_v2.csv（輸入相同不重複；輸入有更正時新增一筆並保留舊的）。"
        with open(os.path.join(ROOT, "04_回測", f"市場環境報告_{as_of}.md"), "w", encoding="utf-8") as f: f.write(text)
    out(text)

if __name__ == "__main__":
    main()
