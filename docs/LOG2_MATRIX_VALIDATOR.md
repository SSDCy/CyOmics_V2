# LOG2-MATRIX-VALIDATOR-001 — pure eleven-artifact byte validator

One small source capability: `log2_matrix_validator.validate(original_bytes,
selected_descriptor_bytes, family, flags, outputs, *, check=server_check)`.
There is no default check callback. It is a trusted server budget/source-disposition
closure returning None or raising on failure; a false/true/integer return refuses.
It must never originate from HTTP/client configuration. This function does not
authenticate that closure or actor. Tests use synthetic void callbacks only.

Inputs are actual immutable bytes; no filenames, paths, commands, process,
network, R, DB, credential, cache, registration, Owner API or publish operation is
present. `family` is exactly Intensity or LFQ intensity. `flags` is an exact
three-bool tuple in Reverse/contaminant/site-only order. Original bytes and all
eleven actual artifact byte references are detached from later caller-dict
replacement. Mutable bytearray/memoryview and additional/missing artifacts refuse.
No artifact or original bytes are changed.

## Exact validation

The new R source's existing strict contract is retained: unsigned nonnegative
decimal/scientific raw tokens, exact blanks, optional plus, finite conversion,
nonzero-mantissa underflow refusal, no trimming/deduplication/group guessing.
Every selected source cell is validated, including flag-excluded rows. Present
flag columns contain only empty or + even if disabled; enabled missing flags
refuse. All source records/IDs/order, complete rowmap/work ordinals, selected
family headers/descriptors, overlapping filter reasons and31summary fields are
checked against the actual source. Duplicate IDs and all-NA retained rows stay.

Original UTF8 may have one leading BOM and LF/CRLF endings, including no final
EOL. Lone CR, invalid UTF8, NUL, ragged/blank rows and duplicate headers refuse;
support is deliberately not expanded beyond Root's agreed narrow dialect.
Original SHA covers the original bytes, including any BOM/CRLF; normalization
for parsing is not a rewrite. Producer TSV artifacts are strict UTF8/LF/no BOM,
with terminal LF, exact headers/rows/full EOF and normal TSV quoting. Literal raw
values/IDs are compared as strings, not numerically normalized. The parameters
JSON must equal the exact canonical preset bytes for all three supplied flags,
with no trailing LF or extra key. Existing quant-diff's seven-subset/four masks
are unchanged; this private validator accepts exactly the full eleven-output set.

Per retained cell: blank and original zero produce working literal NA with their
independent masks; working_NA is their OR and imputed is always FALSE. Positive
raw values require finite signed derived values. Raw1→derived0 and raw0.5→-1
remain valid; no second zero→NA transformation occurs. Exact binary powers of
two require the exact integral exponent. Other values are independently compared
with Python math.log2 using relative tolerance1e-12 and absolute tolerance0,
as explicitly agreed by Root. This is a validator reference, not a replacement
R producer or estimator. No absolute floor swallows a near1 input's small nonzero
result. Subnormal positive finite inputs are supported; nonzero raw underflow
and derived underflow-token tricks refuse. These numerical rules have pure
literal/tolerance tests, not an actual cross-runtime arbitrary-number R proof.

All-filtered input raises `empty_not_analyzable` and returns no complete bundle,
including when R's legitimate exit3 contains only its five audit files. It does
not fabricate the missing seven files or publish an empty version. Invalid
nonempty source/artifacts likewise return no bundle. Later publication of an
empty audit disposition would require a separate contract.

Existing documented bounds: original1..25MiB; selected1..128KiB; source1..10000rows,
2..512columns/2,000,000cells; field65536characters; each artifact1..25MiB; complete
eleven-artifact total128MiB. These are validation caps, not OS memory/time limits.
Checks run while source/rows/hashes are processed and immediately before return.
There is no hard thread/process kill guarantee. An explicit final check failure
returns a fixed interrupted error without a partial bundle or hidden retry.

R-session text must be UTF8/LF/no BOM, start with exact4.6.1 version boundary,
contain exactly the seven default base package names and at most compiler as a
separately loaded namespace. This checks text shape only: arbitrary coherent
synthetic text can pass. It is not proof of an R process, binary, version execution,
resource closure or trusted provenance. There is no caller SHA-echo parameter.

## Packed result and trust boundary

On complete consistency success, a frozen `ValidatedLog2Bundle` contains counts,
the actual original/descriptor/parameter SHA, sorted immutable Artifact records
(actual bytes/length/SHA/media type), canonical sorted manifest bytes and its
actual SHA. Raw bodies are excluded from repr. It always keeps:
tool_execution_attestedFalse, owner_authorizedFalse, publication_performedFalse,
analysis_readyFalse. A constructed Python type/boolean is not an Auth capability.

The bundle is internal validation/packaging, not the old raw-QC seven-file result,
an execution profile or a persisted job/processed-version DTO. An independent
future trusted publisher must still bind immutable source/project/Owner/version,
family, confirmed raw-QC job, real approved tool/source/session/exit/closure
evidence and actual artifact hashes. It must compare this actual-source SHA to
that trusted binding. Caller-provided matching declarations or successful math
alone do not prove the right source or that R produced it. Internal s1..N/g1/g2
mapping and confirmed experimental design remain outside this function.

Errors carry only fixed validation codes, never raw tokens/IDs/paths or original
exception strings. There are no logs, file writes, network or DB calls. No
partial bundle, success DB record, profile registration or publication is made.

## Source evidence

Fifteen pure tests include independently prepared literal Fraction/powers-of-two
fixtures and a separate all-flags-disabled two-row hand case. Counterexamples
alter raw tokens, rowmap order/duplicate IDs, descriptors, each mask, summary,
parameters, file set/EOF, session, capacities and mutable data. Additional checks
cover nonpower tolerance inside/outside bounds, near1 not collapsing to0,
exact powers/minimum positive subnormal, invalid values in excluded rows,
BOM/CRLF actual-source SHA, final budget failure and frozen/repr-safe packing.
Synthetic output/session files are deliberately fabricated for validation tests;
they are not native R goldens or execution evidence. No R/SSH/DB/API/user-file,
service/Git/state or current111 preview action occurred. Original source/core
and previous fixtures/QA remain unchanged. STOP for independent source QA.

## R1: strict session package sections

Independent QA demonstrated that the old session parser accepted the existing
real R session after insertion of `other attached packages: limma_3.68.5`. The
old candidate and exact failing probe/observation remain sealed unchanged.
R1 modifies only `_session`: it rejects other attached-package headings, checks
base-package vector entries with exact indices/tokens, and consumes the complete
optional namespace vector. Only zero or one fully recognized compiler_VERSION
token is allowed; unknown text, duplicate packages, malformed indices/headers or
partially matching version tokens cannot be skipped. No other numerical, mask,
parameter, eleven-artifact or publication contract changes.

Original15 test methods remain unchanged and four session regressions were
added. Both already collected actual Intensity/LFQ eleven-file sets pass with
their original manifest SHA unchanged; the exact independent inserted-limma
counterexample and unknown namespace tails now refuse. This is local readback
validation, not another R/SSH run. Legitimate absent/empty namespace cases still
pass the stated at-most-compiler contract. Session text remains non-attesting:
tool_execution_attested and publication stay false. Stop for independent R1 QA.
