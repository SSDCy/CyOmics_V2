# FASTA identity index — pure source candidate

`fasta_index.parse_fasta(bytes_or_byte_chunks, limits=Limits())` produces a complete immutable in-memory index. It accepts no path, URL, Owner, HTTP, storage or engine parameter and performs no internal filesystem/network writes or reads. Importing it creates no runtime object with I/O. Its sole identity dependency is the existing `sequence.vendor_domain_r1.normalize_sequence` and `sequence_identity_review_v0.1`, not an independently invented residue normalization.

This is a separately versioned `fasta_ascii_identity_index_v0.1` candidate. It does not replace or claim compatibility with every existing frontend input case. Parse success is not permission to access a project, evidence of annotation, a taxonomy/accession mapping, or BLAST/Pfam execution. Actual storage of original file bytes, DatasetVersion/Owner isolation, API, worker and persistent indexes are future work.

The result preserves exact source byte count/SHA256, record order and ordinal, original header text after `>` without trimming, exact first whitespace-delimited case-sensitive ID, original sequence lines, their untrimmed concatenation and its SHA256. LF/CRLF wrapping is explicitly decoded, while whole-file SHA retains every original header/newline byte. Wholly blank sequence lines are retained in `sequence_lines`; they contribute no residues. Source raw bytes themselves must be kept by future storage if exact whole-file reconstruction is required.

Canonical identity uses the existing vendor function: uppercase only after validating nonempty ASCII letters in `ACDEFGHIKLMNPQRSTVWYBJOUXZ`; ambiguity letters remain themselves. It never strips whitespace, stops, gaps, punctuation or substitutes residues. An invalid or empty assembled sequence remains in its original ordinal with raw sequence/hash, `canonical_sequence=None`, `sequence_sha256=None`, status `invalid_no_canonical_sequence_identity` and explicit `canonical_issue` (`empty_sequence`, `sequence_whitespace`, `stop_or_gap`, `illegal_residue`). This is an identity-format classification, not a scientific judgment about the protein.

Duplicate IDs are preserved as all ordinal tuples in a read-only mapping. `records_for_id` returns all records, never selects the first or silently deduplicates. Equal sequence hashes are not deduplicated either. Protein group member splitting/association is not included in the product; the separate read-only golden check preserves each semicolon member and only compares presence/ambiguity metadata.

## Explicit compatibility boundaries

| Case | Existing frontend | Existing vendor audit | This candidate |
| --- | --- | --- | --- |
| Duplicate first-token ID | Rejects | Retains/counts | Retains every ordinal; ambiguous lookup returns all |
| Sequence whitespace | Ignored for preview length | Retained; invalid canonical identity | Retained; invalid canonical identity |
| Stop/gap/other characters | No alphabet validation | Raw retained, canonical absent | Raw retained, canonical absent with issue |
| Lowercase legal residues | Length only | Explicit uppercase canonical | Same vendor canonical, original retained |
| UTF-8 BOM/non-ASCII | Decoder/front parser may accept | ASCII decoder refuses | UTF-8 checked, then explicit unsupported BOM/non-ASCII |
| Newlines | LF/CRLF/lone CR | LF split, strips line-final CR | LF/CRLF; lone CR explicitly unsupported |
| Empty sequence | Rejects | Retains invalid identity | Retains invalid identity |

`FastaIndexError.code` and `.line` are content-free machine-readable details. `.category='unsupported'` marks `UTF8_BOM`, `NON_ASCII_FASTA`, `LONE_CR`; malformed UTF-8 is `INVALID_UTF8`. Structure/limit codes distinguish no records, sequence before header, empty ID and individual budgets. Non-ASCII defline support and coordinated frontend duplicate policy require a later explicit version decision. No fallback, stripping or file rewrite happens to make input pass.

The independent lone-CR refusal is intentionally narrower than some vendor edge cases; do not label all inputs vendor-equivalent merely because ASCII canonical residue rules match. The authorized golden file is ASCII LF, so these unsupported cases do not change its identities.

## Bounds and failure

Hard ceilings: 25MiB source, 50,000 records, 256 characters per ID, 2,000,000 total ID characters, 1,000,000 characters per line and assembled sequence, 500,000 physical lines and input chunks. An exact `Limits` instance may lower positive integer ceilings, never raise them. Empty chunks count toward the chunk cap. The line/chunk caps additionally bound structural overhead rather than assuming byte limits alone bound object count.

This in-memory implementation buffers decoded source and record strings; peak memory exceeds 25MiB and is bounded by the documented byte/line/record ceilings. It is not an out-of-core or hard wall-time sandbox. Caller-supplied iterators may themselves block or raise; their external work/exception handling is the caller's responsibility. No provisional record or callback is published before complete success. Original content is omitted from dataclass reprs; consumers must still avoid logging private fields.

## Verification scope

Eleven synthetic tests cover chunk/newline boundaries, exact raw/canonical identities, duplicates, illegal/empty sequences, unsupported encoding cases, malformed structure, wrong input types and each budget. Initial test-only threshold error (5-byte input incorrectly expected to exceed a 5-byte cap) is preserved in `initial-*`; corrected threshold 4 leaves product behavior unchanged.

Read-only golden validation used the two expressly authorized local files: FASTA source pin `8cb4a7994a80b6fecef0310e288c4763261026989b73a4e467d3310aad5daba5`, 17,195,782 bytes, 38,387 records/unique IDs, zero invalid canonical records, 16,367,314 assembled sequence characters. Five deterministic existing-vendor audit records match ordinal, ID, raw/canonical hash and length; aggregate records, invalid/duplicate counts and total characters also match. This is not a new independent scientific annotation oracle.

The old TXT has 2,252 groups; exact member presence gives 2,223 fully matched, zero partially matched and 29 unmatched, also confirmed by running the unchanged existing frontend modules. These expected numbers live only in review/check scripts, never product code. Original file hashes are unchanged before/after. No actual FASTA sequence, original data file or ID list is persisted in the delivery or proposed public Git payload; only counts/hashes and synthetic fixtures are retained.

Candidate/source tests have not yet undergone another agent's independent QA. No shared repository, UI, server, database, R, remote service or deployment changed.
