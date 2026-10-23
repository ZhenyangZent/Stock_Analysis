"""Fail-closed prospective forward tracker candidate.

This module is intentionally separate from every formal classification, risk,
snapshot and outcome path.  Importing it performs no I/O.  The tracker is
disabled by default and can only write its two fixed synthetic JSONL filenames
under an explicit absolute directory outside the repository.

The accepted evaluation plan and activation authority are separate, canonical
SHA-256-bound inputs.  The MARKET-74 candidate plan is *not* approved and is
therefore rejected by this repository candidate.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo


EXECUTION_MODE = "EXTERNAL_SYNTHETIC_ONLY"
AUTHORITY_SCOPE = "ENABLE_EXTERNAL_SYNTHETIC_PROSPECTIVE_TRACKER"
PLAN_APPROVAL_SCOPE = "APPROVE_FORWARD_EVALUATION_PLAN"
SIGNAL_FILE = "prospective_signals.synthetic.jsonl"
EVENT_FILE = "prospective_outcomes.synthetic.jsonl"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EVENT_TYPES = {"OUTCOME_PENDING", "OUTCOME_MATURED", "OUTCOME_VOIDED"}
METRIC_FIELDS = {
    "stock_total_return", "benchmark_total_return", "excess_return",
    "max_adverse_excursion", "max_favorable_excursion", "realized_volatility",
}
FORBIDDEN_FIELDS = {"new_threshold", "reclassified_label", "rule_override"}
SIGNAL_ID_FIELDS = (
    "signal_type", "subject_id", "market", "snapshot_id", "snapshot_sha256",
    "manifest_sha256", "rules_version", "rules_sha256", "data_cutoff_at",
    "signal_published_at", "signal_payload_sha256", "evaluation_plan_sha256",
    "benchmark_id", "calendar_version_id", "calendar_sha256",
    "universe_membership_id", "universe_membership_sha256",
)


def canonical_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def object_sha256(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def require_sha256(value, field: str) -> None:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise ValueError(f"{field}_INVALID_SHA256")


def parse_timestamp(value, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field}_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_TIMEZONE_REQUIRED")
    return parsed


def parse_date(value, field: str) -> dt.date:
    if not isinstance(value, str):
        raise ValueError(f"{field}_INVALID")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field}_INVALID") from exc


def _require(record: dict, fields) -> None:
    missing = sorted(field for field in fields if field not in record)
    if missing:
        raise ValueError("REQUIRED_FIELD_MISSING:" + ",".join(missing))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _containing_git_worktree(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def default_config() -> dict:
    """Return a fresh, inert config; no filesystem access is performed."""
    return {
        "schema_version": "FORWARD_TRACKER_CONFIG_1_CANDIDATE",
        "tracker_enabled": False,
        "execution_mode": EXECUTION_MODE,
        "evaluation_plan_sha256": None,
        "storage_root": None,
        "formal_ledger_path": None,
        "plan_approval_record": None,
        "plan_approval_sha256": None,
        "authority_record": None,
        "authority_sha256": None,
    }


def storage_root_sha256(path: Path) -> str:
    return object_sha256({"external_storage_root": str(path.resolve()).replace("\\", "/")})


def expected_signal_id(signal: dict) -> str:
    return "SIG_" + object_sha256({field: signal.get(field) for field in SIGNAL_ID_FIELDS})


def expected_outcome_id(signal_id: str, horizon_sessions: int, plan_sha256: str) -> str:
    return "OUT_" + object_sha256({
        "signal_id": signal_id,
        "horizon_sessions": horizon_sessions,
        "evaluation_plan_sha256": plan_sha256,
    })


def expected_event_id(event: dict) -> str:
    return "OEV_" + object_sha256({key: value for key, value in event.items() if key != "event_id"})


def build_activation_authority(plan_sha256: str, storage_root, issued_at: str,
                               authority_id: str = "SYNTHETIC_TEST_AUTHORITY") -> dict:
    """Build external-only authority; this does not approve an evaluation plan."""
    require_sha256(plan_sha256, "EVALUATION_PLAN")
    parse_timestamp(issued_at, "AUTHORITY_ISSUED_AT")
    root = Path(storage_root)
    if not root.is_absolute():
        raise ValueError("EXTERNAL_STORAGE_ROOT_MUST_BE_ABSOLUTE")
    resolved = root.resolve()
    if _is_within(resolved, REPOSITORY_ROOT):
        raise ValueError("REPOSITORY_STORAGE_FORBIDDEN")
    return {
        "schema_version": "FORWARD_TRACKER_AUTHORITY_1_CANDIDATE",
        "authority_id": authority_id,
        "approved": True,
        "scope": AUTHORITY_SCOPE,
        "execution_mode": EXECUTION_MODE,
        "evaluation_plan_sha256": plan_sha256,
        "external_storage_root_sha256": storage_root_sha256(resolved),
        "issued_at": issued_at,
        "formal_outcome_write_authorized": False,
        "historical_backfill_authorized": False,
        "rule_change_authorized": False,
    }


def build_plan_approval_record(plan_sha256: str, issued_at: str,
                               authority_id: str = "SYNTHETIC_TEST_PLAN_APPROVAL") -> dict:
    """Build a synthetic approval record without granting tracker activation."""
    require_sha256(plan_sha256, "EVALUATION_PLAN")
    parse_timestamp(issued_at, "PLAN_APPROVAL_ISSUED_AT")
    return {
        "schema_version": "FORWARD_PLAN_APPROVAL_1_CANDIDATE",
        "authority_id": authority_id,
        "approved": True,
        "scope": PLAN_APPROVAL_SCOPE,
        "evaluation_plan_sha256": plan_sha256,
        "tracker_activation_authorized": False,
        "formal_outcome_write_authorized": False,
        "historical_backfill_authorized": False,
        "issued_at": issued_at,
    }


class ForwardTrackerCandidate:
    """Append-only candidate that can never target a formal repository path."""

    def __init__(self, config: dict, plan: dict, context: dict, storage_root):
        self.config = copy.deepcopy(config)
        self.plan = copy.deepcopy(plan)
        self.context = context
        self._raw_root = Path(storage_root)
        if not self._raw_root.is_absolute():
            raise ValueError("EXTERNAL_STORAGE_ROOT_MUST_BE_ABSOLUTE")
        self.root = self._raw_root.resolve()
        self.signal_path = self.root / SIGNAL_FILE
        self.event_path = self.root / EVENT_FILE

    def _path_gate(self) -> None:
        current = self._raw_root.resolve()
        if current != self.root:
            raise ValueError("EXTERNAL_STORAGE_ROOT_RESOLUTION_CHANGED")
        if _is_within(current, REPOSITORY_ROOT):
            raise ValueError("REPOSITORY_STORAGE_FORBIDDEN")
        if _containing_git_worktree(current) is not None:
            raise ValueError("STORAGE_INSIDE_GIT_WORKTREE_FORBIDDEN")
        for path in (self.signal_path, self.event_path,
                     self.signal_path.with_suffix(self.signal_path.suffix + ".candidate"),
                     self.event_path.with_suffix(self.event_path.suffix + ".candidate")):
            if path.exists() and path.is_symlink():
                raise ValueError("SYMLINK_STORAGE_TARGET_FORBIDDEN")
            if path.parent.resolve() != self.root:
                raise ValueError("STORAGE_PATH_ESCAPE")

    def _gate(self, observed_at: str | None = None) -> str:
        if self.config.get("tracker_enabled") is not True:
            raise ValueError("TRACKER_DISABLED")
        if self.config.get("execution_mode") != EXECUTION_MODE:
            raise ValueError("EXECUTION_MODE_FORBIDDEN")
        if self.config.get("formal_ledger_path") is not None:
            raise ValueError("FORMAL_LEDGER_PATH_FORBIDDEN")
        configured = self.config.get("storage_root")
        if not isinstance(configured, str) or Path(configured).resolve() != self.root:
            raise ValueError("EXTERNAL_STORAGE_ROOT_MISMATCH")
        self._path_gate()

        plan_sha = object_sha256(self.plan)
        require_sha256(self.config.get("evaluation_plan_sha256"), "CONFIG_EVALUATION_PLAN")
        if self.config["evaluation_plan_sha256"] != plan_sha:
            raise ValueError("CONFIG_PLAN_SHA256_MISMATCH")
        if self.plan.get("approval_status") != "APPROVED":
            raise ValueError("EVALUATION_PLAN_NOT_APPROVED")
        if self.context.get("plans", {}).get(plan_sha) != self.plan:
            raise ValueError("PLAN_CONTENT_NOT_BOUND_IN_CONTEXT")
        plan_approval = self.config.get("plan_approval_record")
        plan_approval_hash = self.config.get("plan_approval_sha256")
        if not isinstance(plan_approval, dict) or plan_approval_hash is None:
            raise ValueError("PLAN_APPROVAL_AUTHORITY_REQUIRED")
        require_sha256(plan_approval_hash, "PLAN_APPROVAL_AUTHORITY")
        if object_sha256(plan_approval) != plan_approval_hash:
            raise ValueError("PLAN_APPROVAL_CONTENT_HASH_MISMATCH")
        plan_approval_required = {
            "approved": True,
            "scope": PLAN_APPROVAL_SCOPE,
            "evaluation_plan_sha256": plan_sha,
            "tracker_activation_authorized": False,
            "formal_outcome_write_authorized": False,
            "historical_backfill_authorized": False,
        }
        for field, expected in plan_approval_required.items():
            if plan_approval.get(field) != expected:
                raise ValueError("PLAN_APPROVAL_FIELD_MISMATCH:" + field)
        plan_approved_at = parse_timestamp(
            plan_approval.get("issued_at"), "PLAN_APPROVAL_ISSUED_AT")

        authority = self.config.get("authority_record")
        authority_hash = self.config.get("authority_sha256")
        if not isinstance(authority, dict) or authority_hash is None:
            raise ValueError("ACTIVATION_AUTHORITY_REQUIRED")
        require_sha256(authority_hash, "ACTIVATION_AUTHORITY")
        if object_sha256(authority) != authority_hash:
            raise ValueError("AUTHORITY_CONTENT_HASH_MISMATCH")
        required = {
            "approved": True,
            "scope": AUTHORITY_SCOPE,
            "execution_mode": EXECUTION_MODE,
            "evaluation_plan_sha256": plan_sha,
            "external_storage_root_sha256": storage_root_sha256(self.root),
            "formal_outcome_write_authorized": False,
            "historical_backfill_authorized": False,
            "rule_change_authorized": False,
        }
        for field, expected in required.items():
            if authority.get(field) != expected:
                raise ValueError("AUTHORITY_FIELD_MISMATCH:" + field)
        issued = parse_timestamp(authority.get("issued_at"), "AUTHORITY_ISSUED_AT")
        if issued < plan_approved_at:
            raise ValueError("ACTIVATION_AUTHORITY_PRECEDES_PLAN_APPROVAL")
        if observed_at is not None and issued > parse_timestamp(observed_at, "OBSERVED_AT"):
            raise ValueError("AUTHORITY_ISSUED_AFTER_OPERATION")
        return plan_sha

    def _bound(self, group: str, key, digest: str, label: str) -> dict:
        require_sha256(digest, label)
        value = self.context.get(group, {}).get(key)
        if value is None:
            raise ValueError(label + "_IDENTITY_NOT_BOUND")
        if object_sha256(value) != digest:
            raise ValueError(label + "_CONTENT_HASH_MISMATCH")
        return value

    def _calendar(self, signal: dict) -> list[dict]:
        calendar = self._bound(
            "calendars", (signal["calendar_version_id"], signal["calendar_sha256"]),
            signal["calendar_sha256"], "CALENDAR")
        rows = calendar.get("sessions")
        if not isinstance(rows, list):
            raise ValueError("CALENDAR_SESSIONS_INVALID")
        seen = set()
        timezone = ZoneInfo(self.plan["market_timezones"][signal["market"]])
        for row in rows:
            if row.get("market") != signal["market"]:
                continue
            day = parse_date(row.get("session_date"), "SESSION_DATE")
            if day in seen:
                raise ValueError("CALENDAR_DUPLICATE_SESSION_DATE")
            seen.add(day)
            opened = parse_timestamp(row.get("open_at"), "SESSION_OPEN_AT")
            closed = parse_timestamp(row.get("close_at"), "SESSION_CLOSE_AT")
            if not opened < closed:
                raise ValueError("SESSION_TIME_ORDER_INVALID")
            for instant in (opened, closed):
                local = instant.astimezone(timezone)
                if local.date() != day or instant.utcoffset() != local.utcoffset():
                    raise ValueError("SESSION_TIMEZONE_OR_DATE_MISMATCH")
            if row.get("session_status") not in {"OPEN", "CLOSED"}:
                raise ValueError("SESSION_STATUS_INVALID")
        return rows

    def validate_signal(self, signal: dict) -> bool:
        required = set(SIGNAL_ID_FIELDS) | {
            "schema_version", "signal_id", "observed_at", "horizons_sessions",
            "entry_policy", "not_investment_advice",
        }
        _require(signal, required)
        if FORBIDDEN_FIELDS & set(signal):
            raise ValueError("FORMAL_RULE_MUTATION_FORBIDDEN")
        self._gate(signal["observed_at"])
        for field in ("snapshot_sha256", "manifest_sha256", "rules_sha256",
                      "signal_payload_sha256", "evaluation_plan_sha256",
                      "calendar_sha256", "universe_membership_sha256"):
            require_sha256(signal[field], field.upper())
        cutoff = parse_timestamp(signal["data_cutoff_at"], "DATA_CUTOFF_AT")
        published = parse_timestamp(signal["signal_published_at"], "SIGNAL_PUBLISHED_AT")
        observed = parse_timestamp(signal["observed_at"], "OBSERVED_AT")
        if cutoff > published:
            raise ValueError("DATA_CUTOFF_AFTER_SIGNAL_PUBLICATION")
        if published > observed:
            raise ValueError("SIGNAL_PUBLICATION_AFTER_OBSERVATION")
        signal_type = signal.get("signal_type")
        allowed = (self.plan.get("horizons_by_signal_type") or {}).get(signal_type)
        if allowed is None:
            raise ValueError("SIGNAL_TYPE_NOT_IN_PLAN")
        if set(signal["horizons_sessions"]) != set(allowed):
            raise ValueError("SIGNAL_HORIZONS_DO_NOT_MATCH_PLAN")
        if signal["entry_policy"] != self.plan.get("entry_policy"):
            raise ValueError("ENTRY_POLICY_DOES_NOT_MATCH_PLAN")
        if signal["benchmark_id"] != self.plan["benchmark_by_market"].get(signal["market"]):
            raise ValueError("BENCHMARK_ID_DOES_NOT_MATCH_PLAN")
        if signal["calendar_version_id"] != self.plan.get("calendar_version_id"):
            raise ValueError("CALENDAR_VERSION_DOES_NOT_MATCH_PLAN")
        self._calendar(signal)
        membership = self._bound(
            "memberships", (signal["universe_membership_id"], signal["universe_membership_sha256"]),
            signal["universe_membership_sha256"], "UNIVERSE_MEMBERSHIP")
        if signal["subject_id"] not in membership.get("members", []):
            raise ValueError("SUBJECT_NOT_IN_POINT_IN_TIME_UNIVERSE")
        if signal["signal_id"] != expected_signal_id(signal):
            raise ValueError("SIGNAL_ID_INVALID")
        if signal.get("not_investment_advice") is not True:
            raise ValueError("NOT_INVESTMENT_ADVICE_FLAG_REQUIRED")
        return True

    def first_entry_session(self, signal: dict) -> str:
        self.validate_signal(signal)
        published = parse_timestamp(signal["signal_published_at"], "SIGNAL_PUBLISHED_AT")
        timezone = ZoneInfo(self.plan["market_timezones"][signal["market"]])
        local_date = published.astimezone(timezone).date()
        candidates = []
        for row in self._calendar(signal):
            if row.get("market") != signal["market"] or row.get("session_status") != "OPEN":
                continue
            day = parse_date(row["session_date"], "SESSION_DATE")
            close = parse_timestamp(row["close_at"], "SESSION_CLOSE_AT")
            if self.plan["entry_policy"] == "NEXT_OPEN_SESSION_DATE_AFTER_PUBLICATION":
                eligible = day > local_date
            elif self.plan["entry_policy"] == "FIRST_COMMON_CLOSE_AFTER_PUBLICATION":
                eligible = close > published
            else:
                raise ValueError("ENTRY_POLICY_UNKNOWN")
            if eligible:
                candidates.append((close, day.isoformat()))
        if not candidates:
            raise ValueError("NO_ELIGIBLE_ENTRY_SESSION")
        return min(candidates)[1]

    def derive_pending(self, signal: dict, horizon_sessions: int, observed_at: str) -> dict:
        self.validate_signal(signal)
        if horizon_sessions not in signal["horizons_sessions"]:
            raise ValueError("HORIZON_NOT_PREREGISTERED")
        event = {
            "schema_version": "FORWARD_OUTCOME_EVENT_1_CANDIDATE",
            "event_type": "OUTCOME_PENDING",
            "outcome_id": expected_outcome_id(signal["signal_id"], horizon_sessions,
                                               signal["evaluation_plan_sha256"]),
            "signal_id": signal["signal_id"],
            "horizon_sessions": horizon_sessions,
            "evaluation_plan_sha256": signal["evaluation_plan_sha256"],
            "event_sequence": 0,
            "prior_event_id": None,
            "observed_at": observed_at,
            "entry_session": self.first_entry_session(signal),
            "outcome_matured_at": None,
            "include_in_denominator": True,
        }
        event["event_id"] = expected_event_id(event)
        return event

    @staticmethod
    def _signal(signals: list[dict], signal_id: str) -> dict:
        matches = [row for row in signals if row.get("signal_id") == signal_id]
        if len(matches) != 1:
            raise ValueError("SIGNAL_REFERENCE_NOT_UNIQUE")
        return matches[0]

    @staticmethod
    def _outcome_events(events: list[dict], outcome_id: str) -> list[dict]:
        return sorted((row for row in events if row.get("outcome_id") == outcome_id),
                      key=lambda row: row.get("event_sequence", -1))

    def _session_path(self, signal: dict, entry: str, horizon: int) -> list[dict]:
        rows = [row for row in self._calendar(signal)
                if row.get("market") == signal["market"] and row.get("session_status") == "OPEN"]
        rows.sort(key=lambda row: parse_timestamp(row["close_at"], "SESSION_CLOSE_AT"))
        dates = [row["session_date"] for row in rows]
        if entry not in dates:
            raise ValueError("ENTRY_SESSION_NOT_IN_CALENDAR")
        start = dates.index(entry)
        path = rows[start:start + horizon + 1]
        if len(path) != horizon + 1:
            raise ValueError("INSUFFICIENT_FORWARD_SESSIONS")
        return path

    def _validate_pending(self, event: dict, signal: dict) -> None:
        if event["event_sequence"] != 0 or event.get("prior_event_id") is not None:
            raise ValueError("PENDING_SEQUENCE_INVALID")
        if event.get("outcome_matured_at") is not None:
            raise ValueError("PENDING_HAS_MATURITY_TIME")
        if any(event.get(field) is not None for field in METRIC_FIELDS | {"end_session"}):
            raise ValueError("PENDING_HAS_OUTCOME")
        if event.get("entry_session") != self.first_entry_session(signal):
            raise ValueError("ENTRY_SESSION_NOT_DERIVED_FROM_PLAN")
        if parse_timestamp(event["observed_at"], "OBSERVED_AT") < parse_timestamp(
                signal["signal_published_at"], "SIGNAL_PUBLISHED_AT"):
            raise ValueError("PENDING_OBSERVED_BEFORE_SIGNAL_PUBLICATION")

    def _validate_mature(self, event: dict, signal: dict, prior: dict) -> None:
        required = METRIC_FIELDS | {
            "entry_session", "end_session", "outcome_matured_at", "price_input_sha256",
            "corporate_action_input_sha256", "calendar_version_id", "calendar_sha256",
            "benchmark_id", "subject_id",
        }
        _require(event, required)
        if prior["event_type"] != "OUTCOME_PENDING":
            raise ValueError("MATURE_REQUIRES_PENDING")
        if event["event_sequence"] != prior["event_sequence"] + 1 or event.get("prior_event_id") != prior["event_id"]:
            raise ValueError("MATURE_TRANSITION_INVALID")
        if event["entry_session"] != prior["entry_session"]:
            raise ValueError("ENTRY_SESSION_CHANGED")
        if event["subject_id"] != signal["subject_id"]:
            raise ValueError("SUBJECT_ID_MISMATCH")
        if event["benchmark_id"] != signal["benchmark_id"]:
            raise ValueError("BENCHMARK_ID_MISMATCH")
        if (event["calendar_version_id"], event["calendar_sha256"]) != (
                signal["calendar_version_id"], signal["calendar_sha256"]):
            raise ValueError("CALENDAR_IDENTITY_CHANGED")
        path = self._session_path(signal, event["entry_session"], event["horizon_sessions"])
        if event["end_session"] != path[-1]["session_date"]:
            raise ValueError("END_SESSION_NOT_COMPLETE_HORIZON")
        matured = parse_timestamp(event["outcome_matured_at"], "OUTCOME_MATURED_AT")
        observed = parse_timestamp(event["observed_at"], "OBSERVED_AT")
        end_close = parse_timestamp(path[-1]["close_at"], "END_SESSION_CLOSE_AT")
        if matured != end_close:
            raise ValueError("MATURITY_TIME_MUST_EQUAL_END_SESSION_CLOSE")
        if observed < matured:
            raise ValueError("OUTCOME_OBSERVED_BEFORE_MATURITY")
        price = self._bound("price_bundles", event["price_input_sha256"],
                            event["price_input_sha256"], "PRICE_INPUT")
        actions = self._bound("action_bundles", event["corporate_action_input_sha256"],
                              event["corporate_action_input_sha256"], "CORPORATE_ACTION_INPUT")
        expected_dates = [row["session_date"] for row in path]
        for bundle, label in ((price, "PRICE"), (actions, "CORPORATE_ACTION")):
            if bundle.get("subject_id") != signal["subject_id"] or bundle.get("benchmark_id") != signal["benchmark_id"]:
                raise ValueError(label + "_IDENTITY_MISMATCH")
            if not bundle.get("source_id"):
                raise ValueError(label + "_SOURCE_ID_REQUIRED")
            if (bundle.get("coverage_start"), bundle.get("coverage_end")) != (
                    expected_dates[0], expected_dates[-1]):
                raise ValueError(label + "_COVERAGE_MISMATCH")
            available = parse_timestamp(bundle.get("available_at"), label + "_AVAILABLE_AT")
            fetched = parse_timestamp(bundle.get("fetched_at"), label + "_FETCHED_AT")
            if available < matured or available > fetched or fetched > observed:
                raise ValueError(label + "_POINT_IN_TIME_ORDER_INVALID")
        prices = price.get("total_return_index") or {}
        if sorted(prices) != sorted(expected_dates):
            raise ValueError("PRICE_PATH_IDENTITY_INCOMPLETE")
        stock = [prices[day]["stock"] for day in expected_dates]
        bench = [prices[day]["benchmark"] for day in expected_dates]
        if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in stock + bench):
            raise ValueError("PRICE_VALUE_INVALID")
        stock_path = [x / stock[0] - 1 for x in stock[1:]]
        bench_path = [x / bench[0] - 1 for x in bench[1:]]
        relative = [s - b for s, b in zip(stock_path, bench_path)]
        returns = [stock[i] / stock[i - 1] - 1 for i in range(1, len(stock))]
        mean = sum(returns) / len(returns)
        expected = {
            "stock_total_return": stock_path[-1],
            "benchmark_total_return": bench_path[-1],
            "excess_return": relative[-1],
            "max_adverse_excursion": min(relative),
            "max_favorable_excursion": max(relative),
            "realized_volatility": math.sqrt(sum((x - mean) ** 2 for x in returns) / len(returns)),
        }
        for field, value in expected.items():
            supplied = event.get(field)
            if not isinstance(supplied, (int, float)) or not math.isfinite(supplied) or abs(supplied - value) > 1e-12:
                raise ValueError(field.upper() + "_MISMATCH")

    @staticmethod
    def _validate_void(event: dict, prior: dict) -> None:
        if prior["event_type"] != "OUTCOME_PENDING":
            raise ValueError("VOID_REQUIRES_PENDING")
        if event["event_sequence"] != prior["event_sequence"] + 1 or event.get("prior_event_id") != prior["event_id"]:
            raise ValueError("VOID_TRANSITION_INVALID")
        if not isinstance(event.get("void_reason"), str) or not event["void_reason"]:
            raise ValueError("VOID_REASON_REQUIRED")
        if parse_timestamp(event["observed_at"], "OBSERVED_AT") < parse_timestamp(
                prior["observed_at"], "PRIOR_OBSERVED_AT"):
            raise ValueError("VOID_OBSERVED_BEFORE_PRIOR_EVENT")
        if any(event.get(field) is not None for field in METRIC_FIELDS):
            raise ValueError("VOID_HAS_METRICS")

    def validate_event(self, event: dict, existing: list[dict], signals: list[dict]) -> bool:
        required = {
            "schema_version", "event_id", "event_type", "outcome_id", "signal_id",
            "horizon_sessions", "evaluation_plan_sha256", "event_sequence",
            "prior_event_id", "observed_at", "include_in_denominator",
        }
        _require(event, required)
        if FORBIDDEN_FIELDS & set(event):
            raise ValueError("FORMAL_RULE_MUTATION_FORBIDDEN")
        if event["event_type"] not in EVENT_TYPES:
            raise ValueError("EVENT_TYPE_INVALID")
        self._gate(event["observed_at"])
        signal = self._signal(signals, event["signal_id"])
        self.validate_signal(signal)
        if event["evaluation_plan_sha256"] != signal["evaluation_plan_sha256"]:
            raise ValueError("EVENT_PLAN_DOES_NOT_MATCH_SIGNAL")
        if event["horizon_sessions"] not in signal["horizons_sessions"]:
            raise ValueError("EVENT_HORIZON_NOT_PREREGISTERED")
        expected_outcome = expected_outcome_id(
            signal["signal_id"], event["horizon_sessions"], signal["evaluation_plan_sha256"])
        if event["outcome_id"] != expected_outcome:
            raise ValueError("OUTCOME_ID_INVALID")
        if event["event_id"] != expected_event_id(event):
            raise ValueError("EVENT_ID_INVALID")
        prior_events = self._outcome_events(existing, event["outcome_id"])
        if event["event_type"] == "OUTCOME_PENDING":
            if prior_events:
                raise ValueError("PENDING_ALREADY_EXISTS")
            self._validate_pending(event, signal)
        else:
            if not prior_events:
                raise ValueError("OUTCOME_TRANSITION_WITHOUT_PENDING")
            prior = prior_events[-1]
            if prior["event_type"] in {"OUTCOME_MATURED", "OUTCOME_VOIDED"}:
                raise ValueError("OUTCOME_ALREADY_TERMINAL")
            if event["event_type"] == "OUTCOME_MATURED":
                self._validate_mature(event, signal, prior)
            else:
                self._validate_void(event, prior)
        if event.get("include_in_denominator") is not True:
            raise ValueError("DENOMINATOR_PRESERVATION_REQUIRED")
        return True

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL_INVALID:{path.name}:{number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL_RECORD_NOT_OBJECT:{path.name}:{number}")
            rows.append(row)
        return rows

    @staticmethod
    def _bytes(rows: list[dict]) -> bytes:
        return b"".join(canonical_bytes(row) for row in rows)

    def _validate_candidate(self, path: Path, rows: list[dict]) -> None:
        if path == self.signal_path:
            accepted = []
            for row in rows:
                self.validate_signal(row)
                prior = next((item for item in accepted if item["signal_id"] == row["signal_id"]), None)
                if prior is None:
                    accepted.append(row)
                elif prior != row:
                    raise ValueError("SIGNAL_ID_CONFLICT")
        elif path == self.event_path:
            signals = self._read(self.signal_path)
            accepted = []
            for row in rows:
                self.validate_event(row, accepted, signals)
                accepted.append(row)
        else:
            raise ValueError("UNKNOWN_STORAGE_TARGET")

    def _atomic_commit(self, path: Path, rows: list[dict], fault_stage: str | None = None) -> None:
        self._path_gate()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path_gate()
        candidate = path.with_suffix(path.suffix + ".candidate")
        with candidate.open("xb") as handle:
            handle.write(self._bytes(rows))
            handle.flush()
            os.fsync(handle.fileno())
        if fault_stage == "AFTER_CANDIDATE_WRITE":
            raise RuntimeError("SYNTHETIC_INTERRUPTION_AFTER_CANDIDATE_WRITE")
        os.replace(candidate, path)

    def receive_signal(self, signal: dict, fault_stage: str | None = None) -> str:
        self.validate_signal(signal)
        existing = self._read(self.signal_path)
        prior = next((row for row in existing if row.get("signal_id") == signal["signal_id"]), None)
        if prior is not None:
            if prior == signal:
                return "ALREADY_APPLIED"
            raise ValueError("SIGNAL_ID_CONFLICT")
        self._atomic_commit(self.signal_path, existing + [copy.deepcopy(signal)], fault_stage)
        return "APPENDED"

    def receive_event(self, event: dict, fault_stage: str | None = None) -> str:
        signals = self._read(self.signal_path)
        existing = self._read(self.event_path)
        same_id = next((row for row in existing if row.get("event_id") == event.get("event_id")), None)
        if same_id is not None:
            if same_id == event:
                return "ALREADY_APPLIED"
            raise ValueError("EVENT_ID_CONFLICT")
        self.validate_event(event, existing, signals)
        self._atomic_commit(self.event_path, existing + [copy.deepcopy(event)], fault_stage)
        return "APPENDED"

    def recover(self) -> dict:
        self._gate()
        recovered = []
        for path in (self.signal_path, self.event_path):
            candidate = path.with_suffix(path.suffix + ".candidate")
            if not candidate.exists():
                continue
            candidate_rows = self._read(candidate)
            current_rows = self._read(path)
            if candidate_rows[:len(current_rows)] != current_rows:
                raise ValueError("CANDIDATE_PREFIX_MISMATCH:" + path.name)
            self._validate_candidate(path, candidate_rows)
            os.replace(candidate, path)
            recovered.append(path.name)
        return {"recovered": recovered}

    def state(self) -> dict:
        self._gate()
        signals = self._read(self.signal_path)
        events = self._read(self.event_path)
        active = {}
        for event in sorted(events, key=lambda row: (row["outcome_id"], row["event_sequence"])):
            active[event["outcome_id"]] = event
        counts = {"PENDING": 0, "MATURE": 0, "VOID": 0}
        names = {"OUTCOME_PENDING": "PENDING", "OUTCOME_MATURED": "MATURE", "OUTCOME_VOIDED": "VOID"}
        for event in active.values():
            counts[names[event["event_type"]]] += 1
        return {"signal_count": len(signals), "event_count": len(events), **counts}


__all__ = [
    "AUTHORITY_SCOPE", "EVENT_FILE", "EXECUTION_MODE", "ForwardTrackerCandidate",
    "PLAN_APPROVAL_SCOPE", "REPOSITORY_ROOT", "SIGNAL_FILE", "build_activation_authority",
    "build_plan_approval_record", "canonical_bytes",
    "default_config", "expected_event_id", "expected_outcome_id", "expected_signal_id",
    "object_sha256", "storage_root_sha256",
]
