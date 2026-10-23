"""Disabled T46 correction-aware reader candidate.

This module is deliberately isolated from every production reader and writer.
The formal entry point always fails closed.  Only explicitly issued synthetic
test capabilities can exercise the candidate resolver.

Candidate status does not approve a correction schema, cutoff policy,
authority policy, reader migration, queue replacement, or T46 recovery.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import weakref
from dataclasses import dataclass
from typing import Any

CANDIDATE_ENABLED = False
FORMAL_T46_RECOVERY_AUTHORIZED = False
FORMAL_READER_ACTIVATION_AUTHORIZED = False

SCHEMA_VERSION = "DOCUMENT_CORRECTION_1_PROTOTYPE"
SYNTHETIC_SCOPE = "EXTERNAL_SYNTHETIC_FIXTURE_ONLY"
TEMPORAL_CONTRACT_VERSION = "SYNTHETIC_TEMPORAL_CONTRACT_1"
ADAPTER_SCHEMA = "CORRECTION_AWARE_READER_ADAPTER_0.2_EXTERNAL_PROTOTYPE"
SERIALIZED_HANDLE_SCHEMA = "CORRECTION_AWARE_READER_HANDLE_0.2_EXTERNAL_PROTOTYPE"
QUEUE_SCHEMA = "EFFECTIVE_DOCUMENT_EXTRACTION_QUEUE_0.2_EXTERNAL_PROTOTYPE"
CACHE_CONTRACT_VERSION = "EFFECTIVE_VIEW_CACHE_BINDING_1"

TRUSTED_INVENTORY_SCHEMA = "T46_READER_INVENTORY_1"
TRUSTED_INVENTORY_REPOSITORY_HEAD = "0b33109accfbd4c51c91c99c664c649ef5cbcde8"
TRUSTED_READER_INVENTORY_SHA256 = "96a5f2256c2f8cf040fecd54bba6797db93eee8ecdde11d15cf7660fccad6b38"
TRUSTED_CRITICAL_READER_IDS = frozenset({
    "R01_REGISTER_DOC_DEDUP", "R02_EXTRACTION_VALIDATOR", "R03_FIELD_VALUE_BUILDER",
    "R04_CURRENTNESS_REPLAY_API", "R05_EARNINGS_DOCS_WATCH", "R06_RESEARCH_SNAPSHOT_AI_VALUES",
    "R07_WEEKLY_REFRESH_PREVIEW", "R08_AUTO_LOOP_DYNAMIC_EXTRACTOR", "R09_PIT_FREEZE_ONLY_DRAFT",
    "R10_MANUAL_T09_SCRIPTS", "R12_T46_RECOVERY_SCRIPT", "R14_EXTERNAL_OR_UNVERSIONED_READER",
})

_RECORD_TYPES = {"VOID", "SUPERSEDE"}
_SEMANTICS = {"ERRONEOUS_REGISTRATION_ALL_AS_OF", "EFFECTIVE_AT_CORRECTION_AVAILABLE_AT"}
_REASONS = {"ERRONEOUS_HISTORICAL_BACKFILL", "ERRONEOUS_REGISTRATION", "SOURCE_RETRACTION",
            "DUPLICATE_REGISTRATION", "CONTENT_INTEGRITY_FAILURE"}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_TIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|([+-])(\d{2}):(\d{2}))$"
)


class CandidateError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _fail(code: str, detail: str = "") -> None:
    raise CandidateError(code, detail)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def hash_object(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _without(value: dict, *keys: str) -> dict:
    return {key: item for key, item in value.items() if key not in keys}


def _require_hash(value: Any, code: str, detail: str = "") -> None:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        _fail(code, detail)


def parse_timestamp_strict(value: Any, code: str = "TIMESTAMP_INVALID", detail: str = "") -> int:
    if not isinstance(value, str):
        _fail(code, detail)
    match = _TIME_RE.fullmatch(value)
    if not match:
        _fail(code, detail)
    year, month, day, hour, minute, second = map(int, match.group(1, 2, 3, 4, 5, 6))
    fraction = match.group(7) or ""
    zone, sign, off_hour, off_minute = match.group(8, 9, 10, 11)
    if year < 1 or hour > 23 or minute > 59 or second > 59:
        _fail(code, detail)
    try:
        date = dt.date(year, month, day)
    except ValueError:
        _fail(code, detail)
    offset_seconds = 0
    if zone != "Z":
        oh, om = int(off_hour), int(off_minute)
        if oh > 23 or om > 59:
            _fail(code, detail)
        offset_seconds = (oh * 60 + om) * 60 * (1 if sign == "+" else -1)
    days = (date - dt.date(1970, 1, 1)).days
    seconds = days * 86400 + hour * 3600 + minute * 60 + second - offset_seconds
    nanos = int((fraction + "000000000")[:9])
    return seconds * 1_000_000_000 + nanos


def seal_document(document: dict) -> dict:
    body = _without(document, "row_sha256")
    return {**body, "row_sha256": hash_object(body)}


def seal_authority(authority: dict) -> dict:
    body = _without(authority, "authority_sha256")
    return {**body, "authority_sha256": hash_object(body)}


def seal_temporal_contract(contract: dict) -> dict:
    body = _without(contract, "contract_sha256")
    return {**body, "contract_sha256": hash_object(body)}


def seal_correction(correction: dict) -> dict:
    base = _without(correction, "correction_id", "row_sha256")
    with_id = {**base, "correction_id": hash_object(base)}
    return {**with_id, "row_sha256": hash_object(with_id)}


def build_document_manifest(documents: list[dict]) -> dict:
    return {"schema_version": "SYNTHETIC_DOCUMENT_MANIFEST_1", "scope": SYNTHETIC_SCOPE,
            "documents": sorted(copy.deepcopy(documents), key=lambda row: row["doc_id"])}


def _validate_manifest(manifest: dict, expected_hash: str) -> dict[str, dict]:
    _require_hash(expected_hash, "EXPECTED_MANIFEST_HASH_INVALID", "expectedDocumentManifestSha256")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "SYNTHETIC_DOCUMENT_MANIFEST_1":
        _fail("DOCUMENT_MANIFEST_SCHEMA_INVALID")
    if manifest.get("scope") != SYNTHETIC_SCOPE:
        _fail("FORMAL_T46_AUTHORITY_NOT_GRANTED")
    if hash_object(manifest) != expected_hash:
        _fail("DOCUMENT_MANIFEST_HASH_MISMATCH")
    if not isinstance(manifest.get("documents"), list):
        _fail("DOCUMENT_MANIFEST_DOCUMENTS_INVALID")
    result = {}
    for document in manifest["documents"]:
        doc_id = document.get("doc_id") if isinstance(document, dict) else None
        if not isinstance(doc_id, str) or not doc_id:
            _fail("DOCUMENT_ID_INVALID")
        if doc_id in result:
            _fail("DUPLICATE_DOCUMENT_ID", doc_id)
        for key, code in (("raw_sha256", "DOCUMENT_RAW_HASH_INVALID"),
                          ("text_sha256", "DOCUMENT_TEXT_HASH_INVALID"),
                          ("row_sha256", "DOCUMENT_ROW_HASH_INVALID")):
            _require_hash(document.get(key), code, doc_id)
        if hash_object(_without(document, "row_sha256")) != document["row_sha256"]:
            _fail("DOCUMENT_ROW_HASH_MISMATCH", doc_id)
        parse_timestamp_strict(document.get("available_at"), "DOCUMENT_AVAILABLE_AT_INVALID", doc_id)
        result[doc_id] = document
    return result


def _validate_authority(authority: dict) -> int:
    if not isinstance(authority, dict):
        _fail("AUTHORITY_REQUIRED")
    if authority.get("scope") != SYNTHETIC_SCOPE:
        _fail("FORMAL_T46_AUTHORITY_NOT_GRANTED")
    authority_id = authority.get("authority_id")
    if not isinstance(authority_id, str) or not authority_id:
        _fail("AUTHORITY_ID_REQUIRED")
    _require_hash(authority.get("authority_sha256"), "AUTHORITY_HASH_INVALID", authority_id)
    if hash_object(_without(authority, "authority_sha256")) != authority["authority_sha256"]:
        _fail("AUTHORITY_HASH_MISMATCH", authority_id)
    return parse_timestamp_strict(authority.get("authorized_at"), "AUTHORITY_AUTHORIZED_AT_INVALID", authority_id)


def _validate_temporal_contract(document_as_of: str, knowledge_cutoff: str,
                                contract: dict | None) -> str:
    if document_as_of == knowledge_cutoff and contract is None:
        return "SAME_CUTOFF"
    if not isinstance(contract, dict):
        _fail("TEMPORAL_CONTRACT_REQUIRED_FOR_UNEQUAL_CUTOFFS")
    required = {"schema_version": TEMPORAL_CONTRACT_VERSION, "scope": SYNTHETIC_SCOPE,
                "mode": "BITEMPORAL_EXPLICIT", "decision": "ALLOW_UNEQUAL_CUTOFFS_SYNTHETIC_ONLY",
                "document_as_of": document_as_of, "knowledge_cutoff": knowledge_cutoff}
    for key, expected in required.items():
        if contract.get(key) != expected:
            _fail("TEMPORAL_CONTRACT_BINDING_MISMATCH", key)
    _require_hash(contract.get("contract_sha256"), "TEMPORAL_CONTRACT_HASH_INVALID")
    if hash_object(_without(contract, "contract_sha256")) != contract["contract_sha256"]:
        _fail("TEMPORAL_CONTRACT_HASH_MISMATCH")
    authorized = parse_timestamp_strict(contract.get("authorized_at"), "TEMPORAL_CONTRACT_AUTHORIZED_AT_INVALID")
    if authorized > parse_timestamp_strict(knowledge_cutoff, "KNOWLEDGE_CUTOFF_INVALID"):
        _fail("TEMPORAL_CONTRACT_NOT_KNOWN_AT_CUTOFF")
    return contract["contract_sha256"]


def _validate_correction(event: dict, docs: dict[str, dict], manifest_hash: str,
                         evidence_registry: dict[str, str]) -> tuple[int, int]:
    if not isinstance(event, dict) or event.get("schema_version") != SCHEMA_VERSION:
        _fail("CORRECTION_SCHEMA_INVALID")
    if event.get("record_type") not in _RECORD_TYPES:
        _fail("CORRECTION_RECORD_TYPE_INVALID")
    if event.get("effective_semantics") not in _SEMANTICS:
        _fail("EFFECTIVE_SEMANTICS_INVALID")
    if event.get("reason_code") not in _REASONS:
        _fail("REASON_CODE_INVALID")
    if event.get("source_document_manifest_sha256") != manifest_hash:
        _fail("CORRECTION_SOURCE_MANIFEST_HASH_MISMATCH")
    target_id = event.get("target_doc_id")
    if target_id not in docs:
        _fail("CORRECTION_TARGET_NOT_FOUND", str(target_id))
    target = docs[target_id]
    if event.get("target_row_sha256") != target["row_sha256"]:
        _fail("CORRECTION_TARGET_HASH_MISMATCH", target_id)
    if event.get("target_raw_sha256") != target["raw_sha256"]:
        _fail("CORRECTION_TARGET_RAW_HASH_MISMATCH", target_id)
    if event["record_type"] == "VOID":
        if event.get("replacement_doc_id") is not None or event.get("replacement_row_sha256") is not None:
            _fail("VOID_REPLACEMENT_MUST_BE_NULL", target_id)
    else:
        replacement_id = event.get("replacement_doc_id")
        if not isinstance(replacement_id, str) or replacement_id not in docs:
            _fail("CORRECTION_REPLACEMENT_NOT_FOUND", str(replacement_id))
        if replacement_id == target_id:
            _fail("SUPERSEDE_SELF_REFERENCE", target_id)
        if event.get("replacement_row_sha256") != docs[replacement_id]["row_sha256"]:
            _fail("CORRECTION_REPLACEMENT_HASH_MISMATCH", replacement_id)
    correction_id = event.get("correction_id", "")
    created = parse_timestamp_strict(event.get("created_at"), "CORRECTION_CREATED_AT_INVALID", correction_id)
    effective = parse_timestamp_strict(event.get("effective_at"), "CORRECTION_EFFECTIVE_AT_INVALID", correction_id)
    if effective < created:
        _fail("CORRECTION_BACKDATED_EFFECTIVE_AT", correction_id)
    if _validate_authority(event.get("authority_ref")) > created:
        _fail("AUTHORITY_NOT_VALID_AT_CORRECTION_CREATION", correction_id)
    if parse_timestamp_strict(target["available_at"]) > created:
        _fail("TARGET_NOT_AVAILABLE_AT_CORRECTION_CREATION", target_id)
    if event["record_type"] == "SUPERSEDE":
        replacement_id = event["replacement_doc_id"]
        if parse_timestamp_strict(docs[replacement_id]["available_at"]) > created:
            _fail("REPLACEMENT_NOT_AVAILABLE_AT_CORRECTION_CREATION", replacement_id)
    refs = event.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        _fail("EVIDENCE_REQUIRED", correction_id)
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("evidence_id"), str):
            _fail("EVIDENCE_REFERENCE_INVALID")
        _require_hash(ref.get("sha256"), "EVIDENCE_HASH_INVALID", ref["evidence_id"])
        if evidence_registry.get(ref["evidence_id"]) != ref["sha256"]:
            _fail("EVIDENCE_BINDING_MISMATCH", ref["evidence_id"])
    _require_hash(correction_id, "CORRECTION_ID_INVALID")
    _require_hash(event.get("row_sha256"), "CORRECTION_ROW_HASH_INVALID", correction_id)
    if hash_object(_without(event, "correction_id", "row_sha256")) != correction_id:
        _fail("CORRECTION_ID_MISMATCH", correction_id)
    if hash_object(_without(event, "row_sha256")) != event["row_sha256"]:
        _fail("CORRECTION_ROW_HASH_MISMATCH", correction_id)
    return created, effective


def _validate_no_cycles(edges: dict[str, str]) -> None:
    for start in edges:
        seen, current = set(), start
        while current in edges:
            if current in seen:
                _fail("SUPERSEDE_CYCLE", start)
            seen.add(current)
            current = edges[current]


def resolve_effective_document_view(*, document_manifest: dict, expected_document_manifest_sha256: str,
                                    corrections: list[dict], evidence_registry: dict[str, str],
                                    document_as_of: str, knowledge_cutoff: str,
                                    temporal_contract: dict | None = None) -> dict:
    docs = _validate_manifest(document_manifest, expected_document_manifest_sha256)
    document_time = parse_timestamp_strict(document_as_of, "DOCUMENT_AS_OF_INVALID")
    knowledge_time = parse_timestamp_strict(knowledge_cutoff, "KNOWLEDGE_CUTOFF_INVALID")
    contract_identity = _validate_temporal_contract(document_as_of, knowledge_cutoff, temporal_contract)
    if not isinstance(corrections, list):
        _fail("CORRECTION_LOG_INVALID")
    previous, unique, correction_ids, duplicate_count = None, [], set(), 0
    for event in corrections:
        created, effective = _validate_correction(event, docs, expected_document_manifest_sha256,
                                                  evidence_registry or {})
        if previous is not None and created < previous:
            _fail("CORRECTION_LOG_NOT_APPEND_TIME_ORDERED")
        previous = created
        if event["correction_id"] in correction_ids:
            duplicate_count += 1
            continue
        correction_ids.add(event["correction_id"])
        unique.append((event, created, effective))
    known = [row for row in unique if row[1] <= knowledge_time and row[2] <= knowledge_time]
    visible = [row for row in known if row[0]["effective_semantics"] == "ERRONEOUS_REGISTRATION_ALL_AS_OF"
               or row[2] <= document_time]
    terminal, edges = {}, {}
    for event, _, _ in visible:
        target = event["target_doc_id"]
        if target in terminal:
            _fail("CONFLICTING_TERMINAL_CORRECTION", target)
        terminal[target] = event
        if event["record_type"] == "SUPERSEDE":
            edges[target] = event["replacement_doc_id"]
    _validate_no_cycles(edges)
    active = {doc_id: doc for doc_id, doc in docs.items()
              if parse_timestamp_strict(doc["available_at"]) <= document_time
              and parse_timestamp_strict(doc["available_at"]) <= knowledge_time}
    for event, _, _ in visible:
        active.pop(event["target_doc_id"], None)
        if event["record_type"] == "SUPERSEDE":
            replacement = docs[event["replacement_doc_id"]]
            available = parse_timestamp_strict(replacement["available_at"])
            if available > document_time or available > knowledge_time:
                _fail("REPLACEMENT_NOT_AVAILABLE_AS_OF", event["replacement_doc_id"])
            active[event["replacement_doc_id"]] = replacement
    documents = sorted(active.values(), key=lambda row: row["doc_id"])
    prefix = [row[0] for row in visible]
    body = {"schema_version": "EFFECTIVE_DOCUMENT_VIEW_1_PROTOTYPE", "scope": SYNTHETIC_SCOPE,
            "document_as_of": document_as_of, "knowledge_cutoff": knowledge_cutoff,
            "temporal_contract_identity": contract_identity,
            "source_document_manifest_sha256": expected_document_manifest_sha256,
            "correction_log_sha256": hash_object(corrections),
            "visible_correction_prefix_sha256": hash_object(prefix), "source_document_count": len(docs),
            "known_correction_count": len(known), "visible_correction_count": len(visible),
            "future_correction_count": len(unique) - len(known),
            "known_not_yet_effective_count": len(known) - len(visible),
            "duplicate_noop_count": duplicate_count, "effective_document_count": len(documents),
            "documents": documents}
    return {**body, "effective_view_sha256": hash_object(body)}


@dataclass(frozen=True)
class _SyntheticTestCapability:
    _marker: object


_CAPABILITY_MARKER = object()


def issue_synthetic_test_capability() -> _SyntheticTestCapability:
    """Issue a process-local capability. Never accepted by the formal entry."""
    return _SyntheticTestCapability(_CAPABILITY_MARKER)


class _ReaderHandle:
    __slots__ = ("handle_id", "__weakref__")

    def __init__(self, handle_id: str):
        self.handle_id = handle_id


_HANDLES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _require_capability(capability: Any) -> None:
    if not isinstance(capability, _SyntheticTestCapability) or capability._marker is not _CAPABILITY_MARKER:
        _fail("SYNTHETIC_TEST_CAPABILITY_REQUIRED")


def open_formal_reader(*_args: Any, **_kwargs: Any) -> None:
    _fail("FORMAL_T46_SOURCE_REJECTED_CANDIDATE_DISABLED")


def _validate_view(view: dict) -> set[str]:
    if not isinstance(view, dict) or view.get("schema_version") != "EFFECTIVE_DOCUMENT_VIEW_1_PROTOTYPE":
        _fail("EFFECTIVE_VIEW_SCHEMA_INVALID")
    _require_hash(view.get("effective_view_sha256"), "EFFECTIVE_VIEW_HASH_INVALID")
    if hash_object(_without(view, "effective_view_sha256")) != view["effective_view_sha256"]:
        _fail("EFFECTIVE_VIEW_HASH_MISMATCH")
    ids = set()
    for document in view.get("documents", []):
        doc_id = document.get("doc_id") if isinstance(document, dict) else None
        if not isinstance(doc_id, str) or doc_id in ids:
            _fail("EFFECTIVE_VIEW_DOCUMENT_ID_INVALID")
        ids.add(doc_id)
        for key, code in (("row_sha256", "EFFECTIVE_VIEW_DOCUMENT_ROW_HASH_INVALID"),
                          ("raw_sha256", "EFFECTIVE_VIEW_DOCUMENT_RAW_HASH_INVALID"),
                          ("text_sha256", "EFFECTIVE_VIEW_DOCUMENT_TEXT_HASH_INVALID")):
            _require_hash(document.get(key), code)
    return ids


def open_synthetic_reader(raw_input: dict, *, capability: _SyntheticTestCapability) -> _ReaderHandle:
    _require_capability(capability)
    if not isinstance(raw_input, dict) or raw_input.get("adapterSchema") != ADAPTER_SCHEMA:
        _fail("ADAPTER_SCHEMA_INVALID")
    if raw_input.get("governanceMode") != "SINGLE_CUTOFF_ONLY":
        _fail("GOVERNANCE_MODE_NOT_APPROVED")
    if raw_input.get("authorityMode") != "PROSPECTIVE_ONLY":
        _fail("AUTHORITY_MODE_NOT_APPROVED")
    if raw_input.get("documentAsOf") != raw_input.get("knowledgeCutoff"):
        _fail("SINGLE_CUTOFF_MISMATCH")
    source = copy.deepcopy(raw_input)
    view = resolve_effective_document_view(
        document_manifest=source["documentManifest"],
        expected_document_manifest_sha256=source["expectedDocumentManifestSha256"],
        corrections=source["corrections"], evidence_registry=source["evidenceRegistry"],
        document_as_of=source["documentAsOf"], knowledge_cutoff=source["knowledgeCutoff"],
    )
    active_ids = sorted(_validate_view(view))
    handle_id = hash_object({"adapter_schema": ADAPTER_SCHEMA, "cache_contract_version": CACHE_CONTRACT_VERSION,
                             "effective_view_sha256": view["effective_view_sha256"],
                             "source_document_manifest_sha256": view["source_document_manifest_sha256"],
                             "source_correction_log_sha256": view["correction_log_sha256"],
                             "document_as_of": view["document_as_of"],
                             "knowledge_cutoff": view["knowledge_cutoff"]})
    handle = _ReaderHandle(handle_id)
    _HANDLES[handle] = {"valid": True, "input": source, "view": copy.deepcopy(view),
                        "active_ids": active_ids, "handle_id": handle_id}
    return handle


def _require_handle(handle: Any) -> dict:
    try:
        state = _HANDLES.get(handle)
    except TypeError:
        _fail("READER_HANDLE_NOT_ISSUED")
    if state is None:
        _fail("READER_HANDLE_NOT_ISSUED")
    if not state["valid"]:
        _fail("READER_HANDLE_INVALIDATED")
    if sorted(_validate_view(state["view"])) != state["active_ids"]:
        _fail("READER_HANDLE_DOCUMENT_SET_MISMATCH")
    return state


def inspect_reader_handle(handle: Any) -> dict:
    state = _require_handle(handle)
    return copy.deepcopy({"schema_version": ADAPTER_SCHEMA, "handle_id": state["handle_id"],
                          "active_doc_ids": state["active_ids"], "effective_view": state["view"]})


def invalidate_reader_handle(handle: Any) -> None:
    _require_handle(handle)["valid"] = False


def serialize_reader_handle(handle: Any) -> dict:
    state = _require_handle(handle)
    view = state["view"]
    body = {"schema_version": SERIALIZED_HANDLE_SCHEMA, "adapter_schema": ADAPTER_SCHEMA,
            "cache_contract_version": CACHE_CONTRACT_VERSION, "handle_id": state["handle_id"],
            "effective_view_sha256": view["effective_view_sha256"],
            "source_document_manifest_sha256": view["source_document_manifest_sha256"],
            "source_correction_log_sha256": view["correction_log_sha256"],
            "document_as_of": view["document_as_of"], "knowledge_cutoff": view["knowledge_cutoff"],
            "resolver_input": copy.deepcopy(state["input"])}
    return {**body, "serialized_handle_sha256": hash_object(body)}


def restore_reader_handle(envelope: dict, *, capability: _SyntheticTestCapability) -> _ReaderHandle:
    _require_capability(capability)
    if not isinstance(envelope, dict) or envelope.get("schema_version") != SERIALIZED_HANDLE_SCHEMA:
        _fail("SERIALIZED_HANDLE_SCHEMA_INVALID")
    if envelope.get("adapter_schema") != ADAPTER_SCHEMA:
        _fail("SERIALIZED_HANDLE_ADAPTER_VERSION_MISMATCH")
    if envelope.get("cache_contract_version") != CACHE_CONTRACT_VERSION:
        _fail("SERIALIZED_HANDLE_CACHE_VERSION_MISMATCH")
    _require_hash(envelope.get("serialized_handle_sha256"), "SERIALIZED_HANDLE_HASH_INVALID")
    if hash_object(_without(envelope, "serialized_handle_sha256")) != envelope["serialized_handle_sha256"]:
        _fail("SERIALIZED_HANDLE_HASH_MISMATCH")
    handle = open_synthetic_reader(envelope["resolver_input"], capability=capability)
    state = _require_handle(handle)
    view = state["view"]
    bindings = {"handle_id": state["handle_id"], "effective_view_sha256": view["effective_view_sha256"],
                "source_document_manifest_sha256": view["source_document_manifest_sha256"],
                "source_correction_log_sha256": view["correction_log_sha256"],
                "document_as_of": view["document_as_of"], "knowledge_cutoff": view["knowledge_cutoff"]}
    for key, expected in bindings.items():
        if envelope.get(key) != expected:
            _fail("SERIALIZED_HANDLE_BINDING_MISMATCH", key)
    return handle


def rebuild_synthetic_queue(*, reader_handle: Any, requested_by_issuer: dict[str, list[str]],
                            previous_queue: dict | None = None,
                            capability: _SyntheticTestCapability) -> dict:
    _require_capability(capability)
    state = _require_handle(reader_handle)
    view, active = state["view"], set(state["active_ids"])
    if not isinstance(requested_by_issuer, dict):
        _fail("QUEUE_REQUEST_INVALID")
    companies = []
    for issuer_id in sorted(requested_by_issuer):
        ids = list(requested_by_issuer[issuer_id])
        if len(set(ids)) != len(ids):
            _fail("QUEUE_DUPLICATE_DOC", issuer_id)
        for doc_id in ids:
            if doc_id not in active:
                _fail("QUEUE_REFERENCES_INACTIVE_DOCUMENT", doc_id)
            document = next((row for row in view["documents"] if row["doc_id"] == doc_id), None)
            if document is None or document.get("issuer_id") != issuer_id:
                _fail("QUEUE_ISSUER_MISMATCH", doc_id)
        companies.append({"issuer_id": issuer_id, "new_report_doc_ids": sorted(ids), "status": "NOT_STARTED"})
    if previous_queue is not None:
        _require_hash(previous_queue.get("queue_sha256"), "STALE_QUEUE_HASH_INVALID")
        if hash_object(_without(previous_queue, "queue_sha256")) != previous_queue["queue_sha256"]:
            _fail("STALE_QUEUE_HASH_MISMATCH")
        checks = (("schema_version", QUEUE_SCHEMA, "STALE_QUEUE_SCHEMA_MISMATCH"),
                  ("cache_contract_version", CACHE_CONTRACT_VERSION, "STALE_QUEUE_CACHE_VERSION_MISMATCH"),
                  ("source_effective_view_sha256", view["effective_view_sha256"], "STALE_QUEUE_EFFECTIVE_VIEW_BINDING"),
                  ("source_document_manifest_sha256", view["source_document_manifest_sha256"], "STALE_QUEUE_MANIFEST_BINDING"),
                  ("source_correction_log_sha256", view["correction_log_sha256"], "STALE_QUEUE_CORRECTION_BINDING"))
        for key, expected, code in checks:
            if previous_queue.get(key) != expected:
                _fail(code)
        for company in previous_queue.get("companies", []):
            for doc_id in company.get("new_report_doc_ids", []):
                if doc_id not in active:
                    _fail("STALE_QUEUE_REFERENCES_INACTIVE_DOCUMENT", doc_id)
    body = {"schema_version": QUEUE_SCHEMA, "cache_contract_version": CACHE_CONTRACT_VERSION,
            "scope": SYNTHETIC_SCOPE, "document_as_of": view["document_as_of"],
            "knowledge_cutoff": view["knowledge_cutoff"], "source_reader_handle_id": state["handle_id"],
            "source_effective_view_sha256": view["effective_view_sha256"],
            "source_document_manifest_sha256": view["source_document_manifest_sha256"],
            "source_correction_log_sha256": view["correction_log_sha256"], "companies": companies}
    return {**body, "queue_sha256": hash_object(body)}


def evaluate_reader_coverage(request: dict) -> dict:
    if not isinstance(request, dict):
        _fail("READER_COVERAGE_REQUEST_INVALID")
    inventory, expected_hash = request.get("inventory"), request.get("expectedInventorySha256")
    if not isinstance(inventory, dict):
        _fail("READER_INVENTORY_INVALID")
    if expected_hash != TRUSTED_READER_INVENTORY_SHA256:
        _fail("READER_INVENTORY_EXPECTED_HASH_UNTRUSTED")
    actual_hash = hash_object(inventory)
    if actual_hash != TRUSTED_READER_INVENTORY_SHA256:
        _fail("READER_INVENTORY_HASH_MISMATCH")
    if inventory.get("schema_version") != TRUSTED_INVENTORY_SCHEMA:
        _fail("READER_INVENTORY_SCHEMA_MISMATCH")
    if inventory.get("repository_head") != TRUSTED_INVENTORY_REPOSITORY_HEAD:
        _fail("READER_INVENTORY_REPOSITORY_MISMATCH")
    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        _fail("READER_INVENTORY_EMPTY")
    ids = [row.get("reader_id") for row in entries]
    if len(set(ids)) != len(ids):
        _fail("READER_INVENTORY_DUPLICATE_ID")
    critical = [row for row in entries if row.get("recovery_critical") is True]
    if {row.get("reader_id") for row in critical} != TRUSTED_CRITICAL_READER_IDS:
        _fail("CRITICAL_READER_SET_MISMATCH")
    blockers = [row for row in critical if row.get("migration_status") not in {"MIGRATED_VERIFIED", "EXEMPT_VERIFIED"}]
    unresolved = [row for row in critical if row.get("deployment") in {"UNKNOWN", "LIVE_DYNAMIC_AGENT_ENTRY"}
                  or "UNKNOWN" in str(row.get("migration_status"))]
    return {"status": "BLOCKED", "inventory_sha256": actual_hash,
            "inventory_schema_version": inventory["schema_version"],
            "inventory_repository_head": inventory["repository_head"], "reader_count": len(entries),
            "critical_reader_count": len(critical), "blocker_count": len(blockers),
            "blocker_ids": sorted(row["reader_id"] for row in blockers),
            "unresolved_identity_count": len(unresolved),
            "unresolved_identity_ids": sorted(row["reader_id"] for row in unresolved),
            "formal_coverage_authorized": False}
