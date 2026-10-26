# Stock_Analysis current status

DATA_AS_OF: `2026-10-26`
LAST_VERIFIED_AT: `2026-10-26T21:21:10+08:00`
STATUS_TASK_ID: `STOCK-GITHUB-STATUS-CURRENTNESS-180`
FORMAL_MAIN_SHA: `6e2740d9877364619ca046cb54829350fd0db5ef`
REMOTE_MAIN_SHA: `6e2740d9877364619ca046cb54829350fd0db5ef`
REMOTE_DRIFT_GATE: `PASS`

## Executive status

The repository's formal `main` remains the initial reproducible research import. The work listed below is evidence-backed offline or shadow research unless a row explicitly says `FORMAL_APPROVED`. No new formal universe, source policy, current-market signal, production data capture, or model release is approved by this status update.

`PHASED_10_TO_22` is the user-approved research strategy: ten pilot companies first, followed by eleven additional candidates. This approval governs research ordering only. It does not approve a change to the formal universe or source policy. `9069` remains `REFERENCE_ONLY`.

## Evidence classification

- `GPT_INDEPENDENTLY_VERIFIED`: explicitly accepted through an independent GPT review. None of A171–H175/B176/E169 is promoted to this class by F180.
- `CODEX_REPORTED`: supported by a hash-bound Codex handoff, but not independently accepted by GPT.
- `ARCHIVED_TEST_ONLY`: historical or synthetic test evidence; not proof of live or predictive performance.
- `NOT_VERIFIED`: the required verification has not occurred.
- `BLOCKED`: a recorded gate prevents promotion.
- `FORMAL_APPROVED`: explicit formal authority exists. Zero new items receive this status here.

## Current workstream snapshot

| Lane | Task | Current state | Evidence class | Formal authority |
|---|---|---|---|---|
| A | `A171` | `SHADOW_RESEARCH_ONLY`; 10 pilot companies plus 11 later candidates; `9069 REFERENCE_ONLY` | `CODEX_REPORTED` | `NOT_FORMAL_APPROVED` |
| B | `B176` | `FAIL`; 25/30 independent matrix checks passed and five lookahead/integration counterexamples failed | `CODEX_REPORTED` | `BLOCKED` |
| C | `C173` | `OFFLINE_ACCEPTED`; real browser GUI interaction and pixel QA remain `NOT_VERIFIED` | `CODEX_REPORTED` | `NOT_FORMAL_APPROVED` |
| D | `D172` | 16 source categories inventoried; 0 are `FORMAL_APPROVED` | `CODEX_REPORTED` | `NOT_FORMAL_APPROVED` |
| E | `E169` | `OFFLINE_CANDIDATE`; B168 historical failure is preserved; awaiting `B177` | `CODEX_REPORTED` | `BLOCKED` |
| F | `F170` | `NOT_PUBLISHED`; its prior run made 0 commits, 0 pushes, and 0 PRs | `CODEX_REPORTED` | `N/A` |
| G | `G174` | 17 synthetic tests passed; historical-time trust remains incomplete | `ARCHIVED_TEST_ONLY` | `BLOCKED` |
| H | `H175` | T46 live ledger grew from 706 to 708 rows; formal recovery remains `RECOVERY_BLOCKED` | `CODEX_REPORTED` | `BLOCKED` |

## Research facts that remain bounded

- Pilot coverage remains 10 companies, with 95 financial metrics in the existing research evidence.
- The offline financial display contains 60 metrics.
- The research-gap surface contains 160 cells, including 72 `MISSING` and 39 `PARTIAL` in the preserved snapshot.
- Taiwan monthly-revenue research has synthetic walk-forward evidence only. Real monthly-revenue predictive performance is not established.
- Formal current-period market-light availability is not established.
- No forward formal evaluation plan has been approved.

## GitHub live readback before F180 publication

- Authentication: active account `ZhenyangZent`; token value was not recorded.
- Remote `main`: `6e2740d9877364619ca046cb54829350fd0db5ef`.
- Pull requests: 8 open, all Draft; this count is the pre-publication readback for F180.
- Issues: 0.
- Listed GitHub Actions workflows: 0.
- Traditional branch protection on `main`: absent (`404 Branch not protected`).
- Repository rulesets: 0.

## Main versus candidates

Formal `main` contains none of the six status-center documents at this snapshot. The F180 candidate changes only `docs/project-status/`; it does not change research code, data, ledgers, snapshots, CI, security settings, the formal universe, or source policy. Open research PRs remain separate candidates and must not be treated as merged or formally released.
