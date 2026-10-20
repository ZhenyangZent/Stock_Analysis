"""風險模組（影子模式）：只估計「損失可能有多大」，不預測什麼時候跌，也不影響 verdict.py 的結論。

  python 03_工具/risk.py                      算所有有足夠股價的代號，存報告，並把預測寫進帳本
  python 03_工具/risk.py --as-of 2026-10-02   指定基準日（只用當天與之前的資料）
  python 03_工具/risk.py --portfolio 檔案.json 另外算組合（內容：{"MU": 0.4, "AVGO": 0.6}，權重合計 1）
  python 03_工具/risk.py --no-ledger          只看結果，不寫帳本
  python 03_工具/risk.py --evaluate           拿帳本裡已到期的預測對照實際結果
  python 03_工具/risk.py --walk-forward       用歷史資料逐日重做預測並對照（回補測試，不是即時紀錄）

損失一律用正數表示：VaR95 = 0.08 代表「估計有 95% 的機會，損失不超過 8%」。
"""
import os, sys, csv, json, math, hashlib, datetime, collections
from common import ROOT, RULES_PATH, load_rules, canonical, out, file_lock
from pricedata import PRICES, ACTIONS, load_prices, load_actions, total_returns

LEDGER = os.path.join(ROOT, "04_回測", "風險預測帳本.csv")
VOID = os.path.join(ROOT, "04_回測", "風險預測作廢紀錄.csv")   # 欄位 forecast_id_prefix,reason,by,date；帳本本身不改，評分時排除
LEDGER2 = os.path.join(ROOT, "04_回測", "風險預測帳本_v2.csv")  # 1.1.1 起寫這裡；舊帳本保留不動，評分時兩本一起讀
LEDGER2_COLS = ["forecast_id", "as_of", "market", "ticker", "horizon", "model_id", "model_sha256", "data_id",
                "var95", "var975", "var99", "es975", "vol_forecast", "model_status", "limitations", "written_at"]
LEDGER_COLS = ["forecast_id", "as_of", "ticker", "horizon", "model_id", "model_sha256", "data_cutoff", "dataset_sha256",
               "var95", "var99", "es975", "vol_forecast", "model_status", "limitations", "written_at"]

# ---------- 基本統計（只用標準函式庫，結果可重現）
def quantile(xs, q):
    """線性內插分位數。"""
    s = sorted(xs); n = len(s)
    if n == 0: return None
    pos = q * (n - 1); lo = int(math.floor(pos)); hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)

def stdev(xs):
    n = len(xs)
    if n < 2: return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))

def h_day(rets, h):
    """重疊的 h 日複利報酬。"""
    if h == 1: return list(rets)
    logs = [math.log(1 + r) for r in rets]
    out_, s = [], sum(logs[:h])
    if len(logs) >= h: out_.append(math.exp(s) - 1)
    for i in range(h, len(logs)):
        s += logs[i] - logs[i - h]
        out_.append(math.exp(s) - 1)
    return out_

def ewma_vol(rets, lam):
    """每一天「用到前一天為止的資料」估的波動，最後再回傳用到最後一天的估計（給下一天用）。"""
    seed = rets[:20] if len(rets) >= 20 else rets
    var = sum(r * r for r in seed) / max(len(seed), 1)
    sig = []
    for r in rets:
        sig.append(math.sqrt(var))
        var = lam * var + (1 - lam) * r * r
    return sig, math.sqrt(var)

def loss_measures(rets, h):
    xs = h_day(rets, h)
    if len(xs) < 40: return None
    tail = sorted(xs)[:max(1, int(math.floor(len(xs) * 0.025)))]
    return {"var95": -quantile(xs, 0.05), "var975": -quantile(xs, 0.025), "var99": -quantile(xs, 0.01), "es975": -sum(tail) / len(tail), "n": len(xs)}

def forecast(rets, cfg, model, h):
    """只用傳入的報酬序列（基準日與之前），回傳 h 日損失估計。"""
    window = rets[-cfg["lookback_days"]:]
    if len(window) < cfg["min_history_days"]: return None
    if model == "HS":
        m = loss_measures(window, h); vol = stdev(window[-60:])
    elif model == "EWMA_HS":
        sig, now = ewma_vol(window, cfg["ewma_lambda"])
        scaled = [r * now / s if s > 0 else r for r, s in zip(window, sig)]
        scaled = [max(x, -0.99) for x in scaled]
        m = loss_measures(scaled, h); vol = now
    else:
        raise ValueError(model)
    if m is None: return None
    return {**m, "vol": vol}

def drawdown(levels):
    """回傳 (最大回撤, 目前回撤, 最長回復天數；仍未回復時計到最後一天)。"""
    peak, mdd, since, longest = levels[0], 0.0, 0, 0
    for x in levels:
        if x >= peak: peak, since = x, 0
        else:
            since += 1; longest = max(longest, since)
            mdd = max(mdd, 1 - x / peak)
    return mdd, 1 - levels[-1] / peak, longest

def beta(a, b):
    """a 對 b 的貝他與相關係數（兩者是日期對齊後的報酬）。"""
    n = len(a)
    if n < 30: return None, None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0: return None, None
    return cov / vb, cov / math.sqrt(va * vb)

def sha_file(*paths):
    h = hashlib.sha256()
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()

# ---------- 主要計算
def asset_report(t, series, acts, bench_rets, cfg, as_of, listing, market_latest=None):
    series = [x for x in series if x[0] <= as_of]
    tr = total_returns(series, acts)
    rets = [r for _, r in tr]
    res = {"ticker": t, "market": listing, "data_cutoff": series[-1][0] if series else None, "n_days": len(rets), "has_actions": bool(acts)}
    h = hashlib.sha256(json.dumps([[d, c] for d, c, _ in series], separators=(",", ":")).encode())
    h.update(json.dumps(sorted((d, a["div"], a["split"]) for d, a in (acts or {}).items() if d <= as_of)).encode())
    res["data_id"] = h.hexdigest()   # 這一檔「到基準日為止的價格與公司行動」的指紋；資料被更正過就會不同
    if listing == "TW":  # 台股單日漲跌幅上限 10%，超過又沒有分割或除權息紀錄，多半是資料沒還原
        res["suspect_days"] = [f"{d}（{r:+.0%}）" for d, r in tr[-cfg["lookback_days"]:] if abs(r) > 0.105 and d not in (acts or {})]
    if len(rets) < cfg["min_history_days"]:
        res["status"] = f"資料不足（需要 {cfg['min_history_days']} 個交易日，目前 {len(rets)}）"
        return res
    latest = market_latest or as_of   # 每個市場用自己的最新交易日，台股和美股不互相比
    res["stale"] = series[-1][0] < latest
    res["status"] = "影子模式" if not res["stale"] else f"資料過期（最後一筆是 {series[-1][0]}，同市場最新是 {latest}）"
    ann = math.sqrt(252)
    res["vol"] = {str(n): stdev(rets[-n:]) * ann for n in (20, 60, 120)}
    neg = [min(r, 0) for r in rets[-120:]]
    res["downside_vol_120"] = math.sqrt(sum(x * x for x in neg) / len(neg)) * ann
    levels = [1.0]
    for r in rets[-cfg["lookback_days"]:]: levels.append(levels[-1] * (1 + r))
    res["max_drawdown"], res["current_drawdown"], res["longest_recovery_days"] = drawdown(levels)
    if bench_rets is not None:
        bm = dict(bench_rets); pairs = [(r, bm[d]) for d, r in tr if d in bm][-cfg["beta_window"]:]
        b, corr = beta([p[0] for p in pairs], [p[1] for p in pairs])
        res["beta"], res["corr"] = b, corr
        if b is not None:
            res["benchmark_shock"] = {f"{s:.0%}": b * s for s in cfg["benchmark_shocks"]}
    vols = [(c * v) for _, c, v in series[-20:] if v]
    res["avg_dollar_volume_20"] = sum(vols) / len(vols) if vols else None
    if listing == "TW":
        res["limit_like_days_250"] = sum(1 for r in rets[-250:] if abs(r) >= cfg["tw_limit_move"])
    res["forecasts"] = {m: {str(h): forecast(rets, cfg, m, h) for h in cfg["horizons"]} for m in cfg["models"]}
    res["limitations"] = "未含匯率、流動性折價、停牌與漲跌停" + ("" if acts else "；沒有除權息紀錄，若期間有配息則損失會被高估")
    if res.get("suspect_days"):
        res["status"] = "資料可疑：有超過漲跌幅上限的單日變動，疑似分割或除權未登記"
        res["limitations"] += "；" + res["status"]
    return res

def portfolio_report(weights, prices, actions, cfg, as_of):
    tr = {t: dict(total_returns([x for x in prices.get(t, []) if x[0] <= as_of], actions.get(t))) for t in weights}
    missing = [t for t in weights if len(tr[t]) < cfg["min_history_days"]]
    if missing: return {"status": f"資料不足：{missing}"}
    dates = sorted(set.intersection(*[set(v) for v in tr.values()]))[-cfg["lookback_days"]:]
    if len(dates) < cfg["min_history_days"]:
        return {"status": f"資料不足：各代號共同的交易日只有 {len(dates)} 天"}
    pr = [sum(w * tr[t][d] for t, w in weights.items()) for d in dates]
    res = {"status": "影子模式", "n_days": len(dates), "weights": weights, "vol_60": stdev(pr[-60:]) * math.sqrt(252),
           "forecasts": {m: {str(h): forecast(pr, cfg, m, h) for h in cfg["horizons"]} for m in cfg["models"]}}
    ts = list(weights); cs = []
    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            _, c = beta([tr[ts[i]][d] for d in dates[-120:]], [tr[ts[j]][d] for d in dates[-120:]])
            if c is not None: cs.append(c)
    res["avg_pairwise_corr_120"] = sum(cs) / len(cs) if cs else None
    und = {}
    for h in cfg["horizons"]:
        parts = [forecast([tr[t][d] for d in dates], cfg, "HS", h) for t in ts]
        und[str(h)] = sum(abs(weights[t]) * p["var99"] for t, p in zip(ts, parts)) if all(parts) else None
    res["var99_if_correlation_one"] = und
    res["limitations"] = "台股與美股只取共同交易日；未含匯率；權重視為固定"
    return res

def _read_csv(path):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def void_prefixes():
    return [r["forecast_id_prefix"] for r in _read_csv(VOID) if r.get("forecast_id_prefix")]

def ledger_rows():
    """兩本帳本合併、排除作廢；同一（基準日、代號、天數、模型）有多筆時只留最早寫入的那筆。"""
    void = void_prefixes(); rows = []
    for r in _read_csv(LEDGER): rows.append({**r, "var975": "", "ledger": "v1"})
    for r in _read_csv(LEDGER2): rows.append({**r, "ledger": "v2"})
    n_all = len(rows)
    def voided(r):   # 舊帳本用開頭比對；v2 的識別碼含模型與資料指紋，必須寫到指紋（至少 5 個 | ）才算，免得更正後補記的也被作廢
        return any(r["forecast_id"].startswith(v) and (r["ledger"] == "v1" or v.count("|") >= 5) for v in void)
    rows = [r for r in rows if not voided(r)]
    n_valid, first = len(rows), {}
    for r in sorted(rows, key=lambda r: r.get("written_at", "")):
        first.setdefault((r["as_of"], r["ticker"], r["horizon"], r["model_id"]), r)
    return list(first.values()), n_all, n_all - n_valid, n_valid - len(first)

def write_ledger(reports, model_sha, status, lock_holder=None):
    """每一檔用自己的資料截止日當基準日。預測一旦寫入就不改；同一份資料、同一版模型不重複寫。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, LEDGER2)
    os.makedirs(os.path.dirname(LEDGER2), exist_ok=True)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with file_lock(LEDGER2):
        existing, _, _, _ = ledger_rows()
        have_key = {(r["as_of"], r["ticker"], r["horizon"], r["model_id"]) for r in existing}
        have_id = {r["forecast_id"] for r in _read_csv(LEDGER2)}
        rows = []
        for r in reports:
            if r.get("stale") or r.get("suspect_days"): continue   # 資料過期或可疑的不記
            a = r["data_cutoff"]
            for m, hs in (r.get("forecasts") or {}).items():
                for h, f in hs.items():
                    fid = f"{a}|{r['ticker']}|{h}|{m}|{model_sha[:8]}|{r['data_id'][:8]}"
                    if f is None or fid in have_id or (a, r["ticker"], str(h), m) in have_key: continue
                    rows.append([fid, a, r.get("market") or "", r["ticker"], h, m, model_sha, r["data_id"], f"{f['var95']:.6f}", f"{f['var975']:.6f}",
                                 f"{f['var99']:.6f}", f"{f['es975']:.6f}", f"{f['vol']:.6f}", status, r["limitations"], now])
        new = not os.path.exists(LEDGER2)
        with open(LEDGER2, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(LEDGER2_COLS)
            w.writerows(rows)
    return len(rows)

# ---------- 驗證
def kupiec(n, x, p):
    """Kupiec 比例檢定：n 次預測、x 次超出、宣稱超出機率 p。回傳 (LR, p 值)。"""
    if n == 0: return None, None
    def ll(q): return ((n - x) * math.log(1 - q) if n > x else 0.0) + (x * math.log(q) if x else 0.0)
    lr = max(0.0, -2 * (ll(p) - ll(x / n)))
    return lr, math.erfc(math.sqrt(lr / 2))

def exact_binomial_p(n, x, p):
    """Two-sided exact binomial p-value (probability-ordering definition)."""
    if n <= 0: return None
    observed = math.comb(n, x) * (p ** x) * ((1 - p) ** (n - x))
    probabilities = [math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k)) for k in range(n + 1)]
    return min(1.0, sum(value for value in probabilities if value <= observed + 1e-15))

def proportion_p(n, x, p, exact_max_n=249):
    """Use exact binomial inference for small samples; asymptotic Kupiec afterwards."""
    if n <= 0: return None, "INSUFFICIENT_SAMPLE"
    if n <= exact_max_n: return exact_binomial_p(n, x, p), "EXACT_BINOMIAL"
    return kupiec(n, x, p)[1], "KUPIEC_ASYMPTOTIC"

def norm_p(z):
    return math.erfc(abs(z) / math.sqrt(2))

def hac_lag(n):
    """Newey-West 的落後期數，規則固定：floor(4 × (n/100)^(2/9))。"""
    return int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0))) if n > 1 else 0

def hac_se(xs):
    """平均數的標準誤，考慮時間上的相依（今天超出、明天也容易超出）。xs 必須依時間排序。
    用 Newey-West 估計；估出來比一般標準誤小時取一般標準誤，寧可保守。"""
    n = len(xs)
    if n < 2: return None
    m = sum(xs) / n; e = [x - m for x in xs]
    g0 = sum(v * v for v in e) / n
    L = min(hac_lag(n), n - 1); lrv = g0
    for k in range(1, L + 1):
        gk = sum(e[i] * e[i - k] for i in range(k, n)) / n
        lrv += 2 * (1 - k / (L + 1)) * gk
    plain = g0 * n / (n - 1)
    v = max(lrv, plain)
    return math.sqrt(v / n) if v > 0 else None

def summarize(records, cfg):
    """records: dict(model, h, level, date, ticker, hit, loss, var975, es975)。同一代號內需已是不重疊樣本。

    同一天多檔股票常常一起超出，不是各自獨立的證據。所以這裡先把同一天各檔的結果平均成一個數字，
    再用「不同日期數」當樣本數做檢定；另外列出各代號單獨檢定的結果。"""
    g = collections.defaultdict(list)
    for r in records:
        if r["level"] is not None: g[(r["model"], r["h"], r["level"])].append(r)
    L = ["| 模型 | 天數 | 信賴水準 | 不同日期數 | 觀察筆數 | 實際超出率 | 宣稱超出率 | 檢定 p 值（以日期為單位，已考慮時間相依） | 單獨檢定顯示低估的代號 | 超出後下一期又超出 | 判讀 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for (m, h, lvl), rs in sorted(g.items()):
        p = 1 - lvl
        by_date = collections.defaultdict(list); by_t = collections.defaultdict(list)
        for r in rs: by_date[r["date"]].append(1.0 if r["hit"] else 0.0); by_t[r["ticker"]].append(r)
        day = [sum(v) / len(v) for _, v in sorted(by_date.items())]; D = len(day)
        rate = sum(day) / D; se = hac_se(day)   # day 已依日期排序
        need = cfg["evaluate_min_n"].get(f"{lvl:.2f}", 250)
        if D < need:
            pv, p_method = None, "INSUFFICIENT_SAMPLE"
        elif se:
            pv, p_method = norm_p((rate - p) / se), "HAC_NORMAL_APPROXIMATION"
        else:
            pv, p_method = proportion_p(D, int(round(rate * D)), p, exact_max_n=need - 1)
        under = tested = 0; after = []
        for t, xs in by_t.items():
            xs = sorted(xs, key=lambda r: r["date"]); hits = [r["hit"] for r in xs]
            after += [b for a, b in zip(hits, hits[1:]) if a]
            if len(xs) >= need:
                tested += 1
                ticker_p, _ = proportion_p(len(xs), sum(hits), p, exact_max_n=need - 1)
                if ticker_p < 0.05 and sum(hits) / len(xs) > p: under += 1
        cl = sum(after) / len(after) if after else None
        if D < need: verdict = f"樣本不足（至少要 {need} 個不同日期），只能監控"
        elif pv < 0.05: verdict = "低估風險" if rate > p else "高估風險"
        else: verdict = "未發現偏誤"
        if D >= need and cl is not None and len(after) >= 10 and cl > 3 * p: verdict += "；超出事件會連續發生"
        shown_p = "—" if pv is None else f"{pv:.3f}"
        L.append(f"| {m} | {h} | {lvl:.1%} | {D} | {len(rs)} | {rate:.1%} | {p:.1%} | {shown_p} ({p_method}) | {under} / {tested} | {'—' if cl is None else f'{cl:.0%}'} | {verdict} |")
    # 尾端平均損失：只看損失超過 97.5% 門檻的那些期間，實際損失應該平均等於預測的尾端平均
    e = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in records:
        if r.get("var975") and r.get("es975") and r["loss"] > r["var975"]:
            e[(r["model"], r["h"])][r["date"]].append(r["loss"] / r["es975"] - 1)
    L += ["", f"尾端平均損失：{cfg.get('es_label', '初步尾端校準')}", "",
          "只看損失超過 97.5% 門檻的期間；差距 0 代表預測剛好，正數代表實際比預測更重。",
          "這是簡化的比較，不是正式的尾端損失檢定，只能用來發現明顯的低估，不能用來證明模型正確。", "",
          "| 模型 | 天數 | 尾端事件的不同日期數 | 實際損失相對預測尾端平均的差距 | 檢定 p 值（單尾：是否低估） | 判讀 |", "|---|---|---|---|---|---|"]
    for (m, h), d in sorted(e.items()):
        xs = [sum(v) / len(v) for _, v in sorted(d.items())]; n = len(xs); mean = sum(xs) / n; se = hac_se(xs)
        if n < cfg.get("es_min_tail_events", 30) or not se: L.append(f"| {m} | {h} | {n} | {mean:+.0%} | — | 樣本不足（至少要 {cfg.get('es_min_tail_events', 30)} 個日期），只能監控 |"); continue
        pv = 0.5 * math.erfc((mean / se) / math.sqrt(2))
        L.append(f"| {m} | {h} | {n} | {mean:+.0%} | {pv:.3f} | {'初步：低估尾端損失' if pv < 0.05 else '初步：未發現低估'} |")
    return L

def realized(rets_after, h):
    if len(rets_after) < h: return None
    v = 1.0
    for r in rets_after[:h]: v *= 1 + r
    return 1 - v  # 損失為正

def _records(model, h, date, ticker, loss, f):
    base = {"model": model, "h": h, "date": date, "ticker": ticker, "loss": loss, "var975": f.get("var975"), "es975": f.get("es975")}
    return [{**base, "level": 0.95, "hit": loss > f["var95"]}, {**base, "level": 0.99, "hit": loss > f["var99"]}]

def evaluate(rules):
    cfg = rules["risk"]
    rows, n_all, n_void, n_dup = ledger_rows()
    if not n_all: return ["帳本還沒有任何預測。"]
    prices, identity_issues = load_prices(rules, return_issues=True)
    actions = load_actions(rules)
    tr = {t: total_returns(s, actions.get(t)) for t, s in prices.items()}
    recs, pending, last = [], 0, {}
    num = lambda x: float(x) if x not in ("", None) else None
    for r in sorted(rows, key=lambda r: (r["ticker"], r["model_id"], int(r["horizon"]), r["as_of"])):
        h = int(r["horizon"]); series = tr.get(r["ticker"], [])
        idx = next((i for i, (d, _) in enumerate(series) if d > r["as_of"]), None)   # 從基準日之後第一個交易日開始算
        loss = realized([x for _, x in series[idx:]], h) if idx is not None else None
        if loss is None: pending += 1; continue
        k = (r["ticker"], r["model_id"], h)
        if k in last and idx < last[k] + h: continue  # 只取不重疊的預測
        last[k] = idx
        recs += _records(r["model_id"], h, r["as_of"], r["ticker"], loss, {"var95": num(r["var95"]), "var99": num(r["var99"]), "var975": num(r.get("var975")), "es975": num(r["es975"])})
    warning = ([f"價格身分錯誤 {len(identity_issues)} 筆已排除；詳情請執行 fetch_prices.py --check。", ""]
               if identity_issues else [])
    return warning + [f"兩本帳本共 {n_all} 筆預測：作廢 {n_void} 筆、同一預測重複寫入 {n_dup} 筆不計；有效 {len(rows)} 筆，其中 {pending} 筆尚未到期，可評分 {len(recs) // 2} 筆（已排除重疊區間）。", ""] + summarize(recs, cfg)

def walk_forward(rules, prices=None, actions=None):
    cfg = rules["risk"]
    identity_issues = []
    if prices is None:
        prices, identity_issues = load_prices(rules, return_issues=True)
        actions = load_actions(rules)
    listing = rules["prices"]["listing"]; recs = collections.defaultdict(list)
    for t, s in sorted(prices.items()):
        tr = total_returns(s, actions.get(t)); rets = [r for _, r in tr]
        for m in cfg["models"]:
            for h in cfg["horizons"]:
                i = cfg["min_history_days"]
                while i + h <= len(rets):
                    f = forecast(rets[:i], cfg, m, h)
                    if f: recs[listing.get(t, "其他")] += _records(m, h, tr[i - 1][0], t, realized(rets[i:], h), f)
                    i += h
    L = ["這是回補測試：用現在手上的歷史資料，假裝站在過去每一天重做預測。它能檢查模型有沒有系統性低估，",
         "但不等於即時紀錄（資料可能事後修正過，而且模型參數是看過這段歷史之後才定的）。", "",
         "台股與美股分開算。樣本數以「不同日期數」計：同一天多檔一起超出，只算一個日期的證據。",
         "p 值已用 Newey-West 方法考慮時間上的相依（超出事件會連續發生），落後期數規則事先固定。",
         "即使如此，這段期間只涵蓋一種市場狀態，而且追蹤名單是事後挑選的熱門股，不能代表模型在其他狀態或其他股票上也準。", ""]
    if identity_issues:
        L += [f"價格身分錯誤 {len(identity_issues)} 筆已排除；詳情請執行 fetch_prices.py --check。", ""]
    names = {"TW": "台股", "US": "美股"}
    for mk in sorted(recs):
        L += [f"## {names.get(mk, mk)}（{len({r['ticker'] for r in recs[mk]})} 檔）", ""] + summarize(recs[mk], cfg) + [""]
    return L

# ---------- 輸出
def pct(x, d=1): return "—" if x is None else f"{x:.{d}%}"

def render(as_of, reports, port, cfg, identity_issues=None, health_results=None, rules=None):
    cut_ = collections.defaultdict(set)
    for r in reports:
        if r.get("data_cutoff") and not r.get("stale"): cut_[{"TW": "台股", "US": "美股"}.get(r.get("market"), "其他")].add(r["data_cutoff"])
    L = [f"# 風險報告　基準日 " + "、".join(f"{k} {max(v)}" for k, v in sorted(cut_.items())) + "　狀態：影子模式（未經驗證）", "",
         "台股與美股各用自己最新的交易日當基準日。",
         "這份報告估計「如果下跌，可能跌多深」，不預測什麼時候會跌。數字未經樣本外驗證，不可當成買賣或放寬部位的依據。",
         "損失用正數表示。VaR 95% = 8% 的意思是：估計有 95% 的機會，這段期間的損失不超過 8%。", ""]
    ok = [r for r in reports if "forecasts" in r]; bad = [r for r in reports if "forecasts" not in r]
    if ok:
        L += ["## 波動與連動", "", "| 代號 | 資料截止 | 年化波動 20/60/120 日 | 下檔波動 | 對基準貝他 | 相關係數 | 最大回撤 | 目前回撤 | 最長回復天數 | 20 日均成交金額 | 狀態 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in ok:
            v = r["vol"]; adv = r.get("avg_dollar_volume_20")
            L.append(f"| {r['ticker']} | {r['data_cutoff']} | {pct(v['20'],0)} / {pct(v['60'],0)} / {pct(v['120'],0)} | {pct(r['downside_vol_120'],0)} | "
                     f"{'—' if r.get('beta') is None else format(r['beta'], '.2f')} | {'—' if r.get('corr') is None else format(r['corr'], '.2f')} | "
                     f"{pct(r['max_drawdown'],0)} | {pct(r['current_drawdown'],0)} | {r['longest_recovery_days']} | {'—' if not adv else format(adv, ',.0f')} | {r['status']} |")
        for m in cfg["models"]:
            L += ["", f"## 損失估計（模型 {m}：{'歷史模擬' if m == 'HS' else '依近期波動調整的歷史模擬'}）", "",
                  "| 代號 | " + " | ".join(f"{h} 日 VaR95 / VaR99 / 尾端平均" for h in cfg["horizons"]) + " |", "|---|" + "---|" * len(cfg["horizons"])]
            for r in ok:
                cells = []
                for h in cfg["horizons"]:
                    f = r["forecasts"][m][str(h)]
                    cells.append("—" if not f else f"{pct(f['var95'])} / {pct(f['var99'])} / {pct(f['es975'])}")
                L.append(f"| {r['ticker']} | " + " | ".join(cells) + " |")
        L += ["", "## 基準指數下跌時的推估（貝他 × 跌幅，只是線性推估）", "", "| 代號 | " + " | ".join(f"基準跌 {abs(s):.0%}" for s in cfg["benchmark_shocks"]) + " |", "|---|" + "---|" * len(cfg["benchmark_shocks"])]
        for r in ok:
            bs = r.get("benchmark_shock")
            L.append(f"| {r['ticker']} | " + " | ".join("—" if not bs else pct(bs[f"{s:.0%}"]) for s in cfg["benchmark_shocks"]) + " |")
        na = [r["ticker"] for r in ok if not r["has_actions"]]
        for r in ok:
            if r.get("suspect_days"):
                L += ["", f"**{r['ticker']} 的數字不可用**：{'、'.join(r['suspect_days'][:5])} 的單日變動超過台股漲跌幅上限，而 corporate_actions.csv 沒有對應紀錄。請先補上分割或除權資料。"]
        if na: L += ["", "沒有除權息紀錄的代號：" + "、".join(na) + "。若期間有配息，除息日會被算成下跌，損失估計會偏高。"]
    if bad:
        L += ["", "## 無法計算", ""] + [f"- {r['ticker']}：{r['status']}" for r in bad]
    if identity_issues:
        L += ["", "## 價格身分錯誤", ""]
        for issue in identity_issues:
            L.append(f"- prices.csv 第 {issue['line']} 行 {issue['date']} {issue['ticker']}：{'、'.join(issue['errors'])}（該列已排除）")
    if port:
        L += ["", "## 組合", ""]
        if "forecasts" not in port: L.append(port["status"])
        else:
            L += [f"權重：{port['weights']}；共同交易日 {port['n_days']} 天；60 日年化波動 {pct(port['vol_60'],0)}；120 日平均兩兩相關 {'—' if port['avg_pairwise_corr_120'] is None else format(port['avg_pairwise_corr_120'], '.2f')}", "",
                  "| 天數 | " + " | ".join(f"{m} VaR95 / VaR99 / 尾端平均" for m in cfg["models"]) + " | 相關係數變成 1 時的 VaR99 |", "|---|" + "---|" * (len(cfg["models"]) + 1)]
            for h in cfg["horizons"]:
                cells = []
                for m in cfg["models"]:
                    f = port["forecasts"][m][str(h)]
                    cells.append("—" if not f else f"{pct(f['var95'])} / {pct(f['var99'])} / {pct(f['es975'])}")
                L.append(f"| {h} | " + " | ".join(cells) + f" | {pct(port['var99_if_correlation_one'][str(h)])} |")
            L += ["", port["limitations"] + "。"]
    if health_results is not None:
        from health import render_section
        L += ["", render_section(health_results, rules or load_rules())]
    L += ["", "## 還沒做的部分", ""] + [f"- {x}" for x in cfg["not_implemented"]]
    return "\n".join(L)

def main():
    rules = load_rules(); cfg = rules["risk"]
    lock_holder = sys.argv[sys.argv.index("--lock-holder") + 1] if "--lock-holder" in sys.argv else None
    today = datetime.date.today().isoformat()
    if "--evaluate" in sys.argv or "--walk-forward" in sys.argv:
        wf = "--walk-forward" in sys.argv
        L = [f"# {'風險模型回補測試' if wf else '風險預測對照實際結果'}　{today}", ""] + (walk_forward(rules) if wf else evaluate(rules))
        text = "\n".join(L)
        with open(os.path.join(ROOT, "04_回測", f"{'風險回補測試' if wf else '風險預測評分'}_{today}.md"), "w", encoding="utf-8") as f: f.write(text)
        out(text); return
    prices, identity_issues = load_prices(rules, return_issues=True)
    actions = load_actions(rules)
    if not prices:
        out("01_共用資料/prices.csv 沒有資料。請先執行 python 03_工具/fetch_prices.py"); sys.exit(1)
    as_of = sys.argv[sys.argv.index("--as-of") + 1] if "--as-of" in sys.argv else max(s[-1][0] for s in prices.values())
    listing = rules["prices"]["listing"]; bm = rules["benchmarks"]
    latest = {}
    for t, srs in prices.items():
        ds = [d for d, _, _ in srs if d <= as_of]
        if ds: latest[listing.get(t)] = max(latest.get(listing.get(t), ""), ds[-1])
    reports = []
    for t in sorted(prices):
        b = bm["default_tw"] if listing.get(t) == "TW" else bm["default_us"]
        bser = [x for x in prices.get(b, []) if x[0] <= as_of]
        brets = total_returns(bser, actions.get(b)) if b != t and len(bser) > 30 else None
        reports.append(asset_report(t, prices[t], actions.get(t), brets, cfg, as_of, listing.get(t), latest.get(listing.get(t))))
    port = None
    if "--portfolio" in sys.argv:
        with open(sys.argv[sys.argv.index("--portfolio") + 1], encoding="utf-8") as f:
            w = {canonical(k, rules): float(v) for k, v in json.load(f).items()}
        port = portfolio_report(w, prices, actions, cfg, as_of)
    from data_spine import read_observations_checked
    from health import evaluate_all
    health_results = evaluate_all(read_observations_checked(), rules, as_of + "T23:59:59+00:00")   # 1.8.3：不同步就停止
    if "--json" in sys.argv:
        out(json.dumps({"as_of": as_of, "status": cfg["status"], "assets": reports, "portfolio": port,
                        "price_identity_issues": identity_issues, "health": health_results}, ensure_ascii=False, indent=2)); return
    text = render(as_of, reports, port, cfg, identity_issues, health_results, rules)
    if "--no-ledger" not in sys.argv:
        n = write_ledger(reports, sha_file(os.path.abspath(__file__), RULES_PATH), cfg["status"],
                         lock_holder=lock_holder)
        text += f"\n\n已把 {n} 筆預測寫入 04_回測/風險預測帳本_v2.csv（已記過的不會重複或改寫）。"
        with open(os.path.join(ROOT, "04_回測", f"風險報告_{as_of}.md"), "w", encoding="utf-8") as f: f.write(text)
    out(text)

if __name__ == "__main__":
    main()
