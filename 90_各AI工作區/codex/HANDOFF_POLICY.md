# AI Handoff Policy

DOCUMENT_STATUS: `CANONICAL_HANDOFF_DOCUMENT`
TARGET_PATH: `90_各AI工作區/codex/HANDOFF_POLICY.md`
AS_OF_UTC: `2026-10-20T14:33:07Z`

## 1. Authority

This policy defines handoff evidence; it does not authorize rule, data, snapshot, recovery, lock, or Git mutations. The formal decision authority remains `00_規範/使用者決定紀錄.md`; executable thresholds and protocol semantics remain in the current and archived rules.

## 2. Required evidence states

Use only `VERIFIED`, `INFERRED`, `UNKNOWN`, `BLOCKED`, `SHADOW`, and `SUPERSEDED`. `VERIFIED` requires a source path/member plus complete SHA-256 or a deterministic recomputation bound to such input. Every claim includes `AS_OF_UTC` or a historical cutoff. Never present a historical verification as current without a live-state refresh.

## 3. Mandatory takeover refresh

Every takeover records live Git HEAD/branch/status, current rule version/hash/archive equality, latest valid formal snapshot/cutoff/input hashes, lock state/history, and exact authority. If any differs from the handoff, the handoff remains historical evidence only.

## 4. Conflict gate

When user decisions, current rules, archived rules, canonical ledger semantics, or a handoff disagree, stop the affected formal action and request reconciliation. Record all conflicting paths and hashes. Do not choose, merge, repair, or supersede without authority.

## 5. Minimum handoff fields

Include task/contract, authority identity/hash, repository and before/after Git state, rules version/hash, input manifest and artifact hashes, point-in-time cutoff, environment, exact actions and commands, before/after hashes, gate results, test counts and raw-output evidence, action counters, protected-scope proof, known gaps, rollback/recovery requirements, independent review, and exact next authorized action.

## 6. Decision traceability

Each formal decision or linked ADR preserves decision reason, direct evidence, alternatives, trade-offs, user approval evidence, affected scope, implementation/review evidence, `SUPERSEDES` and `SUPERSEDED_BY` links, and revisit conditions. ADRs are explanatory and never become a second decision ledger.

## 7. No-repeat and revalidation policy

Do not rerun verified work merely because another model takes over. Reuse hash-bound evidence for the same historical question. Revalidate when code, data prefix, rules, schema, environment, authority, source vintage, or acceptance criteria change, or when the artifact cannot be restored. State exactly which trigger fired.

## 8. Supersede and append-only policy

Do not delete or silently overwrite historical evidence. A replacement names the old artifact and hash, the exact invalidated fields, the reason, and the effective scope. Canonical ledger corrections follow their schema and write ledger; Markdown cannot replace a ledger correction.

## 9. Sensitive and restricted evidence

Never publish secrets, `.env`, private keys, portfolio data, personal financial details, or unlicensed raw content. For large or restricted artifacts, store safe provenance and a restorable backup identifier; a hash without an obtainable object is not full reproducibility.

## 10. Reviewer rule

Material code, rules, canonical data, formal snapshots and recovery actions require independent read-only review unless the user's authority explicitly says otherwise. Reviewers recompute critical values and hashes rather than restating the implementer's summary.

## 11. Artifact ZIP archive policy

Every Codex evidence ZIP has one canonical workspace location: `01_EVIDENCE/YYYY-MM-DD/TASK_ID/<versioned-name>.zip`. Create new ZIPs directly there; do not place them on the desktop, in Downloads, at repository root, or at an evidence-date root for later sorting. Task temporary files belong only in `99_TEMP/TASK_ID/`.

Before accepting an artifact, record its task ID, original evidence time, byte size, complete SHA-256, member count, CRC result, path-safety result, duplicate-member result, review state, supersede relation, applicable Git/rules/snapshot identity, and whether an independently restorable second copy exists. The durable local index is `00_START_HERE/ARTIFACT_INDEX.json` in the Codex workspace. A new version may supersede an older one, but never deletes or overwrites it; version succession and independent review are separate facts.

When a legacy ZIP must be organized, copy it to the canonical destination without overwrite, verify SHA-256 before and after, validate CRC/path safety/unique members/member count, and only then remove the verified duplicate source when it is inside the Codex workspace and the task authority permits removal. Any mismatch, unknown ownership, or destination collision stops the affected move. Never remove user or other-AI artifacts.

Same-disk organization is not an independent backup. A GitHub hash or index entry without an obtainable original object is not recoverability. Do not automatically publish ZIPs to GitHub; secrets, `.env`, portfolio data, personal financial data, restricted content, and blocked incident ledgers remain excluded. Repository policy may link to this workflow, but the formal decision ledger and repository rules remain authoritative.
