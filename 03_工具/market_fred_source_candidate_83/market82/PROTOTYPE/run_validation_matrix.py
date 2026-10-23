from __future__ import annotations

import json
import sys
from pathlib import Path


PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD / "PROTOTYPE"))

from hardened_currentness_validator import validate_receipt  # noqa: E402


def main() -> int:
    fixture_dir = PAYLOAD / "FIXTURES"
    evidence_root = PAYLOAD / "EVIDENCE_BYTES"
    rows = []
    full_results = {}
    for path in sorted(fixture_dir.glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        result = validate_receipt(receipt, evidence_root)
        full_results[path.name] = result
        rows.append(
            {
                "fixture": path.name,
                "validator_status": result["validator_status"],
                "data_currentness_status": result["data_currentness_status"],
                "code_integration_status": result["code_integration_status"],
                "pit_vintage_status": result["pit_vintage_status"],
                "formal_publish_status": result["formal_publish_status"],
                "error_codes": sorted({item["code"] for item in result["errors"]}),
                "warning_codes": sorted({item["code"] for item in result["warnings"]}),
            }
        )
    report = PAYLOAD / "REPORT"
    report.mkdir(exist_ok=True)
    (report / "VALIDATION_MATRIX.json").write_text(
        json.dumps({"fixture_count": len(rows), "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (PAYLOAD / "EVIDENCE" / "FULL_VALIDATION_RESULTS.json").write_text(
        json.dumps(full_results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"fixture_count": len(rows), "pass_count": sum(row["validator_status"] == "PASS" for row in rows), "fail_closed_count": sum(row["validator_status"] == "FAIL_CLOSED" for row in rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
