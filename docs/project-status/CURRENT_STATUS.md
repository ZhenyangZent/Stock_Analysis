# Stock_Analysis current status

DATA_AS_OF: `2026-10-30`
LAST_VERIFIED_AT: `2026-10-30T01:57:46+08:00`
STATUS_TASK_ID: `STOCK-PR9-ROUND37-STATUS-CORRECTION-328`
STATUS_STAGE: `A315_H322_ROUND37_STATUS_CORRECTION`
LAST_VERIFIED_REMOTE_MAIN_SHA: `6e2740d9877364619ca046cb54829350fd0db5ef`
LAST_VERIFIED_PR9_HEAD_SHA: `9a857bf5d4bfdcbbc5cb633b91c88dd0d0c6b1db`
CURRENT_REMOTE_MAIN_SHA: `6e2740d9877364619ca046cb54829350fd0db5ef`
PR9_HEAD_BEFORE_F215_SHA: `56a18e260b5b8a20ce482f30d48ccdc8172d7cc7`

## Executive status

F215 re-read GitHub successfully before publication. Draft PR #9 is `OPEN`, `draft=true`, unmerged, based on `main` at `6e2740d9877364619ca046cb54829350fd0db5ef`, headed before F215 by `56a18e260b5b8a20ce482f30d48ccdc8172d7cc7`, and changes exactly six files under `docs/project-status/`. The PR reported no CI checks; traditional `main` branch protection returned 404 and repository rulesets were empty. F215 does not modify those governance settings.

F205's accepted offline patch remains the baseline. F215 added exact local ZIP/manifest identity for A201, B200, C203, D204, E208, G206, and H207 without promoting those Codex-reported artifacts to GPT-verified or formal status. B200 is `FAIL_RELEASE_GATE`; B202 remains awaiting evidence. F215's one-time six-document publication authority was consumed by commit `c68f24acb6d3a1b8ed8f2aa6b2665a9fe8fe0f2a`; it grants no future write, Ready, merge, main, CI, security, data, source-policy, PIT, or signal authority.

## Evidence classification

- `GPT_VERIFIED`: evidence whose exact acceptance scope was supplied by the GPT commander, including A194, B198, and E197 here.
- `CODEX_REPORTED`: hash-bound handoff evidence without a separately supplied GPT acceptance record.
- `ARCHIVED_WINDOWS_EVIDENCE`: Windows or synthetic execution evidence; not proof of production deployment or live performance.
- `NOT_VERIFIED`: required independent evidence or acceptance identity is absent.
- `FAIL_RELEASE_GATE`: an explicit release condition failed and the failure is preserved.
- `FORMAL_APPROVED`: explicit formal authority exists. No new source, universe, network, PIT, scheduler, or recovery authority is approved here.

## Current workstream snapshot

| Lane | Current task | Status | Evidence level | Formal authority |
|---|---|---|---|---|
| A | `A201` | 21 companies, 12 industry segments, and 54 hypotheses; `SHADOW_RESEARCH_ONLY`; A211 `ASSIGNED_AWAITING_EVIDENCE` | `CODEX_REPORTED`; A194 remains `GPT_VERIFIED` synthetic parser evidence | formal universe/PIT `false` |
| B | `B200` | `FAIL_RELEASE_GATE`; full regression `227/228`; ten-sequence gate `NOT_RUN`; B202 `WAITING_FOR_EVIDENCE` | `FAIL_RELEASE_GATE`; `ARCHIVED_WINDOWS_EVIDENCE` | FRED network authority `false` |
| C | `C203` | archived tests `68/68 PASS`; `GUI_BROWSER_QA_NOT_VERIFIED`; C212 `ASSIGNED_AWAITING_EVIDENCE` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `false` |
| D | `D204` | offline contract tests `86/86 PASS`; `REAL_PROVIDER_BYTES=0`; D213 `ASSIGNED_AWAITING_EVIDENCE` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `FORMAL_APPROVAL=false` |
| E | `E199` / `E208` | E199 development self-test `228/228`; B200 failure retained. E197 offline alias hardening accepted but production trust root absent. E208 synthetic staging `31/31`; OS outbound isolation not verified. E209/E214 awaiting evidence | E197 `GPT_VERIFIED`; E199/E208 `CODEX_REPORTED` | launch/network/formal trust `false` |
| F | `F233` | Live readback passed; F215 publication preserved; material docs-only patch ready but not published | F215 receipt `GPT_VERIFIED`; F233 local validation only | Git/Ready/merge/main authority `false` |
| G | `G206` | offline evaluation tests `45/45 PASS`; `MATURE_REAL_OUTCOME_COUNT=0`; predictive value `NOT_ESTABLISHED`; G216 awaiting evidence | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | formal evaluation `false` |
| H | `H207` | `T46_RECOVERY_BLOCKED`; historical 708-row ledger; scheduler enabled as last verified; H217 awaiting evidence | `CODEX_REPORTED`; preserved read-only observation | recovery/scheduler mutation `false` |

## Preserved governance boundaries

- `PHASED_10_TO_22` was approved on `2026-10-25` as a research strategy only; it is not a formal-universe change.
- `9069` remains `REFERENCE_ONLY`.
- D172 remains a 16-source inventory with 0 `FORMAL_APPROVED` sources; its canonical artifact is `MARKET_SOURCE_COVERAGE_172.zip`.
- E169 remains `FRED_OFFLINE_CANDIDATE` and `NETWORK_AUTHORITY_FALSE`. B176 and B177 historical failures remain preserved; B198 is the newer independent release-gate failure and does not erase them.
- B198 failed because malformed marker `pid="not-an-integer"` caused an unhandled Int32 conversion, bypassed the common failure dispatcher and automatic cleanup, and left the owned child alive until audit cleanup. No full 195 regression or ten-sequence release run followed.
- A201 expands the shadow research map to 21 companies, 12 industry segments, and 54 hypotheses. It remains research-only and does not change the formal universe. A194's Python 3.12, official-provider, historical-PIT, and production-PIT gaps remain open.
- E197 is accepted only as verify-only offline alias hardening: 104/104 Windows checks passed, while production trust remains absent. E199's development self-test is 228/228, but the independent B200 regression is 227/228 and the ten-sequence gate was not run. E208 adds 31/31 synthetic staging evidence without proving OS outbound isolation.
- C203's 68/68 and D204's 86/86 are archived/offline results. C203 has no browser GUI QA; D204 used zero real provider bytes and has no formal approval.
- G206's 45/45 offline checks establish contract behavior only. Mature real outcomes remain zero, so predictive value is not established.
- T46 remains `RECOVERY_BLOCKED`; H207 preserves the historical 708-row ledger and last-verified enabled scheduler without recovery or scheduler-mutation authority.
- Real Taiwan monthly-revenue predictive performance, formal PIT, production trust roots, and formal current-period market lights remain unproven.
- No formal forward-evaluation protocol is approved.

## PR #9 live identity before F215 publication

F215 independently re-confirmed PR #9 as `OPEN`, `draft=true`, unmerged, base `main` at `6e2740d9877364619ca046cb54829350fd0db5ef`, head `56a18e260b5b8a20ce482f30d48ccdc8172d7cc7`, one commit, and exactly six changed documentation files. The remote PR branch and local clean branch matched that head. Remote `main` matched the base. No drift was detected.

The six live Git blob SHA-1 values are recorded in `EVIDENCE_INDEX.md`. They are not interchangeable with ZIP document SHA-256 values. For F180, raw bytes differ by one ZIP-only trailing LF per document; normalized equality after removing that one LF passed.

## Publication boundary

F215's one-time commit/push authority has been consumed. F233 re-read PR #9 as `OPEN`, `draft=true`, unmerged, head `c68f24acb6d3a1b8ed8f2aa6b2665a9fe8fe0f2a`, and base/main `6e2740d9877364619ca046cb54829350fd0db5ef`. The six live Git blob SHA-1 values match the F215 post-publication receipt exactly. No checks are reported; traditional `main` branch protection is absent and repository rulesets are empty.

## Material currentness after F215

- `A221 Stage 2` is a `CODEX_REPORTED` synthetic candidate: exact archive integrity passed and fresh archived results report Stage 1 17/17 plus V3 27/27, combined 44/44. Production trust roots, real observations, formal PIT, and formal signal authority remain absent.
- `B223` remains `BLOCKED_INDEPENDENT_PREFLIGHT_NOT_VERIFIED`; 252 assertions were reported fresh, but independent OS-I/O provenance, persisted process identity/exit evidence, and the ten-sequence gate were not established. The canonical B223 outer handoff ZIP was not present for F233, so this remains bounded extracted evidence rather than a newly verified archive.
- `E225` is an offline policy-gate candidate with 70/70 archived tests. Its erratum corrects the protected-entry SHA-256 to `01462ebbbda2c9b593440a6fd42e6547081e9314ff0cee47355d25ad91c00f9f`; production trust, durable replay protection, real provider compatibility, PIT, and formal source policy remain absent.
- `E227` is preserved as a `TRUSTED_TEST_HARNESS_ONLY` candidate: exact archive integrity passed; archived clean-extract evidence reports 271/271 and observed native Windows code 32 in the controlled harness. Arbitrary equal-privilege in-process authenticity is not covered and no production trust root exists. B228's later bounded status is recorded below.
- `H218` is the last hash-bound scheduler state available to F233: the exact weekly scheduler was disabled once and read back as `State=Disabled`, `Enabled=false`. `H226` has no canonical handoff ZIP/manifest in the available workspace and remains `BLOCKED_INPUT`; T46 remains `RECOVERY_BLOCKED`.
- No new item above is promoted to `GPT_VERIFIED` or `FORMAL_APPROVED` by F233.

## F233 publication boundary

F233 had read-only GitHub authority and isolated status-document candidate authority only. Its candidate remained unpublished. F241 supplies the later exact single-push authorization described below.

## F241 latest bounded status

- `B228` is recorded from the user-authorized F241 status packet as `PASS_OFFLINE_TRUSTED_HARNESS_RELEASE`: 10/10 Windows sequences and 2710/2710 sequence assertions. No canonical B228 handoff archive was present for F241 integrity verification, so this is `USER_SUPPLIED_STATUS`, not `GPT_ACCEPTED` or `FORMAL_APPROVED`. Production FRED authority remains false.
- `D236` exact archive integrity passed. It corrects Pilot10/Phase2 membership in research metadata only; the formal issuer registry, formal universe, source policy, and PIT remain unchanged.
- `A237` exact archive integrity passed. Three preregistered monthly-revenue hypotheses (`MRH01`, `MRH02`, `MRH03`) are affected by earlier cohort errors; no real observation, mature outcome, evaluator run, or realized performance effect is established.
- `E232 Resumed` exact archive integrity passed. Its archived run reports 18/18 synthetic integration tests, two synthetic observations, zero accepted numeric features, final protected consumer `DESCRIPTION_ONLY`, and six `DATA_INSUFFICIENT` diagnostics. No formal feature or PIT was issued.
- `B238` is `STAGE1_COMPLETED_STAGE2_AWAITING_EVIDENCE` per the user-supplied F241 status. No canonical B238 handoff archive was present; no Stage 2 result is accepted.
- `E239` arrived after the F241 packet's assigned state. Its exact archive integrity passed; the Codex report records fresh E232 replay 18/18 and new status/cohort negatives 13/13, with zero qualified numeric features. It remains `CODEX_REPORTED_NOT_GPT_ACCEPTED` and grants no formal authority.
- `G234` exact archive integrity passed. All 22 decisions remain `PROPOSED_NOT_APPROVED`, formal activation is disabled, real observations and mature outcomes are zero, and predictive value is not established.
- `G240` also arrived after the packet's assigned state. Its exact archive integrity passed and it remains a separate `PROPOSED_NOT_APPROVED` cohort/denominator amendment candidate; it does not activate G234 or establish predictive value.
- `H218` remains the last hash-bound scheduler state: exact weekly scheduler `Disabled`, `Enabled=false`. `H226` and `H235` remain blocked or incomplete; T46 remains `RECOVERY_BLOCKED`.
- `F233` is preserved as `VERIFIED_DOCS_ONLY_PATCH_CANDIDATE_NOT_PUBLISHED`. F241 is authorized for one six-document commit and one push only; this authority does not include Ready, merge, main, CI/security, code, or formal-data actions.

Across all current workstreams, real approved market observation count is 0, formal PIT is not issued, formal source policy is not approved, and no formal investment signal is available.

## F286 currentness batch

F286 re-read GitHub before publication. Draft PR #9 was `OPEN`, `draft=true`, unmerged, based on `main` at `6e2740d9877364619ca046cb54829350fd0db5ef`, and headed by `79e5eae814eab37138eefe0063c7629625cbaacf`. The PR still changed exactly the six historical status documents, reported no CI checks, and `main` traditional branch protection returned 404. These governance settings were not changed.

The user granted standing authority for material documentation-only updates under `docs/project-status/*.md` and `docs/handoffs/*.md`. It does not authorize main, merge, Ready, force push, code, rules, CI/security, source policy, PIT, formal data, or T46 mutations. Each batch still requires fresh remote identity, evidence classification, exact-path, consistency, secret, and private-path gates.

| Lane | Latest bounded status | Evidence class | Explicit boundary |
|---|---|---|---|
| A | `A280`: 40/40 GPT-independent offline validation passed; fiscal-period identity remains research evidence | `GPT_ACCEPTED_OFFLINE` | No official financial certification, formal PIT, or universe change |
| B | `B279`: `FAIL_OFFLINE_LOCAL_TOOL_QA`; 24/27 fresh passed, 3 failed; corrupted ZIP CRC metadata was accepted | `INDEPENDENT_FAIL` | F276 release gate is blocked; package integrity success elsewhere does not erase the failure |
| C | `C281`: original runtime `TypeError` reproduced; fixed Node runtime candidate verified | `GPT_ACCEPTED_OFFLINE` within tested Node scope | Windows browser GUI/pixel/accessibility QA remains `NOT_VERIFIED` |
| D | `D282`: official dataset documents reviewed; API-resource byte identity remains unverified | `GPT_ACCEPTED_OFFLINE` for document review only | Real provider bytes `0`; no source admission or production authority |
| E | `E278`: 18/18 GPT-independent synthetic replay passed | `GPT_ACCEPTED_OFFLINE` synthetic scope | Completeness only; no parser, source, PIT, or production-ledger approval |
| F | `F283`: A-H ZIP-only templates documented; self-tests passed | `CODEX_REPORTED` | B279 independently blocks treating F276 collector as released or CRC-validating |
| G | `G284`: eight study contracts preserved; 22 decisions pending | `CODEX_REPORTED` | Mature real outcomes `0`; predictive value not established; formal evaluation disabled |
| H | `H285`: R07 historical Git lineages documented; R10 remains historical snapshots only | `CODEX_REPORTED`; formal identity `NOT_VERIFIED` | Formal Reader migration `0/12`; T46 remains `RECOVERY_BLOCKED` |

Archive integrity passed for all eight handoffs, but this establishes only byte identity. It does not convert `CODEX_REPORTED` into `GPT_ACCEPTED_OFFLINE`, does not override `INDEPENDENT_FAIL`, and does not grant `FORMAL_APPROVED` authority.

## F295 regression-blocker triage

F295 reproduced both F286 blockers from the clean PR #9 head. Without the Git-excluded MOPS raw-document prerequisite, the saved-fixture test failed while resolving `2024Q1_C_*.xhtml`, and Patch183 recomputation found 0 filing inputs, 2,722 active row IDs, 0 recomputed IDs, and therefore 2,722 missing active IDs.

Repository policy intentionally excludes `data_spine/raw`, `*.html`, and `*.xhtml`; the repository guide requires raw source documents to be reconstructed outside Git. The required official 2330 2024 Q1 consolidated filing was found in the trusted local raw store with SHA-256 `68117c2ed0caa105921d9cfe27afe9ed02472dcc33066ead9b31e1603a77357c`. No fixture bytes were fabricated, copied into Git, or changed.

After mounting the existing trusted raw store read-only in the isolated worktree, both targeted tests passed. Patch183 then observed 142 filings, 2,722 active IDs, 2,840 recomputed IDs, and 0 missing active IDs. With an ASCII-only temporary directory, the complete repository regression suite passed 669/669. This is a prerequisite-restoration result, not formal data, source-policy, or production authority.

The B279 independent collector failure remains historical and controlling for the F276 release gate. F287 remains a patched candidate awaiting B291 independent acceptance.

## F312 evidence currentness

F312 re-read Draft PR #9 as `OPEN`, `draft=true`, unmerged, with head `fe06d37fd398eec1ba60c2377e7617f2faed8ed5` and base/main `6e2740d9877364619ca046cb54829350fd0db5ef`. The PR changes seven files under `docs/project-status/` and reports no CI checks. F312 updates only the six established status documents; `PUBLISHING_POLICY.md` is unchanged.

F306 independently confirmed the exact A298-H305 ZIPs at the single authorized central READY location: 8/8 existed, 8/8 pinned SHA-256 identities matched, all CRC and declared payload checks passed, and the central index reported no issue. This is a packaging/location result only; it does not convert candidate evidence into a release or formal approval.

| Lane | Latest bounded result | Evidence class | Preserved boundary |
|---|---|---|---|
| A | `A298`: 21-company research ontology, 11 metric concepts, 30 typed Pilot10 metrics, Phase2 typed metrics `0` | `CODEX_REPORTED` | No formal-universe, PIT, certification, or signal authority |
| B | `B299`: original 38/38 passed; independent 39/43 passed and four failed | `INDEPENDENT_FAIL` | Two uncontrolled provider-type exceptions, output-parent symlink acceptance, and incomplete cleanup remain release blockers |
| C | `C300`: 89/89 fresh offline preview checks passed | `CODEX_REPORTED` | Browser/file-picker/pixel QA remains `NOT_VERIFIED`; no shared or formal data write |
| D | `D301`: conservative official-CSV raw contract; E294 regression 38/38, CSV import rejected before output | `CODEX_REPORTED` | Contract-ready but implementation-blocked; no provider bytes, parser admission, PIT, or source approval |
| E | `E302`: 38/38 E294 regression plus 32/32 new checks; both B299 provider-type `TypeError` cases repaired in the isolated candidate | `CODEX_REPORTED` | Output-parent reparse handling and receipt-failure cleanup remain unverified gaps; max state is quarantine pending review |
| F | `F303`: B291 direct 19/19 preserved; packaged runners 9/9 and 14/14 passed with CRC/concurrency/index gates | `CODEX_REPORTED` | Candidate tool `f4ad401059e740d8d293730ed53b0de9262b1d5b09ab7912585c06cbffd86ba4` is not deployed; replacement authority absent |
| G | `G304`: 28/28 synthetic sample-completeness checks passed | `CODEX_REPORTED` | Real observations `0`; formal activation false; predictive value not established |
| H | `H305`: manual official-data operations for two documented dataset routes | `CODEX_REPORTED` | No provider GET or real bytes; Provider Admission/PIT/Reader/recovery absent; T46 remains `RECOVERY_BLOCKED` |

No A298-H305 item is `FORMAL_APPROVED`. B299's four independent failures remain explicit and are not overwritten by E302's local repair or F303's runner results.

## F320 Round 36 evidence currentness

F320 independently re-read Draft PR #9 before this batch as `OPEN`, `draft=true`, unmerged, with head `2dafa6c5d5abe5ba95d0632a92e89ce784893590` and base/main `6e2740d9877364619ca046cb54829350fd0db5ef`. The historical sealed head therefore matched the live head, no prior F320 commit was present, and no unexplained branch drift was observed. The PR cumulatively changes seven files under `docs/project-status/`; this batch is limited to the six existing status documents and leaves `PUBLISHING_POLICY.md` unchanged.

All eight pinned F312/A307-H314 archives passed outer ZIP SHA-256, pinned manifest SHA-256, declared payload hash and size, CRC, duplicate/casefold, unsafe-path, undeclared-file, and manifest task-ID checks. Archive integrity establishes identity only; acceptance classifications remain separate.

| Lane | Latest bounded result | Evidence class | Preserved boundary |
|---|---|---|---|
| A | `A307`: `PASS_OFFLINE`; four acquisition priorities, three Taiwan priorities, and eight studies; real observations `0` | `CODEX_REPORTED` | No live fetch, official financial certification, formal PIT, universe, or signal authority |
| B | `B308`: `FAIL_INDEPENDENT_QA`; independent checks `15 PASS / 4 FAIL`; package self-containment failed | `INDEPENDENT_FAIL` | B299's historical four failures remain preserved; no importer release or formal data authority |
| C | `C309`: `PASS_OFFLINE_GUI_NOT_VERIFIED`; fresh offline tests `110/110` | `CODEX_REPORTED`; GUI `NOT_VERIFIED` | No browser/file-picker/pixel QA, importer execution, formal data write, or market signal |
| D | `D310`: `PASS_SYNTHETIC_CONTRACT`; fresh tests `25/25`; explicit adapter required rather than direct schema identity | `CODEX_REPORTED` | Real provider bytes `0`; parser admission, historical PIT, and formal source authority absent |
| E | `E311`: `59 PASS / 1 NOT_VERIFIED` across its recorded JSON regression and E311 checks; `RELEASE_BLOCKED` | `CODEX_REPORTED`; Windows junction `ACCESS_DENIED_NOT_VERIFIED` | The missing E302 baseline is a separate `STATIC_PACKAGING_DEPENDENCY_GAP`; the junction was not executed successfully, so no junction runtime PASS or FAIL is claimed; production release, network, source, PIT, and formal writes remain false |
| F | `F312`: docs-only Draft PR publication succeeded at `2dafa6c5d5abe5ba95d0632a92e89ce784893590` | `CODEX_REPORTED` publication receipt | Status publication grants no Ready, merge, main, code, data, or shared-tool deployment authority |
| G | `G313`: `PASS_SYNTHETIC_INDEPENDENT_QA`; fresh tests `12/12`; archived G296 `18/18` and G304 `28/28` preserved | `CODEX_REPORTED` | Real observations `0`; formal activation false; predictive value `NOT_ESTABLISHED` |
| H | `H314`: `PASS_SYNTHETIC_TOOL`; local CSV receipt helper records identity without parsing or copying source bytes | `CODEX_REPORTED` | Source admission and historical PIT remain false; T46 stays `RECOVERY_BLOCKED` with Reader Ready/Migrated `0/12` |

Across Round 36, approved real market observations remain `0`, formally admitted provider bytes remain `0`, mature real outcomes remain `0`, predictive value is `NOT_ESTABLISHED`, and no Round 36 item is `FORMAL_APPROVED`. F303's shared collector remains a non-deployed candidate. B299 and B308 failures, E311's junction `NOT_VERIFIED`, and all earlier FAIL/BLOCKED records remain visible and controlling within their scopes.

## F328 Round 37 status correction

F328 initially re-read Draft PR #9 as `OPEN`, `draft=true`, unmerged, at head `9a857bf5d4bfdcbbc5cb633b91c88dd0d0c6b1db` and base/main `6e2740d9877364619ca046cb54829350fd0db5ef`; the historical F320 head matched and no unexplained drift existed. F328 also validated all eight pinned F320/A315-H322 archives byte-for-byte: outer ZIP SHA-256, pinned root-manifest SHA-256, 312 declared payload hashes and sizes, CRC, path safety, duplicate/casefold aliases, undeclared files, and manifest task identity all passed. Package integrity establishes identity only and does not promote a reported candidate to release or formal approval.

| Lane | Latest bounded result | Evidence class | Preserved boundary |
|---|---|---|---|
| A | `A315`: `39/39 PASS_OFFLINE` for the bounded manual-CSV research-admission candidate | `CODEX_REPORTED` | Formal admission `0`; real approved observations `0`; no official financial certification, PIT, universe, or signal authority |
| B | `B316`: `FAIL_INDEPENDENT_QA`; `24 PASS / 2 FAIL / 2 NOT_VERIFIED`; original E311 clean-extract runner failed because the E302 baseline source was not packaged | `INDEPENDENT_FAIL` | The clean-extract failure is a static packaging/dependency failure. Separately, two parent rename/swap cases produced real raw-only residue at runtime. The junction case is `ACCESS_DENIED_NOT_VERIFIED`. B299 and B308 failures remain preserved |
| C | `C317`: task archive reports `74/74`; GPT-independent JavaScript retest `43/43`; Python initial `28/31` with three missing inputs, then supplemented `31/31` | `CODEX_REPORTED` plus `GPT_ACCEPTED_OFFLINE` only for the stated independent retest scope | Windows GUI/browser/file-picker/pixel/accessibility QA remains `NOT_VERIFIED`; no formal data write |
| D | `D318`: `19/19 PASS_SYNTHETIC_CONTRACT` candidate | `CODEX_REPORTED` | No real monthly-revenue bytes, parser/source admission, formal PIT, or production authority |
| E | `E319`: clean-extract integration `111 PASS / 0 FAIL / 1 NOT_VERIFIED`; Stage 1 candidate complete | `CODEX_REPORTED`; root manifest declares no formal authority class | `RELEASE_BLOCKED` by B316; packaged dependencies do not cure the parent-swap runtime failures or grant source/network/formal authority |
| F | `F320`: one normal docs-only commit and one normal push reported for Round 36 | `CODEX_REPORTED` publication receipt | Historical result only; it does not prove F328 publication or grant main/Ready/merge authority |
| G | `G321`: `21/21 PASS_SYNTHETIC_INDEPENDENT_QA` | `GPT_ACCEPTED_OFFLINE` for the bounded synthetic retest; package itself is `CODEX_REPORTED` | Real approved observations `0`, mature real outcomes `0`, predictive value `NOT_ESTABLISHED`, formal activation false |
| H | `H322`: Windows QA `15 PASS / 1 FAIL / 3 NOT_VERIFIED`; the large aggregate runner `ConvertTo-Json` did not complete and the canonical table was reconstructed offline from the eighth run's per-case raw logs | `CODEX_REPORTED`; one observed semantics FAIL | Exact `LOCAL_RECEIPT_OBSERVATION_ONLY` semantics are missing/mismatched; symlink, hardlink, and hash-race cases remain `NOT_VERIFIED`; Reader Ready/Migrated `0/12`; T46 `RECOVERY_BLOCKED` |

Round 37 preserves approved real market observations at `0`, formally admitted provider bytes at `0`, and mature real outcomes at `0`. Predictive value remains `NOT_ESTABLISHED`; T46 remains `RECOVERY_BLOCKED`; `9069` remains `REFERENCE_ONLY`; no Round 37 item is `FORMAL_APPROVED`.
