# T46 derived analysis correction V2

DOCUMENT_STATUS: `CANONICAL_DERIVED_CORRECTION`
AS_OF_UTC: `2026-10-20T14:33:07Z`
SUPERSEDES_FIELDS: `t46_analysis.json:appended_suffix.document_type_counts,t46_analysis.json:appended_suffix.duplicate_source_url_groups`
DOES_NOT_RECOVER_T46: `true`

## Immutable input

- Audit ZIP SHA-256: `9ba5588e968c84a8ac032eeeb798e4882689ad2a41eaf170d97634d9f062b03b`.
- Ledger member: `GPT_TAKEOVER_AUDIT_EVIDENCE/05_T46_EVIDENCE/docs_ledger.jsonl`.
- Ledger bytes: `668666`.
- Ledger SHA-256: `0b7f5242ad52557b6a07f7b0abe4d6fcf9821b09589a997cd7a57563676f3b88`.
- Original evidence and ledger mutated: `false`.

## Error corrected

The earlier derivative read nonexistent fields `document_type` and `source_url`. The actual ledger schema uses `doc_type` and `url`. This V2 supersedes only the two affected derivative statistics and preserves the historical report and ZIP.

## Deterministic method

The review tool `recompute_t46_v2.py` SHA-256 is `61d185bf74f04253bc40e24f9d088cd86aa9634a81249cb16997fbc3147930e4`. It verifies ZIP CRC and expected ZIP/ledger/prefix hashes, splits the first 107 JSONL rows as the approved prefix, requires `doc_type` and `url`, counts suffix types, and counts non-empty URLs occurring more than once within each partition. It also intersects prefix and suffix URL sets. Output is written as explicit UTF-8 bytes with LF line endings, independent of the host operating system's newline convention.

Review-package command (paths are relative to the external `GPT_KNOWLEDGE_FINALFIX_20261020` review package, not to the repository):

```text
py -3 03_TOOLS/recompute_t46_v2.py <GPT_TAKEOVER_AUDIT_EVIDENCE_20261020.zip> --output 01_CANDIDATES/T46_DERIVED_ANALYSIS_V2.json
```

Canonical LF output JSON SHA-256: `041335c51e6c0155ca7e9c0735edd8c1c26b4c263a09a3da72dbd9bbaee52e64` (`2197` bytes; `71` LF; `0` CRLF).

The earlier Windows review artifact used CRLF line endings and had SHA-256 `750e87aa20e06c57aa1c85d8bff002fd0c0eb4ced09bbf673449765d6bbd1824` (`2268` bytes; `71` CRLF). Its parsed JSON value is identical to the canonical LF result. It is preserved as historical evidence and is superseded only for cross-platform byte identity; no analytical field or statistic changed.

## Corrected results

- Total ledger rows: `706`.
- Original prefix: `107` rows, `108430` bytes, SHA-256 `e5d5e81a1859a0b4a708c9880428c1353be51b74820451ac13d127219acd87b9`.
- Appended suffix: `599` rows, `560236` bytes, SHA-256 `15489db73ae3c4fbd76e82d68868575300e874dbf92c17bcd5e7b4c156db52ec`.
- Suffix `doc_type`: `10-K=76`, `10-Q=220`, `8-K-EX99-EARNINGS=303`.
- Suffix duplicate `url` groups: `0`.
- Prefix duplicate `url` groups: `6`.
- Prefix/suffix shared URLs: `0`.

## Interpretation and prohibition

These statistics describe the incident evidence only. They do not validate the 599 appended rows for formal research and do not authorize recovery, truncation, file movement, lock mutation, ledger replacement, snapshot creation, or Git publication. T46 remains blocked until a separately authorized recovery passes lock-before-side-effect, prefix, append-only/write-ledger, rollback, and independent-review gates.
