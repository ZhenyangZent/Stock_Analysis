"""Evaluate formal classification history at 20/60/120 sessions.

Research evaluation only; not investment advice and never used by verdict.py.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import classification_history
import evaluation_samples
import pricedata
from common import ROOT, load_rules

HORIZONS = (20, 60, 120)
MIN_N = 30


def _summary(samples):
    summary = {}
    for horizon in HORIZONS:
        summary[str(horizon)] = {}
        labels = sorted({s.get("classification") for s in samples
                         if s["horizon"] == horizon and s.get("classification")})
        for label in labels:
            group = [s for s in samples if s["horizon"] == horizon
                     and s.get("classification") == label and s.get("excess_return") is not None]
            values = [s["excess_return"] for s in group]
            summary[str(horizon)][label] = {
                "n": len(values), "mean_excess_return": statistics.mean(values) if values else None,
                "median_excess_return": statistics.median(values) if values else None,
                "conclusion": "樣本不足，不得下結論" if len(values) < MIN_N else "僅描述，不構成投資建議",
            }
    return summary


def evaluate(ledger=classification_history.LEDGER, rules=None, prices=None, actions=None):
    rules = rules or load_rules(); prices = prices or pricedata.load_prices(rules)
    actions = actions or pricedata.load_actions(rules)
    total = {ticker: pricedata.tr_index(series, actions.get(ticker, {})) for ticker, series in prices.items()}
    rows = classification_history._existing(ledger); samples = []
    selected = evaluation_samples.earliest_unique(rows, lambda record: record.get("companies", []))
    for record, company, published, version in selected:
        ticker, anchor = company.get("ticker"), company.get("anchor_session")
        benchmark = rules["benchmarks"]["default_tw" if company.get("market") == "TW" else "default_us"]
        for horizon in HORIZONS:
            performance = evaluation_samples.common_forward(
                total.get(ticker, []), total.get(benchmark, []), published, horizon)
            samples.append({"classification_id": record.get("classification_id"), "snapshot_id": record["snapshot_id"],
                            "ticker": ticker, "market": company.get("market"), "anchor_session": anchor,
                            "published_at": published.isoformat(), "rules_version": version,
                            "entry_session": performance.get("entry_session") if performance else None,
                            "end_session": performance.get("end_session") if performance else None,
                            "classification": company.get("classification"), "horizon": horizon,
                            "return": performance.get("stock_return") if performance else None,
                            "benchmark": benchmark,
                            "benchmark_return": performance.get("benchmark_return") if performance else None,
                            "excess_return": performance.get("excess_return") if performance else None})
    versions = sorted({s["rules_version"] for s in samples})
    by_version = {version: _summary([s for s in samples if s["rules_version"] == version]) for version in versions}
    all_summary = _summary(samples)
    latest = max((r.get("snapshot_id") for r in rows if r.get("snapshot_id")), default=None)
    input_hashes = {"classification_history_sha256": evaluation_samples.sha256_file(ledger),
                    "prices_sha256": evaluation_samples.sha256_file(pricedata.PRICES),
                    "corporate_actions_sha256": evaluation_samples.sha256_file(pricedata.ACTIONS)}
    return {"schema_version": "CLASSIFICATION_EVAL_2", "not_investment_advice": True,
            "min_sample_n": MIN_N, "horizons": list(HORIZONS), "summary": all_summary,
            "summary_by_rules_version": by_version, "all_versions_summary": all_summary,
            "rules_versions": versions, "mixed_rules_versions": len(versions) > 1,
            "latest_included_snapshot_id": latest, "input_sha256": input_hashes,
            "price_cutoff_date": evaluation_samples.price_cutoff(prices),
            "deduplicated_company_snapshot_count": len(selected), "samples": samples}


def markdown(result):
    lines = ["# 分類成效追蹤", "", "本報表只供研究評估，不是買賣建議；樣本少於 30 筆不得下結論。", "",
             f"- 最新納入快照：`{result.get('latest_included_snapshot_id') or 'NONE'}`",
             f"- 價格截止日：`{result.get('price_cutoff_date') or 'UNKNOWN'}`",
             f"- 規則版本：`{', '.join(result.get('rules_versions') or []) or 'UNKNOWN'}`；混版：`{str(result.get('mixed_rules_versions', False)).lower()}`",
             "",
             "| 期間 | 分類 | N | 平均超額報酬 | 中位超額報酬 | 判讀 |", "|---:|---|---:|---:|---:|---|"]
    def pct(value): return "—" if value is None else f"{value:.2%}"
    for horizon, groups in result["summary"].items():
        for label, row in groups.items():
            lines.append(f"| {horizon} | {label} | {row['n']} | {pct(row['mean_excess_return'])} | "
                         f"{pct(row['median_excess_return'])} | {row['conclusion']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--output"); parser.add_argument("--json-output")
    args = parser.parse_args(argv); result = evaluate()
    if args.output: Path(args.output).write_text(markdown(result), encoding="utf-8")
    if args.json_output: Path(args.json_output).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"sample_count": len(result["samples"]), "summary": result["summary"]}, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

