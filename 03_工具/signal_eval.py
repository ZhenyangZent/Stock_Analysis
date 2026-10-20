"""影子訊號評估：只評估歷史已提交快照，不改正式分類或門檻。

每家公司每次財報期別更新後只取第一份快照；報酬為個股含息報酬減去
16 檔追蹤名單同期等權含息報酬。樣本不足只列 N，不下結論。
"""
from __future__ import annotations
import argparse, json, math, statistics
from pathlib import Path
import datetime as dt

import pricedata
import data_spine
import evaluation_samples
import trend
import health
from common import load_rules, score_one

ROOT = Path(__file__).resolve().parent.parent
SNAP_ROOT = ROOT / "02_執行紀錄" / "影子快照"
MIN_N = 12
HORIZONS = (20, 60)


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values); i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]: j += 1
        rank = (i + j - 1) / 2 + 1
        for k in range(i, j): ranks[order[k]] = rank
        i = j
    return ranks


def spearman(xs, ys):
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2: return None
    x, y = _rank(xs), _rank(ys); mx, my = statistics.mean(x), statistics.mean(y)
    num = sum((a-mx)*(b-my) for a,b in zip(x,y))
    den = math.sqrt(sum((a-mx)**2 for a in x) * sum((b-my)**2 for b in y))
    return num / den if den else None


def _clustered_mean_se(rows, value_key="relative_return"):
    """事件日期分群的平均值標準誤；同日多家公司不當成獨立事件。"""
    if not rows:
        return None, 0
    mean = statistics.mean(r[value_key] for r in rows)
    clusters = {}
    for row in rows:
        clusters.setdefault(row["available_at"][:10], []).append(row[value_key] - mean)
    g, n = len(clusters), len(rows)
    if g < 2:
        return None, g
    variance = (g / (g - 1)) * sum(sum(values) ** 2 for values in clusters.values()) / (n ** 2)
    return math.sqrt(max(variance, 0.0)), g


def _descriptive_group(rows):
    se, clusters = _clustered_mean_se(rows)
    returns = [r["relative_return"] for r in rows]
    maes = [r["mae"] for r in rows]
    return {"n": len(rows), "event_date_clusters": clusters,
            "mean_relative_return": statistics.mean(returns) if returns else None,
            "median_relative_return": statistics.median(returns) if returns else None,
            "clustered_se": se, "mean_mae": statistics.mean(maes) if maes else None,
            "win_rate": sum(x > 0 for x in returns) / len(returns) if returns else None}


def _raw_quintiles(rows):
    if not rows:
        return {}
    ranks = _rank([r["value"] for r in rows]); n = len(rows)
    groups = {}
    for row, rank in zip(rows, ranks):
        quintile = min(5, max(1, math.ceil(rank * 5 / n)))
        groups.setdefault(str(quintile), []).append(row)
    return {key: _descriptive_group(group) for key, group in sorted(groups.items())}


def _summarize_shadow_samples(samples, rules):
    result = {}
    for field in ("M_TOTAL","C1","C2","C3","I1","V1","H_LIGHT"):
        result[field] = {}
        for horizon in HORIZONS:
            rows = [r for r in samples if r["field"]==field and r["horizon"]==horizon]
            rho = spearman([r["value"] for r in rows],[r["relative_return"] for r in rows])
            groups = {}
            spec = ({"C1":rules["company"]["indicators"]["C1"], "C2":rules["company"]["indicators"]["C2"],
                     "C3":rules["company"]["indicators"]["C3"], "I1":rules["theme"]["indicators"]["I1"],
                     "V1":rules["valuation"]["indicators"]["V1"]}.get(field))
            for row in rows:
                group = score_one(row["value"], spec["rule"]) if spec else int(row["value"])
                groups.setdefault(str(group),[]).append(row["relative_return"])
            result[field][str(horizon)] = {"sample_n":len(rows), "conclusion":None if len(rows)<MIN_N else "DESCRIPTIVE_ONLY",
                                           "spearman":rho, "mean_relative_return":statistics.mean([r["relative_return"] for r in rows]) if rows else None,
                                           "mean_mae":statistics.mean([r["mae"] for r in rows]) if rows else None,
                                           "threshold_groups":{k:{"n":len(v),"mean":statistics.mean(v)} for k,v in sorted(groups.items())}}
    return result


def committed_snapshots(root=SNAP_ROOT):
    out = []
    for folder in sorted(root.glob("SNAP_*")):
        marker, snap = folder / "COMMITTED.json", folder / "snapshot.json"
        if not marker.exists() or not snap.exists(): continue
        m = json.loads(marker.read_text(encoding="utf-8")); raw = snap.read_bytes()
        import hashlib
        if hashlib.sha256(raw).hexdigest() != m.get("snapshot_sha256"): continue
        snap_data = json.loads(raw)
        rules_version = None
        for market in ("TW", "US"):
            shadow = folder / f"shadow_verdict_{market}.json"
            if shadow.exists():
                rules_version = ((json.loads(shadow.read_text(encoding="utf-8")).get("judge") or {}).get("protocol_version"))
                if rules_version: break
        out.append({"run_id": folder.name, "snapshot": snap_data,
                    "published_at": m.get("published_at") or m.get("committed_at"),
                    "rules_version": rules_version or "UNKNOWN",
                    "snapshot_sha256": m.get("snapshot_sha256")})
    return out


def _theme(ticker, rules):
    return next((tid for tid, t in rules["themes"].items() if ticker in t.get("watchlist", [])), None)


def signal_values(company, snapshot, rules):
    trend = company.get("trend") or {}; health = company.get("health") or {}
    h_status = health.get("status")
    h_value = {"穩健": 1, "留意": 0, "警示": -1}.get(h_status)
    theme = _theme(company["ticker"], rules)
    return {"M_TOTAL": (snapshot.get("market", {}).get("assessment") or {}).get("score"),
            "C1": trend.get("C1"), "C2": trend.get("C2"), "C3": trend.get("C3"),
            "I1": (snapshot.get("theme_breadth", {}).get(theme) or {}).get("value"),
            "V1": (company.get("valuation_v1") or {}).get("value"), "H_LIGHT": h_value}


def first_filing_events(snapshots, rules):
    candidates = []
    for item in snapshots:
        if isinstance(item, dict) and "snapshot" in item:
            run_id, snap = item["run_id"], item["snapshot"]
            published_at = item.get("published_at")
            rules_version = item.get("rules_version") or "UNKNOWN"
        else:  # legacy test fixtures
            run_id, snap = item
            published_at = snap.get("published_at") or snap.get("created_at")
            rules_version = snap.get("rules_version") or snap.get("protocol_version") or "UNKNOWN"
        for company in snap.get("companies", []):
            period = (company.get("trend") or {}).get("latest_period_end")
            if not period: continue
            candidates.append({"snapshot_id": run_id, "published_at": published_at,
                               "protocol_version": rules_version, "company": company,
                               "snapshot": snap, "period_end": period})
    selected = evaluation_samples.earliest_unique(candidates, lambda row: [row["company"]])
    return [{"run_id": row["snapshot_id"], "ticker": company["ticker"], "market": company["market"],
             "anchor": company.get("anchor_session"), "period_end": row["period_end"],
             "published_at": published.isoformat(), "rules_version": version,
             "signals": signal_values(company, row["snapshot"], rules)}
            for row, company, published, version in selected]


def evaluate(snapshots=None, rules=None, prices=None, actions=None):
    rules = rules or load_rules(); snapshots = snapshots or committed_snapshots()
    prices = prices or pricedata.load_prices(rules); actions = actions or pricedata.load_actions(rules)
    tickers = sorted({s["ticker"] for s in json.loads((ROOT/"01_共用資料/data_spine/entity_registry.json").read_text(encoding="utf-8"))["securities"]
                      if s.get("price_series_enabled") and s.get("security_type") != "ETF"})
    tri = {t: pricedata.tr_index(prices.get(t, []), actions.get(t, {})) for t in tickers}
    samples = []
    for event in first_filing_events(snapshots, rules):
        t = event["ticker"]
        for horizon in HORIZONS:
            performance = _relative_path(t, event["published_at"], horizon, tri, tickers,
                                         publication_based=True)
            if not performance: continue
            for field, value in event["signals"].items():
                if isinstance(value,(int,float)) and not isinstance(value,bool):
                    samples.append({**event, "field":field, "value":float(value), "horizon":horizon,
                                    **performance, "peer_n":performance["peer_n_end"]})
    result = _summarize_shadow_samples(samples, rules)
    versions = sorted({row.get("rules_version", "UNKNOWN") for row in samples})
    return {"mode":"SHADOW", "not_investment_advice":True, "min_n":MIN_N, "results":result,
            "rules_versions": versions, "mixed_rules_versions": len(versions) > 1,
            "results_by_rules_version": {version:_summarize_shadow_samples(
                [row for row in samples if row.get("rules_version", "UNKNOWN") == version], rules)
                for version in versions}, "all_versions_results":result,
            "latest_included_snapshot_id": max((e["run_id"] for e in first_filing_events(snapshots, rules)), default=None),
            "input_sha256": {"prices_sha256": evaluation_samples.sha256_file(pricedata.PRICES),
                             "corporate_actions_sha256": evaluation_samples.sha256_file(pricedata.ACTIONS)},
            "price_cutoff_date": evaluation_samples.price_cutoff(prices), "samples":samples}


def _filing_events(observations, registry, start="2024-01-01", end="2026-12-31"):
    """每一發行人／財報期取第一次官方可得的單季營收申報。未知回補與法定期限 proxy 不算。"""
    ticker_by_issuer = {}
    for sec in registry["securities"]:
        if sec.get("price_series_enabled") and sec.get("security_type") != "ETF":
            ticker_by_issuer.setdefault(sec["issuer_id"], sec)
    grouped = {}
    for row in observations:
        if (row.get("metric_id") != "FIN_REVENUE" or row.get("period_type") != "Q" or
                row.get("issuer_id") not in ticker_by_issuer or
                row.get("availability_status") != "POINT_IN_TIME_KNOWN" or
                row.get("availability_basis") in {"UNKNOWN_BACKFILL", "STATUTORY_DEADLINE_CONSERVATIVE"}):
            continue
        available = row.get("available_at")
        if not available or not (start <= available[:10] <= end):
            continue
        key = (row["issuer_id"], row.get("period_end"))
        rank = (data_spine.parse_time(available), row.get("observation_id", ""))
        if key not in grouped or rank < grouped[key][0]:
            grouped[key] = (rank, row)
    out = []
    for (_, period), (_, row) in sorted(grouped.items(), key=lambda item: item[1][0]):
        sec = ticker_by_issuer[row["issuer_id"]]
        out.append({"ticker": sec["ticker"], "issuer_id": row["issuer_id"], "market": sec["market"],
                    "period_end": period, "available_at": row["available_at"],
                    "event_observation_id": row["observation_id"],
                    "availability_basis": row.get("availability_basis")})
    return out


def _baseline_session(event, sessions):
    return evaluation_samples.next_session_after(event["available_at"], sessions, event["market"])


def _relative_path(ticker, anchor, horizon, tri, tickers, publication_based=False):
    by_date = {t: dict(seq) for t, seq in tri.items()}
    dates = {t: [d for d, _ in seq] for t, seq in tri.items()}
    if ticker not in dates: return None
    if publication_based:
        published = evaluation_samples.parse_time(anchor)
        eligible = [d for d in dates[ticker] if dt.date.fromisoformat(d) > published.date()]
        if len(eligible) <= horizon: return None
        baseline, path_dates = eligible[0], eligible[1:horizon + 1]
    else:
        if anchor not in dates[ticker]: return None
        i = dates[ticker].index(anchor)
        if i + horizon >= len(dates[ticker]): return None
        baseline, path_dates = anchor, dates[ticker][i + 1:i + horizon + 1]
    rel_path, peer_counts = [], []
    for date in path_dates:
        if date not in by_date[ticker]: return None
        stock = by_date[ticker][date] / by_date[ticker][baseline] - 1
        peers = [by_date[p][date] / by_date[p][baseline] - 1 for p in tickers
                 if baseline in by_date.get(p, {}) and date in by_date.get(p, {})]
        if not peers: return None
        rel_path.append(stock - statistics.mean(peers)); peer_counts.append(len(peers))
    return {"entry_session": baseline, "end": path_dates[-1], "relative_return": rel_path[-1], "mae": min(rel_path),
            "peer_n_min": min(peer_counts), "peer_n_end": peer_counts[-1]}


def historical_pit_event_study(rules=None, observations=None, prices=None, actions=None,
                               sessions=None, registry=None):
    """2024--2026 官方財報時點事件研究；不讀、不寫前瞻 outcomes ledger。"""
    rules = rules or load_rules(); registry = registry or data_spine.load_registry()
    observations = observations or data_spine.read_observations_checked()
    # 歷史研究明確以官方 available_at 重建當時可見資料；canonical 實際寫入時間只代表
    # 本系統何時補抓，不得把 2026 的補抓時間誤當成 2024 財報不存在。此模式永不寫前瞻帳本。
    replay_observations = []
    for row in observations:
        clone = dict(row)
        if row.get("available_at"):
            clone["selection_written_at"] = row["available_at"]
            # trend／health 的舊 SEC 三時間 gate 也檢查 ingested_at；歷史研究以官方
            # available_at 重建，不把本系統 2026 補抓日期當成當時不可得。
            clone["ingested_at"] = row["available_at"]
        replay_observations.append(clone)
    sessions = sessions or data_spine.load_sessions()
    prices = prices or pricedata.load_prices(rules); actions = actions or pricedata.load_actions(rules)
    tickers = sorted({s["ticker"] for s in registry["securities"]
                      if s.get("price_series_enabled") and s.get("security_type") != "ETF"})
    tri = {t: pricedata.tr_index(prices.get(t, []), actions.get(t, {})) for t in tickers}
    samples, event_rows = [], []
    for event in _filing_events(replay_observations, registry):
        anchor = _baseline_session(event, sessions)
        if not anchor: continue
        cutoff = event["available_at"]
        tr = trend.evaluate_issuer(event["issuer_id"], replay_observations, rules, cutoff,
                                   current_snapshot_mode=False, mops_enabled=True)
        he = health.evaluate_issuer(event["issuer_id"], replay_observations, rules, cutoff,
                                    current_snapshot_mode=False, mops_enabled=True)
        signals = {"C1": tr.get("C1"), "C2": tr.get("C2"), "C3": tr.get("C3"),
                   "H_LIGHT": {"穩健": 1, "留意": 0, "警示": -1}.get(he.get("status"))}
        event_row = {**event, "anchor": anchor, "signals": signals,
                     "trend_period_end": tr.get("latest_period_end"), "health_period_end": he.get("latest_period_end")}
        event_rows.append(event_row)
        for horizon in HORIZONS:
            performance = _relative_path(event["ticker"], anchor, horizon, tri, tickers)
            if not performance: continue
            for field, value in signals.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    samples.append({**event_row, **performance, "field": field,
                                    "value": float(value), "horizon": horizon})
    results = {}
    for field in ("C1", "C2", "C3", "H_LIGHT"):
        results[field] = {}
        for horizon in HORIZONS:
            rows = [r for r in samples if r["field"] == field and r["horizon"] == horizon]
            spec = ({"C1": rules["company"]["indicators"]["C1"],
                     "C2": rules["company"]["indicators"]["C2"],
                     "C3": rules["company"]["indicators"]["C3"]}.get(field))
            threshold_groups = {}
            for row in rows:
                group = score_one(row["value"], spec["rule"]) if spec else int(row["value"])
                threshold_groups.setdefault(str(group), []).append(row)
            results[field][str(horizon)] = {
                "sample_n": len(rows), "conclusion": None if len(rows) < MIN_N else "DESCRIPTIVE_ONLY",
                "mean_relative_return": statistics.mean([r["relative_return"] for r in rows]) if rows else None,
                "mean_mae": statistics.mean([r["mae"] for r in rows]) if rows else None,
                "spearman": spearman([r["value"] for r in rows], [r["relative_return"] for r in rows]),
                "threshold_groups": {key: _descriptive_group(group)
                                     for key, group in sorted(threshold_groups.items())},
                "raw_value_quintiles": _raw_quintiles(rows)}
    return {"mode": "HISTORICAL_PIT_EVENT_STUDY", "not_investment_advice": True,
            "window": "2024-2026", "min_n": MIN_N, "event_count": len(event_rows),
            "events": event_rows, "results": results, "samples": samples,
            "forward_ledger_write_count": 0,
            "limitations": ["RETROSPECTIVE_RECONSTRUCTION_NOT_FORWARD_LEDGER",
                            "USES_CURRENT_CANONICAL_EXTRACTION_WITH_OFFICIAL_AVAILABLE_AT"]}


def render(data):
    lines=["# 影子訊號評估", "", "只做描述性評估，不改正式分類、規則或門檻，也不是買賣指令。", "",
           "| 欄位 | 期間 | N | 名單內相對報酬均值 | MAE 均值 | Spearman | 判讀 |", "|---|---:|---:|---:|---:|---:|---|"]
    for field, horizons in data["results"].items():
        for h, r in horizons.items():
            fmt=lambda x: "—" if x is None else f"{x:.4f}"
            conclusion = f"樣本數 {r['sample_n']}，不下結論" if r["sample_n"] < data["min_n"] else "僅描述，不作因果結論"
            lines.append(f"| {field} | {h} 日 | {r['sample_n']} | {fmt(r['mean_relative_return'])} | {fmt(r['mean_mae'])} | {fmt(r['spearman'])} | {conclusion} |")
    return "\n".join(lines)+"\n"


def render_historical(data):
    lines = ["# 歷史 PIT 財報事件研究", "",
             "以官方可得時間重算當時的 C1–C3 與 H；和前瞻帳本分開，只做描述，不改門檻。", "",
             f"事件數：`{data['event_count']}`；模式：`{data['mode']}`。", "",
             "| 訊號 | 期間 | N | 名單內相對報酬均值 | MAE 均值 | Spearman | 判讀 |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for field, horizons in data["results"].items():
        for horizon, row in horizons.items():
            fmt = lambda x: "—" if x is None else f"{x:.4f}"
            note = f"樣本數 {row['sample_n']}，不下結論" if row["sample_n"] < data["min_n"] else "僅描述，不作因果結論"
            lines.append(f"| {field} | {horizon} 日 | {row['sample_n']} | {fmt(row['mean_relative_return'])} | "
                         f"{fmt(row['mean_mae'])} | {fmt(row['spearman'])} | {note} |")
    lines += ["", "## 門檻分組（+1／0／−1）", "",
              "標準誤按官方事件日期分群；以下皆僅描述。", "",
              "| 訊號 | 期間 | 分組 | N | 日期群 | 相對報酬中位數 | 相對報酬平均 | 分群標準誤 | MAE | 勝率 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for field, horizons in data["results"].items():
        for horizon, row in horizons.items():
            for group, stats in row["threshold_groups"].items():
                fmt = lambda x: "—" if x is None else f"{x:.4f}"
                lines.append(f"| {field} | {horizon} 日 | {group} | {stats['n']} | {stats['event_date_clusters']} | "
                             f"{fmt(stats['median_relative_return'])} | {fmt(stats['mean_relative_return'])} | "
                             f"{fmt(stats['clustered_se'])} | {fmt(stats['mean_mae'])} | {fmt(stats['win_rate'])} |")
    lines += ["", "## 原始數值五分位", "",
              "| 訊號 | 期間 | 五分位 | N | 日期群 | 相對報酬中位數 | 相對報酬平均 | 分群標準誤 | MAE | 勝率 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for field, horizons in data["results"].items():
        for horizon, row in horizons.items():
            for group, stats in row["raw_value_quintiles"].items():
                fmt = lambda x: "—" if x is None else f"{x:.4f}"
                lines.append(f"| {field} | {horizon} 日 | Q{group} | {stats['n']} | {stats['event_date_clusters']} | "
                             f"{fmt(stats['median_relative_return'])} | {fmt(stats['mean_relative_return'])} | "
                             f"{fmt(stats['clustered_se'])} | {fmt(stats['mean_mae'])} | {fmt(stats['win_rate'])} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("--output"); ap.add_argument("--json",action="store_true")
    ap.add_argument("--historical-pit", action="store_true"); args=ap.parse_args(argv)
    data = historical_pit_event_study() if args.historical_pit else evaluate()
    text = json.dumps(data,ensure_ascii=False,indent=2) if args.json else (render_historical(data) if args.historical_pit else render(data))
    if args.output: Path(args.output).write_text(text,encoding="utf-8")
    else: print(text)
    return 0

if __name__ == "__main__": raise SystemExit(main())
