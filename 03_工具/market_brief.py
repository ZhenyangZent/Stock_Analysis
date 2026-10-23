#!/usr/bin/env python3
"""Render a verified COMMITTED research snapshot as a historical market brief.

This module deliberately does not import or call the scoring pipeline.  It only
validates and rearranges bytes that were already committed by
``research_snapshot.py``.  A successful integrity check is never presented as
evidence that the market signal predicts future returns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = PROJECT_ROOT / "02_執行紀錄" / "影子快照"
RUN_ID_RE = re.compile(r"SNAP_\d{8}T\d{6}Z_[0-9a-f]{8}")
REQUIRED_PAYLOADS = ("input_manifest.json", "research.md", "snapshot.json")
ALLOWED_MANIFEST_STATUS = frozenset({"OK", "SOURCE_STALE"})
INDICATORS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")
INDICATOR_NAMES = {
    "M1": "聯準會最近一次利率動作",
    "M2": "10 年期美債殖利率近 20 交易日變化",
    "M3": "布蘭特原油近 20 交易日變化",
    "M4": "失業率近 3 個月變化",
    "M5": "核心 CPI 年增率近 3 個月變化",
    "M6": "SOXX 相對 200 日均線",
    "M7": "高收益債信用利差近 20 交易日變化",
}
INDICATOR_UNITS = {
    "M1": "選項",
    "M2": "基點",
    "M3": "%",
    "M4": "百分點",
    "M5": "百分點",
    "M6": "%",
    "M7": "基點",
}
FRESHNESS_DAYS_AT_CUTOFF = {"M2": 7, "M3": 12, "M4": 75, "M5": 75, "M6": 7, "M7": 7}
T46_FIELDS = ("C5", "C6", "I2")


class BriefError(RuntimeError):
    """Fail-closed validation error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class VerifiedSnapshot:
    run_dir: Path
    committed: Mapping[str, Any]
    manifest: Mapping[str, Any]
    snapshot: Mapping[str, Any]
    input_manifest: Mapping[str, Any]
    research_text: str
    hashes: Mapping[str, str]
    byte_lengths: Mapping[str, int]
    rules_version: str
    rules_sha256: str
    current_rules_version: str
    current_rules_sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_object(data: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BriefError("INVALID_JSON", f"{name}: {exc}") from exc
    if not isinstance(value, dict):
        raise BriefError("INVALID_JSON_OBJECT", name)
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise BriefError("INVALID_TIMESTAMP", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BriefError("INVALID_TIMESTAMP", f"{field}: {value}") from exc
    if parsed.tzinfo is None:
        raise BriefError("TIMESTAMP_TIMEZONE_REQUIRED", field)
    return parsed


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise BriefError("INVALID_DATE", field)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BriefError("INVALID_DATE", f"{field}: {value}") from exc


def _required_string(obj: Mapping[str, Any], key: str, owner: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise BriefError("REQUIRED_FIELD_MISSING", f"{owner}.{key}")
    return value


def _validate_run_path(snapshot_root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise BriefError("RUN_ID_FORMAT_GATE", repr(run_id))
    root = snapshot_root.resolve(strict=True)
    run_dir = (root / run_id).resolve(strict=True)
    if run_dir.parent != root or run_dir.name != run_id or not run_dir.is_dir():
        raise BriefError("RUN_PATH_CONTAINMENT_GATE", str(run_dir))
    return run_dir


def _read_required(run_dir: Path, name: str) -> bytes:
    path = run_dir / name
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BriefError("REQUIRED_FILE_MISSING", name) from exc
    if resolved.parent != run_dir or not resolved.is_file():
        raise BriefError("PAYLOAD_PATH_CONTAINMENT_GATE", name)
    return resolved.read_bytes()


def _read_project_file(project_root: Path, relative: Path) -> bytes:
    root = project_root.resolve(strict=True)
    try:
        resolved = (root / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise BriefError("PROJECT_FILE_MISSING", relative.as_posix()) from exc
    if root not in resolved.parents or not resolved.is_file():
        raise BriefError("PROJECT_PATH_CONTAINMENT_GATE", relative.as_posix())
    return resolved.read_bytes()


def _resolve_rules(project_root: Path, expected_sha: str, expected_length: int | None) -> tuple[str, str]:
    candidates = [project_root / "00_規範" / "rules.json"]
    history = project_root / "00_規範" / "rules_歷史"
    if history.is_dir():
        candidates.extend(sorted(history.glob("rules_*.json")))
    matches: list[tuple[str, str]] = []
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if sha256(raw) != expected_sha:
            continue
        if expected_length is not None and len(raw) != expected_length:
            raise BriefError("RULES_BYTE_LENGTH_GATE", path.name)
        rules = _json_object(raw, str(path))
        version = _required_string(rules, "protocol_version", path.name)
        matches.append((version, sha256(raw)))
    identities = sorted(set(matches))
    if not identities:
        raise BriefError("RULES_ARCHIVE_HASH_GATE", expected_sha)
    if len(identities) != 1:
        raise BriefError("RULES_IDENTITY_AMBIGUOUS", repr(identities))
    return identities[0]


def load_committed_snapshot(
    run_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    snapshot_root: Path | None = None,
) -> VerifiedSnapshot:
    """Read and fully bind one committed snapshot without mutating any input."""
    project_root = Path(project_root).resolve(strict=True)
    root = Path(snapshot_root) if snapshot_root is not None else project_root / "02_執行紀錄" / "影子快照"
    run_dir = _validate_run_path(root, run_id)

    raw = {name: _read_required(run_dir, name) for name in ("COMMITTED.json", "manifest.json", *REQUIRED_PAYLOADS)}
    committed = _json_object(raw["COMMITTED.json"], "COMMITTED.json")
    manifest = _json_object(raw["manifest.json"], "manifest.json")
    actual_manifest_sha = sha256(raw["manifest.json"])
    if committed.get("manifest_sha256") != actual_manifest_sha:
        raise BriefError("COMMITTED_MANIFEST_HASH_GATE", actual_manifest_sha)
    payloads = manifest.get("payloads")
    if not isinstance(payloads, dict) or set(payloads) != set(REQUIRED_PAYLOADS):
        raise BriefError("MANIFEST_PAYLOAD_SET_GATE", repr(sorted(payloads) if isinstance(payloads, dict) else payloads))
    hashes: dict[str, str] = {"COMMITTED.json": sha256(raw["COMMITTED.json"]), "manifest.json": actual_manifest_sha}
    byte_lengths: dict[str, int] = {"COMMITTED.json": len(raw["COMMITTED.json"]), "manifest.json": len(raw["manifest.json"])}
    for name in REQUIRED_PAYLOADS:
        meta = payloads.get(name)
        if not isinstance(meta, dict):
            raise BriefError("MANIFEST_PAYLOAD_META_GATE", name)
        actual_hash, actual_length = sha256(raw[name]), len(raw[name])
        if meta.get("sha256") != actual_hash:
            raise BriefError("PAYLOAD_SHA256_GATE", name)
        if meta.get("byte_length") != actual_length:
            raise BriefError("PAYLOAD_BYTE_LENGTH_GATE", name)
        hashes[name], byte_lengths[name] = actual_hash, actual_length

    # Payload JSON is parsed only after its manifest-bound bytes pass.  This
    # keeps corruption errors distinct from semantic cross-snapshot errors.
    snapshot = _json_object(raw["snapshot.json"], "snapshot.json")
    input_manifest = _json_object(raw["input_manifest.json"], "input_manifest.json")
    try:
        research_text = raw["research.md"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BriefError("RESEARCH_UTF8_GATE", str(exc)) from exc
    identities = {
        "requested": run_id,
        "committed": committed.get("run_id"),
        "manifest": manifest.get("run_id"),
        "snapshot": snapshot.get("run_id"),
    }
    if set(identities.values()) != {run_id}:
        raise BriefError("MIXED_SNAPSHOT_RUN_ID_GATE", json.dumps(identities, ensure_ascii=False, sort_keys=True))

    if committed.get("snapshot_sha256") != hashes["snapshot.json"]:
        raise BriefError("COMMITTED_SNAPSHOT_HASH_GATE", hashes["snapshot.json"])
    if committed.get("research_page_sha256") != hashes["research.md"]:
        raise BriefError("COMMITTED_RESEARCH_HASH_GATE", hashes["research.md"])
    if manifest.get("input_manifest_sha256") != hashes["input_manifest.json"]:
        raise BriefError("INPUT_MANIFEST_HASH_GATE", hashes["input_manifest.json"])
    status = manifest.get("status")
    if status not in ALLOWED_MANIFEST_STATUS or manifest.get("errors") not in ([], None):
        raise BriefError("COMMITTED_STATUS_GATE", repr(status))

    cutoff_snapshot = _required_string(snapshot, "data_cutoff_at", "snapshot")
    cutoff_input = _required_string(input_manifest, "data_cutoff_at", "input_manifest")
    if cutoff_snapshot != cutoff_input:
        raise BriefError("CUTOFF_BINDING_GATE", f"{cutoff_snapshot} != {cutoff_input}")
    cutoff = _aware_datetime(cutoff_snapshot, "data_cutoff_at")
    published = _aware_datetime(committed.get("published_at"), "published_at")
    committed_at = _aware_datetime(committed.get("committed_at"), "committed_at")
    if published != committed_at or published < cutoff:
        raise BriefError("COMMIT_TIME_BINDING_GATE", f"cutoff={cutoff_snapshot}; published={committed.get('published_at')}")

    snapshot_anchors = snapshot.get("anchors")
    input_anchors = input_manifest.get("anchors")
    if not isinstance(snapshot_anchors, dict) or snapshot_anchors != input_anchors:
        raise BriefError("ANCHOR_BINDING_GATE", repr((snapshot_anchors, input_anchors)))
    for market in ("TW", "US"):
        anchor = snapshot_anchors.get(market)
        _date(anchor, f"anchors.{market}")
        for owner, binding in (("snapshot", snapshot.get("session_binding")), ("input_manifest", input_manifest.get("session_binding"))):
            if not isinstance(binding, dict) or not isinstance(binding.get(market), dict):
                raise BriefError("SESSION_BINDING_GATE", f"{owner}.{market}")
            if binding[market].get("session_date") != anchor:
                raise BriefError("SESSION_ANCHOR_GATE", f"{owner}.{market}")
    if snapshot.get("derivation_version") != input_manifest.get("derivation_version"):
        raise BriefError("DERIVATION_VERSION_BINDING_GATE", repr((snapshot.get("derivation_version"), input_manifest.get("derivation_version"))))
    if snapshot.get("schema_version") != input_manifest.get("schema_version"):
        raise BriefError("SCHEMA_VERSION_BINDING_GATE", repr((snapshot.get("schema_version"), input_manifest.get("schema_version"))))

    inputs = input_manifest.get("inputs")
    rules_meta = inputs.get("rules") if isinstance(inputs, dict) else None
    if not isinstance(rules_meta, dict):
        raise BriefError("RULES_INPUT_BINDING_GATE", "input_manifest.inputs.rules")
    rules_sha = _required_string(rules_meta, "sha256", "input_manifest.inputs.rules")
    cas_sha = rules_meta.get("cas_sha256")
    if cas_sha is not None and cas_sha != rules_sha:
        raise BriefError("RULES_CAS_BINDING_GATE", repr((rules_sha, cas_sha)))
    rules_length = rules_meta.get("byte_length")
    if not isinstance(rules_length, int) or rules_length < 1:
        raise BriefError("RULES_BYTE_LENGTH_GATE", repr(rules_length))
    rules_version, verified_rules_sha = _resolve_rules(project_root, rules_sha, rules_length)
    current_rules_bytes = _read_project_file(project_root, Path("00_規範") / "rules.json")
    current_rules = _json_object(current_rules_bytes, "00_規範/rules.json")
    current_rules_version = _required_string(current_rules, "protocol_version", "00_規範/rules.json")

    market = snapshot.get("market")
    assessment = market.get("assessment") if isinstance(market, dict) else None
    values = market.get("values") if isinstance(market, dict) else None
    if not isinstance(assessment, dict) or not isinstance(values, dict):
        raise BriefError("MARKET_PAYLOAD_GATE", "market.assessment/values")
    scores = assessment.get("scores")
    if not isinstance(scores, dict) or any(key not in values or key not in scores for key in INDICATORS):
        raise BriefError("MARKET_INDICATOR_SET_GATE", "M1-M7")
    if assessment.get("filled") != sum(values[key].get("value") is not None for key in INDICATORS):
        raise BriefError("MARKET_FILLED_COUNT_GATE", repr(assessment.get("filled")))
    score_values = [scores[key] for key in INDICATORS if values[key].get("value") is not None]
    if any(value not in (-1, 0, 1) for value in score_values) or assessment.get("score") != sum(score_values):
        raise BriefError("MARKET_SCORE_BINDING_GATE", repr(assessment))

    return VerifiedSnapshot(
        run_dir=run_dir,
        committed=committed,
        manifest=manifest,
        snapshot=snapshot,
        input_manifest=input_manifest,
        research_text=research_text,
        hashes=hashes,
        byte_lengths=byte_lengths,
        rules_version=rules_version,
        rules_sha256=verified_rules_sha,
        current_rules_version=current_rules_version,
        current_rules_sha256=sha256(current_rules_bytes),
    )


def _indicator_rows(verified: VerifiedSnapshot) -> tuple[list[dict[str, Any]], list[str]]:
    snapshot = verified.snapshot
    cutoff_date = _aware_datetime(snapshot["data_cutoff_at"], "data_cutoff_at").date()
    values = snapshot["market"]["values"]
    scores = snapshot["market"]["assessment"]["scores"]
    rows: list[dict[str, Any]] = []
    quality_flags: list[str] = []
    for key in INDICATORS:
        item = values[key]
        value = item.get("value")
        data_date = item.get("data_date")
        missing = value is None
        freshness = "MISSING" if missing else "AT_CUTOFF_NOT_MACHINE_VERIFIABLE"
        age_days: int | None = None
        if data_date is not None:
            observed = _date(data_date, f"market.values.{key}.data_date")
            age_days = (cutoff_date - observed).days
            if age_days < 0:
                raise BriefError("INDICATOR_AFTER_CUTOFF_GATE", key)
            threshold = FRESHNESS_DAYS_AT_CUTOFF.get(key)
            if threshold is None:
                freshness = "EVIDENCE_INSUFFICIENT_PUBLISHED_AVAILABLE_TIME"
                quality_flags.append(f"{key}_PUBLICATION_TIME_NOT_CAPTURED")
            elif age_days > threshold:
                freshness = "STALE_AT_SNAPSHOT_CUTOFF"
                quality_flags.append(f"{key}_STALE_AT_SNAPSHOT_CUTOFF")
            else:
                freshness = "WITHIN_SNAPSHOT_AGE_GATE"
        elif not missing:
            raise BriefError("INDICATOR_DATA_DATE_GATE", key)
        rows.append(
            {
                "id": key,
                "name": INDICATOR_NAMES[key],
                "value": value,
                "unit": INDICATOR_UNITS[key],
                "score": scores[key] if not missing else None,
                "data_date": data_date,
                "missing": missing,
                "reason": item.get("reason"),
                "age_days_at_cutoff": age_days,
                "freshness_at_cutoff": freshness,
            }
        )
    return rows, sorted(set(quality_flags))


def _attribution_20d(company: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = [row for row in company.get("attribution") or [] if row.get("status") == "OK"]
    row = next((item for item in rows if item.get("window_days") == 20), None)
    if row is None:
        return None
    return {
        "classification": row.get("classification"),
        "total_return": row.get("total_return"),
        "benchmark_return": row.get("benchmark_return"),
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
    }


def _company_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for company in snapshot.get("companies") or []:
        trend = company.get("trend") or {}
        health = company.get("health") or {}
        risk = company.get("risk") or {}
        valuation = company.get("valuation_v1") or {}
        hs20 = (((risk.get("forecasts") or {}).get("HS") or {}).get("20") or {})
        missing = [key for key, value in (company.get("formal_readiness") or {}).items() if value is False]
        if company.get("price_status", "OK") != "OK":
            missing.insert(0, "PRICE_NOT_AT_ANCHOR")
        rows.append(
            {
                "ticker": company.get("ticker"),
                "market": company.get("market"),
                "anchor_session": company.get("anchor_session"),
                "trend": {key: trend.get(key) for key in ("C1", "C2", "C3")},
                "trend_period_end": trend.get("latest_period_end"),
                "health_status": health.get("status"),
                "valuation_v1": valuation.get("value"),
                "valuation_status": valuation.get("status"),
                "attribution_20d": _attribution_20d(company),
                "risk": {
                    "status": risk.get("status"),
                    "data_cutoff": risk.get("data_cutoff"),
                    "volatility_20d": (risk.get("vol") or {}).get("20"),
                    "hs_var99_20d": hs20.get("var99"),
                    "max_drawdown": risk.get("max_drawdown"),
                },
                "missing_or_unready": sorted(set(missing)),
            }
        )
    return rows


def _theme_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    activated = ((snapshot.get("activated_fields") or {}).get("themes") or {})
    rows: list[dict[str, Any]] = []
    for theme_id, item in sorted((snapshot.get("theme_breadth") or {}).items()):
        historical_ai = activated.get(theme_id) or {}
        rows.append(
            {
                "id": theme_id,
                "name": item.get("name"),
                "i1_value": item.get("value"),
                "i1_score": item.get("score"),
                "coverage": {"have": item.get("n_have"), "total": item.get("n_total")},
                "accelerating": item.get("accelerating") or [],
                "decelerating": item.get("decelerating") or [],
                "missing": item.get("missing") or [],
                "historical_frozen_document_fields": {
                    key: historical_ai.get(key) for key in ("I2", "I4") if key in historical_ai
                },
                "document_currentness": "BLOCKED_T46_CURRENTNESS",
            }
        )
    return rows


def build_brief(verified: VerifiedSnapshot) -> dict[str, Any]:
    snapshot = verified.snapshot
    assessment = snapshot["market"]["assessment"]
    indicators, quality_flags = _indicator_rows(verified)
    supports = [row["id"] for row in indicators if row["score"] == 1]
    pressures = [row["id"] for row in indicators if row["score"] == -1]
    missing = [row["id"] for row in indicators if row["missing"]]
    input_manifest = verified.input_manifest
    limitations = list(snapshot.get("limitations") or [])
    macro_limitation = input_manifest.get("macro_limitation")
    if macro_limitation:
        limitations.append(macro_limitation)
    if input_manifest.get("macro_revision_vintage_available") is not True:
        limitations.append("MACRO_REVISION_VINTAGE_NOT_AVAILABLE")
    limitations.extend(
        [
            "M4_M5_HISTORICAL_POSITIONAL_ALGORITHM_CALENDAR_MONTH_FIX_NOT_FORMALLY_INTEGRATED",
            "T46_DOCUMENT_CURRENTNESS_BLOCKED_HISTORICAL_FROZEN_EVIDENCE_ONLY",
            "MARKET_SIGNAL_PREDICTIVE_VALUE_NOT_VALIDATED",
        ]
    )
    rules_warning = (
        f"快照綁定規則 {verified.rules_version}；只保留當時值。"
        "M4/M5 精確曆月修補尚未正式整合，不事後重寫歷史分數。"
    )
    next_conditions = [
        "建立新且另行授權的 COMMITTED snapshot，才可描述新的市場時點。",
        "M1 必須補齊 source_published_at、available_at 與 fetched_at，否則 currentness fail-closed。",
        "M2/M3/M6/M7 更新後須重新驗證交易 session 與 cutoff；本工具不抓取資料。",
        "M4/M5 精確曆月修補完成正式治理前，持續顯示歷史演算法保留。",
        "T46 完成受控復原與 correction-aware currentness 驗證前，不得把 C5/C6/I2 稱為最新文件研究。",
        "建立成熟的 append-only forward outcome 後，另行評估預測有效性。",
    ]
    light = assessment.get("status")
    light_display = {"黃": "黃燈", "綠": "綠燈", "紅": "紅燈"}.get(light, light)
    return {
        "schema_version": "MARKET_BRIEF_COMMITTED_1",
        "mode": "HISTORICAL_ONLY",
        "current_claim": False,
        "not_investment_advice": True,
        "snapshot": {
            "run_id": verified.committed["run_id"],
            "status_at_commit": verified.manifest["status"],
            "data_cutoff_at": snapshot["data_cutoff_at"],
            "published_at": verified.committed["published_at"],
            "anchors": snapshot["anchors"],
            "rules_version": verified.rules_version,
            "rules_sha256": verified.rules_sha256,
            "snapshot_sha256": verified.hashes["snapshot.json"],
            "manifest_sha256": verified.hashes["manifest.json"],
            "input_manifest_sha256": verified.hashes["input_manifest.json"],
            "research_sha256": verified.hashes["research.md"],
        },
        "repository_rules": {
            "version": verified.current_rules_version,
            "sha256": verified.current_rules_sha256,
            "used_to_reinterpret_snapshot": False,
        },
        "market": {
            "historical_light": light_display,
            "historical_light_raw": light,
            "historical_score": assessment.get("score"),
            "filled": assessment.get("filled"),
            "indicators": indicators,
            "supporting_factors": supports,
            "pressure_factors": pressures,
            "missing_indicators": missing,
        },
        "themes": _theme_rows(snapshot),
        "companies": _company_rows(snapshot),
        "document_research": {
            "status": "BLOCKED_T46_CURRENTNESS",
            "permitted_interpretation": "HISTORICAL_FROZEN_EVIDENCE_ONLY",
            "current_claim": False,
            "affected_fields": list(T46_FIELDS),
        },
        "data_quality": {
            "integrity": "PASS",
            "currentness": "HISTORICAL_ONLY",
            "quality_flags": quality_flags,
            "limitations": sorted(set(str(item) for item in limitations)),
            "rules_warning": rules_warning,
        },
        "predictive_validity": {
            "status": "NOT_VALIDATED",
            "confidence_percentage": None,
            "statement": "程式與快照完整性通過，不代表市場燈號具有勝率、準確率或超額報酬證據。",
        },
        "next_observation_conditions": next_conditions,
        "source_files": {
            name: {"sha256": verified.hashes[name], "byte_length": verified.byte_lengths[name]}
            for name in ("COMMITTED.json", "manifest.json", *REQUIRED_PAYLOADS)
        },
    }


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(brief: Mapping[str, Any]) -> str:
    snap = brief["snapshot"]
    market = brief["market"]
    lines = [
        "# COMMITTED 市場研究簡報",
        "",
        "> **HISTORICAL_ONLY** — 本頁只呈現已提交快照的歷史內容，不是目前市場判斷，也不是投資建議。",
        "",
        f"- Snapshot：`{snap['run_id']}`；commit 狀態：`{snap['status_at_commit']}`",
        f"- 資料截止：`{snap['data_cutoff_at']}`；發布：`{snap['published_at']}`",
        f"- 價格錨點：TW `{snap['anchors']['TW']}`；US `{snap['anchors']['US']}`",
        f"- 歷史規則：`{snap['rules_version']}`；SHA-256 `{snap['rules_sha256']}`",
        f"- 現行 repository rules：`{brief['repository_rules']['version']}`；未用來重算或改寫歷史結果",
        "",
        "## 歷史市場環境",
        "",
        f"燈號 **{market['historical_light']}**；總分 `{market['historical_score']}`；覆蓋 `{market['filled']}/7`。",
        "",
        "| 指標 | 值 | 分數 | 資料日期 | 缺值 | 截止時點品質 |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in market["indicators"]:
        value = _cell(row["value"])
        if row["unit"] not in ("選項", None) and row["value"] is not None:
            value += f" {row['unit']}"
        lines.append(
            f"| {row['id']} {row['name']} | {value} | {_cell(row['score'])} | "
            f"{_cell(row['data_date'])} | {_cell(row['missing'])} | `{row['freshness_at_cutoff']}` |"
        )
    lines += [
        "",
        "主要支撐：" + ("、".join(market["supporting_factors"]) or "無") + "。",
        "主要壓力：" + ("、".join(market["pressure_factors"]) or "無") + "。",
        "",
        "## 歷史產業／主線摘要",
        "",
        "| 主線 | I1 | 覆蓋 | 加速 | 減速 | 文件 currentness |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in brief["themes"]:
        lines.append(
            f"| {row['id']} {_cell(row['name'])} | {_cell(row['i1_value'])} | "
            f"{_cell(row['coverage']['have'])}/{_cell(row['coverage']['total'])} | "
            f"{', '.join(row['accelerating']) or '—'} | {', '.join(row['decelerating']) or '—'} | "
            f"`{row['document_currentness']}` |"
        )
    lines += [
        "",
        "I2 等文件衍生欄位只可解讀為快照內的歷史凍結證據；T46 currentness 尚未恢復。",
        "",
        "## 歷史公司與風險摘要",
        "",
        "| 代號 | 市場 | C1 | C2 | C3 | H | V1 | 20日歸因 | 20日波動 | HS VaR99 20日 | 最大回撤 |",
        "|---|---|---:|---:|---:|---|---:|---|---:|---:|---:|",
    ]
    for row in brief["companies"]:
        attr = row["attribution_20d"] or {}
        risk = row["risk"]
        lines.append(
            f"| {_cell(row['ticker'])} | {_cell(row['market'])} | {_cell(row['trend']['C1'])} | "
            f"{_cell(row['trend']['C2'])} | {_cell(row['trend']['C3'])} | {_cell(row['health_status'])} | "
            f"{_cell(row['valuation_v1'])} | {_cell(attr.get('classification'))} | "
            f"{_cell(None if risk['volatility_20d'] is None else risk['volatility_20d'] * 100)}% | "
            f"{_cell(None if risk['hs_var99_20d'] is None else risk['hs_var99_20d'] * 100)}% | "
            f"{_cell(None if risk['max_drawdown'] is None else risk['max_drawdown'] * 100)}% |"
        )
    quality = brief["data_quality"]
    predictive = brief["predictive_validity"]
    lines += [
        "",
        "上述風險值為歷史影子估計，不預測下跌時點，不構成交易或部位指令。",
        "",
        "## 資料品質（與預測有效性分開）",
        "",
        f"- 完整性：`{quality['integrity']}`",
        f"- Currentness：`{quality['currentness']}`",
        f"- 品質旗標：{', '.join(f'`{item}`' for item in quality['quality_flags']) or '無'}",
        f"- M4/M5：{quality['rules_warning']}",
        "- T46：`BLOCKED_T46_CURRENTNESS`；C5/C6/I2 不得標示為目前有效的最新文件研究。",
        "",
        "## 預測有效性",
        "",
        f"- 狀態：`{predictive['status']}`",
        f"- 說明：{predictive['statement']}",
        "- 未提供未校準的信心百分比。",
        "",
        "## 重大限制",
        "",
    ]
    lines.extend(f"- `{item}`" for item in quality["limitations"])
    lines += ["", "## 下一個觀察條件", ""]
    lines.extend(f"{index}. {item}" for index, item in enumerate(brief["next_observation_conditions"], start=1))
    lines += [
        "",
        "## 完整性身分",
        "",
        f"- snapshot SHA-256：`{snap['snapshot_sha256']}`",
        f"- manifest SHA-256：`{snap['manifest_sha256']}`",
        f"- input manifest SHA-256：`{snap['input_manifest_sha256']}`",
        f"- research SHA-256：`{snap['research_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def _atomic_create(path: Path, data: bytes, forbidden_root: Path) -> None:
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    source_root = forbidden_root.resolve(strict=True)
    if target == source_root or source_root in target.parents:
        raise BriefError("OUTPUT_INSIDE_SNAPSHOT_ROOT_GATE", str(target))
    if target.exists():
        raise BriefError("OUTPUT_EXISTS_GATE", str(target))
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            raise BriefError("OUTPUT_EXISTS_GATE", str(target))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_outputs(brief: Mapping[str, Any], markdown_path: Path, json_path: Path, snapshot_root: Path = SNAPSHOT_ROOT) -> None:
    markdown_path, json_path = Path(markdown_path), Path(json_path)
    if markdown_path.resolve(strict=False) == json_path.resolve(strict=False):
        raise BriefError("OUTPUT_PATH_COLLISION_GATE", str(markdown_path))
    json_bytes = (json.dumps(brief, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    markdown_bytes = render_markdown(brief).encode("utf-8")
    _atomic_create(markdown_path, markdown_bytes, snapshot_root)
    try:
        _atomic_create(json_path, json_bytes, snapshot_root)
    except Exception:
        # The reports are a pair.  Removing the newly created first output is
        # safe because this function refuses pre-existing targets.
        markdown_path.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one verified COMMITTED snapshot as HISTORICAL_ONLY.")
    parser.add_argument("--snapshot-run-id", required=True, help="Exact SNAP_... run ID; latest is never inferred.")
    parser.add_argument("--markdown-output", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verified = load_committed_snapshot(args.snapshot_run_id)
        brief = build_brief(verified)
        write_outputs(brief, args.markdown_output, args.json_output)
    except BriefError as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "reason_code": exc.code, "detail": exc.detail}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "PASS_HISTORICAL_ONLY",
                "run_id": args.snapshot_run_id,
                "markdown_output": str(args.markdown_output.resolve()),
                "json_output": str(args.json_output.resolve()),
                "snapshot_sha256": verified.hashes["snapshot.json"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
