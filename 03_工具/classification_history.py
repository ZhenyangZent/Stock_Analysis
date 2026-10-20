"""Append-only formal-snapshot classification history (T36 C).

This ledger is research evaluation evidence only.  It never feeds verdicts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from common import ROOT
from staging_write import append_bytes, validate_jsonl_bytes

SNAP_ROOT = Path(ROOT) / "02_執行紀錄" / "影子快照"
LEDGER = Path(ROOT) / "04_回測" / "classification_history.jsonl"


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _existing(path=LEDGER):
    if not Path(path).exists(): return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def build_record(folder: Path) -> dict:
    marker = _read_json(folder / "COMMITTED.json")
    manifest_path = folder / "manifest.json"
    if _sha(manifest_path) != marker.get("manifest_sha256"):
        raise ValueError("CLASSIFICATION_MANIFEST_HASH_MISMATCH")
    manifest = _read_json(manifest_path)
    declared_snapshot = ((manifest.get("payloads") or {}).get("snapshot.json") or {}).get("sha256")
    if not declared_snapshot or declared_snapshot != marker.get("snapshot_sha256"):
        raise ValueError("CLASSIFICATION_SNAPSHOT_MANIFEST_BINDING_MISMATCH")
    snapshot_path = folder / "snapshot.json"
    if _sha(snapshot_path) != declared_snapshot:
        raise ValueError("CLASSIFICATION_SNAPSHOT_HASH_MISMATCH")
    snapshot = _read_json(snapshot_path)
    company_snapshot = {row["ticker"]: row for row in snapshot.get("companies", [])}
    shadow_marker_path = folder / "SHADOW_COMMITTED.json"
    shadow_manifest = _read_json(shadow_marker_path) if shadow_marker_path.exists() else {"payloads": {}}
    companies, shadow_files = [], []
    for market in ("TW", "US"):
        path = folder / f"shadow_verdict_{market}.json"
        if not path.exists(): continue
        declared_shadow = ((shadow_manifest.get("payloads") or {}).get(path.name) or {}).get("sha256")
        if not declared_shadow or _sha(path) != declared_shadow:
            raise ValueError("CLASSIFICATION_SHADOW_MANIFEST_HASH_MISMATCH:" + path.name)
        shadow_files.append({"file": path.name, "sha256": declared_shadow})
        shadow = _read_json(path)
        rows = {row["ticker"]: row for row in shadow.get("rows", [])}
        judged = {row["ticker"]: row for row in (shadow.get("judge") or {}).get("companies", [])}
        themes = (shadow.get("judge") or {}).get("themes") or {}
        market_result = (shadow.get("judge") or {}).get("market") or {}
        for ticker, item in sorted(rows.items()):
            score = judged.get(ticker, {}); snap = company_snapshot.get(ticker, {})
            theme = item.get("theme"); theme_result = themes.get(theme, {})
            companies.append({
                "ticker": ticker, "security_id": snap.get("security_id"), "market": market,
                "anchor_session": snap.get("anchor_session"), "theme": theme,
                "classification": item.get("shadow_verdict"),
                "controls": {"V1_ADVISORY": item.get("V1_ADVISORY"),
                             "company_score_min_1": item.get("company_score_min_1")},
                "scores": {"market": market_result.get("score"),
                           "theme": theme_result.get("score"),
                           "company": score.get("company_score"),
                           "valuation": score.get("valuation_score")},
                "market_light": market_result.get("light"),
                "V1": (snap.get("valuation_v1") or {}).get("value"),
            })
    status = "OK" if shadow_files else "LEGACY_NO_SHADOW_CLASSIFICATION"
    protocol_version = None
    for market in ("TW", "US"):
        path = folder / f"shadow_verdict_{market}.json"
        if path.exists():
            protocol_version = ((_read_json(path).get("judge") or {}).get("protocol_version"))
            if protocol_version: break
    record = {
        "record_type": "FORMAL_SNAPSHOT_CLASSIFICATION", "schema_version": "CLASSIFICATION_HISTORY_1",
        "snapshot_id": folder.name, "snapshot_sha256": declared_snapshot,
        "manifest_sha256": marker["manifest_sha256"],
        "snapshot_date": str(marker.get("published_at") or marker.get("committed_at"))[:10],
        "published_at": marker.get("published_at") or marker.get("committed_at"),
        "protocol_version": protocol_version,
        "classification_status": status, "market_light": (snapshot.get("market", {}).get("assessment") or {}).get("light"),
        "companies": companies, "shadow_files": shadow_files,
        "not_investment_advice": True,
    }
    identity = json.dumps({"snapshot_id": record["snapshot_id"], "snapshot_sha256": record["snapshot_sha256"]},
                          sort_keys=True, separators=(",", ":"))
    record["classification_id"] = "CLASS_" + hashlib.sha256(identity.encode()).hexdigest()
    return record


def append_snapshot(folder, ledger=LEDGER, lock_holder=None):
    record = build_record(Path(folder)); existing = _existing(ledger)
    by_snapshot = {row.get("snapshot_id"): row for row in existing}
    if record["snapshot_id"] in by_snapshot:
        if by_snapshot[record["snapshot_id"]].get("snapshot_sha256") != record["snapshot_sha256"]:
            raise ValueError("CLASSIFICATION_HISTORY_SNAPSHOT_COLLISION")
        return 0, by_snapshot[record["snapshot_id"]]
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    append_bytes(str(ledger), payload, writer="classification_history.append_snapshot",
                 validator=validate_jsonl_bytes, lock_holder=lock_holder,
                 extra={"snapshot_id": record["snapshot_id"], "company_count": len(record["companies"])})
    return 1, record


def backfill(snapshot_root=SNAP_ROOT, ledger=LEDGER, lock_holder=None):
    added, statuses = 0, []
    for folder in sorted(Path(snapshot_root).glob("SNAP_*")):
        if not (folder / "COMMITTED.json").exists(): continue
        n, record = append_snapshot(folder, ledger=ledger, lock_holder=lock_holder)
        added += n; statuses.append({"snapshot_id": folder.name, "status": record["classification_status"], "added": n})
    return {"added": added, "snapshots": statuses}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--snapshot")
    parser.add_argument("--lock-holder")
    args = parser.parse_args(argv)
    if args.backfill:
        result = backfill(lock_holder=args.lock_holder)
    elif args.snapshot:
        n, record = append_snapshot(args.snapshot, lock_holder=args.lock_holder)
        result = {"added": n, "snapshot_id": record["snapshot_id"]}
    else:
        parser.error("--backfill or --snapshot is required")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

