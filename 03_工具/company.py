# -*- coding: utf-8 -*-
"""公司層彙整報告（影子模式）。

用法：python 03_工具/company.py [--ticker 代號] [--no-report] [--current-snapshot] [--preview-pending]

台股財報在對照表啟用前預設不取用（顯示「台股財報待複查啟用」）；--preview-pending 會納入，
報告檔名與內文標明「未經抽查的預覽」。本程式本來就不寫帳本。

把每家公司已有的三個影子模組結果放在同一頁：營收趨勢（trend.py）、財務體質（health.py）、
近期價格變動的拆解（attribution.py）。本程式不新增任何門檻、不打總分、不下結論、不寫帳本，
也不影響 verdict.py。缺的資料照實列出。這是研究輔助，不是投資建議，也不是買賣指令。
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_rules, out
from data_spine import read_jsonl, OBSERVATIONS_PATH, load_registry, read_observations_checked
import trend, health, attribution

HEALTH_NAMES = {"H1": "現金可撐季數", "H2": "淨負債／獲利", "H3": "利息保障", "H4": "短債／現金",
                "H5": "自由現金流", "H6": "流動比率", "H7": "股數稀釋"}

def _pct(x, d=1): return "缺" if x is None else f"{x:+.{d}f}%"
def _pp(x, d=1): return "缺" if x is None else f"{x:+.{d}f} 個百分點"
def _ret(x): return "缺" if x is None else f"{x:+.1%}"

def build(rules, registry=None, observations=None, ctx=None, cutoff=None, current_snapshot_mode=False,
          preview_pending=False, mops_enabled=None):
    """回傳 [{ticker, name, market, trend, health, moves, missing}]，ETF 不列。"""
    registry = registry or load_registry()
    observations = read_observations_checked() if observations is None else observations   # 1.8.3：不同步就停止
    cutoff = cutoff or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if mops_enabled is None:
        mops_enabled = health.mops_gate_enabled()
    tr = trend.evaluate_all(observations, rules, cutoff, registry, current_snapshot_mode, preview_pending, mops_enabled)
    he = {r["issuer_id"]: r for r in health.evaluate_all(observations, rules, cutoff, registry, current_snapshot_mode,
                                                          preview_pending, mops_enabled)}
    ctx = ctx or attribution.build_context(rules)
    moves = {}
    for e in attribution.scan_latest(rules, ctx, force=True):
        moves.setdefault(e["ticker"], {})[e.get("window_days")] = e
    names = {i["issuer_id"]: i.get("issuer_name", "") for i in registry["issuers"]}
    rows = []
    for s in registry["securities"]:
        if s.get("security_type") == "ETF": continue
        t, h, mv = tr.get(s["issuer_id"]), he.get(s["issuer_id"]), moves.get(s["ticker"], {})
        missing = []
        if not t or t.get("C1") is None: missing.append("單季營收年增率")
        if not t or t.get("C2") is None: missing.append("營收年增率比上一季的變動")
        if not t or t.get("C3") is None: missing.append("毛利率與去年同季的變動")
        if not h or h["status"] == "資料不足":
            miss = "、".join(HEALTH_NAMES.get(k, k) for k in (h or {}).get("required_missing", []))
            missing.append("財務體質" + (f"（缺：{miss}）" if miss else ""))
        if not any(e.get("status") == "OK" for e in mv.values()): missing.append("價格")
        missing += ["估值（使用者決定只用歷史估值，資料尚未建立）", "新聞與重大訊息（尚未建立）"]
        unknown_ids = sorted(set((t or {}).get("unknown_observation_ids", [])) | set((h or {}).get("unknown_observation_ids", [])))
        limitations = sorted(set((t or {}).get("limitations", [])) | set(
            flag for flag in (h or {}).get("limitations", []) if flag in health.LIMITATION_TEXT))
        gated = any(((x or {}).get("mops_gate") or {}).get("excluded_observation_count") for x in (t, h))
        preview_used = any((x or {}).get("unreviewed_preview") for x in (t, h))
        rows.append({"ticker": s["ticker"], "name": names.get(s["issuer_id"], ""), "market": s["market"],
                     "current_snapshot_mode": current_snapshot_mode, "unknown_observation_ids": unknown_ids,
                     "limitations": limitations, "pending_gate": gated, "unreviewed_preview": preview_used,
                     "trend": t, "health": h, "moves": mv, "missing": missing})
    return rows

def render_one(r):
    L = [f"## {r['ticker']}　{r['name']}（{r['market']}）", ""]
    t = r["trend"]
    if t and t.get("C1") is not None:
        L.append(f"- 營收趨勢：{t.get('label') or t['status']}。單季營收年增率 {_pct(t['C1'])}；年增率比上一季變動 {_pp(t.get('C2'))}"
                 f"；毛利率比去年同季變動 {_pp(t.get('C3'))}；本季毛利率 {'缺' if t.get('gross_margin_pct') is None else format(t['gross_margin_pct'], '.1f') + '%'}"
                 f"（財報期末 {t.get('latest_period_end')}）。")
    else:
        L.append("- 營收趨勢：資料不足" + (f"（{health.PENDING_REASON}）" if r.get("pending_gate") else "") + "。")
    h = r["health"]
    if h and h["status"] != "資料不足":
        flagged = [f"{HEALTH_NAMES.get(k, k)}＝{v['status']}" for k, v in h["indicators"].items() if v["status"] in ("留意", "警示")]
        stale = "；有指標用的是較舊的財報" if h.get("stale_indicators") or h.get("stale_breaches") else ""
        L.append(f"- 財務體質：{h['status']}" + ("（" + "、".join(flagged) + "）" if flagged else "") + f"{stale}（財報期末 {h.get('latest_period_end')}）。")
    else:
        L.append("- 財務體質：資料不足" + (f"（{health.PENDING_REASON}）" if r.get("pending_gate") else "") + "。")
    ok = [(w, e) for w, e in sorted(r["moves"].items(), key=lambda x: x[0] or 0) if e.get("status") == "OK"]
    if ok:
        L.append(f"- 近期價格（截至 {ok[0][1]['end_date']}，含息報酬，拆解是近似值）：")
        for w, e in ok:
            ind = "同業不足" if e.get("industry_part") is None else _ret(e["industry_part"])
            L.append(f"  - {w} 個交易日 {_ret(e['total_return'])}：大盤 {_ret(e['market_part'])}、{e.get('peer_scope') or '同產業'} {ind}"
                     f"、公司自身 {_ret(e['company_part'])}｜{e['classification']}｜{'已達下跌事件門檻' if e.get('triggered') else '未達下跌事件門檻'}")
    else:
        L.append("- 近期價格：資料不足。")
    L.append("- 還沒有的資料：" + "；".join(r["missing"]) + "。")
    for flag in r.get("limitations") or []:
        L.append(f"- 限制：{health.LIMITATION_TEXT[flag]}（{flag}）。")
    if r.get("unreviewed_preview"):
        L.append(f"- {health.PREVIEW_LABEL}：本公司的台股財報結果尚未通過複查與原文抽查，不是正式結果。")
    if r.get("current_snapshot_mode") and r.get("unknown_observation_ids"):
        L.append("- 限制：僅反映目前已補抓的財報，不能用於歷史重建；POINT_IN_TIME_REPLAY_ALLOWED=false。")
    return "\n".join(L)

def render(as_of, rows, rules):
    head = [f"# 公司層彙整報告　{as_of}　狀態：影子模式", "",
            "這份報告只是把營收趨勢、財務體質、近期價格三個影子模組的結果放在一起，沒有總分、沒有結論，"
            "不是投資建議，也不是買賣指令。各模組的門檻都還沒用本系統的歷史資料驗證。"
            "財報只看得到已申報的數字，看不到表外負債、客戶集中、訂單取消等風險。", "",
            f"規範版本 {rules['protocol_version']}。", "",
            "| 代號 | 營收趨勢 | 財務體質 | 20 日報酬 | 缺幾類資料 |", "|---|---|---|---|---|"]
    for r in rows:
        t, h = r["trend"], r["health"]
        e20 = r["moves"].get(20) or {}
        head.append(f"| {r['ticker']} | {(t or {}).get('label') or '資料不足'} | {(h or {}).get('status', '資料不足')} | "
                    f"{_ret(e20.get('total_return')) if e20.get('status') == 'OK' else '缺'} | {len(r['missing'])} |")
    return "\n".join(head) + "\n\n" + "\n\n".join(render_one(r) for r in rows)

def report_name(as_of, snapshot=False, preview=False):
    return f"公司層彙整報告_{as_of}{'_目前狀況' if snapshot else ''}{'_' + health.PREVIEW_LABEL if preview else ''}.md"

def main():
    rules = load_rules(); as_of = datetime.date.today().isoformat()
    snapshot = "--current-snapshot" in sys.argv
    preview = "--preview-pending" in sys.argv
    if preview and "--as-of" in sys.argv:
        raise ValueError("--preview-pending 與 --as-of 互斥")
    rows = build(rules, current_snapshot_mode=snapshot, preview_pending=preview)
    if "--ticker" in sys.argv:
        want = sys.argv[sys.argv.index("--ticker") + 1].upper()
        rows = [r for r in rows if r["ticker"].upper() == want]
        if not rows: out(f"追蹤名單裡沒有 {want}。"); return
    text = render(as_of, rows, rules)
    if preview:
        text = text.replace("狀態：影子模式", f"狀態：影子模式（{health.PREVIEW_LABEL}）", 1)
        text += (f"\n\n**{health.PREVIEW_LABEL}**：本報告納入台股財報轉接器的待啟用資料（taiwan.status = "
                 f"{health.taiwan_gate_status()}），尚未通過 Codex 複查與 Claude 原文抽查；不寫帳本，不可當作正式結果。")
    if snapshot:
        unknown_ids = sorted({oid for r in rows for oid in r.get("unknown_observation_ids", [])})
        text += "\n\nPOINT_IN_TIME_REPLAY_ALLOWED=false\n\nHISTORICAL_LEDGER_WRITE_ALLOWED=false\n\nunknown_observation_ids: " + str(unknown_ids)
    if "--no-report" not in sys.argv and "--ticker" not in sys.argv:
        with open(os.path.join(ROOT, "04_回測", report_name(as_of, snapshot, preview)), "w", encoding="utf-8") as f: f.write(text)
    out(text)

if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv: out(__doc__)
    else: main()
