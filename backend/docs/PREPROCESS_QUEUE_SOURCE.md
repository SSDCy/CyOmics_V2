# Preprocessing queue: Owner enqueue/read source candidate

Default-off and **queued-only**. This four-product slice contains the repository,
one SQL draft, synthetic FakePool/static tests and this contract. No SQL was
executed, table created, profile registered, worker launched, HTTP route mounted,
processed version saved or scientific calculation performed. Existing RawQC
jobs/results, TXT interfaces and migrations are not modified.

## Invocation and trust boundary

```
repo = PreprocessRepository(pool=None, execution_json=None, enabled=False)
await repo.enqueue(subject, project_id, dataset_version_id, raw_body, budget)
await repo.read_job(subject, project_id, job_id, budget)
await repo.read_request(subject, project_id, dataset_version_id, request_id, budget)
```

This is an internal repository, not JWT authentication. Only a future handler
that has actually called the existing Authority.verify may supply subject and
route IDs. Never decode these arguments or pool/execution from the client body.
The only request body is the existing `preprocess_contract`'s exact three keys:
request_id, quant_family and parameters. Its strict pure parser runs before any
transaction/driver creation. Invalid JSON/server-only extra fields are rejected
without a connection. Parameters have 12 exact keys and only three named strict
booleans vary. No client path/Owner/source/execution field is accepted.

None/disabled/missing pool or missing enqueue execution configuration refuses
before driver/transaction I/O. An explicitly enabled read repository may omit
current execution configuration to read already stored queued jobs; it still
requires a pool and verified subject. Expired budget is checked before pool
acquisition. No environment activation, credential loader or pool constructor is
called here. Trust in a supplied pool's actual role/grants remains an integration
gate, not something a Python object can prove.

## Same-transaction Owner/source binding and idempotency

The transaction is the already reviewed RawQC `Transactions` implementation,
loaded lazily and reused without edits. A local adapter only makes unknown
disposition lock this repository. It parametrically sets LOCAL app.user_id,
statement_timeout and lock_timeout, then performs fixed parameterized SQL. Reads
are explicit READ ONLY transactions; writes use READ COMMITTED.

Owner/project authorization is checked before queue/source lookup. Missing and
foreign projects both return dataset_not_found, without a queue/source query.
The current dataset's project/file relationship and raw metadata are read in the
same transaction. The existing `import_persistence.SourceRef` is constructed from
server row metadata, and the existing complete `decode_version` validates its
raw-backed import report, configuration, provenance hashes and dimensions. No
raw content/bytea is fetched. The requested family must exist; enabled filtering
columns must exist. Full token validation and computation remain the future R
runner's job. No biological groups are inferred.

The immutable source JSON is an 11-key execution metadata snapshot derived from
that existing SourceRef/report, including project/dataset/file IDs, source
bytes/SHA, parser/config/report hashes, dimensions and the SHA of the original
ordered family descriptor. This is not a new authorization DTO. Every read and
replay rechecks current authoritative source metadata against that snapshot.

The durable idempotency scope is **UNIQUE(project_id, request_id)**. Project has
one strong Owner binding; a separate owner's project is a different scope. The
scoped request digest contains project, dataset and the entire canonical
three-key request. Therefore reusing the same request ID in a project with a
different dataset, family or any flag returns 409 preprocessing_request_conflict.
An advisory transaction lock keyed by project/47008 serializes lookup and insert.
An existing request's drift is checked before resolving the newly requested
dataset/family, so an unavailable replacement cannot evade the conflict rule.

An identical replay validates the current original source and returns its old
execution binding, not the current deployment's execution pins. It does not
insert or report created=True again. There is no worker notification or launch
permit in this API. No arbitrary new quota is introduced; capacity/admission
policy is a separate future decision.

## Execution proposal and queued DTO

execution_json is canonical UTF-8 bytes supplied only at trusted server startup.
Its exact 10-key shape is contract, entry_kind, executable, limits,
process_policy_sha256, r_version, registered, runner, script and validator. It is
explicitly `log2_local_source_proposal_v0.1`, native_R/R4.6.1 and registered=false.
Script 11996/08aec…, validator 14877/ba5fa… and native 14488/9c572… are fixed. The
runner is a bounded bytes/SHA server snapshot; no supplied filesystem path or
engine is stored. Limits match the existing fixed 45 CPU/60 wall/2 GiB policy and
its reviewed process-policy SHA, but this metadata does **not** register a log2
execution profile, authorize execution or prove the pinned runner actually ran.

The binding digest includes the scoped request hash, source, family, full
parameters and that immutable proposal. Persisted rows must be canonical, exact
schema and correctly bound before COMMIT and before read return. Stored source,
parameters, execution or binding corruption fails closed.

Public queued DTO fields are schemaVersion, job_id, request_id,
request_sha256, binding_sha256, source, quant_family, parameters,
parameters_sha256, execution_sha256, status and created_at, plus fixed result=null,
computation_performed=false, worker_started=false and execution_registered=false.
created_at is aware UTC with six fractional digits. It contains no source body,
sequence, arbitrary error text, execution path or artifact URL. There is no
RawQC seven-artifact result format reused for log2's eleven artifacts.

Enqueue returns frozen `QueueObservation` with a detached `job_public()` decoder,
created, commit_confirmed and teardown_confirmed. The observation is only a
server-local point-in-time transaction receipt, not Auth or a future launch grant.
It is constructed only after COMMIT and the shared transaction's finally have
confirmed teardown. The source slice does not decide future status transitions,
eleven-artifact success/error schema, worker claim/recovery, processed-version
lineage, downloader or publication. Non-queued persisted status is rejected;
those future contracts need separate review and migration.

## Unknown disposition and resource boundary

The reused transaction cleanup closes before pool release, never returns an
open or uncertain socket, consumes late acquisitions, and makes closed-getter or
finish exceptions sticky. A later closed=True cannot erase a previous exception.
COMMIT dispatched without a confirmed result is submission_unknown; a successful
rollback cannot prove that COMMIT did not happen. A commit/close/release unknown
does not return a successful observation/DTO, and irreversibly locks this
repository instance. Subsequent operations refuse without new SQL. There is no
automatic retry, reconcile, reconnect or repository replacement. A higher-level
explicit recovery/read policy is intentionally not implemented here.

New error codes are fixed preprocessing_* codes, plus existing unauthenticated
and dataset_not_found. RawQC transaction error codes and original private driver
exception text are mapped, not returned. These rules do not prove PostgreSQL
permissions, actual cancellation or network socket closure from FakePool tests.

## SQL draft and independent validation still required

`008_private_preprocessing_jobs.sql` targets only the fixed PostgreSQL17
cyomicsv2_business_dev/businessdev environment, requires the explicit review GUC,
existing safe app roles/parent FORCE RLS and 007, and refuses existing new objects.
**008 is a draft number also used by a separate old FASTA candidate. Root must
reconcile migration numbering before any execution; neither draft is applied by
this delivery.** The script creates only preprocessing_jobs and its fixed invoker
guard; it changes no parent tables, roles, passwords, HBA, Auth or service.

The new table has FORCE RLS with current app.user_id plus project/version/file
Owner joins, immutable queued-only rows and a unique scoped request key. Runtime
gets only explicit SELECT and INSERT columns; no status/timestamp INSERT grants,
UPDATE/DELETE/TRUNCATE, worker role/function or SECURITY DEFINER is added. The
guard rederives source/ordered-family SHA, exact canonical flags/preset/execution
and request/binding digests. The Python full report decoder remains required.
Foreign-key singles plus checked joins preserve composite consistency without
adding/changing parent UNIQUEs. Numeric JSON type/bounds casts use CASE guards.

Seventeen unique author methods pass using an in-memory SQL-shaped pool and
source/static checks. Covered: default-off/invalid-body/expired zero-connection,
Owner/foreign/missing lookup, complete queued DTO, same request replay,
dataset/family/flags drift, immutable old execution, source/report corruption,
missing flag, concurrent fake serialization, insert rollback, COMMIT unknown,
sticky getter/finish-after-closed, read teardown failure, row binding/status
corruption, client-supplied authority and SQL scope/grants shape. Final attempt002
is 17 PASS/0 errors/failures/skips; attempt001 also passed before the final narrow
lazy-import sanitization and SQL dimension type-gate hardening. These are not
34 tests. The known exact Windows stdlib asyncio selfpipe exception was reused;
only 15 standard-library loop selfpipe bind/connect pairs occurred. External
network/process/private-write guard hits are zero. No real DB/SQL/RLS/grant,
Authority/login, R, HTTP, SSH, service or user data was used.

Before application, independent QA must review source and then separately
authorize a synthetic BEGIN/ROLLBACK development test: wrong Owner/project/
dataset/file, source/config/report/descriptor drift, family/each flag mismatch,
duplicate request and same-UUID drift, duplicate JSON keys/bool-number aliases,
source dimensions, request/binding digest corruption, direct forbidden grants,
RLS/FORCE behavior, immutability and rollback absence/restored fingerprints.
No one of those real SQL checks is claimed completed here. Future actual runtime
and Owner E2E gates are also open. **STOP.**
