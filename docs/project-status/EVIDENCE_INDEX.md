# Evidence index

DATA_AS_OF: `2026-10-26`
LAST_VERIFIED_AT: `2026-10-26T21:21:10+08:00`
STATUS_TASK_ID: `STOCK-GITHUB-STATUS-CURRENTNESS-180`

All paths below are portable evidence identifiers, not promises that a contributor's local filesystem is reachable from GitHub. F180 read every declared ZIP payload and matched it to the corresponding manifest SHA-256: 4,088 matched, 0 missing, 0 mismatched.

| Task | Evidence identifier | ZIP SHA-256 | Manifest SHA-256 | Evidence date | Source level | Verified statement |
|---|---|---|---|---|---|---|
| `F170` | `STOCK-GITHUB-STATUS-GOVERNANCE-170.zip` | `c0a0af692e401c4cd1971f2c28d11cff60676f2dd61ff1a9f487a0301f138f62` | `daac394c0aeff4687b86213d597fb743f7d39a367c4fd1b1695d9837e0f7ccb6` | `2026-10-26` | `CODEX_REPORTED` | Offline candidate exists; publication counters were all zero |
| `A171` | `AI_SUPPLY_CHAIN_DEEP_RESEARCH_171.zip` | `6d090b35872b2be1498beda914339babb86d19f03d2d04675ccb9ba9f349fe99` | `7b48c3b6c52d6f7cb0e985ac878c41b74497dfec886a7d1bb17d72b5e07774cd` | `2026-10-26` | `CODEX_REPORTED` | `SHADOW_RESEARCH_ONLY`; no formal universe change |
| `D172` | `D172.zip` | `64728e67c8a63708fc37c9486d80baa6267cde69dc444e002f4d0b89ac65022e` | `7ac7d7d43ccee26be94b5f46674916d3adcbb5f33d64b79d29136d0a02ad1a38` | `2026-10-26` | `CODEX_REPORTED` | 16 source categories; 0 `FORMAL_APPROVED` |
| `C173` | `STOCK_RESEARCH_COCKPIT_173.zip` | `835621170ad14d61db52fae7d7de693e52aed5863f937b1d05ded665e9179a16` | `c4e1ac168b365e48538a2f13f7d2015ec44d96dfec454df3eb32eb0acee777b6` | `2026-10-26` | `CODEX_REPORTED` | F180 packet says `OFFLINE_ACCEPTED`; GUI remains `NOT_VERIFIED` |
| `G174` | `STOCK_FORWARD_EVALUATION_174.zip` | `50987961dacc4c80dc52af9c8b7f50767a000f473b9ad1c2ddc33bb2ee450dce` | `552aec371e834b45720f1058581cf822eb458973f75135e3b6604f894cd1e0b3` | `2026-10-26` | `ARCHIVED_TEST_ONLY` | 17 synthetic tests passed; real predictive value is not established |
| `H175` | `T46_RECOVERY_READINESS_175.zip` | `d8102ee2a39738f549f0594adf7f7ffa0325bcbb03dd8058617c7106c9824425` | `970f0198ce3392894791ebb38edcc3644b314a693184c30cd94782b0989d5abf` | `2026-10-26` | `CODEX_REPORTED` | 706-row historical prefix preserved inside a 708-row live ledger; recovery blocked |
| `B176` | `TW_REVENUE_INDEPENDENT_AUDIT_176.zip` | `539eaf809a137de808c8ec6e51091eb5da22568714f0b92de84e14e74754119c` | `dbbbf9da7a88c5cdec104b567c30fefb93a7855f8996918598969feb054ebad9` | `2026-10-26` | `CODEX_REPORTED` | Five lookahead/integration checks failed; overall qualification `FAIL` |
| `E169` | `MARKET_FRED_WORKER_READINESS_169.zip` | `eb98233ca3e721b73760de27a9bd6e5d5543f8bba7c429d9c449b9530cfde1c4` | `d5daa37d034ceadb09fb96d535521526ddc47099b5afd77603fe38ae2680f1ea` | `2026-10-26` | `CODEX_REPORTED` | Offline candidate only; awaiting `B177` |

## Additional bound evidence

- H175 ledger SHA-256: `872e02e7b21694eba2dbcd3c44ed64ef22cf600bac2f216014214d150ce1f0d2` for 708 live rows.
- H175 historical 706-row prefix SHA-256: `0b7f5242ad52557b6a07f7b0abe4d6fcf9821b09589a997cd7a57563676f3b88`.
- B176 input identities include A162 `8a6d980fc6afaccb8f8de699b4e6996c262355ab1fa0b03ea26078f67f1a08e8`, D164 `22c1b4675696c11bc54c12cdb845ddbc0864c06b0de1e5bb0bfee1a2a76c3a4e`, and A152 `534ffe33df970ecf0728e79f6bbf3ed7f96b54fae6d0cb44fb2e425368422e4c`.

## Evidence update rule

An evidence row may be promoted only when the exact ZIP, manifest, verification date, reviewer identity, and acceptance scope are recorded. `CODEX_REPORTED` never silently becomes `GPT_INDEPENDENTLY_VERIFIED`; synthetic success never establishes live predictive performance.
