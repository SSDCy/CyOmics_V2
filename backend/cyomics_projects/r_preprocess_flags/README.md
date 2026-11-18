# Three-flag preprocessing: isolated source candidate

**Not registered and not connected to the website.** This independent R
script implements only explicit Reverse/contaminant/site-only row filtering with
zero-preserving identity working values. It does not change the original raw-QC
script, seven-output allowlist, runner, process-policy v3, API or shared source.
No R syntax parse or R execution occurred while authoring this candidate.

## Inputs and exact behavior

`contract.json` fixes eleven positional arguments: original TXT, selected-column
TSV, family, three explicit lowercase `true|false` flags, then exactly
`preserve none none none` and an absent output directory. A future trusted
worker must map admitted server parameters to this argv; this is not a public
path/command interface. Extra/missing arguments, other methods or boolean
spellings refuse. Nothing infers groups or replicate types from sample names.

The selected TSV has exactly `column_ordinal/name_raw/quant_family/sample_label_raw`
columns. Canonical zero-based ordinals and exact text must describe all and only
the original columns of one family, in original source order. Family is exactly
Intensity or LFQ intensity. The fixture expectations cover Intensity; support
for LFQ remains source-only. A separate all-enabled Intensity synthetic unit was
actually executed; see the validation record below.

The full source must be complete bounded UTF8 TSV with the same quote-disabled,
no-trim/no-fill/no-NA-token-coercion input semantics as raw QC. A complete byte
snapshot is compared again after reading/parsing and before/after output. This
detects byte changes but supplies no SHA/FD/Owner or immutable-filesystem proof.
Header widths/dimensions are checked before table expansion. The shape limits
match the dataset source bounds; selected metadata has a proposed 128 KiB input
bound. These are parser gates, not a newly registered CPU/memory/output profile.

Enabled flag columns must exist. Every present flag column, including a disabled
one, must contain only exact empty string or `+`; ` +`, `TRUE`, `1`, `NA`, etc.
are whole-input errors. Disabled missing columns are allowed and contribute no
cause. These rules are intentional and separately testable, not silent skips.
All numeric selected cells, including rows later excluded, must be exact blank
or a valid finite nonnegative token; literal `NA` is invalid, and overflow/
underflow refuse. No invalid source is made acceptable by filtering its row.

All enabled reasons are logged in fixed order. Their counts can overlap;
excluded rows are the union. Original 1-based row ordinals and Protein IDs group
text remain intact, including duplicate IDs and multi-member groups. No dedup,
splitting, unique-peptide threshold, historical 345 preset or hidden row rule is
implemented. All-present raw flags remain available from the immutable source.

## Separate raw tokens, numeric values and mask scopes

For retained rows, `raw-retained.tsv` preserves original selected strings,
including empty and zero spellings. `working-matrix.tsv` separately represents
finite numeric values with `sprintf("%.17g", ...)`; blank becomes the protocol
NA token, while numeric zero stays numeric zero. Thus raw `+0.0e0` is retained
in the raw table and corresponds to numeric 0 in the working table. Original
source/QC records and all-row masks are not updated.

Each of four masks has exactly the retained source-row prefixes, selected-column
order and dimensions of both matrix tables: original blank, original numeric
zero, working NA and imputed. Working NA equals original blank only in this
identity slice; imputed is entirely FALSE. No mask conflates blank with zero.
`source_*` summary fields count the entire selected family before filtering;
`retained_*`, working_NA and imputed count only retained cells. Row mapping and
filter logs cover all source rows. Removed rows have no work ordinal; retained
work ordinals are contiguous, while source row ordinals are never renumbered.

## Completion, empty and failure are distinct

Nonempty output has exactly ten files: raw-retained, working-matrix, four masks,
filter-log, row-map, summary and R-session-info (see the machine contract for
names/schemas). Exit 0 and `PREPROCESS_FLAGS_COMPLETE` mean only completed
nonempty identity filtering. They do not mean a statistically analyzable design,
Owner-authorized publication, PCA/DE readiness or a scientific result.

All-excluded output has only the four audit files: filter-log, row-map, summary
and sessionInfo. Its explicit status is `empty_not_analyzable`, marker is
`PREPROCESS_FLAGS_EMPTY`, and exit is **3**, never success. It creates no empty
matrix or mask files. Exit 2 emits only a fixed whitelisted error code (or
`preprocess_unconfirmed`), never arbitrary condition/filename/input text. Invalid
input refuses before output-directory creation. A later output/flush/snapshot
failure can leave private partial files without success; there is no automatic
cleanup, deletion or retry. A future coordinator must refuse to publish them.

The output directory requests mode 0700. This R path interface is not a
no-follow/descriptor/ownership/TOCTOU sandbox or fsync/atomic durability layer.
Those properties, complete hashes and result persistence require a separate
reviewed trusted runner/publisher. Existing raw-QC v3 limits/allowlists cannot be
silently reused for these ten files. The new execution profile, script/validator
bundle, session/software versions and resource/cleanup evidence remain pending.
No FASTA CPU budget was inherited and no new effective process budget is claimed.

## Fixtures and validation status

Small literal synthetic fixtures and independently specified expected tables are
delivered outside the source candidate. The eight-row source has two Intensity
columns in deliberately unsorted name order, one unrelated LFQ column,
multi-member and duplicate IDs, two blanks, three numeric zeros, overlapping
flags and alternative numeric spellings. Expected all-enabled keeps source rows
1/6/8; all-disabled retains all eight; the all-excluded source has audit-only
expectations. Four negative fixtures cover invalid disabled-present flags,
enabled missing flags, nonempty NA numeric tokens and reordered descriptors.

Expected TSV tables compare exact parsed cells/headers/types, not byte equality
with R's quoting choices. They were generated from explicit literal cause,
ordinal, raw/numeric and mask specifications, not by running this R script.
The nonempty cases have nine expected tables; actual sessionInfo must be
separately verified against Root's future pinned R environment, not fabricated.
The source/fixture checks verify text structure and expectation consistency only.
They do not parse full R grammar, execute R, prove numerical behavior, bound
runtime resource use, verify real studies or mark user scientific acceptance.

Root must independently review the source and choose a fresh limited synthetic
R run before any registration. Even after that, real Owner/source/result SHA
binding, immutable version/params lineage, admission/cancel/COMMIT-unknown and
browser end-to-end acceptance remain separate work. No R/DB/SSH/network/real
input/server scan/package install or service action occurred here. STOP.


## Reviewed execution record: 2026-11-18

The statements above describe the original source-authoring deliverable. After
source review, a separate isolated R 4.6.1 unit parsed this exact script and ran
the eight-row all-enabled Intensity fixture. Both native children exited 0.
Independent review checked nine actual TSV tables against the literal expected
tables and an independent eight-row model: 251 cells including headers matched.
Ten outputs, dynamic R session information and both owned-process receipts were
read back and checked. Source rows 1, 6 and 8 were retained; overlapping reasons
did not double-count excluded rows. Original strings, zeros and blanks remained
distinct from working NA values; no values were imputed.

This is a limited synthetic execution result, not real-user-file validation,
LFQ/empty/negative/mixed-flag execution, persistent result publication, Owner
isolation, API integration or a registered preprocessing worker. Existing raw-QC
script pins and its seven-output contract remain unchanged. No job/API is
activated by importing these files. See docs/PREPROCESS_FLAGS_VALIDATION.md.
