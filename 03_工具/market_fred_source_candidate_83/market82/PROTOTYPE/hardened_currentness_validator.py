#!/usr/bin/env python3
"""Source-semantics-hardened, fail-closed M1-M7 validator candidate.

The validator reads actual evidence bytes from an explicit evidence root.  It
does not perform network access or write production state.  Formal authority is
read only from pinned registries outside the data receipt; receipt governance
claims are ignored.  This candidate is incapable of returning formal publish
eligibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


SOURCE_POLICY_REGISTRY_SHA256 = "7aa64ca5dcac32a09dfccbc054a20afd780e619e613f563ed960490fe674d006"
AUTHORITY_REGISTRY_SHA256 = "1eb78a0b778a2206699901803f48ea84c05fb206a31e9b554a333d67cb6a1551"
SOURCE_ADAPTER_REGISTRY_SHA256 = "48bd6ad4a68297b9a02db40fdd601b6ef307f5e405cd6cdbe7349482571a736a"
INDICATOR_IDS = tuple(f"M{i}" for i in range(1, 8))
SESSION_IDS = {"M2", "M3", "M6", "M7"}
MONTHLY_IDS = {"M4", "M5"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_T46_TOKENS = ("t46", "docs_ledger", "document_ledger", "ai_extractions", "ai_field_values")
SEMANTIC_FIELDS = ("observation_key", "observation_date", "calendar_key", "value", "unit", "source_id", "vintage_policy")
EXPECTED_SOURCE_SEMANTICS = {
    "FED_FOMC": ("DECISION_ENUM", "N/A"),
    "FRED_DGS10": ("PERCENT", "N/A"),
    "FRED_BRENT": ("USD_PER_BARREL", "N/A"),
    "FRED_UNRATE_REVISED": ("PERCENT", "REVISED"),
    "FRED_CPILFESL_REVISED": ("INDEX_1982_1984_100", "REVISED"),
    "TIINGO_SOXX": ("USD_INDEX_LEVEL", "N/A"),
    "FRED_HY_OAS": ("PERCENTAGE_POINTS", "N/A"),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def _error(errors: list[dict[str, str]], code: str, path: str, detail: str = "") -> dict[str, str]:
    item = {"code": code, "path": path}
    if detail:
        item["detail"] = detail
    errors.append(item)
    return item


def _timestamp(value: Any, path: str, errors: list[dict[str, str]]) -> datetime | None:
    if not isinstance(value, str) or not value:
        _error(errors, "TIMESTAMP_MISSING", path)
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _error(errors, "TIMESTAMP_INVALID", path)
        return None
    if parsed.tzinfo is None:
        _error(errors, "TIMESTAMP_TIMEZONE_REQUIRED", path)
        return None
    return parsed


def _month_shift(key: str, delta: int) -> str | None:
    try:
        year, month = (int(part) for part in key.split("-"))
        if not 1 <= month <= 12:
            return None
    except (AttributeError, ValueError):
        return None
    serial = year * 12 + month - 1 + delta
    shifted_year, shifted_month0 = divmod(serial, 12)
    return f"{shifted_year:04d}-{shifted_month0 + 1:02d}"


def required_months(indicator_id: str, latest: str) -> dict[str, str]:
    if indicator_id == "M4":
        return {"L": latest, "L_MINUS_3": _month_shift(latest, -3) or "INVALID"}
    return {
        "L": latest,
        "L_MINUS_12": _month_shift(latest, -12) or "INVALID",
        "L_MINUS_3": _month_shift(latest, -3) or "INVALID",
        "L_MINUS_15": _month_shift(latest, -15) or "INVALID",
    }


class EvidenceLoader:
    def __init__(self, root: Path, errors: list[dict[str, str]]):
        self.root = root.resolve()
        self.errors = errors

    def read(self, ref: Any, path: str) -> tuple[bytes | None, Path | None]:
        if not isinstance(ref, dict):
            _error(self.errors, "ARTIFACT_REF_INVALID", path)
            return None, None
        relative = ref.get("path")
        expected_sha = ref.get("sha256")
        expected_bytes = ref.get("bytes")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            _error(self.errors, "ARTIFACT_PATH_INVALID", f"{path}.path", str(relative))
            return None, None
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            _error(self.errors, "ARTIFACT_PATH_ESCAPE", f"{path}.path", relative)
            return None, None
        if not candidate.is_file() or candidate.is_symlink():
            _error(self.errors, "ARTIFACT_FILE_MISSING_OR_UNSAFE", path, relative)
            return None, candidate
        data = candidate.read_bytes()
        actual_sha = sha256_bytes(data)
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            _error(self.errors, "ARTIFACT_SHA256_INVALID", f"{path}.sha256")
        elif actual_sha != expected_sha:
            _error(self.errors, "ARTIFACT_BYTE_HASH_MISMATCH", path, f"expected={expected_sha}; actual={actual_sha}")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            _error(self.errors, "ARTIFACT_BYTE_COUNT_INVALID", f"{path}.bytes")
        elif len(data) != expected_bytes:
            _error(self.errors, "ARTIFACT_BYTE_COUNT_MISMATCH", path, f"expected={expected_bytes}; actual={len(data)}")
        return data, candidate

    def read_json(self, ref: Any, path: str) -> tuple[dict[str, Any] | None, bytes | None]:
        data, _ = self.read(ref, path)
        if data is None:
            return None, None
        try:
            decoded = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _error(self.errors, "ARTIFACT_JSON_INVALID", path, str(exc))
            return None, data
        if not isinstance(decoded, dict):
            _error(self.errors, "ARTIFACT_JSON_OBJECT_REQUIRED", path)
            return None, data
        return decoded, data


def _load_pinned_registry(filename: str, expected_sha: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "CONTRACTS" / filename
    try:
        data = path.read_bytes()
    except OSError as exc:
        _error(errors, "PINNED_REGISTRY_MISSING", f"contracts.{filename}", str(exc))
        return {}
    actual = sha256_bytes(data)
    if actual != expected_sha:
        _error(errors, "PINNED_REGISTRY_HASH_MISMATCH", f"contracts.{filename}", f"expected={expected_sha}; actual={actual}")
        return {}
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _error(errors, "PINNED_REGISTRY_JSON_INVALID", f"contracts.{filename}", str(exc))
        return {}
    return value if isinstance(value, dict) else {}


def _parse_synthetic_source_payload(
    payload: dict[str, Any], source_id: str, path: str, errors: list[dict[str, str]]
) -> list[dict[str, Any]] | None:
    if payload.get("adapter_id") != "SYNTHETIC_MARKET_RECORDS_V1" or payload.get("schema_version") != "1.0.0":
        _error(errors, "SOURCE_ADAPTER_UNSUPPORTED", path, str(payload.get("adapter_id")))
        return None
    if payload.get("synthetic") is not True or payload.get("source_id") != source_id:
        _error(errors, "SOURCE_PAYLOAD_IDENTITY_INVALID", path, source_id)
        return None
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        _error(errors, "SOURCE_RECORDS_MISSING", f"{path}.records")
        return None
    expected_unit, expected_vintage = EXPECTED_SOURCE_SEMANTICS.get(source_id, (None, None))
    if expected_unit is None:
        _error(errors, "SOURCE_SEMANTICS_POLICY_MISSING", path, source_id)
        return None
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = set(SEMANTIC_FIELDS)
    for index, raw in enumerate(records):
        rpath = f"{path}.records[{index}]"
        if not isinstance(raw, dict) or set(raw) != required:
            _error(errors, "SOURCE_RECORD_SCHEMA_MISMATCH", rpath, f"required={sorted(required)}")
            continue
        key = raw.get("observation_key")
        if not isinstance(key, str) or not key:
            _error(errors, "SOURCE_RECORD_KEY_INVALID", rpath)
            continue
        if key in seen:
            _error(errors, "SOURCE_RECORD_DUPLICATE_KEY", rpath, key)
        seen.add(key)
        expected_date = key if len(key) == 10 else f"{key}-01" if len(key) == 7 else None
        if expected_date is None or raw.get("observation_date") != expected_date or raw.get("calendar_key") != key[:7]:
            _error(errors, "SOURCE_RECORD_DATE_KEY_MISMATCH", rpath, key)
        else:
            try:
                date.fromisoformat(expected_date)
            except ValueError:
                _error(errors, "SOURCE_RECORD_DATE_INVALID", rpath, expected_date)
        if raw.get("source_id") != source_id:
            _error(errors, "SOURCE_RECORD_SOURCE_MISMATCH", rpath, str(raw.get("source_id")))
        if raw.get("unit") != expected_unit:
            _error(errors, "SOURCE_RECORD_UNIT_MISMATCH", rpath, str(raw.get("unit")))
        if raw.get("vintage_policy") != expected_vintage:
            _error(errors, "SOURCE_RECORD_VINTAGE_MISMATCH", rpath, str(raw.get("vintage_policy")))
        value = raw.get("value")
        value_valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if source_id == "FED_FOMC":
            value_valid = value in {"升息", "降息", "不變"}
        if not value_valid:
            _error(errors, "SOURCE_RECORD_VALUE_INVALID", rpath, repr(value))
        parsed.append({field: raw[field] for field in SEMANTIC_FIELDS})
    return parsed


def validate_receipt(receipt: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    indicator_errors: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_policy = _load_pinned_registry("source_policy_registry.json", SOURCE_POLICY_REGISTRY_SHA256, errors)
    authority = _load_pinned_registry("authority_registry.json", AUTHORITY_REGISTRY_SHA256, errors)
    adapter_registry = _load_pinned_registry("source_adapter_registry.json", SOURCE_ADAPTER_REGISTRY_SHA256, errors)
    loader = EvidenceLoader(evidence_root, errors)

    required_top = {"schema_version", "receipt_id", "receipt_kind", "synthetic", "run_cutoff", "identity", "scope", "source_receipt_refs", "indicators"}
    for key in sorted(required_top - set(receipt)):
        _error(errors, "SCHEMA_FIELD_MISSING", key)
    if receipt.get("schema_version") != "MARKET_SOURCE_SEMANTICS_3.0.0":
        _error(errors, "SCHEMA_VERSION_UNSUPPORTED", "schema_version")
    if receipt.get("receipt_kind") != "MARKET_ONLY":
        _error(errors, "RECEIPT_KIND_NOT_MARKET_ONLY", "receipt_kind")
    cutoff = _timestamp(receipt.get("run_cutoff"), "run_cutoff", errors)

    scope = receipt.get("scope") if isinstance(receipt.get("scope"), dict) else {}
    if scope.get("t46_document_ledger_required") is not False:
        _error(errors, "T46_DEPENDENCY_FORBIDDEN", "scope.t46_document_ledger_required")
    scope_text = json.dumps(scope.get("dependency_domains", []), ensure_ascii=False).lower()
    if any(token in scope_text for token in FORBIDDEN_T46_TOKENS):
        _error(errors, "T46_DEPENDENCY_FORBIDDEN", "scope")

    if "governance" in receipt or "formal_publish_status" in receipt:
        warnings.append({"code": "RECEIPT_GOVERNANCE_CLAIMS_IGNORED", "path": "governance"})

    source_records: dict[str, dict[str, Any]] = {}
    source_bytes_sha: dict[str, str] = {}
    source_receipt_ids: dict[str, str] = {}
    source_semantic_rows: dict[str, list[dict[str, Any]]] = {}
    for index, ref in enumerate(receipt.get("source_receipt_refs", []) if isinstance(receipt.get("source_receipt_refs"), list) else []):
        path = f"source_receipt_refs[{index}]"
        record, _ = loader.read_json(ref, path)
        if not record:
            continue
        source_id = record.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            _error(errors, "SOURCE_ID_MISSING", f"{path}.artifact.source_id")
            continue
        if ref.get("source_id") != source_id:
            _error(errors, "SOURCE_REF_ID_MISMATCH", path, f"ref={ref.get('source_id')}; artifact={source_id}")
        if source_id in source_records:
            _error(errors, "DUPLICATE_SOURCE_ID", path, source_id)
        source_records[source_id] = record
        source_receipt_ids[source_id] = str(record.get("receipt_id"))
        counts = (record.get("requested_items"), record.get("succeeded_items"), record.get("failed_items"))
        if not all(isinstance(value, int) and value >= 0 for value in counts):
            _error(errors, "SOURCE_COUNTS_INVALID", path)
        elif counts[0] != counts[1] + counts[2]:
            _error(errors, "SOURCE_COUNTS_DONT_BALANCE", path)
        if isinstance(counts[2], int) and counts[2] > 0:
            _error(errors, "PARTIAL_SOURCE_FAILURE", path, source_id)
        published = _timestamp(record.get("source_published_at"), f"{path}.artifact.source_published_at", errors)
        available = _timestamp(record.get("available_at"), f"{path}.artifact.available_at", errors)
        fetched = _timestamp(record.get("fetched_at"), f"{path}.artifact.fetched_at", errors)
        if published and available and published > available:
            _error(errors, "SOURCE_RECEIPT_TIMESTAMP_ORDER_INVALID", path, "source_published_at > available_at")
        if available and fetched and available > fetched:
            _error(errors, "SOURCE_RECEIPT_TIMESTAMP_ORDER_INVALID", path, "available_at > fetched_at")
        if cutoff and any(value and value > cutoff for value in (published, available, fetched)):
            _error(errors, "SOURCE_TIME_AFTER_CUTOFF", path, source_id)
        response, response_raw = loader.read_json(record.get("response_artifact"), f"{path}.artifact.response_artifact")
        if response and response.get("source_id") != source_id:
            _error(errors, "SOURCE_PAYLOAD_ID_MISMATCH", path, source_id)
        if response_raw is not None:
            source_bytes_sha[source_id] = sha256_bytes(response_raw)
        adapter_id = record.get("adapter_id")
        adapter_contract = adapter_registry.get("adapters", {}).get(adapter_id) if isinstance(adapter_registry.get("adapters"), dict) else None
        if not isinstance(adapter_contract, dict) or adapter_contract.get("implementation_status") != "IMPLEMENTED_SYNTHETIC_ONLY":
            _error(errors, "SOURCE_ADAPTER_UNSUPPORTED", f"{path}.artifact.adapter_id", str(adapter_id))
        elif response:
            if response.get("adapter_id") != adapter_id:
                _error(errors, "SOURCE_ADAPTER_CROSS_BINDING_MISMATCH", path, str(adapter_id))
            parsed_rows = _parse_synthetic_source_payload(response, source_id, f"{path}.artifact.response_artifact", errors)
            if parsed_rows is not None:
                source_semantic_rows[source_id] = parsed_rows
                derived_source_rowset_sha = canonical_json_sha256(parsed_rows)
                if record.get("canonical_source_rowset_sha256") != derived_source_rowset_sha:
                    _error(errors, "CANONICAL_SOURCE_ROWSET_HASH_MISMATCH", path, f"derived={derived_source_rowset_sha}")
        policy = source_policy.get("policies", {}).get(source_id) if isinstance(source_policy.get("policies"), dict) else None
        if not isinstance(policy, dict) or policy.get("synthetic_validation_allowed") is not True:
            _error(errors, "SOURCE_POLICY_NOT_APPROVED", path, source_id)

    indicators_raw = receipt.get("indicators") if isinstance(receipt.get("indicators"), list) else []
    indicators: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(indicators_raw):
        path = f"indicators[{index}]"
        if not isinstance(item, dict) or item.get("indicator_id") not in INDICATOR_IDS:
            _error(errors, "INDICATOR_INVALID", path)
            continue
        indicator_id = item["indicator_id"]
        if indicator_id in indicators:
            _error(errors, "DUPLICATE_INDICATOR", path, indicator_id)
        indicators[indicator_id] = item

        def ierr(code: str, subpath: str, detail: str = "") -> None:
            error = _error(errors, code, f"{path}.{subpath}", detail)
            indicator_errors[indicator_id].append(error)

        source_id = item.get("source_id")
        source_record = source_records.get(str(source_id))
        if source_record is None:
            ierr("INDICATOR_SOURCE_NOT_FOUND", "source_id", str(source_id))
        policy = source_policy.get("policies", {}).get(source_id, {}) if isinstance(source_policy.get("policies"), dict) else {}
        if indicator_id not in policy.get("indicator_ids", []):
            ierr("SOURCE_POLICY_INDICATOR_MISMATCH", "source_id", str(source_id))

        schedule, _ = loader.read_json(item.get("schedule_receipt_ref"), f"{path}.schedule_receipt_ref")
        if not schedule:
            schedule = {}
        if schedule.get("status") != "COMPLETE":
            ierr("SCHEDULE_RECEIPT_INCOMPLETE", "schedule_receipt_ref")
        if schedule.get("indicator_id") != indicator_id:
            ierr("SCHEDULE_INDICATOR_MISMATCH", "schedule_receipt_ref", str(schedule.get("indicator_id")))
        if schedule.get("source_id") != source_id:
            ierr("SCHEDULE_SOURCE_MISMATCH", "schedule_receipt_ref", str(schedule.get("source_id")))
        if schedule.get("calendar_id") != policy.get("calendar_id"):
            ierr("SCHEDULE_CALENDAR_POLICY_MISMATCH", "schedule_receipt_ref", str(schedule.get("calendar_id")))
        schedule_payload, schedule_raw = loader.read_json(schedule.get("calendar_artifact"), f"{path}.schedule_receipt_ref.calendar_artifact")
        if schedule_payload:
            if schedule_payload.get("calendar_id") != schedule.get("calendar_id"):
                ierr("SCHEDULE_PAYLOAD_CALENDAR_MISMATCH", "schedule_receipt_ref.calendar_artifact")
            if schedule_payload.get("latest_expected_period") != schedule.get("latest_expected_period"):
                ierr("SCHEDULE_PAYLOAD_PERIOD_MISMATCH", "schedule_receipt_ref.calendar_artifact")
        schedule_bytes_sha = sha256_bytes(schedule_raw) if schedule_raw is not None else None
        expected = schedule.get("latest_expected_period")
        if item.get("latest_expected_period") != expected:
            ierr("EXPECTED_PERIOD_CROSS_BINDING_MISMATCH", "latest_expected_period")
        expected_published = _timestamp(schedule.get("latest_expected_published_at"), f"{path}.schedule_receipt.latest_expected_published_at", errors)
        if cutoff and expected_published and expected_published > cutoff:
            ierr("EXPECTED_PUBLICATION_AFTER_CUTOFF", "schedule_receipt_ref")

        observations = item.get("observations") if isinstance(item.get("observations"), list) else []
        if not observations:
            ierr("REQUIRED_OBSERVATION_MISSING", "observations", str(expected))
        duplicate_keys: list[tuple[Any, Any]] = []
        latest_matches = []
        values_by_month: dict[str, list[Any]] = defaultdict(list)
        policies: set[str] = set()
        for observation_index, observation in enumerate(observations):
            opath = f"observations[{observation_index}]"
            if not isinstance(observation, dict):
                ierr("OBSERVATION_INVALID", opath)
                continue
            key = observation.get("observation_key")
            vintage = observation.get("vintage_policy")
            duplicate_keys.append((key, vintage))
            if key == expected:
                latest_matches.append(observation)
            if observation.get("source_id") != source_id:
                ierr("OBSERVATION_SOURCE_MISMATCH", opath, str(observation.get("source_id")))
            if source_record and observation.get("source_receipt_id") != source_record.get("receipt_id"):
                ierr("OBSERVATION_SOURCE_RECEIPT_MISMATCH", opath)
            if observation.get("schedule_receipt_id") != schedule.get("receipt_id"):
                ierr("OBSERVATION_SCHEDULE_RECEIPT_MISMATCH", opath)
            if observation.get("source_payload_sha256") != source_bytes_sha.get(str(source_id)):
                ierr("OBSERVATION_SOURCE_BYTES_MISMATCH", opath)
            if observation.get("schedule_payload_sha256") != schedule_bytes_sha:
                ierr("OBSERVATION_SCHEDULE_BYTES_MISMATCH", opath)
            published = _timestamp(observation.get("source_published_at"), f"{path}.{opath}.source_published_at", errors)
            available = _timestamp(observation.get("available_at"), f"{path}.{opath}.available_at", errors)
            fetched = _timestamp(observation.get("fetched_at"), f"{path}.{opath}.fetched_at", errors)
            if published and available and published > available:
                ierr("OBSERVATION_TIMESTAMP_ORDER_INVALID", opath, "source_published_at > available_at")
            if available and fetched and available > fetched:
                ierr("OBSERVATION_TIMESTAMP_ORDER_INVALID", opath, "available_at > fetched_at")
            if cutoff and any(value and value > cutoff for value in (published, available, fetched)):
                ierr("OBSERVATION_TIME_AFTER_CUTOFF", opath)
            if indicator_id in MONTHLY_IDS:
                month = str(observation.get("calendar_key"))
                values_by_month[month].append(observation.get("value"))
                if isinstance(vintage, str):
                    policies.add(vintage)
        for duplicate, count in Counter(duplicate_keys).items():
            if count > 1:
                ierr("DUPLICATE_OBSERVATION", "observations", repr(duplicate))
        expected_semantic_rows = source_semantic_rows.get(str(source_id))
        if expected_semantic_rows is None:
            ierr("SOURCE_SEMANTIC_ROWSET_UNAVAILABLE", "observations", str(source_id))
        else:
            expected_by_key = {row["observation_key"]: row for row in expected_semantic_rows}
            actual_by_key: dict[str, dict[str, Any]] = {}
            for observation_index, observation in enumerate(observations):
                if not isinstance(observation, dict):
                    continue
                key = observation.get("observation_key")
                if not isinstance(key, str):
                    continue
                actual_by_key[key] = {field: observation.get(field) for field in SEMANTIC_FIELDS}
            if len(observations) != len(expected_semantic_rows):
                ierr("OBSERVATION_SOURCE_ROWSET_COUNT_MISMATCH", "observations", f"source={len(expected_semantic_rows)}; receipt={len(observations)}")
            for key in sorted(set(expected_by_key) - set(actual_by_key)):
                ierr("OBSERVATION_MISSING_FOR_SOURCE_RECORD", "observations", key)
            for key in sorted(set(actual_by_key) - set(expected_by_key)):
                ierr("SOURCE_RECORD_MISSING_FOR_OBSERVATION", "observations", key)
            for key in sorted(set(expected_by_key) & set(actual_by_key)):
                expected_row = expected_by_key[key]
                actual_row = actual_by_key[key]
                for field in SEMANTIC_FIELDS:
                    if actual_row[field] != expected_row[field]:
                        ierr("OBSERVATION_SOURCE_SEMANTIC_MISMATCH", f"observations[{key}].{field}", f"source={expected_row[field]!r}; receipt={actual_row[field]!r}")
        if len(latest_matches) != 1:
            ierr("EXPECTED_PERIOD_OBSERVATION_COUNT_INVALID", "observations", f"expected=1; actual={len(latest_matches)}; period={expected}")
        elif source_record:
            latest = latest_matches[0]
            for key in ("source_published_at", "available_at", "fetched_at"):
                if latest.get(key) != source_record.get(key):
                    ierr("OBSERVATION_SOURCE_TIME_CROSS_BINDING_MISMATCH", f"observations.{key}")
            if latest.get("source_published_at") != schedule.get("latest_expected_published_at"):
                ierr("OBSERVATION_SCHEDULE_TIME_CROSS_BINDING_MISMATCH", "observations.source_published_at")
        derived_observed = max((str(row.get("observation_key")) for row in observations if isinstance(row, dict)), default=None)
        if item.get("latest_observed_period") != derived_observed:
            ierr("LATEST_OBSERVED_PERIOD_NOT_DERIVED", "latest_observed_period", f"claimed={item.get('latest_observed_period')}; derived={derived_observed}")
        if indicator_id in SESSION_IDS and derived_observed != expected:
            ierr("MISSING_EXPECTED_TRADING_OR_PROVIDER_PERIOD", "observations", f"expected={expected}; derived={derived_observed}")
        if item.get("missing_expected_periods"):
            ierr("MISSING_EXPECTED_PERIODS", "missing_expected_periods")

        if indicator_id == "M1":
            if schedule.get("calendar_id") != "FED_FOMC_MEETING_CALENDAR":
                ierr("M1_EVENT_CALENDAR_MISSING", "schedule_receipt_ref")
            if item.get("publication_receipt_present") is not True:
                ierr("M1_PUBLICATION_RECEIPT_MISSING", "publication_receipt_present")
        if indicator_id in MONTHLY_IDS:
            required = required_months(indicator_id, str(expected))
            if item.get("required_calendar_keys") != required:
                ierr("CALENDAR_KEY_DECLARATION_MISMATCH", "required_calendar_keys")
            missing = sorted(set(required.values()) - set(values_by_month))
            conflicts = sorted(key for key, values in values_by_month.items() if len(set(map(repr, values))) > 1)
            if missing:
                ierr("EXACT_CALENDAR_KEY_MISSING", "observations", ",".join(missing))
            if conflicts:
                ierr("EXACT_CALENDAR_KEY_CONFLICT", "observations", ",".join(conflicts))
            declared_policy = item.get("vintage_policy")
            if len(policies) != 1 or declared_policy not in policies:
                ierr("VINTAGE_POLICY_MIXED", "vintage_policy", ",".join(sorted(policies)))
            if declared_policy not in policy.get("allowed_vintage_policies", []):
                ierr("VINTAGE_POLICY_NOT_APPROVED_BY_SOURCE_POLICY", "vintage_policy", str(declared_policy))
        if indicator_id == "M6":
            anchors = item.get("price_anchors") if isinstance(item.get("price_anchors"), dict) else {}
            anchor_values = [anchors.get(key) for key in ("latest_complete_market_session", "price_anchor", "corporate_action_anchor", "total_return_anchor")]
            if any(value != expected for value in anchor_values):
                ierr("PRICE_ANCHOR_MISMATCH", "price_anchors", repr(anchor_values))

        if source_record:
            expected_rowset = source_record.get("rowset_sha256")
            actual_rowset = canonical_json_sha256(observations)
            if expected_rowset != actual_rowset:
                ierr("OBSERVATION_ROWSET_HASH_MISMATCH", "observations", f"expected={expected_rowset}; actual={actual_rowset}")

    for missing_id in sorted(set(INDICATOR_IDS) - set(indicators)):
        item = _error(errors, "INDICATOR_MISSING", "indicators", missing_id)
        indicator_errors[missing_id].append(item)

    data_errors = [error for error in errors if error["path"] != "contracts.authority_registry.json"]
    data_current = not data_errors
    authorities = authority.get("authorities", {}) if isinstance(authority.get("authorities"), dict) else {}
    code_integrated = authorities.get("code_integration", {}).get("status") == "APPROVED"
    pit_approved = authorities.get("pit_vintage_policy", {}).get("status") == "APPROVED"
    source_formal = authorities.get("source_policy", {}).get("status") == "APPROVED_FORMAL"
    all_authorities = all((
        code_integrated,
        pit_approved,
        source_formal,
        authorities.get("formal_market_data_write", {}).get("status") == "AUTHORIZED",
        authorities.get("formal_snapshot", {}).get("status") == "AUTHORIZED",
        authorities.get("formal_publication", {}).get("status") == "AUTHORIZED",
    ))
    synthetic = receipt.get("synthetic") is True
    candidate_can_publish = False
    formal_eligible = data_current and all_authorities and not synthetic and candidate_can_publish

    indicator_results = []
    for indicator_id in INDICATOR_IDS:
        codes = sorted({item["code"] for item in indicator_errors.get(indicator_id, [])})
        indicator_results.append({"indicator_id": indicator_id, "status": "CURRENT" if not codes else "DATA_INSUFFICIENT", "failure_codes": codes})

    return {
        "receipt_id": receipt.get("receipt_id", "UNKNOWN"),
        "validator_status": "PASS" if not errors else "FAIL_CLOSED",
        "data_currentness_status": "DATA_CURRENT" if data_current else "DATA_INSUFFICIENT",
        "source_provenance_status": "SYNTHETIC_SOURCE_SEMANTICS_VERIFIED" if data_current and synthetic else "NOT_VERIFIED",
        "code_integration_status": "CODE_INTEGRATED" if code_integrated else "CODE_NOT_INTEGRATED",
        "pit_vintage_status": "PIT_APPROVED" if pit_approved else "PIT_NOT_APPROVED",
        "formal_publish_status": "FORMAL_PUBLISH_ELIGIBLE" if formal_eligible else "FORMAL_PUBLISH_NOT_ELIGIBLE",
        "candidate_formal_publish_capability": "DISABLED",
        "authority_registry_id": authority.get("registry_id", "UNKNOWN"),
        "source_policy_registry_id": source_policy.get("registry_id", "UNKNOWN"),
        "indicator_results": indicator_results,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "gates": {
            "actual_source_bytes_gate": "PASS" if not any(e["code"].startswith("ARTIFACT_") or e["code"] in {"SOURCE_PAYLOAD_ID_MISMATCH", "OBSERVATION_SOURCE_BYTES_MISMATCH"} for e in errors) else "FAIL",
            "observation_presence_gate": "PASS" if not any(e["code"] in {"REQUIRED_OBSERVATION_MISSING", "EXPECTED_PERIOD_OBSERVATION_COUNT_INVALID"} for e in errors) else "FAIL",
            "observation_timestamp_chain_gate": "PASS" if not any(e["code"] in {"OBSERVATION_TIMESTAMP_ORDER_INVALID", "OBSERVATION_TIME_AFTER_CUTOFF", "OBSERVATION_SOURCE_TIME_CROSS_BINDING_MISMATCH", "OBSERVATION_SCHEDULE_TIME_CROSS_BINDING_MISMATCH"} for e in errors) else "FAIL",
            "rowset_binding_gate": "PASS" if not any(e["code"] == "OBSERVATION_ROWSET_HASH_MISMATCH" for e in errors) else "FAIL",
            "source_semantic_binding_gate": "PASS" if not any(e["code"].startswith("SOURCE_RECORD_") or e["code"].startswith("OBSERVATION_SOURCE_SEMANTIC") or e["code"] in {"SOURCE_SEMANTIC_ROWSET_UNAVAILABLE", "OBSERVATION_SOURCE_ROWSET_COUNT_MISMATCH", "OBSERVATION_MISSING_FOR_SOURCE_RECORD", "SOURCE_RECORD_MISSING_FOR_OBSERVATION", "CANONICAL_SOURCE_ROWSET_HASH_MISMATCH", "SOURCE_ADAPTER_UNSUPPORTED", "SOURCE_ADAPTER_CROSS_BINDING_MISMATCH"} for e in errors) else "FAIL",
            "schedule_binding_gate": "PASS" if not any("SCHEDULE" in e["code"] and e["code"] != "OBSERVATION_SCHEDULE_TIME_CROSS_BINDING_MISMATCH" for e in errors) else "FAIL",
            "exact_calendar_key_gate": "PASS" if not any(e["code"].startswith("EXACT_CALENDAR_KEY") for e in errors) else "FAIL",
            "t46_independence_gate": "PASS" if not any(e["code"] == "T46_DEPENDENCY_FORBIDDEN" for e in errors) else "FAIL",
            "external_authority_gate": "PASS" if all_authorities else "FAIL",
            "formal_publish_gate": "PASS" if formal_eligible else "FAIL",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be an object")
        result = validate_receipt(receipt, args.evidence_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "receipt_id": "UNKNOWN",
            "validator_status": "FAIL_CLOSED",
            "data_currentness_status": "DATA_INSUFFICIENT",
            "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
            "candidate_formal_publish_capability": "DISABLED",
            "error_count": 1,
            "errors": [{"code": "INPUT_READ_ERROR", "path": str(args.input), "detail": str(exc)}],
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if result.get("validator_status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
