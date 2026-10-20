"""auto_loop.py／auto_gates.py 的測試：全部使用假的執行者與複查者，在暫存根目錄裡跑，不呼叫 Codex 或 Claude，
也不碰專案的任何檔案、鎖或帳本。執行：python 03_工具/test_auto_loop.py"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_gates as gates
import auto_loop as loop

GOOD = "做完了。\nSTATUS: PASS\nFILES_CHANGED: x\nKEY_HASHES: x\nWEEKLY_USAGE_REMAINING_PERCENT: 80\nNEXT_ACTION: 無\n"
PASS_REVIEW = "抽查 2 個數字，重算一致；引句都在原文。\nREVIEW: PASS\n"


def make_task(tid, deps=(), **kw):
    task = {"id": tid, "title": f"任務 {tid}", "status": "TODO", "executor": "codex", "model": "gpt-6-sol", "effort": "medium",
            "reviewer": "claude-opus", "depends_on": list(deps), "stop_for_user": False, "allow_rules_change": False,
            "allow_formal_snapshot": False, "prompt": f"做 {tid}", "acceptance": ["回歸全綠"]}
    task.update(kw)
    return task


class LoopBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        r = self.root = self.tmp / "proj"
        for rel in ("00_規範", "01_共用資料/data_spine/raw", "02_執行紀錄/影子快照/SNAP_20260101T000000Z_aaaaaaaa", "03_工具", "04_回測"):
            (r / rel).mkdir(parents=True)
        (r / "00_規範/rules.json").write_text('{"protocol_version":"1.0"}\n', encoding="utf-8")
        self.ledger = r / "01_共用資料/data_spine/observations.jsonl"
        self.ledger.write_text('{"a":1}\n{"a":2}\n{"a":3}\n', encoding="utf-8")
        (r / "01_共用資料/prices.csv").write_text("date,ticker\n2026-01-01,X\n", encoding="utf-8")
        (r / "02_執行紀錄/2026-10-05_claude.json").write_text('{"run":1}\n', encoding="utf-8")
        (r / "02_執行紀錄/影子快照/SNAP_20260101T000000Z_aaaaaaaa/COMMITTED.json").write_text('{"c":1}\n', encoding="utf-8")
        self.queue_path = r / "00_規範/自動任務佇列.json"
        self.lock = {"holder": None}
        self.calls = {"executor": 0, "reviewer": 0}
        self.regression_ok, self.dry_ok = True, True
        self.exec_effect = lambda task: None
        self.exec_text = GOOD
        self.exec_result = None
        self.review_text = PASS_REVIEW
        self.review_effect = lambda task: None
        self.clock = [dt.datetime(2026, 10, 15, 12, 0, 0).astimezone()]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_queue(self, tasks):
        self.queue_path.write_text(json.dumps({"queue_version": "0.1", "tasks": tasks}, ensure_ascii=False, indent=2), encoding="utf-8")

    def deps(self):
        def executor(spec, prompt, tag, timeout_s):
            self.calls["executor"] += 1
            task_id = tag.split("_")[0]
            self.exec_effect(task_id)
            if self.exec_result is not None:
                return self.exec_result
            return loop.RunResult(0, self.exec_text, "", False, self.exec_text)

        def reviewer(spec, prompt, tag, timeout_s):
            self.calls["reviewer"] += 1
            self.review_effect(tag.split("_")[0])
            return loop.RunResult(0, self.review_text, "", False, self.review_text)

        def now():
            self.clock[0] += dt.timedelta(seconds=1)
            return self.clock[0]

        def regression():
            return {"name": "regression_passes", "ok": self.regression_ok, "detail": None}

        def dry():
            return {"name": "dry_run_zero_writes", "ok": self.dry_ok, "detail": None}

        return loop.Deps(root=self.root, executor_runner=executor, reviewer_runner=reviewer,
                         lock_reader=lambda: self.lock["holder"], now_fn=now, sleep_fn=lambda s: self.clock.__setitem__(
                             0, self.clock[0] + dt.timedelta(seconds=s)), regression_gate=regression, dry_run_gate=dry)

    def run_loop(self, **opts):
        return loop.run_loop(loop.Options(**opts), self.deps(), self.queue_path)

    def status(self, tid):
        return next(t for t in json.loads(self.queue_path.read_text(encoding="utf-8"))["tasks"] if t["id"] == tid)["status"]

    def failed_gates(self, summary, tid="T01"):
        return [g["name"] for t in summary["tasks"] if t["id"] == tid for g in t.get("gates", []) if not g["ok"]]


class HappyPath(LoopBase):
    def test_two_tasks_done_in_dependency_order_and_only_status_result_change(self):
        self.write_queue([make_task("T02", deps=["T01"]), make_task("T01")])
        core_before = gates.queue_core_sha256(self.queue_path)
        s = self.run_loop(max_tasks=5)
        self.assertEqual((self.status("T01"), self.status("T02")), ("DONE", "DONE"))
        self.assertEqual([t["id"] for t in s["tasks"]], ["T01", "T02"])          # T02 依賴 T01，即使排在前面也要等
        self.assertEqual(s["stop_reason"], "NO_ELIGIBLE_TASK")
        self.assertEqual(gates.queue_core_sha256(self.queue_path), core_before)
        run_dir = Path(s["run_dir"])
        for name in ("summary.md", "run.json", "T01_prompt.md", "T01_gates.json", "T01_review_output.txt"):
            self.assertTrue((run_dir / name).exists(), name)
        self.assertIn("固定規則", (run_dir / "T01_prompt.md").read_text(encoding="utf-8"))
        self.assertEqual(self.calls, {"executor": 2, "reviewer": 2})

    def test_legitimate_append_is_allowed(self):
        self.write_queue([make_task("T01")])
        self.exec_effect = lambda t: self.ledger.write_text(self.ledger.read_text(encoding="utf-8") + '{"a":4}\n', encoding="utf-8")
        s = self.run_loop(max_tasks=1)
        self.assertEqual(self.status("T01"), "DONE"); self.assertEqual(s["stop_reason"], "LIMIT_MAX_TASKS")


class HardGatesStopTheLoop(LoopBase):
    def stops(self, expected_gate, reason="GATE_FAILED"):
        s = self.run_loop(max_tasks=5)
        self.assertEqual(s["stop_reason"], reason, s)
        self.assertEqual(self.status("T01"), "FAILED")
        self.assertIn(expected_gate, self.failed_gates(s))
        self.assertEqual(self.status("T02"), "TODO")                               # 後面的任務沒有被執行
        self.assertEqual(self.calls["reviewer"], 0)                                # 硬檢查失敗就不用進複查
        return s

    def setUp(self):
        super().setUp()
        self.write_queue([make_task("T01"), make_task("T02")])

    def test_regression_failure_stops(self):
        self.regression_ok = False
        self.stops("regression_passes")

    def test_dry_run_with_writes_stops(self):
        self.dry_ok = False
        self.stops("dry_run_zero_writes")

    def test_rules_change_stops_unless_authorised(self):
        self.exec_effect = lambda t: (self.root / "00_規範/rules.json").write_text('{"protocol_version":"9.9"}\n', encoding="utf-8")
        self.stops("rules_unchanged")

    def test_rules_change_allowed_when_task_is_authorised(self):
        self.write_queue([make_task("T01", allow_rules_change=True)])
        self.exec_effect = lambda t: (self.root / "00_規範/rules.json").write_text('{"protocol_version":"9.9"}\n', encoding="utf-8")
        s = self.run_loop(max_tasks=1)
        self.assertEqual(self.status("T01"), "DONE", s)

    def test_ledger_rewritten_in_place_stops(self):
        self.exec_effect = lambda t: self.ledger.write_text('{"a":1}\n{"a":9}\n{"a":3}\n', encoding="utf-8")
        s = self.stops("ledgers_append_only")
        detail = next(g for g in s["tasks"][0]["gates"] if g["name"] == "ledgers_append_only")["detail"]
        self.assertTrue(any(x.startswith("REWRITTEN") for x in detail), detail)

    def test_ledger_rewrite_hidden_behind_an_append_stops(self):
        self.exec_effect = lambda t: self.ledger.write_text('{"a":9}\n{"a":2}\n{"a":3}\n{"a":4}\n', encoding="utf-8")   # 改第一行再追加
        s = self.stops("ledgers_append_only")
        self.assertTrue(any(x.startswith("REWRITTEN_PREFIX") for x in
                            next(g for g in s["tasks"][0]["gates"] if g["name"] == "ledgers_append_only")["detail"]))

    def test_ledger_truncated_or_deleted_stops(self):
        self.exec_effect = lambda t: self.ledger.write_text('{"a":1}\n', encoding="utf-8")
        s = self.stops("ledgers_append_only")
        self.assertTrue(any(x.startswith("TRUNCATED") for x in next(g for g in s["tasks"][0]["gates"] if g["name"] == "ledgers_append_only")["detail"]))

    def test_ledger_deleted_stops(self):
        self.exec_effect = lambda t: self.ledger.unlink()
        s = self.stops("ledgers_append_only")
        self.assertTrue(any(x.startswith("REMOVED") for x in next(g for g in s["tasks"][0]["gates"] if g["name"] == "ledgers_append_only")["detail"]))

    def test_existing_formal_run_record_modified_stops(self):
        self.exec_effect = lambda t: (self.root / "02_執行紀錄/2026-10-05_claude.json").write_text('{"run":2}\n', encoding="utf-8")
        s = self.stops("ledgers_append_only")
        self.assertTrue(any(x.startswith("MODIFIED_IMMUTABLE") for x in next(g for g in s["tasks"][0]["gates"] if g["name"] == "ledgers_append_only")["detail"]))

    def test_creating_user_owned_portfolio_stops(self):
        self.exec_effect = lambda t: (self.root / "01_共用資料/portfolio.json").write_text("{}", encoding="utf-8")
        self.stops("ledgers_append_only")

    def test_extra_formal_snapshot_stops_unless_authorised(self):
        def effect(t):
            d = self.root / "02_執行紀錄/影子快照/SNAP_20260202T000000Z_bbbbbbbb"; d.mkdir()
            (d / "COMMITTED.json").write_text("{}", encoding="utf-8")
        self.exec_effect = effect
        self.stops("no_formal_snapshot")

    def test_formal_snapshot_allowed_when_authorised(self):
        self.write_queue([make_task("T01", allow_formal_snapshot=True)])
        def effect(t):
            d = self.root / "02_執行紀錄/影子快照/SNAP_20260202T000000Z_bbbbbbbb"; d.mkdir()
            (d / "COMMITTED.json").write_text("{}", encoding="utf-8")
        self.exec_effect = effect
        self.assertEqual(self.run_loop(max_tasks=1)["tasks"][0]["status"], "DONE")

    def test_existing_snapshot_removed_stops(self):
        self.exec_effect = lambda t: shutil.rmtree(self.root / "02_執行紀錄/影子快照/SNAP_20260101T000000Z_aaaaaaaa")
        self.stops("no_formal_snapshot")

    def test_lock_not_released_stops(self):
        self.exec_effect = lambda t: self.lock.__setitem__("holder", {"who": "executor", "purpose": "忘了釋放"})
        self.stops("task_lock_released")

    def test_task_lock_gate_ignores_unrelated_scoped_holder(self):
        state = {"holders": [{"who":"other","scopes":["docs"]}]}
        gate = gates.gate_lock_released(lambda: state, "codex_t99")
        self.assertTrue(gate["ok"])
        state["holders"].append({"who":"codex_t99","scopes":["ai"]})
        self.assertFalse(gates.gate_lock_released(lambda: state, "codex_t99")["ok"])

    def test_missing_status_or_usage_line_stops(self):
        self.exec_text = "做完了。\nFILES_CHANGED: x\nWEEKLY_USAGE_REMAINING_PERCENT: 80\n"
        s = self.stops("report_markers")
        self.assertIn("STATUS", next(g for g in s["tasks"][0]["gates"] if g["name"] == "report_markers")["detail"]["missing"])

    def test_missing_usage_line_stops(self):
        self.exec_text = "STATUS: PASS\nFILES_CHANGED: x\n"
        s = self.stops("report_markers")
        self.assertIn("WEEKLY_USAGE_REMAINING_PERCENT", next(g for g in s["tasks"][0]["gates"] if g["name"] == "report_markers")["detail"]["missing"])

    def test_failing_status_line_stops(self):
        self.exec_text = GOOD.replace("STATUS: PASS", "STATUS: FAIL")
        self.stops("report_markers")

    def test_executor_nonzero_exit_or_timeout_stops(self):
        self.exec_result = loop.RunResult(1, GOOD, "boom", False, GOOD)
        self.stops("executor_exit")
        self.setUp(); self.exec_result = loop.RunResult(-1, "", "TIMEOUT", True, "")
        self.stops("executor_exit")

    def test_executor_tampering_with_queue_stops(self):
        def effect(t):
            q = json.loads(self.queue_path.read_text(encoding="utf-8"))
            q["tasks"][1]["allow_rules_change"] = True                             # 偷偷授權後面的任務
            self.queue_path.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
        self.exec_effect = effect
        self.stops("queue_only_status_result")

    def test_executor_marking_other_tasks_done_stops(self):
        def effect(t):
            q = json.loads(self.queue_path.read_text(encoding="utf-8"))
            q["tasks"][1]["status"] = "DONE"
            self.queue_path.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
        self.exec_effect = effect
        self.stops("queue_other_statuses_unchanged")


class StopsRequiringTheUser(LoopBase):
    def test_blocker_stops_for_user_and_next_task_is_not_run(self):
        self.write_queue([make_task("T01"), make_task("T02")])
        self.exec_text = GOOD + "BLOCKER: 需要使用者決定是否升版 rules\n"
        s = self.run_loop(max_tasks=5)
        self.assertEqual(s["stop_reason"], "BLOCKER")
        self.assertEqual(self.status("T01"), "STOPPED_FOR_USER"); self.assertEqual(self.status("T02"), "TODO")
        self.assertEqual(self.calls, {"executor": 1, "reviewer": 0})
        self.assertIn("需要使用者決定", json.dumps(s["stop_detail"], ensure_ascii=False))

    def test_blocker_plus_gate_failure_is_failed(self):
        self.write_queue([make_task("T01")])
        self.exec_text = GOOD + "BLOCKER: x\n"; self.regression_ok = False
        self.run_loop(); self.assertEqual(self.status("T01"), "FAILED")

    def test_stop_for_user_task_is_never_executed(self):
        self.write_queue([make_task("T01"), make_task("T12", deps=["T01"], stop_for_user=True), make_task("T13")])
        s = self.run_loop(max_tasks=9)
        self.assertEqual(s["stop_reason"], "STOP_FOR_USER"); self.assertEqual(s["stop_detail"], "T12")
        self.assertEqual(self.status("T12"), "STOPPED_FOR_USER"); self.assertEqual(self.status("T13"), "TODO")
        self.assertEqual(self.calls["executor"], 1)                                # 只有 T01 被執行

    def test_review_fail_stops(self):
        self.write_queue([make_task("T01"), make_task("T02")])
        self.review_text = "數字對不上。\nREVIEW: FAIL\n"
        s = self.run_loop(max_tasks=5)
        self.assertEqual(s["stop_reason"], "REVIEW_FAILED"); self.assertEqual(self.status("T01"), "FAILED"); self.assertEqual(self.status("T02"), "TODO")

    def test_review_without_explicit_pass_counts_as_fail(self):
        self.write_queue([make_task("T01")])
        self.review_text = "看起來沒問題。"
        self.assertEqual(self.run_loop()["stop_reason"], "REVIEW_FAILED")

    def test_reviewer_writing_a_shared_file_fails_the_review(self):
        self.write_queue([make_task("T01")])
        self.review_effect = lambda t: self.ledger.write_text(self.ledger.read_text(encoding="utf-8") + '{"a":"reviewer"}\n', encoding="utf-8")
        s = self.run_loop()
        self.assertEqual(s["stop_reason"], "REVIEW_FAILED"); self.assertEqual(self.status("T01"), "FAILED")

    def test_invalid_queue_or_leftover_running_task_refuses_to_start(self):
        self.write_queue([make_task("T01", reviewer="gpt-nonsense")])
        self.assertEqual(self.run_loop()["stop_reason"], "QUEUE_INVALID")
        self.setUp(); self.write_queue([make_task("T01", status="RUNNING")])
        self.assertEqual(self.run_loop()["stop_reason"], "PREFLIGHT_FAILED"); self.assertEqual(self.calls["executor"], 0)


class Limits(LoopBase):
    def test_max_tasks(self):
        self.write_queue([make_task(f"T0{i}") for i in range(1, 5)])
        s = self.run_loop(max_tasks=2)
        self.assertEqual((s["stop_reason"], s["done_count"]), ("LIMIT_MAX_TASKS", 2)); self.assertEqual(self.status("T03"), "TODO")

    def test_max_hours(self):
        self.write_queue([make_task("T01"), make_task("T02")])
        orig = self.deps
        def deps():
            d = orig(); inner = d.executor_runner
            def slow(spec, prompt, tag, timeout_s):
                self.clock[0] += dt.timedelta(hours=1.5)
                return inner(spec, prompt, tag, timeout_s)
            d.executor_runner = slow
            return d
        self.deps = deps
        s = self.run_loop(max_tasks=9, max_hours=1.0)
        self.assertEqual((s["stop_reason"], s["done_count"]), ("LIMIT_MAX_HOURS", 1)); self.assertEqual(self.status("T02"), "TODO")

    def test_weekly_usage_below_minimum_stops_after_the_task(self):
        self.write_queue([make_task("T01"), make_task("T02")])
        self.exec_text = GOOD.replace("PERCENT: 80", "PERCENT: 25")
        s = self.run_loop(max_tasks=9, min_weekly=30)
        self.assertEqual((s["stop_reason"], s["done_count"]), ("LIMIT_WEEKLY_LOW", 1)); self.assertEqual(self.status("T01"), "DONE")

    def test_weekly_usage_unknown_twice_stops(self):
        self.write_queue([make_task(f"T0{i}") for i in range(1, 5)])
        self.exec_text = GOOD.replace("PERCENT: 80", "PERCENT: UNKNOWN")
        s = self.run_loop(max_tasks=9)
        self.assertEqual((s["stop_reason"], s["done_count"]), ("WEEKLY_USAGE_UNKNOWN_TWICE", 2))

    def test_unknown_once_then_known_keeps_going(self):
        self.write_queue([make_task("T01"), make_task("T02"), make_task("T03")])
        texts = iter([GOOD.replace("PERCENT: 80", "PERCENT: UNKNOWN"), GOOD, GOOD])
        orig = self.deps
        def deps():
            d = orig()
            d.executor_runner = lambda spec, prompt, tag, t: (lambda x: loop.RunResult(0, x, "", False, x))(next(texts))
            return d
        self.deps = deps
        self.assertEqual(self.run_loop(max_tasks=9)["done_count"], 3)

    def test_busy_lock_is_waited_for_then_stops_after_30_minutes(self):
        self.write_queue([make_task("T01")])
        self.lock["holder"] = {"who": "codex_s3e"}
        s = self.run_loop(lock_wait_min=30, lock_poll_s=60)
        self.assertEqual(s["stop_reason"], "LOCK_BUSY_TIMEOUT"); self.assertEqual(self.calls["executor"], 0)

    def test_lock_released_while_waiting_lets_the_task_run(self):
        self.write_queue([make_task("T01")])
        self.lock["holder"] = {"who": "codex_s3e"}
        orig = self.deps
        def deps():
            d = orig(); sleeps = []
            def sleep(s):
                sleeps.append(s)
                if len(sleeps) == 3: self.lock["holder"] = None
            d.sleep_fn = sleep
            return d
        self.deps = deps
        self.assertEqual(self.run_loop(max_tasks=1)["tasks"][0]["status"], "DONE")


class CommandsAndGates(unittest.TestCase):
    def test_extraction_prompts_carry_the_forbidden_reading_list(self):
        must = ["00_規範/自動任務佇列.json", "02_執行紀錄/", "90_各AI工作區/*/", "ai_field_values*.jsonl", "ai_reviews.jsonl",
                "第一次抽取的內容", "docs_ledger.jsonl", "03_工具/ai_fields.py", "抽取任務禁讀清單",
                "00_規範/使用者決定紀錄.md", "03_工具/test_ai_fields.py", "00_規範/抽取規則摘要_S4.md"]
        for task in (make_task("TX", field="I2"), make_task("TY", field=["C5", "X"]), make_task("TZ", title="S4c I4 第二次獨立抽取")):
            prompt = loop.build_prompt(task)
            self.assertTrue(loop.is_extraction_task(task), task["id"])
            for text in must: self.assertIn(text, prompt, (task["id"], text))
            self.assertIn(task["prompt"], prompt)                         # 任務定義直接寫在提示裡
            forbidden, allowed = prompt.split("允許讀取：")[0], prompt.split("允許讀取：")[1]
            self.assertIn("00_規範/使用者決定紀錄.md", forbidden); self.assertIn("03_工具/test_ai_fields.py", forbidden)
            self.assertIn("00_規範/抽取規則摘要_S4.md", allowed); self.assertNotIn("使用者決定紀錄", allowed)
            self.assertNotIn("rules.json", allowed)
        plain = make_task("TP", title="回歸測試整理")
        self.assertFalse(loop.is_extraction_task(plain))
        self.assertNotIn("抽取任務禁讀清單", loop.build_prompt(plain))
        self.assertNotIn("抽取任務禁讀清單", loop.build_review_prompt(make_task("TX", field="I2"), "r", []))   # 複查者不受限

    def test_forbidden_arguments_are_refused(self):
        for bad in ("--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions", "danger-full-access", "bypassPermissions"):
            with self.assertRaisesRegex(ValueError, "FORBIDDEN_ARGUMENT"):
                gates.assert_safe_command(["codex", "exec", bad])

    def test_built_commands_use_verified_flags_and_no_bypass(self):
        root = Path("C:/x")
        for cli, model in (("codex", "gpt-6-sol"), ("claude", "opus")):
            for role in ("executor", "reviewer"):
                spec = {"cli": cli, "model": model, "effort": "medium", "role": role}
                args = loop.build_command(spec, "EXE", root, Path("out.txt"))
                joined = " ".join(args)
                gates.assert_safe_command(args)
                if cli == "codex":
                    self.assertEqual(args[1], "exec"); self.assertIn("-m", args); self.assertIn('model_reasoning_effort="medium"', args)
                    self.assertEqual(args[args.index("-s") + 1], "workspace-write" if role == "executor" else "read-only")
                    self.assertEqual(args[-1], "-")                          # 提示詞走標準輸入
                    self.assertIn("-C", args); self.assertIn("--output-last-message", args)
                else:
                    self.assertIn("-p", args); self.assertEqual(args[args.index("--model") + 1], model)
                    self.assertEqual(args[args.index("--effort") + 1], "medium"); self.assertIn("--no-session-persistence", args)
                    self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits" if role == "executor" else "dontAsk")
                    if role == "reviewer":
                        self.assertIn("Edit", args[args.index("--disallowedTools") + 1]); self.assertIn("Write", args[args.index("--disallowedTools") + 1])
                self.assertNotIn("dangerously", joined)

    def test_regression_gate_reads_real_unittest_output(self):
        ok = lambda args, cwd=None, timeout=0: {"returncode": 0, "stdout": "", "stderr": "....\n----\nRan 448 tests in 100s\n\nOK\n"}
        self.assertTrue(gates.gate_regression(ok)["ok"])
        for res in ({"returncode": 1, "stdout": "", "stderr": "Ran 448 tests in 1s\n\nFAILED (failures=1)\n"},
                    {"returncode": 0, "stdout": "", "stderr": "Ran 448 tests in 1s\n\nFAILED (errors=1)\n"},
                    {"returncode": 0, "stdout": "", "stderr": ""}):
            self.assertFalse(gates.gate_regression(lambda args, cwd=None, timeout=0, r=res: r)["ok"])

    def test_dry_run_gate_requires_zero_writes_in_output_and_on_disk(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "01_共用資料").mkdir(parents=True); (tmp / "01_共用資料/prices.csv").write_text("a\n", encoding="utf-8")
            good = lambda args, cwd=None, timeout=0: {"returncode": 0, "stdout": '{"write_count": 0}', "stderr": ""}
            self.assertTrue(gates.gate_dry_run(good, tmp)["ok"])
            counted = lambda args, cwd=None, timeout=0: {"returncode": 0, "stdout": '{"write_count": 3}', "stderr": ""}
            self.assertFalse(gates.gate_dry_run(counted, tmp)["ok"])
            def sneaky(args, cwd=None, timeout=0):                           # 輸出說零寫入，實際偷偷寫了檔案
                (tmp / "01_共用資料/prices.csv").write_text("a\nb\n", encoding="utf-8")
                return {"returncode": 0, "stdout": '{"write_count": 0}', "stderr": ""}
            r = gates.gate_dry_run(sneaky, tmp)
            self.assertFalse(r["ok"]); self.assertTrue(r["detail"]["touched"])
            self.assertFalse(gates.gate_dry_run(lambda a, cwd=None, timeout=0: {"returncode": 2, "stdout": '{"write_count": 0}', "stderr": ""}, tmp)["ok"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_report_parsing(self):
        rep = gates.parse_report("STATUS: PASS\nWEEKLY_USAGE_REMAINING_PERCENT: 61.5%\nBLOCKER: 要決定\n")
        self.assertEqual((rep["status"], rep["blockers"]), ("PASS", ["要決定"]))
        self.assertEqual(gates.parse_weekly_percent("61.5%"), 61.5)
        for raw in ("UNKNOWN", None, "n/a", ""):
            self.assertIsNone(gates.parse_weekly_percent(raw))
        self.assertTrue(gates.gate_report_markers("STATUS: ALREADY_DONE\nWEEKLY_USAGE_REMAINING_PERCENT: UNKNOWN\n")["ok"])

    def test_lock_and_temp_files_are_not_tracked(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "01_共用資料/data_spine"; d.mkdir(parents=True)
            (d / "observations.jsonl").write_text("x\n", encoding="utf-8")
            (d / "observations.jsonl.寫入中.完成").write_text("", encoding="utf-8"); (d / "x.jsonl.tmp").write_text("", encoding="utf-8")
            files = gates.snapshot_state(tmp)["files"]
            self.assertEqual(list(files), ["01_共用資料/data_spine/observations.jsonl"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
