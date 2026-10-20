"""自動迴圈主程式（規格：90_各AI工作區/claude/自動迴圈規格_v0.1_2026-10-15.md）。

每一輪：事前檢查 → 選任務 → 新對話執行 → 硬檢查（auto_gates.py）→ 獨立複查 → 標 DONE，直到達到上限或遇到必須停下的情況。
迴圈本身只會改待辦清單的 status 與 result；它不取專案鎖、不改規則、不建立正式快照。
執行者與複查者都以「runner」注入，測試用假的 runner，不呼叫 Codex 或 Claude。

用法（先看計畫，不執行任何任務）：
  python 03_工具/auto_loop.py --plan
第一次試跑：
  python 03_工具/auto_loop.py --max-tasks 2 --max-hours 1 --min-weekly 30
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import auto_gates as gates  # noqa: E402

QUEUE_REL = gates.QUEUE_PATH
RUN_ROOT_REL = "02_執行紀錄/自動迴圈"
STATUSES = {"TODO", "RUNNING", "DONE", "FAILED", "STOPPED_FOR_USER", "SKIPPED"}
REQUIRED_TASK_KEYS = ("id", "title", "status", "executor", "model", "effort", "reviewer", "depends_on", "stop_for_user",
                      "allow_rules_change", "allow_formal_snapshot", "prompt", "acceptance")
# 規格 6：共同前言
PREAMBLE = """你在「股票判斷」專案中，由自動迴圈呼叫；這是一個全新的對話，請先讀 AGENTS.md、00_規範/01_專案交接說明.md 與本任務指名的規格。
固定規則：不輸出買賣建議；缺資料不當成 0；資料只能新增，不能改寫；寫入共用檔前要取鎖，完成後釋放；不改 rules.json 或門檻；
不建立正式快照；不繞過 robots；不刪檔；不安裝套件；不使用金鑰以外的認證。
遇到需要使用者決定的事，輸出 BLOCKER: <原因>，然後停止。
報告結尾必須有以下幾行：STATUS:、FILES_CHANGED:、KEY_HASHES:、WEEKLY_USAGE_REMAINING_PERCENT:（讀不到就寫 UNKNOWN）、NEXT_ACTION:。
"""
# 複查者可用的模型設定（reviewer 欄位的值 → 實際 CLI 與模型）。模型名稱以任務清單為準，執行前用 --plan 檢查。
REVIEWER_SPECS = {"claude-opus": {"cli": "claude", "model": "opus", "effort": "medium"},
                  "codex-sol": {"cli": "codex", "model": "gpt-5.6-sol", "effort": "medium"}}
# Claude 無沙箱參數，只能靠工具白名單／黑名單；Codex 用 --sandbox。以下清單是「盡力而為」，真正的防線是事後的硬檢查。
CLAUDE_EXEC_ALLOWED = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]
CLAUDE_EXEC_DENIED = ["Bash(rm *)", "Bash(del *)", "Bash(rmdir *)", "Bash(Remove-Item *)", "Bash(pip *)", "Bash(pip3 *)",
                      "Bash(npm *)", "Bash(curl *)", "Bash(wget *)", "Bash(git push *)", "Bash(git reset *)"]
CLAUDE_REVIEW_ALLOWED = ["Read", "Glob", "Grep", "Bash"]
CLAUDE_REVIEW_DENIED = CLAUDE_EXEC_DENIED + ["Edit", "Write", "NotebookEdit"]


class StopLoop(Exception):
    def __init__(self, reason: str, detail=None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    report: str = ""          # 執行者最後訊息（codex 的 --output-last-message）；沒有就用 stdout


@dataclass
class Options:
    max_tasks: int = 3
    max_hours: float = 3.0
    min_weekly: float = 30.0
    task_timeout_min: float = 90.0
    lock_wait_min: float = 30.0
    lock_poll_s: float = 60.0


@dataclass
class Deps:
    root: Path = ROOT
    executor_runner: object = None
    reviewer_runner: object = None
    lock_reader: object = None
    now_fn: object = field(default=lambda: dt.datetime.now().astimezone())
    sleep_fn: object = time.sleep
    state_fn: object = None
    regression_gate: object = None
    dry_run_gate: object = None


# ---------------------------------------------------------------------------
# 待辦清單
# ---------------------------------------------------------------------------
def load_queue(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_queue(queue: dict) -> list:
    errors, ids = [], set()
    for i, t in enumerate(queue.get("tasks", [])):
        for k in REQUIRED_TASK_KEYS:
            if k not in t:
                errors.append(f"tasks[{i}] 缺少 {k}")
        if t.get("status") not in STATUSES:
            errors.append(f"{t.get('id')}：status 無效")
        if t.get("id") in ids:
            errors.append(f"id 重複：{t.get('id')}")
        ids.add(t.get("id"))
        for k in ("stop_for_user", "allow_rules_change", "allow_formal_snapshot"):
            if k in t and not isinstance(t[k], bool):
                errors.append(f"{t.get('id')}：{k} 必須是布林")
        if t.get("executor") not in ("codex", "claude"):
            errors.append(f"{t.get('id')}：executor 必須是 codex 或 claude")
        if t.get("reviewer") not in REVIEWER_SPECS:
            errors.append(f"{t.get('id')}：reviewer「{t.get('reviewer')}」不在 {sorted(REVIEWER_SPECS)}")
    for t in queue.get("tasks", []):
        for d in t.get("depends_on", []):
            if d not in ids:
                errors.append(f"{t.get('id')}：depends_on 指向不存在的 {d}")
    return errors


def update_task(path: Path, task_id: str, status: str, result=None) -> None:
    """只改 status 與 result；其他欄位逐位元組保持原值（重新讀檔、改兩個欄位、原子替換）。"""
    queue = load_queue(path)
    core_before = gates.queue_core_sha256(path)
    task = next(t for t in queue["tasks"] if t["id"] == task_id)
    task["status"] = status
    if result is not None:
        task["result"] = result
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if gates.queue_core_sha256(path) != core_before:
        raise StopLoop("QUEUE_CORE_CHANGED_BY_LOOP")


def select_task(queue: dict):
    """第一個 TODO 且依賴全部 DONE 的任務。"""
    status = {t["id"]: t["status"] for t in queue["tasks"]}
    for t in queue["tasks"]:
        if t["status"] == "TODO" and all(status.get(d) == "DONE" for d in t["depends_on"]):
            return t
    return None


# ---------------------------------------------------------------------------
# 命令列（參數名稱來自 codex exec --help 與 claude --help 的實際輸出，2026-10-15）
# ---------------------------------------------------------------------------
def find_executable(name: str, override: str | None = None) -> str | None:
    if override:
        return override if Path(override).exists() else None
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    if name == "codex":
        for c in (home / ".codex" / ".sandbox-bin" / "codex.exe",):
            if c.exists():
                return str(c)
    if name == "claude":
        appdata = os.environ.get("APPDATA")
        if appdata:
            hits = sorted(glob.glob(os.path.join(appdata, "Claude", "claude-code", "*", "*", "claude.exe")))
            if hits:
                return hits[-1]
    return None


def resolve_spec(task: dict, role: str) -> dict:
    if role == "executor":
        return {"cli": task["executor"], "model": task["model"], "effort": task["effort"], "role": "executor"}
    return dict(REVIEWER_SPECS[task["reviewer"]], role="reviewer")


def build_command(spec: dict, exe: str, root: Path, out_file: Path) -> list:
    """回傳命令列（提示詞一律走標準輸入，避免命令列長度限制）。"""
    if spec["cli"] == "codex":
        sandbox = "workspace-write" if spec["role"] == "executor" else "read-only"
        args = [exe, "exec", "--skip-git-repo-check", "-m", spec["model"], "-c", f'model_reasoning_effort="{spec["effort"]}"', "-s", sandbox, "-c", "windows.sandbox=unelevated",
                "-C", str(root), "--color", "never", "--output-last-message", str(out_file), "-"]
    else:
        allowed = CLAUDE_EXEC_ALLOWED if spec["role"] == "executor" else CLAUDE_REVIEW_ALLOWED
        denied = CLAUDE_EXEC_DENIED if spec["role"] == "executor" else CLAUDE_REVIEW_DENIED
        mode = "acceptEdits" if spec["role"] == "executor" else "dontAsk"
        args = [exe, "-p", "--model", spec["model"], "--effort", spec["effort"], "--permission-mode", mode,
                "--permission-prompts", "none", "--no-session-persistence", "--output-format", "text",
                "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(denied)]
    gates.assert_safe_command(args)
    return args


class CliRunner:
    """真正呼叫 codex／claude 的 runner。"""

    def __init__(self, root: Path, run_dir: Path | None = None, codex_exe=None, claude_exe=None):
        self.root, self.run_dir = Path(root), (Path(run_dir) if run_dir else None)
        self.exe = {"codex": codex_exe, "claude": claude_exe}

    def set_run_dir(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)

    def __call__(self, spec: dict, prompt: str, tag: str, timeout_s: float) -> RunResult:
        exe = find_executable(spec["cli"], self.exe.get(spec["cli"]))
        if not exe:
            raise StopLoop("EXECUTABLE_NOT_FOUND", spec["cli"])
        out_file = self.run_dir / f"{tag}_last_message.txt"
        args = build_command(spec, exe, self.root, out_file)
        # Codex Windows 非提升沙箱不能寫使用者 TEMP（2026-10-16 RUN_20261016T144640 回歸 PermissionError），
        # 改用專案內暫存目錄（沙箱可寫的 workdir 範圍）。
        tmp = self.root / "90_各AI工作區" / "_autoloop_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp))
        try:
            p = subprocess.run(args, input=prompt, cwd=str(self.root), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired as e:
            return RunResult(-1, (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                             "TIMEOUT", True)
        report = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
        return RunResult(p.returncode, p.stdout, p.stderr, False, report or p.stdout)


# ---------------------------------------------------------------------------
# 主迴圈
# ---------------------------------------------------------------------------
# T20（T09 裁決）：抽取任務要維持兩次抽取的獨立性。任務定義直接寫在提示裡，並列出不得讀取的檔案。
EXTRACTION_FIELDS = {"C5", "C6", "I2", "I3", "I4"}
EXTRACTION_FORBIDDEN = """### 抽取任務禁讀清單（獨立性要求）
本任務的完整定義已寫在這份提示裡，不需要、也不可以為了任務定義去讀佇列檔。抽取前與抽取中**不得讀取**：
- `00_規範/自動任務佇列.json`
- `02_執行紀錄/` 底下任何檔案
- `90_各AI工作區/*/` 底下其他 AI 的報告與抽取草稿
- `01_共用資料/data_spine/ai_field_values*.jsonl` 與 `ai_field_value_voids.jsonl`
- `01_共用資料/data_spine/ai_reviews.jsonl`
- 同一欄位第一次抽取的內容（`ai_extractions.jsonl` 裡其他抽取者的列）
- `00_規範/使用者決定紀錄.md`（含指名公司的範例結果；規則改讀下面的摘要）
- `03_工具/test_ai_fields.py`（測試裡有公司名稱與結果）
允許讀取：`docs_ledger.jsonl` 與其登錄的原文；規則只讀 `00_規範/抽取規則摘要_S4.md` 與 `03_工具/ai_fields.py`。
寫入抽取一律透過 `ai_fields.append_extraction`（程式內部的重複檢查不算你讀了帳本）。
"""


def is_extraction_task(task: dict) -> bool:
    raw = task.get("field")
    fields = {raw} if isinstance(raw, str) else set(raw or [])
    return bool(fields & EXTRACTION_FIELDS) or "抽取" in str(task.get("title", ""))


def build_prompt(task: dict) -> str:
    acceptance = "\n".join(f"- {a}" for a in task.get("acceptance", [])) or "（無）"
    forbidden = f"\n{EXTRACTION_FORBIDDEN}" if is_extraction_task(task) else ""
    return (f"{PREAMBLE}\n## 任務 {task['id']}：{task['title']}\n\n{task['prompt']}\n\n### 驗收條件\n{acceptance}\n"
            f"{forbidden}")


def build_review_prompt(task: dict, report: str, changed: list) -> str:
    return (f"你是獨立複查者，請只讀、不寫任何共用檔案。\n## 被複查的任務 {task['id']}：{task['title']}\n\n{task['prompt']}\n\n"
            f"### 驗收條件\n" + "\n".join(f"- {a}" for a in task.get("acceptance", [])) +
            f"\n\n### 執行者報告（原文）\n{report[-20000:]}\n\n### 這一輪變動的受追蹤檔案\n"
            f"{json.dumps(changed, ensure_ascii=False, indent=1)[:8000]}\n\n"
            "請：1) 抽查 2 個以上數字，自己重算；2) 確認引句能在原文找到；3) 檢查驗收條件是否都滿足。\n"
            "最後一行必須是 `REVIEW: PASS` 或 `REVIEW: FAIL`，並在前面附原因。\n")


def parse_review(text: str) -> str:
    found = re.findall(r"^\s*REVIEW:\s*(PASS|FAIL)\b", text or "", re.M)
    return found[-1] if found else "FAIL"        # 沒有明確的 PASS 一律當 FAIL


def compact(gate_results: list) -> list:
    return [{"name": g["name"], "ok": g["ok"], "detail": g["detail"] if not g["ok"] else None} for g in gate_results]


def run_loop(opts: Options, deps: Deps, queue_path: Path | None = None) -> dict:
    root = Path(deps.root)
    queue_path = Path(queue_path or root / QUEUE_REL)
    started = deps.now_fn()
    run_id = "RUN_" + started.strftime("%Y%m%dT%H%M%S")
    run_dir = root / RUN_ROOT_REL / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for runner in (deps.executor_runner, deps.reviewer_runner):
        if hasattr(runner, "set_run_dir"):
            runner.set_run_dir(run_dir)
    state_fn = deps.state_fn or (lambda: gates.snapshot_state(root))
    summary = {"run_id": run_id, "started_at": started.isoformat(), "options": vars(opts), "tasks": [], "stop_reason": None,
               "stop_detail": None, "weekly_readings": []}
    done_count, unknown_streak = 0, 0
    try:
        queue = load_queue(queue_path)
        errors = validate_queue(queue)
        if errors:
            raise StopLoop("QUEUE_INVALID", errors)
        if any(t["status"] == "RUNNING" for t in queue["tasks"]):
            raise StopLoop("PREFLIGHT_FAILED", "待辦清單有 RUNNING 的任務（上次中斷？）請人工檢查")
        while True:
            # --- 上限
            if done_count >= opts.max_tasks:
                raise StopLoop("LIMIT_MAX_TASKS")
            elapsed_h = (deps.now_fn() - started).total_seconds() / 3600
            if elapsed_h >= opts.max_hours:
                raise StopLoop("LIMIT_MAX_HOURS", f"{elapsed_h:.2f}h")
            # --- 1. 事前檢查：協作鎖必須是空的
            waited = 0.0
            while deps.lock_reader() is not None:
                if waited >= opts.lock_wait_min * 60:
                    raise StopLoop("LOCK_BUSY_TIMEOUT", deps.lock_reader())
                deps.sleep_fn(opts.lock_poll_s)
                waited += opts.lock_poll_s
            # --- 2. 選任務
            queue = load_queue(queue_path)
            task = select_task(queue)
            if task is None:
                raise StopLoop("NO_ELIGIBLE_TASK")
            if task["stop_for_user"]:
                update_task(queue_path, task["id"], "STOPPED_FOR_USER", {"run_id": run_id, "reason": "stop_for_user"})
                summary["tasks"].append({"id": task["id"], "status": "STOPPED_FOR_USER"})
                raise StopLoop("STOP_FOR_USER", task["id"])
            entry = {"id": task["id"], "title": task["title"], "started_at": deps.now_fn().isoformat()}
            summary["tasks"].append(entry)
            other_statuses = {t["id"]: t["status"] for t in queue["tasks"] if t["id"] != task["id"]}
            update_task(queue_path, task["id"], "RUNNING", {"run_id": run_id})
            before = state_fn()
            # --- 3. 執行（新對話）
            prompt = build_prompt(task)
            (run_dir / f"{task['id']}_prompt.md").write_text(prompt, encoding="utf-8")
            spec = resolve_spec(task, "executor")
            res = deps.executor_runner(spec, prompt, f"{task['id']}_executor", opts.task_timeout_min * 60)
            report = res.report or res.stdout
            (run_dir / f"{task['id']}_executor_output.txt").write_text(report + "\n--- stderr ---\n" + (res.stderr or ""), encoding="utf-8")
            rep = gates.parse_report(report)
            entry["report"] = {k: rep[k] for k in ("status", "weekly_remaining_raw", "files_changed", "key_hashes", "next_action")}
            weekly = gates.parse_weekly_percent(rep["weekly_remaining_raw"])
            summary["weekly_readings"].append({"task": task["id"], "value": weekly, "raw": rep["weekly_remaining_raw"]})
            # --- 4. 硬檢查
            results = []
            results.append({"name": "executor_exit", "ok": (not res.timed_out) and res.returncode == 0,
                            "detail": {"returncode": res.returncode, "timed_out": res.timed_out}})
            results.append(gates.gate_no_blocker(report))
            results.append(gates.gate_report_markers(report))
            after = state_fn()
            queue_now = load_queue(queue_path)
            statuses_now = {t["id"]: t["status"] for t in queue_now["tasks"] if t["id"] != task["id"]}
            results.append({"name": "queue_other_statuses_unchanged", "ok": statuses_now == other_statuses,
                            "detail": {"before": other_statuses, "after": statuses_now}})
            if statuses_now != other_statuses:                  # 執行者偷改了別的任務的狀態：還原成原值，再停止
                for tid, original in other_statuses.items():
                    if statuses_now.get(tid) != original:
                        update_task(queue_path, tid, original)
            results += gates.run_integrity_gates(before, after, task, deps.lock_reader, root)
            results.append(deps.regression_gate())
            results.append(deps.dry_run_gate())
            (run_dir / f"{task['id']}_gates.json").write_text(json.dumps(compact(results), ensure_ascii=False, indent=1), encoding="utf-8")
            entry["gates"] = compact(results)
            failed = [g["name"] for g in results if not g["ok"]]
            blockers = gates.parse_report(report)["blockers"]
            if blockers:
                status = "FAILED" if [n for n in failed if n != "no_blocker"] else "STOPPED_FOR_USER"
                update_task(queue_path, task["id"], status, {"run_id": run_id, "blockers": blockers, "failed_gates": failed})
                entry["status"] = status
                raise StopLoop("BLOCKER", blockers)
            if failed:
                update_task(queue_path, task["id"], "FAILED", {"run_id": run_id, "failed_gates": failed})
                entry["status"] = "FAILED"
                raise StopLoop("GATE_FAILED", failed)
            # --- 5. 獨立複查
            changed = gates.changed_files(before, after)
            review_prompt = build_review_prompt(task, report, changed)
            (run_dir / f"{task['id']}_review_prompt.md").write_text(review_prompt, encoding="utf-8")
            rv = deps.reviewer_runner(resolve_spec(task, "reviewer"), review_prompt, f"{task['id']}_reviewer", opts.task_timeout_min * 60)
            review_text = rv.report or rv.stdout
            (run_dir / f"{task['id']}_review_output.txt").write_text(review_text, encoding="utf-8")
            verdict = parse_review(review_text)
            after_review = state_fn()                       # 複查者不能寫任何共用檔案
            review_gates = [g for g in (gates.gate_ledgers(after, after_review, root),
                                        gates.gate_rules(after, after_review, False),
                                        gates.gate_snapshots(after, after_review, False),
                                        gates.gate_queue_integrity(after, after_review),
                                        gates.gate_lock_released(deps.lock_reader, gates._task_lock_holder(task))) if not g["ok"]]
            changed_by_reviewer = gates.changed_files(after, after_review)
            if changed_by_reviewer:
                review_gates.append({"name": "reviewer_wrote_files", "ok": False, "detail": changed_by_reviewer})
            entry["review"] = verdict
            if rv.timed_out or rv.returncode != 0:
                verdict, review_gates = "FAIL", review_gates + [{"name": "reviewer_exit", "ok": False, "detail": rv.returncode}]
            if verdict != "PASS" or review_gates:
                update_task(queue_path, task["id"], "FAILED", {"run_id": run_id, "review": verdict, "failed_gates": [g["name"] for g in review_gates]})
                entry["status"] = "FAILED"
                raise StopLoop("REVIEW_FAILED", {"verdict": verdict, "gates": [g["name"] for g in review_gates]})
            # --- 6. 結束一項
            update_task(queue_path, task["id"], "DONE", {"run_id": run_id, "finished_at": deps.now_fn().isoformat(),
                                                         "status_line": rep["status"], "review": "PASS"})
            entry["status"] = "DONE"
            done_count += 1
            unknown_streak = unknown_streak + 1 if weekly is None else 0
            if unknown_streak >= 2:
                raise StopLoop("WEEKLY_USAGE_UNKNOWN_TWICE")
            if weekly is not None and weekly < opts.min_weekly:
                raise StopLoop("LIMIT_WEEKLY_LOW", weekly)
    except StopLoop as stop:
        summary["stop_reason"], summary["stop_detail"] = stop.reason, stop.detail
    summary["finished_at"] = deps.now_fn().isoformat()
    summary["done_count"] = done_count
    (run_dir / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (run_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    summary["run_dir"] = str(run_dir)
    return summary


def render_summary(s: dict) -> str:
    lines = [f"# 自動迴圈摘要 {s['run_id']}", "", f"- 起訖：{s['started_at']} → {s.get('finished_at')}",
             f"- 完成任務數：{s.get('done_count', 0)}", f"- 停止原因：**{s['stop_reason']}** {s['stop_detail'] if s['stop_detail'] else ''}",
             f"- 上限：{json.dumps(s['options'], ensure_ascii=False)}", "", "## 任務", ""]
    for t in s["tasks"]:
        lines.append(f"### {t['id']} {t.get('title', '')}")
        lines.append(f"- 狀態：{t.get('status')}；複查：{t.get('review')}；STATUS：{(t.get('report') or {}).get('status')}")
        if t.get("report"):
            lines += [f"- FILES_CHANGED：{t['report'].get('files_changed')}", f"- KEY_HASHES：{t['report'].get('key_hashes')}",
                      f"- NEXT_ACTION：{t['report'].get('next_action')}"]
        for g in t.get("gates", []):
            if not g["ok"]:
                lines.append(f"- 檢查失敗 `{g['name']}`：{json.dumps(g['detail'], ensure_ascii=False)[:300]}")
        lines.append("")
    lines += ["## 額度", "", *(f"- {w['task']}：{w['raw']}" for w in s["weekly_readings"]), "",
              "## 需要使用者決定的事項", "", f"- 停止原因 {s['stop_reason']}：{s['stop_detail']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 命令列
# ---------------------------------------------------------------------------
def real_deps(root: Path, run_dir_holder: dict, codex_exe=None, claude_exe=None, python=None) -> Deps:
    import lock as project_lock
    deps = Deps(root=root, lock_reader=project_lock.read)
    deps.regression_gate = lambda: gates.gate_regression(gates.default_command_runner, root, python)
    deps.dry_run_gate = lambda: gates.gate_dry_run(gates.default_command_runner, root, python)
    deps.executor_runner = deps.reviewer_runner = CliRunner(root, None, codex_exe, claude_exe)
    return deps


def plan(queue_path: Path, root: Path, codex_exe=None, claude_exe=None) -> int:
    queue = load_queue(queue_path)
    errors = validate_queue(queue)
    print("待辦清單檢查：", "通過" if not errors else errors)
    task = select_task(queue)
    codex_path, claude_path = find_executable("codex", codex_exe), find_executable("claude", claude_exe)
    print(f"codex：{codex_path or '找不到'}；claude：{claude_path or '找不到'}（不在 PATH 時用 --codex-exe／--claude-exe 指定）")
    if shutil.which("codex") is None or shutil.which("claude") is None:
        print("注意：codex／claude 不在 PATH 上；以上路徑是從已安裝的應用程式資料夾找到的。")
    cfg = Path.home() / ".codex" / "config.toml"
    cfg_model = None
    if cfg.exists():
        m = re.search(r'^model\s*=\s*"([^"]+)"', cfg.read_text(encoding="utf-8", errors="replace"), re.M)
        cfg_model = m.group(1) if m else None
    if task is None:
        print("沒有可執行的任務。")
        return 0
    print(f"下一項任務：{task['id']} {task['title']}（stop_for_user={task['stop_for_user']}）")
    for role in ("executor", "reviewer"):
        spec = resolve_spec(task, role)
        exe = codex_path if spec["cli"] == "codex" else claude_path
        try:
            cmd = build_command(spec, exe or spec["cli"], root, root / "out.txt")
            print(f"[{role}] " + " ".join(cmd))
        except ValueError as e:
            print(f"[{role}] 拒絕：{e}")
        if spec["cli"] == "codex" and cfg_model and spec["model"] != cfg_model:
            print(f"  警告：任務指定模型「{spec['model']}」與 ~/.codex/config.toml 的 model「{cfg_model}」不同，請確認名稱有效。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自動迴圈")
    ap.add_argument("--queue", default=str(ROOT / QUEUE_REL))
    ap.add_argument("--max-tasks", type=int, default=3)
    ap.add_argument("--max-hours", type=float, default=3.0)
    ap.add_argument("--min-weekly", type=float, default=30.0)
    ap.add_argument("--task-timeout-min", type=float, default=90.0)
    ap.add_argument("--plan", action="store_true", help="只顯示下一項任務與將執行的命令，不執行")
    ap.add_argument("--codex-exe")
    ap.add_argument("--claude-exe")
    ap.add_argument("--python", help="跑回歸與 dry-run 用的 Python（預設與本程式相同）")
    args = ap.parse_args(argv)
    if args.plan:
        return plan(Path(args.queue), ROOT, args.codex_exe, args.claude_exe)
    opts = Options(args.max_tasks, args.max_hours, args.min_weekly, args.task_timeout_min)
    summary = run_loop(opts, real_deps(ROOT, {}, args.codex_exe, args.claude_exe, args.python), Path(args.queue))
    print(f"停止原因：{summary['stop_reason']}；完成 {summary['done_count']} 項；摘要：{summary['run_dir']}\\summary.md")
    return 0 if summary["stop_reason"] in ("LIMIT_MAX_TASKS", "LIMIT_MAX_HOURS", "NO_ELIGIBLE_TASK") else 2


if __name__ == "__main__":
    raise SystemExit(main())
