"""Disabled-by-default point-in-time input freezer.

This module captures evidence only.  It does not compute classifications and it
does not alter the existing research snapshot path.  Callers must provide an
explicit staging directory outside the source repository and a hash-bound
historical prefix witness for each append-only event ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "PIT_FREEZE_ONLY_0.1_DISABLED"
REQUIRED_LEDGERS = {
    "docs_ledger": "01_共用資料/data_spine/docs_ledger.jsonl",
    "ai_extractions": "01_共用資料/data_spine/ai_extractions.jsonl",
    "ai_field_values": "01_共用資料/data_spine/ai_field_values.jsonl",
    "ai_field_value_voids": "01_共用資料/data_spine/ai_field_value_voids.jsonl",
    "ai_reviews": "01_共用資料/data_spine/ai_reviews.jsonl",
}
EVENT_LEDGERS = ("ai_field_value_voids", "ai_reviews")
T46_LIVE_ROW_COUNT = 706
T46_CLEAN_PREFIX_ROW_COUNT = 107
T46_CLEAN_PREFIX_SHA256 = "e5d5e81a1859a0b4a708c9880428c1353be51b74820451ac13d127219acd87b9"
PRODUCTION_WITNESS_TYPES = {"APPEND_JOURNAL_CHECKPOINT", "TRUSTED_FROZEN_LEDGER_PREFIX"}
SYNTHETIC_WITNESS_TYPE = "SYNTHETIC_TEST_WITNESS"


class FreezeOnlyError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code + (":" + detail if detail else ""))


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_time(value: Any, detail: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FreezeOnlyError("PIT_TIMESTAMP_MISSING", detail)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreezeOnlyError("PIT_TIMESTAMP_INVALID", detail) from exc
    if stamp.tzinfo is None:
        raise FreezeOnlyError("PIT_TIMESTAMP_TIMEZONE_MISSING", detail)
    return stamp


def _confined(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise FreezeOnlyError("PATH_MISSING")
    root = root.resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise FreezeOnlyError("PATH_ESCAPES_ROOT", relative)
    return target


def _outside(source_root: Path, staging: Path) -> None:
    source = source_root.resolve()
    target = staging.resolve()
    if target == source or source in target.parents:
        raise FreezeOnlyError("STAGING_INSIDE_SOURCE_ROOT", str(staging))


def _read_jsonl(root: Path, relative: str) -> tuple[bytes, list[dict[str, Any]]]:
    path = _confined(root, relative)
    if not path.is_file():
        raise FreezeOnlyError("REQUIRED_LEDGER_MISSING", relative)
    data = path.read_bytes()
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FreezeOnlyError("INPUT_JSONL_INVALID", f"{relative}:{number}") from exc
        if not isinstance(row, dict):
            raise FreezeOnlyError("INPUT_JSONL_ROW_NOT_OBJECT", f"{relative}:{number}")
        rows.append(row)
    return data, rows


def _metadata(relative: str, data: bytes, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": relative,
        "byte_length": len(data),
        "sha256": sha256(data),
        "row_count": len(rows),
        "row_count_semantics": "NONEMPTY_JSONL_OBJECT_ROWS_NO_HEADER",
    }


def _unique(rows: list[dict[str, Any]], key: str, ledger: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for number, row in enumerate(rows, 1):
        identity = row.get(key)
        if not isinstance(identity, str) or not identity:
            raise FreezeOnlyError("IDENTITY_MISSING", f"{ledger}:{number}:{key}")
        if identity in result:
            code = "DUPLICATE_KEY_REPEATED" if result[identity] == row else "DUPLICATE_KEY_CONFLICT"
            raise FreezeOnlyError(code, f"{ledger}:{identity}")
        result[identity] = row
    return result


def _prefix_bytes(data: bytes, selected_rows: int) -> bytes:
    if selected_rows == 0:
        return b""
    count = 0
    offset = 0
    for line in data.splitlines(keepends=True):
        offset += len(line)
        if line.strip():
            count += 1
            if count == selected_rows:
                return data[:offset]
    raise FreezeOnlyError("EVENT_PREFIX_BYTE_BOUNDARY_NOT_FOUND", str(selected_rows))


def _event_prefix(data: bytes, rows: list[dict[str, Any]], cutoff: datetime,
                  ledger: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    previous: datetime | None = None
    future_seen = False
    for number, row in enumerate(rows, 1):
        try:
            created = _parse_time(row.get("created_at"), f"{ledger}:{number}:created_at")
        except FreezeOnlyError as exc:
            code = "EVENT_TIME_MISSING" if exc.code == "PIT_TIMESTAMP_MISSING" else "EVENT_TIME_INVALID"
            raise FreezeOnlyError(code, f"{ledger}:{number}:created_at") from exc
        if previous is not None and created < previous:
            raise FreezeOnlyError("EVENT_LEDGER_NOT_APPEND_TIME_ORDERED", f"{ledger}:{number}")
        previous = created
        if created <= cutoff:
            if future_seen:
                raise FreezeOnlyError("EVENT_PREFIX_NOT_PROVABLE", f"{ledger}:{number}")
            selected.append(row)
        else:
            future_seen = True
    prefix = _prefix_bytes(data, len(selected))
    return selected, {
        "event_time_field": "created_at",
        "selection": "EXACT_APPEND_ONLY_PREFIX_AT_CUTOFF",
        "source_row_count": len(rows),
        "effective_row_count": len(selected),
        "future_row_count": len(rows) - len(selected),
        "prefix_byte_length": len(prefix),
        "prefix_sha256": sha256(prefix),
    }


def synthetic_witness(ledger: str, source_sha256: str, prefix: dict[str, Any],
                      cutoff_at: str) -> dict[str, Any]:
    payload = {
        "witness_type": SYNTHETIC_WITNESS_TYPE,
        "scope": "EXTERNAL_SYNTHETIC_FIXTURE_ONLY",
        "ledger": ledger,
        "source_sha256": source_sha256,
        "prefix_sha256": prefix["prefix_sha256"],
        "effective_row_count": prefix["effective_row_count"],
        "cutoff_at": cutoff_at,
    }
    return dict(payload, witness_sha256=sha256(canonical_bytes(payload)))


def _validate_witness(ledger: str, source_sha: str, prefix: dict[str, Any],
                      cutoff_at: str, witness: Any, allow_synthetic: bool) -> dict[str, Any]:
    if not isinstance(witness, dict):
        raise FreezeOnlyError("HISTORICAL_PREFIX_PROVENANCE_UNVERIFIED", ledger)
    witness_type = witness.get("witness_type")
    if witness_type == SYNTHETIC_WITNESS_TYPE:
        if not allow_synthetic or witness.get("scope") != "EXTERNAL_SYNTHETIC_FIXTURE_ONLY":
            raise FreezeOnlyError("SYNTHETIC_WITNESS_NOT_AUTHORIZED", ledger)
    elif witness_type in PRODUCTION_WITNESS_TYPES:
        # Plumbing only: no production witness authority has been hash-bound.
        # A future activation change must provide an explicit approved authority
        # registry rather than trusting a caller-created descriptor.
        raise FreezeOnlyError("HISTORICAL_PREFIX_AUTHORITY_REQUIRED", ledger)
    else:
        raise FreezeOnlyError("HISTORICAL_PREFIX_PROVENANCE_UNVERIFIED", ledger)
    signed_keys = ("witness_type", "scope", "ledger", "source_sha256",
                   "prefix_sha256", "effective_row_count", "cutoff_at")
    signed = {key: witness.get(key) for key in signed_keys}
    if witness.get("witness_sha256") != sha256(canonical_bytes(signed)):
        raise FreezeOnlyError("HISTORICAL_WITNESS_HASH_MISMATCH", ledger)
    expected = {
        "ledger": ledger,
        "source_sha256": source_sha,
        "prefix_sha256": prefix["prefix_sha256"],
        "effective_row_count": prefix["effective_row_count"],
        "cutoff_at": cutoff_at,
    }
    if any(witness.get(key) != value for key, value in expected.items()):
        raise FreezeOnlyError("HISTORICAL_WITNESS_BINDING_MISMATCH", ledger)
    return dict(witness)


def _target_available_at(row: dict[str, Any], detail: str) -> datetime:
    try:
        return _parse_time(row.get("available_at"), detail)
    except FreezeOnlyError as exc:
        raise FreezeOnlyError("TARGET_AVAILABILITY_UNVERIFIED", detail) from exc


def _validate_voids(voids: list[dict[str, Any]], values: dict[str, dict[str, Any]]) -> None:
    for number, row in enumerate(voids, 1):
        target_id = row.get("voided_value_id")
        target = values.get(target_id)
        if target is None:
            raise FreezeOnlyError("VOID_TARGET_NOT_FOUND", f"{number}:{target_id}")
        created = _parse_time(row.get("created_at"), f"void:{number}:created_at")
        available = _target_available_at(target, f"value:{target_id}:available_at")
        if created < available:
            raise FreezeOnlyError("VOID_PRECEDES_TARGET_AVAILABILITY", str(target_id))


def _review_target(row: dict[str, Any], values: dict[str, dict[str, Any]],
                   extractions: dict[str, dict[str, Any]], number: int) -> tuple[str, datetime]:
    value_id = row.get("value_id") or row.get("target_value_id")
    if value_id:
        target = values.get(value_id)
        if target is None:
            raise FreezeOnlyError("REVIEW_TARGET_NOT_FOUND", f"{number}:{value_id}")
        return str(value_id), _target_available_at(target, f"value:{value_id}:available_at")
    extraction_id = row.get("extraction_id")
    if extraction_id:
        target = extractions.get(extraction_id)
        if target is None:
            raise FreezeOnlyError("REVIEW_TARGET_NOT_FOUND", f"{number}:{extraction_id}")
        return str(extraction_id), _target_available_at(target, f"extraction:{extraction_id}:available_at")
    raise FreezeOnlyError("REVIEW_TARGET_PROVENANCE_UNVERIFIED", str(number))


def _validate_reviews(reviews: list[dict[str, Any]], values: dict[str, dict[str, Any]],
                      extractions: dict[str, dict[str, Any]]) -> None:
    for number, row in enumerate(reviews, 1):
        created = _parse_time(row.get("created_at"), f"review:{number}:created_at")
        target_id, available = _review_target(row, values, extractions, number)
        if created < available:
            raise FreezeOnlyError("REVIEW_PRECEDES_TARGET_AVAILABILITY", target_id)
        for key in ("as_of", "effective_as_of", "recorded_as_of"):
            if row.get(key) is None:
                continue
            stamp = _parse_time(row[key], f"review:{number}:{key}")
            if stamp > created:
                raise FreezeOnlyError("REVIEW_TIME_CONFLICT", f"{number}:{key}>created_at")


def _validate_dependencies(docs: dict[str, dict[str, Any]],
                           extractions: dict[str, dict[str, Any]],
                           values: dict[str, dict[str, Any]]) -> None:
    for extraction_id, row in extractions.items():
        for doc_id in row.get("doc_ids") or []:
            if doc_id not in docs:
                raise FreezeOnlyError("EXTRACTION_DOCUMENT_NOT_FOUND", f"{extraction_id}:{doc_id}")
    for value_id, row in values.items():
        for extraction_id in row.get("extraction_ids") or []:
            if extraction_id not in extractions:
                raise FreezeOnlyError("VALUE_EXTRACTION_NOT_FOUND", f"{value_id}:{extraction_id}")
        for doc_id in row.get("doc_ids") or []:
            if doc_id not in docs:
                raise FreezeOnlyError("VALUE_DOCUMENT_NOT_FOUND", f"{value_id}:{doc_id}")


def _artifact_meta(root: Path, doc: dict[str, Any], doc_id: str, field: str) -> dict[str, Any]:
    relative = doc.get(field + "_path")
    if not isinstance(relative, str) or not relative:
        raise FreezeOnlyError("DOCUMENT_ARTIFACT_PATH_MISSING", f"{doc_id}:{field}")
    path = _confined(root, relative)
    if not path.is_file():
        raise FreezeOnlyError("DOCUMENT_ARTIFACT_MISSING", f"{doc_id}:{field}:{relative}")
    data = path.read_bytes()
    expected = doc.get(field + "_sha256")
    actual = sha256(data)
    if expected != actual:
        raise FreezeOnlyError("DOCUMENT_HASH_MISMATCH", f"{doc_id}:{field}")
    return {"source_path": relative, "byte_length": len(data), "sha256": actual,
            "payload": data}


def _validate_docs_trust(data: bytes, rows: list[dict[str, Any]], trust: Any,
                         allow_synthetic: bool) -> dict[str, Any]:
    if len(rows) == T46_LIVE_ROW_COUNT:
        raise FreezeOnlyError("T46_LIVE_LEDGER_NOT_AUTHORIZED")
    if not isinstance(trust, dict):
        raise FreezeOnlyError("DOCS_TRUST_AUTHORITY_REQUIRED")
    mode = trust.get("mode")
    if mode in {"T46_VERIFIED_CLEAN_PREFIX", "LIVE_LEDGER", "EXTERNAL_CORRECTION_VIEW"}:
        raise FreezeOnlyError("T46_CLEAN_PREFIX_AUTHORITY_REQUIRED")
    if mode != "SYNTHETIC_TEST_ONLY" or not allow_synthetic:
        raise FreezeOnlyError("DOCS_TRUST_AUTHORITY_REQUIRED")
    payload = {key: trust.get(key) for key in ("mode", "scope", "sha256", "row_count")}
    if (trust.get("scope") != "EXTERNAL_SYNTHETIC_FIXTURE_ONLY"
            or trust.get("sha256") != sha256(data)
            or trust.get("row_count") != len(rows)
            or trust.get("authority_sha256") != sha256(canonical_bytes(payload))):
        raise FreezeOnlyError("SYNTHETIC_DOCS_AUTHORITY_BINDING_MISMATCH")
    return dict(trust)


def synthetic_docs_trust(data: bytes, row_count: int) -> dict[str, Any]:
    payload = {"mode": "SYNTHETIC_TEST_ONLY", "scope": "EXTERNAL_SYNTHETIC_FIXTURE_ONLY",
               "sha256": sha256(data), "row_count": row_count}
    return dict(payload, authority_sha256=sha256(canonical_bytes(payload)))


def freeze_only(root: Path, specification: dict[str, Any], staging_dir: Path,
                *, allow_synthetic_test_authority: bool = False) -> dict[str, Any]:
    """Freeze five ledgers and bound artifacts into an isolated staging tree.

    This function never computes a verdict.  All validation completes before
    the staging directory is created.
    """
    root = Path(root)
    staging_dir = Path(staging_dir)
    _outside(root, staging_dir)
    paths = specification.get("inputs") or REQUIRED_LEDGERS
    if not isinstance(paths, dict) or set(paths) != set(REQUIRED_LEDGERS):
        raise FreezeOnlyError("PRODUCTION_INPUT_SET_INVALID")
    cutoff_at = specification.get("cutoff_at")
    cutoff = _parse_time(cutoff_at, "cutoff_at")
    loaded = {name: _read_jsonl(root, paths[name]) for name in sorted(paths)}
    docs_data, doc_rows = loaded["docs_ledger"]
    trust = _validate_docs_trust(docs_data, doc_rows, specification.get("docs_trust"),
                                 allow_synthetic_test_authority)
    docs = _unique(doc_rows, "doc_id", "docs_ledger")
    extractions = _unique(loaded["ai_extractions"][1], "extraction_id", "ai_extractions")
    values = _unique(loaded["ai_field_values"][1], "value_id", "ai_field_values")
    _validate_dependencies(docs, extractions, values)

    event_prefixes: dict[str, Any] = {}
    effective: dict[str, list[dict[str, Any]]] = {}
    witnesses = specification.get("historical_witnesses") or {}
    for ledger in EVENT_LEDGERS:
        data, rows = loaded[ledger]
        selected, prefix = _event_prefix(data, rows, cutoff, ledger)
        witness = _validate_witness(ledger, sha256(data), prefix, cutoff_at,
                                    witnesses.get(ledger), allow_synthetic_test_authority)
        event_prefixes[ledger] = dict(prefix, historical_witness=witness)
        effective[ledger] = selected

    _validate_voids(effective["ai_field_value_voids"], values)
    _validate_reviews(effective["ai_reviews"], values, extractions)
    artifacts: list[dict[str, Any]] = []
    payloads: list[tuple[str, bytes]] = []
    for doc_id, doc in sorted(docs.items()):
        item = {"doc_id": doc_id}
        for field in ("raw", "text"):
            meta = _artifact_meta(root, doc, doc_id, field)
            target = f"artifacts/{doc_id}/{field}.bin"
            payloads.append((target, meta.pop("payload")))
            item[field] = dict(meta, staging_path=target)
        artifacts.append(item)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "FREEZE_ONLY_DISABLED_BY_DEFAULT",
        "pit_certification": "NOT_GRANTED_FREEZE_ONLY",
        "classification_effect": "NONE",
        "cutoff_at": cutoff_at,
        "source_root": ".",
        "inputs": {name: _metadata(paths[name], data, rows)
                   for name, (data, rows) in loaded.items()},
        "dependencies": {"documents": len(docs), "extractions": len(extractions),
                         "field_values": len(values)},
        "docs_trust": trust,
        "event_prefixes": event_prefixes,
        "document_artifacts": artifacts,
    }
    manifest["derivation_sha256"] = sha256(canonical_bytes(manifest))

    if staging_dir.exists():
        raise FreezeOnlyError("STAGING_ALREADY_EXISTS", str(staging_dir))
    staging_dir.mkdir(parents=True, exist_ok=False)
    for name, data in payloads:
        target = _confined(staging_dir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (staging_dir / "freeze_manifest.json").write_bytes(canonical_bytes(manifest))
    return manifest


def verify_freeze(root: Path, manifest: dict[str, Any], staging_dir: Path) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise FreezeOnlyError("FREEZE_MANIFEST_SCHEMA_INVALID")
    signed = {key: value for key, value in manifest.items() if key != "derivation_sha256"}
    if manifest.get("derivation_sha256") != sha256(canonical_bytes(signed)):
        raise FreezeOnlyError("FREEZE_MANIFEST_DERIVATION_MISMATCH")
    root = Path(root)
    loaded: dict[str, tuple[bytes, list[dict[str, Any]]]] = {}
    for name, meta in manifest.get("inputs", {}).items():
        data, rows = _read_jsonl(root, meta["path"])
        if _metadata(meta["path"], data, rows) != meta:
            raise FreezeOnlyError("FREEZE_INPUT_IDENTITY_MISMATCH", name)
        loaded[name] = (data, rows)
    if set(loaded) != set(REQUIRED_LEDGERS):
        raise FreezeOnlyError("PRODUCTION_INPUT_SET_INVALID")
    cutoff_at = manifest.get("cutoff_at")
    cutoff = _parse_time(cutoff_at, "cutoff_at")
    docs_data, doc_rows = loaded["docs_ledger"]
    _validate_docs_trust(docs_data, doc_rows, manifest.get("docs_trust"), True)
    docs = _unique(doc_rows, "doc_id", "docs_ledger")
    extractions = _unique(loaded["ai_extractions"][1], "extraction_id", "ai_extractions")
    values = _unique(loaded["ai_field_values"][1], "value_id", "ai_field_values")
    _validate_dependencies(docs, extractions, values)
    if manifest.get("dependencies") != {
            "documents": len(docs), "extractions": len(extractions), "field_values": len(values)}:
        raise FreezeOnlyError("FREEZE_DEPENDENCY_SUMMARY_MISMATCH")
    effective: dict[str, list[dict[str, Any]]] = {}
    for ledger in EVENT_LEDGERS:
        data, rows = loaded[ledger]
        selected, prefix = _event_prefix(data, rows, cutoff, ledger)
        recorded = (manifest.get("event_prefixes") or {}).get(ledger)
        if not isinstance(recorded, dict):
            raise FreezeOnlyError("HISTORICAL_PREFIX_PROVENANCE_UNVERIFIED", ledger)
        witness = _validate_witness(ledger, sha256(data), prefix, cutoff_at,
                                    recorded.get("historical_witness"), True)
        expected = dict(prefix, historical_witness=witness)
        if recorded != expected:
            raise FreezeOnlyError("FREEZE_EVENT_PREFIX_MISMATCH", ledger)
        effective[ledger] = selected
    _validate_voids(effective["ai_field_value_voids"], values)
    _validate_reviews(effective["ai_reviews"], values, extractions)
    expected_artifacts = []
    for doc_id, doc in sorted(docs.items()):
        item = {"doc_id": doc_id}
        for field in ("raw", "text"):
            meta = _artifact_meta(root, doc, doc_id, field)
            meta.pop("payload")
            item[field] = dict(meta, staging_path=f"artifacts/{doc_id}/{field}.bin")
        expected_artifacts.append(item)
    if manifest.get("document_artifacts") != expected_artifacts:
        raise FreezeOnlyError("FREEZE_ARTIFACT_MANIFEST_MISMATCH")
    for item in manifest.get("document_artifacts", []):
        for field in ("raw", "text"):
            meta = item[field]
            source = _confined(Path(root), meta["source_path"]).read_bytes()
            staged = _confined(Path(staging_dir), meta["staging_path"]).read_bytes()
            if source != staged or sha256(staged) != meta["sha256"] or len(staged) != meta["byte_length"]:
                raise FreezeOnlyError("FREEZE_ARTIFACT_IDENTITY_MISMATCH", f"{item['doc_id']}:{field}")
