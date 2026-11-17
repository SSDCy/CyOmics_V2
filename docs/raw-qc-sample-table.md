# Sample QC table — verified R output, optional preview

An explicit click reads only the completed job's `sample-qc.tsv` through the same
authenticated artifact transport as downloads. The existing manifest, complete
bytes, headers, EOF, SHA-256, project/source/family/job and session checks apply.
Preview and download share one lifetime flight; unsettled hashing retains it.
No preview creates a Blob URL, download, new statistic, plot or automatic fetch.

The strict UTF-8 quoted TSV parser requires the ten R contract columns. It keeps
all values as strings and preserves row order, quotes, scientific notation, zero
and NA. Text is rendered safely. The view is limited to 1 MiB and 4096 rows;
oversized results are rejected in full with a full-download option, never
silently truncated. Folding, cancellation, account/source/family changes,
expiry, unmount and stale responses clear the table. A 409 still requires an
explicit task-state check. Feature flags remain off by default.

Fixtures contain two previously generated synthetic Tiny R outputs, compared
independently as 40 original cells. Browser captures use explicitly synthetic
sessions and data; source tests do not prove actual Owner/API/R integration or
human acceptance. The scientific R script and backend routes are unchanged.
