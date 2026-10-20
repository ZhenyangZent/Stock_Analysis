"""營收趨勢（影子模式）：由程式從已申報的財報數字算出公司層的 C1、C2、C3 與主線層的 I1。
它回答「這家公司的營收在加速還是減速、毛利率在變好還是變差」，不影響 verdict.py 的結論。

  python 03_工具/trend.py                     用現在已收錄的財報計算，存報告並記錄
  python 03_工具/trend.py --as-of 2026-09-30   指定基準日（只用當時已申報、已收錄的數字）
  python 03_工具/trend.py --no-ledger          只看結果
  python 03_工具/trend.py --preview-pending    納入尚未啟用的台股財報做「未經抽查的預覽」，一律不寫帳本
                                               （可加 --current-snapshot；不可與 --as-of 並用）

需要 observations.jsonl 裡有單季營收（FIN_REVENUE）與單季毛利（FIN_GROSS_PROFIT）。
C4（財測相對共識）、C5（一次性項目）、C6（歸因等級）需要閱讀與判斷，仍由人或模型查證後填寫。
"""
import os, sys, csv, json, hashlib, datetime, collections
from common import ROOT, load_rules, score_one, canonical, out, file_lock
from data_spine import OBSERVATIONS_PATH, load_registry, read_jsonl, read_observations_checked
from health import (point_in_time, current_snapshot, consistent_report_scope, _latest_by_period, _iso_date,
                    mops_gate_enabled, gate_pending_sources, excluded_backfill_periods, scope_limitations,
                    taiwan_gate_status, PENDING_REASON, PREVIEW_LABEL, BACKFILL_EXCLUDED_REASON, LIMITATION_TEXT)

LEDGER = os.path.join(ROOT, "04_回測", "營收趨勢紀錄.csv")
LEDGER_COLS = ["record_id", "as_of", "issuer_id", "ticker", "status", "label", "latest_period_end", "data_id",
               "c1_yoy_pct", "c2_accel_pt", "c3_gm_change_pt", "scores_json", "protocol_version",
               "supersedes_record_id", "written_at"]
DISCLAIMER = ("這是影子模式：只整理已申報的營收與毛利數字，不是買賣建議。"
              "營收加速不代表股價會漲；股價可能早已反映，也可能有併購或一次性因素，這些程式看不出來。")

def _same_quarter_last_year(rows_by_end, end):
    """找去年同一季：期末相差 350 到 380 天。會計年度 52／53 週的公司也落在這個範圍。"""
    d = _iso_date(end)
    cands = [(abs((d - _iso_date(e)).days - 365), e) for e in rows_by_end if 350 <= (d - _iso_date(e)).days <= 380]
    return min(cands)[1] if cands else None

def _prev_quarter(rows_by_end, end):
    d = _iso_date(end)
    cands = [(abs((d - _iso_date(e)).days - 91), e) for e in rows_by_end if 80 <= (d - _iso_date(e)).days <= 105]
    return min(cands)[1] if cands else None

def _backfill_note(excluded, wanted):
    """1.8.1（C180-05）：缺的那一期若是被嚴格取用排除的回補資料，原因要寫明。"""
    hit = [p for p in excluded if p in wanted]
    return f"（{'、'.join(hit)} {BACKFILL_EXCLUDED_REASON}）" if hit else ""

def evaluate_issuer(issuer_id, observations, rules, cutoff, is_etf=False, current_snapshot_mode=False,
                    preview_pending=False, mops_enabled=None):
    cfg = rules["trend"]
    res = {"issuer_id": issuer_id, "status": "資料不足", "label": "資料不足", "latest_period_end": None,
           "C1": None, "C2": None, "C3": None, "scores": {"C1": None, "C2": None, "C3": None}, "reasons": []}
    if is_etf:
        res.update(status="不適用", label="不適用"); return res
    if mops_enabled is None:
        mops_enabled = mops_gate_enabled()
    issuer_rows = [r for r in observations if r.get("issuer_id") == issuer_id]
    issuer_rows, gated_count = gate_pending_sources(issuer_rows, mops_enabled, preview_pending)
    res["mops_gate"] = {"enabled": bool(mops_enabled), "preview_pending": bool(preview_pending),
                        "excluded_observation_count": gated_count}
    rows = current_snapshot(issuer_rows, issuer_id, cutoff) if current_snapshot_mode else point_in_time(issuer_rows, issuer_id, cutoff)
    rows = consistent_report_scope(rows)
    unknown_ids = sorted(r.get("observation_id") for r in rows if r.get("availability_status") == "BACKFILLED_UNKNOWN")
    preview_used = bool(preview_pending and not mops_enabled and any(r.get("source_system") == "MOPS_XBRL" for r in rows))
    res["unknown_observation_ids"] = unknown_ids
    res["unreviewed_preview"] = preview_used
    res["point_in_time_replay_allowed"] = not bool(unknown_ids)
    res["historical_ledger_write_allowed"] = not bool(unknown_ids) and not preview_used
    for flag in scope_limitations(rows):
        res.setdefault("limitations", []).append(flag)
    excluded = [] if current_snapshot_mode else excluded_backfill_periods(issuer_rows, issuer_id, {"FIN_REVENUE", "FIN_GROSS_PROFIT"})
    res["backfill_excluded_periods"] = excluded
    rev = {r["period_end"]: float(r["value"]) for r in _latest_by_period(rows, "FIN_REVENUE", "Q")}
    gp = {r["period_end"]: float(r["value"]) for r in _latest_by_period(rows, "FIN_GROSS_PROFIT", "Q")}
    if gated_count and not rev:
        res["reasons"].append(PENDING_REASON); return res
    if not rev:
        res["reasons"].append("沒有單季營收資料" + _backfill_note(excluded, excluded)); return res
    end = max(rev); res["latest_period_end"] = end
    if (_iso_date(cutoff) - _iso_date(end)).days > cfg["stale_after_days"]:
        res["reasons"].append(f"最近一季（{end}）距基準日超過 {cfg['stale_after_days']} 天"); return res
    def yoy(e):
        ly = _same_quarter_last_year(rev, e)
        if not ly or rev[ly] <= 0: return None
        return (rev[e] / rev[ly] - 1) * 100
    def last_year_window(e):
        return [p for p in excluded if 350 <= (_iso_date(e) - _iso_date(p)).days <= 380]
    c1 = yoy(end)
    if c1 is None:
        res["reasons"].append("找不到去年同季的營收，無法計算年增率" + _backfill_note(excluded, last_year_window(end))); return res
    res["C1"] = c1
    prev = _prev_quarter(rev, end); prev_yoy = yoy(prev) if prev else None
    if prev_yoy is None:
        wanted = [p for p in excluded if 80 <= (_iso_date(end) - _iso_date(p)).days <= 105] + (last_year_window(prev) if prev else [])
        res["reasons"].append("上一季的年增率算不出來，無法判斷加速或減速" + _backfill_note(excluded, wanted))
    else: res["C2"] = c1 - prev_yoy
    ly = _same_quarter_last_year(rev, end)
    if end in gp and ly in gp and rev[end] > 0 and rev[ly] > 0:
        res["C3"] = (gp[end] / rev[end] - gp[ly] / rev[ly]) * 100
        res["gross_margin_pct"] = gp[end] / rev[end] * 100
    else:
        res["reasons"].append("缺本季或去年同季的毛利，無法計算毛利率變化" + _backfill_note(excluded, [end] + last_year_window(end)))
    ind = rules["company"]["indicators"]
    res["scores"] = {k: score_one(res[k], ind[k]["rule"]) for k in ("C1", "C2", "C3")}
    res["status"] = "已計算" if res["C2"] is not None else "部分計算"
    res["label"] = label(res, cfg)
    return res

def label(res, cfg):
    c1, c2 = res["C1"], res["C2"]
    if c1 is None: return "資料不足"
    if c1 < 0: return "衰退" if c2 is None or c2 <= 0 else "衰退但跌幅收斂"
    if c2 is None: return "成長（無法判斷加速或減速）"
    if c2 > cfg["flat_band_pt"]: return "加速成長"
    if c2 < -cfg["flat_band_pt"]: return "成長但減速"
    return "穩定成長"

def evaluate_all(observations, rules, cutoff, registry=None, current_snapshot_mode=False, preview_pending=False,
                 mops_enabled=None):
    registry = registry or load_registry()
    if mops_enabled is None:
        mops_enabled = mops_gate_enabled()
    etf = {s["issuer_id"] for s in registry["securities"] if s.get("security_type") == "ETF"}
    return {i["issuer_id"]: evaluate_issuer(i["issuer_id"], observations, rules, cutoff, i["issuer_id"] in etf, current_snapshot_mode,
                                            preview_pending, mops_enabled)
            for i in registry["issuers"]}

def theme_breadth(results, rules, registry):
    """I1：追蹤名單中營收年增率比上一期加速的公司佔比。算得出 C2 的公司不到名單一半時回資料不足。"""
    by_ticker = {s["ticker"]: s["issuer_id"] for s in registry["securities"]}
    outp = {}
    for tid, th in rules["themes"].items():
        members = [canonical(x, rules) for x in th["watchlist"]]
        known = [(t, results.get(by_ticker.get(t))) for t in members]
        have = [(t, r) for t, r in known if r and r["C2"] is not None]
        need = max(1, (len(members) + 1) // 2)
        if len(have) < need:
            outp[tid] = {"name": th["name"], "value": None, "score": None, "n_have": len(have), "n_total": len(members),
                         "reason": f"名單 {len(members)} 家只有 {len(have)} 家算得出加速或減速，不到一半"}
            continue
        v = 100.0 * sum(1 for _, r in have if r["C2"] > 0) / len(have)
        outp[tid] = {"name": th["name"], "value": v, "score": score_one(v, rules["theme"]["indicators"]["I1"]["rule"]),
                     "n_have": len(have), "n_total": len(members),
                     "accelerating": [t for t, r in have if r["C2"] > 0], "decelerating": [t for t, r in have if r["C2"] <= 0],
                     "missing": [t for t, r in known if not r or r["C2"] is None], "reason": ""}
    return outp

def fmt(x, unit="%", d=1): return "—" if x is None else f"{x:+.{d}f}{unit}"
def sc(x): return "缺" if x is None else format(x, "+d")

def explain(r):
    """表格「說明」欄：原因加上機器可讀限制的中文。"""
    notes = list(r["reasons"]) + [LIMITATION_TEXT[x] for x in r.get("limitations") or [] if x in LIMITATION_TEXT]
    return "；".join(notes)

def result_data_id(result):
    """同一期資料後補或更正時必須產生不同識別碼，避免舊帳本列擋住新結果。"""
    payload = {k: result.get(k) for k in ("status", "label", "latest_period_end", "C1", "C2", "C3", "gross_margin_pct", "scores")}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()

def render(as_of, results, themes, rules, registry):
    names = {i["issuer_id"]: i["issuer_name"] for i in registry["issuers"]}
    tick = collections.defaultdict(list)
    for s in registry["securities"]:
        if s.get("price_series_enabled"): tick[s["issuer_id"]].append(s["ticker"])
    L = [f"# 營收趨勢報告　基準日 {as_of}　狀態：影子模式", "", DISCLAIMER, "",
         "## 公司", "", "| 公司 | 趨勢 | 最近一季 | 營收年增率（C1） | 年增率較上一季（C2） | 毛利率年變化（C3） | 分數 C1／C2／C3 | 說明 |", "|---|---|---|---|---|---|---|---|"]
    for iid, r in results.items():
        if r["status"] == "不適用": continue
        s = r["scores"]
        L.append(f"| {'、'.join(tick.get(iid) or [])} {names.get(iid, iid)} | {r['label']} | {r['latest_period_end'] or '—'} | {fmt(r['C1'])} | {fmt(r['C2'], ' 個百分點')} | {fmt(r['C3'], ' 個百分點')} | {sc(s['C1'])}／{sc(s['C2'])}／{sc(s['C3'])} | {explain(r)} |")
    L += ["", "## 主線：營收加速的公司佔比（I1）", "", "| 主線 | 加速佔比 | 分數 | 加速 | 未加速 | 沒有資料 | 說明 |", "|---|---|---|---|---|---|---|"]
    for tid, t in themes.items():
        L.append(f"| {t['name']} | {'—' if t['value'] is None else format(t['value'], '.0f') + '%'} | {sc(t['score'])} | {'、'.join(t.get('accelerating', [])) or '—'} | {'、'.join(t.get('decelerating', [])) or '—'} | {'、'.join(t.get('missing', [])) or '—'} | {t['reason'] or '{} 家中 {} 家有資料'.format(t['n_total'], t['n_have'])} |")
    L += ["", "## 這份報告看不到的", "",
          "- 成長是不是來自併購或一次性項目（C5）、財測相對市場預期（C4）、營收有多少真的來自 AI（C6）。這三項仍要人或模型查證。",
        taiwan_scope_note(),
          "- 門檻（年增 20% 以上算好、年增率下滑 10 個百分點以上算差等）是一開始憑經驗定的，沒有驗證過。",
          "- 各公司會計年度不同，「最近一季」不是同一段日曆期間。"]
    return "\n".join(L)

def taiwan_scope_note(tag_map_path=None):
    """1.8.6（C185-01）：限制文字依 financial_tag_map.json 的 taiwan.status 切換，不寫死。"""
    import health
    tail = "更補正舊季若只有回補時間，只能用於目前狀況，不能用於歷史回放。短期投資與稀釋股數仍缺資料，不得反推或當零。"
    if health.mops_gate_enabled(tag_map_path):
        return "- 台股七家公司的財報來自官方 MOPS XBRL，已通過獨立複查與原文抽查並正式取用；3081 只有個別財報，不與合併報表直接比較。" + tail
    return ("- 台股七家公司已由官方 MOPS XBRL 匯入，但在複查與原文抽查通過、對照表啟用前，預設不取用（顯示「台股財報待複查啟用」）；"
            "只有 --preview-pending 會納入，且一律不寫帳本。" + tail)

def write_ledger(as_of, results, registry, rules, lock_holder=None):
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, LEDGER)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tick = {}
    for s in registry["securities"]: tick.setdefault(s["issuer_id"], s["ticker"])
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds"); n = 0
    with file_lock(LEDGER):
        active = {}
        if os.path.exists(LEDGER):
            with open(LEDGER, encoding="utf-8-sig", newline="") as f:
                old_rows = list(csv.DictReader(f))
            superseded = {r.get("supersedes_record_id") for r in old_rows if r.get("supersedes_record_id")}
            for row in old_rows:
                if row.get("record_id") in superseded:
                    continue
                key = (row.get("as_of"), row.get("issuer_id"), row.get("latest_period_end"), row.get("protocol_version"))
                active[key] = row
        new = not os.path.exists(LEDGER)
        with open(LEDGER, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(LEDGER_COLS)
            for iid, r in results.items():
                if r["C1"] is None: continue
                key = (as_of, iid, r["latest_period_end"] or "", rules["protocol_version"])
                data_id = result_data_id(r)
                parent = active.get(key)
                if parent and parent.get("data_id") == data_id: continue
                record_id = "TREND_" + hashlib.sha256("|".join(key + (data_id,)).encode()).hexdigest()
                w.writerow([record_id, as_of, iid, tick.get(iid, ""), r["status"], r["label"], r["latest_period_end"], data_id,
                            f"{r['C1']:.4f}", "" if r["C2"] is None else f"{r['C2']:.4f}", "" if r["C3"] is None else f"{r['C3']:.4f}",
                            json.dumps(r["scores"]), rules["protocol_version"], parent.get("record_id", "") if parent else "", now]); n += 1
                active[key] = {"record_id": record_id, "data_id": data_id}
    return n

def output_plan(as_of, snapshot=False, preview=False, no_ledger=False):
    """決定是否寫帳本與報告檔名。--preview-pending 一律不寫帳本（沒加 --no-ledger 也一樣）。"""
    if preview:
        return {"write_ledger": False,
                "report_name": f"營收趨勢報告_{as_of}{'_目前狀況' if snapshot else ''}_{PREVIEW_LABEL}.md"}
    if snapshot or no_ledger:
        return {"write_ledger": False, "report_name": None}
    return {"write_ledger": True, "report_name": f"營收趨勢報告_{as_of}.md"}

def main():
    rules = load_rules(); registry = load_registry()
    lock_holder = sys.argv[sys.argv.index("--lock-holder") + 1] if "--lock-holder" in sys.argv else None
    explicit = "--as-of" in sys.argv
    snapshot = "--current-snapshot" in sys.argv
    preview = "--preview-pending" in sys.argv
    if explicit and snapshot:
        raise ValueError("--current-snapshot 與 --as-of 互斥")
    if explicit and preview:
        raise ValueError("--preview-pending 與 --as-of 互斥")
    as_of = sys.argv[sys.argv.index("--as-of") + 1] if explicit else datetime.date.today().isoformat()
    # 指定歷史基準日時只用當時已收錄的財報；看最新狀況時用現在已收錄的全部財報
    cutoff = as_of + "T23:59:59+00:00" if explicit else datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    plan = output_plan(as_of, snapshot, preview, "--no-ledger" in sys.argv)
    results = evaluate_all(read_observations_checked(), rules, cutoff, registry, snapshot, preview)   # 1.8.3：不同步就停止
    themes = theme_breadth(results, rules, registry)
    text = render(as_of, results, themes, rules, registry)
    if preview:
        text = text.replace("狀態：影子模式", f"狀態：影子模式（{PREVIEW_LABEL}）", 1)
        text = text.replace("\n\n", f"\n\n**{PREVIEW_LABEL}**：本報告納入台股財報轉接器的待啟用資料（taiwan.status = "
                            f"{taiwan_gate_status()}），尚未通過 Codex 複查與 Claude 原文抽查，只供複查者與使用者檢視；"
                            "不寫任何帳本，不可當作正式結果。\n\n", 1)
    unknown_ids = sorted({oid for r in results.values() for oid in r.get("unknown_observation_ids", [])})
    if snapshot:
        text = text.replace("基準日", "目前狀況基準日", 1)
        text += "\n\nPOINT_IN_TIME_REPLAY_ALLOWED=false\n\nHISTORICAL_LEDGER_WRITE_ALLOWED=false\n\n僅反映目前已補抓的財報，不能用於歷史重建。\n\nunknown_observation_ids: " + json.dumps(unknown_ids)
    if not any(r["C1"] is not None for r in results.values()):
        text += "\n\n目前沒有任何公司有單季營收資料。需要先把營收與毛利加進財報抓取的項目並重新匯入。"
    if preview:
        text += f"\n\nHISTORICAL_LEDGER_WRITE_ALLOWED=false\n\n{PREVIEW_LABEL}；不寫帳本。"
        with open(os.path.join(ROOT, "04_回測", plan["report_name"]), "w", encoding="utf-8") as f: f.write(text)
        text += f"\n\n預覽報告已存為 04_回測/{plan['report_name']}。"
    if plan["write_ledger"] and "--help" not in sys.argv and "-h" not in sys.argv:
        n = write_ledger(as_of, results, registry, rules, lock_holder=lock_holder)
        text += f"\n\n本次新增 {n} 筆到 04_回測/營收趨勢紀錄.csv；重複資料不新增，帳本既有有效紀錄保留。"
        with open(os.path.join(ROOT, "04_回測", plan["report_name"]), "w", encoding="utf-8") as f: f.write(text)
    out(text)

if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv: out(__doc__)
    else: main()
