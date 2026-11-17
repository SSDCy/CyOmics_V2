# Private sample design workflow

Sample design is attached to an authorized project, an immutable DatasetVersion,
and one quantitative family: `Intensity` or `LFQ intensity`. These are alternative
quantifications of sample columns, not automatically twice as many biological samples.

The private project page restores the existing import report before loading the
sample design. Users enter group, treatment, control status, replicate identifier,
and replicate type against the original zero-based column ordinal and raw names.
No experiment groups or biological replicates are inferred from column names.
Incomplete information may be saved as an unconfirmed draft. Confirmation is a
separate action; saving does not confirm scientific readiness or change raw values.

## Persistence and isolation

- `GET/POST /api/v1/datasets/{dataset_uuid}/sample-designs` require the normal
  verified private session and server-side project ownership checks.
- Query parameters select `project_id` and `quant_family=intensity|lfq`.
- POST uses a request UUID and expected head UUID (or null for the first version).
  Writes append a version; a stale head produces a conflict rather than overwriting.
- An uncertain write must be checked by its request UUID. The client never retries
  POST automatically, and a historical request result is not treated as the latest head.
- PostgreSQL migration 006 creates a dedicated table with forced row-level security.
  Migration execution belongs to the deployment process, not application startup.
- API and BFF feature factories default to disabled. A reviewed private host must
  explicitly enable the feature and supply its fixed backend transport. Browser
  inputs cannot select another backend, filesystem path, user, or trusted source.

An explicitly enabled private host may forward a cache-miss GET through its
fixed backend transport to obtain a strictly validated error, including an
ownership denial. Without the verified import source, a backend 200 is destroyed
before reading its body and becomes `sample_design_unavailable`; it cannot fill
the source cache or expose private data. POST still requires the verified source.
The standalone bridge's denial-only option defaults to false. This source change
does not by itself prove a real cross-account request returned 404.

## Verification boundaries

Portable offline behavior tests are documented in
`frontend/tests/README.sample-design-offline.md`. They use synthetic fixtures and
deny live I/O. Passing them does not establish actual authentication, database,
browser reload, concurrency, cross-owner, or human acceptance.

Quantitative preprocessing and differential analysis remain separate milestones.
They require validated sample/contrast design and traceable input and parameter
versions. Scientific calculations and figures must use established R packages or
official R functions; application code must not invent replacement statistical or
plotting algorithms. FASTA-based domain and structure modules may progress
independently while preserving protein identifiers and sequence hashes.
