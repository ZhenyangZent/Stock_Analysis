# Evidence index

DATA_AS_OF: `2026-10-30`
LAST_VERIFIED_AT: `2026-10-30T00:57:31+08:00`
STATUS_TASK_ID: `STOCK-PR9-ROUND36-CURRENTNESS-320`
STATUS_STAGE: `A307_H314_ROUND36_EVIDENCE_CURRENTNESS`

## Accepted and status-bearing evidence

| TASK_ID | ARTIFACT_FILENAME | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | SOURCE_LEVEL | GPT_ACCEPTANCE_SCOPE | STATUS_AS_OF | FORMAL_AUTHORITY | NEXT_DEPENDENCY |
|---|---|---|---|---|---|---|---|---|
| `STOCK-GITHUB-STATUS-GOVERNANCE-170` | `STOCK-GITHUB-STATUS-GOVERNANCE-170.zip` | `c0a0af692e401c4cd1971f2c28d11cff60676f2dd61ff1a9f487a0301f138f62` | `daac394c0aeff4687b86213d597fb743f7d39a367c4fd1b1695d9837e0f7ccb6` | `CODEX_REPORTED` | `NOT_PROVIDED` | `2026-10-26` | `false` | Historical only; remain `NOT_PUBLISHED` |
| `STOCK-GITHUB-STATUS-CURRENTNESS-180` | `STOCK-GITHUB-STATUS-CURRENTNESS-180.zip` | `02f4f4704d5eca1b1f464eb518010c65783346b63ffd53184c035e98e1fc9d33` | `96f5b116622cb88ddef79957b79bc2c0c992096243a53b4821c89c43ed369ad3` | `GPT_VERIFIED` | Exact ZIP, 11 payloads, eight upstream packages/4,088 payloads, and six-file patch | `2026-10-26` | Merge authority `false` | New exact PR-update authority |
| `STOCK-GITHUB-STATUS-CURRENTNESS-187` | `STOCK_GITHUB_STATUS_CURRENTNESS_187.zip` | `81c964463430638b299485cfe2c152178cde7ea76fb37b14ef64ae37000496d6` | `ce40bc044037ad1c6636abe3d4118fa749bbdc2c2b9482e18da503ea0beab279` | `GPT_VERIFIED` | Exact ZIP/manifest/13 payloads, patch application, and 6/6 generated docs | `2026-10-27` | New Git write authority `false` | F191 acceptance and new exact authority |
| `STOCK-GITHUB-LATEST-CURRENTNESS-191` | `STOCK_GITHUB_CURRENTNESS_191.zip` | `525292fcc5a5525c096e12a9318d80c066b85ef04f58baed8b1c97902fda5b48` | `eac470c4f94b2bd8ab5f8171ae3052c9cb0e5c94729184dcb43294f17cc75b4c` | `GPT_VERIFIED` | Exact ZIP/manifest/13 payloads and 6/6 Git patch reconstruction | `2026-10-27` | New Git write authority `false` | Stage 2 acceptance and new exact authority |
| `STOCK-GITHUB-LATEST-CURRENTNESS-191 Stage 2` | `STOCK_GITHUB_CURRENTNESS_191_STAGE2.zip` | `04addfcfffafe849f71d00935241b7961ce8c07db54060645dd9d7aa5356a064` | `fc51864145480466a41f261c57ea925bd582e30c8f639f8c7913a96a4816bc16` | `GPT_VERIFIED` | 13/13 manifest payloads, 6/6 Git blob baseline, 6/6 reconstruction, and docs-only scope | `2026-10-27` | New Git write authority `false` | Stage 3 acceptance and new exact authority |
| `STOCK-GITHUB-LATEST-CURRENTNESS-191 Stage 3` | `STOCK_GITHUB_CURRENTNESS_191_STAGE3.zip` | `b08025a1ce4cfd460aa64dcdd02106502356fbe8fc81a52c8e08b63cbf229675` | `0f3542964c4c716d8bd8240cfa0a1b5f6f0976e999d0b6f8462d5084c00af319` | `GPT_VERIFIED` | Exact ZIP, 12 payloads, six Git blobs, and patch reconstruction | `2026-10-27` | New Git write authority `false` | Stage 4 acceptance and new exact authority |
| `STOCK-GITHUB-LATEST-CURRENTNESS-191 Stage 4` | `STOCK_GITHUB_CURRENTNESS_191_STAGE4.zip` | `43e407c473f7cd2ad1604ccef5562279f661fb3af9ab2929b8a25db1498b599d` | `7664c2b96995d525d35f794707dd4b491c006a163343b03320407489224f49fe` | `GPT_VERIFIED` | Exact ZIP and 12 manifest payloads accepted as F205 baseline; publication remained blocked | `2026-10-27` | New Git write authority `false` | F205 review, auth recovery, fresh remote readback |
| `STOCK-GITHUB-TRUTHFUL-CURRENTNESS-205` | `STOCK_GITHUB_STATUS_AND_CI_205.zip` | `ce064cccadb070bd513d4ae67f69e107cff5301661c750ca79ccc464205e821a` | `6f86004e14a955bb654a30fc16e0d10f081e13b8e51a5b5f9c13bbff2f695159` | `GPT_VERIFIED` | Exact F205 status-center candidate accepted; 14 payloads and six-document patch; remained not published | `2026-10-28` | F215 one-time six-file publication only | F215 remote publication receipt |
| `TW-REVENUE-PROTECTED-RECEIPT-INTEGRATION-184 Stage 2` | `TW_REVENUE_PROTECTED_BRIDGE_184_STAGE2.zip` | `f503b9161c1ca2a19d3180f09afb91cfdc3e651c948cd7ad13a072bd3966a71e` | `86e5d3adbdc16b988075ce86b2749eb94cbdd27c87611f744dad34dc31869f0f` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `NOT_PROVIDED` | `2026-10-27` | `false` | A189 accepted evidence |
| `A189` | `TW_REVENUE_SOURCE_VALUE_BINDING_189.zip` | `c10718f19449c21f2a089227394c5ddf9f8086978a7538ca1e730bc50bd0a808` | `d5a1bfa5bdfd2bac559003020654172d3f50374f91df8c1420f49376c56a99ed` | `GPT_VERIFIED` | `ACCEPTED_CORE_VALUE_BINDING`; parser hardening remains required | `2026-10-27` | `false` | A192 exact handoff and acceptance |
| `A192` | `TW_REVENUE_TYPED_SOURCE_PARSER_192.zip` | `5d614d116a45cee44ea1a0a47a5f44957399d2009f3756fbc1b10203ad6ef740` | `2fa60e376b62a6b8b80ef6cc410f6d4eb1454911719e22c0187fc8e39a1ea101` | `GPT_VERIFIED` | Function accepted within tested scope; 35/35 synthetic parser negative tests pass; omitted fixture and clean-extract replay gap preserved | `2026-10-27` | Historical/production PIT `false` | A194 exact handoff and acceptance |
| `A194` | `TW_REVENUE_TYPED_PARSER_HERMETIC_194.zip` | `a08c924940617146a1330736908efacaab8a1080ed4f51d94dfcb563411284b1` | `77d8d41b0bd8a0f991ea7d65a468247c4f18b6cb488a84e0918d1f33c49a31b7` | `GPT_VERIFIED`; `ARCHIVED_WINDOWS_EVIDENCE` | Synthetic parser accepted; Python 3.13 clean-extract 57/57; Python 3.12 runtime unavailable; upstream 78 archived, not rerun | `2026-10-27` | formal/production PIT `false` | A201 exact evidence and independent acceptance |
| `B176` | `TW_REVENUE_INDEPENDENT_AUDIT_176.zip` | `539eaf809a137de808c8ec6e51091eb5da22568714f0b92de84e14e74754119c` | `dbbbf9da7a88c5cdec104b567c30fefb93a7855f8996918598969feb054ebad9` | `ARCHIVED_WINDOWS_EVIDENCE` | Historical independent integration evidence | `2026-10-27` | `false` | Preserve independently from B177 |
| `B177` | `MARKET_FRED_WORKER_RELEASE_177.zip` | `44b15ad2efb44416034c24ad9f8bd9c86c14b1325d68d88127e497578a3b9ad0` | `9da4cea9e2b9a14163d1c712b67e02bd268276de65a5c19d45aa969e836fb32a` | `GPT_VERIFIED`; `FAIL_RELEASE_GATE` | `FAIL_RELEASE_GATE_MARKER_MISMATCH_ORPHAN`; regression 179/179 pass in a single run; negative preflight 6/12 pass and 6/12 fail; ten-sequence gate not run due to preflight failure | `2026-10-27` | FRED network authority `false` | Preserved; B198 is a distinct later gate |
| `B198` | `MARKET_FRED_WORKER_RELEASE_198.zip` | `1915f264f8ae942e69fb497c57d7dc42e37a10a7da926009fdc8e85be89aba35` | `b9e02f7167291b76f71ee2b8062391358dbb107f89d55c0683419cc31be75599` | `GPT_VERIFIED`; `FAIL_RELEASE_GATE`; `ARCHIVED_WINDOWS_EVIDENCE` | Malformed non-integer PID bypassed common failure cleanup and left owned worker alive until audit cleanup; full 195 regression and ten sequences not run | `2026-10-27` | FRED network authority `false` | B202 exact evidence and independent acceptance |
| `C173` | `C173` | `835621170ad14d61db52fae7d7de693e52aed5863f937b1d05ded665e9179a16` | `c4e1ac168b365e48538a2f13f7d2015ec44d96dfec454df3eb32eb0acee777b6` | `CODEX_REPORTED` | `NOT_PROVIDED` | `2026-10-27` | `false` | GUI verification |
| `MARKET-DATA-SOURCE-COVERAGE-172` | `MARKET_SOURCE_COVERAGE_172.zip` | `64728e67c8a63708fc37c9486d80baa6267cde69dc444e002f4d0b89ac65022e` | `7ac7d7d43ccee26be94b5f46674916d3adcbb5f33d64b79d29136d0a02ad1a38` | `CODEX_REPORTED` | 16 source categories; 0 formally approved | `2026-10-27` | `false` | Formal source decisions |
| `MARKET-SOURCE-POLICY-PACKAGE-HARDEN-182` | `MARKET_SOURCE_POLICY_PACKAGE_182.zip` | `e8f331ae665f0207061dced4f0249cb2b610ba5bf27870f5e86080a1b0ddcb0d` | `81b7eb75bd6611f5cc38b79cf6f4b95d9c05aa0d7c9afe13c32851b561391a14` | `CODEX_REPORTED` | `NOT_PROVIDED` | `2026-10-27` | `REAL_NETWORK_AUTHORITY_FALSE` | Separate bounded network authority |
| `TW-REVENUE-TRANSPORT-TRUST-BOUNDARY-188` | `TW_REVENUE_TRANSPORT_BOUNDARY_188.zip` | `ee611552ab0b598fd5939d8116fc51c335f980cd01b79f732ee0b0023b8ed150` | `973f112409a89120c08a1612976610876fb8c99b7019c533bccf14804785ac9e` | `CODEX_REPORTED`; offline candidate | `NOT_PROVIDED` | `2026-10-27` | Real network authority `false` | E190 accepted evidence |
| `E190` | `TW_REVENUE_RUNTIME_IDENTITY_190.zip` | `deb2edb975c8b920edc0e94461eaedecdc2d66ff534e76d304604c5a6cf619ab` | `11975763160a833e49930e1907a8164922a4d6290cbf1cfc03a36a99b4be591a` | `GPT_VERIFIED` | `OFFLINE_CANDIDATE_ACCEPTED`; external trust-root gap remains | `2026-10-27` | Real network authority `false` | E193 exact handoff and acceptance |
| `E193` | `TW_REVENUE_EXTERNAL_SOURCE_IDENTITY_193.zip` | `0ef6d873edea06faae6a20989fb5b8ab021a716449c1131ea328485300d2fc1a` | `750f58ad373ca59b47d01f28d260d88b6902bb62f069751e71fddc63b2c3433a` | `GPT_VERIFIED` | Limited offline prototype accepted; 15 new tests pass; real symlink path aliases remain unblocked | `2026-10-27` | Launch/operational authority `false` | E195 exact handoff and acceptance |
| `E197` | `TW_REVENUE_RAW_LEXICAL_ALIAS_197.zip` | `ef436f02548e10172139fb5a6f76c41fb4ac66b3b9e583713f17daddc143fc81` | `727fd35d8d576b333812590254cec5e27f465a7cd133703f39859d99e0bab5ff` | `GPT_VERIFIED`; `ARCHIVED_WINDOWS_EVIDENCE` | Verify-only offline acceptance; 12/12 new negatives plus 92/92 regression; operational trust excluded | `2026-10-27` | launch/network/formal trust `false` | E199/B200 bounded follow-up |
| `E199` | `MARKET_FRED_MALFORMED_MARKER_199.zip` | `a0c23147658c5c603190fd2be41212b72dd475aa379b0145c4994bd268e36ab7` | `7f5251ed106368e7b0ab83efa948ea8d70ab77bd5bd33eb0557b8d1b547412c9` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | Offline candidate reports 228/228 clean-extract pass while preserving one 227/228 contention failure; no GPT acceptance supplied | `2026-10-27` | FRED network authority `false` | B200 exact evidence and independent acceptance |
| `A201` | `AI_CAUSAL_SIGNAL_ATLAS_201.zip` | `9dabc593b2d0ff2f58e28a4de2281a17fac48732658bc0e4484c857c76e960f4` | `be5eb5a3c866a5e59b8fe12821efa46ff6b7bfaab221e162fabd99a2472b4c5e` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | 33/33 manifest payloads verified; 21 companies, 12 industry segments, 54 hypotheses; shadow research only | `2026-10-28` | formal universe/PIT `false` | A211 exact evidence and independent acceptance |
| `B200` | `MARKET_FRED_WORKER_RELEASE_200.zip` | `f77b3dc7956f7a36aa006ec1575e36bfae522c6bbba656ba53a30548550d6469` | `7c49f94eb5f144f35962227befb90a6381f6a9d79627da38b071619f4f9e1346` | `CODEX_REPORTED`; `FAIL_RELEASE_GATE`; `ARCHIVED_WINDOWS_EVIDENCE` | 1,976/1,976 manifest payloads verified; full regression 227/228; ten-sequence gate not run | `2026-10-28` | FRED network authority `false` | B202 exact evidence and independent acceptance |
| `C203` | `STOCK_RESEARCH_COCKPIT_GUI_203.zip` | `377cee05ac8c11f6d110295644ee948294afb7f664cce82626d52d90484630ab` | `66cb15efec782626c978f56632c63c58b4479f452790b315d954d70208c89435` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | 42/42 manifest payloads verified; archived tests 68/68; browser GUI QA not verified | `2026-10-28` | `false` | C212 evidence and real-browser QA |
| `D204` | `TW_OFFICIAL_PARSER_ADMISSION_204.zip` | `f64ae2f609d9ce36beee10e4d5e3c72f1818ee5767ae8f8a7dce101c256f013e` | `3042f356068372e885fceebc0d1bc887b6dbd326bc0b0513433991ddc801031a` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | 17/17 manifest payloads verified; 86/86 offline contract tests; real provider bytes 0 | `2026-10-28` | formal approval/network authority `false` | D213 evidence and real-provider review |
| `E208` | `TW_REVENUE_SEALED_STAGING_208.zip` | `e0fbe8260dbba2782629dc2df2bb3f56e5f28e4ef5e48c39afafbcbeaad3153a` | `61714d4d8a00064d55e280ec913e4f4716c252f82a173413d3a8e8bf1c9b70d0` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | 28/28 manifest payloads verified; 31/31 synthetic staging; OS outbound isolation not verified | `2026-10-28` | launch/network/formal trust `false` | E209/E214 evidence and OS isolation proof |
| `G206` | `STOCK_PROSPECTIVE_SCIENCE_206.zip` | `4e8889f89f929d5ab51cc8428d004a9602fb9e189b0452ad5e37d064395f1013` | `231133efa0902f4ba3ca96689ffb7e55a663dcec3516699f53770b356487e296` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | 11/11 manifest payloads verified; 45/45 offline evaluation tests; mature real outcomes 0 | `2026-10-28` | formal evaluation `false` | G216 evidence and separately authorized prospective evaluation |
| `H207` | `T46_SAFE_HOLD_207.zip` | `c8fd84d508a6df075af292d45ea048eaf98322df1ea4834cca0a88e6bdff5361` | `7f150082ad83dd5a3204f3043d20af0909571e24eff694349fd2509275a4f308` | `CODEX_REPORTED`; read-only audit | 9/9 manifest payloads verified; historical ledger 708 rows; scheduler enabled as last verified; recovery blocked | `2026-10-28` | recovery/scheduler mutation `false` | H217 evidence, writer exclusion, backup and recovery authority |
| `MARKET-FRED-WORKER-READINESS-HARDEN-169` | `MARKET_FRED_WORKER_READINESS_HARDEN_169.zip` | `eb98233ca3e721b73760de27a9bd6e5d5543f8bba7c429d9c449b9530cfde1c4` | `d5daa37d034ceadb09fb96d535521526ddc47099b5afd77603fe38ae2680f1ea` | `CODEX_REPORTED`; offline candidate | `NOT_PROVIDED` | `2026-10-27` | FRED network authority `false` | E196 remediation evidence and separate network authority |
| `STOCK-PROTECTED-TIME-RECEIPT-HARDEN-183` | `STOCK_PROTECTED_TIME_RECEIPT_183.zip` | `bb4b3cf5f2c558b882c64d6a868403de5b8bff0daf993363a8fbb6f6afb534ca` | `a550dedf02561ebaceec618c595a1e446d3a3db2321e57ea653503c915973685` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `NOT_PROVIDED` | `2026-10-27` | `false` | Production trust root |
| `T46-WEEKLY-WRITER-AUTHORITY-AUDIT-185` | `T46_WRITER_AUTHORITY_AUDIT_185.zip` | `06ee17006da77a8052925616227ebf2361995e1a186115984df44f4411154762` | `1cdf7ce089ee3b979b4adca7b798caccc7e37a03b592b61882479b5ae0d14875` | `CODEX_REPORTED`; read-only audit | `NOT_PROVIDED` | `2026-10-27` | `false` | Scheduler decision and T46 recovery authority |

The A184, E188, G183, and H185 pinned archives were locally rehashed and their 79 declared payloads were checked against their manifests: missing, mismatch, duplicate, unsafe-path, and undeclared counts were all 0. This is a Codex integrity check, not a substitute for GPT acceptance.

## Awaiting handoff or acceptance

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | LOCAL_INTEGRITY | GPT_ACCEPTANCE_SCOPE | STATUS_DECISION |
|---|---|---|---|---|---|
| `B202` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `WAITING_FOR_EVIDENCE` |
| `E209` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_HANDOFF_NOT_ACCEPTED` |
| `A211` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |
| `C212` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |
| `D213` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |
| `E214` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |
| `G216` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |
| `H217` | `UNKNOWN` | `UNKNOWN` | `NOT_VERIFIED` | `NOT_ACCEPTED` | `ASSIGNED_AWAITING_EVIDENCE` |

Assigned task identifiers do not establish evidence or acceptance.

## Live-before-F215 PR #9 Git blob and F180 ZIP-document identity

F215 re-read these six blob identities from the live PR files API at head `56a18e260b5b8a20ce482f30d48ccdc8172d7cc7` immediately before preparing the authorized update. They are the pre-publication baseline, not the candidate's post-publication blobs.

| Path | GIT_BLOB_SHA1 | GIT_BLOB_SHA256 | F180_ZIP_DOCUMENT_SHA256 | RAW_BYTE_IDENTITY | NORMALIZED_IDENTITY |
|---|---|---|---|---|---|
| `docs/project-status/CURRENT_STATUS.md` | `ceb50920c74874564b1d1dd0a8a8a0c6c2b74877` | `f9621189913462937819ae254caaedab7601361736d0a330584c3d89e47efe75` | `56e67b7817cf61ec4d834f9ab29cb3c15286c5c38460bd72d14832c4581cb12f` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |
| `docs/project-status/DECISION_LOG.md` | `4f81a622ed9b50b6846ce0cd904c859c3314cff0` | `8ced5b97d2192ad921eceac725d2bcde362d910a44e25d6b30dcdb3b9311cdb6` | `427e37eb47a22f4354387225423ad8452bb06892d40942ef174491c26737feb4` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |
| `docs/project-status/EVIDENCE_INDEX.md` | `6504ee4d304489861aa787ede16b694f5413c9b7` | `0d2aec25bc8a6ac60cf740c546672da06a05dbd2168f996a354e800d5a3b4f02` | `bb67de3004308daf32c068029e910d42bb7e845451585080f1bc50613fa2bbc2` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |
| `docs/project-status/RELEASE_GATES.md` | `d8e1ba19feb6b5f60c883fe602cb7b9b261e4bb4` | `b1e4e6f054a5f88c22f3469d0b535e9219b542ebcf8bfbc6fb85e46150fa9584` | `61f217e6e88428f0f9a0081f729a833f1279c185dd51efaf35007051d9501781` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |
| `docs/project-status/ROADMAP.md` | `535b2948be9b6c2ed2e779855f8b6b56565e2ecf` | `cabbb1d979dc4f501c61da74634f4d990b14efe095bbfac8819bf5f1dc291c76` | `858e4b797129cfb7cc38fbaac3d90abe87512cb4078b732bf69c6fa0a31d1d6f` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |
| `docs/project-status/WORKSTREAMS.md` | `9dbe6de63066a602bfaaa17817dab17418f775a3` | `e1db1c9b4c95a6a41758a3acd74dd983f895f5c186159c31ef61f9984cb73ab8` | `81b6d3310cf92e3547dcb91a65297fc77a644d9b9debbca9bc3d39bbc47cc86e` | `false` | `PASS_AFTER_REMOVING_ONE_TRAILING_LF_FROM_ZIP_DOCUMENT` |

Git blob SHA-1, Git blob byte SHA-256, ZIP-document SHA-256, handoff ZIP SHA-256, and manifest SHA-256 are distinct identities.

## F233 live PR #9 identity

| Path | LIVE_GIT_BLOB_SHA1 | F215_RECEIPT_BLOB_SHA1 | GATE |
|---|---|---|---|
| `docs/project-status/CURRENT_STATUS.md` | `2799d7735f8376614c91b61a1231e8e5d1c870f9` | `2799d7735f8376614c91b61a1231e8e5d1c870f9` | `PASS` |
| `docs/project-status/DECISION_LOG.md` | `fc9094bcf4375442566fe4921d8dc039ff124cba` | `fc9094bcf4375442566fe4921d8dc039ff124cba` | `PASS` |
| `docs/project-status/EVIDENCE_INDEX.md` | `aaad9f81ddcb0467a116df7ccfec4a63b66dce91` | `aaad9f81ddcb0467a116df7ccfec4a63b66dce91` | `PASS` |
| `docs/project-status/RELEASE_GATES.md` | `a0bfe71d330acc2d9bb536a0b3c9c83bfdc11485` | `a0bfe71d330acc2d9bb536a0b3c9c83bfdc11485` | `PASS` |
| `docs/project-status/ROADMAP.md` | `f9cc8a013c1c19ffd70d2c2a70d30499de6ae829` | `f9cc8a013c1c19ffd70d2c2a70d30499de6ae829` | `PASS` |
| `docs/project-status/WORKSTREAMS.md` | `bfcd30ff1ffcb4c78666664c1781b46c3fa18282` | `bfcd30ff1ffcb4c78666664c1781b46c3fa18282` | `PASS` |

## F233 newer evidence identity

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | PAYLOADS | INTEGRITY | CLASSIFICATION | STATUS |
|---|---|---|---:|---|---|---|
| `F215` | `cc5c3b9f2f3c0ad2803853c56d4970efb3dc91a675efb76ff558be3561c38c0a` | `78688ab1d6e51d776a08fee9c5cd5d8eff16321d202e9ceeb202c166cb0dc1ec` | 12 | `PASS` | `GPT_VERIFIED` publication receipt | `PUBLISHED` |
| `A221_STAGE2` | `0b7ffae86a01f3ea26495b561d71c7cfa7cfa00f76e1a0a3cb24101e84c90321` | `020b7ae17c43f28b1275ad40d1d46ce07f1413b5d4420af9f02c09cdc066b478` | 28 | `PASS` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `PASS_SYNTHETIC_ONLY_NO_FORMAL_AUTHORITY` |
| `B223` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `BLOCKED_INPUT` | `CODEX_REPORTED_EXTRACTED_EVIDENCE` | `BLOCKED_INDEPENDENT_PREFLIGHT_NOT_VERIFIED` |
| `E225` | `064f77252fc15822be9bc24b7902f3bd7cccc21b79db84be1f9a91eb545089d1` | `8f1a8142dff1348b2464737af6d26b58d883b3f5ec2e01f378b52672301bde8d` | 31 | `PASS` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `OFFLINE_POLICY_GATE_CANDIDATE` |
| `E225_ERRATUM` | `N/A` | `N/A` | `N/A` | `PASS_READ_ONLY_FILE_SHA256=c086a5bf51f349e80596e3ab8fd692984bbaed3cecb4961c36b35f6fc29cae4e` | `CODEX_REPORTED` | `IDENTITY_TRANSCRIPTION_CORRECTED` |
| `E227` | `66f2b8d0bba1dbf1e7dc3ea67dfe945ac846ec85bb0c17ce7f54a339e92d59d8` | `198fb72d7efd4259f7b105c9aba7ef10925add73e40df53b2b3970391663fdaf` | 42 | `PASS` | `CODEX_REPORTED`; `ARCHIVED_WINDOWS_EVIDENCE` | `TRUSTED_TEST_HARNESS_ONLY_CANDIDATE` |
| `H226` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `BLOCKED_INPUT` | `NOT_VERIFIED` | `BLOCKED_CANONICAL_HANDOFF_ABSENT` |

For each passing ZIP, F233 verified exact payload bytes/SHA-256, sizes, full stream reads (CRC), exact member set, path traversal, exact duplicates, and casefold aliases. Integrity does not constitute GPT acceptance or formal authority.

## F241 evidence status matrix

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | PAYLOADS | INTEGRITY | ACCEPTANCE_CLASS | BOUNDED_RESULT |
|---|---|---|---:|---|---|---|
| `F233` | `63ab92731509d28a263f3098bafec9ce719b953687da9f08bc77038231e951d7` | `86ccd9d3bc3b51fc8a3e6e043e3809b293f1d1e6ee45fe3091904be3d1e7678f` | 15 | `PASS` | `GPT_VERIFIED_INPUT_TO_F241` | docs-only patch candidate, historically not published |
| `D236` | `906d1f1958ebf819f3ef5e5380c1344c1f48336d5ea5ee671f2191421d2c76a7` | `54fa5ab6fe019e7665cafec2486630f48ded21e71b0b3a0b29e9bca080987625` | 9 | `PASS` | `CODEX_REPORTED` | corrected research cohort membership; no formal registry change |
| `A237` | `6a0dbcaffa5ccac7956afc2a1ab53a48a27608196d60045256af1de39f603b3e` | `caef8d52b05063971e3233e83640f0480065f78f97135fb25dc8117eb9be9684` | 18 | `PASS` | `CODEX_REPORTED` | three hypotheses affected; real outcome effect unproven |
| `E232_RESUMED` | `65a55ee1973cbc10b3c2c1be9bdebf6579facb0c7f36230b2dca59ad37acdca6` | `235d87d8664a9fe8730ca1ef0fd777b8f04e42c97b161c996c872fbbc79976db` | 18 | `PASS` | `CODEX_REPORTED` | 18/18 synthetic; zero accepted numeric features |
| `G234` | `5b2dd370733201388885ea0e37ad6b5628fe9d73c802953c945a127888435b7c` | `17b830a1559334d2c8efe0d51c3e191b41d1182cd2abc08cb7ea11ed4fec3fa5` | 10 | `PASS` | `CODEX_REPORTED` | 22 proposed decisions; activation disabled |
| `E239` | `6d69549b759bb1028e8108fc30f3ce6bcc8b2d760f09bb12f899c8b705023cf5` | `88ef6709c6acd81120cd9f27a3852a179189fdbf744a2c09d2373794f5dae5c8` | 20 | `PASS` | `CODEX_REPORTED_NOT_GPT_ACCEPTED` | later handoff; 18/18 plus 13/13 synthetic only |
| `G240` | `edc3cfcad4fd828e8fce319cf372a7efd4721a52e5cb7bce18e20f3ad5f87d4f` | `602ebe1906fc2f95860ca710cc343c5fa0ed9c02691dce6901d7dfbe6aaec5d6` | 11 | `PASS` | `CODEX_REPORTED_NOT_GPT_ACCEPTED` | later amendment candidate, not approved or activated |
| `B228` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `BLOCKED_INPUT_CANONICAL_HANDOFF_ABSENT` | `USER_SUPPLIED_STATUS` | 10/10 and 2710/2710 offline trusted harness; no production FRED authority |
| `B238` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `BLOCKED_INPUT_CANONICAL_HANDOFF_ABSENT` | `USER_SUPPLIED_STATUS` | Stage 1 complete; Stage 2 awaiting evidence |
| `H226` / `H235` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `BLOCKED_OR_INCOMPLETE` | `NOT_VERIFIED` | T46 remains `RECOVERY_BLOCKED` |

F241 stream-read each available archive and verified payload bytes/SHA-256, size, CRC, exact member set, traversal safety, exact duplicate names, and casefold aliases. Archive integrity and Codex self-reports are not GPT acceptance or formal approval.

## F286 first standing-publication evidence matrix

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | PAYLOADS | INTEGRITY | ACCEPTANCE_CLASS | BOUNDED_RESULT |
|---|---|---|---:|---|---|---|
| `AI-PILOT10-FISCAL-PERIOD-IDENTITY-280` | `9c19fd2fabb86291288b4a0ccef6c40b440f5eb3687be1ed4c5c8a2e1d208166` | `fea2f25561d7e21d19145faa3f5f47c54e6826bd5a2687576167ea7bc6761b1b` | 19 | `PASS` | `GPT_ACCEPTED_OFFLINE` | 40/40 independent offline validation; no official financial certification |
| `STOCK-CENTRAL-HANDOFF-INDEPENDENT-QA-279` | `17ac6f1f076eaa72608feeeef5f1823d152dda94acffd5828c8df59fb3484eb4` | `ca1a2b03a78fdef8f83d6d924a9b45964a70cbe7f5ca1d1107f8dbac813130a6` | 42 | `PASS` | `INDEPENDENT_FAIL` | 24/27 fresh passed; corrupted ZIP CRC metadata accepted; F276 release blocked |
| `STOCK-COCKPIT-OFFLINE-RUNTIME-STABILITY-281` | `6feb796872f1073bd2f148638803d1d3a2a89df60b1a4bca0c364a70f0322cb9` | `a86a80ff722dc94964306c56c2532ac03f1f1a31f03227326c1665f9528b99d1` | 49 | `PASS` | `GPT_ACCEPTED_OFFLINE` | TypeError reproduced and Node candidate verified; GUI not verified |
| `TW-OFFICIAL-DATASET-API-IDENTITY-282` | `43c56719da390ac39013e61f60294f4d391b7c4a08d85c32f76e682b2a5bcf3c` | `a0acb73750a4aa839f8b50e1931cdacd95151ac68847a582d8536ae76675e177` | 8 | `PASS` | `GPT_ACCEPTED_OFFLINE` document-review scope | API-resource byte identity unverified; real provider bytes 0 |
| `TW-SYNTHETIC-CAPTURE-COMPLETENESS-278` | `528bed1e4a9f50d2a7b9488b3cc63adf59a7798f186d5d97eeee62487f924c4d` | `dc59ec037940f1201105e0942fc9a682bbd5ec1f3b2e00beca85a0ed9ea63569` | 19 | `PASS` | `GPT_ACCEPTED_OFFLINE` synthetic scope | 18/18 synthetic replay; no parser/source approval |
| `STOCK-ALL-WORKSTREAM-ZIP-ONLY-ADOPTION-283` | `d5386db9b8a953b8e37bc9fb9ab4ec3e00462713aadcfc711b2e875e444e8642` | `9af97a9453b755fd3ef35bc9e4ce35372834adab943b682ac87bdc95f0e9fdab` | 17 | `PASS` | `CODEX_REPORTED` | templates/self-tests passed; independent F276 release gate failed |
| `STOCK-PROSPECTIVE-REAL-OUTCOME-READINESS-284` | `42759bce9124e762f97251dd278d74ccb18da8fc3f4365685f3351c6292748e9` | `e19891b49f572babf26fbc93d237d4c625936caaaeccf3f54dfb76d5af695535` | 10 | `PASS` | `CODEX_REPORTED` | eight studies; 22 decisions pending; mature real outcomes 0 |
| `T46-READER-R07-R10-PROVENANCE-285` | `ca71d3e60ae005edcbdef3fed901a89ce411e4cc41468b3dcc0f8addad267ac6` | `8cfd2a2745685811d81c0a192025bd6baa29785cceb4b32c4d9b58304df08f7d` | 7 | `PASS` | `CODEX_REPORTED`; `NOT_VERIFIED` for formal identity | R07 lineage mapped; R10 historical snapshots; migration 0/12; T46 blocked |

F286 independently matched all eight outer ZIP SHA-256 values, raw manifest SHA-256 values, manifest task IDs, declared payload hashes and byte sizes, exact member coverage, duplicate/casefold safety, and full entry reads. This integrity gate is intentionally separate from acceptance and release classification. In particular, the B279 `INDEPENDENT_FAIL` is not overwritten by the same archive's integrity pass.

## F295 triage evidence

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | EVIDENCE_CLASS | BOUNDED_RESULT |
|---|---|---|---|---|
| `STOCK-PR9-CONTINUOUS-EVIDENCE-PUBLICATION-286` | `ddd2377a80458639dcbe3f0ebb49e1691db3a25aad7d8ac5180257652e5b2aaf` | `5da6a403c0d5d9884590b819b73f9e4f1899dd6b329a13e130ff35c4ac6aa336` | `ARCHIVED_EVIDENCE` | Original docs batch and two regression failures preserved; archive integrity does not imply publication |
| `STOCK-ZIP-ONLY-COLLECTOR-INTEGRITY-FIX-287` | `144c69794cda149762f7d420db50199fe2c979da959e7cfb72a74dacc0f38e20` | `991e460bb5a7aaea8cac99b3365b3fd24dd86529bfda5705d480fb75b4648bee` | `CODEX_REPORTED` | Patched candidate only; B291 independent acceptance remains required |
| `STOCK-CENTRAL-RECEIVED-ARCHIVE-289` | `25dae9b7465cbee1c450902d3129d287aaa6e4b404893b0e49ee13a38393451a` | `ee257d2413b8fc4f6e6814132ef0bb7f60d1e6a67e8b4f54f1383fce13c1d627` | `ARCHIVED_EVIDENCE` | Central archive operation verified; no GitHub or formal-data authority |

F295 fresh execution reproduced both baseline failures without raw fixtures, then passed both targeted tests after mounting the existing hash-identified official raw store. The full regression suite passed 669/669 with that prerequisite and an ASCII-only temporary directory. These results establish repository regression readiness for the docs batch only; they do not constitute official-source certification, formal approval, or production activation.

## F312 evidence identities

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | PAYLOAD_COUNT | SOURCE_LEVEL | BOUNDED_RESULT |
|---|---|---|---:|---|---|
| `AI-21-COMPANY-FINANCIAL-EVIDENCE-ONTOLOGY-298` | `45058c0f7a240d3fdcb34bab16e068a2e053eab40f9fe58f26fe7881fde994cb` | `ae44c04753e7b1afca8481e210bd3a96d74068447f0b8e264b9410c0009782c2` | 19 | `CODEX_REPORTED` | 21-company ontology candidate; no formal certification |
| `TW-OFFLINE-RAW-IMPORT-INDEPENDENT-QA-299` | `9bd655a5f35396196d549aa3df8a139200899b813f7c15428e46ea73ff2bed9e` | `57449481ef32e9b9a255f637e371ba93fd772c85286ca31f37c88f591bcb67ed` | 15 | `INDEPENDENT_FAIL` | 39/43 independent; four failures preserved |
| `STOCK-WORKBENCH-MANUAL-RAW-EVIDENCE-PREVIEW-300` | `9ce7cc9c587fa1bb0f11817cc54caf7452de35b32a24cba08a0933f7a15932e5` | `5886ed841191a55db4d367aaada257743fb453ffef5a4b941a136d6ac1d96e80` | 42 | `CODEX_REPORTED` | 89/89 offline; GUI not verified |
| `TW-OFFICIAL-CSV-RAW-ADMISSION-CONTRACT-301` | `71140793f26b704d83c600032213759ef244d03c4c22df68b218f1f713fffc3d` | `c392f7b51d3d608ce298ebfb14695feca04bec44909b5f0fe48e4e603d4ffdad` | 8 | `CODEX_REPORTED` | Contract candidate; CSV implementation blocked |
| `TW-OFFLINE-CSV-QUARANTINE-IMPORTER-302` | `566bbb34778a143a961afd0d4f38960f0ebe93bfefe3f05dfac40241c4cb05ca` | `9d11060546af867ad13f3805069885c81dbd2e58718af32d2d55b3041da71969` | 21 | `CODEX_REPORTED` | 70/70 isolated candidate; B299 path/cleanup gaps remain |
| `STOCK-COLLECTOR-RUNNER-COMPATIBILITY-303` | `72b8d6528262d5d221db3390d94766952f0936c01cd2adf5c5b473aceeb9e20f` | `f8ddf070a50c9829b9dfa812c7f84f65d0de24d7d7d0cac0edef2ee77009211e` | 26 | `CODEX_REPORTED` | Candidate passes tested gates; not deployed |
| `STOCK-PROSPECTIVE-SAMPLE-COMPLETENESS-304` | `11a37659355225413f65cf1c58183315b5c391ad1a0e6f2f29301bc522fd916f` | `43f2178b4469d4457cfc7f871d78e55b2fb11aee463797a8d2a6a33816f12f2a` | 18 | `CODEX_REPORTED` | 28/28 synthetic; no real outcomes or predictive-value claim |
| `STOCK-MANUAL-OFFICIAL-DATA-OPERATIONS-305` | `d48e43917dcf2930b71c34ecd8af9c72abd54ec5ef729ce53dd99c9ab94f1075` | `b9aeab3bcb83058d5698b902e95c31e5596cb5ab329b3e30963c20ffb032e355` | 7 | `CODEX_REPORTED` | Documentation only; no provider bytes or admission |
| `STOCK-CENTRAL-READY-LOCATION-FIX-306` | `69f2c921f47833dcc8d3b9a64b541c27e2220afd670a113b88c8ed807b3a99fb` | `37e4022f9d95fe78b7256f4b71bdc618d49d4ee223aa4d0d7fcfa944b8690f55` | 7 | `CODEX_REPORTED_LOCATION_AND_INTEGRITY` | A298-H305 central READY 8/8 verification; not a release gate |

All nine manifest identities above were read from the exact archives. Archive integrity and central location remain separate from functional acceptance, deployment, and `FORMAL_APPROVED` authority.

## F320 Round 36 evidence identities

| TASK_ID | HANDOFF_ZIP_SHA256 | MANIFEST_SHA256 | PAYLOAD_COUNT | MANIFEST_AUTHORITY | BOUNDED_RESULT |
|---|---|---|---:|---|---|
| `STOCK-PR9-EVIDENCE-CURRENTNESS-312` | `1593f9d50e104fe35761f938c525c3a510e681985cc618de870d0d1ebf5848f0` | `821c45ee42ba95c07865b7c0303075dcd3b620ac7cb06c99826a0f4dca07db5e` | 9 | `CODEX_REPORTED` | Docs-only Draft PR publication at `2dafa6c5d5abe5ba95d0632a92e89ce784893590`; archived 822/822 is historical, not F320 execution |
| `AI-FIRST-REAL-EVIDENCE-ACQUISITION-307` | `9daab7ee95c9a9478ab5bbfe2a548e1d666884b8270a98a307712f8bfc324d18` | `4e25ac70d795b8af6ef0265fe082662d8c1e0ea83c39cd27e2aaae1fd8f6a7b4` | 19 | `CODEX_REPORTED` | `PASS_OFFLINE`; no real observation or live fetch |
| `TW-E302-INDEPENDENT-IMPORT-QA-308` | `c998db39fae0c0bb26d0af5375c99b746ee51b068759dc22f3a3588c3f6d4450` | `68c4ea045112391e8d50b1b4ec328f6d3ff9393ed063750c9c7ce64a55f4507b` | 27 | `INDEPENDENT_FAIL` | `15 PASS / 4 FAIL`; package self-containment failed |
| `STOCK-WORKBENCH-RAW-INTAKE-HANDOFF-309` | `8c92bb939b857874742a224d97178a44b26a5bee4574b0a107535bc034a861a2` | `4fb355e11aa85c133225ec7a0d16ff544bc9c9344bc1633102b21864791411f9` | 50 | `CODEX_REPORTED` | `110/110` offline; GUI `NOT_VERIFIED` |
| `TW-CSV-ADMISSION-INTEROPERABILITY-310` | `83b63765fae654290f1f5de0801a96952e187ecb96e860d924da8074b6d3221d` | `526fc26fe2a7ace61c1d3015870e874a6d5131bbab19903f2e4c8ae3ef65268f` | 8 | `CODEX_REPORTED` | `25/25` synthetic contract; real provider bytes `0` |
| `TW-OFFLINE-IMPORT-OUTPUT-BOUNDARY-311` | `4eb8101e8527340c86e89b6584146b96a6914fce2dd141919e6dc1bb337a3e7a` | `973d8c7f7b5e36d44b0a62b15c51803c85606de07dfe06d13db30d63c451155e` | 23 | `CODEX_REPORTED` | `59 PASS / 1 NOT_VERIFIED`; junction `STATIC_DEPENDENCY_GAP`; release blocked |
| `STOCK-PROSPECTIVE-COMPLETENESS-QA-313` | `b3ac328475b94b3120fc882c4df059f7c64085dc2fe8c92596fffa3bae3209d4` | `68d0523f8fca5484adb0c7a0f689854cf7626155518cc0ef6d372ef5f80c499c` | 21 | `CODEX_REPORTED` | `12/12` fresh synthetic QA; real observations `0` |
| `STOCK-USER-MANUAL-CSV-HANDOFF-314` | `34db648f6cecf62589b4bce747eaf216c5d67fcef337022cae6634e1fc438991` | `e3e30003ed960929ef9fc98ddfed2ea8fd5723adba7b5286d0dbb8520c2b2a7b` | 10 | `CODEX_REPORTED` | Synthetic local receipt helper; source admission and PIT absent |

F320 independently verified all 167 declared payloads across these eight archives, with missing, hash mismatch, size mismatch, CRC failure, duplicate, casefold alias, unsafe path, undeclared file, and task-ID mismatch counts all zero. Raw archive identity, reported test status, independent acceptance, and formal authority remain distinct fields.
