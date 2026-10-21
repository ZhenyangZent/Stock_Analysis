# ADR-YYYY-NNN — <decision title>

ADR_STATUS: `PROPOSED|ACCEPTED|SUPERSEDED|REJECTED`
AS_OF_UTC: `<timestamp>`
DECISION_EFFECTIVE_AT: `<timestamp or N/A>`
DECISION_AUTHORITY_LINK: `00_規範/使用者決定紀錄.md#<date-and-title>`
AUTHORITY_EVIDENCE: `<message/task packet/artifact path>`
AUTHORITY_SHA256: `<64 lowercase hex or UNKNOWN>`
RULE_VERSION_BEFORE: `<version or N/A>`
RULE_VERSION_AFTER: `<version or N/A>`
SUPERSEDES: `<ADR/path/hash or N/A>`
SUPERSEDED_BY: `<ADR/path/hash or N/A>`

## Context and reason

Describe the problem, why a decision is needed now, the affected formal operations, and the consequence of taking no action.

## Evidence baseline

For every `VERIFIED` claim, record source path/member, bytes, SHA-256, published/available/fetched/cutoff times, and the recomputation method. Separate `INFERRED`, `UNKNOWN`, and dynamic tests not run.

## Alternatives considered

For each alternative record benefits, costs, risks, compatibility, reversibility, and why it was selected or rejected. Include the status-quo option.

## Decision

State the exact choice, scope, effective time, thresholds changed or explicitly unchanged, writable/protected paths, and whether implementation requires separate authority.

## User approval evidence

Quote or identify the explicit approval, timestamp, channel/task identity, and hash where available. `ACCEPTED` is forbidden without approval evidence.

## Consequences and trade-offs

Describe expected behavior, compatibility, data eligibility, point-in-time effects, legal/licensing/privacy constraints, and remaining uncertainty.

## Implementation and independent review

- Commit/PR: `NOT_RUN`
- Files and before/after hashes: `NOT_RUN`
- Commands and environment: `NOT_RUN`
- Tests and raw output: `NOT_RUN`
- Independent reviewer/result/artifact hash: `NOT_RUN`

## Recovery and supersede semantics

Specify rollback or additive correction procedure, append-only restrictions, irreversible steps, gates, and the exact link to any replaced decision. Never delete historical ADRs.

## Revalidation triggers

List the code, input, rule, schema, environment, evidence-vintage, authority, or outcome changes that require re-evaluation.

## Authority boundary

This ADR explains a decision. If it conflicts with the formal decision record or current/archived rules, stop the affected operation and request reconciliation. This ADR alone never authorizes mutation.
