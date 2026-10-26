# Release and publication gates

DATA_AS_OF: `2026-10-26`
LAST_VERIFIED_AT: `2026-10-26T21:21:10+08:00`
STATUS_TASK_ID: `STOCK-GITHUB-STATUS-CURRENTNESS-180`

## F180 status-document publication gate

| Gate | State | Evidence |
|---|---|---|
| GitHub authentication | `PASS` | Active keyring account `ZhenyangZent`; secret value was not recorded |
| Remote-main identity | `PASS` | Live `origin/main` equals `6e2740d9877364619ca046cb54829350fd0db5ef` |
| Worktree isolation | `PASS` | Dedicated worktree and F180 branch; only `docs/project-status/` is permitted |
| Evidence integrity | `PASS` | 4,088 declared ZIP payloads matched manifests; 0 missing/mismatch |
| Status-only authority | `PASS` | F180 explicitly permits one normal commit, push, and Draft PR after gates pass |
| Diff scope | `PASS` | Exactly six files under `docs/project-status/`; no other path is present |
| Secret/private-path scan | `PASS` | No token, credential, API key, private exact path, or evidence ZIP payload was found |
| Main write | `PROHIBITED` | Direct push to `main` is outside authority |
| Merge or Ready action | `PROHIBITED` | Requires separate authorization |

## Research gates

| Area | Current gate | Promotion requirement |
|---|---|---|
| A171 shadow research | `NOT_FORMAL_APPROVED` | Independent relationship/PIT review and separate universe authority |
| B176 monthly revenue | `FAIL` | Fix all five lookahead/integration failures and obtain independent pass |
| C173 cockpit | `GUI_NOT_VERIFIED` | Real-browser interaction and visual verification |
| D172 sources | `0_FORMAL_APPROVED` | Source-by-source authority, provenance, timing, revision, and PIT checks |
| E169 FRED worker | `BLOCKED_PENDING_B177` | Independent ten-round verification followed by separate acquisition authority |
| G174 forward evaluation | `SYNTHETIC_ONLY` | Trusted time root, approved protocol, and separately authorized real evaluation |
| H175 T46 recovery | `RECOVERY_BLOCKED` | Writer exclusion, reader migration, exact recovery authority, and post-action verification |

## Required CI roadmap — planning only

No workflow is deployed by F180. A future separately authorized CI change should require:

1. docs-only changed-path enforcement for status PRs;
2. Markdown link and format validation;
3. secret, credential, private-path, and oversized-file scanning;
4. evidence-reference schema validation for Task ID, ZIP SHA, manifest SHA, date, and source level;
5. fail-closed checks preventing `CODEX_REPORTED` from being labeled `GPT_INDEPENDENTLY_VERIFIED`;
6. branch protection or ruleset review before making any check required.

## Release checklist

- Re-read authentication and remote `main` immediately before push.
- Confirm the branch is not `main` and has no existing remote ref or PR.
- Confirm exactly six allowed paths and a clean cached diff check.
- Confirm security scan counts are all zero.
- Use one normal commit and one normal push; never force push.
- Create exactly one Draft PR targeting `main`.
- Verify the remote branch SHA and Draft PR state.
- Do not merge, mark Ready, retarget, deploy CI, or change security settings.
