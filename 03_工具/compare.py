"""比對兩份執行紀錄，列出數值不同的欄位（用來找出不同模型的分歧點）。
用法：python 03_工具/compare.py A.json B.json
"""
import sys
from common import load_json, load_rules, out
from verdict import judge

def flat(run):
    d = {}
    for k, f in (run.get("market") or {}).items(): d[f"market.{k}"] = f
    for t, fs in (run.get("themes") or {}).items():
        for k, f in fs.items(): d[f"themes.{t}.{k}"] = f
    for c in run.get("companies") or []:
        for part in ("fields", "valuation"):
            for k, f in (c.get(part) or {}).items(): d[f"{c.get('ticker')}.{k}"] = f
        d[f"{c.get('ticker')}.maturity_tier"] = {"value": c.get("maturity_tier")}
        d[f"{c.get('ticker')}.event_within_days"] = {"value": c.get("event_within_days")}
    return d

if __name__ == "__main__":
    if len(sys.argv) < 3:
        out(__doc__); sys.exit(1)
    a, b = load_json(sys.argv[1]), load_json(sys.argv[2])
    fa, fb = flat(a), flat(b)
    L = [f"# 比對　A：{a.get('model')}　B：{b.get('model')}", "", "| 欄位 | A | B | A 查證程度 | B 查證程度 |", "|---|---|---|---|---|"]
    n = 0
    for k in sorted(set(fa) | set(fb)):
        va, vb = (fa.get(k) or {}).get("value"), (fb.get(k) or {}).get("value")
        if va != vb:
            n += 1
            L.append(f"| {k} | {va} | {vb} | {(fa.get(k) or {}).get('status','')} | {(fb.get(k) or {}).get('status','')} |")
    L.append(f"\n共 {n} 個欄位不同。")
    rules = load_rules()
    ra, rb = judge(a, rules), judge(b, rules)
    L += ["", f"市場燈號　A：{ra['market']['light']}　B：{rb['market']['light']}", "", "| 代號 | A 結論 | B 結論 |", "|---|---|---|"]
    vb_map = {c["ticker"]: c["verdict"] for c in rb["companies"]}
    for c in ra["companies"]:
        L.append(f"| {c['ticker']} | {c['verdict']} | {vb_map.get(c['ticker'], '（無）')} |")
    out("\n".join(L))
