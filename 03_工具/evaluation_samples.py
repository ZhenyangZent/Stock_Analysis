"""Shared point-in-time sample eligibility for research evaluation engines.

This module never changes verdicts.  It centralises publication-time entry,
deduplication, and common-calendar horizon construction so evaluators cannot
silently disagree about which observations are eligible.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path


def parse_time(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def publication_time(record):
    """Use the first declared publication timestamp, never anchor_session."""
    return parse_time(record.get("published_at") or record.get("created_at"))


def earliest_unique(records, company_rows):
    """Yield the earliest publication for (company, anchor, rules_version)."""
    chosen = {}
    for record in records:
        published = publication_time(record)
        if published is None:
            continue
        version = record.get("protocol_version") or record.get("rules_version") or "UNKNOWN"
        for company in company_rows(record):
            company_id = company.get("security_id") or company.get("ticker")
            key = (company_id, company.get("anchor_session"), version)
            rank = (published, str(record.get("snapshot_id") or record.get("run_id") or ""))
            if key not in chosen or rank < chosen[key][0]:
                chosen[key] = (rank, record, company)
    return [(record, company, rank[0], key[2])
            for key, (rank, record, company) in sorted(chosen.items(), key=lambda item: item[1][0])]


def common_forward(stock_series, benchmark_series, published_at, horizon):
    """Return aligned total returns from the next common close after publication.

    `horizon` counts common trading intervals.  The entry close itself is the
    baseline, so horizon+1 common dates are required.
    """
    published = parse_time(published_at) if not isinstance(published_at, dt.datetime) else published_at
    if published is None:
        return None
    stock = dict(stock_series); benchmark = dict(benchmark_series)
    dates = sorted(set(stock) & set(benchmark))
    eligible = [date for date in dates if dt.date.fromisoformat(date) > published.date()]
    if len(eligible) <= horizon:
        return None
    path = eligible[:horizon + 1]
    start, end = path[0], path[-1]
    stock_path = [stock[date] / stock[start] - 1 for date in path[1:]]
    benchmark_path = [benchmark[date] / benchmark[start] - 1 for date in path[1:]]
    return {"entry_session": start, "end_session": end, "path_dates": path[1:],
            "stock_return": stock_path[-1], "benchmark_return": benchmark_path[-1],
            "excess_return": stock_path[-1] - benchmark_path[-1],
            "relative_path": [s - b for s, b in zip(stock_path, benchmark_path)]}


def next_session_after(available_at, sessions, market):
    cutoff = parse_time(available_at)
    candidates = []
    for row in sessions:
        if row.get("market") != market or row.get("session_status", "OPEN") != "OPEN":
            continue
        try:
            close = parse_time(row.get("close_at"))
        except (TypeError, ValueError):
            continue
        if close and close > cutoff:
            candidates.append((close, row.get("session_date")))
    return min(candidates)[1] if candidates else None


def sha256_file(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def price_cutoff(*series_groups):
    dates = [date for group in series_groups for series in group.values() for date, *_ in series]
    return max(dates) if dates else None
