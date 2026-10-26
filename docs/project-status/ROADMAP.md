# Project roadmap

DATA_AS_OF: `2026-10-26`
LAST_VERIFIED_AT: `2026-10-26T21:21:10+08:00`
STATUS_TASK_ID: `STOCK-GITHUB-STATUS-CURRENTNESS-180`

## Next milestone

The next evidence milestone is `B177`: an independent ten-round verification of the `E169` offline FRED worker-readiness candidate. Passing that review would still not authorize live acquisition, formal source-policy changes, or merge; those require separate authority.

## Ordered roadmap

1. Publish this docs-only status snapshot as one Draft PR without touching `main`.
2. Run `B177` independently against the exact E169 ZIP and manifest; preserve the historical `B168 WORKER_NOT_READY` result.
3. Correct the five B176 lookahead/integration failures, then rerun an independent monthly-revenue qualification audit.
4. Complete real-browser GUI and pixel verification for C173 without adding formal data authority.
5. Resolve H175 writer exclusion and reader-migration prerequisites before proposing any T46 recovery execution.
6. Establish a trusted historical-time root and obtain approval for a formal forward-evaluation protocol before using G174 beyond synthetic tests.
7. Review D172 sources one by one; no source becomes formal through inventory inclusion alone.
8. Continue A171 under the approved `PHASED_10_TO_22` research sequence while retaining `9069 REFERENCE_ONLY`.

## GitHub maintenance SOP

- Update these documents after GPT accepts a handoff, recording exact Task ID, ZIP SHA-256, manifest SHA-256, evidence date, and source level.
- Preserve the original reason for every `BLOCKED` or `FAIL` state.
- Add new candidate evidence as a new dated fact; never overwrite historical failure.
- Keep CI, Draft PRs, formal releases, and shadow research visibly separate.
- Re-read remote `main`, authentication, PRs, Issues, workflows, and protection/rulesets before every GitHub write.
- If authentication, authority, isolation, or scope validation fails, publish nothing and report the blocker.

## README entry-point proposal

In a separately authorized documentation change, add a short README link to `docs/project-status/CURRENT_STATUS.md`. F180 does not edit README.

## Issue organization proposal

Use one Issue per governed dependency, labeled by lane (`lane-a` through `lane-h`), evidence state (`blocked`, `offline-candidate`, `independent-review`), and authority state (`needs-formal-approval`). Link exact Task IDs and hashes; do not upload large evidence ZIPs.

## PR dependency graph proposal

Represent dependencies in PR bodies using explicit `Depends on PR #N` and `Evidence task: TASK_ID` lines. Preserve the existing eight-PR candidate chain, but do not retarget, rebase, mark Ready, or merge those PRs as part of status publication.
