"""股價資料的讀取與含息報酬計算。fetch_prices.py、score.py、risk.py 都用這裡，確保算法一致。

prices.csv            date,ticker,security_id,currency,close,volume,source,fetched_at
                      （未還原收盤價；舊格式可讀，新寫入列綁定 security_id 與幣別）
corporate_actions.csv date,ticker,type,value,source,fetched_at     （type：現金股利｜分割｜股票股利；date 是除息或分割生效日）
"""
import csv, os, collections, io, math, re
from common import ROOT, canonical
from data_spine import load_registry, resolve_security

PRICES = os.path.join(ROOT, "01_共用資料", "prices.csv")
ACTIONS = os.path.join(ROOT, "01_共用資料", "corporate_actions.csv")
PRICE_COLS = ["date", "ticker", "security_id", "currency", "close", "volume", "source", "fetched_at"]
ACTION_COLS = ["date", "ticker", "type", "value", "source", "fetched_at"]
ACTION_TYPES = ("現金股利", "分割", "股票股利")


def append_csv_log(path, header, rows, lock_holder=None, writer="pricedata.append_csv_log"):
    """Append arbitrary CSV audit rows through the same staging-first byte gate."""
    if not rows:
        return 0
    from staging_write import append_bytes, validate_csv_bytes
    header_needed = not os.path.exists(path) or os.path.getsize(path) == 0
    buf = io.StringIO(newline=""); out = csv.writer(buf)
    if header_needed: out.writerow(header)
    out.writerows(rows)
    append_bytes(path, buf.getvalue().encode("utf-8"), writer=writer,
                 validator=validate_csv_bytes, lock_holder=lock_holder,
                 extra={"added": len(rows)})
    return len(rows)

def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def load_prices(rules, path=PRICES, return_issues=False, registry=None):
    """讀取價格並驗證身分。身分錯誤預設直接報錯；return_issues=True 時回傳 (prices, issues)。"""
    registry = registry or load_registry()
    px = collections.defaultdict(dict)
    issues = []
    for line_no, r in enumerate(_rows(path), 2):
        try:
            t, d, c = canonical(r["ticker"], rules), r["date"].strip(), float(r["close"])
        except (KeyError, ValueError, AttributeError):
            continue
        if r.get("security_id") or r.get("currency"):
            identity_errors = validate_price_identity(r, rules, registry)
            if identity_errors:
                issues.append({"line": line_no, "date": r.get("date"), "ticker": r.get("ticker"),
                               "errors": identity_errors})
                continue
        if c <= 0 or d in px[t]:
            continue
        try: v = float(r.get("volume") or "")
        except ValueError: v = None
        px[t][d] = (c, v)
    result = {t: [(d, c, v) for d, (c, v) in sorted(m.items())] for t, m in px.items()}
    if issues and not return_issues:
        detail = "；".join(f"line {x['line']} {x['ticker']} {','.join(x['errors'])}" for x in issues[:10])
        raise ValueError(f"PRICE_IDENTITY_ERRORS:{len(issues)}:{detail}")
    return (result, issues) if return_issues else result

def validate_price_identity(row, rules, registry=None):
    """新價格列必須同時符合 ticker、security_id 與幣別；回傳錯誤清單。"""
    errors = []
    ticker = canonical(row.get("ticker"), rules)
    sec, state = resolve_security(ticker, registry or load_registry())
    if state != "OK": return [state]
    if row.get("security_id") != sec["security_id"]:
        errors.append(f"SECURITY_ID_MISMATCH:{row.get('security_id')}!={sec['security_id']}")
    if row.get("currency") != sec["currency"]:
        errors.append(f"CURRENCY_MISMATCH:{row.get('currency')}!={sec['currency']}")
    return errors

def load_actions(rules, path=ACTIONS):
    """回傳 {代號: {日期: {'div': 現金股利合計, 'split': 分割倍數}}}。"""
    acts = collections.defaultdict(dict)
    for r in _rows(path):
        try:
            t, d, typ, v = canonical(r["ticker"], rules), r["date"].strip(), r["type"].strip(), float(r["value"])
        except (KeyError, ValueError, AttributeError):
            continue
        a = acts[t].setdefault(d, {"div": 0.0, "split": 1.0})
        if typ == "現金股利": a["div"] += v
        elif typ in {"分割", "股票股利"} and v > 0: a["split"] *= v
    return acts

def total_returns(series, actions=None):
    """含息日報酬 [(日期, 報酬)]。第 t 天報酬 =（收盤 × 分割倍數 + 現金股利）÷ 前一天收盤 − 1。
    分割倍數＝每 1 舊股換成幾股新股。"""
    actions = actions or {}
    out = []
    for i in range(1, len(series)):
        d, c, _ = series[i]
        a = actions.get(d, {"div": 0.0, "split": 1.0})
        out.append((d, (c * a["split"] + a["div"]) / series[i - 1][1] - 1))
    return out

def tr_index(series, actions=None):
    """含息累積指數 [(日期, 指數)]，第一天為 1。"""
    if not series:
        return []
    idx, level = [(series[0][0], 1.0)], 1.0
    for d, r in total_returns(series, actions):
        level *= 1 + r
        idx.append((d, level))
    return idx

def _validate_new_row(row, cols):
    errors = []
    for field in ("close", "rate", "value", "volume"):
        if field not in cols or row.get(field) in (None, ""):
            continue
        try:
            value = float(row[field])
        except (TypeError, ValueError):
            errors.append(f"{field}:NOT_NUMERIC"); continue
        if not math.isfinite(value):
            errors.append(f"{field}:NON_FINITE")
        if field in {"close", "rate"} and value <= 0:
            errors.append(f"{field}:NON_POSITIVE")
    for field in (name for name in cols if name.endswith("_at")):
        value = row.get(field)
        if value in (None, ""):
            continue
        text = str(value)
        match = re.search(r"\.(\d+)", text)
        if match and len(match.group(1)) > 6:
            errors.append(f"{field}:FRACTION_EXCEEDS_MICROSECONDS")
    if errors:
        raise ValueError("INVALID_APPEND_ROW:" + "|".join(errors))


def append_rows(path, cols, new_rows, key=("date", "ticker"), lock_holder=None, return_details=False):
    """只新增、不改寫。既有鍵值相同的列一律保留舊的；新舊數值不同的列回傳在 conflicts 供人工檢查。
    回傳 (新增筆數, conflicts)。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    old = {}
    for r in _rows(path):
        old[tuple(r.get(k, "").strip() for k in key)] = r
    add, conflicts, reconciliations, seen = [], [], [], set()
    for r in new_rows:
        _validate_new_row(r, cols)
        k = tuple(str(r[c]).strip() for c in key)
        if k in seen:
            continue
        seen.add(k)
        if k in old:
            cmp = [c for c in cols if c not in key and c not in ("source", "fetched_at", "volume", "ingested_at")]
            def num(x):
                try: return round(float(x), 6)
                except (TypeError, ValueError): return str(x).strip()
            differing = [c for c in cmp if c in old[k] and num(old[k][c]) != num(r.get(c, ""))]
            metadata = [c for c in differing if c in {"security_id", "currency"}
                        and not str(old[k].get(c) or "").strip() and str(r.get(c) or "").strip()]
            if differing and set(differing) == set(metadata):
                reconciliations.append({"key": "|".join(k), "status": "METADATA_COMPLETION",
                                        "fields": sorted(metadata), "canonical_changed": False})
            elif differing:
                conflicts.append({"key": "|".join(k), "status": "VALUE_CONFLICT",
                                  "old": {c: old[k].get(c) for c in cmp}, "new": {c: r.get(c, "") for c in cmp},
                                  "source": r.get("source", "")})
            else:
                reconciliations.append({"key": "|".join(k), "status": "RECONFIRMED_NO_VALUE_CHANGE",
                                        "canonical_changed": False})
            continue
        add.append(r)
    if add:
        header_needed = not os.path.exists(path) or os.path.getsize(path) == 0
        old_cols = None
        if not header_needed:
            with open(path, encoding="utf-8-sig", newline="") as f:
                old_cols = next(csv.reader(f), None)
        from staging_write import append_bytes, commit_candidate, validate_csv_bytes
        sorted_add = sorted(add, key=lambda r: tuple(str(r[c]) for c in key))
        if old_cols and old_cols != cols:  # 舊格式（例如只有三欄）：整檔升級成新欄位，內容不變
            rows = _rows(path)
            buf = io.StringIO(newline="")
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore"); w.writeheader()
            for r in rows: w.writerow({c: r.get(c, "") for c in cols})
            for r in sorted_add: w.writerow(r)
            candidate = buf.getvalue().encode("utf-8")
            commit_candidate(path, candidate, writer="pricedata.append_rows.schema_upgrade",
                             validator=validate_csv_bytes, lock_holder=lock_holder,
                             append_only=False, extra={"added": len(add), "key": list(key)})
        else:
            buf = io.StringIO(newline="")
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            if header_needed: w.writeheader()
            for r in sorted_add: w.writerow(r)
            append_bytes(path, buf.getvalue().encode("utf-8"), writer="pricedata.append_rows",
                         validator=validate_csv_bytes, lock_holder=lock_holder,
                         extra={"added": len(add), "key": list(key)})
    return (len(add), conflicts, reconciliations) if return_details else (len(add), conflicts)

def completeness(prices, listing):
    """用「同市場其他代號有交易的日期」當交易日清單，找出每個代號缺的日期。
    只檢查該代號第一筆資料之後、且至少有兩個同市場代號有交易的日期。回傳 {代號: [缺的日期]}。"""
    by_mkt = collections.defaultdict(lambda: collections.Counter())
    for t, s in prices.items():
        for d, _, _ in s: by_mkt[listing.get(t)][d] += 1
    gaps = {}
    for t, s in prices.items():
        if not s: continue
        have = {d for d, _, _ in s}; first = s[0][0]
        miss = sorted(d for d, n in by_mkt[listing.get(t)].items() if n >= 2 and d > first and d not in have)
        if miss: gaps[t] = miss
    return gaps
