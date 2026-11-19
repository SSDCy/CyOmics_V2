S1-NORMALIZATION-MEDIAN-CORE-001

One source-only scientific increment. The plan names Median but does not fix its
formula. This core explicitly centers observed log2 values by the median of the
per-sample medians, with equal sample weights. It does not normalize raw values,
apply another log2, fill NA, remove all-NA rows, or alter any original raw/QC data.
stats::median defines odd/even medians; factors are additive log2 offsets.

Use median_center_log2(working, masks, provenance, method="median") after a trusted
future coordinator has loaded and validated the complete saved parent and all
artifact bytes. working follows the existing full working-matrix.tsv schema;
the original two prefix columns, full sample names/order, duplicate protein IDs,
four mask tables and complete supplied parent provenance are retained. The core
does not attest byte hashes, ownership, persistence, execution or publication.
It does not register a replacement for the existing None-only Python validator.

Median requires >=1 observed finite value in every sample. A single observation
is supported with a warning. An all-NA sample fails the complete Median operation;
None preserves it unchanged. Existing None returns the exact input working table
and masks, including numeric types. Any imputed input is presently unsupported.
No sample or row subset is silently used. Output parameters, factors and all
counts are returned alongside the complete matrix; no preview truncation exists.

The one new target actually ran on B using R 4.5.3 --vanilla. It pins all 11
existing synthetic saved-log2 LFQ artifacts, decodes the full working matrix and
four masks, cross-checks parameters, preserves the other parent pins, checks literal
expected numeric results for the entire matrix, checks an unequal-count reference
case and failure boundaries, and records actual input/output byte pins. The target
emits experimental evidence files, not an admitted production artifact allowlist.
Four startup locale warnings remain in original stderr. A's controlled R 4.6.1
runner and any future execution/persistence profile have not been validated.

Finite target command (new evidence destination must not exist):
python backend/tests/normalization_median_target.py --frozen-backend <frozen>/backend
  --rscript D:/R-4.5.3/bin/Rscript.exe --evidence <new-private-evidence-directory>

Read contract.json for exact input/output conditions. No HTTP, UI, runtime runner,
SQL, Total/Quantile, plotting, batch correction, imputation, differential analysis,
mapping or broader QC framework is included. Independent QA and Root own review.
