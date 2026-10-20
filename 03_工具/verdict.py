"""由執行紀錄計算各層分數與每家公司的結論。
用法：python 03_工具/verdict.py 02_執行紀錄/2026-10-05_claude.json [--json]
"""
import sys, json, copy
from common import load_json, load_rules, score_layer, light, val, out, event_state
from data_spine import event_value, load_portfolio, portfolio_gate_status, schema2_run_inputs
from validate import validate

def judge(run, rules, inputs=None):
    """inputs：選用的凍結輸入（見 validate.validate）；另可含 portfolio。None 時讀正本檔，舊行為不變。"""
    validation_errors, validation_warnings = validate(run, rules, inputs)
    inputs = inputs or {}
    v = rules["verdict"]
    m_total, m_detail, m_filled, m_missing = score_layer(run.get("market"), rules["market"])
    m_light = light(m_total, m_filled, rules["market"])
    themes = {}
    for tid, fields in (run.get("themes") or {}).items():
        applicability = (rules["theme"].get("applicability_by_theme") or {})
        policy = applicability.get(tid, applicability.get("default", {}))
        allowed = policy.get("indicators")
        layer_rules = copy.deepcopy(rules["theme"])
        if allowed:
            layer_rules["indicators"] = {k: v for k, v in layer_rules["indicators"].items() if k in allowed}
        layer_rules["min_filled"] = int(policy.get("min_filled", layer_rules["min_filled"]))
        t, d, f, miss = score_layer(fields, layer_rules)
        themes[tid] = {"score": t, "detail": d, "enough": f >= layer_rules["min_filled"], "missing": miss,
                       "min_filled": layer_rules["min_filled"],
                       "not_applicable": sorted(policy.get("not_applicable", []))}
    schema = run.get("run_schema_version", 1)
    derived = []
    if schema == 2:
        try:
            derived = schema2_run_inputs(run, rules, inputs.get("registry"), inputs.get("sessions"), inputs.get("events"),
                                         inputs.get("observations"), inputs.get("event_ledger"), inputs.get("config"),
                                         snapshot_root=inputs.get("snapshot_root"),
                                         current_inputs=inputs.get("canonical_inputs"),
                                         rules_archive_path=inputs.get("rules_archive_path"))
        except Exception as e:
            derived = [{"event_value": None, "event_source": "SCHEMA2_CANONICAL_ERROR",
                        "v1": {"value": None, "status": "DATA_INSUFFICIENT"},
                        "errors": [f"CANONICAL_INPUT_ERROR:{e}"]}
                       for _ in (run.get("companies") or [])]
    pf = (run.get("portfolio") or {}) if schema == 1 else {}
    cap_note = ""
    portfolio_check = {"status": "LEGACY_NOT_EVALUATED", "note": ""}
    if all(isinstance(pf.get(k), (int, float)) for k in ("ai_share", "leverage", "max_drawdown_tolerance")):
        drop = pf["ai_share"] * pf["leverage"] * v["stress_drop"]
        if drop > pf["max_drawdown_tolerance"]:
            cap_note = f"部位已達上限（板塊跌 {v['stress_drop']:.0%} 時總資產跌 {drop:.1%}，超過可接受的 {pf['max_drawdown_tolerance']:.1%}）"
    else:
        if schema == 2:
            portfolio_check = portfolio_gate_status(inputs["portfolio"] if "portfolio" in inputs else load_portfolio())
            cap_note = portfolio_check["note"]
        else:
            cap_note = "部位資料未填，無法檢查曝險上限"
    companies = []
    for i, c in enumerate(run.get("companies") or []):
        company_prefixes = (f"companies[{i}]", f"companies[{i}](")
        company_errors = [e for e in validation_errors if e.startswith(company_prefixes)]
        global_errors = [e for e in validation_errors if not e.startswith("companies[")]
        blocking_errors = global_errors + company_errors
        c_total, c_detail, c_filled, c_miss = score_layer(c.get("fields"), rules["company"])
        valuation = copy.deepcopy(c.get("valuation") or {})
        if schema == 2:
            d = derived[i] if i < len(derived) else {"event_value": None, "event_source": "SCHEMA2_DERIVATION_MISSING",
                                                     "v1": {"value": None}, "errors": ["DERIVATION_OUTPUT_MISSING"]}
            canonical_v1 = d.get("v1") or {}
            valuation["V1"] = {"value": canonical_v1.get("value"),
                               "status": "已開原文" if canonical_v1.get("value") is not None else "未查"}
        v_total, v_detail, v_filled, v_miss = score_layer(valuation, rules["valuation"])
        # The approved alternative valuation may only penalize; its declared
        # method is accepted only after the value itself passes strict replay.
        if (schema == 2 and (d.get("v1") or {}).get("method") in
                set((rules.get("v1_alternative") or {}).get("order") or [])
                and v_detail.get("V1") is not None):
            cap = (d.get("v1") or {}).get("score_cap")
            if isinstance(cap, int) and not isinstance(cap, bool):
                original = v_detail["V1"]
                v_detail["V1"] = min(original, cap)
                v_total += v_detail["V1"] - original
        th = themes.get(c.get("theme"))
        if schema == 2:
            ev, ev_source = d.get("event_value"), d.get("event_source")
        else:
            ev, ev_source = event_value(c, run)
        ev_kind, ev_days = event_state(ev, v)
        rev_status = ((c.get("fields") or {}).get("C1") or {}).get("status")
        accepted = (rules.get("verdict") or {}).get("computed_revenue_accepted") or {}
        computed_revenue_ok = (rev_status == accepted.get("status_code")
                               and "C1" in accepted.get("fields", [])
                               and not any("DERIVED_SOURCE_MISMATCH" in e for e in blocking_errors))
        if blocking_errors:
            if any("DERIVED_SOURCE_MISMATCH" in e for e in blocking_errors):
                verdict, why = "待查證", "來源驗證失敗：" + "；".join(blocking_errors)
            else:
                verdict, why = "資料不足", "格式或輸入驗證失敗：" + "；".join(blocking_errors)
        elif c.get("maturity_tier") not in (1, 2, 3):
            verdict, why = "資料不足", "maturity_tier 缺漏或無效"
        elif c_filled < rules["company"]["min_filled"] or v_filled < rules["valuation"]["min_filled"]:
            verdict, why = "資料不足", f"公司層缺 {c_miss}；估值層缺 {v_miss}"
        elif c.get("maturity_tier") == 3:
            verdict, why = "不行：尚無營收驗證", "成熟度第 3 層"
        elif rev_status != "已開原文" and not computed_revenue_ok:
            verdict, why = "待查證", f"營收數字的查證程度是「{rev_status}」，需要開原文"
        elif ev_kind == "未填":
            verdict, why = "資料不足", "event_within_days 未填：沒查下一個重大事件日，不能略過事件檢查"
        elif ev_kind == "無效":
            verdict, why = "資料不足", f"event_within_days 的值「{ev}」無效（只能是非負整數或「無」）"
        elif ev_kind == "有" and ev_days <= v["event_block_days"]:
            verdict, why = "觀望：事件前", f"重大事件在 {ev_days} 個交易日內"
        elif m_light == "紅" and v_total < 0:
            verdict, why = "不行：環境不利且估值偏高", f"市場紅燈、估值分數 {v_total}"
        elif m_light == "資料不足":
            verdict, why = "無法判定：市場資料不足", f"市場層只填 {m_filled} 項，缺 {m_missing}"
        elif not th or not th["enough"]:
            verdict, why = "無法判定：主線資料不足", ("本次紀錄沒有這條主線的資料" if not th else f"主線層缺 {th['missing']}")
        elif (c_total >= v["company_score_min"] and th["score"] >= v["theme_score_min"]
              and v_total >= v["valuation_score_min"] and m_light in ("綠", "黃")):
            verdict, why = "可列入研究", f"公司 {c_total}、主線 {th['score']}、估值 {v_total}、市場{m_light}"
        else:
            verdict = "等待"
            why = f"公司 {c_total}、主線 {th['score']}、估值 {v_total}、市場{m_light}"
        companies.append({"ticker": c.get("ticker"), "name": c.get("name"), "theme": c.get("theme"),
                          "verdict": verdict, "why": why, "company_score": c_total, "company_detail": c_detail,
                          "valuation_score": v_total, "valuation_detail": v_detail, "cap_note": cap_note,
                          "event_distance_source": ev_source,
                          "canonical_input_errors": d.get("errors", []) if schema == 2 else [],
                          "validation_errors": blocking_errors})
    return {"protocol_version": rules["protocol_version"], "run_date": run.get("run_date"), "model": run.get("model"),
            "market": {"light": m_light, "score": m_total, "filled": m_filled, "detail": m_detail, "missing": m_missing},
            "themes": themes, "companies": companies, "portfolio_check": portfolio_check,
            "validation_warnings": validation_warnings}

def render(r, rules):
    L = [f"# 判斷結果　{r['run_date']}　模型：{r['model']}　規則版本：{r['protocol_version']}", ""]
    m = r["market"]
    L += [f"## 市場環境：{m['light']}（分數 {m['score']}，已填 {m['filled']}/{len(m['detail'])}）", ""]
    for k, s in m["detail"].items():
        L.append(f"- {k} {rules['market']['indicators'][k]['name']}：{'缺漏' if s is None else f'{s:+d}'}")
    L += ["", "## 產業主線", ""]
    for tid, t in r["themes"].items():
        name = rules["themes"].get(tid, {}).get("name", tid)
        L.append(f"- {name}：分數 {t['score']}" + ("" if t["enough"] else f"（資料不足，缺 {t['missing']}）"))
    L += ["", "## 公司", "", "| 代號 | 公司 | 結論 | 原因 |", "|---|---|---|---|"]
    for c in r["companies"]:
        L.append(f"| {c['ticker']} | {c['name']} | {c['verdict']} | {c['why']} |")
    notes = {c["cap_note"] for c in r["companies"] if c["cap_note"]}
    if notes:
        L += ["", "部位檢查：" + "；".join(sorted(notes))]
    portfolio_check = r.get("portfolio_check") or {}
    if portfolio_check.get("status") not in (None, "LEGACY_NOT_EVALUATED"):
        L += ["", f"部位檢查狀態：{portfolio_check['status']}。{portfolio_check.get('note', '')}"]
    off = [f"{k}（{v['name']}）" for k, v in rules.get("valuation", {}).get("indicators", {}).items() if str(v.get("status", "")).startswith("DISABLED")]
    if off:
        L += ["", "已停用的估值指標：" + "、".join(off) + "。不計分，也不影響結論。"]
    caveat = rules.get("valuation", {}).get("caveat") or load_rules().get("valuation", {}).get("caveat")  # 舊版紀錄也要印目前的提醒
    if caveat:
        L += ["", "估值提醒：" + caveat]
    L += ["", "「可列入研究」代表通過所有檢查、值得深入看，不代表應該買進。這不是投資建議。"]
    return "\n".join(L)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        out(__doc__); sys.exit(1)
    run = load_json(sys.argv[1])
    rules = load_rules(run.get("protocol_version"))  # 用這份紀錄當時的規則版本
    result = judge(run, rules)
    out(json.dumps(result, ensure_ascii=False, indent=2) if "--json" in sys.argv else render(result, rules))
