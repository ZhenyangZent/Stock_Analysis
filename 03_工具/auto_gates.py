"""自動迴圈的硬檢查（規格：90_各AI工作區/claude/自動迴圈規格_v0.1_2026-10-15.md 第 3～5 節）。

這個檔案只負責「判斷有沒有違規」，不執行任務。每個檢查回傳 {"name", "ok", "detail"}；
任何一項 ok=False，迴圈就把任務標 FAILED 並停止。測試用假的執行者時，所有檢查都照常執行。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 追蹤範圍
# ---------------------------------------------------------------------------
# 只能往後追加的正式帳本：新檔開頭必須和舊檔逐位元組相同，而且不能消失。
APPEND_ONLY_GLOBS = (
    "01_共用資料/data_spine/*.jsonl", "01_共用資料/data_spine/*.csv",
    "01_共用資料/data_spine/ai_field_value_voids.jsonl",     # T18：欄位值作廢帳本（上一行已涵蓋，明列以免日後改窄萬用字元時漏掉）
    "01_共用資料/prices.csv", "01_共用資料/macro.csv", "01_共用資料/macro_realtime.csv",
    "01_共用資料/corporate_actions.csv", "01_共用資料/抓取紀錄.jsonl", "01_共用資料/prices_差異.csv",
    "04_回測/*.jsonl", "04_回測/*.csv",
)
# 既有檔案一個位元組都不能變、不能消失（可以新增檔案）：正式執行紀錄、使用者專屬持股、原始文件、影子快照。
IMMUTABLE_GLOBS = (
    "02_執行紀錄/*.json", "02_執行紀錄/影子快照/**/*", "01_共用資料/portfolio.json",
    "01_共用資料/data_spine/raw/**/*",
)
# 暫存或鎖檔，不納入比對
IGNORE_PATTERNS = ("*.寫入中*", "*.tmp", "*.write_journal.json", "*.完成", "__pycache__/*", "*.pyc", "02_執行紀錄/自動迴圈/*")
SNAPSHOT_DIR = "02_執行紀錄/影子快照"
RULES_PATH = "00_規範/rules.json"
QUEUE_PATH = "00_規範/自動任務佇列.json"
ACCEPTED_STATUS = {"PASS", "DONE", "OK", "ALREADY_DONE"}

# 命令列中絕不允許出現的參數（規格 3.3：不得使用永不詢問或跳過沙箱的模式）
FORBIDDEN_ARGS = ("--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
                  "--dangerously-skip-permissions", "danger-full-access", "bypassPermissions")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prefix_sha256(path: Path, length: int) -> str:
    h, left = hashlib.sha256(), length
    with open(path, "rb") as f:
        while left > 0:
            chunk = f.read(min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


def _match(rel: str, patterns) -> bool:
    return any(fnmatch.fnmatch(rel, p) or (p.endswith("/**/*") and rel.startswith(p[:-5] + "/")) for p in patterns)


def _ignored(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p) for p in IGNORE_PATTERNS)


def snapshot_state(root: Path = ROOT) -> dict:
    """記錄所有受追蹤檔案的大小與 sha256；另記 rules.json、03_工具/*.py、待辦清單（去除 status/result 後）與影子快照目錄。"""
    root = Path(root)
    files = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _ignored(rel) or not (_match(rel, APPEND_ONLY_GLOBS) or _match(rel, IMMUTABLE_GLOBS)):
            continue
        kind = "append_only" if _match(rel, APPEND_ONLY_GLOBS) else "immutable"
        files[rel] = {"kind": kind, "size": path.stat().st_size, "sha256": sha256_file(path)}
    rules = root / RULES_PATH
    tools = {p.name: sha256_file(p) for p in sorted((root / "03_工具").glob("*.py"))} if (root / "03_工具").exists() else {}
    snaps = sorted(p.name for p in (root / SNAPSHOT_DIR).glob("SNAP_*") if p.is_dir() and (p / "COMMITTED.json").exists()) \
        if (root / SNAPSHOT_DIR).exists() else []
    return {"files": files, "rules_sha256": sha256_file(rules) if rules.exists() else None, "tools": tools,
            "snapshots": snaps, "queue_core_sha256": queue_core_sha256(root / QUEUE_PATH)}


def queue_core_sha256(path: Path):
    """待辦清單去除每項的 status／result 後的雜湊：迴圈只能改這兩個欄位。"""
    if not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    core = json.loads(json.dumps(data))
    for t in core.get("tasks", []):
        t.pop("status", None)
        t.pop("result", None)
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def changed_files(before: dict, after: dict) -> list:
    """兩個狀態之間新增、變動、消失的受追蹤檔案（給複查者的差異清單）。"""
    names = sorted(set(before["files"]) | set(after["files"]))
    out = []
    for n in names:
        a, b = before["files"].get(n), after["files"].get(n)
        if a is None:
            out.append({"path": n, "change": "ADDED", "size": b["size"]})
        elif b is None:
            out.append({"path": n, "change": "REMOVED"})
        elif a["sha256"] != b["sha256"]:
            out.append({"path": n, "change": "MODIFIED", "size_before": a["size"], "size_after": b["size"]})
    return out


def _result(name: str, ok: bool, detail=None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


# ---------------------------------------------------------------------------
# 狀態比對類檢查
# ---------------------------------------------------------------------------
def gate_ledgers(before: dict, after: dict, root: Path = ROOT) -> dict:
    """正式帳本只能往後追加；既有不可變檔案不得變動；任何受追蹤檔案不得消失。"""
    root, problems = Path(root), []
    for rel, old in before["files"].items():
        new = after["files"].get(rel)
        if new is None:
            problems.append(f"REMOVED:{rel}")
        elif old["kind"] == "append_only":
            if new["size"] < old["size"]:
                problems.append(f"TRUNCATED:{rel}")
            elif new["size"] == old["size"]:
                if new["sha256"] != old["sha256"]:
                    problems.append(f"REWRITTEN:{rel}")
            elif prefix_sha256(root / rel, old["size"]) != old["sha256"]:
                problems.append(f"REWRITTEN_PREFIX:{rel}")
        elif new["sha256"] != old["sha256"]:
            problems.append(f"MODIFIED_IMMUTABLE:{rel}")
    for rel, new in after["files"].items():
        if rel not in before["files"] and rel == "01_共用資料/portfolio.json":
            problems.append(f"CREATED_USER_OWNED:{rel}")      # 持股檔是使用者專屬輸入，AI 不得建立
    return _result("ledgers_append_only", not problems, problems)


def gate_rules(before: dict, after: dict, allow_rules_change: bool) -> dict:
    changed = before["rules_sha256"] != after["rules_sha256"]
    return _result("rules_unchanged", (not changed) or allow_rules_change,
                   {"changed": changed, "allowed": allow_rules_change})


def gate_snapshots(before: dict, after: dict, allow_formal_snapshot: bool) -> dict:
    new = sorted(set(after["snapshots"]) - set(before["snapshots"]))
    gone = sorted(set(before["snapshots"]) - set(after["snapshots"]))
    ok = not gone and (not new or allow_formal_snapshot)
    return _result("no_formal_snapshot", ok, {"new": new, "removed": gone, "allowed": allow_formal_snapshot})


def gate_queue_integrity(before: dict, after: dict) -> dict:
    ok = before["queue_core_sha256"] == after["queue_core_sha256"]
    return _result("queue_only_status_result", ok, {"before": before["queue_core_sha256"], "after": after["queue_core_sha256"]})


def gate_lock_released(lock_reader, expected_holder=None) -> dict:
    """Only this task's holder must be gone; unrelated scoped writers may remain."""
    state = lock_reader()
    people = list((state or {}).get("holders") or ([] if state is None else [state]))
    present = ([h for h in people if h.get("who") == expected_holder]
               if expected_holder else people)
    return _result("task_lock_released", not present,
                   {"expected_holder": expected_holder, "remaining_task_entries": present})


def _task_lock_holder(task):
    explicit = task.get("lock_holder")
    if explicit: return explicit
    match = re.search(r"取鎖\s+([A-Za-z0-9_.-]+)", task.get("prompt") or "")
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# 執行類檢查（測試時可注入假的 runner）
# ---------------------------------------------------------------------------
def default_command_runner(args, cwd=ROOT, timeout=3600) -> dict:
    p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def regression_command(root: Path = ROOT, python: str | None = None) -> list:
    return [python or sys.executable, "-X", "utf8", str(Path(root) / "03_工具" / "test_regression.py")]


def dryrun_command(root: Path = ROOT, python: str | None = None) -> list:
    return [python or sys.executable, "-X", "utf8", str(Path(root) / "03_工具" / "research_snapshot.py"), "--dry-run"]


def gate_regression(runner=default_command_runner, root: Path = ROOT, python: str | None = None) -> dict:
    """完整回歸測試全過：結束碼 0，且輸出有 OK、沒有 FAILED／ERROR。"""
    res = runner(regression_command(root, python), cwd=root, timeout=3600)
    text = (res.get("stdout") or "") + (res.get("stderr") or "")
    ran = re.search(r"Ran (\d+) tests?", text)
    ok = res.get("returncode") == 0 and bool(ran) and re.search(r"^OK\b", text, re.M) is not None and "FAILED" not in text
    return _result("regression_passes", ok, {"returncode": res.get("returncode"), "tests": int(ran.group(1)) if ran else None,
                                             "tail": text[-400:]})


def gate_dry_run(runner=default_command_runner, root: Path = ROOT, python: str | None = None) -> dict:
    """research_snapshot --dry-run：結束碼 0、輸出的 write_count 為 0，而且執行前後受追蹤檔案完全沒變。"""
    before = snapshot_state(root)
    res = runner(dryrun_command(root, python), cwd=root, timeout=1800)
    after = snapshot_state(root)
    text = res.get("stdout") or ""
    m = re.search(r'"write_count"\s*:\s*(\d+)', text)
    write_count = int(m.group(1)) if m else None
    touched = changed_files(before, after)
    ok = res.get("returncode") == 0 and write_count == 0 and not touched
    return _result("dry_run_zero_writes", ok, {"returncode": res.get("returncode"), "write_count": write_count, "touched": touched})


# ---------------------------------------------------------------------------
# 輸出內容類檢查
# ---------------------------------------------------------------------------
def parse_report(text: str) -> dict:
    """解析執行者報告尾端的固定欄位。多次出現時取最後一次。"""
    def last(pattern):
        found = re.findall(pattern, text or "", re.M)
        return found[-1].strip() if found else None
    return {"status": last(r"^\s*STATUS:\s*(.+)$"),
            "weekly_remaining_raw": last(r"^\s*WEEKLY_USAGE_REMAINING_PERCENT:\s*(.+)$"),
            "blockers": [b.strip() for b in re.findall(r"^\s*BLOCKER:\s*(.+)$", text or "", re.M)],
            "files_changed": last(r"^\s*FILES_CHANGED:\s*(.+)$"), "key_hashes": last(r"^\s*KEY_HASHES:\s*(.+)$"),
            "next_action": last(r"^\s*NEXT_ACTION:\s*(.+)$")}


def parse_weekly_percent(raw):
    """回傳 float 或 None（UNKNOWN、缺漏、無法解析一律 None）。"""
    if raw is None:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%?", str(raw))
    return float(m.group(1)) if m and str(raw).strip().upper() != "UNKNOWN" else None


def gate_report_markers(text: str) -> dict:
    """輸出必須有 STATUS: 與 WEEKLY_USAGE_REMAINING_PERCENT: 兩行；STATUS 必須是被接受的值。"""
    rep = parse_report(text)
    missing = [k for k, v in (("STATUS", rep["status"]), ("WEEKLY_USAGE_REMAINING_PERCENT", rep["weekly_remaining_raw"])) if not v]
    first = (rep["status"] or "").split()[0].strip("`*.,;:").upper() if rep["status"] else ""
    ok = not missing and first in ACCEPTED_STATUS
    return _result("report_markers", ok, {"missing": missing, "status": rep["status"]})


def gate_no_blocker(text: str) -> dict:
    blockers = parse_report(text)["blockers"]
    return _result("no_blocker", not blockers, blockers)


def assert_safe_command(args) -> None:
    """命令列中出現永不詢問／跳過沙箱的參數就拒絕執行。"""
    joined = " ".join(str(a) for a in args)
    for bad in FORBIDDEN_ARGS:
        if bad in joined:
            raise ValueError(f"FORBIDDEN_ARGUMENT:{bad}")


def run_integrity_gates(before: dict, after: dict, task: dict, lock_reader, root: Path = ROOT) -> list:
    """執行者結束後、與狀態有關的全部硬檢查（不含需要執行外部程式的回歸與 dry-run）。"""
    return [gate_rules(before, after, bool(task.get("allow_rules_change"))),
            gate_ledgers(before, after, root),
            gate_snapshots(before, after, bool(task.get("allow_formal_snapshot"))),
            gate_queue_integrity(before, after),
            gate_lock_released(lock_reader, _task_lock_holder(task))]
