"""共用函式：讀規則、計分。所有工具都用這裡的邏輯，確保結果一致。"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(ROOT, "00_規範", "rules.json")
RULES_ARCHIVE = os.path.join(ROOT, "00_規範", "rules_歷史")
STATUS = {"已開原文", "僅摘要", "轉述", "未查", "OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}
TYPES = {"實績", "財測", "共識", "第三方估計", "傳聞", "推論"}

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_rules(version=None):
    """讀規則。version 給了就讀那一版（目前版或 rules_歷史/ 裡的存檔）。
    找不到那一版時丟出 FileNotFoundError，不會偷偷改用目前版。"""
    current = load_json(RULES_PATH)
    if version is None or version == current["protocol_version"]:
        return current
    path = os.path.join(RULES_ARCHIVE, f"rules_{version}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到規則版本 {version}（應在 00_規範/rules_歷史/rules_{version}.json）")
    return load_json(path)

def canonical(ticker, rules):
    """把同一檔證券的不同寫法轉成標準代號（例如 2408.TW → 2408）；不是別名就原樣回傳。
    不同證券（例如 2330 與 TSM）不會互轉。"""
    if not isinstance(ticker, str):
        return ticker
    return (rules.get("entities") or {}).get("aliases", {}).get(ticker.strip(), ticker.strip())

def val(field):
    """取出欄位的 value；欄位不存在或未查時回傳 None。"""
    if not isinstance(field, dict):
        return None
    return field.get("value")

def score_one(value, rule):
    """單一指標計分：+1、0、-1；value 為 None 時回傳 None（缺漏）。"""
    if value is None:
        return None
    t = rule["type"]
    if t == "map":
        return rule["map"].get(value) if isinstance(value, str) else None  # 選項不在表內視為缺漏
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
        return None
    if t == "high_bad":
        return -1 if value > rule["bad_above"] else (1 if value < rule["good_below"] else 0)
    if t == "high_good":
        return 1 if value > rule["good_above"] else (-1 if value < rule["bad_below"] else 0)
    raise ValueError(f"未知的規則類型：{t}")

def is_disabled(spec):
    return str(spec.get("status", "")).startswith("DISABLED")

def score_layer(fields, layer_rules):
    """回傳 (總分, 各指標分數, 已填數, 缺漏清單)。
    停用的指標完全不參與：不計分、不算已填、不列缺漏，分數明細裡也不出現。"""
    detail, missing = {}, []
    for key, spec in layer_rules["indicators"].items():
        if is_disabled(spec):
            continue
        s = score_one(val((fields or {}).get(key)), spec["rule"])
        detail[key] = s
        if s is None:
            missing.append(key)
    filled = len(detail) - len(missing)
    total = sum(s for s in detail.values() if s is not None)
    return total, detail, filled, missing

def light(total, filled, layer_rules):
    if filled < layer_rules["min_filled"]:
        return "資料不足"
    if total >= layer_rules["green_at"]:
        return "綠"
    if total <= layer_rules["red_at"]:
        return "紅"
    return "黃"

def event_state(ev, vrules):
    """事件欄位的四種狀態：('未填'|'無效'|'無'|'有', 天數或 None)。
    合法值只有：非負整數（距離下一個重大事件的交易日數），或字串「無」（查過，近期沒有）。"""
    if ev is None:
        return "未填", None
    if ev == vrules.get("event_none_value", "無"):
        return "無", None
    if isinstance(ev, bool) or not isinstance(ev, int) or ev < 0:
        return "無效", None
    return "有", ev

class file_lock:
    """短時間的寫入鎖：寫同一個檔案的兩個程式不會交錯。拿不到就等，逾時丟錯，不會硬寫。"""
    def __init__(self, path, wait=20.0, stale=120.0):
        self.lock, self.wait, self.stale = path + ".寫入中", wait, stale
    def __enter__(self):
        import time
        t0 = time.time()
        while True:
            try:
                os.close(os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock) > self.stale:  # 上一個程式當掉留下的
                        os.replace(self.lock, self.lock + ".逾時")
                        continue
                except OSError:
                    pass
                if time.time() - t0 > self.wait:
                    raise TimeoutError(f"另一個程式正在寫 {os.path.basename(self.lock[:-4])}，等了 {self.wait:.0f} 秒仍未結束")
                time.sleep(0.2)
    def __exit__(self, *a):
        import time, uuid
        # 每次使用唯一完成檔，避免 Windows 在覆寫既有「.完成」檔時短暫拒絕存取。
        completed = self.lock + f".完成.{os.getpid()}.{uuid.uuid4().hex}"
        deadline = time.monotonic() + 1.0
        while True:
            try:
                os.replace(self.lock, completed)   # 用搬移代替刪除（有些環境不允許刪檔）
                return False
            except FileNotFoundError:
                return False
            except OSError as exc:
                if time.monotonic() < deadline:
                    time.sleep(.02)
                    continue
                if a and a[1] is not None and hasattr(a[1], "add_note"):
                    a[1].add_note(f"FILE_LOCK_CLEANUP_FAILED:{type(exc).__name__}:{exc}")
                    return False
                raise RuntimeError(f"FILE_LOCK_CLEANUP_FAILED:{type(exc).__name__}:{exc}") from exc

def out(text):
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
