"""分範圍共用鎖；未指定 scope 的舊呼叫仍取得全專案鎖。

  python 03_工具/lock.py status
  python 03_工具/lock.py acquire <名稱> "<目的>" [--hours 4] [--scope ai,docs]
  python 03_工具/lock.py release <名稱>
  python 03_工具/lock.py force-release <名稱> "<理由>"
"""
import os, sys, json, datetime, time, uuid, socket, hashlib
from common import ROOT, out

LOCK = os.path.join(ROOT, "協作鎖定.lock")
HIST = os.path.join(ROOT, "協作鎖定_歷史")
SCOPES = ("ai", "docs", "observations", "events", "market", "rules", "snapshot", "queue")
GLOBAL_SCOPES = {"rules", "snapshot", "all", "*"}
MUTEX_STALE_SECONDS = 60.0
MUTEX_WAIT_SECONDS = 10.0

def now(): return datetime.datetime.now().astimezone()

def _expand(scopes=None):
    if scopes is None: return list(SCOPES)
    if isinstance(scopes, str): scopes = [x.strip() for x in scopes.split(",") if x.strip()]
    scopes = list(scopes or [])
    unknown = sorted(set(scopes) - set(SCOPES) - GLOBAL_SCOPES)
    if unknown: raise ValueError("LOCK_SCOPE_INVALID:" + ",".join(unknown))
    if not scopes or set(scopes) & GLOBAL_SCOPES: return list(SCOPES)
    return sorted(set(scopes), key=SCOPES.index)

def _container(people):
    value = {"schema_version": "SCOPED_LOCKS_1", "holders": people}
    if len(people) == 1: value.update(people[0])
    return value

def _load_raw():
    try:
        with open(LOCK, encoding="utf-8") as f: raw = json.load(f)
    except FileNotFoundError: return None
    except (json.JSONDecodeError, OSError):
        return _container([{"who":"不明","scopes":list(SCOPES),"purpose":"鎖定檔無法讀取","start":"","expires":""}])
    if isinstance(raw, dict) and isinstance(raw.get("holders"), list): return _container(raw["holders"])
    if isinstance(raw, dict) and raw.get("who"):
        return _container([{**raw, "scopes":list(SCOPES), "legacy_full_lock":True}])
    return _container([{"who":"不明","scopes":list(SCOPES),"purpose":"鎖定檔格式無效","start":"","expires":""}])

def read(): return _load_raw()
def holders(info=None): return list((info or read() or {}).get("holders") or [])
def holder_present(who, info=None): return any(h.get("who") == who for h in holders(info))

def expired(info):
    try: return now() > datetime.datetime.fromisoformat(info["expires"])
    except (KeyError, ValueError, TypeError): return False

def _sha256_or_absent(path):
    try:
        with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError: return "ABSENT"

def _mutex_info(path=None):
    path = path or LOCK + ".更新中"
    try: stat = os.stat(path)
    except FileNotFoundError: return None
    payload = {}
    try:
        with open(path, encoding="utf-8") as f: payload = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        payload = {}
    created = payload.get("created_at")
    try:
        created_dt = datetime.datetime.fromisoformat(created)
        if created_dt.tzinfo is None: created_dt = created_dt.replace(tzinfo=now().tzinfo)
        age = max(0.0, (now() - created_dt.astimezone()).total_seconds())
    except (TypeError, ValueError):
        created_dt = datetime.datetime.fromtimestamp(stat.st_mtime, tz=now().tzinfo)
        created = created_dt.isoformat(timespec="seconds")
        age = max(0.0, time.time() - stat.st_mtime)
    return {"path":path,"pid":payload.get("pid"),"hostname":payload.get("hostname"),
            "created_at":created,"age_seconds":age}

def _archive_mutex_recovery(info, stale_path, lock_sha_before, lock_sha_after):
    os.makedirs(HIST, exist_ok=True)
    item = {"event":"LOCK_UPDATE_MUTEX_STALE_RECOVERED",
            "recovered_at":now().isoformat(timespec="seconds"),
            "stale_mutex":info,"stale_path":stale_path,
            "holder_lock_sha256_before":lock_sha_before,
            "holder_lock_sha256_after":lock_sha_after,
            "holder_lock_unchanged":lock_sha_before == lock_sha_after}
    dst = os.path.join(HIST, f"{now().strftime('%Y%m%d_%H%M%S_%f')}_互斥檔回收.json")
    with open(dst, "x", encoding="utf-8") as f: json.dump(item, f, ensure_ascii=False, indent=2)
    return dst

class _Mutex:
    def __enter__(self):
        self.path = LOCK + ".更新中"
        self.fd = None
        deadline = time.monotonic() + MUTEX_WAIT_SECONDS
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                payload = {"pid":os.getpid(),"hostname":socket.gethostname(),
                           "created_at":now().isoformat(timespec="microseconds")}
                os.write(self.fd, (json.dumps(payload, ensure_ascii=False)+"\n").encode("utf-8"))
                os.fsync(self.fd)
                return self
            except FileExistsError:
                info = _mutex_info(self.path)
                if info and info["age_seconds"] > MUTEX_STALE_SECONDS:
                    stamp = now().strftime("%Y%m%d_%H%M%S_%f")
                    stale_path = self.path + f".stale.{stamp}.{os.getpid()}"
                    before = _sha256_or_absent(LOCK)
                    try: os.replace(self.path, stale_path)
                    except FileNotFoundError: continue
                    after = _sha256_or_absent(LOCK)
                    _archive_mutex_recovery(info, stale_path, before, after)
                    continue
                if time.monotonic() >= deadline: raise TimeoutError("LOCK_UPDATE_MUTEX_TIMEOUT")
                time.sleep(.05)
            except Exception:
                if self.fd is not None:
                    try: os.close(self.fd)
                    except OSError: pass
                raise
    def __exit__(self, *_):
        try: os.close(self.fd)
        except OSError as e: out(f"警告　LOCK_UPDATE_MUTEX_CLOSE_FAILED:{type(e).__name__}:{e}")
        cleanup_deadline = time.monotonic() + .25
        while True:
            try: os.unlink(self.path); break
            except FileNotFoundError: break
            except OSError as e:
                if time.monotonic() < cleanup_deadline:
                    time.sleep(.01); continue
                out(f"警告　LOCK_UPDATE_MUTEX_CLEANUP_FAILED:{type(e).__name__}:{e}"); break

def _write(people):
    if not people:
        try: os.unlink(LOCK)
        except FileNotFoundError: pass
        return
    temp = LOCK + "." + uuid.uuid4().hex + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(_container(people), f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
    os.replace(temp, LOCK)

def require_holder(who, scope=None):
    if not isinstance(who, str) or not who.strip():
        raise PermissionError("PROJECT_LOCK_HOLDER_REQUIRED：寫入時必須提供持鎖者名稱")
    requested = _expand(scope); info = read()
    if info is None: raise PermissionError("PROJECT_LOCK_REQUIRED：寫入共用資料前須持有專案鎖")
    mine = next((h for h in holders(info) if h.get("who") == who), None)
    if mine is None:
        actual = ",".join(h.get("who", "不明") for h in holders(info))
        raise PermissionError(f"PROJECT_LOCK_HOLDER_MISMATCH：鎖定是 {actual} 的，{who} 不得寫入")
    if expired(mine): raise PermissionError(f"PROJECT_LOCK_EXPIRED：{who} 的鎖已於 {mine.get('expires')} 逾時")
    missing = sorted(set(requested) - set(mine.get("scopes") or []))
    if missing: raise PermissionError("PROJECT_LOCK_SCOPE_MISMATCH：缺少範圍 " + ",".join(missing))
    return mine

def scope_for_path(path):
    target = os.path.normcase(os.path.abspath(path)); root = os.path.normcase(os.path.abspath(ROOT))
    try: rel = os.path.relpath(target, root).replace("\\", "/")
    except ValueError: return None
    if rel == ".." or rel.startswith("../"): return None
    name = os.path.basename(target).lower()
    if rel == "00_規範/自動任務佇列.json": return "queue"
    if rel.startswith("00_規範/"): return "rules"
    if rel == "02_執行紀錄/待抽取清單.json": return "ai"
    if rel.startswith("02_執行紀錄/影子快照/"): return "snapshot"
    if rel.startswith("04_回測/weekly_refresh/"): return "snapshot"
    if rel == "01_共用資料/data_spine/raw/mops/manifest.jsonl": return "docs"
    if name in {"classification_history.jsonl","staging_write_audit_snapshot.jsonl"}: return "snapshot"
    if name in {"ai_extractions.jsonl","ai_field_values.jsonl","ai_field_value_voids.jsonl","ai_reviews.jsonl","staging_write_audit_ai.jsonl"}: return "ai"
    if name in {"docs_ledger.jsonl","staging_write_audit_docs.jsonl"}: return "docs"
    if name in {"observations.jsonl","observation_write_ledger.jsonl","observation_supersede_semantics.jsonl","staging_write_audit_observations.jsonl"}: return "observations"
    if name in {"events.jsonl","event_write_ledger.jsonl","staging_write_audit_events.jsonl"}: return "events"
    if name in {"prices.csv","corporate_actions.csv","macro.csv","macro_realtime.csv","macro_vintages.csv",
                "macro_差異.csv","prices_差異.csv","抓取紀錄.jsonl","market_sessions.csv",
                "calendar_versions.jsonl","fx_rates.csv","fomc_decisions.csv",
                "staging_write_audit_market.jsonl"}: return "market"
    if rel.startswith(("01_共用資料/","02_執行紀錄/","04_回測/")): return "all"
    return None

def require_holder_for_path(who, path, scope=None):
    inferred = scope or scope_for_path(path)
    if inferred is not None or who is not None: return require_holder(who, inferred)
    return None

def acquire(who, purpose, hours=4.0, scopes=None):
    requested = _expand(scopes)
    entry = {"who":who,"scopes":requested,"purpose":purpose,"start":now().isoformat(timespec="seconds"),
             "expires":(now()+datetime.timedelta(hours=hours)).isoformat(timespec="seconds")}
    with _Mutex():
        people = holders()
        if any(h.get("who") == who for h in people): return False, read()
        conflicts = [h for h in people if set(h.get("scopes") or []) & set(requested)]
        if conflicts: return False, _container(conflicts)
        _write(people + [entry])
    return True, entry

def _archive(entry, how, note=""):
    os.makedirs(HIST, exist_ok=True)
    item = {**entry,"released":now().isoformat(timespec="seconds"),"how":how,"note":note}
    dst = os.path.join(HIST, f"{now().strftime('%Y%m%d_%H%M%S_%f')}_{entry.get('who') or '不明'}.json")
    with open(dst, "x", encoding="utf-8") as f: json.dump(item, f, ensure_ascii=False, indent=2)
    return dst

def release(who):
    with _Mutex():
        people=holders(); mine=next((h for h in people if h.get("who")==who),None)
        if mine is None: return False,"目前沒有這個持有者的鎖定"
        _archive(mine,"正常解除"); _write([h for h in people if h.get("who")!=who])
    return True,"已解除"

def force_release(who, reason):
    with _Mutex():
        people=holders(); mine=next((h for h in people if h.get("who")==who),None)
        if mine is None: return False,"目前沒有這個持有者的鎖定"
        if not expired(mine): return False,f"鎖定還沒逾時（到 {mine.get('expires')}），不可強制解除"
        _archive(mine,"使用者授權強制解除",reason); _write([h for h in people if h.get("who")!=who])
    return True,"已強制解除並留下紀錄"

def main():
    a=sys.argv[1:]
    if not a or a[0]=="status":
        people=holders()
        if not people: out("目前沒有鎖定。")
        for h in people: out(f"鎖定中：{h.get('who')}｜範圍 {','.join(h.get('scopes') or [])}｜{h.get('purpose')}｜開始 {h.get('start')}｜期限 {h.get('expires')}"+("｜已逾時" if expired(h) else ""))
        mutex = _mutex_info()
        if mutex:
            state = "可回收" if mutex["age_seconds"] > MUTEX_STALE_SECONDS else "使用中"
            out(f"互斥暫存檔：{state}｜存在 {mutex['age_seconds']:.1f} 秒｜pid {mutex.get('pid') or 'UNKNOWN'}｜主機 {mutex.get('hostname') or 'UNKNOWN'}｜建立 {mutex.get('created_at')}")
        return 0
    if a[0]=="acquire" and len(a)>=3:
        hours=float(a[a.index("--hours")+1]) if "--hours" in a else 4.0
        scope_arg=a[a.index("--scope")+1] if "--scope" in a else None
        ok,info=acquire(a[1],a[2],hours,scope_arg)
        if ok: out(f"已取得鎖定（{','.join(info['scopes'])}），期限 {info['expires']}。改完請執行 release。")
        else: out("取得失敗："+"；".join(f"{h.get('who')}[{','.join(h.get('scopes') or [])}]" for h in holders(info))+" 衝突中。請不要修改該範圍共用檔。")
        return 0 if ok else 1
    if a[0]=="release" and len(a)>=2:
        ok,msg=release(a[1]); out(msg); return 0 if ok else 1
    if a[0]=="force-release" and len(a)>=3:
        ok,msg=force_release(a[1],a[2]); out(msg); return 0 if ok else 1
    out(__doc__); return 1

if __name__=="__main__": sys.exit(main())
