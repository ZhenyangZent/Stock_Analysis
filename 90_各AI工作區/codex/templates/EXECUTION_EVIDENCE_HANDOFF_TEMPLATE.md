# Execution / Evidence Handoff

CONTRACT: `<exact authorized work>`
STATUS: `<one terminal state>`
REPOSITORY: `ZhenyangZent/Stock_Analysis`
TASK_ID: `<id>`
RUN_ID: `<id or N/A>`
AS_OF_UTC: `<timestamp>`

AUTHORITY_SOURCE: `<path/message identity>`
AUTHORITY_SHA256: `<64 lowercase hex or UNKNOWN>`
AUTHORITY_SCOPE: `<writes/actions allowed>`
PROTECTED_SCOPE: `<explicitly forbidden paths/actions>`

HEAD_SHA_BEFORE: `<full sha>`
HEAD_SHA_AFTER: `<full sha>`
BRANCH_BEFORE: `<branch>`
BRANCH_AFTER: `<branch>`
WORKTREE_STATUS_BEFORE: `<exact>`
WORKTREE_STATUS_AFTER: `<exact>`
RULE_VERSION: `<version>`
RULES_SHA256: `<sha>`
RULES_ARCHIVE_EQUALITY_GATE: `<PASS|FAIL|NOT_RUN>`

LOCK_STATE_BEFORE: `<holder/scopes/expiry/hash or N/A_READ_ONLY>`
LOCK_STATE_AFTER: `<holder/scopes/expiry/hash or N/A_READ_ONLY>`
LOCK_MUTATION_COUNT: `<integer|NOT_RUN|UNKNOWN>`
LOCK_CHANGE_ATTRIBUTION: `<identity or UNKNOWN>`

INPUT_MANIFEST_SHA256: `<sha or N/A>`
INPUT_ARTIFACTS: `<path/member, bytes and hashes>`
POINT_IN_TIME_CUTOFF: `<timestamp or N/A>`
SOURCE_TIMES: `<published/available/fetched>`
ENVIRONMENT: `<OS, Python major.minor, tzdata, dependencies>`

FILES_CHANGED: `<exact paths or NONE>`
BEFORE_AFTER_HASHES: `<path: before -> after>`
COMMANDS_EXECUTED: `<exact commands, secrets redacted>`
TEST_STARTED_AT_UTC: `<timestamp or NOT_RUN>`
TEST_ENDED_AT_UTC: `<timestamp or NOT_RUN>`
TEST_RESULT: `<PASS/FAIL/NOT_RUN with counts>`
RAW_TEST_OUTPUT: `<artifact path and sha or UNKNOWN>`

AUTHORITY_GATE: `<PASS|FAIL|NOT_RUN>`
CONFLICT_RECONCILIATION_GATE: `<PASS|FAIL|NOT_RUN>`
SCHEMA_GATE: `<PASS|FAIL|NOT_RUN>`
POINT_IN_TIME_GATE: `<PASS|FAIL|NOT_RUN>`
APPEND_ONLY_PREFIX_GATE: `<PASS|FAIL|NOT_RUN>`
WRITE_LEDGER_SYNC_GATE: `<PASS|FAIL|NOT_RUN>`
SECRET_SCAN_GATE: `<PASS|FAIL|NOT_RUN>`
INDEPENDENT_REVIEW: `<PASS|FAIL|PENDING|NOT_RUN>`
REVIEW_ARTIFACT_SHA256: `<sha or UNKNOWN>`

HISTORICAL_EVIDENCE_REUSED: `<paths/hashes or NONE>`
REVALIDATION_TRIGGER_FIRED: `<trigger or NONE>`
KNOWN_GAPS: `<explicit list>`
ROLLBACK_OR_RECOVERY: `<steps and new authority required>`

LOCK_ACQUIRE_COUNT: `<integer|NOT_RUN|UNKNOWN>`
LOCK_RELEASE_COUNT: `<integer|NOT_RUN|UNKNOWN>`
LOCK_FORCE_RELEASE_COUNT: `<integer|NOT_RUN|UNKNOWN>`
REPOSITORY_FILE_WRITE_COUNT: `<integer|NOT_RUN|UNKNOWN>`
LEDGER_APPEND_COUNT: `<integer|NOT_RUN|UNKNOWN>`
FORMAL_SNAPSHOT_COUNT: `<integer|NOT_RUN|UNKNOWN>`
TEST_EXECUTION_COUNT: `<integer|NOT_RUN|UNKNOWN>`
STAGE_COUNT: `<integer|NOT_RUN|UNKNOWN>`
COMMIT_COUNT: `<integer|NOT_RUN|UNKNOWN>`
PUSH_COUNT: `<integer|NOT_RUN|UNKNOWN>`
PUSH_SUCCESS_COUNT: `<integer|NOT_RUN|UNKNOWN>`

## Counter evidence rules

- Every counter is required and must report the action count measured for this task. The placeholders above are not default values or evidence of success.
- Use `0` only when before/after evidence or an execution log proves that the action did not occur. If the action was not executed, use `NOT_RUN`; if it cannot be established, use `UNKNOWN`.
- A write task must record actual lock acquire/release/force-release counts, test executions, repository-file writes, ledger appends, formal snapshots, staging actions, commits, pushes and successful pushes.
- Do not inherit a prior handoff's counts or convert an unmeasured action to zero. Do not report `PASS` merely because a template field was left unchanged.

ARTIFACTS: `<links, bytes, hashes and integrity>`
NEXT_ACTION: `<next legal action, required authority, blocker-release condition, and prohibited actions>`
