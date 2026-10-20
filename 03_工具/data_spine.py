"""1.2.1 最小資料脊椎：證券身分、事件時點、日曆、觀察值、持股、匯率與 V1。

本模組只做可重現的結構驗證與衍生計算。它不抓新聞、不做情緒計分，也不產生買賣建議。
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import statistics
from zoneinfo import ZoneInfo

from common import ROOT, file_lock, load_json

SPINE_DIR = os.path.join(ROOT, "01_共用資料", "data_spine")
CONFIG_PATH = os.path.join(ROOT, "00_規範", "data_spine_phase1.json")
REGISTRY_PATH = os.path.join(SPINE_DIR, "entity_registry.json")
EVENTS_PATH = os.path.join(SPINE_DIR, "events.jsonl")
EVENT_WRITE_LEDGER_PATH = os.path.join(SPINE_DIR, "event_write_ledger.jsonl")
OBSERVATIONS_PATH = os.path.join(SPINE_DIR, "observations.jsonl")
SUPERSEDE_SEMANTICS_PATH = os.path.join(SPINE_DIR, "observation_supersede_semantics.jsonl")
WRITE_LEDGER_NAME = "observation_write_ledger.jsonl"
WRITE_LEDGER_PATH = os.path.join(SPINE_DIR, WRITE_LEDGER_NAME)
WRITE_BASES = {"RECORDED_AT_WRITE", "BACKFILLED_FROM_EVIDENCE", "ASSUMED_EQUAL_TO_INGESTED_AT"}
SESSIONS_PATH = os.path.join(SPINE_DIR, "market_sessions.csv")
CALENDAR_VERSIONS_PATH = os.path.join(SPINE_DIR, "calendar_versions.jsonl")
FX_PATH = os.path.join(SPINE_DIR, "fx_rates.csv")
PORTFOLIO_PATH = os.path.join(ROOT, "01_共用資料", "portfolio.json")

TIME_PRECISIONS = {"EXACT_TIMESTAMP", "DATE_ONLY", "UNKNOWN"}
AVAILABILITY_STATUSES = {
    "POINT_IN_TIME_KNOWN", "PUBLISHED_TIME_ONLY", "BACKFILLED_UNKNOWN", "LEGACY_UNKNOWN"
}
PORTFOLIO_STATES = {"NOT_PROVIDED", "PROVIDED_EMPTY", "PROVIDED_NONEMPTY"}
MOPS_DEBT_METRICS = {"FIN_DEBT_CURRENT", "FIN_DEBT_NONCURRENT"}
ABSENT_COMPONENTS_LIMITATION = "ABSENT_COMPONENTS_TREATED_AS_NONE"
OVERLAP_KEYS = ("covered_by_aggregate", "excluded_overlapping_components", "overlap_rules_version")
REPRESENTED_KEY = "aggregate_not_reported_details_used"   # 1.8.3（C182-01）：只在非空時出現
EXCLUDED_BECAUSE = {"DETAILS_PRESENT", "AGGREGATE_PRESENT"}
PORTFOLIO_AUTHORITY = "USER_OWNED_READ_ONLY_TO_AGENTS"
SUPERSEDE_TYPES = {"AVAILABILITY_CORRECTION", "VALUE_REVISION"}
SUPERSEDE_EDGE_RETRACTION = "SUPERSEDE_EDGE_RETRACTION"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_time(value, field="timestamp"):
    """解析含時區 ISO-8601；缺時區一律拒絕。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 缺漏")
    try:
        x = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"{field} 不是合法 ISO-8601：{value}") from e
    if x.tzinfo is None:
        raise ValueError(f"{field} 必須包含時區：{value}")
    return x


def _event_time_for_read(value, field="event.timestamp"):
    """Read legacy event timestamps with an explicit microsecond truncation rule.

    Four pre-T41 event rows contain seven fractional digits.  Python releases
    disagree on accepting them, so legacy reads truncate only the fractional
    component to six digits.  New writes are rejected by validate_event_record.
    Other malformed timestamps remain errors and are never silently skipped.
    """
    if not isinstance(value, str):
        return parse_time(value, field)
    match = re.fullmatch(r"(.*T\d{2}:\d{2}:\d{2})\.(\d+)(Z|[+-]\d{2}:\d{2})", value.strip())
    normalized = value.strip()
    if match and len(match.group(2)) > 6:
        normalized = match.group(1) + "." + match.group(2)[:6] + match.group(3)
    return parse_time(normalized, field)


def _event_fraction_exceeds_microseconds(value):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r".*T\d{2}:\d{2}:\d{2}\.(\d+)(?:Z|[+-]\d{2}:\d{2})", value.strip())
    return bool(match and len(match.group(1)) > 6)


def _date(value, field="date"):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} 必須是 YYYY-MM-DD") from e


def load_registry(path=REGISTRY_PATH):
    data = load_json(path)
    errors = validate_registry(data)
    if errors:
        raise ValueError("entity_registry.json 無效：" + "；".join(errors))
    return data


def validate_registry(data):
    errors = []
    issuers = {x.get("issuer_id") for x in data.get("issuers", []) if isinstance(x, dict)}
    security_ids, tickers, aliases = set(), set(), {}
    required = ("security_id", "issuer_id", "ticker", "exchange", "market", "market_timezone",
                "security_type", "currency", "price_series_enabled", "valid_from", "valid_to",
                "adr_underlying_security_id", "adr_ratio", "adr_ratio_valid_from", "adr_ratio_valid_to")
    for i, sec in enumerate(data.get("securities", [])):
        p = f"securities[{i}]"
        if not isinstance(sec, dict):
            errors.append(f"{p} 必須是物件"); continue
        for k in required:
            if k not in sec: errors.append(f"{p} 缺少 {k}")
        sid, ticker = sec.get("security_id"), sec.get("ticker")
        if sid in security_ids: errors.append(f"security_id 重複：{sid}")
        if ticker in tickers: errors.append(f"ticker 重複：{ticker}")
        security_ids.add(sid); tickers.add(ticker)
        if sec.get("issuer_id") not in issuers: errors.append(f"{p} issuer_id 不存在")
        try: ZoneInfo(sec.get("market_timezone", ""))
        except Exception: errors.append(f"{p} market_timezone 無效")
        if sec.get("security_type") == "ADR":
            if not sec.get("adr_underlying_security_id") or not isinstance(sec.get("adr_ratio"), (int, float)):
                errors.append(f"{p} ADR 必須有 underlying 與 ratio")
            if not sec.get("adr_ratio_valid_from"): errors.append(f"{p} ADR ratio 缺有效起日")
        for alias in sec.get("aliases", []):
            if alias in aliases and aliases[alias] != sid: errors.append(f"alias 跨證券重複：{alias}")
            aliases[alias] = sid
    for sec in data.get("securities", []):
        u = sec.get("adr_underlying_security_id")
        if u and u not in security_ids: errors.append(f"ADR underlying 不存在：{u}")
    return errors


def resolve_security(identifier, registry=None):
    """回傳 (security, status)。已知但未啟用不會偷換成另一檔證券。"""
    registry = registry or load_registry()
    raw = identifier.strip() if isinstance(identifier, str) else identifier
    for sec in registry["securities"]:
        if raw in (sec["security_id"], sec["ticker"]) or raw in sec.get("aliases", []):
            return sec, "OK" if sec["price_series_enabled"] else "KNOWN_SECURITY_NOT_ENABLED"
    return None, "UNKNOWN_SECURITY"


def read_jsonl(path):
    if not os.path.exists(path): return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try: rows.append(json.loads(line))
                except json.JSONDecodeError as e: raise ValueError(f"{path} 第 {n} 行不是合法 JSON") from e
    return rows


def validate_supersede_semantic(row, observation_ids=None):
    """Validate one append-only declaration describing a supersede edge.

    Absence of a declaration is interpreted as VALUE_REVISION for compatibility
    with observations written before G-S3-11.
    """
    # Schema consumers written before G-S3-13 call this public validator for
    # every JSONL record.  Dispatch retractions here while load/append perform
    # the stronger ordered target-relation checks below.
    if row.get("record_type") == SUPERSEDE_EDGE_RETRACTION:
        return validate_supersede_retraction(row, None, observation_ids)
    errors = []
    required = ("relation_id", "superseded_observation_id", "superseding_observation_id",
                "supersede_type", "reason", "recorded_at")
    for key in required:
        if row.get(key) in (None, ""):
            errors.append(f"supersede semantic 缺少 {key}")
    if row.get("supersede_type") not in SUPERSEDE_TYPES:
        errors.append("supersede_type 無效")
    try:
        parse_time(row.get("recorded_at"), "recorded_at")
    except ValueError as exc:
        errors.append(str(exc))
    if observation_ids is not None:
        if row.get("superseded_observation_id") not in observation_ids:
            errors.append("superseded_observation_id 不存在")
        if row.get("superseding_observation_id") not in observation_ids:
            errors.append("superseding_observation_id 不存在")
    return errors


def validate_supersede_retraction(row, relations=None, observation_ids=None):
    """Validate one append-only G-S3-13 edge retraction."""
    errors = []
    required = ("retraction_id", "target_relation_id", "superseded_observation_id",
                "superseding_observation_id", "original_supersede_type", "reason",
                "run_id", "lock_holder", "recorded_at", "available_at")
    for key in required:
        if row.get(key) in (None, ""):
            errors.append(f"supersede edge retraction 缺少 {key}")
    if row.get("record_type") != SUPERSEDE_EDGE_RETRACTION:
        errors.append("record_type 無效")
    if row.get("original_supersede_type") not in SUPERSEDE_TYPES:
        errors.append("original_supersede_type 無效")
    for key in ("recorded_at", "available_at"):
        try:
            parse_time(row.get(key), key)
        except ValueError as exc:
            errors.append(str(exc))
    if row.get("restores_all_as_of") not in {True, False}:
        errors.append("restores_all_as_of 必須是 boolean")
    if row.get("restores_all_as_of"):
        if row.get("original_supersede_type") != "AVAILABILITY_CORRECTION":
            errors.append("restores_all_as_of 只允許 AVAILABILITY_CORRECTION")
        if row.get("retraction_basis") != "ERRONEOUS_REGISTRATION":
            errors.append("restores_all_as_of 只允許 ERRONEOUS_REGISTRATION")
    if observation_ids is not None:
        if row.get("superseded_observation_id") not in observation_ids:
            errors.append("superseded_observation_id 不存在")
        if row.get("superseding_observation_id") not in observation_ids:
            errors.append("superseding_observation_id 不存在")
    if relations is not None:
        relation = relations.get(row.get("target_relation_id"))
        if relation is None:
            errors.append("target_relation_id 不存在")
        else:
            expected = (relation.get("superseding_observation_id"),
                        relation.get("superseded_observation_id"),
                        relation.get("supersede_type"))
            declared = (row.get("superseding_observation_id"),
                        row.get("superseded_observation_id"),
                        row.get("original_supersede_type"))
            if declared != expected:
                errors.append("撤回列與原關係不一致")
    return errors


def load_supersede_semantics(path=SUPERSEDE_SEMANTICS_PATH, observations=None):
    rows = read_jsonl(path)
    ids = {r.get("observation_id") for r in observations} if observations is not None else None
    seen_ids, seen_edges, relations, retracted_relations = set(), set(), {}, set()
    for row in rows:
        if row.get("record_type") == SUPERSEDE_EDGE_RETRACTION:
            errors = validate_supersede_retraction(row, relations, ids)
            rid = row.get("retraction_id")
            target = row.get("target_relation_id")
            if rid in seen_ids:
                errors.append("retraction_id 重複")
            if target in retracted_relations:
                errors.append("同一 supersede edge 不可重複撤回")
        else:
            errors = validate_supersede_semantic(row, ids)
            edge = (row.get("superseding_observation_id"), row.get("superseded_observation_id"))
            rid = row.get("relation_id")
            if rid in seen_ids:
                errors.append("relation_id 重複")
            if edge in seen_edges:
                errors.append("supersede edge 重複宣告")
        if errors:
            raise ValueError("INVALID_SUPERSEDE_SEMANTICS:" + "|".join(errors))
        seen_ids.add(rid)
        if row.get("record_type") == SUPERSEDE_EDGE_RETRACTION:
            retracted_relations.add(target)
        else:
            seen_edges.add(edge); relations[rid] = row
    return rows


def _effective_supersede_edges(semantics, as_of=None):
    relations = {r.get("relation_id"): r for r in semantics
                 if r.get("record_type") != SUPERSEDE_EDGE_RETRACTION}
    retractions = {r.get("target_relation_id"): r for r in semantics
                   if r.get("record_type") == SUPERSEDE_EDGE_RETRACTION}
    cutoff = parse_time(as_of, "supersede_as_of") if isinstance(as_of, str) else as_of
    out = {}
    for relation_id, relation in relations.items():
        retraction = retractions.get(relation_id)
        state = relation.get("supersede_type")
        if retraction:
            if retraction.get("restores_all_as_of"):
                state = "RETRACTED"
            elif cutoff is None or parse_time(retraction.get("available_at"), "available_at") <= cutoff:
                state = "RETRACTED"
        edge = (relation.get("superseding_observation_id"), relation.get("superseded_observation_id"))
        out[edge] = (state, relation_id, retraction)
    return out


def apply_supersede_semantics(observations, semantics=None, as_of=None):
    """Return copies annotated with in-memory `_supersede_type` metadata."""
    semantics = [] if semantics is None else semantics
    by_edge = _effective_supersede_edges(semantics, as_of) if semantics else {}
    out = []
    for original in observations:
        row = dict(original)
        parent = row.get("supersedes_observation_id")
        if parent:
            declared = by_edge.get((row.get("observation_id"), parent))
            if declared:
                row["_supersede_type"] = declared[0]
                row["_supersede_relation_id"] = declared[1]
                if declared[2]:
                    row["_supersede_retraction_id"] = declared[2].get("retraction_id")
                    row["_supersede_retracted_at"] = declared[2].get("available_at")
                    row["_supersede_restores_all_as_of"] = declared[2].get("restores_all_as_of", False)
            else:
                original_type = row.get("_supersede_original_type") or row.get("_supersede_type") or row.get("supersede_type")
                retracted_at = row.get("_supersede_retracted_at")
                cutoff = parse_time(as_of, "supersede_as_of") if isinstance(as_of, str) else as_of
                if row.get("_supersede_restores_all_as_of"):
                    row["_supersede_type"] = "RETRACTED"
                elif retracted_at and (cutoff is None or parse_time(retracted_at, "available_at") <= cutoff):
                    row["_supersede_type"] = "RETRACTED"
                else:
                    row["_supersede_type"] = original_type or "VALUE_REVISION"
            row["_supersede_original_type"] = (row.get("_supersede_original_type") or
                                                (declared[0] if declared and declared[0] != "RETRACTED" else
                                                 (declared[2].get("original_supersede_type") if declared and declared[2] else None)) or
                                                row.get("supersede_type") or "VALUE_REVISION")
        out.append(row)
    return out


def resolve_visible_observation_versions(observations, visible_rows=None, semantics=None, as_of=None):
    """Resolve supersede chains with G-S3-11 point-in-time semantics.

    AVAILABILITY_CORRECTION invalidates its parent at every as-of time. A
    VALUE_REVISION invalidates its parent only when the child is itself in the
    caller's visible set. Callers define visibility before calling this helper.
    """
    annotated = apply_supersede_semantics(observations, semantics, as_of=as_of)
    visible_ids = ({r.get("observation_id") for r in annotated} if visible_rows is None else
                   {r.get("observation_id") for r in visible_rows})
    globally_invalid = {r.get("supersedes_observation_id") for r in annotated
                        if r.get("supersedes_observation_id") and
                        r.get("_supersede_type") == "AVAILABILITY_CORRECTION"}
    value_revision_parents = {r.get("supersedes_observation_id") for r in annotated
                              if r.get("observation_id") in visible_ids and
                              r.get("supersedes_observation_id") and
                              r.get("_supersede_type") == "VALUE_REVISION"}
    invalid = globally_invalid | value_revision_parents
    return [r for r in annotated if r.get("observation_id") in visible_ids and r.get("observation_id") not in invalid]


def append_supersede_semantics(rows, path=SUPERSEDE_SEMANTICS_PATH, observations=None, lock_holder=None):
    """Append a validated batch of new edge declarations without rewriting history."""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    existing = read_jsonl(path)
    old_ids = {r.get("relation_id") or r.get("retraction_id") for r in existing}
    old_edges = {(r.get("superseding_observation_id"), r.get("superseded_observation_id")) for r in existing
                 if r.get("record_type") != SUPERSEDE_EDGE_RETRACTION}
    relations = {r.get("relation_id"): r for r in existing
                 if r.get("record_type") != SUPERSEDE_EDGE_RETRACTION}
    retracted_relations = {r.get("target_relation_id") for r in existing
                           if r.get("record_type") == SUPERSEDE_EDGE_RETRACTION}
    observation_ids = {r.get("observation_id") for r in (observations if observations is not None else read_observations_checked())}
    new_ids, new_edges = set(), set()
    for row in rows:
        if row.get("record_type") == SUPERSEDE_EDGE_RETRACTION:
            errors = validate_supersede_retraction(row, relations, observation_ids)
            rid = row.get("retraction_id")
            target = row.get("target_relation_id")
            if rid in old_ids | new_ids:
                errors.append("retraction_id 缺漏或重複")
            if target in retracted_relations:
                errors.append("同一 supersede edge 不可重複撤回")
            if row.get("lock_holder") != lock_holder:
                errors.append("lock_holder 與實際持鎖者不一致")
        else:
            errors = validate_supersede_semantic(row, observation_ids)
            edge = (row.get("superseding_observation_id"), row.get("superseded_observation_id"))
            rid = row.get("relation_id")
            if rid in old_ids | new_ids:
                errors.append("relation_id 缺漏或重複")
            if edge in old_edges | new_edges:
                errors.append("supersede edge 已宣告")
        if errors:
            raise ValueError("INVALID_SUPERSEDE_SEMANTICS:" + "|".join(errors))
        new_ids.add(rid)
        if row.get("record_type") == SUPERSEDE_EDGE_RETRACTION:
            retracted_relations.add(target)
        else:
            new_edges.add(edge); relations[rid] = row
    if not rows:
        return 0
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    from staging_write import append_bytes, validate_jsonl_bytes
    append_bytes(path, payload, writer="data_spine.append_supersede_semantics",
                 validator=validate_jsonl_bytes, lock_holder=lock_holder,
                 extra={"relation_count": len(rows)})
    return len(rows)


def append_jsonl(path, row, id_field, supersedes_field, lock_holder=None):
    """只新增 JSONL；更正必須以新 ID 指向舊 ID。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    old = read_jsonl(path)
    ids = {x.get(id_field) for x in old}
    rid = row.get(id_field)
    if not rid or rid in ids: raise ValueError(f"{id_field} 缺漏或重複")
    parent = row.get(supersedes_field)
    if parent and parent not in ids: raise ValueError(f"{supersedes_field} 指向不存在的 ID")
    from staging_write import append_bytes, validate_jsonl_bytes
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    def validate(candidate):
        detail = validate_jsonl_bytes(candidate)
        parsed = [json.loads(line) for line in candidate.decode("utf-8").splitlines() if line.strip()]
        values = [item.get(id_field) for item in parsed]
        if len(values) != len(set(values)):
            raise ValueError(f"{id_field} 缺漏或重複")
        return detail
    append_bytes(path, payload, writer="data_spine.append_jsonl", validator=validate,
                 lock_holder=lock_holder, extra={"id_field": id_field, "id": rid})


def validate_event_record(row):
    """Validate canonical event/coverage rows before append-only registration."""
    errors = []
    required = ("event_id", "record_type", "issuer_id", "available_at", "ingested_at",
                "source_url", "source_quote", "source_checked_at", "source_excerpt_sha256")
    for key in required:
        if row.get(key) in (None, ""):
            errors.append(f"事件缺少 {key}")
    if row.get("record_type") not in {"EVENT", "COVERAGE"}:
        errors.append("record_type 無效")
    for key in ("available_at", "ingested_at", "source_checked_at"):
        if _event_fraction_exceeds_microseconds(row.get(key)):
            errors.append(f"{key} 小數秒不得超過 6 位")
        try:
            parse_time(row.get(key), key)
        except ValueError as exc:
            errors.append(str(exc))
    if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_excerpt_sha256", ""))):
        errors.append("source_excerpt_sha256 必須是完整 SHA-256")
    if row.get("record_type") == "EVENT":
        if row.get("event_type") != "EARNINGS_RESULTS":
            errors.append("event_type 目前只允許 EARNINGS_RESULTS")
        if row.get("event_status") not in {"SCHEDULED", "RESCHEDULED", "CANCELLED"}:
            errors.append("event_status 無效")
        if row.get("time_precision") not in {"DATE_ONLY", "EXACT_TIMESTAMP"}:
            errors.append("事件 time_precision 無效")
        try:
            _date(row.get("event_local_date"), "event_local_date")
            ZoneInfo(row.get("event_timezone"))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"事件日期或時區無效：{exc}")
        if row.get("time_precision") == "EXACT_TIMESTAMP" and not row.get("event_local_time"):
            errors.append("EXACT_TIMESTAMP 缺少 event_local_time")
    else:
        for key in ("coverage_start_session_id", "coverage_end_session_id", "checked_at"):
            if not row.get(key):
                errors.append(f"COVERAGE 缺少 {key}")
        if _event_fraction_exceeds_microseconds(row.get("checked_at")):
            errors.append("checked_at 小數秒不得超過 6 位")
        try:
            parse_time(row.get("checked_at"), "checked_at")
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def append_event_batch(rows, reason, events_path=EVENTS_PATH, ledger_path=EVENT_WRITE_LEDGER_PATH,
                       written_at=None, lock_holder=None):
    """Append validated events and a hash-bound ledger row; roll back this append if ledger write fails."""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, events_path)
    if not rows:
        return None
    old = read_jsonl(events_path)
    old_ids = {row.get("event_id") for row in old}
    new_ids = [row.get("event_id") for row in rows]
    if len(new_ids) != len(set(new_ids)) or old_ids & set(new_ids):
        raise ValueError("EVENT_ID_DUPLICATE")
    for row in rows:
        errors = validate_event_record(row)
        if errors:
            raise ValueError("INVALID_EVENT:" + "|".join(errors))
        parent = row.get("supersedes_event_id")
        if parent and parent not in old_ids and parent not in set(new_ids):
            raise ValueError("EVENT_SUPERSEDES_MISSING_PARENT")
    os.makedirs(os.path.dirname(events_path), exist_ok=True)
    before = open(events_path, "rb").read() if os.path.exists(events_path) else b""
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)
    stamp = written_at or dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        from staging_write import append_bytes, validate_jsonl_bytes
        staging_report = append_bytes(
            events_path, payload, writer="data_spine.append_event_batch.events",
            validator=validate_jsonl_bytes, lock_holder=lock_holder,
            extra={"event_ids": new_ids, "reason": reason}, record_audit=False)
        length, digest = _file_state(events_path)
        identity = json.dumps({"event_ids": new_ids, "sha": digest, "written_at": stamp},
                              sort_keys=True, separators=(",", ":"))
        entry = {"batch_id": "EVENTWRITE_" + hashlib.sha256(identity.encode()).hexdigest(),
                 "written_at": stamp, "reason": reason, "event_ids": new_ids,
                 "event_count": len(new_ids), "file_byte_length_after": length,
                 "file_sha256_after": digest,
                 "staging_validation_sha256": staging_report["validation_report_sha256"]}
        append_jsonl(ledger_path, entry, "batch_id", "supersedes_batch_id", lock_holder=lock_holder)
        return entry
    except Exception:
        with open(events_path, "r+b") as handle:
            handle.truncate(len(before)); handle.flush(); os.fsync(handle.fileno())
        raise


def validate_observation(row, historical_replay=False):
    errors = []
    required = ("observation_id", "metric_id", "unit", "period_end", "source_url",
                "source_published_at", "available_at", "ingested_at", "availability_status")
    for k in required:
        if k not in row: errors.append(f"缺少 {k}")
    if not row.get("issuer_id") and not row.get("security_id"):
        errors.append("issuer_id 與 security_id 至少要有一個")
    st = row.get("availability_status")
    if st not in AVAILABILITY_STATUSES: errors.append("availability_status 無效")
    if not row.get("ingested_at"): errors.append("ingested_at 必填")
    if st == "BACKFILLED_UNKNOWN" and row.get("available_at") is not None:
        errors.append("BACKFILLED_UNKNOWN.available_at 必須是 null")
    if historical_replay and st in {"BACKFILLED_UNKNOWN", "LEGACY_UNKNOWN"}:
        errors.append(f"{st} 不可用於歷史 as-of replay")
    for k in ("source_published_at", "available_at", "ingested_at"):
        if row.get(k):
            if _event_fraction_exceeds_microseconds(row.get(k)):
                errors.append(f"{k} 小數秒不得超過 6 位")
            try: parse_time(row[k], k)
            except ValueError as e: errors.append(str(e))
    value = row.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(float(value)):
        errors.append("value 不得為 NaN 或無限值")
    source = row.get("source_system")
    if "selection_written_at" in row:
        errors.append("selection_written_at 只能在記憶體中使用，不得寫入 canonical")
    if source == "MOPS_XBRL":
        for k in ("availability_basis", "time_precision", "raw_sha256", "manifest_record_id",
                  "report_scope"):
            if not row.get(k): errors.append(f"MOPS_XBRL 缺少 {k}")
        if row.get("availability_basis") not in {
                "OFFICIAL_TIMESTAMP", "OFFICIAL_DATE", "FIRST_SEEN_CONSERVATIVE", "UNKNOWN_BACKFILL",
                "STATUTORY_DEADLINE_CONSERVATIVE",
                "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE",
                "V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN"}:
            errors.append("MOPS_XBRL availability_basis 無效")
        if row.get("time_precision") not in {"EXACT_TIMESTAMP", "DATE_ONLY", "UNKNOWN"}:
            errors.append("MOPS_XBRL time_precision 無效")
        if row.get("report_scope") not in {"CONSOLIDATED", "INDIVIDUAL"}:
            errors.append("MOPS_XBRL report_scope 無效")
        if (not isinstance(row.get("raw_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", row.get("raw_sha256", ""))):
            errors.append("MOPS_XBRL raw_sha256 必須是完整 SHA-256")
        basis = row.get("availability_basis")
        if basis == "FIRST_SEEN_CONSERVATIVE" and not row.get("first_seen_at"):
            errors.append("FIRST_SEEN_CONSERVATIVE 缺少 first_seen_at")
        if basis != "FIRST_SEEN_CONSERVATIVE" and row.get("first_seen_at"):
            errors.append("非 FIRST_SEEN_CONSERVATIVE 不得填 first_seen_at")
        if basis == "UNKNOWN_BACKFILL":
            if st != "BACKFILLED_UNKNOWN" or row.get("available_at") is not None or row.get("source_published_at") is not None:
                errors.append("UNKNOWN_BACKFILL 必須對應 BACKFILLED_UNKNOWN 且公布／可用時間為 null")
            if row.get("time_precision") != "UNKNOWN":
                errors.append("UNKNOWN_BACKFILL.time_precision 必須是 UNKNOWN")
        elif basis in {"OFFICIAL_TIMESTAMP", "OFFICIAL_DATE", "FIRST_SEEN_CONSERVATIVE",
                       "STATUTORY_DEADLINE_CONSERVATIVE",
                       "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE"} and st != "POINT_IN_TIME_KNOWN":
            errors.append(f"{basis} 必須對應 POINT_IN_TIME_KNOWN")
        if basis == "OFFICIAL_TIMESTAMP":
            if (not row.get("source_published_at") or not row.get("available_at") or
                    row.get("source_published_at") != row.get("available_at")):
                errors.append("OFFICIAL_TIMESTAMP 必須有相同的 source_published_at 與 available_at")
            if row.get("time_precision") != "EXACT_TIMESTAMP":
                errors.append("OFFICIAL_TIMESTAMP.time_precision 必須是 EXACT_TIMESTAMP")
        if basis == "OFFICIAL_DATE" and row.get("time_precision") != "DATE_ONLY":
            errors.append("OFFICIAL_DATE.time_precision 必須是 DATE_ONLY")
        if basis == "FIRST_SEEN_CONSERVATIVE":
            if row.get("available_at") != row.get("first_seen_at") or row.get("source_published_at") is not None:
                errors.append("FIRST_SEEN_CONSERVATIVE 必須以 first_seen_at 作 available_at，且不得填 source_published_at")
            if row.get("time_precision") != "EXACT_TIMESTAMP":
                errors.append("FIRST_SEEN_CONSERVATIVE.time_precision 必須是 EXACT_TIMESTAMP")
        if basis == "STATUTORY_DEADLINE_CONSERVATIVE":
            if (not row.get("available_at") or row.get("source_published_at") is not None or
                    row.get("time_precision") != "DATE_ONLY"):
                errors.append("STATUTORY_DEADLINE_CONSERVATIVE 必須只有 DATE_ONLY available_at，且不得宣告實際公布時間")
            if row.get("metric_id") not in {"VER_NET_INCOME_PARENT", "VER_EQUITY_PARENT",
                                             "VER_ISSUED_CAPITAL", "VER_TREASURY_SHARES",
                                             "VER_DILUTED_EPS_Q", "VER_DILUTED_EPS_YTD"}:
                errors.append("STATUTORY_DEADLINE_CONSERVATIVE 只允許 V1 版本列")
        if basis == "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE":
            if (not row.get("available_at") or row.get("source_published_at") is not None or
                    row.get("time_precision") != "DATE_ONLY"):
                errors.append("更正日期保守時點必須只有 DATE_ONLY available_at，且不得宣告實際公布時間")
            v1_metrics = {"VER_NET_INCOME_PARENT", "VER_EQUITY_PARENT",
                          "VER_ISSUED_CAPITAL", "VER_TREASURY_SHARES",
                          "VER_DILUTED_EPS_Q", "VER_DILUTED_EPS_YTD"}
            metric = str(row.get("metric_id") or "")
            if metric not in v1_metrics:
                # G-S3-9b：一般 MOPS FIN_* 可使用「法定期限／官方更正日取較晚者」；
                # V1 的既有行為完全不變。一般財務列必須把官方更正證據與可重算時點
                # 一起保存，不能只填一個看似合理的 available_at。
                if not metric.startswith("FIN_"):
                    errors.append("更正日期保守時點只允許 V1 版本列或一般 FIN_* 指標")
                correction_date = str(row.get("correction_date") or "")
                correction_sha = row.get("correction_page_sha256")
                if not re.fullmatch(r"20\d{6}", correction_date):
                    errors.append("一般 FIN_* 更正時點列缺少有效 correction_date")
                if not isinstance(correction_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", correction_sha):
                    errors.append("一般 FIN_* 更正時點列缺少完整 correction_page_sha256")
                if not row.get("manifest_record_id"):
                    errors.append("一般 FIN_* 更正時點列缺少 manifest_record_id")
                fiscal_period = str(row.get("fiscal_period") or "")
                fiscal_year = row.get("fiscal_year")
                if not isinstance(fiscal_year, int) or fiscal_period not in {"Q1", "Q2", "Q3", "Q4"}:
                    errors.append("一般 FIN_* 更正時點列缺少可重算的 fiscal_year/fiscal_period")
                elif re.fullmatch(r"20\d{6}", correction_date):
                    if fiscal_period == "Q4":
                        deadline = dt.date(fiscal_year + 1, 3, 31)
                    else:
                        quarter_end = {"Q1": dt.date(fiscal_year, 3, 31),
                                       "Q2": dt.date(fiscal_year, 6, 30),
                                       "Q3": dt.date(fiscal_year, 9, 30)}[fiscal_period]
                        deadline = quarter_end + dt.timedelta(days=45)
                    correction = dt.datetime.strptime(correction_date, "%Y%m%d").date()
                    expected = max(deadline, correction).isoformat() + "T23:59:59+08:00"
                    if row.get("available_at") != expected:
                        errors.append(f"一般 FIN_* 更正時點列 available_at 必須等於 {expected}")
        if basis == "V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN":
            if (st != "BACKFILLED_UNKNOWN" or row.get("available_at") is not None or
                    row.get("source_published_at") is not None or row.get("time_precision") != "UNKNOWN"):
                errors.append("更正日期未知的 V1 列必須不可回播且沒有公布／可用時間")
            if row.get("metric_id") not in {"VER_NET_INCOME_PARENT", "VER_EQUITY_PARENT",
                                             "VER_ISSUED_CAPITAL", "VER_TREASURY_SHARES",
                                             "VER_DILUTED_EPS_Q", "VER_DILUTED_EPS_YTD"}:
                errors.append("V1_INELIGIBLE_CORRECTION_DATE_UNKNOWN 只允許 V1 版本列")
        derived = str(row.get("derivation", "")).startswith(("DERIVED_", "COMPOSITE_"))
        if derived:
            if not isinstance(row.get("components"), list) or not row.get("components"):
                errors.append("MOPS 推導觀測值缺少 components")
            if not row.get("components_sha256"):
                errors.append("MOPS 推導觀測值缺少 components_sha256")
        elif row.get("components") or row.get("components_sha256"):
            errors.append("MOPS 直接事實不得填 components")
        if row.get("supersedes_observation_id") and not row.get("revision_reason"):
            errors.append("MOPS 更正列缺少 revision_reason")
        if "absent_components" in row or any(k in row for k in OVERLAP_KEYS + (REPRESENTED_KEY,)):
            errors += _validate_absent_components(row)
    elif source == "MOPS_PDF":
        for k in ("raw_sha256", "source_page", "source_pdf_table_title", "availability_basis",
                  "time_precision", "report_scope", "review_status", "reviewer", "reviewed_at", "checked_by"):
            if row.get(k) in (None, "", []): errors.append(f"MOPS_PDF 缺少 {k}")
        if row.get("metric_id") != "FIN_DILUTED_SHARES":
            errors.append("MOPS_PDF 目前只允許 FIN_DILUTED_SHARES")
        if row.get("unit") != "shares":
            errors.append("MOPS_PDF FIN_DILUTED_SHARES 單位必須是 shares")
        if row.get("period_type") not in {"Q", "FY"}:
            errors.append("MOPS_PDF FIN_DILUTED_SHARES period_type 只能是 Q 或 FY")
        value = row.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append("MOPS_PDF FIN_DILUTED_SHARES value 必須是正整數股數")
        if (not isinstance(row.get("raw_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", row.get("raw_sha256", ""))):
            errors.append("MOPS_PDF raw_sha256 必須是完整 SHA-256")
        if not isinstance(row.get("source_page"), int) or isinstance(row.get("source_page"), bool) or row.get("source_page", 0) <= 0:
            errors.append("MOPS_PDF source_page 必須是正整數")
        if row.get("report_scope") not in {"CONSOLIDATED", "INDIVIDUAL"}:
            errors.append("MOPS_PDF report_scope 無效")
        if row.get("review_status") != "APPROVED_BY_INDEPENDENT_AI":
            errors.append("MOPS_PDF 必須經獨立 AI 複核通過")
        if row.get("reviewer") == row.get("checked_by"):
            errors.append("MOPS_PDF 判讀者與複核者不得相同")
        basis = row.get("availability_basis")
        if basis not in {"OFFICIAL_TIMESTAMP", "UNKNOWN_BACKFILL"}:
            errors.append("MOPS_PDF availability_basis 無效")
        if row.get("time_precision") not in {"EXACT_TIMESTAMP", "UNKNOWN"}:
            errors.append("MOPS_PDF time_precision 無效")
        if basis == "OFFICIAL_TIMESTAMP":
            if (st != "POINT_IN_TIME_KNOWN" or not row.get("source_published_at") or
                    row.get("source_published_at") != row.get("available_at")):
                errors.append("MOPS_PDF OFFICIAL_TIMESTAMP 必須對應相同的公布／可用時間與 POINT_IN_TIME_KNOWN")
            if row.get("time_precision") != "EXACT_TIMESTAMP":
                errors.append("MOPS_PDF OFFICIAL_TIMESTAMP.time_precision 必須是 EXACT_TIMESTAMP")
        elif basis == "UNKNOWN_BACKFILL":
            if st != "BACKFILLED_UNKNOWN" or row.get("source_published_at") is not None or row.get("available_at") is not None:
                errors.append("MOPS_PDF UNKNOWN_BACKFILL 必須對應 BACKFILLED_UNKNOWN 且公布／可用時間為 null")
            if row.get("time_precision") != "UNKNOWN":
                errors.append("MOPS_PDF UNKNOWN_BACKFILL.time_precision 必須是 UNKNOWN")
        if str(row.get("derivation", "")).startswith("COMPARATIVE_") and not row.get("comparative_of_period"):
            errors.append("MOPS_PDF 比較欄缺少 comparative_of_period")
        if row.get("supersedes_observation_id") and row.get("revision_reason") not in {
                "COMPARATIVE_RESTATED", "COMPARATIVE_CONFIRMED_UNCHANGED"}:
            errors.append("MOPS_PDF revision_reason 無效")
    else:
        if row.get("availability_basis") and row.get("availability_basis") not in {
                "OFFICIAL_TIMESTAMP", "OFFICIAL_DATE", "FIRST_SEEN_CONSERVATIVE", "UNKNOWN_BACKFILL", "LEGACY_UNKNOWN"}:
            errors.append("availability_basis 無效")
        if row.get("time_precision") and row.get("time_precision") not in {"EXACT_TIMESTAMP", "DATE_ONLY", "UNKNOWN"}:
            errors.append("time_precision 無效")
    return errors


def _named_items(items, label, extra_check=None):
    errors, names = [], []
    if not isinstance(items, list):
        return [f"{label} 必須是清單"], names
    for item in items:
        ok = (isinstance(item, dict) and isinstance(item.get("local_name"), str) and item.get("local_name"))
        if ok and extra_check:
            ok = extra_check(item)
        if not ok:
            errors.append(f"{label} 項目格式錯誤")
            continue
        names.append(item["local_name"])
    if len(names) != len(set(names)):
        errors.append(f"{label} 不得重複")
    return errors, names


def _has_namespaces(item):
    ns = item.get("namespaces_checked")
    return isinstance(ns, list) and bool(ns) and all(isinstance(x, str) and x for x in ns)


def _validate_absent_components(row):
    """台股有息負債組成的結構與雜湊檢查。

    1.8.1 格式：components＋absent_components。
    1.8.2 格式（C181-01）：再加 covered_by_aggregate、excluded_overlapping_components、overlap_rules_version；
    四類科目互斥，三類清單與規則版本都參與 components_sha256。
    """
    if row.get("metric_id") not in MOPS_DEBT_METRICS:
        return ["absent_components／涵蓋欄位只適用於台股有息負債細項"]
    errors, absent_names = _named_items(row.get("absent_components"), "absent_components", _has_namespaces)
    components = row.get("components")
    if not isinstance(components, list) or not components:
        errors.append("有 absent_components 時至少要有一個存在的組成科目")
        return errors
    if not str(row.get("derivation", "")).startswith("COMPOSITE_"):
        errors.append("有 absent_components 的負債觀測值必須是 COMPOSITE_ 推導")
    present = [c.get("local_name") for c in components if isinstance(c, dict)]
    payload = {"components": components, "absent_components": row.get("absent_components")}
    groups = [set(present), set(absent_names)]
    if any(k in row for k in OVERLAP_KEYS):
        missing = [k for k in OVERLAP_KEYS if k not in row]
        if missing:
            errors.append("1.8.2 負債格式缺少 " + "、".join(missing))
        if not isinstance(row.get("overlap_rules_version"), str) or not row.get("overlap_rules_version"):
            errors.append("overlap_rules_version 必須是非空字串")
        cov_err, covered = _named_items(
            row.get("covered_by_aggregate"), "covered_by_aggregate",
            lambda i: _has_namespaces(i) and i.get("aggregate_local_name") in present)
        exc_err, excluded = _named_items(
            row.get("excluded_overlapping_components"), "excluded_overlapping_components",
            lambda i: (isinstance(i.get("namespace"), str) and i.get("namespace")
                       and isinstance(i.get("value"), (int, float)) and not isinstance(i.get("value"), bool)
                       and i.get("value") >= 0 and i.get("excluded_because") in EXCLUDED_BECAUSE
                       and isinstance(i.get("overlaps_with"), list) and bool(i.get("overlaps_with"))
                       and all(x in present for x in i["overlaps_with"])))
        errors += cov_err + exc_err
        groups += [set(covered), set(excluded)]
        payload.update({k: row.get(k) for k in OVERLAP_KEYS})
        if REPRESENTED_KEY in row:
            rep_err, represented = _named_items(
                row.get(REPRESENTED_KEY), REPRESENTED_KEY,
                lambda i: (_has_namespaces(i) and isinstance(i.get("details_used"), list) and bool(i.get("details_used"))
                           and all(x in present for x in i["details_used"])))
            if not row.get(REPRESENTED_KEY):
                rep_err.append(f"{REPRESENTED_KEY} 只能在非空時出現")
            errors += rep_err
            groups.append(set(represented))
            payload[REPRESENTED_KEY] = row.get(REPRESENTED_KEY)
    elif REPRESENTED_KEY in row:
        errors.append(f"{REPRESENTED_KEY} 只能出現在 1.8.2 以後的負債格式")
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            if groups[i] & groups[j]:
                errors.append("同一科目不得同時屬於組成、缺席、彙總涵蓋、重疊排除或彙總未申報中的兩類")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if row.get("components_sha256") != hashlib.sha256(text.encode()).hexdigest():
        errors.append("components_sha256 必須涵蓋 components、absent_components 及 1.8.2／1.8.3 的涵蓋欄位")
    flagged = ABSENT_COMPONENTS_LIMITATION in (row.get("limitations") or [])
    if bool(row.get("absent_components")) != flagged:
        errors.append(f"有缺席細項時必須且只能標 {ABSENT_COMPONENTS_LIMITATION}")
    return errors


def validate_active_mops_debt(observations):
    """目前有效（未被取代）的台股負債觀測值都必須是 1.8.2 格式（缺席、彙總涵蓋、重疊排除三類都明寫）。"""
    active_ids = {r.get("observation_id") for r in resolve_visible_observation_versions(observations)}
    required = ("absent_components",) + OVERLAP_KEYS
    return [f"{r.get('observation_id')}: 有效的 MOPS 負債觀測值缺少 " + "、".join(k for k in required if k not in r)
            for r in observations
            if r.get("source_system") == "MOPS_XBRL" and r.get("metric_id") in MOPS_DEBT_METRICS
            and r.get("observation_id") in active_ids and any(k not in r for k in required)]


# ---------- 1.8.2（C181-02）觀測值寫入紀錄 ----------
# ingested_at 維持「原始來源首次收錄時間」。observation 何時真正寫進 canonical 檔，記在獨立的
# append-only 寫入紀錄：每批一列，含批次 ID、實際寫入時間、observation_id 清單、寫入後檔案長度與 SHA-256。

def write_ledger_path_for(observations_path):
    return os.path.join(os.path.dirname(os.path.abspath(observations_path)), WRITE_LEDGER_NAME)


def _file_state(path):
    with open(path, "rb") as f:
        data = f.read()
    return len(data), hashlib.sha256(data).hexdigest()


def _prefix_sha256(path, length):
    with open(path, "rb") as f:
        data = f.read(length)
    return hashlib.sha256(data).hexdigest() if len(data) == length else None


def make_write_batch(observation_ids, observations_path, reason, written_at=None,
                     written_at_basis="RECORDED_AT_WRITE", evidence=None, recorded_at=None,
                     staging_validation_sha256=None):
    """依目前 observations 檔狀態組一筆寫入紀錄（尚未寫入紀錄檔）。"""
    if written_at_basis not in WRITE_BASES:
        raise ValueError("written_at_basis 無效")
    ids = list(observation_ids)
    length, digest = _file_state(observations_path)
    recorded_at = recorded_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if written_at_basis == "RECORDED_AT_WRITE":
        written_at = written_at or recorded_at
    identity = json.dumps({"written_at": written_at, "ids": ids, "length": length, "sha": digest},
                          sort_keys=True, separators=(",", ":"))
    return {"batch_id": "OBSWRITE_" + hashlib.sha256(identity.encode()).hexdigest(),
            "written_at": written_at, "written_at_basis": written_at_basis, "evidence": evidence,
            "reason": reason, "observation_ids": ids, "observation_count": len(ids),
            "file_byte_length_after": length, "file_sha256_after": digest, "recorded_at": recorded_at,
            "staging_validation_sha256": staging_validation_sha256}


def validate_write_batch(entry):
    errors = []
    for k in ("batch_id", "written_at_basis", "reason", "observation_ids", "observation_count",
              "file_byte_length_after", "file_sha256_after", "recorded_at"):
        if entry.get(k) in (None, "") and k != "observation_ids":
            errors.append(f"寫入紀錄缺少 {k}")
    basis = entry.get("written_at_basis")
    if basis not in WRITE_BASES:
        errors.append("written_at_basis 無效")
    if basis == "ASSUMED_EQUAL_TO_INGESTED_AT":
        if entry.get("written_at") is not None:
            errors.append("ASSUMED_EQUAL_TO_INGESTED_AT 的 written_at 必須是 null（逐筆用 ingested_at）")
        if entry.get("evidence") != "ASSUMED_EQUAL_TO_INGESTED_AT":
            errors.append("ASSUMED_EQUAL_TO_INGESTED_AT 的 evidence 必須照實寫 ASSUMED_EQUAL_TO_INGESTED_AT")
    else:
        try:
            if parse_time(entry.get("written_at"), "written_at") is None:
                errors.append("written_at 必填")
        except ValueError as e:
            errors.append(str(e))
    if basis == "BACKFILLED_FROM_EVIDENCE" and not entry.get("evidence"):
        errors.append("事後補登必須寫明 evidence")
    ids = entry.get("observation_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or entry.get("observation_count") != len(ids or []):
        errors.append("observation_ids 必須是非空、無重複，且與 observation_count 相符")
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("file_sha256_after", ""))):
        errors.append("file_sha256_after 必須是完整 SHA-256")
    return errors


def append_write_ledger(entry, ledger_path=WRITE_LEDGER_PATH, lock_holder=None):
    """寫入紀錄檔只新增不改寫；batch_id 與 observation_id 都不可重複登記。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, ledger_path)
    errors = validate_write_batch(entry)
    if errors:
        raise ValueError("INVALID_WRITE_BATCH:" + "|".join(errors))
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with file_lock(ledger_path):
        old = read_jsonl(ledger_path)
        if any(x.get("batch_id") == entry["batch_id"] for x in old):
            raise ValueError("WRITE_BATCH_DUPLICATE:" + entry["batch_id"])
        seen = {oid for x in old for oid in x.get("observation_ids") or []}
        dup = seen & set(entry["observation_ids"])
        if dup:
            raise ValueError("OBSERVATION_ALREADY_IN_WRITE_LEDGER:" + sorted(dup)[0])
        if old and int(entry["file_byte_length_after"]) <= int(old[-1].get("file_byte_length_after", 0)):
            raise ValueError("WRITE_LEDGER_FILE_LENGTH_NOT_INCREASING")
        _append_bytes(ledger_path, (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"), "ledger")
    return entry


# ---------- 1.8.3（C182-02）可復原的兩階段寫入；1.8.4 修補 C183-01／02／03 ----------
# 流程：寫 journal（寫入前兩個檔的長度與雜湊、預計追加的位元組雜湊）→ 追加觀測值 → 追加寫入紀錄 →
# 把 journal 改名封存為「…完成」。任何一步失敗就把兩個檔截回 journal 記載的寫入前長度並驗證雜湊。
# 下次寫入前若發現殘留 journal，先判斷是已完整寫完（只補標完成）還是未完成（截回）。
# 1.8.4：
# - 復原只看原始位元組，不先把整份寫入紀錄當 JSON 解析；尾端半行也能安全截回（C183-01）。
# - 復原分兩階段：先預檢兩個檔（存在、長度不小於寫入前、寫入前那段 SHA-256 相符），全部通過才截斷；
#   任一項失敗兩個檔都不動，報錯交人工處理（C183-02）。
# - 封存的 journal 檔名含建立時間與 journal 內容 SHA-256，目的檔已存在就報錯，不覆寫（C183-03）。
# append-only 的例外只有兩個：截斷本次尚未完成而追加的位元組、把 journal 改名封存。

JOURNAL_SUFFIX = ".write_journal.json"
_FAULT_HOOK = None   # 只給故障注入測試用：callable(stage)，可在指定步驟拋例外


class WriteJournalError(RuntimeError):
    """journal 無法安全復原（例如既有內容的雜湊對不上），需要人工處理。"""


def journal_path_for(observations_path):
    return os.path.abspath(observations_path) + JOURNAL_SUFFIX


def _fault(stage):
    if _FAULT_HOOK is not None:
        _FAULT_HOOK(stage)


def _append_bytes(path, data, stage):
    """分兩段追加並 fsync，讓測試能模擬「寫到一半」。"""
    _fault(stage + ":before")
    half = len(data) // 2
    with open(path, "ab") as f:
        f.write(data[:half]); f.flush(); os.fsync(f.fileno())
        _fault(stage + ":mid")
        f.write(data[half:]); f.flush(); os.fsync(f.fileno())
    _fault(stage + ":after")


def _state_or_empty(path):
    if not os.path.exists(path):
        return 0, hashlib.sha256(b"").hexdigest()
    return _file_state(path)


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _preflight(journal, observations_path):
    """不改任何檔：兩個檔都要存在（寫入前長度為 0 時可不存在）、長度不小於寫入前、寫入前那段雜湊相符。

    回傳 {"observations": bytes, "ledger": bytes}；任一項不符就拋 WriteJournalError。
    """
    out = {}
    checks = (("observations", observations_path, journal["observations_length_before"], journal["observations_sha256_before"]),
              ("ledger", write_ledger_path_for(observations_path), journal["ledger_length_before"], journal["ledger_sha256_before"]))
    problems = []
    for label, path, length, digest in checks:
        if not os.path.exists(path):
            if length == 0:
                out[label] = b""
                continue
            problems.append(f"{label} 不存在")
            continue
        data = _read_bytes(path)
        if len(data) < length:
            problems.append(f"{label} 比寫入前還短（{len(data)} < {length}）")
        elif hashlib.sha256(data[:length]).hexdigest() != digest:
            problems.append(f"{label} 寫入前的內容已改變")
        out[label] = data
    if problems:
        raise WriteJournalError("journal 預檢失敗，兩個檔都不動：" + "；".join(problems))
    return out


def _tail_is_expected_batch(journal, data):
    """寫入前內容之後的尾端，是否恰好是 journal 預期的那一批（觀測值與一行完整的寫入紀錄）。"""
    obs_tail = data["observations"][journal["observations_length_before"]:]
    if len(obs_tail) != journal["append_byte_length"] or hashlib.sha256(obs_tail).hexdigest() != journal["append_sha256"]:
        return False
    led_tail = data["ledger"][journal["ledger_length_before"]:]
    if not led_tail.endswith(b"\n") or led_tail.count(b"\n") != 1:
        return False
    try:
        entry = json.loads(led_tail.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(entry, dict) or validate_write_batch(entry):
        return False
    return (entry.get("observation_ids") == journal["observation_ids"]
            and int(entry.get("file_byte_length_after", -1)) == len(data["observations"])
            and entry.get("file_sha256_after") == hashlib.sha256(data["observations"]).hexdigest())


def _truncate_checked(path, length, digest):
    if not os.path.exists(path):
        return   # 預檢已確認：只有寫入前長度為 0 時才可能不存在
    if os.path.getsize(path) > length:
        with open(path, "r+b") as f:
            f.truncate(length); f.flush(); os.fsync(f.fileno())
    if _state_or_empty(path) != (length, digest):
        raise WriteJournalError(f"{os.path.basename(path)} 截斷後雜湊不符")


def _archive_journal(jpath, status):
    """把 journal 改名封存；檔名含建立時間與內容雜湊，目的檔已存在就報錯，不覆寫歷史。"""
    raw = _read_bytes(jpath)
    try:
        created = json.loads(raw.decode("utf-8")).get("created_at") or ""
    except (UnicodeDecodeError, json.JSONDecodeError):
        created = ""
    stamp = re.sub(r"[^0-9A-Za-z]", "", created) or "UNKNOWN"
    dest = f"{jpath}.{stamp}_{hashlib.sha256(raw).hexdigest()[:16]}.{status}"
    if os.path.exists(dest):
        raise WriteJournalError(f"journal 封存檔已存在，拒絕覆寫：{os.path.basename(dest)}")
    os.rename(jpath, dest)
    return dest


def journal_archives(observations_path, status=None):
    """已封存的 journal（完成或已復原），依檔名排序。"""
    base = os.path.basename(journal_path_for(observations_path)) + "."
    folder = os.path.dirname(journal_path_for(observations_path))
    names = sorted(n for n in os.listdir(folder) if n.startswith(base) and not n.endswith(".tmp")
                   and (status is None or n.endswith("." + status)))
    return [os.path.join(folder, n) for n in names]


def _rollback(journal, observations_path):
    """兩階段復原：先預檢（不改檔），通過後才把兩個檔截回寫入前長度，最後封存 journal。"""
    _preflight(journal, observations_path)
    _truncate_checked(observations_path, journal["observations_length_before"], journal["observations_sha256_before"])
    _truncate_checked(write_ledger_path_for(observations_path), journal["ledger_length_before"], journal["ledger_sha256_before"])
    return _archive_journal(journal_path_for(observations_path), "已復原")


def _load_journal(observations_path):
    path = journal_path_for(observations_path)
    try:
        with open(path, encoding="utf-8") as f:
            journal = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise WriteJournalError("journal 本身無法解析，需人工處理") from e
    required = ("observations_file", "observations_length_before", "observations_sha256_before",
                "ledger_length_before", "ledger_sha256_before", "append_byte_length", "append_sha256", "observation_ids")
    if any(k not in journal for k in required):
        raise WriteJournalError("journal 欄位不完整，需人工處理")
    if journal.get("observations_file") != os.path.basename(observations_path):
        raise WriteJournalError("journal 指向的檔案與目前觀測值檔不同")
    return journal


def recover_pending_write(observations_path, lock_holder=None):
    """有殘留 journal 時先處理：預檢 → 完整寫完就封存為完成，否則截回寫入前。回傳 None／"COMPLETED"／"ROLLED_BACK"。

    只依原始位元組判斷，不解析整份寫入紀錄；寫入紀錄尾端是半行時也能截回（C183-01）。
    """
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, observations_path)
    if not os.path.exists(journal_path_for(observations_path)):
        return None
    journal = _load_journal(observations_path)
    data = _preflight(journal, observations_path)
    if _tail_is_expected_batch(journal, data):
        _archive_journal(journal_path_for(observations_path), "完成")
        return "COMPLETED"
    _rollback(journal, observations_path)
    return "ROLLED_BACK"


def transactional_append_observations(observations_path, rows, ledger_reason, lock_holder=None):
    """把 rows 追加進觀測值檔並同步追加一筆寫入紀錄；兩者一起成功或一起失敗。呼叫端須已持有檔案鎖。"""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, observations_path)
    ledger_path = write_ledger_path_for(observations_path)
    recover_pending_write(observations_path, lock_holder=lock_holder)
    if os.path.exists(observations_path):
        assert_write_ledger_in_sync(observations_path, ledger_path)
    payload = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for r in rows)
    before_bytes = _read_bytes(observations_path) if os.path.exists(observations_path) else b""
    from staging_write import validate_candidate, validate_jsonl_bytes
    def validate_staged(candidate):
        detail = validate_jsonl_bytes(candidate)
        appended_errors = []
        for row in rows:
            appended_errors.extend(validate_observation(row))
        if appended_errors:
            raise ValueError("STAGING_OBSERVATION_INVALID:" + "|".join(appended_errors))
        combined = [json.loads(line) for line in candidate.decode("utf-8").splitlines() if line.strip()]
        # Exercise supersede resolution on the full candidate before canonical append.
        resolve_visible_observation_versions(combined)
        detail.update({"affected_issuers": sorted({r.get("issuer_id") for r in rows if r.get("issuer_id")}),
                       "affected_metrics": sorted({r.get("metric_id") for r in rows if r.get("metric_id")}),
                       "append_count": len(rows)})
        return detail
    staging_report = validate_candidate(
        observations_path, before_bytes + payload, payload=payload,
        writer="data_spine.transactional_append_observations",
        validator=validate_staged,
        extra={"reason": ledger_reason, "observation_ids": [r["observation_id"] for r in rows]})
    obs_len, obs_sha = _state_or_empty(observations_path)
    led_len, led_sha = _state_or_empty(ledger_path)
    journal = {"journal_version": 1, "observations_file": os.path.basename(observations_path),
               "ledger_file": os.path.basename(ledger_path),
               "observations_length_before": obs_len, "observations_sha256_before": obs_sha,
               "ledger_length_before": led_len, "ledger_sha256_before": led_sha,
               "append_byte_length": len(payload), "append_sha256": hashlib.sha256(payload).hexdigest(),
               "observation_ids": [r["observation_id"] for r in rows], "ledger_reason": ledger_reason,
               "staging_validation_sha256": staging_report["validation_report_sha256"],
               "created_at": dt.datetime.now().astimezone().isoformat(timespec="microseconds")}
    jpath = journal_path_for(observations_path)
    _write_json_atomic(jpath, journal)
    try:
        _fault("journal_written")
        _append_bytes(observations_path, payload, "observations")
        entry = make_write_batch(journal["observation_ids"], observations_path, ledger_reason,
                                 staging_validation_sha256=journal["staging_validation_sha256"])
        append_write_ledger(entry, ledger_path, lock_holder=lock_holder)
        if not _tail_is_expected_batch(journal, _preflight(journal, observations_path)):
            raise WriteJournalError("寫入後核對失敗")
        _fault("before_commit")
        _archive_journal(jpath, "完成")
    except Exception:
        _rollback(journal, observations_path)
        raise
    return entry


def read_observations_checked(path=None):
    """讀取端（health、trend、company、attribution、risk、schema 2）共用：觀測值檔必須與寫入紀錄最後一批一致。

    只核對最後一批的長度與 SHA-256、各批筆數合計等於列數，並拒絕有未完成 journal 的狀態；
    不重算全部歷史批次（完整檢查用 validate_write_ledger）。不一致就報錯，不默默使用未登記的列。
    """
    path = path or OBSERVATIONS_PATH
    if os.path.exists(journal_path_for(path)):
        raise ValueError("OBSERVATIONS_WRITE_PENDING：有未完成的觀測值寫入 journal，請等寫入結束或先執行復原")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        data = f.read()
    entries = read_jsonl(write_ledger_path_for(path))
    if not entries:
        if data.strip():
            raise ValueError("OBSERVATIONS_WRITE_LEDGER_MISSING：觀測值檔沒有寫入紀錄")
        return []
    last = entries[-1]
    if len(data) != int(last.get("file_byte_length_after", -1)) or hashlib.sha256(data).hexdigest() != last.get("file_sha256_after"):
        raise ValueError("OBSERVATIONS_NOT_IN_WRITE_LEDGER：觀測值檔與寫入紀錄最後一批的長度或雜湊不一致，可能有未登記的列")
    rows = []
    for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError as e: raise ValueError(f"{path} 第 {n} 行不是合法 JSON") from e
    if sum(int(e.get("observation_count", 0)) for e in entries) != len(rows):
        raise ValueError("OBSERVATIONS_NOT_IN_WRITE_LEDGER：寫入紀錄的筆數合計與觀測值列數不符")
    semantics_path = (SUPERSEDE_SEMANTICS_PATH if os.path.abspath(path) == os.path.abspath(OBSERVATIONS_PATH)
                      else os.path.join(os.path.dirname(os.path.abspath(path)), "observation_supersede_semantics.jsonl"))
    semantics = load_supersede_semantics(semantics_path, rows) if os.path.exists(semantics_path) else []
    return apply_supersede_semantics(rows, semantics)


def assert_write_ledger_in_sync(observations_path, ledger_path=None):
    """寫入前檢查：紀錄檔最後一批的長度與雜湊必須等於目前 observations 檔，否則代表有未登記的寫入。"""
    ledger_path = ledger_path or write_ledger_path_for(observations_path)
    exists = os.path.exists(observations_path) and os.path.getsize(observations_path) > 0
    entries = read_jsonl(ledger_path)
    if not entries:
        if exists and os.path.abspath(observations_path) == os.path.abspath(OBSERVATIONS_PATH):
            raise ValueError("WRITE_LEDGER_MISSING_FOR_CANONICAL_OBSERVATIONS")
        return
    length, digest = _file_state(observations_path)
    last = entries[-1]
    if int(last["file_byte_length_after"]) != length or last["file_sha256_after"] != digest:
        raise ValueError("WRITE_LEDGER_OUT_OF_SYNC")


def validate_write_ledger(observations_path=OBSERVATIONS_PATH, ledger_path=None):
    """完整檢查：每批雜湊等於當時檔案前綴、每筆 observation 恰好登記一次、最後一批等於目前檔案。"""
    ledger_path = ledger_path or write_ledger_path_for(observations_path)
    entries = read_jsonl(ledger_path)
    rows = read_jsonl(observations_path)
    errors = []
    seen = {}
    previous = 0
    for i, entry in enumerate(entries, 1):
        errors += [f"batch {i}: {e}" for e in validate_write_batch(entry)]
        length = int(entry.get("file_byte_length_after", 0))
        if length <= previous:
            errors.append(f"batch {i}: 檔案長度沒有增加")
        previous = length
        if _prefix_sha256(observations_path, length) != entry.get("file_sha256_after"):
            errors.append(f"batch {i}: 檔案前綴 SHA-256 與紀錄不符")
        for oid in entry.get("observation_ids") or []:
            if oid in seen:
                errors.append(f"batch {i}: {oid} 重複登記")
            seen[oid] = i
    ids = [r.get("observation_id") for r in rows]
    missing = [oid for oid in ids if oid not in seen]
    unknown = sorted(set(seen) - set(ids))
    if missing:
        errors.append(f"{len(missing)} 筆 observation 沒有寫入紀錄，例如 {missing[0]}")
    if unknown:
        errors.append(f"寫入紀錄有 {len(unknown)} 個不存在的 observation_id，例如 {unknown[0]}")
    if entries:
        length, digest = _file_state(observations_path)
        if int(entries[-1]["file_byte_length_after"]) != length or entries[-1]["file_sha256_after"] != digest:
            errors.append("最後一批的長度或雜湊不等於目前 observations 檔")
    return errors


_WRITE_TIMES_CACHE = {}


def load_write_times(ledger_path=WRITE_LEDGER_PATH):
    """{observation_id: 實際寫入時間}；ASSUMED_EQUAL_TO_INGESTED_AT 批次的值為 None（逐筆用 ingested_at）。"""
    try:
        stat = os.stat(ledger_path)
    except OSError:
        return {}
    key = (os.path.abspath(ledger_path), stat.st_mtime_ns, stat.st_size)
    if key not in _WRITE_TIMES_CACHE:
        times = {}
        for entry in read_jsonl(ledger_path):
            stamp = None if entry.get("written_at_basis") == "ASSUMED_EQUAL_TO_INGESTED_AT" else entry.get("written_at")
            for oid in entry.get("observation_ids") or []:
                times[oid] = stamp
        _WRITE_TIMES_CACHE.clear()
        _WRITE_TIMES_CACHE[key] = times
    return _WRITE_TIMES_CACHE[key]


def observation_written_at(row, write_times):
    """選版本用的寫入時間。優先序：記憶體中的 selection_written_at（下跌歸因以申報時間為準）
    → 寫入紀錄 → ingested_at（沒有寫入紀錄的舊列）。都解析不了時回 None，視為一直都在。"""
    for value in (row.get("selection_written_at"), write_times.get(row.get("observation_id")), row.get("ingested_at")):
        if value:
            try:
                return parse_time(value, "written_at")
            except ValueError:
                continue
    return None


def load_sessions(path=SESSIONS_PATH, row_count=None):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if row_count is not None:
        rows = rows[:int(row_count)]
    # append-only 修訂：被 supersede 的列不屬於目前有效日曆，但仍留在檔案。
    superseded = {r.get("supersedes_session_id") for r in rows if r.get("supersedes_session_id")}
    active = [r for r in rows if r.get("session_id") not in superseded]
    return sorted(active, key=lambda r: (r.get("market", ""), r.get("open_at", "")))


def session_digest(path=SESSIONS_PATH):
    return sha256_file(path)


def _calendar_prefix(path, row_count):
    with open(path, "rb") as f: lines = f.readlines()
    return b"".join(lines[:1 + int(row_count)])


def register_calendar_version(version_id, path=SESSIONS_PATH, ledger_path=CALENDAR_VERSIONS_PATH, created_at=None,
                              lock_holder=None):
    with open(path, encoding="utf-8-sig", newline="") as f: row_count = sum(1 for _ in csv.DictReader(f))
    digest = hashlib.sha256(_calendar_prefix(path, row_count)).hexdigest()
    old = read_jsonl(ledger_path)
    hit = next((x for x in old if x.get("calendar_version_id") == version_id), None)
    if hit:
        if hit.get("calendar_sha256") != digest or int(hit.get("row_count", -1)) != row_count:
            raise ValueError("calendar_version_id 已存在但內容不同")
        return hit
    row = {"calendar_version_id": version_id, "calendar_sha256": digest, "row_count": row_count,
           "created_at": created_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    append_jsonl(ledger_path, row, "calendar_version_id", "supersedes_calendar_version_id",
                 lock_holder=lock_holder)
    return row


def verify_calendar_binding(version_id, digest, path=SESSIONS_PATH, ledger_path=CALENDAR_VERSIONS_PATH):
    hit = next((x for x in read_jsonl(ledger_path) if x.get("calendar_version_id") == version_id), None)
    if not hit: return False, "CALENDAR_VERSION_NOT_REGISTERED"
    if hit.get("calendar_sha256") != digest: return False, "CALENDAR_HASH_NOT_BOUND_TO_VERSION"
    actual = hashlib.sha256(_calendar_prefix(path, hit.get("row_count", 0))).hexdigest()
    if actual != digest: return False, "CALENDAR_PREFIX_INTEGRITY_FAILED"
    return True, None


def load_bound_sessions(version_id, digest, path=SESSIONS_PATH, ledger_path=CALENDAR_VERSIONS_PATH):
    ok, reason = verify_calendar_binding(version_id, digest, path, ledger_path)
    if not ok:
        raise ValueError(f"CALENDAR_BINDING_FAILED:{reason}")
    hit = next(x for x in read_jsonl(ledger_path) if x.get("calendar_version_id") == version_id)
    return load_sessions(path, hit["row_count"])


def _market_sessions(sessions, market):
    out = []
    for s in sessions:
        if s.get("market") != market or s.get("session_status") != "OPEN": continue
        z = dict(s)
        try:
            z["_open"] = parse_time(s["open_at"], "open_at")
            z["_close"] = parse_time(s["close_at"], "close_at")
        except (KeyError, ValueError) as e:
            raise ValueError(f"INVALID_MARKET_SESSION:{s.get('session_id', 'UNKNOWN')}:{e}") from e
        if z["_close"] <= z["_open"]:
            raise ValueError(f"INVALID_MARKET_SESSION:{s.get('session_id', 'UNKNOWN')}:close_at 不得早於或等於 open_at")
        out.append(z)
    return sorted(out, key=lambda x: x["_open"])


def event_effective_sessions(event, security, sessions):
    """回傳 information session 與 block session；DATE_ONLY 採兩個相反的保守方向。"""
    precision = event.get("time_precision")
    if precision not in TIME_PRECISIONS or precision == "UNKNOWN":
        return {"status": "DATA_INSUFFICIENT", "reason": "EVENT_TIME_UNKNOWN",
                "information_effective_session_id": None, "block_effective_session_id": None}
    try:
        event_tz = ZoneInfo(event["event_timezone"])
        market_tz = ZoneInfo(security["market_timezone"])
        if precision == "DATE_ONLY":
            day = _date(event.get("event_local_date"), "event_local_date")
            earliest = dt.datetime.combine(day, dt.time.min, event_tz).astimezone(market_tz)
            latest = dt.datetime.combine(day, dt.time.max, event_tz).astimezone(market_tz)
        else:
            raw_time = event.get("event_local_time")
            if isinstance(raw_time, str) and "T" not in raw_time:
                day = _date(event.get("event_local_date"), "event_local_date")
                exact = dt.datetime.combine(day, dt.time.fromisoformat(raw_time), event_tz).astimezone(market_tz)
            else:
                exact = parse_time(raw_time, "event_local_time").astimezone(market_tz)
            earliest = latest = exact
    except Exception as e:
        return {"status": "DATA_INSUFFICIENT", "reason": str(e),
                "information_effective_session_id": None, "block_effective_session_id": None}
    ss = _market_sessions(sessions, security["market"])
    info = next((s for s in ss if s["_open"] > latest), None)
    block = next((s for s in reversed(ss) if s["_open"] <= earliest), None)
    if not info or not block:
        return {"status": "DATA_INSUFFICIENT", "reason": "CALENDAR_COVERAGE_MISSING",
                "information_effective_session_id": info and info["session_id"],
                "block_effective_session_id": block and block["session_id"]}
    return {"status": "OK", "reason": None,
            "information_effective_session_id": info["session_id"],
            "block_effective_session_id": block["session_id"]}


def trading_session_distance(analysis_session_id, block_session_id, market, sessions):
    ss = _market_sessions(sessions, market)
    ids = [s["session_id"] for s in ss]
    if analysis_session_id not in ids or block_session_id not in ids:
        return None, "CALENDAR_COVERAGE_MISSING"
    a, b = ids.index(analysis_session_id), ids.index(block_session_id)
    if b < a: return None, "EVENT_ALREADY_PASSED"
    return b - a, None


def _tw_statutory_windows(year):
    """Return quarter-end, shadow-window start and statutory deadline dates."""
    return [
        {"fiscal_year":year,"fiscal_period":"Q1","period_end":dt.date(year,3,31),
         "window_start":dt.date(year,4,25),"statutory_deadline":dt.date(year,5,15)},
        {"fiscal_year":year,"fiscal_period":"Q2","period_end":dt.date(year,6,30),
         "window_start":dt.date(year,7,25),"statutory_deadline":dt.date(year,8,14)},
        {"fiscal_year":year,"fiscal_period":"Q3","period_end":dt.date(year,9,30),
         "window_start":dt.date(year,10,25),"statutory_deadline":dt.date(year,11,14)},
        {"fiscal_year":year,"fiscal_period":"Q4","period_end":dt.date(year,12,31),
         "window_start":dt.date(year+1,1,25),"statutory_deadline":dt.date(year+1,3,31)},
    ]


def derive_tw_statutory_event_window(security_identifier, analysis_session_id, observations, sessions, registry=None):
    """TW statutory earnings window used by the versioned G-S3b-TW policy.

    The window starts on quarter-end + 25 calendar days and ends at the legal
    deadline.  A non-statutory canonical observation can end it at the next TW
    session.  STATUTORY_DEADLINE_CONSERVATIVE rows are deliberately ignored as
    evidence of an actual filing time.
    """
    registry = registry or load_registry()
    sec, state = resolve_security(security_identifier, registry)
    if state != "OK":
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"reason":state}
    if sec.get("market") != "TW":
        return {"status":"NOT_APPLICABLE","distance":None,"reason":"TW_ONLY"}
    market_sessions = _market_sessions(sessions, "TW")
    ids = [s["session_id"] for s in market_sessions]
    if analysis_session_id not in ids:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"reason":"ANALYSIS_SESSION_NOT_FOUND"}
    analysis_i = ids.index(analysis_session_id)
    analysis_date = _date(market_sessions[analysis_i]["session_date"], "session_date")

    candidates = []
    for year in range(analysis_date.year - 1, analysis_date.year + 2):
        candidates.extend(_tw_statutory_windows(year))
    candidates.sort(key=lambda x: x["window_start"])
    for window in candidates:
        # A legal deadline already in the past cannot be the current/future window.
        if window["statutory_deadline"] < analysis_date:
            continue
        start = next((s for s in market_sessions
                      if _date(s["session_date"], "session_date") >= window["window_start"]), None)
        deadline_exclusive = next((s for s in market_sessions
                                   if _date(s["session_date"], "session_date") > window["statutory_deadline"]), None)
        if not start or not deadline_exclusive:
            return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,
                    "reason":"CALENDAR_COVERAGE_MISSING","fiscal_year":window["fiscal_year"],
                    "fiscal_period":window["fiscal_period"]}

        actual_times = []
        for row in observations or []:
            if row.get("issuer_id") != sec.get("issuer_id"):
                continue
            if row.get("availability_basis") in {
                    "STATUTORY_DEADLINE_CONSERVATIVE",
                    "STATUTORY_DEADLINE_OR_CORRECTION_DATE_CONSERVATIVE"}:
                continue
            if str(row.get("period_end") or "") != window["period_end"].isoformat():
                continue
            period = str(row.get("fiscal_period") or "").upper()
            if window["fiscal_period"] == "Q4":
                if period not in {"Q4", "FY"}: continue
            elif period and period != window["fiscal_period"]:
                continue
            try: available = parse_time(row.get("available_at"), "observation.available_at")
            except ValueError: continue
            deadline_end = dt.datetime.combine(window["statutory_deadline"], dt.time.max, ZoneInfo("Asia/Taipei"))
            period_start = dt.datetime.combine(window["period_end"], dt.time.min, ZoneInfo("Asia/Taipei"))
            if period_start <= available.astimezone(ZoneInfo("Asia/Taipei")) < deadline_end:
                actual_times.append(available)
        actual = min(actual_times) if actual_times else None
        end_exclusive = deadline_exclusive
        if actual:
            end_exclusive = next((s for s in market_sessions if s["_open"] > actual), None)
            if not end_exclusive:
                return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,
                        "reason":"CALENDAR_COVERAGE_MISSING","fiscal_year":window["fiscal_year"],
                        "fiscal_period":window["fiscal_period"]}
        start_i, end_i = ids.index(start["session_id"]), ids.index(end_exclusive["session_id"])
        if analysis_i >= end_i:
            continue
        distance = 0 if analysis_i >= start_i else start_i - analysis_i
        classification = "IN_WINDOW" if distance == 0 else "BEFORE_WINDOW"
        available_at = dt.datetime.combine(window["period_end"], dt.time.max,
                                           ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        coverage = {"record_type":"COVERAGE","issuer_id":sec.get("issuer_id"),
                    "evidence_method":"STATUTORY_CALENDAR","available_at":available_at,
                    "coverage_validity":"STATUTORY_RULE_NO_7_DAY_EXPIRY",
                    "fiscal_year":window["fiscal_year"],"fiscal_period":window["fiscal_period"],
                    "window_start":window["window_start"].isoformat(),
                    "statutory_deadline":window["statutory_deadline"].isoformat()}
        return {"status":"KNOWN_UPCOMING","distance":distance,"reason":None,
                "classification":classification,"fiscal_year":window["fiscal_year"],
                "fiscal_period":window["fiscal_period"],"period_end":window["period_end"].isoformat(),
                "window_start":window["window_start"].isoformat(),
                "window_start_session_id":start["session_id"],
                "statutory_deadline":window["statutory_deadline"].isoformat(),
                "window_end_exclusive_session_id":end_exclusive["session_id"],
                "actual_available_at":actual.isoformat() if actual else None,
                "evidence_method":"STATUTORY_CALENDAR","coverage_record":coverage}
    return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"reason":"CALENDAR_COVERAGE_MISSING"}


def calendar_coverage(analysis_session_id, market, sessions, before=5, after=40):
    ids = [s["session_id"] for s in _market_sessions(sessions, market)]
    if analysis_session_id not in ids: return False, "ANALYSIS_SESSION_NOT_FOUND"
    i = ids.index(analysis_session_id)
    if i < before or len(ids) - i - 1 < after: return False, "CALENDAR_COVERAGE_MISSING"
    return True, None


def _event_ledger_ids(event_ledger):
    ids = set()
    for batch in event_ledger or []:
        if not isinstance(batch, dict) or not batch.get("batch_id") or not batch.get("written_at"):
            continue
        ids.update(x for x in batch.get("event_ids", []) if isinstance(x, str))
    return ids


def _visible_event_versions(events, event_ledger, issuer_id, cutoff):
    registered = _event_ledger_ids(event_ledger)
    visible = []
    for event in events:
        if event.get("issuer_id") != issuer_id or event.get("event_id") not in registered:
            continue
        try:
            available = _event_time_for_read(event.get("available_at"), "event.available_at")
            ingested = _event_time_for_read(event.get("ingested_at"), "event.ingested_at")
        except ValueError as exc:
            raise ValueError(f"EVENT_TIMESTAMP_INVALID:{event.get('event_id')}:{exc}") from exc
        if available <= cutoff and ingested <= cutoff:
            visible.append(event)
    return visible


def _active_event_versions(visible):
    by_id = {e.get("event_id"): e for e in visible}
    children = {}
    for event in visible:
        parent = event.get("supersedes_event_id")
        if parent:
            if parent not in by_id:
                return None, "EVENT_CHAIN_MISSING_PARENT"
            children.setdefault(parent, []).append(event["event_id"])
    if any(len(x) > 1 for x in children.values()):
        return None, "EVENT_CHAIN_FORK"
    # A cycle has no active leaf that can be trusted.
    for start in by_id:
        seen = set(); cur = start
        while cur in children:
            if cur in seen: return None, "EVENT_CHAIN_CYCLE"
            seen.add(cur); cur = children[cur][0]
    superseded = set(children)
    return [e for e in visible if e.get("event_id") not in superseded], None


def _coverage_record_result(record, start_id, end_id, market, sessions, cutoff, validity_days):
    if record.get("record_type") != "COVERAGE": return False, None
    try:
        checked = _event_time_for_read(record.get("checked_at"), "coverage.checked_at")
    except ValueError as exc:
        return False, f"EVENT_TIMESTAMP_INVALID:{record.get('event_id')}:{exc}"
    if checked > cutoff or (cutoff - checked).total_seconds() > validity_days * 86400:
        return False, None
    ids = [s["session_id"] for s in _market_sessions(sessions, market)]
    try:
        return (ids.index(record.get("coverage_start_session_id")) <= ids.index(start_id)
                and ids.index(record.get("coverage_end_session_id")) >= ids.index(end_id)), None
    except ValueError:
        return False, None


def _coverage_record_covers(record, start_id, end_id, market, sessions, cutoff, validity_days):
    """Compatibility boolean wrapper; canonical derivation uses the reason-bearing helper."""
    return _coverage_record_result(record, start_id, end_id, market, sessions, cutoff, validity_days)[0]


def _official_event_date_coverage_applies(event, policy):
    """Return whether an already-visible official event keeps its date valid through the event.

    This is deliberately narrower than ordinary COVERAGE.  It applies only to a
    scheduled earnings event whose acquisition method is explicitly allowlisted
    by the governing rules.  It must never turn absence of an event into
    VERIFIED_NONE; absence continues to require a fresh COVERAGE row.
    """
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    if event.get("record_type") != "EVENT" or event.get("event_type") != "EARNINGS_RESULTS":
        return False
    if event.get("event_status") not in {"SCHEDULED", "RESCHEDULED"}:
        return False
    allowed_methods = set(policy.get("official_evidence_methods") or ())
    methods = {part.strip() for part in str(event.get("evidence_method", "")).split(";") if part.strip()}
    allowed_sources = set(policy.get("official_source_systems") or ())
    return bool(methods & allowed_methods or event.get("source_system") in allowed_sources)


def derive_event_distance(security_identifier, analysis_session_id, event_ids, events, sessions, registry=None,
                          cutoff=None, event_ledger=None, none_window_sessions=20, coverage_valid_days=7,
                          official_event_coverage_policy=None):
    """Derive canonical event status from all ledger-registered versions visible by cutoff."""
    registry = registry or load_registry()
    sec, state = resolve_security(security_identifier, registry)
    if state != "OK": return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":[],"reason":state}
    if cutoff is None:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":[],"reason":"RUN_CUTOFF_MISSING"}
    cutoff = parse_time(cutoff, "run_cutoff_at") if not isinstance(cutoff, dt.datetime) else cutoff
    try:
        visible = _visible_event_versions(events, event_ledger, sec.get("issuer_id"), cutoff)
    except ValueError as exc:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":[],"reason":str(exc)}
    visible_ids = sorted(e["event_id"] for e in visible)
    if sorted(event_ids or []) != visible_ids:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":"EVENT_IDS_MISMATCH"}
    active, chain_error = _active_event_versions(visible)
    if chain_error:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":chain_error}
    coverage = [e for e in active if e.get("record_type") == "COVERAGE"]
    distances = []
    for event in active:
        if event.get("record_type") == "COVERAGE" or event.get("event_status") == "CANCELLED": continue
        effective = event_effective_sessions(event, sec, sessions)
        if effective["status"] != "OK":
            return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":effective["reason"]}
        market_sessions = _market_sessions(sessions, sec["market"])
        ids = [s["session_id"] for s in market_sessions]
        block_id = effective["block_effective_session_id"]
        info_id = effective["information_effective_session_id"]
        if analysis_session_id not in ids or block_id not in ids or info_id not in ids:
            return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":"CALENDAR_COVERAGE_MISSING"}
        analysis_i, block_i, info_i = ids.index(analysis_session_id), ids.index(block_id), ids.index(info_id)
        # DATE_ONLY 事件在「開始阻擋」到「資訊可用」之間都視為事件前；事件當天不會因下一個事件存在而漏擋。
        if block_i <= analysis_i < info_i:
            distances.append((0, block_id, event["event_id"]))
        elif analysis_i < block_i:
            distances.append((block_i - analysis_i, block_id, event["event_id"]))
        else:
            continue
    if distances:
        distance, block_id, eid = min(distances)
        selected = next((event for event in active if event.get("event_id") == eid), None)
        official_date_covered = (
            sec.get("market") == "US" and
            _official_event_date_coverage_applies(selected or {}, official_event_coverage_policy)
        )
        coverage_results = [_coverage_record_result(
            x, analysis_session_id, block_id, sec["market"], sessions, cutoff, coverage_valid_days)
            for x in coverage]
        invalid = next((reason for _covered, reason in coverage_results if reason), None)
        if invalid and not official_date_covered:
            return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":invalid}
        covered = official_date_covered or any(result[0] for result in coverage_results)
        if covered:
            return {"status":"KNOWN_UPCOMING","distance":distance,"evidence":visible_ids,"reason":None,
                    "event_id":eid,
                    "coverage_basis":"OFFICIAL_EVENT_DATE_VALID_THROUGH_EVENT" if official_date_covered else "FRESH_COVERAGE"}
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":"EVENT_COVERAGE_INCOMPLETE"}
    ids = [s["session_id"] for s in _market_sessions(sessions, sec["market"])]
    if analysis_session_id not in ids or ids.index(analysis_session_id) + none_window_sessions >= len(ids):
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":"CALENDAR_COVERAGE_MISSING"}
    end_id = ids[ids.index(analysis_session_id) + none_window_sessions]
    coverage_results = [_coverage_record_result(
        x, analysis_session_id, end_id, sec["market"], sessions, cutoff, coverage_valid_days)
        for x in coverage]
    invalid = next((reason for _covered, reason in coverage_results if reason), None)
    if invalid:
        return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":invalid}
    if any(result[0] for result in coverage_results):
        return {"status":"VERIFIED_NONE_IN_WINDOW","distance":None,"evidence":visible_ids,"reason":None}
    return {"status":"UNKNOWN_OR_INCOMPLETE","distance":None,"evidence":visible_ids,"reason":"EVENT_COVERAGE_INCOMPLETE"}


def event_value(company, run):
    """schema 1 保留宣告值；schema 2 必須由 canonical 資料重算，拒絕信任紀錄宣告值。"""
    if run.get("run_schema_version", 1) != 2:
        return company.get("event_within_days"), "LEGACY_DECLARED_DISTANCE"
    return None, "SCHEMA2_CANONICAL_RECOMPUTE_REQUIRED"


def validate_portfolio(data):
    errors = []
    state = data.get("portfolio_state")
    if state not in PORTFOLIO_STATES: errors.append("portfolio_state 無效")
    positions = data.get("positions")
    if not isinstance(positions, list): errors.append("positions 必須是陣列"); positions = []
    if state in {"NOT_PROVIDED", "PROVIDED_EMPTY"} and positions:
        errors.append(f"{state} 的 positions 必須是空陣列")
    if state == "PROVIDED_NONEMPTY" and not positions:
        errors.append("PROVIDED_NONEMPTY 至少要有一筆 position")
    if data.get("authority") not in (None, PORTFOLIO_AUTHORITY):
        errors.append("portfolio authority 必須是 USER_OWNED_READ_ONLY_TO_AGENTS")
    for i, p in enumerate(positions):
        if not p.get("security_id"): errors.append(f"positions[{i}] 缺少 security_id")
    return errors


def load_portfolio(path=PORTFOLIO_PATH):
    """檔案不存在就是未提供；AI 不會因此建立檔案。"""
    if not os.path.exists(path):
        return {"portfolio_state": "NOT_PROVIDED", "positions": [], "authority": PORTFOLIO_AUTHORITY}
    data = load_json(path)
    errors = validate_portfolio(data)
    if errors: raise ValueError("portfolio.json 無效：" + "；".join(errors))
    return data


def assert_portfolio_write_authority(authority):
    """普通共用鎖不是持股寫入授權。只有使用者直接選檔的專用匯入流程可通過。"""
    if authority != "USER_DIRECT_FILE_IMPORT_SEPARATELY_AUTHORIZED":
        raise PermissionError("PORTFOLIO_USER_OWNED_READ_ONLY_TO_AGENTS")
    return True


def append_market_session(path, row, lock_holder=None):
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    cols = ["session_id", "market", "session_date", "open_at", "close_at", "session_status",
            "calendar_version_id", "source", "source_published_at", "available_at", "ingested_at",
            "supersedes_session_id"]
    existing = []
    for k, value in row.items():
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            raise ValueError(f"日曆欄位不得包含換行：{k}")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f: existing = list(csv.DictReader(f))
    ids = {r.get("session_id") for r in existing}
    if not row.get("session_id") or row["session_id"] in ids: raise ValueError("session_id 缺漏或重複")
    parent = row.get("supersedes_session_id")
    if parent:
        old = next((r for r in existing if r.get("session_id") == parent), None)
        if not old: raise ValueError("supersedes_session_id 不存在")
        if (old.get("market"), old.get("session_date")) != (row.get("market"), row.get("session_date")):
            raise ValueError("日曆修訂必須是同市場同日期")
    elif any(r.get("market") == row.get("market") and r.get("session_date") == row.get("session_date") and
             r.get("session_id") not in {x.get("supersedes_session_id") for x in existing} for r in existing):
        raise ValueError("同市場同日期已有有效 session；修訂必須使用 supersedes_session_id")
    for k in ("open_at", "close_at", "available_at", "ingested_at"):
        parse_time(row.get(k), k)
    from staging_write import append_bytes, validate_csv_bytes
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=cols, extrasaction="ignore")
    if not os.path.exists(path) or os.path.getsize(path) == 0: writer.writeheader()
    writer.writerow({k: row.get(k, "") for k in cols})
    append_bytes(path, buffer.getvalue().encode("utf-8"), writer="data_spine.append_market_session",
                 validator=validate_csv_bytes, lock_holder=lock_holder,
                 extra={"session_id": row["session_id"], "market": row.get("market")})


def select_fx(rows, pair, market_close_at, run_cutoff_at, max_age_hours=72):
    close, cutoff = parse_time(market_close_at, "market_close_at"), parse_time(run_cutoff_at, "run_cutoff_at")
    eligible = []
    for r in rows:
        if r.get("fx_pair") != pair: continue
        try:
            fixing, available = parse_time(r["fixing_at"], "fixing_at"), parse_time(r["available_at"], "available_at")
            rate = float(r["rate"])
        except (KeyError, ValueError, TypeError): continue
        if rate > 0 and fixing <= close and available <= cutoff: eligible.append((fixing, rate, r))
    if not eligible: return None, "FX_NOT_AVAILABLE"
    fixing, rate, row = max(eligible, key=lambda x: x[0])
    if (close.astimezone(dt.timezone.utc) - fixing.astimezone(dt.timezone.utc)).total_seconds() > max_age_hours * 3600:
        return None, "FX_STALE_OVER_72H"
    return {"rate": rate, "row": row}, None


def convert_amount(amount, rate):
    return float(amount) * float(rate)


def base_currency_return(local_index_start, local_index_end, fx_start, fx_end):
    return (float(local_index_end) / float(local_index_start)) * (float(fx_end) / float(fx_start)) - 1


def _month_shift(year, month, delta):
    total = year * 12 + month - 1 + delta
    return total // 12, total % 12 + 1


def required_complete_months(as_of, count=60):
    d = _date(as_of) if isinstance(as_of, str) else as_of
    y, m = _month_shift(d.year, d.month, -1)
    out = []
    for delta in range(-(count - 1), 1):
        yy, mm = _month_shift(y, m, delta)
        out.append(f"{yy:04d}-{mm:02d}")
    return out


def valuation_method(security_id, rules):
    mapping = rules["valuation"]["v1_rules"].get("valuation_method_by_security", {})
    return mapping.get(security_id, rules["valuation"]["v1_rules"].get("default_method_code", "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN"))


def calculate_v1(security_id, run_cutoff_at, current_multiple, month_end_history, ttm_eps_quarters, rules,
                 market_timezone="UTC"):
    """只使用 run_cutoff_at 前已可得資料；history 每個完整月份都要有列，無效值用 null。"""
    spec = rules["valuation"]["v1_rules"]
    method = valuation_method(security_id, rules)
    try:
        cutoff = parse_time(run_cutoff_at, "run_cutoff_at")
        as_of_date = cutoff.astimezone(ZoneInfo(market_timezone)).date()
    except (ValueError, TypeError) as e:
        return {"value": None, "status": "DATA_INSUFFICIENT_RUN_CUTOFF", "method": method, "reason": str(e)}
    if not isinstance(current_multiple, (int, float)) or isinstance(current_multiple, bool) or current_multiple <= 0:
        return {"value": None, "status": "NOT_APPLICABLE_CURRENT_MULTIPLE", "method": method}
    if method == "TRAILING_PE_RELATIVE_TO_5Y_MEDIAN":
        valid_eps = []
        for r in ttm_eps_quarters:
            try:
                if parse_time(r["available_at"], "available_at") <= cutoff:
                    valid_eps.append((_date(r["period_end"]), float(r["value"])))
            except (KeyError, ValueError, TypeError): pass
        valid_eps = sorted(dict(valid_eps).items(), reverse=True)[:spec["positive_earnings_quarters_required"]]
        if len(valid_eps) < spec["positive_earnings_quarters_required"] or any(v <= 0 for _, v in valid_eps):
            return {"value": None, "status": "NOT_APPLICABLE_EARNINGS", "method": method}
        for i in range(len(valid_eps) - 1):
            gap = (valid_eps[i][0] - valid_eps[i + 1][0]).days
            if gap < 70 or gap > 110:
                return {"value": None, "status": "NOT_APPLICABLE_EARNINGS", "method": method}
    required = required_complete_months(as_of_date, spec["history_months_required"])
    by_month = {}
    for r in month_end_history:
        try:
            if parse_time(r["available_at"], "available_at") <= cutoff:
                by_month[str(r.get("month_end", ""))[:7]] = r.get("value")
        except (KeyError, ValueError, TypeError):
            continue
    if any(m not in by_month for m in required):
        return {"value": None, "status": "NOT_APPLICABLE_INSUFFICIENT_HISTORY", "method": method}
    values = [float(by_month[m]) for m in required if isinstance(by_month[m], (int, float)) and not isinstance(by_month[m], bool) and by_month[m] > 0]
    if len(values) < spec["min_valid_month_ends"]:
        return {"value": None, "status": "DATA_INSUFFICIENT_VALID_MONTHS", "method": method}
    median = statistics.median(values)
    return {"value": current_multiple / median, "status": "OK", "method": method,
            "current_multiple": current_multiple, "median_60m": median, "valid_months": len(values)}


def _point_in_time_rows(observations, security_id, metric_id, cutoff):
    """回傳截止時間前已可得且能用於 as-of 重播的有效 observation。"""
    candidates = []
    for r in observations:
        if r.get("security_id") != security_id or r.get("metric_id") != metric_id:
            continue
        if r.get("availability_status") not in {"POINT_IN_TIME_KNOWN", "PUBLISHED_TIME_ONLY"}:
            continue
        try:
            available = parse_time(r["available_at"], "available_at")
            ingested = parse_time(r["ingested_at"], "ingested_at")
            period_end = _date(r["period_end"], "period_end")
            value = float(r["value"])
        except (KeyError, ValueError, TypeError):
            continue
        if available <= cutoff and ingested <= cutoff:
            candidates.append(r)
    visible = resolve_visible_observation_versions(observations, candidates, as_of=cutoff)
    return [(_date(r["period_end"], "period_end"), parse_time(r["available_at"], "available_at"),
             float(r["value"]), r) for r in visible]


def calculate_v1_from_observations(security_identifier, run_cutoff_at, observations, rules, registry=None):
    """由 canonical observations 計算 V1；紀錄中的 V1 永遠不是權威來源。"""
    registry = registry or load_registry()
    sec, state = resolve_security(security_identifier, registry)
    if state != "OK":
        return {"value": None, "status": state, "method": None}
    try:
        cutoff = parse_time(run_cutoff_at, "run_cutoff_at")
    except ValueError as e:
        return {"value": None, "status": "DATA_INSUFFICIENT_RUN_CUTOFF", "method": None, "reason": str(e)}
    method = valuation_method(sec["security_id"], rules)
    metric_id = "TRAILING_PB" if method == "TRAILING_PB_RELATIVE_TO_5Y_MEDIAN" else "TRAILING_PE"
    local_cutoff_date = cutoff.astimezone(ZoneInfo(sec["market_timezone"])).date()
    multiples = [x for x in _point_in_time_rows(observations, sec["security_id"], metric_id, cutoff)
                 if x[0] <= local_cutoff_date]
    if not multiples:
        return {"value": None, "status": "DATA_INSUFFICIENT_CANONICAL_OBSERVATIONS", "method": method}
    current = max(multiples, key=lambda x: (x[0], x[1]))[2]
    by_month = {}
    for period_end, available, value, _ in multiples:
        key = period_end.strftime("%Y-%m")
        if key not in by_month or (period_end, available) > by_month[key][0]:
            by_month[key] = ((period_end, available), {"month_end": period_end.isoformat(), "value": value,
                                                       "available_at": available.isoformat()})
    history = [x[1] for x in by_month.values()]
    eps = [{"period_end": p.isoformat(), "value": v, "available_at": a.isoformat()}
           for p, a, v, _ in _point_in_time_rows(observations, sec["security_id"], "TTM_EPS", cutoff)
           if p <= local_cutoff_date]
    return calculate_v1(sec["security_id"], run_cutoff_at, current, history, eps, rules, sec["market_timezone"])


def schema2_run_inputs(run, rules, registry=None, sessions=None, events=None, observations=None, event_ledger=None,
                       config=None, snapshot_root=None, current_inputs=None, rules_archive_path=None):
    """一次載入正本並重算所有 schema 2 衍生輸入；不信任 run 裡的衍生數字。
    各參數為 None 時讀正本檔；S1B 影子預覽以凍結輸入注入，不讀現場檔案。"""
    if run.get("run_schema_version", 1) != 2:
        return []
    registry = registry or load_registry()
    sessions = (load_bound_sessions(run.get("calendar_version_id"), run.get("calendar_sha256"))
                if sessions is None else sessions)
    events = read_jsonl(EVENTS_PATH) if events is None else events
    event_ledger = read_jsonl(EVENT_WRITE_LEDGER_PATH) if event_ledger is None else event_ledger
    observations = read_observations_checked() if observations is None else observations
    config = load_json(CONFIG_PATH) if config is None else config
    before = int(config["market_calendar"]["coverage_sessions_before"])
    after = int(config["market_calendar"]["coverage_sessions_after"])
    outputs = []
    for company in run.get("companies") or []:
        errors = []
        event_result = None
        event_source = rules.get("data_spine", {}).get("derivation_version", "EVENT_DISTANCE_1.2.1")
        sec, state = resolve_security(company.get("ticker"), registry)
        event_distance, event_reason = None, state if state != "OK" else None
        if state == "OK":
            try:
                covered, coverage_reason = calendar_coverage(run.get("analysis_session_id"), sec["market"], sessions, before, after)
                if not covered:
                    event_reason = coverage_reason
                else:
                    statutory = (rules.get("verdict", {}).get("tw_statutory_event_window") or {})
                    if sec.get("market") == "TW" and statutory.get("enabled") is True:
                        event_result = derive_tw_statutory_event_window(
                            company.get("ticker"), run.get("analysis_session_id"), observations, sessions, registry)
                        event_source = statutory.get("policy_id", "G-S3b-TW")
                    else:
                        event_result = derive_event_distance(
                            company.get("ticker"), run.get("analysis_session_id"), company.get("event_ids"),
                            events, sessions, registry, cutoff=run.get("run_cutoff_at"), event_ledger=event_ledger,
                            none_window_sessions=int(rules["verdict"].get("event_none_meaning_days", 20)), coverage_valid_days=7,
                            official_event_coverage_policy=rules["verdict"].get("us_official_earnings_date_coverage"))
                    if event_result["status"] == "KNOWN_UPCOMING":
                        event_distance, event_reason = event_result["distance"], None
                    elif event_result["status"] == "VERIFIED_NONE_IN_WINDOW":
                        event_distance, event_reason = rules["verdict"].get("event_none_value", "無"), None
                    else:
                        event_distance, event_reason = None, event_result.get("reason")
            except ValueError as e:
                event_reason = str(e)
        if event_reason:
            errors.append(f"EVENT_DERIVATION_FAILED:{event_reason}")
        declared_v1 = ((company.get("valuation") or {}).get("V1") or {})
        if declared_v1.get("status") == "PUBLIC_DATA_COMPUTED":
            # Lazy import is required because computed_provenance resolves
            # securities through this module.  PUBLIC_DATA_COMPUTED is never
            # trusted merely because it is present in the run: replay the
            # committed, hash-bound snapshot and use that value as canonical.
            from computed_provenance import verify_computed_field
            ok, reason = verify_computed_field(
                run, rules, company, "V1", declared_v1,
                snapshot_root=snapshot_root, registry=registry, layer="valuation",
                current_inputs=current_inputs, rules_archive_path=rules_archive_path)
            declared_method = declared_v1.get("calculation_method")
            approved_alternatives = set((rules.get("v1_alternative") or {}).get("order") or [])
            v1 = ({"value": declared_v1.get("value"), "status": "OK",
                   "method": (declared_method if declared_method in approved_alternatives
                              else "PUBLIC_DATA_COMPUTED"),
                   "score_cap": (declared_v1.get("score_cap")
                                 if declared_method in approved_alternatives else None),
                   "verification": reason}
                  if ok else
                  {"value": None, "status": reason, "method": "PUBLIC_DATA_COMPUTED"})
        else:
            v1 = calculate_v1_from_observations(
                company.get("ticker"), run.get("run_cutoff_at"), observations, rules, registry)
        if v1.get("status") != "OK":
            errors.append(f"V1_DERIVATION_FAILED:{v1.get('status')}")
        outputs.append({"event_value": event_distance, "event_reason": event_reason,
                        "event_evidence": event_result.get("evidence", []) if event_result else [],
                        "event_coverage_basis": event_result.get("coverage_basis") if event_result else None,
                        "event_source": event_source,
                        "v1": v1, "errors": errors})
    return outputs


def portfolio_gate_status(portfolio):
    state = portfolio.get("portfolio_state")
    if state == "PROVIDED_EMPTY":
        return {"status": "PASS_EMPTY", "note": "使用者明確表示目前空手"}
    if state == "PROVIDED_NONEMPTY":
        return {"status": "NOT_IMPLEMENTED_SCHEMA2", "note": "schema 2 個別持股尚未換算成 ai_share 壓力公式；未執行部位上限檢查"}
    return {"status": "DATA_INSUFFICIENT", "note": "部位資料未提供，無法檢查曝險上限"}


def validate_schema2_run(run, rules=None, registry=None, sessions=None, events=None, observations=None, event_ledger=None,
                         sessions_path=SESSIONS_PATH, config=None, calendar_check=None, snapshot_root=None,
                         current_inputs=None, rules_archive_path=None):
    """calendar_check：選用的 callable(version_id, digest) -> (ok, reason)；凍結輸入時用它代替讀日曆檔。"""
    errors = []
    if run.get("run_schema_version", 1) != 2: return errors
    for k in ("run_batch_id", "analysis_session_id", "run_cutoff_at", "calendar_version_id", "calendar_sha256"):
        if not run.get(k): errors.append(f"schema 2 缺少 {k}")
    try: parse_time(run.get("run_cutoff_at"), "run_cutoff_at")
    except ValueError as e: errors.append(str(e))
    if run.get("calendar_sha256") and run.get("calendar_version_id"):
        if calendar_check is not None:
            ok, reason = calendar_check(run["calendar_version_id"], run["calendar_sha256"])
        else:
            ledger = CALENDAR_VERSIONS_PATH if sessions_path == SESSIONS_PATH else sessions_path + ".versions.jsonl"
            ok, reason = verify_calendar_binding(run["calendar_version_id"], run["calendar_sha256"], sessions_path, ledger)
        if not ok: errors.append(f"日曆版本綁定失敗：{reason}")
    registry = registry or load_registry()
    if rules is None:
        from common import load_rules
        rules = load_rules(run.get("protocol_version"))
    try:
        derived = schema2_run_inputs(
            run, rules, registry, sessions, events, observations, event_ledger, config,
            snapshot_root=snapshot_root, current_inputs=current_inputs,
            rules_archive_path=rules_archive_path)
    except Exception as e:
        derived = [{"errors": [f"CANONICAL_INPUT_ERROR:{e}"], "event_value": None,
                    "v1": {"value": None, "status": "DATA_INSUFFICIENT"}}
                   for _ in (run.get("companies") or [])]
    company_markets = set()
    for i, company in enumerate(run.get("companies") or []):
        sec, state = resolve_security(company.get("ticker"), registry)
        if state != "OK": errors.append(f"companies[{i}]：{state}")
        elif sec.get("market"):
            company_markets.add(sec["market"])
        for k in ("event_ids",):
            if k not in company: errors.append(f"companies[{i}] schema 2 缺少 {k}")
        if "event_within_days" in company: errors.append(f"companies[{i}] schema 2 不可人工填 event_within_days")
        d = derived[i] if i < len(derived) else {"errors": ["DERIVATION_OUTPUT_MISSING"], "event_value": None, "v1": {"value": None}}
        errors.extend(f"companies[{i}]：{e}" for e in d["errors"])
        declared_event = company.get("derived_event_within_trading_sessions")
        if declared_event is not None and declared_event != d["event_value"]:
            errors.append(f"companies[{i}]：DERIVED_EVENT_MISMATCH:{declared_event}!={d['event_value']}")
        declared_v1 = (((company.get("valuation") or {}).get("V1") or {}).get("value"))
        canonical_v1 = d["v1"].get("value")
        if declared_v1 is not None and (canonical_v1 is None or abs(float(declared_v1) - float(canonical_v1)) > 1e-9):
            errors.append(f"companies[{i}]：DERIVED_V1_MISMATCH:{declared_v1}!={canonical_v1}")
    if len(company_markets) > 1:
        errors.append("MIXED_MARKET_RUN:" + ",".join(sorted(company_markets)))
    return errors


if __name__ == "__main__":
    import sys
    from common import out
    if len(sys.argv) == 2 and sys.argv[1] == "--calendar-hash":
        out(session_digest())
    elif len(sys.argv) == 2 and sys.argv[1] == "--portfolio-status":
        out(json.dumps(load_portfolio(), ensure_ascii=False, indent=2))
    else:
        out("用法：python 03_工具/data_spine.py --calendar-hash | --portfolio-status")
