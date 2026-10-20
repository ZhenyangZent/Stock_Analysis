"""T36 canonical staging-first writes.

The candidate file is written and validated before the canonical target is
changed.  A successful commit uses exactly the candidate bytes.  If commit or
audit recording fails, the canonical target is restored to its byte-identical
prefix.  This module does not weaken the project lock or append-only rules.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path

from common import ROOT, file_lock

AUDIT_DIR = os.path.join(ROOT, "01_共用資料", "data_spine")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_line(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return b""


def _replace_with_retry(source: str, target: str, timeout: float = 2.0) -> None:
    """Preserve atomic replace semantics across transient Windows sharing locks."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def _inside_project(path: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(ROOT)]) == os.path.abspath(ROOT)
    except ValueError:
        return False


def validate_jsonl_bytes(data: bytes) -> dict:
    rows = 0
    for number, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"STAGING_JSONL_INVALID_LINE:{number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"STAGING_JSONL_NOT_OBJECT:{number}")
        rows += 1
    return {"format": "JSONL", "row_count": rows}


def validate_csv_bytes(data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("STAGING_CSV_INVALID") from exc
    if data and (not rows or not rows[0]):
        raise ValueError("STAGING_CSV_HEADER_MISSING")
    width = len(rows[0]) if rows else 0
    if any(len(row) != width for row in rows[1:]):
        raise ValueError("STAGING_CSV_WIDTH_MISMATCH")
    return {"format": "CSV", "row_count": max(0, len(rows) - 1), "column_count": width}


def _write_stage(path: str, candidate: bytes) -> str:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, stage = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".staging.", dir=folder)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(candidate); handle.flush(); os.fsync(handle.fileno())
    except Exception:
        try: os.unlink(stage)
        except OSError: pass
        raise
    return stage


def validate_candidate(path: str, candidate: bytes, *, payload: bytes = b"", writer: str,
                       validator=None, extra: dict | None = None) -> dict:
    """Write and validate a disposable candidate; canonical target is untouched."""
    before = _read(path)
    if payload and candidate != before + payload:
        raise ValueError("STAGING_APPEND_PREFIX_GATE_FAILED")
    stage = _write_stage(path, candidate)
    try:
        if Path(stage).read_bytes() != candidate:
            raise ValueError("STAGING_BYTE_IDENTITY_GATE_FAILED")
        detail = (validator or (lambda _: {}))(candidate) or {}
        report = {
            "schema_version": "STAGING_WRITE_REPORT_1", "writer": writer,
            "target": os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")
                      if _inside_project(path)
                      else os.path.abspath(path),
            "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "before_sha256": sha256(before), "before_length": len(before),
            "payload_sha256": sha256(payload), "payload_length": len(payload),
            "candidate_sha256": sha256(candidate), "candidate_length": len(candidate),
            "gates": {"prefix": "PASS" if not payload or candidate == before + payload else "FAIL",
                      "byte_identity": "PASS", "validator": "PASS"},
            "validation": detail, "extra": extra or {},
        }
        report["validation_report_sha256"] = sha256(canonical_line(report))
        return report
    finally:
        try: os.unlink(stage)
        except OSError: pass


def _audit_path_for(target: str) -> str:
    import lock as project_lock
    scope = project_lock.scope_for_path(target) or "misc"
    return os.path.join(AUDIT_DIR, f"staging_write_audit_{scope}.jsonl")


def _append_audit(report: dict, target: str, lock_holder=None):
    """Internal append-only audit. Failure is propagated so caller can roll back."""
    import lock as project_lock
    audit_path = _audit_path_for(target)
    project_lock.require_holder_for_path(lock_holder, audit_path)
    row = dict(report)
    row["committed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = canonical_line(row)
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    with file_lock(audit_path):
        with open(audit_path, "ab") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())


def commit_candidate(path: str, candidate: bytes, *, payload: bytes = b"", writer: str,
                     validator=None, lock_holder=None, append_only=True, extra=None,
                     record_audit=True) -> dict:
    """Validate candidate first, then commit exactly those bytes or leave target unchanged."""
    import lock as project_lock
    project_lock.require_holder_for_path(lock_holder, path)
    before = _read(path)
    report = validate_candidate(path, candidate, payload=payload, writer=writer,
                                validator=validator, extra=extra)
    if append_only and not candidate.startswith(before):
        raise ValueError("STAGING_APPEND_ONLY_PREFIX_GATE_FAILED")
    with file_lock(path):
        current = _read(path)
        if current != before:
            raise ValueError("STAGING_COMPARE_AND_SWAP_GATE_FAILED")
        stage = _write_stage(path, candidate)
        try:
            _replace_with_retry(stage, path)
            if _read(path) != candidate:
                raise ValueError("STAGING_COMMIT_BYTE_IDENTITY_GATE_FAILED")
            if record_audit and _inside_project(path) and not os.path.basename(path).startswith("staging_write_audit_"):
                _append_audit(report, path, lock_holder=lock_holder)
        except Exception:
            # Exact rollback is safe because CAS proved the previous bytes immediately before commit.
            rollback = _write_stage(path, before)
            _replace_with_retry(rollback, path)
            raise
        finally:
            try: os.unlink(stage)
            except OSError: pass
    return report


def append_bytes(path: str, payload: bytes, *, writer: str, validator=None,
                 lock_holder=None, extra=None, record_audit=True) -> dict:
    before = _read(path)
    return commit_candidate(path, before + payload, payload=payload, writer=writer,
                            validator=validator, lock_holder=lock_holder,
                            append_only=True, extra=extra, record_audit=record_audit)

