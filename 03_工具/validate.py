"""檢查執行紀錄的格式是否符合規範。
用法：python 03_工具/validate.py 02_執行紀錄/檔名.json
"""
import sys, re
from common import load_json, load_rules, STATUS, TYPES, out, canonical, event_state, is_disabled
from data_spine import event_value, resolve_security, validate_schema2_run
from computed_provenance import verify_computed_field, computed_snapshot_consistency

def check_field(path, f, errs, warns):
    if not isinstance(f, dict):
        errs.append(f"{path}：必須是物件"); return
    for k in ("value", "status", "type", "source", "as_of"):
        if k not in f:
            errs.append(f"{path}：缺少 {k}")
    st = f.get("status")
    if st not in STATUS:
        errs.append(f"{path}：status「{st}」不在允許清單")
    if f.get("value") is None:
        if st != "未查":
            errs.append(f"{path}：value 是 null 時 status 必須是「未查」")
        return
    if st == "未查":
        errs.append(f"{path}：有 value 時 status 不可以是「未查」")
    if f.get("type") not in TYPES:
        errs.append(f"{path}：type「{f.get('type')}」不在允許清單")
    if not f.get("source"):
        errs.append(f"{path}：有 value 就必須有 source")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(f.get("as_of") or "")):
        errs.append(f"{path}：as_of 必須是 YYYY-MM-DD")
    if st in ("僅摘要", "轉述"):
        warns.append(f"{path}：查證程度是「{st}」")

def validate(run, rules, inputs=None):
    """inputs：選用的凍結輸入（S1B 影子預覽）。鍵：registry、sessions、events、event_ledger、observations、
    config、calendar_check、snapshot_root。None 時讀正本檔，舊行為不變。"""
    inputs = inputs or {}
    registry = inputs.get("registry")
    errs, warns = [], []
    def reject_uncontracted_computed(prefix, fields):
        for key, field in (fields or {}).items():
            if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
                errs.append(f"{prefix}.{key}：DERIVED_SOURCE_MISMATCH:RULE_CONTRACT_REJECTED")
    for k in ("protocol_version", "run_date", "model", "market", "themes", "companies"):
        if k not in run:
            errs.append(f"缺少頂層欄位 {k}")
    if run.get("protocol_version") != rules["protocol_version"]:
        errs.append(f"規則版本不符：紀錄是 {run.get('protocol_version')}，用來檢查的規則是 {rules['protocol_version']}")
    consistent, consistency_reason = computed_snapshot_consistency(run, rules)
    if not consistent: errs.append("computed_fields：" + consistency_reason)
    schema = run.get("run_schema_version", 1)
    if schema not in (1, 2):
        errs.append(f"run_schema_version 只支援 1 或 2，目前是 {schema}")
    if schema == 1 and "info_cutoff" not in run:
        errs.append("schema 1 缺少頂層欄位 info_cutoff")
    if schema == 2:
        errs.extend(validate_schema2_run(run, rules, registry=registry, sessions=inputs.get("sessions"),
                                         events=inputs.get("events"), observations=inputs.get("observations"),
                                         event_ledger=inputs.get("event_ledger"), config=inputs.get("config"),
                                         calendar_check=inputs.get("calendar_check"),
                                         snapshot_root=inputs.get("snapshot_root"),
                                         current_inputs=inputs.get("canonical_inputs"),
                                         rules_archive_path=inputs.get("rules_archive_path")))
    legacy = run.get("protocol_version") == "1.0.0"  # 1.0.0 的舊紀錄不回頭判錯，只提醒
    def layer(prefix, fields, spec):
        fields = fields or {}
        for key in spec["indicators"]:
            if is_disabled(spec["indicators"][key]):
                f = fields.get(key)
                if isinstance(f, dict) and f.get("value") is not None:
                    warns.append(f"{prefix}.{key}：這個指標已停用，填的值會被忽略，不影響分數與結論")
                continue
            if key not in fields:
                errs.append(f"{prefix}.{key}：缺少這個指標")
            else:
                check_field(f"{prefix}.{key}", fields[key], errs, warns)
                r = spec["indicators"][key]["rule"]
                v = fields[key].get("value") if isinstance(fields[key], dict) else None
                if v is not None and r["type"] == "map" and v not in r["map"]:
                    errs.append(f"{prefix}.{key}：「{v}」不是允許的選項 {list(r['map'])}")
                if v is not None and r["type"] != "map" and (isinstance(v, bool) or not isinstance(v, (int, float))):
                    errs.append(f"{prefix}.{key}：必須是數字")
                elif (v is not None and spec["indicators"][key].get("unit") == "百分比"
                      and not isinstance(v, bool) and isinstance(v, (int, float)) and 0 < abs(v) < 1):
                    warns.append(f"{prefix}.{key}：值是 {v}，單位是百分比數字（379% 要寫 379）。請確認不是把 {v*100:g}% 寫成小數")
        for key in fields:
            if key not in spec["indicators"]:
                errs.append(f"{prefix}.{key}：規範沒有這個指標，不可自行新增")
    layer("market", run.get("market"), rules["market"])
    for field_name, field in (run.get("market") or {}).items():
        if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
            ok, reason = verify_computed_field(run, rules, None, field_name, field,
                                               snapshot_root=inputs.get("snapshot_root"), registry=registry,
                                               layer="market", current_inputs=inputs.get("canonical_inputs"),
                                               rules_archive_path=inputs.get("rules_archive_path"))
            if not ok: errs.append(f"market.{field_name}：{reason}")
    for tid, f in (run.get("themes") or {}).items():
        if tid not in rules["themes"]:
            errs.append(f"themes.{tid}：規範沒有這條主線")
        layer(f"themes.{tid}", f, rules["theme"])
        for field_name, field in (f or {}).items():
            if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
                ok, reason = verify_computed_field(run, rules, None, field_name, field,
                                                   snapshot_root=inputs.get("snapshot_root"), registry=registry,
                                                   layer="theme", theme_id=tid,
                                                   current_inputs=inputs.get("canonical_inputs"),
                                                   rules_archive_path=inputs.get("rules_archive_path"))
                if not ok: errs.append(f"themes.{tid}.{field_name}：{reason}")
    for i, c in enumerate(run.get("companies") or []):
        p = f"companies[{i}]({c.get('ticker')})"
        for k in ("ticker", "name", "theme", "maturity_tier", "benchmark"):
            if c.get(k) in (None, ""):
                errs.append(f"{p}：缺少 {k}")
        if c.get("maturity_tier") not in (1, 2, 3):
            errs.append(f"{p}：maturity_tier 必須是 1、2 或 3")
        canon = canonical(c.get("ticker"), rules)
        if schema == 2:
            _, security_state = resolve_security(c.get("ticker"), registry)
            if security_state != "OK" and not any(security_state in e and f"companies[{i}]" in e for e in errs):
                errs.append(f"{p}：{security_state}")
        distinct = (rules.get("entities") or {}).get("distinct_securities", {})
        if c.get("ticker") in distinct:
            errs.append(f"{p}：{c.get('ticker')} 與 {distinct[c.get('ticker')]} 是不同證券，不可互相代替。{rules['entities'].get('distinct_note', '')}")
        if canon != c.get("ticker"):
            errs.append(f"{p}：代號請用標準代號「{canon}」，不要用「{c.get('ticker')}」")
        if canonical(c.get("benchmark"), rules) != c.get("benchmark"):
            errs.append(f"{p}：benchmark 請用標準代號「{canonical(c.get('benchmark'), rules)}」")
        if schema == 1:
            ev, distance_kind = event_value(c, run)
            kind, _ = event_state(ev, rules["verdict"])
            if kind in ("未填", "無效"):
                msg = f"{p}：event_within_days {'未填' if kind == '未填' else '無效（' + repr(ev) + '）'}，只能是非負整數或「無」；這家公司的結論會是「資料不足」"
                (warns if legacy and kind == "未填" else errs).append(msg)
            if distance_kind != "LEGACY_DECLARED_DISTANCE":
                errs.append(f"{p}：舊版事件距離類型錯誤")
        if c.get("theme") not in (run.get("themes") or {}):
            errs.append(f"{p}：theme「{c.get('theme')}」在本次紀錄沒有對應的主線資料")
        layer(p + ".fields", c.get("fields"), rules["company"])
        for field_name, field in (c.get("fields") or {}).items():
            if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
                ok, reason = verify_computed_field(run, rules, c, field_name, field,
                                                   snapshot_root=inputs.get("snapshot_root"), registry=registry,
                                                   layer="company", current_inputs=inputs.get("canonical_inputs"),
                                                   rules_archive_path=inputs.get("rules_archive_path"))
                if not ok:
                    errs.append(f"{p}.fields.{field_name}：{reason}")
        layer(p + ".valuation", c.get("valuation"), rules["valuation"])
        for field_name, field in (c.get("valuation") or {}).items():
            if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
                ok, reason = verify_computed_field(run, rules, c, field_name, field,
                                                   snapshot_root=inputs.get("snapshot_root"), registry=registry,
                                                   layer="valuation", current_inputs=inputs.get("canonical_inputs"),
                                                   rules_archive_path=inputs.get("rules_archive_path"))
                if not ok:
                    errs.append(f"{p}.valuation.{field_name}：{reason}")
    return errs, warns

if __name__ == "__main__":
    if len(sys.argv) < 2:
        out(__doc__); sys.exit(1)
    run = load_json(sys.argv[1])
    try:
        rules = load_rules(run.get("protocol_version"))
    except FileNotFoundError as e:
        out(f"錯誤　{e}"); sys.exit(1)
    errs, warns = validate(run, rules)
    tickers = [c.get("ticker") for c in run.get("companies") or []]
    for t in sorted({t for t in tickers if tickers.count(t) > 1}, key=str):
        errs.append(f"公司 {t} 重複出現")
    for w in warns: out("提醒　" + w)
    for e in errs: out("錯誤　" + e)
    out(f"\n結果：{len(errs)} 個錯誤、{len(warns)} 個提醒。" + ("格式通過。" if not errs else "請修正後再交付。"))
    sys.exit(1 if errs else 0)
