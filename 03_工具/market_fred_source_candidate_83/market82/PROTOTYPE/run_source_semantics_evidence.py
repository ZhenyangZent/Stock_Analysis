from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
FIXTURE = PAYLOAD / "FIXTURES" / "complete_current_market_only.json"
EVIDENCE = PAYLOAD / "EVIDENCE_BYTES"
OUT = PAYLOAD / "EVIDENCE"
TMP = PAYLOAD.parent / ".evidence_tmp"
sys.path.insert(0, str(PAYLOAD / "PROTOTYPE"))

from hardened_currentness_validator import SEMANTIC_FIELDS, canonical_json_sha256, validate_receipt  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def attack(source_id: str, observation_mutator=None, raw_mutator=None, adapter_id=None) -> dict:
    TMP.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as name:
        root = Path(name) / "evidence"
        shutil.copytree(EVIDENCE, root)
        receipt = json.loads(FIXTURE.read_text(encoding="utf-8"))
        indicator = next(row for row in receipt["indicators"] if row["source_id"] == source_id)
        if observation_mutator:
            observation_mutator(indicator["observations"])
        raw_path = root / "raw_sources" / f"{source_id}.json"
        raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if raw_mutator:
            raw_mutator(raw_payload["records"])
            write_json(raw_path, raw_payload)
        raw_bytes = raw_path.read_bytes()
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        for row in indicator["observations"]:
            row["source_payload_sha256"] = raw_sha
        sr_path = root / "source_receipts" / f"{source_id}.json"
        source_receipt = json.loads(sr_path.read_text(encoding="utf-8"))
        source_receipt["response_artifact"] = {"path": f"raw_sources/{source_id}.json", "sha256": raw_sha, "bytes": len(raw_bytes)}
        source_receipt["canonical_source_rowset_sha256"] = canonical_json_sha256(raw_payload["records"])
        source_receipt["rowset_sha256"] = canonical_json_sha256(indicator["observations"])
        source_receipt["requested_items"] = len(raw_payload["records"])
        source_receipt["succeeded_items"] = len(raw_payload["records"])
        if adapter_id:
            source_receipt["adapter_id"] = adapter_id
        write_json(sr_path, source_receipt)
        sr_bytes = sr_path.read_bytes()
        ref = next(row for row in receipt["source_receipt_refs"] if row["source_id"] == source_id)
        ref.update(bytes=len(sr_bytes), sha256=hashlib.sha256(sr_bytes).hexdigest())
        return validate_receipt(receipt, root)


def main() -> int:
    receipt = json.loads(FIXTURE.read_text(encoding="utf-8"))
    identities = []
    for indicator in receipt["indicators"]:
        source_id = indicator["source_id"]
        raw_path = EVIDENCE / "raw_sources" / f"{source_id}.json"
        source_receipt_path = EVIDENCE / "source_receipts" / f"{source_id}.json"
        raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
        derived_rows = [{field: row[field] for field in SEMANTIC_FIELDS} for row in raw_payload["records"]]
        identities.append({
            "indicator_id": indicator["indicator_id"],
            "source_id": source_id,
            "adapter_id": raw_payload["adapter_id"],
            "raw_payload_sha256": sha(raw_path),
            "raw_payload_bytes": raw_path.stat().st_size,
            "record_count": len(derived_rows),
            "observation_keys": [row["observation_key"] for row in derived_rows],
            "units": sorted({row["unit"] for row in derived_rows}),
            "vintage_policies": sorted({row["vintage_policy"] for row in derived_rows}),
            "derived_canonical_source_rowset_sha256": canonical_json_sha256(derived_rows),
            "source_receipt_declared_canonical_rowset_sha256": source_receipt["canonical_source_rowset_sha256"],
            "source_receipt_sha256": sha(source_receipt_path),
        })
    write_json(OUT / "SOURCE_ROWSET_IDENTITIES.json", {"semantic_fields": list(SEMANTIC_FIELDS), "sources": identities})

    cases = {
        "value_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows[0].update(value=12345.6789)),
        "date_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows[0].update(observation_date="2026-02-18")),
        "source_record_missing_rehashed": attack("FRED_CPILFESL_REVISED", raw_mutator=lambda rows: rows.pop(2)),
        "vintage_tamper_rehashed": attack("FRED_UNRATE_REVISED", lambda rows: rows[0].update(vintage_policy="N/A")),
        "unit_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows[0].update(unit="BASIS_POINTS")),
        "source_id_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows[0].update(source_id="FRED_BRENT")),
        "calendar_key_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows[0].update(calendar_key="2026-01")),
        "observation_count_tamper_rehashed": attack("FRED_DGS10", lambda rows: rows.append(copy.deepcopy(rows[0]))),
        "unsupported_real_adapter": attack("FRED_DGS10", adapter_id="FRED_CSV_REAL"),
    }
    output = {
        "case_count": len(cases),
        "all_rejected": all(result["validator_status"] == "FAIL_CLOSED" and result["data_currentness_status"] == "DATA_INSUFFICIENT" for result in cases.values()),
        "cases": cases,
    }
    write_json(OUT / "SEMANTIC_ADVERSARIAL_RESULTS.json", output)
    print(json.dumps({"case_count": len(cases), "all_rejected": output["all_rejected"], "source_identity_count": len(identities)}, sort_keys=True))
    return 0 if output["all_rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
