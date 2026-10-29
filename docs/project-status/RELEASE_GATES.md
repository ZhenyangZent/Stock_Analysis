# Release and publication gates

DATA_AS_OF: `2026-10-30`
LAST_VERIFIED_AT: `2026-10-30T01:57:46+08:00`
STATUS_TASK_ID: `STOCK-PR9-ROUND37-STATUS-CORRECTION-328`
STATUS_STAGE: `A315_H322_ROUND37_STATUS_CORRECTION`

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

## F286 standing-publication and release gates

| Gate | State | Evidence |
|---|---|---|
| Standing docs-only authority | `true` | Explicit user authorization; limited to material updates under `docs/project-status/*.md` and `docs/handoffs/*.md` |
| PR #9 live identity before batch | `PASS` | `OPEN`, `draft=true`, unmerged; head `79e5eae814eab37138eefe0063c7629625cbaacf`; base/main `6e2740d9877364619ca046cb54829350fd0db5ef` |
| Authentication | `PASS` | Active account `ZhenyangZent`; token value not recorded |
| A280 offline gate | `PASS — 40/40` | `GPT_ACCEPTED_OFFLINE`; no official certification |
| B279 F276 release gate | `FAIL` | 24/27 fresh passed, three failed; corrupt ZIP CRC metadata accepted and published in isolated QA |
| F276/F283 collector release | `BLOCKED` | Self-tests and package integrity do not override B279 independent failure |
| C281 Node runtime candidate | `PASS_WITH_GAP` | Original TypeError reproduced and fixed candidate verified; GUI `NOT_VERIFIED` |
| D282 provider identity | `BLOCKED` | Exact API-resource bytes unverified; real provider bytes `0` |
| E278 synthetic completeness | `PASS_OFFLINE_ONLY` | 18/18 synthetic; parser/source/PIT/production approval absent |
| G284 real-outcome evaluation | `BLOCKED` | 22 decisions pending; mature real outcomes `0`; predictive value not established |
| H285 Reader migration | `BLOCKED` | Formal migration `0/12`; identity/owner approval absent; T46 `RECOVERY_BLOCKED` |
| Formal source policy | `false` | No approval in this batch |
| Formal PIT issued | `false` | No issuance in this batch |
| Real approved market observations | `0` | No formal data authority in this batch |
| Ready/merge/main/force push/CI/security authority | `false` | Explicit standing-authority exclusions |

The standing publication authority removes the need to request another docs-only permission for every material batch; it does not remove any identity, evidence, path, consistency, security, or remote-state gate. A failed preflight blocks that batch without weakening historical records.

## F295 repository regression recovery

| Gate | Status | Evidence / boundary |
|---|---|---|
| Clean-head failure reproduction | `PASS` | Both F286 failures reproduced from PR #9 head without the external raw prerequisite |
| Official fixture provenance | `PASS` | Existing trusted raw document matched SHA-256 `68117c2ed0caa105921d9cfe27afe9ed02472dcc33066ead9b31e1603a77357c`; no fixture was fabricated or committed |
| Saved official fixture parser | `PASS — 1/1` | Passed after trusted raw-store mount |
| Patch183 active-row preservation | `PASS — 1/1` | 2,722 active IDs were a subset of 2,840 recomputed IDs; missing count 0 |
| Full repository regression | `PASS — 669/669` | Completed with trusted raw prerequisite and ASCII-only temporary directory |
| B279 collector release gate | `FAIL — PRESERVED` | Historical 24/27 result and corrupt-CRC acceptance remain controlling for F276 |
| F287 independent acceptance | `BLOCKED` | B291 evidence not yet accepted |
| Formal source/data/PIT authority | `false` | Regression recovery grants no formal authority |

The minimum durable remediation is a canonical, hash-bound external fixture-bundle contract plus fail-fast prerequisite diagnostics. The `active <= new_ids` assertion must remain unchanged; missing raw inputs must not be hidden by skipping or weakening the test.

## F312 candidate and publication gates

| Gate | Status | Evidence / boundary |
|---|---|---|
| F306 central location/integrity | `PASS — 8/8` | Exact pinned SHA-256, CRC, manifest payload, and central index checks; packaging only |
| B299 independent importer QA | `FAIL — 39/43` | Four failures preserved: two type exceptions, output reparse alias, incomplete cleanup |
| E302 strict-type candidate | `PASS — 70/70 CODEX_REPORTED` | Repairs the two type exceptions only; reparse-path and cleanup gaps remain open |
| F303 runner compatibility | `PASS_IN_TESTED_SCOPE_NOT_DEPLOYED` | B291 direct 19/19; packaged 9/9 and 14/14; CRC/concurrency/index pass |
| F303 shared-tool replacement | `BLOCKED_AUTHORITY` | Candidate SHA known; explicit shared `TOOLS` replacement authority absent |
| C300 GUI QA | `NOT_VERIFIED` | Offline 89/89 does not cover browser/file-picker/pixel/accessibility interaction |
| D301/E302 official-source admission | `BLOCKED` | Real provider bytes 0; encoding/dialect/publication/revision/parser authority absent |
| G304 prospective value | `NOT_VERIFIED` | Synthetic 28/28; real observations 0; predictive value not established |
| H305/T46 recovery | `BLOCKED` | Documentation only; no Provider Admission, Reader migration, backup, or recovery authority |
| Formal approval across A298-H305 | `false` | No exact formal-authority record supplied |

B299 remains a failed release gate even though the exact ZIP and F306 location checks pass. ZIP integrity, local candidate tests, and documentation publication are distinct gates.

## F320 Round 36 candidate and publication gates

| Gate | Status | Evidence / boundary |
|---|---|---|
| Same-task deduplication | `PASS` | No F320 central handoff or PR commit existed before this batch |
| PR #9 initial identity | `PASS` | `OPEN`, `draft=true`, unmerged; head `2dafa6c5d5abe5ba95d0632a92e89ce784893590`; base/main `6e2740d9877364619ca046cb54829350fd0db5ef` |
| Pinned archive integrity | `PASS — 8/8` | 167/167 declared payloads; SHA-256, size, CRC, path, duplicate/casefold, undeclared-file, and task-ID gates passed |
| A307 offline plan | `PASS_OFFLINE_CODEX_REPORTED` | No real observations or live fetch; not formally approved |
| B308 independent QA | `FAIL — 15 PASS / 4 FAIL` | Package self-containment failed; B299 history preserved |
| C309 GUI acceptance | `NOT_VERIFIED` | 110/110 offline checks do not cover Windows GUI/browser/file picker |
| D310 provider admission | `BLOCKED` | Synthetic contract passed 25/25; real provider bytes and parser/source approval absent |
| E311 junction/release | `BLOCKED` | 59 checks passed; the missing E302 baseline is `STATIC_PACKAGING_DEPENDENCY_GAP`; the separate Windows junction case is `ACCESS_DENIED_NOT_VERIFIED`, not an observed runtime PASS or FAIL |
| F312 publication identity | `PASS` | Existing docs-only commit `2dafa6c5d5abe5ba95d0632a92e89ce784893590`; historical 822/822 remains archived evidence |
| G313 predictive value | `NOT_VERIFIED` | Synthetic 12/12 fresh; real observations and mature real outcomes `0` |
| H314 source/PIT/Reader | `BLOCKED` | Receipt helper only; Reader Ready/Migrated `0/12`; T46 `RECOVERY_BLOCKED` |
| F303 shared collector deployment | `BLOCKED_AUTHORITY` | Candidate remains undeployed |
| Formal observations/provider bytes/outcomes | `0 / 0 / 0` | No formal activation record in Round 36 |
| Allowed publication paths | `docs/project-status/*.md` | This batch is restricted to the six existing status documents |
| Ready/merge/main/force push/CI/security authority | `false` | Explicit standing-authority exclusions |

One failed evidence lane does not prohibit truthful documentation of that failure. Publication remains conditional on a clean isolated worktree, trusted external MOPS fixture prerequisite, fresh full repository regression, exact six-path diff, consistency/whitespace/security scans, and immediate pre-write remote identity recheck.

## F328 Round 37 candidate and publication gates

| Gate | Status | Evidence / boundary |
|---|---|---|
| Same-task deduplication | `PASS` | No F328 commit, central handoff, or index record existed before this batch |
| Initial PR #9 identity | `PASS` | `OPEN`, `draft=true`, unmerged; head `9a857bf5d4bfdcbbc5cb633b91c88dd0d0c6b1db`; base/main `6e2740d9877364619ca046cb54829350fd0db5ef`; no unexplained drift |
| Pinned archive integrity | `PASS — 8/8` | 312/312 declared payloads; outer and manifest SHA-256, size, CRC, path, duplicate/casefold, undeclared-file, and task-ID gates passed |
| A315 offline candidate | `PASS_OFFLINE_CODEX_REPORTED — 39/39` | No formal admission, real observation, or financial certification |
| B316 independent QA | `FAIL — 24 PASS / 2 FAIL / 2 NOT_VERIFIED` | Two observed parent-swap raw-only residue failures; original clean-extract static dependency failure; junction access denied/not verified |
| C317 offline interoperability | `PASS_WITH_GUI_GAP` | Archive `74/74`; independent JS `43/43`; Python `28/31` then supplemented `31/31`; GUI not verified |
| D318 synthetic contract | `PASS_OFFLINE_CODEX_REPORTED — 19/19` | Real provider bytes and formal source/PIT authority absent |
| E319 integration/release | `BLOCKED` | `111 PASS / 0 FAIL / 1 NOT_VERIFIED`; B316 independent failures remain controlling |
| G321 synthetic evaluation | `PASS_OFFLINE_ONLY — 21/21` | Real observations/outcomes zero; predictive value not established |
| H322 Windows receipt QA | `FAIL_WITH_NOT_VERIFIED — 15/1/3` | Aggregate `ConvertTo-Json` stall; receipt-semantics mismatch; link/race cases not verified |
| Historical independent failures | `PRESERVED` | B299, B308, and B316 remain distinct; no later package-integrity result erases them |
| F303 shared collector deployment | `BLOCKED_AUTHORITY` | Candidate remains undeployed |
| T46 / 9069 | `RECOVERY_BLOCKED / REFERENCE_ONLY` | No mutation or promotion authority |
| Formal observations/provider bytes/outcomes | `0 / 0 / 0` | No formal activation record in Round 37 |
| Predictive value | `NOT_ESTABLISHED` | No mature real-outcome evidence |
| Allowed publication paths | `docs/project-status/*.md` | F328 is restricted to the six existing status documents |
| Ready/merge/main/force push/CI/security authority | `false` | Explicit standing-authority exclusions |

Truthfully publishing a failed evidence lane is allowed, but Git publication remains conditional on fresh repository regression with the hash-bound external MOPS fixture, exact six-path scope, document consistency, whitespace/security scans, and an immediate no-drift readback.
