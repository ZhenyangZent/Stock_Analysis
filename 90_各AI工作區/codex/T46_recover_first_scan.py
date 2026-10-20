"""One-time recovery for the first T46 scan's historical-backfill bug."""
import hashlib, json, os, shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "03_工具"))
from staging_write import commit_candidate, validate_jsonl_bytes

LEDGER = ROOT / "01_共用資料" / "data_spine" / "docs_ledger.jsonl"
FETCHED_AT = "2026-10-20T05:02:34.006977+00:00"
PREFIX_LENGTH = 108430
PREFIX_SHA = "e5d5e81a1859a0b4a708c9880428c1353be51b74820451ac13d127219acd87b9"
CURRENT_SHA = "0b7f5242ad52557b6a07f7b0abe4d6fcf9821b09589a997cd7a57563676f3b88"

data = LEDGER.read_bytes()
assert hashlib.sha256(data).hexdigest() == CURRENT_SHA, "CURRENT_LEDGER_CHANGED"
prefix = data[:PREFIX_LENGTH]
assert hashlib.sha256(prefix).hexdigest() == PREFIX_SHA, "PREFIX_MISMATCH"
all_rows = [json.loads(x) for x in data.decode("utf-8").splitlines() if x.strip()]
prefix_rows = [json.loads(x) for x in prefix.decode("utf-8").splitlines() if x.strip()]
suffix = all_rows[len(prefix_rows):]
assert len(suffix) == 599, len(suffix)
assert all(row.get("fetched_at") == FETCHED_AT for row in suffix), "NON_T46_SUFFIX_ROW"

quarantine = ROOT / "90_各AI工作區" / "codex" / "T46_first_scan_quarantine"
quarantine.mkdir(parents=True, exist_ok=True)
moved = []
for row in suffix:
    for key in ("raw_path", "text_path"):
        source = ROOT / row[key]
        if source.exists():
            target = quarantine / row["issuer_id"] / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append({"doc_id": row["doc_id"], "kind": key, "from": row[key],
                          "to": target.relative_to(ROOT).as_posix()})

write = commit_candidate(str(LEDGER), prefix, writer="T46.first_scan_controlled_recovery",
                         validator=validate_jsonl_bytes, lock_holder="codex_t46", append_only=False,
                         extra={"reason": "FIRST_SCAN_HISTORICAL_BACKFILL_BUG", "removed_suffix_rows": len(suffix),
                                "quarantined_files": len(moved), "approved_prefix_sha256": PREFIX_SHA})
report = {"status": "PASS", "restored_prefix_sha256": hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
          "restored_row_count": len(prefix_rows), "removed_suffix_rows": len(suffix),
          "quarantined_file_count": len(moved), "quarantine": quarantine.relative_to(ROOT).as_posix(),
          "write_report": write}
(ROOT / "90_各AI工作區" / "codex" / "T46_first_scan_recovery_report.json").write_text(
    json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
