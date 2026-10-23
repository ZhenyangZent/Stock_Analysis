#!/usr/bin/env python3
"""Read-only real-provider-format adapter candidates.

No network or production writes are performed. Provider-format semantics and
currentness eligibility are deliberately separate: a payload may parse while
remaining ineligible because publication/availability/fetch evidence is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


REGISTRY_SHA256 = "48bd6ad4a68297b9a02db40fdd601b6ef307f5e405cd6cdbe7349482571a736a"


class AdapterError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def load_registry() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "CONTRACTS" / "source_adapter_registry.json"
    raw = path.read_bytes()
    actual = sha256_bytes(raw)
    if actual != REGISTRY_SHA256:
        raise AdapterError("PINNED_ADAPTER_REGISTRY_HASH_MISMATCH", f"expected={REGISTRY_SHA256}; actual={actual}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise AdapterError("ADAPTER_REGISTRY_INVALID")
    return value


def parse_json_object(raw: bytes, artifact: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AdapterError("PROVIDER_JSON_INVALID", f"{artifact}: {exc}") from None
    if not isinstance(value, dict):
        raise AdapterError("PROVIDER_JSON_OBJECT_REQUIRED", artifact)
    return value


def valid_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AdapterError("PROVIDER_DATE_INVALID", field)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise AdapterError("PROVIDER_DATE_INVALID", f"{field}={value}") from None
    return value


def valid_fred_last_updated(value: Any) -> str:
    """Validate FRED's documented timestamp, including its legacy ``-05`` offset."""
    if not isinstance(value, str):
        raise AdapterError("FRED_LAST_UPDATED_INVALID", str(value))
    normalized = re.sub(r"([+-]\d{2})$", r"\1:00", value)
    try:
        datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S%z")
    except ValueError:
        raise AdapterError("FRED_LAST_UPDATED_INVALID", value) from None
    return value


REQUEST_PARAMETER_FIELDS = (
    "series_id",
    "file_type",
    "output_type",
    "units",
    "order_by",
    "sort_order",
    "realtime_start",
    "realtime_end",
    "observation_start",
    "observation_end",
    "limit",
    "offset",
)


def valid_nonnegative_int(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError("FRED_PAGINATION_METADATA_INVALID", f"{field}={value}")
    if value < (1 if positive else 0):
        raise AdapterError("FRED_PAGINATION_METADATA_INVALID", f"{field}={value}")
    return value


def parse_fred_api_json(
    series_raw: bytes,
    observations_raw: bytes,
    contract_name: str,
    request_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = load_registry()
    adapter = registry.get("adapters", {}).get("FRED_API_JSON_V1", {})
    if adapter.get("implementation_status") != "IMPLEMENTED_PROVIDER_FORMAT_CURRENTNESS_BLOCKED":
        raise AdapterError("FRED_ADAPTER_NOT_IMPLEMENTED")
    contract = registry.get("fred_series_contracts", {}).get(contract_name)
    if not isinstance(contract, dict):
        raise AdapterError("FRED_SERIES_CONTRACT_NOT_PINNED", contract_name)
    if contract.get("vintage_policy") != "REVISED_CURRENT":
        raise AdapterError("ALFRED_INITIAL_POLICY_NOT_APPROVED", str(contract.get("vintage_policy")))
    expected_series_sha = contract.get("series_fixture_sha256")
    expected_observations_sha = contract.get("observations_fixture_sha256")
    if expected_series_sha and sha256_bytes(series_raw) != expected_series_sha:
        raise AdapterError("RAW_ARTIFACT_HASH_MISMATCH", "fred_series_json")
    if expected_observations_sha and sha256_bytes(observations_raw) != expected_observations_sha:
        raise AdapterError("RAW_ARTIFACT_HASH_MISMATCH", "fred_series_observations_json")

    series_payload = parse_json_object(series_raw, "fred_series_json")
    series_rows = series_payload.get("seriess")
    if not isinstance(series_rows, list) or len(series_rows) != 1 or not isinstance(series_rows[0], dict):
        raise AdapterError("FRED_SERIES_CARDINALITY_INVALID")
    series = series_rows[0]
    source_id = series.get("id")
    unit = series.get("units")
    frequency = series.get("frequency_short")
    if source_id != contract.get("series_id"):
        raise AdapterError("FRED_SOURCE_ID_MISMATCH", f"raw={source_id}; contract={contract.get('series_id')}")
    if unit != contract.get("units"):
        raise AdapterError("FRED_UNIT_MISMATCH", f"raw={unit}; contract={contract.get('units')}")
    if frequency != contract.get("frequency_short"):
        raise AdapterError("FRED_FREQUENCY_MISMATCH", f"raw={frequency}; contract={contract.get('frequency_short')}")
    valid_date(series.get("realtime_start"), "series.realtime_start")
    valid_date(series.get("realtime_end"), "series.realtime_end")
    valid_fred_last_updated(series.get("last_updated"))

    payload = parse_json_object(observations_raw, "fred_series_observations_json")
    if payload.get("file_type") != "json" or payload.get("output_type") != 1 or payload.get("units") != "lin":
        raise AdapterError("FRED_OBSERVATION_MODE_UNSUPPORTED", f"file_type={payload.get('file_type')}; output_type={payload.get('output_type')}; units={payload.get('units')}")
    if payload.get("order_by") != "observation_date" or payload.get("sort_order") != "asc":
        raise AdapterError("FRED_OBSERVATION_ORDER_UNSUPPORTED", f"order_by={payload.get('order_by')}; sort_order={payload.get('sort_order')}")
    top_realtime_start = valid_date(payload.get("realtime_start"), "observations.realtime_start")
    top_realtime_end = valid_date(payload.get("realtime_end"), "observations.realtime_end")
    observation_start = valid_date(payload.get("observation_start"), "observations.observation_start")
    observation_end = valid_date(payload.get("observation_end"), "observations.observation_end")
    total_count = valid_nonnegative_int(payload.get("count"), "count")
    limit = valid_nonnegative_int(payload.get("limit"), "limit", positive=True)
    offset = valid_nonnegative_int(payload.get("offset"), "offset")
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        raise AdapterError("FRED_OBSERVATIONS_MISSING")
    if offset >= total_count:
        raise AdapterError("FRED_PAGINATION_METADATA_INVALID", f"offset={offset}; count={total_count}")
    expected_page_rows = min(limit, total_count - offset)
    if len(observations) != expected_page_rows:
        raise AdapterError("FRED_PAGE_ROW_COUNT_MISMATCH", f"count={total_count}; limit={limit}; offset={offset}; expected_page_rows={expected_page_rows}; rows={len(observations)}")

    response_parameters = {
        "series_id": source_id,
        "file_type": payload.get("file_type"),
        "output_type": payload.get("output_type"),
        "units": payload.get("units"),
        "order_by": payload.get("order_by"),
        "sort_order": payload.get("sort_order"),
        "realtime_start": top_realtime_start,
        "realtime_end": top_realtime_end,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "limit": limit,
        "offset": offset,
    }
    if request_parameters is None:
        normalized_request = response_parameters
        request_identity_status = "DERIVED_FROM_RESPONSE_NOT_TRUSTED_FETCH_RECEIPT"
    else:
        if not isinstance(request_parameters, dict) or set(request_parameters) != set(REQUEST_PARAMETER_FIELDS):
            raise AdapterError("FRED_REQUEST_PARAMETER_SET_INVALID", ",".join(sorted(request_parameters)) if isinstance(request_parameters, dict) else type(request_parameters).__name__)
        normalized_request = {field: request_parameters[field] for field in REQUEST_PARAMETER_FIELDS}
        if normalized_request != response_parameters:
            raise AdapterError("FRED_REQUEST_RESPONSE_METADATA_MISMATCH", f"request={normalized_request}; response={response_parameters}")
        request_identity_status = "EXPLICIT_PARAMETERS_BOUND_TO_RESPONSE"

    records: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str, str]] = set()
    previous_observation_date: str | None = None
    for index, raw in enumerate(observations):
        if not isinstance(raw, dict):
            raise AdapterError("FRED_OBSERVATION_ROW_INVALID", str(index))
        observed = valid_date(raw.get("date"), f"observations[{index}].date")
        realtime_start = valid_date(raw.get("realtime_start"), f"observations[{index}].realtime_start")
        realtime_end = valid_date(raw.get("realtime_end"), f"observations[{index}].realtime_end")
        if realtime_end < realtime_start:
            raise AdapterError("FRED_REALTIME_RANGE_INVALID", str(index))
        if realtime_start != top_realtime_start or realtime_end != top_realtime_end:
            raise AdapterError("FRED_REVISED_INITIAL_MIX_OR_NONCURRENT_RANGE", str(index))
        raw_value = raw.get("value")
        if raw_value in (None, "", "."):
            raise AdapterError("FRED_MISSING_VALUE", observed)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise AdapterError("FRED_VALUE_INVALID", f"{observed}={raw_value}") from None
        if not math.isfinite(value):
            raise AdapterError("FRED_VALUE_INVALID", f"{observed}={raw_value}")
        identity = (source_id, observed, realtime_start, realtime_end)
        if identity in identities:
            raise AdapterError("FRED_DUPLICATE_RECORD_IDENTITY", repr(identity))
        identities.add(identity)
        if previous_observation_date is not None and observed <= previous_observation_date:
            raise AdapterError("FRED_PAGE_ORDER_INVALID", f"index={index}; previous={previous_observation_date}; current={observed}")
        previous_observation_date = observed
        identity_payload = {"source_id": source_id, "observation_date": observed, "realtime_start": realtime_start, "realtime_end": realtime_end}
        records.append({
            "record_identity_sha256": canonical_sha(identity_payload),
            "source_id": source_id,
            "observation_key": observed,
            "observation_date": observed,
            "calendar_key": observed[:7],
            "value": value,
            "unit": unit,
            "vintage_policy": "REVISED_CURRENT",
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
            "source_published_at": None,
            "available_at": None,
            "fetched_at": None,
        })

    pagination_complete = offset == 0 and len(records) == total_count
    blockers = [
        "OBSERVATION_SOURCE_PUBLISHED_AT_EVIDENCE_MISSING",
        "OBSERVATION_AVAILABLE_AT_EVIDENCE_MISSING",
        "TRUSTED_FETCH_RECEIPT_MISSING",
    ]
    if not pagination_complete:
        blockers.append("FRED_PAGINATION_INCOMPLETE")
    page_receipt = {
        "raw_payload_sha256": sha256_bytes(observations_raw),
        "raw_payload_bytes": len(observations_raw),
        "source_id": source_id,
        "realtime_start": top_realtime_start,
        "realtime_end": top_realtime_end,
        "vintage_policy": "REVISED_CURRENT",
        "request_parameters": normalized_request,
        "request_parameter_identity_sha256": canonical_sha(normalized_request),
        "request_identity_status": request_identity_status,
        "total_count": total_count,
        "page_row_count": len(records),
        "limit": limit,
        "offset": offset,
        "first_observation_key": records[0]["observation_key"],
        "last_observation_key": records[-1]["observation_key"],
    }
    return {
        "adapter_id": "FRED_API_JSON_V1",
        "adapter_status": "PROVIDER_FORMAT_PARSED",
        "provider_format_status": "PROVIDER_FORMAT_VERIFIED",
        "source_semantics_status": "SOURCE_SEMANTICS_VERIFIED",
        "source_publication_available_status": "SOURCE_PUBLICATION_AVAILABLE_NOT_VERIFIED",
        "currentness_status": "DATA_INSUFFICIENT",
        "currentness_eligible": False,
        "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
        "contract_name": contract_name,
        "contract_scope": contract.get("acceptance_scope"),
        "series_payload_sha256": sha256_bytes(series_raw),
        "observations_payload_sha256": sha256_bytes(observations_raw),
        "canonical_source_rowset_sha256": canonical_sha(records),
        "provider_last_updated": series.get("last_updated"),
        "pagination_complete": pagination_complete,
        "total_count": total_count,
        "page_row_count": len(records),
        "limit": limit,
        "offset": offset,
        "page_receipt": page_receipt,
        "blockers": blockers,
        "records": records,
    }


def aggregate_fred_api_json_pages(
    series_raw: bytes,
    pages: Iterable[dict[str, Any]],
    contract_name: str,
) -> dict[str, Any]:
    """Verify and assemble a complete ordered FRED observation rowset.

    Every page retains its exact provider bytes and an explicit normalized
    request identity.  No payload is rewritten to make ``count`` equal the
    number of rows on one page.
    """
    parsed_pages: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("raw_bytes"), bytes):
            raise AdapterError("FRED_PAGE_INPUT_INVALID", str(index))
        if not isinstance(page.get("request_parameters"), dict):
            raise AdapterError("FRED_REQUEST_PARAMETERS_REQUIRED", str(index))
        parsed_pages.append(parse_fred_api_json(series_raw, page["raw_bytes"], contract_name, page["request_parameters"]))
    if not parsed_pages:
        raise AdapterError("FRED_PAGES_MISSING")

    first = parsed_pages[0]
    total_count = first["total_count"]
    limit = first["limit"]
    source_id = first["page_receipt"]["source_id"]
    realtime_start = first["page_receipt"]["realtime_start"]
    realtime_end = first["page_receipt"]["realtime_end"]
    vintage_policy = first["page_receipt"]["vintage_policy"]
    expected_offset = 0
    all_records: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    seen_offsets: set[int] = set()
    seen_page_sha: set[str] = set()
    previous_key: str | None = None

    for index, parsed in enumerate(parsed_pages):
        receipt = parsed["page_receipt"]
        offset = receipt["offset"]
        if offset in seen_offsets or receipt["raw_payload_sha256"] in seen_page_sha:
            raise AdapterError("FRED_DUPLICATE_PAGE", f"index={index}; offset={offset}")
        seen_offsets.add(offset)
        seen_page_sha.add(receipt["raw_payload_sha256"])
        if offset != expected_offset:
            raise AdapterError("FRED_PAGE_OFFSET_NOT_CONTIGUOUS", f"index={index}; expected={expected_offset}; actual={offset}")
        if parsed["total_count"] != total_count or parsed["limit"] != limit:
            raise AdapterError("FRED_CROSS_PAGE_PAGINATION_CONFLICT", str(index))
        if receipt["source_id"] != source_id:
            raise AdapterError("FRED_CROSS_PAGE_SOURCE_CONFLICT", str(index))
        if (receipt["realtime_start"], receipt["realtime_end"], receipt["vintage_policy"]) != (realtime_start, realtime_end, vintage_policy):
            raise AdapterError("FRED_CROSS_PAGE_VINTAGE_CONFLICT", str(index))
        reference_request = first["page_receipt"]["request_parameters"]
        request = receipt["request_parameters"]
        for field in REQUEST_PARAMETER_FIELDS:
            if field == "offset":
                continue
            if request[field] != reference_request[field]:
                raise AdapterError("FRED_CROSS_PAGE_REQUEST_CONFLICT", f"index={index}; field={field}")
        for record in parsed["records"]:
            identity = record["record_identity_sha256"]
            key = record["observation_key"]
            if identity in seen_identities:
                raise AdapterError("FRED_DUPLICATE_RECORD_IDENTITY", identity)
            if previous_key is not None and key <= previous_key:
                raise AdapterError("FRED_CROSS_PAGE_ORDER_INVALID", f"previous={previous_key}; current={key}")
            seen_identities.add(identity)
            previous_key = key
            all_records.append(record)
        expected_offset += parsed["page_row_count"]

    if expected_offset != total_count or len(all_records) != total_count:
        raise AdapterError("FRED_PAGINATION_INCOMPLETE", f"count={total_count}; assembled={len(all_records)}; next_offset={expected_offset}")

    page_receipts = [page["page_receipt"] for page in parsed_pages]
    blockers = [
        "OBSERVATION_SOURCE_PUBLISHED_AT_EVIDENCE_MISSING",
        "OBSERVATION_AVAILABLE_AT_EVIDENCE_MISSING",
        "TRUSTED_FETCH_RECEIPT_MISSING",
    ]
    return {
        "adapter_id": "FRED_API_JSON_V1",
        "adapter_status": "PROVIDER_FORMAT_PARSED",
        "provider_format_status": "PROVIDER_FORMAT_VERIFIED",
        "source_semantics_status": "SOURCE_SEMANTICS_VERIFIED",
        "source_publication_available_status": "SOURCE_PUBLICATION_AVAILABLE_NOT_VERIFIED",
        "pagination_status": "PAGINATION_COMPLETE",
        "pagination_complete": True,
        "currentness_status": "DATA_INSUFFICIENT",
        "currentness_eligible": False,
        "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
        "contract_name": contract_name,
        "series_payload_sha256": sha256_bytes(series_raw),
        "total_count": total_count,
        "assembled_row_count": len(all_records),
        "page_count": len(page_receipts),
        "page_receipts": page_receipts,
        "page_set_identity_sha256": canonical_sha([
            {
                "raw_payload_sha256": page["raw_payload_sha256"],
                "request_parameter_identity_sha256": page["request_parameter_identity_sha256"],
                "offset": page["offset"],
                "limit": page["limit"],
            }
            for page in page_receipts
        ]),
        "canonical_source_rowset_sha256": canonical_sha(all_records),
        "blockers": blockers,
        "records": all_records,
    }


def enforce_required_observation_keys(result: dict[str, Any], required_keys: list[str]) -> None:
    """Bind parsed provider rows to separately approved schedule/calendar keys."""
    actual = [row.get("observation_key") for row in result.get("records", []) if isinstance(row, dict)]
    duplicates = sorted(key for key in set(actual) if actual.count(key) > 1)
    if duplicates:
        raise AdapterError("PROVIDER_DUPLICATE_OBSERVATION_KEY", ",".join(duplicates))
    missing = sorted(set(required_keys) - set(actual))
    if missing:
        raise AdapterError("PROVIDER_EXPECTED_OBSERVATION_MISSING", ",".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    try:
        result = parse_fred_api_json(args.series.read_bytes(), args.observations.read_bytes(), args.contract)
    except (OSError, AdapterError) as exc:
        code = exc.code if isinstance(exc, AdapterError) else "INPUT_READ_ERROR"
        result = {"adapter_status": "FAIL_CLOSED", "error_code": code, "detail": str(exc), "currentness_status": "DATA_INSUFFICIENT", "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE"}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("adapter_status") == "PROVIDER_FORMAT_PARSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
