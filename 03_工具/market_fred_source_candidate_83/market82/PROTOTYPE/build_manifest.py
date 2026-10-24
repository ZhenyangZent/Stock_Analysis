#!/usr/bin/env python3
"""Build a deterministic SHA-256 inventory for this external handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.json"


def main() -> int:
    entries = []
    for path in sorted(ROOT.rglob("*"), key=lambda item: item.relative_to(ROOT).as_posix()):
        if not path.is_file() or path == MANIFEST:
            continue
        raw = path.read_bytes()
        entries.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    manifest = {
        "task_id": "MARKET-FRED-PAGINATION-INTEGRITY-82",
        "manifest_version": "1.0.0",
        "hash_algorithm": "SHA-256",
        "payload_count": len(entries),
        "entries": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"MANIFEST_PAYLOAD_COUNT={len(entries)}")
    print(f"MANIFEST_SHA256={hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
