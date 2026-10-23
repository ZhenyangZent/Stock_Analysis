"""Isolated tests for the disabled prospective forward tracker candidate.

Set FORWARD_TRACKER_TEST_ROOT to an explicit repository-external directory.
The suite never reads or writes a formal forward ledger.
"""
from __future__ import annotations

import copy
import datetime as dt
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

import forward_tracker_candidate as tracker


def make_plan(status="APPROVED"):
    return {
        "schema_version": "FORWARD_EVALUATION_PLAN_TEST_1",
        "approval_status": status,
        "entry_policy": "NEXT_OPEN_SESSION_DATE_AFTER_PUBLICATION",
        "horizons_by_signal_type": {
            "COMPANY_CLASSIFICATION": [20, 60, 120],
            "RISK_FORECAST": [1, 5, 20],
        },
        "benchmark_by_market": {"US": "SOXX", "TW": "0050"},
        "calendar_version_id": "SYNTHETIC_CALENDAR_V1",
        "market_timezones": {"US": "America/New_York", "TW": "Asia/Taipei"},
        "lifecycle": {
            "append_only": True,
            "mature_terminal": True,
            "void_terminal": True,
        },
    }


def make_calendar(count=130):
    zone = ZoneInfo("America/New_York")
    start = dt.date(2026, 1, 5)
    sessions = []
    for offset in range(count):
        day = start + dt.timedelta(days=offset)
        opened = dt.datetime.combine(day, dt.time(9, 30), zone)
        closed = dt.datetime.combine(day, dt.time(16, 0), zone)
        sessions.append({
            "market": "US",
            "session_date": day.isoformat(),
            "open_at": opened.isoformat(),
            "close_at": closed.isoformat(),
            "session_status": "OPEN",
        })
    return {"version_id": "SYNTHETIC_CALENDAR_V1", "sessions": sessions}


class TrackerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("FORWARD_TRACKER_TEST_ROOT")
        if not configured:
            raise RuntimeError("FORWARD_TRACKER_TEST_ROOT_REQUIRED")
        cls.external_parent = Path(configured).resolve()
        if not cls.external_parent.is_absolute():
            raise RuntimeError("FORWARD_TRACKER_TEST_ROOT_MUST_BE_ABSOLUTE")
        if cls.external_parent == tracker.REPOSITORY_ROOT or tracker.REPOSITORY_ROOT in cls.external_parent.parents:
            raise RuntimeError("FORWARD_TRACKER_TEST_ROOT_MUST_BE_OUTSIDE_REPOSITORY")
        cls.external_parent.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tracker77_", dir=self.external_parent))
        self.plan = make_plan()
        self.plan_sha = tracker.object_sha256(self.plan)
        self.calendar = make_calendar()
        self.calendar_sha = tracker.object_sha256(self.calendar)
        self.membership = {
            "membership_id": "MEMBERSHIP_2026_01_05",
            "as_of": "2026-01-05",
            "members": ["SEC_MU_US"],
        }
        self.membership_sha = tracker.object_sha256(self.membership)
        self.context = {
            "plans": {self.plan_sha: self.plan},
            "approved_plan_sha256s": [self.plan_sha],
            "calendars": {("SYNTHETIC_CALENDAR_V1", self.calendar_sha): self.calendar},
            "memberships": {("MEMBERSHIP_2026_01_05", self.membership_sha): self.membership},
            "price_bundles": {},
            "action_bundles": {},
        }
        self.config = tracker.default_config()
        self.config.update({
            "tracker_enabled": True,
            "evaluation_plan_sha256": self.plan_sha,
            "storage_root": str(self.root),
        })
        plan_approval = tracker.build_plan_approval_record(
            self.plan_sha, "2026-01-05T17:00:00+00:00")
        self.config["plan_approval_record"] = plan_approval
        self.config["plan_approval_sha256"] = tracker.object_sha256(plan_approval)
        authority = tracker.build_activation_authority(
            self.plan_sha, self.root, "2026-01-05T18:00:00+00:00")
        self.config["authority_record"] = authority
        self.config["authority_sha256"] = tracker.object_sha256(authority)
        self.service = tracker.ForwardTrackerCandidate(
            self.config, self.plan, self.context, self.root)
        self.signal = self.make_signal()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=False)

    def make_signal(self):
        signal = {
            "schema_version": "FORWARD_SIGNAL_1_CANDIDATE",
            "signal_type": "COMPANY_CLASSIFICATION",
            "subject_id": "SEC_MU_US",
            "market": "US",
            "snapshot_id": "SYNTHETIC_SNAPSHOT_1",
            "snapshot_sha256": "1" * 64,
            "manifest_sha256": "2" * 64,
            "rules_version": "1.13.2",
            "rules_sha256": "3" * 64,
            "data_cutoff_at": "2026-01-05T19:00:00+00:00",
            "signal_published_at": "2026-01-05T20:00:00+00:00",
            "observed_at": "2026-01-05T20:30:00+00:00",
            "signal_payload_sha256": "4" * 64,
            "evaluation_plan_sha256": self.plan_sha,
            "benchmark_id": "SOXX",
            "calendar_version_id": "SYNTHETIC_CALENDAR_V1",
            "calendar_sha256": self.calendar_sha,
            "universe_membership_id": "MEMBERSHIP_2026_01_05",
            "universe_membership_sha256": self.membership_sha,
            "horizons_sessions": [20, 60, 120],
            "entry_policy": "NEXT_OPEN_SESSION_DATE_AFTER_PUBLICATION",
            "not_investment_advice": True,
        }
        signal["signal_id"] = tracker.expected_signal_id(signal)
        return signal

    def make_mature(self, pending):
        path = self.service._session_path(
            self.signal, pending["entry_session"], pending["horizon_sessions"])
        dates = [row["session_date"] for row in path]
        matured = path[-1]["close_at"]
        matured_dt = tracker.parse_timestamp(matured, "MATURED")
        available = (matured_dt + dt.timedelta(minutes=1)).isoformat()
        fetched = (matured_dt + dt.timedelta(minutes=2)).isoformat()
        observed = (matured_dt + dt.timedelta(minutes=3)).isoformat()
        price = {
            "subject_id": "SEC_MU_US",
            "benchmark_id": "SOXX",
            "source_id": "SYNTHETIC_PRICE_SOURCE",
            "coverage_start": dates[0],
            "coverage_end": dates[-1],
            "available_at": available,
            "fetched_at": fetched,
            "total_return_index": {
                day: {"stock": 100.0 + index, "benchmark": 100.0 + index * 0.5}
                for index, day in enumerate(dates)
            },
        }
        actions = {
            "subject_id": "SEC_MU_US",
            "benchmark_id": "SOXX",
            "source_id": "SYNTHETIC_ACTION_SOURCE",
            "coverage_start": dates[0],
            "coverage_end": dates[-1],
            "available_at": available,
            "fetched_at": fetched,
            "actions": [],
        }
        price_sha = tracker.object_sha256(price)
        action_sha = tracker.object_sha256(actions)
        self.context["price_bundles"][price_sha] = price
        self.context["action_bundles"][action_sha] = actions
        stock = [price["total_return_index"][day]["stock"] for day in dates]
        bench = [price["total_return_index"][day]["benchmark"] for day in dates]
        stock_path = [value / stock[0] - 1 for value in stock[1:]]
        bench_path = [value / bench[0] - 1 for value in bench[1:]]
        relative = [s - b for s, b in zip(stock_path, bench_path)]
        returns = [stock[index] / stock[index - 1] - 1 for index in range(1, len(stock))]
        mean = sum(returns) / len(returns)
        event = {
            "schema_version": "FORWARD_OUTCOME_EVENT_1_CANDIDATE",
            "event_type": "OUTCOME_MATURED",
            "outcome_id": pending["outcome_id"],
            "signal_id": self.signal["signal_id"],
            "horizon_sessions": pending["horizon_sessions"],
            "evaluation_plan_sha256": self.plan_sha,
            "event_sequence": 1,
            "prior_event_id": pending["event_id"],
            "observed_at": observed,
            "entry_session": pending["entry_session"],
            "end_session": dates[-1],
            "outcome_matured_at": matured,
            "price_input_sha256": price_sha,
            "corporate_action_input_sha256": action_sha,
            "calendar_version_id": "SYNTHETIC_CALENDAR_V1",
            "calendar_sha256": self.calendar_sha,
            "benchmark_id": "SOXX",
            "subject_id": "SEC_MU_US",
            "stock_total_return": stock_path[-1],
            "benchmark_total_return": bench_path[-1],
            "excess_return": relative[-1],
            "max_adverse_excursion": min(relative),
            "max_favorable_excursion": max(relative),
            "realized_volatility": math.sqrt(
                sum((value - mean) ** 2 for value in returns) / len(returns)),
            "include_in_denominator": True,
        }
        event["event_id"] = tracker.expected_event_id(event)
        return event

    def make_void(self, pending):
        event = {
            "schema_version": "FORWARD_OUTCOME_EVENT_1_CANDIDATE",
            "event_type": "OUTCOME_VOIDED",
            "outcome_id": pending["outcome_id"],
            "signal_id": self.signal["signal_id"],
            "horizon_sessions": pending["horizon_sessions"],
            "evaluation_plan_sha256": self.plan_sha,
            "event_sequence": 1,
            "prior_event_id": pending["event_id"],
            "observed_at": "2026-05-20T21:00:00+00:00",
            "void_reason": "SYNTHETIC_TERMINAL_VALUE_UNVERIFIABLE",
            "include_in_denominator": True,
        }
        event["event_id"] = tracker.expected_event_id(event)
        return event

    def test_default_config_is_disabled_and_inert(self):
        config = tracker.default_config()
        service = tracker.ForwardTrackerCandidate(config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "TRACKER_DISABLED"):
            service.state()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_import_has_no_filesystem_side_effect(self):
        probe = self.root / "import_probe"
        probe.mkdir()
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent),
                   PYTHONDONTWRITEBYTECODE="1")
        subprocess.run(
            [sys.executable, "-B", "-c", "import forward_tracker_candidate"],
            cwd=probe, env=env, check=True, capture_output=True, text=True)
        self.assertEqual(list(probe.iterdir()), [])

    def test_candidate_plan_is_rejected_even_with_activation_authority(self):
        plan = make_plan("CANDIDATE_NOT_APPROVED")
        plan_sha = tracker.object_sha256(plan)
        config = copy.deepcopy(self.config)
        config["evaluation_plan_sha256"] = plan_sha
        authority = tracker.build_activation_authority(
            plan_sha, self.root, "2026-01-05T18:00:00+00:00")
        config["authority_record"] = authority
        config["authority_sha256"] = tracker.object_sha256(authority)
        context = copy.deepcopy(self.context)
        context["plans"] = {plan_sha: plan}
        context["approved_plan_sha256s"] = [plan_sha]
        plan_approval = tracker.build_plan_approval_record(
            plan_sha, "2026-01-05T17:00:00+00:00")
        config["plan_approval_record"] = plan_approval
        config["plan_approval_sha256"] = tracker.object_sha256(plan_approval)
        service = tracker.ForwardTrackerCandidate(config, plan, context, self.root)
        with self.assertRaisesRegex(ValueError, "EVALUATION_PLAN_NOT_APPROVED"):
            service.state()

    def test_plan_approval_authority_is_separate_and_required(self):
        self.config["plan_approval_record"] = None
        self.config["plan_approval_sha256"] = None
        service = tracker.ForwardTrackerCandidate(
            self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "PLAN_APPROVAL_AUTHORITY_REQUIRED"):
            service.state()

    def test_plan_approval_record_is_hash_bound_and_precedes_activation(self):
        self.config["plan_approval_sha256"] = "f" * 64
        service = tracker.ForwardTrackerCandidate(
            self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "PLAN_APPROVAL_CONTENT_HASH_MISMATCH"):
            service.state()
        approval = tracker.build_plan_approval_record(
            self.plan_sha, "2026-01-05T19:00:00+00:00")
        self.config["plan_approval_record"] = approval
        self.config["plan_approval_sha256"] = tracker.object_sha256(approval)
        service = tracker.ForwardTrackerCandidate(
            self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "ACTIVATION_AUTHORITY_PRECEDES_PLAN_APPROVAL"):
            service.state()

    def test_activation_authority_is_required_and_hash_bound(self):
        self.config["authority_record"] = None
        self.config["authority_sha256"] = None
        service = tracker.ForwardTrackerCandidate(self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "ACTIVATION_AUTHORITY_REQUIRED"):
            service.state()
        self.config["authority_record"] = {"approved": True}
        self.config["authority_sha256"] = "a" * 64
        service = tracker.ForwardTrackerCandidate(self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "AUTHORITY_CONTENT_HASH_MISMATCH"):
            service.state()

    def test_formal_ledger_path_is_always_forbidden(self):
        self.config["formal_ledger_path"] = str(tracker.REPOSITORY_ROOT / "04_回測" / "forward.jsonl")
        service = tracker.ForwardTrackerCandidate(self.config, self.plan, self.context, self.root)
        with self.assertRaisesRegex(ValueError, "FORMAL_LEDGER_PATH_FORBIDDEN"):
            service.state()

    def test_repository_and_relative_storage_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "MUST_BE_ABSOLUTE"):
            tracker.ForwardTrackerCandidate(self.config, self.plan, self.context, "relative")
        config = copy.deepcopy(self.config)
        inside = tracker.REPOSITORY_ROOT / "synthetic-forbidden"
        config["storage_root"] = str(inside)
        service = tracker.ForwardTrackerCandidate(config, self.plan, self.context, inside)
        with self.assertRaisesRegex(ValueError, "REPOSITORY_STORAGE_FORBIDDEN"):
            service.state()

    def test_any_git_worktree_storage_is_rejected(self):
        other_git_root = self.root / "disposable_git_worktree"
        subprocess.run(
            ["git", "init", "--quiet", str(other_git_root)],
            check=True, capture_output=True, text=True)
        self.assertTrue((other_git_root / ".git").exists())
        outside_candidate_but_in_git = other_git_root / "synthetic-forbidden-77"
        config = copy.deepcopy(self.config)
        config["storage_root"] = str(outside_candidate_but_in_git)
        service = tracker.ForwardTrackerCandidate(
            config, self.plan, self.context, outside_candidate_but_in_git)
        with self.assertRaisesRegex(ValueError, "STORAGE_INSIDE_GIT_WORKTREE_FORBIDDEN"):
            service.state()

    def test_signal_time_causality_is_enforced(self):
        bad = copy.deepcopy(self.signal)
        bad["data_cutoff_at"] = "2026-01-05T20:01:00+00:00"
        bad["signal_id"] = tracker.expected_signal_id(bad)
        with self.assertRaisesRegex(ValueError, "DATA_CUTOFF_AFTER_SIGNAL_PUBLICATION"):
            self.service.receive_signal(bad)
        bad = copy.deepcopy(self.signal)
        bad["observed_at"] = "2026-01-05T19:59:00+00:00"
        bad["signal_id"] = tracker.expected_signal_id(bad)
        with self.assertRaisesRegex(ValueError, "SIGNAL_PUBLICATION_AFTER_OBSERVATION"):
            self.service.receive_signal(bad)

    def test_signal_and_outcome_ids_are_derived_not_arbitrary(self):
        bad = copy.deepcopy(self.signal)
        bad["signal_id"] = "SIG_" + "f" * 64
        with self.assertRaisesRegex(ValueError, "SIGNAL_ID_INVALID"):
            self.service.receive_signal(bad)
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        pending["outcome_id"] = "OUT_" + "f" * 64
        pending["event_id"] = tracker.expected_event_id(pending)
        with self.assertRaisesRegex(ValueError, "OUTCOME_ID_INVALID"):
            self.service.receive_event(pending)

    def test_membership_identity_and_membership_are_hash_bound(self):
        bad = copy.deepcopy(self.signal)
        bad["universe_membership_sha256"] = "f" * 64
        bad["signal_id"] = tracker.expected_signal_id(bad)
        with self.assertRaisesRegex(ValueError, "UNIVERSE_MEMBERSHIP_IDENTITY_NOT_BOUND"):
            self.service.receive_signal(bad)
        bad = copy.deepcopy(self.signal)
        membership = dict(self.membership, members=["SEC_OTHER_US"])
        digest = tracker.object_sha256(membership)
        self.context["memberships"][(membership["membership_id"], digest)] = membership
        bad["universe_membership_sha256"] = digest
        bad["signal_id"] = tracker.expected_signal_id(bad)
        with self.assertRaisesRegex(ValueError, "SUBJECT_NOT_IN_POINT_IN_TIME_UNIVERSE"):
            self.service.receive_signal(bad)

    def test_duplicate_signal_is_idempotent_and_mutation_fails(self):
        self.assertEqual(self.service.receive_signal(self.signal), "APPENDED")
        self.assertEqual(self.service.receive_signal(copy.deepcopy(self.signal)), "ALREADY_APPLIED")
        changed = copy.deepcopy(self.signal)
        changed["signal_payload_sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "SIGNAL_ID_INVALID"):
            self.service.receive_signal(changed)

    def test_all_preregistered_horizons_derive_distinct_pending_ids(self):
        self.service.receive_signal(self.signal)
        events = [self.service.derive_pending(
            self.signal, horizon, "2026-01-05T21:00:00+00:00") for horizon in (20, 60, 120)]
        self.assertEqual(len({event["outcome_id"] for event in events}), 3)
        self.assertEqual([event["horizon_sessions"] for event in events], [20, 60, 120])

    def test_pending_to_mature_lifecycle_and_duplicate_replay(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.assertEqual(self.service.receive_event(pending), "APPENDED")
        mature = self.make_mature(pending)
        self.assertEqual(self.service.receive_event(mature), "APPENDED")
        self.assertEqual(self.service.receive_event(copy.deepcopy(mature)), "ALREADY_APPLIED")
        self.assertEqual(self.service.state(), {
            "signal_count": 1, "event_count": 2, "PENDING": 0, "MATURE": 1, "VOID": 0})

    def test_pending_to_void_is_terminal_and_preserves_denominator(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.service.receive_event(pending)
        void = self.make_void(pending)
        self.service.receive_event(void)
        self.assertTrue(void["include_in_denominator"])
        mature = self.make_mature(pending)
        with self.assertRaisesRegex(ValueError, "OUTCOME_ALREADY_TERMINAL"):
            self.service.receive_event(mature)

    def test_early_or_incomplete_maturity_is_rejected(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.service.receive_event(pending)
        mature = self.make_mature(pending)
        mature["end_session"] = pending["entry_session"]
        mature["event_id"] = tracker.expected_event_id(mature)
        with self.assertRaisesRegex(ValueError, "END_SESSION_NOT_COMPLETE_HORIZON"):
            self.service.receive_event(mature)

    def test_missing_prices_fail_closed_without_forward_fill(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.service.receive_event(pending)
        mature = self.make_mature(pending)
        bundle = self.context["price_bundles"].pop(mature["price_input_sha256"])
        bundle["total_return_index"].pop(next(iter(bundle["total_return_index"])))
        digest = tracker.object_sha256(bundle)
        self.context["price_bundles"][digest] = bundle
        mature["price_input_sha256"] = digest
        mature["event_id"] = tracker.expected_event_id(mature)
        with self.assertRaisesRegex(ValueError, "PRICE_PATH_IDENTITY_INCOMPLETE"):
            self.service.receive_event(mature)

    def test_corporate_action_identity_mismatch_fails_closed(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.service.receive_event(pending)
        mature = self.make_mature(pending)
        bundle = self.context["action_bundles"].pop(mature["corporate_action_input_sha256"])
        bundle["subject_id"] = "SEC_OTHER_US"
        digest = tracker.object_sha256(bundle)
        self.context["action_bundles"][digest] = bundle
        mature["corporate_action_input_sha256"] = digest
        mature["event_id"] = tracker.expected_event_id(mature)
        with self.assertRaisesRegex(ValueError, "CORPORATE_ACTION_IDENTITY_MISMATCH"):
            self.service.receive_event(mature)

    def test_interrupted_signal_append_recovers_and_replays(self):
        with self.assertRaisesRegex(RuntimeError, "SYNTHETIC_INTERRUPTION"):
            self.service.receive_signal(self.signal, fault_stage="AFTER_CANDIDATE_WRITE")
        self.assertFalse(self.service.signal_path.exists())
        self.assertEqual(self.service.recover(), {"recovered": [tracker.SIGNAL_FILE]})
        self.assertEqual(self.service.receive_signal(self.signal), "ALREADY_APPLIED")

    def test_calendar_identity_mismatch_fails_closed(self):
        bad = copy.deepcopy(self.signal)
        bad["calendar_sha256"] = "e" * 64
        bad["signal_id"] = tracker.expected_signal_id(bad)
        with self.assertRaisesRegex(ValueError, "CALENDAR_IDENTITY_NOT_BOUND"):
            self.service.receive_signal(bad)

    def test_metrics_are_recomputed_and_cannot_be_forged(self):
        self.service.receive_signal(self.signal)
        pending = self.service.derive_pending(self.signal, 20, "2026-01-05T21:00:00+00:00")
        self.service.receive_event(pending)
        mature = self.make_mature(pending)
        mature["excess_return"] += 0.01
        mature["event_id"] = tracker.expected_event_id(mature)
        with self.assertRaisesRegex(ValueError, "EXCESS_RETURN_MISMATCH"):
            self.service.receive_event(mature)

    def test_no_formal_or_historical_path_exists_in_public_interface(self):
        public = set(tracker.__all__)
        self.assertNotIn("LEDGER", public)
        self.assertNotIn("BACKFILL", public)
        source = Path(tracker.__file__).read_text(encoding="utf-8")
        self.assertNotIn("分類成效追蹤_2026-10-19.json", source)
        self.assertNotIn("風險預測帳本", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
