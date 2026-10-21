# START HERE — Durable AI handoff entry

DOCUMENT_STATUS: `CANONICAL_HANDOFF_DOCUMENT`
TARGET_PATH: `90_各AI工作區/codex/START_HERE.md`
AS_OF_UTC: `2026-10-20T14:33:07Z`

## 1. Purpose and boundary

This repository implements a reproducible, point-in-time, append-only investment research workflow shared by multiple AI systems. Models collect and cite evidence; deterministic code applies frozen rules. Missing values are never silently converted to zero. The system does not issue buy/sell instructions, target prices, return guarantees, or automatic trades.

This page is an entry map, not a second decision authority. Formal authority remains the user's latest explicit authorization, `00_規範/使用者決定紀錄.md`, the applicable current and archived rules, canonical ledgers, and committed formal snapshots.

## 2. Historical evidence baseline — not a live-state assertion

The following facts were independently read at the stated cutoff. Each claim is bound to reproducible evidence:

- Git baseline `main` at `6e2740d9877364619ca046cb54829350fd0db5ef`; evidence: original audit ZIP `9ba5588e968c84a8ac032eeeb798e4882689ad2a41eaf170d97634d9f062b03b`, member `01_AUDIT/git_state.json`.
- Rules `1.13.2`, SHA-256 `fe072304ff62092b355d1c9c844227eb3bee1a7abc1475d0888d50291885e4d1`; evidence: the same ZIP, member `02_RULES/rules.json` and repository readback at `2026-10-20T14:33:07Z`.
- Formal snapshot `SNAP_20261019T174908Z_275da2dd`, input manifest SHA-256 `275da2dd925777f40131e63cc1d6421bf1d71d5d3712409b477cf9d218f5df0f`, snapshot SHA-256 `988062b8c3a6e329034ff1c683f55227a88b1c1c871ad048cab95c450521a383`, committed marker SHA-256 `dd820e06cae6021b9c54d95828025d655a090181b38dcc365d93416bef92a171`; evidence: the same ZIP under `04_FORMAL_SNAPSHOT/` and repository readback at `2026-10-20T14:33:07Z`.
- T46 immutable ledger: 706 rows, SHA-256 `0b7f5242ad52557b6a07f7b0abe4d6fcf9821b09589a997cd7a57563676f3b88`; verified prefix 107 rows, 108430 bytes, SHA-256 `e5d5e81a1859a0b4a708c9880428c1353be51b74820451ac13d127219acd87b9`. Corrected derived statistics are in `reports/T46_DERIVED_ANALYSIS_CORRECTION_V2.md` and `reports/T46_DERIVED_ANALYSIS_V2.json`.

Historical verification does not prove that the same gate still passes now.

## 3. Mandatory live-state refresh on every takeover

Before any formal operation, independently read and record:

1. `git rev-parse HEAD`, branch, `origin/main`, and full worktree status.
2. `00_規範/rules.json` version and SHA-256, plus byte equality with its archived version.
3. The latest directory containing a valid `COMMITTED.json`; verify its committed marker, input manifest, snapshot hashes, cutoff and rule version.
4. `python 03_工具/lock.py status`, the lock file SHA when present, relevant lock-history records, and whether the holder is active or expired.
5. The exact authority, writable paths, protected scope, append-only gates, and reviewer requirement for the new task.

Do not copy a date-sensitive value from this page into a new terminal report without live readback.

## 4. Conflict stop rule

If the formal decision record, current rules, archived rules, canonical ledger semantics, or a handoff summary disagree, stop the affected formal operation. Report the exact conflicting paths and hashes and request reconciliation. Never choose the more convenient version, silently merge them, or treat a summary as superior to formal authority.

## 5. Evidence states

- `VERIFIED`: direct evidence or a deterministic recomputation is identified by path/member and complete SHA-256.
- `INFERRED`: derivation is stated but direct proof is unavailable.
- `UNKNOWN`: absent or unverified; never guess.
- `BLOCKED`: a concrete blocker exists; list its release condition and prohibited actions.
- `SHADOW`: observation only; no effect on formal classification or trading.
- `SUPERSEDED`: retained history points to the replacing decision/report and explains the scope.

A previous `PASS` becomes historical evidence when code, inputs, rules, environment, or authority changes. It is not automatically a current `PASS`.

## 6. Project map

- Authority and rules: `00_規範/`.
- Canonical structured data and ledgers: `01_共用資料/` and `01_共用資料/data_spine/`.
- Formal and shadow executions: `02_執行紀錄/`.
- Fetch, validation, computation and audit tools: `03_工具/`.
- Outcomes and backtest artifacts: `04_回測/`.
- Model-specific drafts: `90_各AI工作區/`.
- Raw source material: `99_原始材料/`; normally excluded from public Git.

## 7. Verified work that should not be repeated without cause

- Reuse a committed snapshot and its frozen CAS/manifest when the question is about that historical cutoff and the hashes still match.
- Reuse official-filing extractions and valuation paths that have direct source hashes and an independent review, unless their revalidation trigger fires.
- Preserve user decisions and supersede chains; do not erase and re-decide them because a new model joined.
- Preserve the original T46 audit ZIP and ledger bytes. Derived corrections must be additive and must never rewrite the incident evidence.

## 8. Revalidation triggers

Revalidate the affected claim when any of these changes: Git HEAD, relevant source file, rule hash, schema, canonical input prefix, snapshot manifest, Python major.minor, timezone database semantics, external source revision, lock/authority state, or an explicit acceptance gate. Also revalidate when a cited artifact cannot be restored by hash.

## 9. Current blockers and open risks at the historical baseline

T46 remains blocked for formal use. The following compact register preserves enough evidence for takeover even when an external review ZIP is unavailable. Every item is a static finding; the dynamic test is `NOT_RUN`, so none is a claim of live-environment reproduction.

| Risk | Source and SHA-256 | Static fact | Dynamic validation | Prohibited action | Repair and unblock acceptance |
|---|---|---|---|---|---|
| A — weekly dry-run enters live scan | `03_工具/weekly_refresh.py`; `adf617a4b8003167b8bc0ecfd9aadf147d7981b7f487a71abceb1a1aa3ace0a5` | Dry-run skips market fetches but still calls `earnings_docs_watch.py --now`. | `NOT_RUN` | Do not treat weekly dry-run as zero-write or run it against formal data. | Repair must bypass/freeze the scanner in dry-run; an isolated integration test must prove zero network, project writes and lock mutation while producing a preview. |
| B — scan failure may not propagate | `03_工具/weekly_refresh.py`; `adf617a4b8003167b8bc0ecfd9aadf147d7981b7f487a71abceb1a1aa3ace0a5` | Scanner failure sets `stopped_at` but may not enter `steps`; overall status can remain PASS and exit zero. | `NOT_RUN` | Do not accept weekly PASS without checking scanner status and `stopped_at`. | Inject scanner exit 1; require FAIL, nonzero process exit, no later steps and released lock. |
| C — recovery moves before holder gate | `90_各AI工作區/codex/T46_recover_first_scan.py`; `b8b161f2ea74f94a8073938cb99820bac31cfe4a988fbfad30d58d27c072745b` | Quarantine directories/files may be created or moved before `commit_candidate` validates the writer lock. | `NOT_RUN` | Do not run the recovery candidate or infer safety from prefix assertions. | Validate holder/scope before any side effect; wrong holder must cause zero changes, injected failures must restore all files, and correct holder must pass prefix/write-ledger gates plus independent review. |
| D — first scan lacks historical baseline | `03_工具/earnings_docs_watch.py`; `b21c8e50ad6633fe3a3e9c1afd7d124cb315cddf18ce475ef33d91b6ab3cd085` | Empty issuer history can accept all eligible historical filings as new. | `NOT_RUN`; incident evidence was preserved, not re-executed. | Do not first-scan an issuer without an approved baseline policy. | Add explicit baseline-without-queue and forward-scan modes; empty-ledger fixture must baseline without queueing history and later scan must append only newer documents. |
| E — snapshot currentness is not docs-ledger-bound | `03_工具/research_snapshot.py`; `db1617668ae85b9451125bac59706e504a3f25bd74f2f307bcfc7a6a666f3f0b` | Formal snapshot inputs do not bind the docs ledger and build does not enforce field currentness from frozen documents. | `NOT_RUN` | Do not claim formal snapshot AI fields are docs-ledger-current solely from snapshot OK. | Bind docs-ledger prefix/CAS in the manifest; changing only it must change input hash/reason, stale values must be rejected, and exact replay must reproduce bytes. |
| F — M4/M5 lack initial-vintage binding | `03_工具/market_env.py`; `91e13ea7736bdf747793b1ddc7a6e384a97aca92e2fd7fe150e63e01843477a0` | Formal M4/M5 still use macro series and lag proxies rather than a hash-bound initial-release vintage path. | `NOT_RUN` | Do not call historical formal M4/M5 strict initial-vintage results. | Bind a vintage ledger with release timestamps; fixtures must select initial values as of cutoff, prevent later revisions leaking backward, bind manifest bytes and replay byte-identically. |

External evidence restore status: `UNKNOWN_NOT_INDEPENDENTLY_RESTORABLE`. No reviewed evidence proves that the audit/review ZIPs have a second independently restorable backup. Recording a GitHub hash or an index does not preserve the underlying raw object and is not a backup.

Lock history is contradictory: the stale `codex_t46` record was present in the audit, absent at one later readback, and later present again with the same bytes. `LOCK_CHANGE_ATTRIBUTION` is `UNKNOWN`; absence alone does not resolve T46.

## 10. Decision traceability

Every formal decision must retain: reason, alternatives, trade-offs, user approval evidence, affected hashes/paths, effective scope, supersedes/superseded-by links, implementation evidence, and revisit triggers. ADRs explain the decision but do not replace `00_規範/使用者決定紀錄.md`.

## 11. Next action

A new task must obtain its own authority. Until then, do not mutate canonical data, rules, snapshots, the T46 ledger, Git history, or the collaboration lock.
