# Release and publication gates

DATA_AS_OF: `2026-10-28`
LAST_VERIFIED_AT: `2026-10-28T01:03:27+08:00`
STATUS_TASK_ID: `STOCK-GITHUB-PR9-ONE-TIME-STATUS-PUBLICATION-215`
STATUS_STAGE: `N/A`

## PR #9 remote identity gate

| Gate | State | Evidence |
|---|---|---|
| Authentication | `PASS` | Active keyring account `ZhenyangZent`; token value not recorded |
| PR exists | `PASS` | Live readback returned PR #9 at `https://github.com/ZhenyangZent/Stock_Analysis/pull/9` |
| State and Draft | `PASS` | `OPEN`, `draft=true`, `mergedAt=null` |
| Base identity | `PASS` | `main` at `6e2740d9877364619ca046cb54829350fd0db5ef` |
| Head identity before F215 | `PASS` | `codex/project-status-currentness-20261026-180` at `56a18e260b5b8a20ce482f30d48ccdc8172d7cc7` |
| Commit list before F215 | `PASS` | One existing commit; its history is not rewritten |
| Changed paths and six blobs | `PASS` | Live PR files API returned exactly the six allowed status documents and expected blob SHA-1 values |
| Protected code/data changes | `PASS` | None in live PR scope or F215 candidate diff |
| Merge | `PROHIBITED` | No merge authority |
| Ready action | `PROHIBITED` | PR must remain Draft |
| Remote main drift | `PASS` | `origin/main` equals PR base `6e2740d9877364619ca046cb54829350fd0db5ef` |
| Current CI checks | `NONE_REPORTED` | `gh pr checks 9` reports no checks on the PR branch |
| Branch protection/rulesets | `ABSENT_AS_READ` | Traditional protection returned 404; repository rulesets `[]`; F215 makes no settings change |

## Evidence and identity gates

| Gate | State | Meaning |
|---|---|---|
| F187 pinned ZIP | `PASS` | SHA-256 equals `81c964463430638b299485cfe2c152178cde7ea76fb37b14ef64ae37000496d6` |
| F187 manifest | `PASS` | SHA-256 equals `ce40bc044037ad1c6636abe3d4118fa749bbdc2c2b9482e18da503ea0beab279` |
| F187 GPT acceptance | `PASS` | ZIP/manifest/13 payloads, patch application, and 6/6 document identity accepted |
| F191 pinned ZIP and manifest | `PASS` | Exact pinned identities and 13 declared payloads verified |
| F191 GPT acceptance | `PASS` | ZIP/manifest/13 payloads and 6/6 patch reconstruction accepted |
| F191 Stage 2 pinned ZIP and manifest | `PASS` | Exact identities and 13 declared payloads verified |
| F191 Stage 2 GPT acceptance | `PASS` | 13/13 payloads, 6/6 Git blob baseline, 6/6 reconstruction, and docs-only scope accepted |
| F191 Stage 3 pinned ZIP and manifest | `PASS` | Exact ZIP, manifest, and 12 declared payload identities verified |
| F191 Stage 3 GPT acceptance | `PASS` | Six Git blobs and patch reconstruction accepted |
| F191 Stage 4 pinned ZIP and manifest | `PASS` | Exact ZIP and 12 declared payloads verified |
| A194 pinned ZIP and manifest | `PASS` | Exact ZIP and 31 declared payloads verified |
| A194 acceptance | `PASS_WITH_LIMITATION` | Accepted synthetic Python 3.13 scope; Python 3.12 runtime and formal PIT excluded |
| E197 pinned ZIP and manifest | `PASS` | Exact ZIP and 33 declared payloads verified |
| E197 acceptance | `PASS_WITH_LIMITATION` | Accepted verify-only offline scope; launch/network/operational trust excluded |
| B198 pinned ZIP and manifest | `PASS` | Exact ZIP and 132 declared payloads verified |
| B198 release gate | `FAIL` | Malformed non-integer PID bypassed common cleanup; full 195 regression and ten sequences not run |
| E199 offline candidate identity | `PASS_CODEX_REPORTED` | Exact local ZIP and 322 declared payloads verified; independent acceptance absent |
| A201 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 33/33 declared payloads, zero missing/hash/size/CRC errors |
| B200 local identity and release gate | `FAIL_RELEASE_GATE` | Exact ZIP/manifest; 1,976/1,976 payloads verified; full regression 227/228 and ten sequences not run |
| C203 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 42/42 payloads; archived tests 68/68; browser GUI QA unverified |
| D204 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 17/17 payloads; offline 86/86; real provider bytes 0; formal approval false |
| E208 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 28/28 payloads; synthetic staging 31/31; OS outbound isolation unverified |
| G206 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 11/11 payloads; offline tests 45/45; mature real outcomes 0 |
| H207 local identity | `PASS_CODEX_REPORTED` | Exact ZIP/manifest; 9/9 payloads; T46 recovery remains blocked |
| A184/E188/G183/H185 pinned ZIP identities | `PASS` | All four equal the F191 pinned hashes |
| A184/E188/G183/H185 manifest payloads | `PASS` | 79/79 declared payloads; missing/mismatch/duplicate/unsafe/undeclared = 0 |
| A189 acceptance identity | `PASS` | `ACCEPTED_CORE_VALUE_BINDING`; `PARSER_HARDENING_REQUIRED` preserved |
| E190 acceptance identity | `PASS` | `OFFLINE_CANDIDATE_ACCEPTED`; `EXTERNAL_TRUST_ROOT_GAP` preserved |
| A192 limited acceptance | `PASS` | 35/35 synthetic parser negative tests; function accepted within tested scope |
| A192 remaining gates | `BLOCKED` | A189 fixture omitted; clean-extract self-contained replay not fully accepted; historical/production PIT unauthorized |
| E193 limited acceptance | `PASS` | Limited offline prototype and original 15 new tests accepted |
| E193 remaining gates | `BLOCKED` | Real symlink aliases not rejected; launch prohibited; operational trust unverified |
| E195 evidence and acceptance | `NOT_VERIFIED` | Assigned, awaiting handoff, not accepted |
| B177 pinned ZIP and manifest | `PASS` | Exact archive, manifest, and 111 declared payload identities verified |
| B177 single regression | `PASS_WITH_LIMITATION` | 179/179 pass; single run only |
| B177 negative preflight | `FAIL` | 6/12 pass and 6/12 fail; marker mismatch leaves an orphan process |
| B177 ten-sequence gate | `NOT_RUN` | Stopped because negative preflight failed |
| B177 release gate | `FAIL` | `FAIL_RELEASE_GATE_MARKER_MISMATCH_ORPHAN` |
| E196 archived preflight evidence | `PASS_WITH_LIMITATION` | 16/16 archived checks passed before B198; does not override B198 release-gate failure or grant network authority |
| GitHub blob identity | `PASS` | Six live Git blob SHA-1 values captured from PR files API |
| F180 raw blob/document equality | `FAIL` | Each F180 ZIP document has one additional trailing LF |
| F180 normalized trailing-LF equivalence | `PASS` | Removing exactly one ZIP-only final LF yields each Git blob byte sequence |
| SHA field separation | `PASS` | Blob SHA-1, byte SHA-256, document SHA-256, archive SHA-256, and manifest SHA-256 remain distinct |

## Historical F215 authorized publication gate (consumed)

| Gate | State |
|---|---|
| Isolated document update | `PASS` |
| Exact candidate paths | `PASS — docs/project-status/ only` |
| Latest required status coverage | `PASS` |
| F187 incorporated | `PASS` |
| F191 accepted base incorporated | `PASS` |
| F191 Stage 2 accepted base incorporated | `PASS` |
| F191 Stage 3 accepted base incorporated | `PASS` |
| B177 current failure incorporated | `PASS` |
| A194 current accepted scope incorporated | `PASS` |
| B198 release-gate failure incorporated | `PASS` |
| E197 accepted scope and E199 candidate boundary incorporated | `PASS` |
| A201/B200/C203/D204/E208/G206/H207 bounded currentness incorporated | `PASS` |
| B202 and A211/C212/D213/E209/E214/G216/H217 pending states incorporated | `PASS` |
| Secret/private-path scan | `PASS` |
| Patch apply and whitespace check | `PASS` |
| New exact Git write authority | `GRANTED_ONCE_FOR_SIX_FILES` |
| Maximum normal commits | `1` |
| Maximum normal push attempts | `1` |
| Main/Ready/merge/force push | `PROHIBITED` |

## Research gates

| Area | Gate |
|---|---|
| A201 | `SHADOW_RESEARCH_ONLY`; 21 companies; 12 industry segments; 54 hypotheses; A211 awaiting evidence; formal universe/PIT false |
| B176 | `HISTORICAL_LOOKAHEAD_INTEGRATION_FAIL` |
| B177 | Historical `FAIL_RELEASE_GATE_MARKER_MISMATCH_ORPHAN` preserved |
| B198 | `FAIL_RELEASE_GATE_MALFORMED_PID_TYPE_UNHANDLED`; B177 12/12 and E196 16/16 preflight pass; full 195 regression and ten sequences `NOT_RUN`; `B202_PENDING_EVIDENCE` |
| B200 | `FAIL_RELEASE_GATE`; full regression 227/228; ten sequences `NOT_RUN`; B202 `WAITING_FOR_EVIDENCE` |
| C173 | `OFFLINE_ACCEPTED`; `GUI_NOT_VERIFIED` |
| C203 | archived tests 68/68; `GUI_BROWSER_QA_NOT_VERIFIED`; C212 awaiting evidence |
| D182 | `OFFLINE_SOURCE_CONTRACT_ACCEPTED`; `REAL_NETWORK_AUTHORITY_FALSE` |
| D204 | offline contract tests 86/86; `REAL_PROVIDER_BYTES=0`; `FORMAL_APPROVAL=false`; D213 awaiting evidence |
| E197 | `ACCEPTED_OFFLINE_VERIFY_ONLY`; `104_OF_104_WINDOWS_PASS`; `LAUNCH_PROHIBITED`; `NETWORK_PROHIBITED`; `OPERATIONAL_TRUST_NOT_VERIFIED` |
| E199 | `OFFLINE_CANDIDATE_NOT_INDEPENDENTLY_ACCEPTED`; development self-test 228/228; B200 failure retained |
| E208 | synthetic staging 31/31; `OS_OUTBOUND_ISOLATION_NOT_VERIFIED`; E209/E214 awaiting evidence |
| E169 | `FRED_OFFLINE_CANDIDATE`; `B177_RELEASE_GATE_FAILED`; `NETWORK_AUTHORITY_FALSE` |
| G183 | `SYNTHETIC_RECEIPT_V2_PASS`; `PRODUCTION_TRUST_ROOT_ABSENT` |
| G206 | offline evaluation 45/45; `MATURE_REAL_OUTCOME_COUNT=0`; `PREDICTIVE_VALUE_NOT_ESTABLISHED`; G216 awaiting evidence |
| H207/T46 | `WEEKLY_SCHEDULER_ENABLED_AS_LAST_VERIFIED`; historical 708-row ledger; `RECOVERY_BLOCKED`; H217 awaiting evidence |
| Formal universe change | `NOT_APPROVED` |
| Formal forward evaluation | `NOT_APPROVED` |

F215's one-time publication authority has been consumed. F233 has no Git write authority.

## F233 incremental gates

| Gate | State |
|---|---|
| PR #9 live state | `PASS — OPEN, draft=true, merged=false` |
| PR #9 head/base | `PASS — c68f24acb6d3a1b8ed8f2aa6b2665a9fe8fe0f2a / 6e2740d9877364619ca046cb54829350fd0db5ef` |
| Six live blobs vs F215 receipt | `PASS — 6/6 exact` |
| Remote main drift | `PASS — no drift` |
| PR checks | `NONE_REPORTED` |
| Main traditional branch protection | `ABSENT` |
| Repository rulesets | `EMPTY` |
| A221 Stage 2 integrity | `PASS`; synthetic candidate only |
| B223 canonical handoff | `BLOCKED_INPUT`; extracted blocked decision retained |
| E225 integrity and erratum | `PASS`; offline candidate only |
| E227 integrity | `PASS`; trusted test harness only |
| H226 canonical handoff | `BLOCKED_INPUT`; T46 remains `RECOVERY_BLOCKED` |
| Scheduler last hash-bound state | `Disabled`; `Enabled=false` from H218 receipt |
| New Git write authority | `NOT_GRANTED` |
| Commit/push/PR update | `PROHIBITED` |

Any later publication requires a fresh exact one-time authority, immediate remote identity recheck, docs-only changed-path/security gates, and preservation of Draft/unmerged state. No authentication state by itself grants that authority.

## F241 current release and authority gates

| Gate | State |
|---|---|
| F233 input archive | `PASS — 15 payloads` |
| PR #9 pre-publication identity | `PASS — OPEN, draft=true, merged=false, head c68f24acb6d3a1b8ed8f2aa6b2665a9fe8fe0f2a` |
| D236/A237/E232 Resumed/G234/E239/G240 archive integrity | `PASS` |
| B228 canonical archive identity | `BLOCKED_INPUT`; status recorded as user-supplied only |
| B228 offline trusted harness | `PASS_OFFLINE_TRUSTED_HARNESS_RELEASE`; 10/10 sequences; 2710/2710 assertions |
| B228 production FRED/network authority | `false` |
| B238 Stage 2 | `AWAITING_EVIDENCE` |
| E232/E239 accepted numeric feature count | `0` |
| Real approved market observation count | `0` |
| Formal PIT issued | `false` |
| Formal source policy approved | `false` |
| G234/G240 formal activation | `false` |
| Predictive value established | `false` |
| Weekly scheduler | `Disabled`; `Enabled=false` |
| T46 recovery | `RECOVERY_BLOCKED` |
| F241 Git write authority | `ONE_NORMAL_COMMIT_AND_ONE_NORMAL_PUSH_ONLY` |
| Ready/merge/main/force push/CI/security authority | `false` |

F241 publication changes status documentation only. A successful push consumes the one-time authority and grants no follow-on Git write.
