# Continuous status publication policy

DATA_AS_OF: `2026-10-29`
LAST_VERIFIED_AT: `2026-10-29T15:38:16+08:00`
STATUS_TASK_ID: `STOCK-PR9-REGRESSION-BLOCKER-TRIAGE-295`
AUTHORITY_STATUS: `STANDING_AUTHORIZED_DOCS_ONLY_PUBLICATION`

## Scope

The F workstream may publish material project-status, governance, acceptance, or handoff updates to the existing Draft PR #9 without requesting a new permission for every batch. Allowed paths are limited to:

- `docs/project-status/*.md`
- `docs/handoffs/*.md`

This authority does not include `main`, merge, Ready, force push, code, `rules.json`, CI workflows, GitHub security, formal data, ledger/snapshot, PIT issuance, source-policy promotion, market capture, or T46 mutation. Other workstreams must not write concurrently to PR #9.

## Required batch preflight

Before every publication batch, F must:

1. Verify the authenticated GitHub account is `ZhenyangZent`.
2. Re-read PR #9 and confirm `OPEN`, `draft=true`, unmerged, base `main`, exact live head/base SHA, head owner, current commit list, checks, and changed paths.
3. Re-read remote `main`; stop on unexplained identity drift.
4. Use a clean isolated worktree at the exact PR head and obtain the repository-required `docs` scope lock.
5. Verify exact upstream ZIP, raw manifest, task ID, declared payload bytes/SHA-256, duplicate/casefold/path safety, and available acceptance identity.
6. Classify each record as `CODEX_REPORTED`, `GPT_ACCEPTED_OFFLINE`, `INDEPENDENT_FAIL`, `NOT_VERIFIED`, or `FORMAL_APPROVED`. Package integrity alone never upgrades acceptance.
7. Preserve historical FAIL and BLOCKED records. A later candidate or PASS must not erase an earlier failure.
8. Generate the exact diff, allow only the approved Markdown paths, and scan for secrets, credentials, private absolute paths, large/binary evidence, and unrelated changes.
9. Run document consistency, whitespace, Markdown-source, and repository regression checks required by current repository instructions. When regression depends on Git-excluded official raw fixtures, mount only a hash-bound trusted local bundle. Never fabricate fixtures, silently skip tests, or treat a missing prerequisite as a product-code failure.
10. Re-read the remote head/base/Draft state immediately before any Git write. Stop if it differs from the validated baseline.

## Publication transaction

For one eligible material batch:

1. Stage only the validated allowed Markdown paths.
2. Create one normal commit containing the consolidated batch.
3. Push once normally to the existing PR #9 head branch.
4. Do not force push, rebase unrelated work, retarget the PR, mark Ready, or merge.
5. Independently read back the remote PR state, head commit, base/main identity, changed paths, and checks.
6. Record the before/after SHAs, exact changed paths, commit/push counts, and any unresolved items in the local handoff evidence.

If the push fails, preserve the error and stop. Standing authority does not permit automatic force push or scope expansion.

## Materiality and classification rules

- Publish only new evidence, changed acceptance, changed blockers, changed authority, corrected identity, or material governance/handoff information.
- Do not publish a new batch when sanitized status documents would be semantically unchanged.
- `CODEX_REPORTED` is a task report, not independent acceptance.
- `GPT_ACCEPTED_OFFLINE` is bounded to the accepted offline test scope and is not production activation.
- `INDEPENDENT_FAIL` remains a release blocker until an exact remediation receives a new independent pass.
- `NOT_VERIFIED` means no passing claim may be made.
- `FORMAL_APPROVED` requires explicit formal authority; it cannot be inferred from tests, ZIP integrity, or GitHub publication.

## Evidence retention

Raw research ZIPs stay in the local evidence workspace by default. GitHub receives sanitized Markdown containing task IDs, complete SHA-256 identities, bounded results, acceptance classification, blockers, next dependencies, and authority boundaries. Never publish API keys, credentials, personal absolute paths, confidential data, or large raw evidence archives.
