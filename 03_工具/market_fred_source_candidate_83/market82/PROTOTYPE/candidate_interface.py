#!/usr/bin/env python3
"""Default-disabled source-semantics validation interface candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from hardened_currentness_validator import validate_receipt


CONFIG_SHA256 = "536a412bae8dca767922c7c017dbc5776863f04f419af8e7aa778f17d5d26c94"


def run_candidate(receipt_path: Path, evidence_root: Path, enable_synthetic_validation: bool = False) -> dict:
    config_path = Path(__file__).resolve().parents[1] / "CONTRACTS" / "candidate_config.json"
    raw = config_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != CONFIG_SHA256:
        return {"status": "FAIL_CLOSED", "code": "CANDIDATE_CONFIG_HASH_MISMATCH", "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE"}
    config = json.loads(raw.decode("utf-8"))
    if config.get("enabled") is not True and not enable_synthetic_validation:
        return {
            "status": "CANDIDATE_DISABLED",
            "code": "EXPLICIT_SYNTHETIC_ENABLE_REQUIRED",
            "candidate_id": config.get("candidate_id"),
            "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
            "formal_publish_capability": "DISABLED",
        }
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    result = validate_receipt(receipt, evidence_root)
    result["interface_status"] = "SYNTHETIC_VALIDATION_ONLY"
    result["formal_publish_status"] = "FORMAL_PUBLISH_NOT_ELIGIBLE"
    result["candidate_formal_publish_capability"] = "DISABLED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--enable-synthetic-validation", action="store_true")
    args = parser.parse_args()
    result = run_candidate(args.input, args.evidence_root, args.enable_synthetic_validation)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("validator_status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
