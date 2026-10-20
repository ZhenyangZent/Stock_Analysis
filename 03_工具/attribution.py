"""下跌歸因（影子模式）：某檔股票明顯下跌時，把跌幅拆成「大盤」「同產業」「公司自身」三塊，
並列出資料面上有沒有異常。它不判斷該不該買，也不影響 verdict.py 的結論。

  python 03_工具/attribution.py                    檢查所有代號最近 1、5、20 個交易日有沒有觸發，存報告並記錄事件
  python 03_工具/attribution.py --as-of 2026-10-02  指定基準日（只用當天與之前的資料）
  python 03_工具/attribution.py --ticker MU         只看一檔；沒觸發也會列出拆解
  python 03_工具/attribution.py --no-ledger         只看結果，不寫事件紀錄
  python 03_工具/attribution.py --history           掃過整段歷史，統計「這類下跌之後」的結果（回補，不是即時紀錄）

拆解方法（近似，不是因果證明）：
  大盤　　＝ 這檔對基準指數的貝他 × 基準指數同期報酬（貝他用下跌區間之前的 120 個交易日估計）
  同產業　＝ 同主線、同市場的其他公司，扣掉各自的大盤部分後的平均
  公司自身＝ 剩下的部分
"""
import os, sys, csv, math, json, hashlib, datetime, collections
from common import ROOT, load_rules, canonical, out, file_lock
from pricedata import load_prices, load_actions, total_returns

LEDGER_V1 = os.path.join(ROOT, "04_回測", "下跌事件紀錄.csv")     # 1.4.0 的舊紀錄（沒有事件），保留不動
LEDGER = os.path.join(ROOT, "04_回測", "下跌事件紀錄_v2.csv")      # 1.6.2 起寫這裡：有資料指紋與更正鏈
LEDGER_COLS = ["event_id", "ticker", "market", "window_days", "start_date", "end_date", "total_return",
               "market_part", "industry_part", "company_part", "classification", "beta", "benchmark",
               "peers", "peer_scope", "sigma_multiple", "flags", "health_status_as_of_event", "health_cutoff",
               "data_id", "derivation_version", "supersedes_event_id", "protocol_version", "written_at"]
DISCLAIMER = ("這是影子模式的近似拆解，不是因果證明，也不是買賣建議。"
              "「主要是大盤拖累」不代表可以買，「公司自身」也不代表應該賣；它只告訴你該去查哪一類原因。")

def _log(r): return math.log(1 + max(r, -0.999999))

def _beta(a, b):
    n = len(a)
    if n < 60: return None
    ma, mb = sum(a) / n, sum(b) / n
    vb = sum((y - mb) ** 2 for y in b)
    if vb <= 0: return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / vb

def _stdev(xs):
    n = len(xs)
    if n < 2: return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))

def build_context(rules, as_of=None, prices=None, actions=None):
    prices = load_prices(rules) if prices is None else prices
    actions = load_actions(rules) if actions is None else actions
    tr = {}
    for t, s in prices.items():
        s = [x for x in s if as_of is None or x[0] <= as_of]
        tr[t] = total_returns(s, actions.get(t))
    listing = rules["prices"]["listing"]
    peers = collections.defaultdict(set)
    for theme in rules["themes"].values():
        wl = [canonical(x, rules) for x in theme["watchlist"]]
        for t in wl:
            peers[t] |= {p for p in wl if p != t and listing.get(p) == listing.get(t)}
    basket = collections.defaultdict(set)   # 備援：追蹤名單裡同市場的所有其他公司（不分主線）
    every = {canonical(x, rules) for th in rules["themes"].values() for x in th["watchlist"]}
    for t in every:
        basket[t] = {p for p in every if p != t and listing.get(p) == listing.get(t)}
    return {"tr": tr, "actions": actions, "listing": listing, "peers": peers, "basket": basket, "bm": rules["benchmarks"]}

def benchmark_of(t, ctx):
    return ctx["bm"]["default_tw"] if ctx["listing"].get(t) == "TW" else ctx["bm"]["default_us"]

def decompose(t, end_date, h, ctx, cfg):
    """回傳拆解結果 dict；資料不夠時 status 說明原因。end_date 必須是這檔有資料的交易日。"""
    series = ctx["tr"].get(t) or []
    dates = [d for d, _ in series]
    if end_date not in dates:
        return {"status": "資料不足：基準日沒有這檔的價格"}
    i = dates.index(end_date)
    bw, mn = cfg["beta_window"], cfg["min_history_days"]
    if i + 1 < h + mn:
        return {"status": f"資料不足：下跌區間之前不到 {mn} 個交易日"}
    win = series[i - h + 1:i + 1]; pre = series[max(0, i - h + 1 - bw):i - h + 1]
    b = benchmark_of(t, ctx)
    if b == t:
        return {"status": "不適用：這檔本身就是基準指數"}
    bm = dict(ctx["tr"].get(b) or [])
    if any(d not in bm for d, _ in win):
        return {"status": f"資料不足：基準指數 {b} 在下跌區間缺資料"}
    pre_pairs = [(r, bm[d]) for d, r in pre if d in bm]
    beta = _beta([p[0] for p in pre_pairs], [p[1] for p in pre_pairs])
    if beta is None:
        return {"status": "資料不足：無法估計對基準指數的貝他"}
    L = sum(_log(r) for _, r in win)
    Lm = beta * sum(_log(bm[d]) for d, _ in win)
    def peer_average(candidates):
        used, parts = [], []
        for p in sorted(candidates):
            ps = dict(ctx["tr"].get(p) or [])
            if any(d not in ps for d, _ in win): continue
            ppre = [(ps[d], bm[d]) for d, _ in pre if d in ps and d in bm]
            pb = _beta([x[0] for x in ppre], [x[1] for x in ppre])
            if pb is None: continue
            used.append(p); parts.append(sum(_log(ps[d]) - pb * _log(bm[d]) for d, _ in win))
        return used, parts
    used_peers, peer_parts = peer_average(ctx["peers"].get(t, ()))
    peer_scope = "同主線"
    if len(peer_parts) < cfg["min_peers"]:   # 同主線同市場的同業不夠，改用追蹤名單裡同市場的所有公司
        used_peers, peer_parts = peer_average(ctx.get("basket", {}).get(t, ()))
        peer_scope = "同板塊"
    Li = sum(peer_parts) / len(peer_parts) if len(peer_parts) >= cfg["min_peers"] else None
    Lc = L - Lm - (Li or 0.0)
    total = math.exp(L) - 1
    scale = total / L if abs(L) > 1e-12 else 0.0   # 把對數拆解按比例換回一般報酬，三塊加起來等於總跌幅
    sig = _stdev([r for _, r in pre[-60:]])
    flags = []
    acts = ctx["actions"].get(t) or {}
    for d, r in win:
        if d in acts: flags.append(f"{d} 有除權息或分割紀錄")
        if ctx["listing"].get(t) == "TW" and abs(r) > 0.105 and d not in acts:
            flags.append(f"{d} 單日 {r:+.0%} 超過漲跌幅上限且無公司行動紀錄，資料可疑")
        elif ctx["listing"].get(t) == "TW" and r <= -0.095:
            flags.append(f"{d} 接近跌停（{r:+.1%}），實際賣壓可能還沒反映完")
    h_ = hashlib.sha256()
    def feed(name, pairs):
        h_.update(name.encode()); h_.update(json.dumps([[d, round(r, 10)] for d, r in pairs], separators=(",", ":")).encode())
    feed(t, pre + win); feed(b, [(d, bm[d]) for d, _ in pre + win if d in bm])
    for p in used_peers:
        ps = dict(ctx["tr"].get(p) or []); feed(p, [(d, ps[d]) for d, _ in pre + win if d in ps])
    h_.update(json.dumps(sorted((d, a["div"], a["split"]) for d, a in acts.items() if win[0][0] <= d <= end_date)).encode())
    res = {"status": "OK", "ticker": t, "market": ctx["listing"].get(t), "window_days": h, "data_id": h_.hexdigest(),
           "start_date": win[0][0], "end_date": end_date, "total_return": total, "beta": beta, "benchmark": b,
           "benchmark_return": math.exp(sum(_log(bm[d]) for d, _ in win)) - 1,
           "market_part": Lm * scale, "industry_part": None if Li is None else Li * scale, "company_part": Lc * scale,
           "peers": used_peers, "peer_scope": peer_scope if Li is not None else None, "flags": flags,
           "sigma_multiple": None if not sig else total / (sig * math.sqrt(h))}
    res["classification"] = classify(res, cfg)
    return res

def classify(res, cfg):
    if any("資料可疑" in f for f in res["flags"]): return "資料可疑，不歸因"
    total = res["total_return"]
    if total >= 0: return "未下跌"
    parts = {"大盤": res["market_part"], res.get("peer_scope") or "同產業": res["industry_part"], "公司自身": res["company_part"]}
    if res["industry_part"] is None:
        parts = {"大盤": res["market_part"], "公司自身或產業（同業不足，無法區分）": res["company_part"]}
    down = {k: v for k, v in parts.items() if v is not None and v < 0}
    if not down: return "無法歸因"
    k, v = min(down.items(), key=lambda x: x[1])
    share = v / sum(down.values())
    if share >= cfg["dominant_share"]:
        return f"主要是{k}"
    return "混合：" + "、".join(k for k, _ in sorted(down.items(), key=lambda x: x[1]))

def triggered(res, cfg, h):
    if res.get("status") != "OK": return False
    floor = cfg["min_drop"][str(h)]
    sm = res.get("sigma_multiple")
    return res["total_return"] <= floor and sm is not None and sm <= -cfg["sigma_trigger"]

def scan_latest(rules, ctx, tickers=None, force=False):
    cfg = rules["attribution"]; events = []
    for t in sorted(tickers or ctx["tr"]):
        s = ctx["tr"].get(t) or []
        if not s: continue
        end = s[-1][0]
        best = None
        for h in cfg["windows"]:
            r = decompose(t, end, h, ctx, cfg)
            if r.get("status") != "OK":
                if force and best is None: best = dict(r, ticker=t, window_days=h, forced=True)
                continue
            r["triggered"] = triggered(r, cfg, h)
            if r["triggered"] and (best is None or not best.get("triggered") or r["sigma_multiple"] < best["sigma_multiple"]):
                best = r
            elif force and best is None:
                best = r
        if force:
            for h in cfg["windows"]:
                r = decompose(t, end, h, ctx, cfg)
                if r.get("status") == "OK": r["triggered"] = triggered(r, cfg, h)
                else: r = dict(r, ticker=t, window_days=h)
                events.append(r)
        elif best is not None and best.get("triggered"): events.append(best)
    return events

MARKET_CLOSE = {"US": ("America/New_York", 16, 0), "TW": ("Asia/Taipei", 13, 30)}   # 一般交易日的收盤時刻

def _session_close(end_date, market):
    """正式交易日曆有這一天就用它的收盤時刻（可涵蓋提早收盤日）；沒有就回 None。"""
    try:
        import csv
        from data_spine import SESSIONS_PATH, parse_time
        with open(SESSIONS_PATH, encoding="utf-8-sig", newline="") as f:
            hits = [r for r in csv.DictReader(f) if r.get("market") == market and r.get("session_date") == end_date and r.get("close_at")]
        return parse_time(hits[-1]["close_at"], "close_at") if hits else None
    except Exception:
        return None

def event_cutoff(end_date, market):
    """事件當天收盤前一秒。財務體質只用這個時刻（含）之前已申報的財報，收盤當下或盤後申報的一律不用。

    美股用紐約時間 16:00、台股用台北時間 13:30，夏令時間由時區資料處理。
    交易日曆沒有該日時用一般收盤時刻，所以提早收盤日（例如美股半日市 13:00）會放寬到 16:00，這是已知限制。
    不認得的市場用當天 00:00 UTC 之前，寧可少用資料。"""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    close = _session_close(end_date, market)
    if close is None:
        y, m, d = (int(x) for x in end_date.split("-"))
        if market in MARKET_CLOSE:
            tz, hh, mm = MARKET_CLOSE[market]
            close = _dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz))
        else:
            close = _dt.datetime(y, m, d, 0, 0, tzinfo=_dt.timezone.utc)
    return (close - _dt.timedelta(seconds=1)).isoformat()

def health_as_of_event(rules, events):
    """回傳 {(代號, 事件結束日): (燈號, 觸發摘要, 截止時刻)}。

    只用事件結束日收盤前已申報的財報。我們的資料是事後才收錄的，所以這裡以申報時間為準、
    不要求當時已收錄；這代表「當時公開可得」，不代表系統當時真的看得到。
    1.8.1：台股 MOPS 財報在對照表啟用前一律不取用（沒有預覽參數），由 evaluate_issuer 的閘門處理。
    1.8.2：更正鏈選版本同樣以申報時間為準（使用者 2026-10-08 決定），所以在記憶體中把
    selection_written_at 設為 available_at；不使用 canonical 寫入紀錄的實際寫入時間。
    財務體質模組或資料不可用時回空字典，不影響歸因。"""
    outp = {}
    try:
        from health import evaluate_issuer
        from data_spine import read_observations_checked, load_registry, parse_time
        reg = load_registry(); obs = read_observations_checked()   # 1.8.3：不同步時進 except，報告顯示財務體質無法取得
        sec = {x["ticker"]: x for x in reg["securities"]}
        for e in events:
            if e.get("status") != "OK": continue
            x = sec.get(e["ticker"])
            if not x: continue
            cutoff = event_cutoff(e["end_date"], e.get("market"))
            cut = parse_time(cutoff, "cutoff"); usable = []
            for r in obs:
                if r.get("issuer_id") != x["issuer_id"]: continue
                try:
                    if parse_time(r.get("available_at"), "available_at") > cut: continue
                except ValueError: continue
                usable.append(dict(r, ingested_at=r["available_at"], selection_written_at=r["available_at"]))   # 以申報時間為準
            res = evaluate_issuer(x["issuer_id"], usable, rules, cutoff, x.get("security_type") == "ETF")
            trig = [f"{k} {v['status']}" for k, v in (res.get("indicators") or {}).items() if v["status"] in ("留意", "警示")]
            outp[(e["ticker"], e["end_date"])] = (res["status"], "、".join(trig), cutoff)
    except Exception as ex:   # 財務體質是附帶資訊，壞了也要能出歸因報告
        outp["__error__"] = (f"財務體質無法取得：{type(ex).__name__}", "", "")
    return outp

def pct(x): return "—" if x is None else f"{x:+.1%}"

def render(as_of, events, rules, health):
    cfg = rules["attribution"]
    wins = "、".join(str(h) for h in cfg["windows"])
    floors = "；".join("{} 日 {:.0%}".format(h, cfg["min_drop"][str(h)]) for h in cfg["windows"])
    L = [f"# 下跌歸因報告　基準日 {as_of}　狀態：影子模式", "", DISCLAIMER, "",
         f"觸發條件：最近 {wins} 個交易日的跌幅超過門檻（{floors}），而且超過這檔平常波動的 {cfg['sigma_trigger']} 倍。門檻未經驗證。", ""]
    ok = [e for e in events if e.get("status") == "OK"]
    if not ok:
        L += ["目前沒有代號觸發。", ""]
    for e in ok:
        hs = health.get((e["ticker"], e["end_date"]))
        L += [f"## {e['ticker']}：{e['window_days']} 個交易日 {pct(e['total_return'])}（{e['start_date']} 到 {e['end_date']}）"
              + ("" if e.get("triggered", True) else "　未達觸發門檻，僅供參考"), "",
              f"**判讀：{e['classification']}**", "",
              "| 來源 | 貢獻 | 說明 |", "|---|---|---|",
              f"| 大盤 | {pct(e['market_part'])} | 基準 {e['benchmark']} 同期 {pct(e['benchmark_return'])}，這檔的貝他 {e['beta']:.2f} |",
              ("| {} | {} | {} {} 扣掉大盤後的平均 |".format(e["peer_scope"], pct(e["industry_part"]), "同主線同市場的同業" if e["peer_scope"] == "同主線" else "同主線同業不足，改用追蹤名單裡同市場的其他公司：", "、".join(e["peers"])) if e["industry_part"] is not None
               else f"| 同產業 | — | 同市場同主線的同業不足 {cfg['min_peers']} 家，無法區分產業與公司 |"),
              f"| 公司自身 | {pct(e['company_part'])} | 扣掉以上之後剩下的部分 |", "",
              f"相當於這檔平常波動的 {abs(e['sigma_multiple']):.1f} 倍。" if e.get("sigma_multiple") is not None else ""]
        if e["flags"]: L += ["", "資料面注意：" + "；".join(e["flags"]) + "。"]
        L += ["", "事件當天的財務體質（只用當天之前已申報的財報）：" + (f"{hs[0]}" + (f"（{hs[1]}）" if hs[1] else "") if hs else "沒有資料") + "。",
              "接下來該查的：" + next_steps(e), ""]
    bad = [e for e in events if e.get("status") != "OK"]
    if bad:
        L += ["## 無法拆解", ""] + [f"- {e.get('ticker')}：{e['status']}" for e in bad] + [""]
    if "__error__" in health: L += [health["__error__"][0], ""]
    L += ["## 這個方法看不到的", "",
          "- 它只用股價，不知道當天發生了什麼新聞。事件表目前是空的，所以無法自動對上財報或公告。",
          "- 貝他是用下跌之前的資料估的，市場劇烈變動時會失準。",
          "- 同業只有追蹤名單裡同市場的公司，家數很少，「同產業」這一塊誤差大。",
          "- 台股遇到跌停時，當天的跌幅不是全部的賣壓。"]
    return "\n".join(L)

def next_steps(e):
    c = e["classification"]
    if c.startswith("資料可疑"): return "先確認是不是分割或除權息沒登記，補好資料再看。"
    if "大盤" in c and "混合" not in c: return "看市場環境指標（利率、信用利差、半導體指數）有沒有轉壞；公司本身未必有新消息。"
    if ("同主線" in c or "同板塊" in c) and "混合" not in c: return "看這條主線或整個 AI 供應鏈有沒有共同的壞消息（報價、客戶資本支出、同業財測）。"
    if "公司自身" in c: return "查這家公司在這段期間的財報、財測、公告與重大新聞；確認原本看好的理由（營收趨勢、財務體質）有沒有改變。"
    return "大盤、產業、公司都有份，三個方向都要看。"

def write_ledger(events, rules, health, lock_holder=None):
    """同一事件、同一份輸入資料、同一版算法只記一次。資料或算法更正後新增一筆並指向被取代的那一筆，舊的保留。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, LEDGER)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    ver = rules["attribution"].get("derivation_version", "ATTR")
    n = 0
    with file_lock(LEDGER):
        old = []
        if os.path.exists(LEDGER):
            with open(LEDGER, encoding="utf-8-sig", newline="") as f: old = list(csv.DictReader(f))
        superseded = {r["supersedes_event_id"] for r in old if r.get("supersedes_event_id")}
        active = {(r["ticker"], r["end_date"], r["window_days"]): r for r in old if r["event_id"] not in superseded}
        ids = {r["event_id"] for r in old}
        new = not os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(LEDGER_COLS)
            for e in events:
                if e.get("status") != "OK" or not e.get("triggered"): continue
                key = (e["ticker"], e["end_date"], str(e["window_days"]))
                parent = active.get(key)
                if parent and parent["data_id"] == e["data_id"] and parent["derivation_version"] == ver: continue
                eid = f"{e['ticker']}|{e['end_date']}|{e['window_days']}|{ver}|{e['data_id'][:12]}"
                if eid in ids: continue
                hs = health.get((e["ticker"], e["end_date"]))
                w.writerow([eid, e["ticker"], e["market"], e["window_days"], e["start_date"], e["end_date"], f"{e['total_return']:.6f}",
                            f"{e['market_part']:.6f}", "" if e["industry_part"] is None else f"{e['industry_part']:.6f}", f"{e['company_part']:.6f}",
                            e["classification"], f"{e['beta']:.4f}", e["benchmark"], " ".join(e["peers"]), e.get("peer_scope") or "",
                            "" if e.get("sigma_multiple") is None else f"{e['sigma_multiple']:.3f}", "；".join(e["flags"]),
                            hs[0] if hs else "", hs[2] if hs else "", e["data_id"], ver,
                            parent["event_id"] if parent else "", rules["protocol_version"], now])
                ids.add(eid); active[key] = {"event_id": eid, "data_id": e["data_id"], "derivation_version": ver}; n += 1
    return n

def history(rules, ctx):
    """掃整段歷史：每個觸發事件之後 20、60 個交易日的報酬，依判讀分組。同一檔同一個視窗取不重疊的事件。"""
    cfg = rules["attribution"]; rows = []
    for t in sorted(ctx["tr"]):
        s = ctx["tr"][t]; dates = [d for d, _ in s]
        for h in cfg["windows"]:
            i = cfg["min_history_days"] + h
            while i < len(s):
                r = decompose(t, dates[i], h, ctx, cfg)
                if r.get("status") == "OK" and triggered(r, cfg, h) and not r["classification"].startswith("資料可疑"):
                    fwd = {}
                    for f in cfg["follow_up_days"]:
                        if i + f < len(s):
                            v = 1.0
                            for _, x in s[i + 1:i + 1 + f]: v *= 1 + x
                            fwd[f] = v - 1
                    rows.append((h, simplify(r["classification"]), r["market"], fwd))
                    i += h
                else:
                    i += 1
    L = ["這是回補統計：用現在手上的歷史資料，找出過去每一次觸發，再看之後發生什麼。", "",
         "**解讀時必須記得：追蹤名單是事後挑選的熱門股，而且資料只涵蓋 2024 年以來的多頭。**",
         "這段期間幾乎任何下跌之後都會漲回來，所以下面的數字會高估「跌了之後反彈」的機會，不能當成買進依據。",
         "它的用途是比較不同類型的下跌之間有沒有差別，以及累積樣本供日後對照。", "",
         "| 視窗 | 判讀 | 事件數 | " + " | ".join(f"之後 {f} 日平均 | 之後 {f} 日上漲比例 | 之後 {f} 日最差" for f in cfg["follow_up_days"]) + " |",
         "|---|---|---|" + "---|---|---|" * len(cfg["follow_up_days"])]
    g = collections.defaultdict(list)
    for h, c, m, fwd in rows: g[(h, c)].append(fwd)
    for (h, c), xs in sorted(g.items()):
        cells = []
        for f in cfg["follow_up_days"]:
            v = [x[f] for x in xs if f in x]
            cells += ["—", "—", "—"] if not v else [f"{sum(v) / len(v):+.1%}", f"{sum(1 for x in v if x > 0) / len(v):.0%}（{len(v)} 筆）", f"{min(v):+.1%}"]
        L.append(f"| {h} 日 | {c} | {len(xs)} | " + " | ".join(cells) + " |")
    L += ["", f"事件數少於 {cfg['min_events_for_reading']} 的分組不要解讀。"]
    return L, rows

def simplify(c):
    if c.startswith("主要是大盤"): return "主要是大盤"
    if c.startswith("主要是同主線") or c.startswith("主要是同板塊"): return "主要是同主線或同板塊"
    if c.startswith("主要是公司自身"): return "主要是公司自身（含無法區分產業）"
    if c.startswith("混合"): return "混合"
    return c

def main():
    rules = load_rules(); cfg = rules["attribution"]
    lock_holder = sys.argv[sys.argv.index("--lock-holder") + 1] if "--lock-holder" in sys.argv else None
    as_of = sys.argv[sys.argv.index("--as-of") + 1] if "--as-of" in sys.argv else None
    ctx = build_context(rules, as_of)
    if not any(ctx["tr"].values()):
        out("沒有股價資料。請先執行 python 03_工具/fetch_prices.py"); sys.exit(1)
    today = datetime.date.today().isoformat()
    if "--history" in sys.argv:
        L, _ = history(rules, ctx)
        text = "\n".join([f"# 下跌事件回補統計　{today}", ""] + L)
        with open(os.path.join(ROOT, "04_回測", f"下跌事件回補統計_{today}.md"), "w", encoding="utf-8") as f: f.write(text)
        out(text); return
    tickers = None; force = False
    if "--ticker" in sys.argv:
        tickers = [canonical(sys.argv[sys.argv.index("--ticker") + 1], rules)]; force = True
    events = scan_latest(rules, ctx, tickers, force)
    last = max((s[-1][0] for s in ctx["tr"].values() if s), default=today)
    health = health_as_of_event(rules, events) if events else {}
    text = render(as_of or last, events, rules, health)
    if "--no-ledger" not in sys.argv and not force:
        n = write_ledger(events, rules, health, lock_holder=lock_holder)
        text += f"\n\n已記錄 {n} 筆新的下跌事件到 04_回測/下跌事件紀錄_v2.csv（輸入相同不重複；資料有更正時新增一筆並保留舊的）。"
        with open(os.path.join(ROOT, "04_回測", f"下跌歸因報告_{as_of or last}.md"), "w", encoding="utf-8") as f: f.write(text)
    out(text)

if __name__ == "__main__":
    main()
