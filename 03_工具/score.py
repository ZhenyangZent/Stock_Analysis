"""回測：計算每個結論之後相對基準指數的報酬，並依結論與各指標分數分組。
用法：python 03_工具/score.py [--horizons 20,60]
需要 01_共用資料/prices.csv（由 fetch_prices.py 產生），含公司與基準指數的收盤價。
報酬用含息報酬：未還原收盤價加上 corporate_actions.csv 的配息與分割。
結果同時存到 04_回測/回測報告_<今天>.md
"""
import os, sys, csv, glob, datetime, collections
from common import ROOT, load_json, load_rules, out, canonical, is_disabled
from verdict import judge
from pricedata import load_prices, load_actions, tr_index

def fwd_return(series, date, h):
    """從 date 當天或之後第一個交易日起算，h 個交易日後的報酬；資料不夠回傳 None。"""
    idx = next((i for i, (d, _) in enumerate(series) if d >= date), None)
    if idx is None or idx + h >= len(series): return None
    return series[idx + h][1] / series[idx][1] - 1

def main():
    hs = [20, 60]
    if "--horizons" in sys.argv:
        hs = [int(x) for x in sys.argv[sys.argv.index("--horizons") + 1].split(",")]
    ppath = os.path.join(ROOT, "01_共用資料", "prices.csv")
    if not os.path.exists(ppath):
        out("找不到 01_共用資料/prices.csv，無法回測。請先補上收盤價。"); sys.exit(1)
    rules = load_rules()
    acts = load_actions(rules)
    raw = load_prices(rules, ppath)
    px = collections.defaultdict(list, {t: tr_index(v, acts.get(t)) for t, v in raw.items()})  # 含息累積指數
    no_actions = sorted(t for t in raw if t not in acts)
    runs = sorted(glob.glob(os.path.join(ROOT, "02_執行紀錄", "*.json")))
    superseded = {load_json(p).get("supersedes") for p in runs} - {None}
    rows, skipped, no_rules = [], 0, []
    for p in runs:
        if os.path.basename(p) in superseded: continue
        run = load_json(p)
        try:
            run_rules = load_rules(run.get("protocol_version"))  # 用紀錄當時的規則版本，不用目前版重算
        except FileNotFoundError:
            no_rules.append(os.path.basename(p)); continue
        res = judge(run, run_rules)
        bench = {c.get("ticker"): c.get("benchmark") for c in run.get("companies") or []}
        for c in res["companies"]:
            for h in hs:
                r1 = fwd_return(px.get(canonical(c["ticker"], rules), []), run["run_date"], h)
                r0 = fwd_return(px.get(canonical(bench.get(c["ticker"]), rules), []), run["run_date"], h)
                if r1 is None or r0 is None: skipped += 1; continue
                rows.append({"h": h, "verdict": c["verdict"], "excess": r1 - r0, "version": run.get("protocol_version"),
                             "market": res["market"]["light"], **{k: s for k, s in {**c["company_detail"], **c["valuation_detail"]}.items()}})
    L = [f"# 回測報告　{datetime.date.today()}", "",
         "追蹤名單為事後挑選的熱門股；本報告不代表歷史策略績效。", "",
         f"讀取 {len(runs)} 份執行紀錄，可計算 {len(rows)} 筆，因股價資料不足略過 {skipped} 筆。", "",
         "每份紀錄用它當時的規則版本計算結論。" + (f"找不到規則存檔而略過的紀錄：{no_rules}。" if no_rules else ""), "",
         ("沒有除權息紀錄的代號：" + "、".join(no_actions) + "。若期間有配息，這些代號的報酬會被低估。") if no_actions else "", "",
         "超額報酬＝個股含息報酬減基準指數含息報酬。樣本少於 30 筆的分組不要下結論。", ""]
    def table(title, keyfn):
        g = collections.defaultdict(list)
        for r in rows:
            k = keyfn(r)
            if k is not None: g[(r["h"], k)].append(r["excess"])
        L.extend([f"## {title}", "", "| 天數 | 分組 | 筆數 | 平均超額報酬 | 勝過基準比例 |", "|---|---|---|---|---|"])
        for (h, k), xs in sorted(g.items(), key=lambda x: (x[0][0], str(x[0][1]))):
            L.append(f"| {h} | {k} | {len(xs)} | {sum(xs)/len(xs):+.1%} | {sum(x > 0 for x in xs)/len(xs):.0%} |")
        L.append("")
    table("依結論", lambda r: r["verdict"])
    table("依市場燈號", lambda r: r["market"])
    table("依規則版本", lambda r: r["version"])
    active_indicators = list(rules["company"]["indicators"]) + [
        k for k, spec in rules["valuation"]["indicators"].items() if not is_disabled(spec)
    ]
    for ind in active_indicators:
        table(f"依指標 {ind} 的分數", lambda r, ind=ind: r.get(ind))
    text = "\n".join(L)
    os.makedirs(os.path.join(ROOT, "04_回測"), exist_ok=True)
    with open(os.path.join(ROOT, "04_回測", f"回測報告_{datetime.date.today()}.md"), "w", encoding="utf-8") as f:
        f.write(text)
    out(text)

if __name__ == "__main__":
    main()
