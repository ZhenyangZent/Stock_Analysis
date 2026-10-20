"""Verify S1A-derived values before they can enter verdict scoring.

The run may name a committed snapshot, but it cannot choose an arbitrary
directory.  Verification always resolves the run id below the project's
canonical snapshot root and recomputes the snapshot from its hash-bound CAS
inputs before comparing the requested value.
"""
from __future__ import annotations

import os
import re
import json
from pathlib import Path

from common import ROOT
from data_spine import parse_time, resolve_security

SNAPSHOT_ROOT = Path(ROOT) / "02_執行紀錄" / "影子快照"
ADAPTER = "S1A_ADAPTER_V1"
COMPANY_RE = re.compile(r"^companies/([^/]+)/trend/(C[1-6])$")
MARKET_RE = re.compile(r"^market/values/(M[1-7])$")
THEME_RE = re.compile(r"^themes/([^/]+)/I1$")
VALUATION_RE = re.compile(r"^companies/([^/]+)/valuation/V1$")
_REPLAY_CACHE = {}
_PRECOMMIT_REPLAY_CACHE = {}


def _fail(reason):
    return False, "DERIVED_SOURCE_MISMATCH:" + reason


def _contract(rules, layer):
    verdict = rules.get("verdict") or {}
    expanded = verdict.get("computed_fields_accepted")
    if isinstance(expanded, dict):
        item = expanded.get(layer)
        return item if isinstance(item, dict) else None, True
    legacy = verdict.get("computed_revenue_accepted")
    return (legacy if layer == "company" and isinstance(legacy, dict) else None), False


def computed_snapshot_consistency(run, rules):
    if not isinstance((rules.get("verdict") or {}).get("computed_fields_accepted"), dict):
        return True, None
    refs = set()
    groups = [run.get("market") or {}] + list((run.get("themes") or {}).values())
    for company in run.get("companies") or []:
        groups += [company.get("fields") or {}, company.get("valuation") or {}]
    for fields in groups:
        for field in fields.values():
            if isinstance(field, dict) and field.get("status") in {"OFFICIAL_FILING_COMPUTED", "PUBLIC_DATA_COMPUTED"}:
                p = field.get("provenance") or {}
                refs.add((p.get("snapshot_run_id"), p.get("snapshot_sha256")))
    return (True, None) if len(refs) <= 1 else (False, "DERIVED_SOURCE_MISMATCH:COMPUTED_FIELDS_SNAPSHOT_MISMATCH")


def _latest_committed(root, cutoff, rs):
    candidates = []
    for path in Path(root).glob("SNAP_*"):
        if path.is_dir() and (path / "COMMITTED.json").exists():
            marker, _, manifest, _ = rs.read_committed(path)
            value = parse_time(manifest.get("data_cutoff_at"), "snapshot.data_cutoff_at")
            if value <= cutoff: candidates.append((value, marker.get("run_id", path.name)))
    return max(candidates)[1] if candidates else None


def _canonical_prefix_gate(input_manifest, current, rs):
    for name in rs.APPEND_INPUTS:
        meta = (input_manifest.get("inputs") or {}).get(name)
        if meta is None: continue
        live = current.get(name)
        n = int(meta.get("prefix_byte_length", meta.get("byte_length", 0)))
        if live is None or len(live) < n or rs.digest(live[:n]) != meta.get("prefix_sha256", meta.get("sha256")):
            return False
    return True


def _precommit_replay(run_id, snapshot_sha, root, run_cutoff, current, rules_archive_path, rs):
    """Replay an explicitly bound, uncommitted comparison candidate.

    The context is carried only inside the frozen canonical-input mapping used
    by validate().  It is never written to a formal run.  A caller that merely
    invents a SNAP_* id therefore fails closed instead of degrading every
    computed field to an ordinary provenance error.
    """
    context = (current or {}).get("__precommit_candidate__")
    if not isinstance(context, dict):
        raise RuntimeError("PROVENANCE_UNVERIFIABLE_PRECOMMIT")
    if context.get("run_id") != run_id or context.get("snapshot_sha256") != snapshot_sha:
        raise RuntimeError("PROVENANCE_UNVERIFIABLE_PRECOMMIT")
    snapshot = context.get("snapshot")
    captured = context.get("captured")
    input_manifest = context.get("input_manifest")
    if not isinstance(snapshot, dict) or not isinstance(captured, dict) or not isinstance(input_manifest, dict):
        raise RuntimeError("PROVENANCE_UNVERIFIABLE_PRECOMMIT")
    if rs.digest(rs.canonical_bytes(snapshot)) != snapshot_sha:
        raise RuntimeError("PRECOMMIT_SNAPSHOT_HASH_MISMATCH")
    for name, meta in (input_manifest.get("inputs") or {}).items():
        data = captured.get(name)
        if not isinstance(data, bytes) or rs.digest(data) != meta.get("sha256") or len(data) != int(meta.get("byte_length", -1)):
            raise RuntimeError("PRECOMMIT_INPUT_HASH_MISMATCH")
    if not _canonical_prefix_gate(input_manifest, current, rs):
        raise RuntimeError("INPUT_NOT_CANONICAL_PREFIX")
    protocol = json.loads(captured["rules"]).get("protocol_version")
    expected_rules = (input_manifest.get("inputs", {}).get("rules") or {}).get("sha256")
    if (context.get("rules_version") != protocol
            or context.get("rules_sha256") != expected_rules
            or rs.digest(captured["rules"]) != expected_rules):
        raise RuntimeError("RULES_VERSION_MISMATCH")
    if rules_archive_path:
        archive = Path(rules_archive_path)
        if not archive.exists() or rs.digest(archive.read_bytes()) != expected_rules:
            raise RuntimeError("RULES_VERSION_MISMATCH")
    cutoff = parse_time(input_manifest.get("data_cutoff_at"), "snapshot.data_cutoff_at")
    if cutoff > run_cutoff:
        raise RuntimeError("SNAPSHOT_AFTER_RUN_CUTOFF")
    latest_id = _latest_committed(root, run_cutoff, rs)
    if latest_id:
        latest_dir = Path(root) / latest_id
        _, _, latest_manifest, _ = rs.read_committed(latest_dir)
        latest_cutoff = parse_time(latest_manifest.get("data_cutoff_at"), "snapshot.data_cutoff_at")
        if cutoff < latest_cutoff:
            raise RuntimeError("SNAPSHOT_NOT_LATEST")
    cache_key = (run_id, snapshot_sha, expected_rules)
    replayed = _PRECOMMIT_REPLAY_CACHE.get(cache_key)
    if replayed is None:
        replayed = rs.build_snapshot(captured, cutoff)
        if rs.digest(rs.canonical_bytes(replayed)) != snapshot_sha:
            raise RuntimeError("PRECOMMIT_REPLAY_HASH_MISMATCH")
        _PRECOMMIT_REPLAY_CACHE[cache_key] = replayed
    return replayed, snapshot, input_manifest


def verify_computed_field(run, rules, company, field_name, field, snapshot_root=None, registry=None,
                          layer="company", theme_id=None, current_inputs=None, rules_archive_path=None):
    """Verify one contracted field; expanded contracts add strict provenance gates."""
    contract, strict = _contract(rules, layer)
    if not contract: return _fail("RULE_CONTRACT_MISSING")
    if (field_name not in contract.get("fields", [])
            or field.get("status") != contract.get("status_code")
            or contract.get("requires") != "VERIFIED_PROVENANCE"):
        return _fail("RULE_CONTRACT_REJECTED")

    provenance = field.get("provenance")
    if not isinstance(provenance, dict):
        return _fail("PROVENANCE_MISSING")
    if provenance.get("adapter") != ADAPTER:
        return _fail("ADAPTER_MISMATCH")
    run_id = provenance.get("snapshot_run_id")
    snapshot_sha = provenance.get("snapshot_sha256")
    path = provenance.get("field_path")
    if not isinstance(run_id, str) or not re.fullmatch(r"SNAP_[A-Za-z0-9_-]+", run_id):
        return _fail("SNAPSHOT_RUN_ID_INVALID")
    if layer in {"company", "valuation"}:
        pattern = COMPANY_RE if layer == "company" else VALUATION_RE
        match = pattern.fullmatch(str(path or ""))
        if not match or (layer == "company" and match.group(2) != field_name) or (layer == "valuation" and field_name != "V1"):
            return _fail("FIELD_PATH_INVALID")
        security, state = resolve_security((company or {}).get("ticker"), registry)
        if state != "OK" or not security: return _fail("SECURITY_ID_UNRESOLVED")
        security_id = security["security_id"]
        if match.group(1) != security_id: return _fail("SECURITY_ID_MISMATCH")
    elif layer == "market":
        match = MARKET_RE.fullmatch(str(path or ""))
        if not match or match.group(1) != field_name: return _fail("FIELD_PATH_INVALID")
    elif layer == "theme":
        match = THEME_RE.fullmatch(str(path or ""))
        if not match or match.group(1) != theme_id or field_name != "I1": return _fail("THEME_ID_MISMATCH")
    else:
        return _fail("LAYER_INVALID")

    root = Path(snapshot_root) if snapshot_root is not None else SNAPSHOT_ROOT
    run_dir = root / run_id
    try:
        # Lazy import avoids pulling the snapshot engine into ordinary legacy
        # validation unless a computed field is actually present.
        import research_snapshot as rs
        run_cutoff = parse_time(run.get("run_cutoff_at"), "run_cutoff_at")
        current = current_inputs if current_inputs is not None else rs.capture_inputs()
        if not (run_dir / "COMMITTED.json").exists():
            if not strict:
                return _fail("PROVENANCE_UNVERIFIABLE_PRECOMMIT")
            replayed, committed_snapshot, input_manifest = _precommit_replay(
                run_id, snapshot_sha, root, run_cutoff, current, rules_archive_path, rs)
            marker = {"run_id": run_id, "snapshot_sha256": snapshot_sha}
            precommit = True
        else:
            marker, _manifest, input_manifest, committed_snapshot = rs.read_committed(run_dir)
            precommit = False
        if marker.get("run_id") != run_id:
            return _fail("COMMITTED_RUN_ID_MISMATCH")
        if not snapshot_sha or marker.get("snapshot_sha256") != snapshot_sha:
            return _fail("COMMITTED_SNAPSHOT_HASH_MISMATCH")
        expected_rules = (input_manifest.get("inputs", {}).get("rules") or {}).get("sha256")
        if precommit:
            if (provenance.get("rules_version") != run.get("protocol_version")
                    or provenance.get("rules_sha256") != expected_rules):
                return _fail("PROVENANCE_RULES_BINDING_MISMATCH")
        elif provenance.get("rules_version") is not None or provenance.get("rules_sha256") is not None:
            if (provenance.get("rules_version") != run.get("protocol_version")
                    or provenance.get("rules_sha256") != expected_rules):
                return _fail("PROVENANCE_RULES_BINDING_MISMATCH")
        if not precommit and rs.digest((run_dir / "snapshot.json").read_bytes()) != snapshot_sha:
            return _fail("SNAPSHOT_BYTES_HASH_MISMATCH")
        if strict and not precommit:
            if _latest_committed(root, run_cutoff, rs) != run_id: return _fail("SNAPSHOT_NOT_LATEST")
            archive = (Path(rules_archive_path) if rules_archive_path else
                       Path(ROOT) / "00_規範" / "rules_歷史" / f"rules_{run.get('protocol_version')}.json")
            expected_rules = (input_manifest.get("inputs", {}).get("rules") or {}).get("sha256")
            if not archive.exists() or rs.digest(archive.read_bytes()) != expected_rules:
                return _fail("RULES_VERSION_MISMATCH")
            if not _canonical_prefix_gate(input_manifest, current, rs): return _fail("INPUT_NOT_CANONICAL_PREFIX")
        if not precommit:
            cache_key = (str(root.resolve()), run_id, snapshot_sha, bool(strict))
            replayed = _REPLAY_CACHE.get(cache_key)
            if replayed is None:
                replayed = rs.replay_snapshot(input_manifest, root / "_cas", current=current)
                _REPLAY_CACHE[cache_key] = replayed
    except Exception as exc:
        reason = str(exc)
        if reason == "PROVENANCE_UNVERIFIABLE_PRECOMMIT":
            return _fail(reason)
        return _fail("REPLAY_FAILED:" + (reason or type(exc).__name__))

    replay_cutoff = replayed.get("data_cutoff_at") or committed_snapshot.get("data_cutoff_at")
    try:
        if parse_time(replay_cutoff, "snapshot.data_cutoff_at") > parse_time(run.get("run_cutoff_at"), "run_cutoff_at"):
            return _fail("SNAPSHOT_AFTER_RUN_CUTOFF")
    except ValueError:
        return _fail("CUTOFF_INVALID")

    if layer in {"company", "valuation"}:
        target = next((c for c in replayed.get("companies", []) if c.get("security_id") == security_id), None)
        if target is None: return _fail("SECURITY_NOT_IN_REPLAY")
        replay_value = ((target.get("trend") or {}).get(field_name) if layer == "company"
                        else (target.get("valuation_v1") or {}).get("value"))
    elif layer == "market":
        replay_value = (((replayed.get("market") or {}).get("values") or {}).get(field_name) or {}).get("value")
    else:
        replay_value = ((replayed.get("theme_breadth") or {}).get(theme_id) or {}).get("value")
    if replay_value != field.get("value"):
        return _fail("REPLAY_VALUE_MISMATCH")
    return True, "VERIFIED_PROVENANCE"
