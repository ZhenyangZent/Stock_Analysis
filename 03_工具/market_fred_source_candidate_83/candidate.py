#!/usr/bin/env python3
"""Default-disabled, offline-only wrapper for the exact MARKET-82 FRED adapter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "MARKET-FRED-DISABLED-REVIEW-CANDIDATE-83"
SUPPORTED_ADAPTER = "FRED_API_JSON_V1"
HERE = Path(__file__).resolve().parent
ADAPTER_PATH = HERE / "market82" / "PROTOTYPE" / "real_source_adapters.py"


class CandidateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _disabled_result() -> dict[str, Any]:
    return {
        "candidate_id": CANDIDATE_ID,
        "candidate_status": "CANDIDATE_DISABLED",
        "provider_format_status": "NOT_EVALUATED",
        "source_publication_available_status": "NOT_EVALUATED",
        "currentness_status": "DATA_INSUFFICIENT",
        "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
        "network_access": "DISABLED",
        "formal_market_data_write": "DISABLED",
        "currentness_activation": "DISABLED",
    }


def _read_bound_file(root: Path, item: dict[str, Any], label: str) -> bytes:
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        raise CandidateError("INVALID_FILE_BINDING", label)
    expected = item["sha256"]
    if not isinstance(expected, str) or len(expected) != 64:
        raise CandidateError("INVALID_SHA256_BINDING", label)
    path = (root / item["path"]).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CandidateError("SOURCE_PATH_OUTSIDE_ALLOWED_ROOT", label) from exc
    if not path.is_file():
        raise CandidateError("SOURCE_PATH_NOT_FILE", label)
    raw = path.read_bytes()
    if _sha256(raw) != expected.lower():
        raise CandidateError("SOURCE_BYTE_SHA256_MISMATCH", label)
    return raw


def _load_market82_adapter():
    spec = importlib.util.spec_from_file_location("_market82_fred_adapter_candidate_83", ADAPTER_PATH)
    if spec is None or spec.loader is None:
        raise CandidateError("ADAPTER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_candidate(bundle: dict[str, Any] | None = None, *, enabled: bool = False, allowed_root: str | Path | None = None) -> dict[str, Any]:
    """Validate hash-bound local bytes. This function cannot fetch or write market data."""
    if not enabled:
        return _disabled_result()
    if allowed_root is None:
        raise CandidateError("ALLOWED_ROOT_REQUIRED")
    root = Path(allowed_root).resolve(strict=True)
    if not root.is_dir():
        raise CandidateError("ALLOWED_ROOT_NOT_DIRECTORY")
    if not isinstance(bundle, dict):
        raise CandidateError("INVALID_BUNDLE")
    if bundle.get("adapter") != SUPPORTED_ADAPTER:
        raise CandidateError("UNSUPPORTED_ADAPTER_FAIL_CLOSED")
    contract_name = bundle.get("contract_name")
    if not isinstance(contract_name, str) or not contract_name:
        raise CandidateError("CONTRACT_NAME_REQUIRED")
    series_raw = _read_bound_file(root, bundle.get("series"), "series")
    page_specs = bundle.get("pages")
    if not isinstance(page_specs, list) or not page_specs:
        raise CandidateError("PAGES_REQUIRED")
    pages = []
    for index, page in enumerate(page_specs):
        if not isinstance(page, dict) or set(page) != {"request", "response"}:
            raise CandidateError("INVALID_PAGE_BINDING", str(index))
        request_raw = _read_bound_file(root, page["request"], f"page[{index}].request")
        response_raw = _read_bound_file(root, page["response"], f"page[{index}].response")
        try:
            request_parameters = json.loads(request_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateError("REQUEST_JSON_INVALID", str(index)) from exc
        if not isinstance(request_parameters, dict):
            raise CandidateError("REQUEST_JSON_NOT_OBJECT", str(index))
        pages.append({"raw_bytes": response_raw, "request_parameters": request_parameters})
    adapter = _load_market82_adapter()
    result = adapter.aggregate_fred_api_json_pages(series_raw, pages, contract_name)
    result.update({
        "candidate_id": CANDIDATE_ID,
        "candidate_status": "OFFLINE_VALIDATION_ONLY",
        "network_access": "DISABLED",
        "formal_market_data_write": "DISABLED",
        "currentness_activation": "DISABLED",
        "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE",
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-offline-validation", action="store_true")
    parser.add_argument("--bundle")
    parser.add_argument("--allowed-root")
    args = parser.parse_args()
    try:
        if not args.enable_offline_validation:
            result = run_candidate(enabled=False)
        else:
            if not args.bundle or not args.allowed_root:
                raise CandidateError("BUNDLE_AND_ALLOWED_ROOT_REQUIRED")
            bundle_path = Path(args.bundle).resolve(strict=True)
            root = Path(args.allowed_root).resolve(strict=True)
            try:
                bundle_path.relative_to(root)
            except ValueError as exc:
                raise CandidateError("BUNDLE_PATH_OUTSIDE_ALLOWED_ROOT") from exc
            result = run_candidate(json.loads(bundle_path.read_text(encoding="utf-8")), enabled=True, allowed_root=root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (CandidateError, FileNotFoundError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, CandidateError) else type(exc).__name__
        print(json.dumps({"candidate_id": CANDIDATE_ID, "candidate_status": "FAIL_CLOSED", "error_code": code,
                          "currentness_status": "DATA_INSUFFICIENT", "formal_publish_status": "FORMAL_PUBLISH_NOT_ELIGIBLE"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
