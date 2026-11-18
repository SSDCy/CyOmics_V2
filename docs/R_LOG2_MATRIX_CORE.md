# Positive log2 matrix core — source candidate v0.1

This is an **unregistered, unexecuted** R source candidate for S1-012 and the
log2/normalization-None subset of S1-013. It does not close either requirement.
It does not change the preserved raw-QC matrix, old preprocessor, limma source,
factory, SQL, UI, API, public defaults, or any runtime registration. The new
producer is not a drop-in replacement for the old raw-QC seven-artifact producer.

The approved scientific operation is `base::log2` on finite, strictly positive
raw values. R's official base manual defines log2 as base-2 logarithm:
<https://stat.ethz.ch/R-manual/R-devel/library/base/html/Log.html>.
We exclude raw zero from the function call because log(0) is nonfinite.
No pseudocount, scaling, normalization, imputation, row/ID deduplication,
group inference, P value, plotting or `analysis_ready` inference is performed.
Validation/serialization helpers are IO and bookkeeping helpers, not replacement
scientific estimators.

## Exact CLI input contract (future trusted worker only)

Eleven positional arguments, all fixed by a future reviewed worker:

1. Original immutable proteinGroups TXT path.
2. Selected-column descriptor TSV path.
3. Exact family `Intensity` or `LFQ intensity`.
4. Explicit Reverse flag `true` / `false`.
5. Explicit Potential contaminant flag `true` / `false`.
6. Explicit Only identified by site flag `true` / `false`.
7. Literal `to_NA`.
8. Literal `positive_only`.
9. Literal `none` (normalization).
10. Literal `none` (imputation).
11. New, nonexistent output directory whose parent is already provisioned.

No paths, command, environment, Owner token or approved tool pins are accepted
from HTTP here. This standalone scientific source itself supplies no filesystem
sandbox, Owner authorization, job/parent/contrast binding, symlink-safe storage,
launch approval, resource enforcement or automatic registration. Its argv shape
alone is not authorization to execute it.

The selected descriptor columns are exactly `column_ordinal`, `name_raw`,
`quant_family`, `sample_label_raw`. Ordinals are original **zero-based source
column positions**. Every column in the selected family is required, in original
source order. The worker must supply descriptors bound to the trusted import
report; the script compares them to the original raw headers and family suffixes.
It does not select a contrast, rename samples, infer groups or infer repetitions.

The strict reader, byte snapshots, repeated raw-byte equality checks, flag
validation and complete row lineage are copied without semantic changes from
the separately reviewed `r_preprocess_flags/preprocess_flags.R`. It uses direct
file `count.fields` / `read.delim`, not a reconstructed textConnection. Original
cell text, including IDs and duplicate IDs, is never rewritten in the input.
The inherited input reader accepts a UTF-8 BOM and ordinary source line endings;
the generated artifacts are UTF-8, LF and no BOM. Quoting in the original raw
TXT is literal (`quote=""`), as in the existing reader. Output TSV uses standard
double-quoted escaping. This does not expand the legacy reader's accepted dialect.

Inherited reader bounds: original 1..25 MiB, descriptor 1..128 KiB,
1..10,000 source rows, 2..512 source columns and at most 2,000,000 source cells.
The future artifact validator must additionally enforce quant-diff's 65,536
character field cap, 25 MiB per artifact and 128 MiB total. This R source does
not assert those output-size bounds or a kernel sandbox; raw text/quote expansion
can enlarge generated output. A future validator must reject oversized outputs
before publication. An output directory created during a later IO failure is
unconfirmed and must never be consumed or silently cleaned/retried.

## Numeric and missingness policy

Only the exact empty string is an original blank. Literal `NA`, whitespace,
negative tokens (including `-0`), Inf, NaN, malformed numbers and exponent overflow
are invalid. The unsigned decimal/scientific regex allows an optional plus sign.
A nonzero mantissa underflowing to numeric zero is rejected. A genuine zero such
as `0`, `00`, `.0`, `0.0` or `0e-1000` remains an original zero. Every selected
source cell is validated before filtering, so an invalid token in a flag-excluded
row also rejects the whole input. No invalid numeric value becomes working NA.

On an invalid numeric input, the output directory has not yet been created and
no artifacts or raw-value diagnostic copies are written. Exit is 2 and stderr
is a fixed safe code plus source record number (one-based) and original column
ordinal (zero-based); the first error follows R's deterministic column-major
matrix order. Generic errors use -1/-1. Original file bytes/SHA must be preserved
by the future storage/worker receipt, not copied into error logs. No raw token,
ID, path, call or arbitrary R error message is returned.

For each retained cell:

| Original raw cell | Derived working cell | Original blank | Original zero | Working NA | Imputed |
| --- | --- | --- | --- | --- | --- |
| `1` | `0` | FALSE | FALSE | FALSE | FALSE |
| `0.5` | `-1` | FALSE | FALSE | FALSE | FALSE |
| `0` | `NA` | FALSE | TRUE | TRUE | FALSE |
| empty string | `NA` | TRUE | FALSE | TRUE | FALSE |

The derived matrix is allocated separately. The sole transformation call is
`working[positive] <- base::log2(numeric[positive])`; original raw tokens and masks
remain independent. Derived finite zero and negative values are legal log2
values and must not be zero-to-NA/log2 transformed a second time by limma.
Scientific formatting is `sprintf("%.17g")`, with missing literal `NA`.

## Output contract

Nonempty success: exit 0 and exact stdout `LOG2_MATRIX_COMPLETE\n`.
Eleven output files, generated with no automatic publication:

- Seven files of the **existing** sealed quant-diff input subset:
  `raw-retained.tsv`, `working-matrix.tsv`, `row-map.tsv`,
  `original-blank-mask.tsv`, `original-zero-mask.tsv`,
  `working-NA-mask.tsv`, `imputed-mask.tsv`.
- `filter-log.tsv`, `summary.tsv`, `R-session-info.txt`, `parameters.json`.

No fifth invalid mask and no modification to the existing seven-file allowlist.
The future quant-diff adapter must pass only those seven artifacts to its subset
consumer, after validating the complete new producer output independently.

Matrix headers are `record_number`, `protein_ids_raw`, then all selected family
headers verbatim. Record numbers are source row ordinals, not renumbered work
rows. Duplicate protein IDs remain separate. `row-map.tsv` covers **all** source
rows in source order with `source_record_number`, `protein_ids_raw`, `kept`,
`work_row_ordinal`; work ordinals are consecutive only for retained rows and
literal `NA` otherwise. Filter-log retains overlapping reasons in the existing
order reverse / potential_contaminant / site_only. No all-NA row is silently
removed; it remains retained and will be not-testable in later contract preflight.

The four masks are cellwise exactly `[blank, zero, blank OR zero, FALSE]`.
Summary is `complete_positive_log2` and `analysis_ready=FALSE`, with counts,
explicit flags, zero/blank `to_NA`, log2 `positive_only`, pseudocount 0 and both
normalization/imputation `none`. If every source row is filtered out, exit 3,
stdout `LOG2_MATRIX_EMPTY\n` and only filter-log, row-map, summary, session-info
and parameters are written; no seven-file nonempty matrix subset is published.

`parameters.json` is the exact sorted canonical JSON map used by the sealed
`quant-diff-contract-001` `_params`, without a trailing LF. It contains precisely
preset, filter_flags, steps, input/output scale, zero/blank policy, log2,
pseudocount, normalization, imputation and seed=null. The canonical steps are:
flags_filter → zero_and_blank_to_NA → log2_positive → normalization_none →
imputation_none. It does not include invented grouping or analysis-ready facts.

## Required provenance and next integration gates

This package's product manifest pins the **actual source bytes** and its fixture
parameter bytes by SHA-256. R base has no adopted SHA-256 implementation here;
the script neither invents one nor echoes unverified caller-provided hashes.
A future reviewed trusted Python publisher must hash immutable original bytes,
selected descriptors, canonical parameter bytes, approved R script/validator/
session/full-result manifest and all produced artifacts; bind source's eleven
fields, family, parent/processed versions and confirmed raw-QC job; and construct
the existing processed_manifest with those actual hashes. This source package
does not supply that validator/publisher or claim an approved execution
registration. Pins and session text alone do not prove that R ran.

Before using with limma, separately review and actually run a bounded native R
fixture, independently compare these literal cells and all output contracts,
verify real exit and complete owned-child closure, then add reviewed validator,
registration and Owner-authorized publication. This slice covers no Median,
Quantile or Total normalization, no imputation, and no biological design approval.

## Pure evidence and limits

`checks.py` runs eleven source/hand checks under a deny-write/process/network
audit hook. It compares strict reader/selection/lineage blocks to the baseline,
checks all eight literal flag combinations of parameter canonicalization,
independently checks power-of-two algebra (not a self-generated R golden), full
row/duplicate ID/mask conservation, a separate two-column LFQ selection, and eight
invalid fixtures. It also executes an AST-exact subset of the sealed quant-diff
artifact consumer on synthetic expected seven-file cells, including derived
0/-1 and all-NA rows. That helper test is **not** full version/Owner/source
preflight and does not prove R produced those files.

The literal expected Intensity working matrix retains source rows 1,2,7,8 and
has five finite and seven missing cells; the source partition is 3 blanks,
6 original zeros and 15 positives across 24 selected source cells. The LFQ
fixture has two columns and includes `0.25 → -2`. No actual R parse/execution,
real protein data, SSH, DB, network, API, UI, production deployment, Owner E2E
or independent QA has occurred in this source-authoring task.

The first author check run had a test-parser literal-token count typo (expected
11 versus actual 10 paste0 components). All product/scientific assertions passed;
the log is preserved in `initial-checks`. The corrected check expects exactly
seven literal fragments plus three explicit boolean arguments. The initial
authoring helper emitted a Python invalid-escape SyntaxWarning before source
generation; its placeholder was corrected, without changing generated R bytes.
Counts across reruns are not added. R syntax and execution remain pending.
