# FASTA source-backed publication candidate

This is a pure, default-disabled model for `fasta_intake_review_v0.1`. It adds no
HTTP, repository, SQL, storage, Owner permission, worker, engine or service. It
does not materialize or persist an index. Existing TXT format/routes/DTOs and the
existing FASTA parser/vendor normalizer are unchanged.

`build_candidate()` refuses with `fasta_intake_unavailable` before inspecting a
source or invoking a control hook. Parsing requires the exact
`configuration_json()` and a trusted `ParseControl`. Import and construction do
not open runtime files, create threads, call a clock, connect or scan anything.
Python's normal source/dependency loading is not runtime input-file attestation.

```python
# Only inside a future admitted server parse slot, after Owner/source resolution:
control = ParseControl(checkpoint=server_checkpoint)  # synchronous, returns None
candidate = build_candidate(
    stored_source,
    expected_project_id=project_id,
    expected_source_id=source_id,
    config_json=configuration_json(),
    control=control,
)
```

`StoredSource` contains exact original bytes and strictly decoded source metadata.
`source_from_json` accepts only bounded UTF8 JSON with exact keys, scalar filename,
canonical UUID/SHA/timestamp and an exact `bytes` body. It verifies full length and
SHA. Directly constructed instances are validated again by the builder. A
project/source mismatch, source corruption, or parse result whose observed bytes
or SHA differs from the complete source cannot produce a candidate. Even a
normally exhausted, valid FASTA prefix is incomplete and rejected.

The original `parse_fasta` and its eight ceilings are reused without modification.
Every record, header, case-sensitive ID, sequence line, raw sequence/hash, ordinal,
canonical value/status/issue and complete duplicate-ID mapping is retained and
validated. Invalid or empty sequences remain records with no canonical identity;
they are not corrected or omitted. Unsupported encoding/structure/limits reject
the complete publication. Provider-specific identifiers may be narrower than
FASTA input; this model preserves them and adds no Provider substitution or
silent renaming. Lookup remains the original all-ordinal mapping.

The approved config is exactly 848 canonical ASCII JSON bytes, SHA
`380475bafbccc807ba0745ca595d3f6fe4953412a6d8403f5f14dab2fd2ce0ac`.
It pins baseline `51ae7bd2e0a156e902839d4e60b98f73f964263d`, `fasta_index.py`
and `sequence/vendor_domain_r1.py`, parser limits, identity/serialization and
publication versions. Config strings with different pins, limits or whitespace
refuse. These metadata pins are checked as an exact profile; they do not inspect
or attest live module files. Future trusted startup must verify its reviewed
complete release. No arbitrary import path or runtime file-reading API is added.

Version UUID5 uses namespace `ae925a0c-6877-58a0-a4f8-1ec6a8301191` and the
colon-separated project ID, source ID, source SHA, index contract and config SHA.
Two different source rows with identical bytes remain different versions. All
supported JSON integer fields reject booleans, fractions/non-finite or unsafe
numbers and normalize integral JSON values such as `1.0` to integers.

The full fingerprint streams canonical JSONL (`sort_keys`, compact separators,
`ensure_ascii=True`, no NaN), hashing a final LF on every line. The first envelope
is `{kind: index, value: <source/contracts/counts>}`, followed by each complete
Record envelope in ordinal order, then `{kind: id_ordinals, id, ordinals}` in
ASCII key order. No record field is omitted. Only the digest is returned; the
whole serialized stream is not retained or logged. Summary JSON has a separate
digest and cannot stand in for the full fingerprint.

`decode_candidate` and `validate_index_dto` check exact shapes, fixed config,
version derivation, summary consistency/digests and immutable flags. They are
structural validators, not proof that somebody parsed or owns a source.
`index_dto(candidate, db_created_at)` returns only metadata/counts and explicit
`index_materialized=False`; it never contains original sequences. A future
Provider must use `rebuild_and_verify`, with matching expected project/source/
version IDs and a freshly Owner-authorized stored source. It reparses ALL source
bytes and compares the complete candidate, including full fingerprint, config and
summary before returning an immutable `VerifiedFastaIndex`. There is no cache or
summary-only shortcut. This object attests integrity only, not Owner/DB authority.

`ParseControl` is trusted server-only code, not client JSON. Its synchronous
checkpoint returns None or raises; raising maps to fixed timeout without exception
text. The default thread CPU clock is sampled around work, between controlled
64KiB chunks and encoder pieces, and immediately before returning a candidate.
The soft CPU ceiling is 8 seconds and may only be lowered. The existing parser
has no checkpoint inside its buffered line loop, so this is not a hard CPU kill,
memory sandbox or admission runner. The future shared runner must keep its slot
until the real task ends, even after cancellation/deadline. The future repository
must recheck its live budget/Owner/transaction before writing: possession of a
previously returned candidate never authorizes a late commit. No thread or loop
is implemented here.

Private dataclasses disable generated reprs, and errors contain only a fixed code
and optional bounded parser code/line/category. They never stringify raw JSON,
headers, IDs, sequences or callback exceptions. Ordinary application code must
still avoid serializing private objects or logging traceback locals.

Verification is synthetic only: the new model suite and the unchanged original
11 parser plus 6 independent tests. A separate JSONL oracle, prefix EOF, late
iterator/cancel/CPU failures, source/config/version/summary corruption, duplicate
and invalid records, strict numeric/JSON and no-runtime-IO guards are included.
No real FASTA/TXT or R job is run. The existing golden 17,195,782-byte / 38,387-record
evidence is not a test of this new serialization; its new full fingerprint remains
pending. DB/RLS, HTTP/Auth/BFF, persistence, throughput, Provider and scientific
results remain untested and unimplemented. All live activation stays off.
