# Internal FASTA source read repository — source only

`FastaSourceRepository(pool=None).load_owned_source(actor, project_id, source_id,
budget)` is a default-disabled server-internal read seam. No pool is created,
no credential or resource is discovered, and no connection is attempted by
import/construction/default-disabled calls. This module has no HTTP, upload,
enumeration, download, parse, version, Provider, quota, worker or SQL migration.
It does not change existing TXT behavior or public app registration.

## Trust and exact query

The caller must first use the real existing Authority and pass its verified
subject as `actor` in trusted server context. A canonical UUID string is only
structural input validation, **not JWT authentication or proof of a human**.
There is no client Owner/header/body override API. The actual pool must have the
separately reviewed runtime role/endpoint/configuration; a Python pool object
does not attest database privileges or RLS.

One fixed parameterized SELECT joins `fasta_sources` and `projects`, requiring
both `p.owner_id=actor` and exact project/source IDs, including matching source
project. It uses `LIMIT 2` and two bounded fetches; two rows are corruption, not
an arbitrary first-match result. It executes only a READ COMMITTED READ ONLY
transaction, transaction-local app.user_id and adaptive statement/lock timeouts,
the source SELECT, and transaction end. There is no client SQL/path/URL. Missing
source, missing project and foreign Owner/project all yield the same fixed
`404 fasta_source_not_found`; the repository never probes another scope first.

## Decode and private result

The result is a ten-column tuple/list matching the reviewed 008 source table.
IDs must be exact DB UUID values; integer bytes cannot be bool/float; timestamps
must be aware DB datetime values, normalized to exact UTC six-digit-Z form.
Filename/format/status/SHA are strict strings. A private immutable bytes body or
one-dimensional contiguous byte memoryview is accepted; memoryview copies are
bounded to 25 MiB and detached. Mutable bytearray/unknown adapters are refused.

The exact nine-key metadata JSON and body are passed through the public reviewed
`fasta_intake_contract.source_from_json`, including full length/SHA and metadata
validation. Expected source/project are independently checked against the row.
The returned `StoredSource` has a safe repr and immutable bytes. No sequence or
ID parsing occurs. This result is private server data; future client metadata
DTO construction must explicitly omit `content`. Possession of this object
does not confer a durable index version, Owner authority or Provider permission.

## Deadline, unknown and teardown policy

The supplied existing Budget is used lazily, with a check before acquisition,
after scheduling, around SQL/fetch/decode and before return. Expiry before work
does not acquire a connection. Fixed per-operation caps do not extend the outer
deadline. There is one acquisition/query attempt, no automatic cache/retry.

Each acquired connection is synchronously closed at most once and released at
most once **only after closed=True is confirmed**, including successful reads.
This removes a poisoned prior session identity rather than returning an open
IDLE socket to the shared pool. A non-IDLE connection initially returned by the
pool is closed without issuing SQL on its previous transaction. Rollback is
attempted only for this method's begun transaction while live and neither
abandoned nor COMMIT-dispatched. Abort callbacks never propagate driver finish
errors, allowing Budget to cancel and consume late operation exceptions.

A lost/read-only COMMIT response or expired operation yields only
`504 fasta_intake_timeout`, never a source result; it is not mislabeled upload
or index write uncertainty. COMMIT uncertainty is preserved if close also fails.
An unconfirmed close/failed release gives no successful source and latches this
repository unavailable; an unclosed connection is not released. Primary
non-timeout failures become unavailable if closure cannot be confirmed. Cancel
propagates as cancellation without private exception arguments. No extra SQL is
sent after abandonment or COMMIT uncertainty.

An abandoned acquisition also latches the instance unavailable. Its late result
is consumed and closed/released using the original captured pool, even if a
trusted caller later changes the public pool attribute. There is no automatic
unquarantine or second acquisition. An operator must resolve unknown ownership
before replacing a quarantined long-lived instance; creating one per request
to bypass this state is not an approved lifecycle. A closed client socket is
not proof that a remote backend process terminated; actual pool/PG statement
bounds remain a separate live gate.

Fixed errors are only unauthenticated/invalid_fasta_request, source_not_found,
source_integrity_failed, intake_unavailable and intake_timeout. Raw driver,
cleanup, metadata/sequence/credential exceptions and RawQC-specific codes are
not returned or logged.

## Evidence and remaining gates

Thirteen targeted isolated-async methods pass on SQL-shaped in-memory objects:
disabled/expired zero acquisition, fixed Owner query, absent/foreign uniformity,
invalid actor/scope, typed metadata/SHA/length and model validation, bounded
memoryview/timezone, extra rows/bad transaction, sanitized SQL error, COMMIT plus
close uncertainty, close/release latch, late acquisition original-pool cleanup,
timeout with finish failure and consumed private late exception, cancellation.
The test-only psycopg module is a NeverConnect/transaction-status stub, not a
driver/role/RLS proof. No production pool is constructed; no DB/R/SSH/credentials,
original file or real login is used. Synthetic data exists only in test memory.

The 008 DDL/RLS has not been applied or validated by this task. Exact PostgreSQL
driver row types, current role/endpoint and actual Owner RLS, connection lifecycle
and metadata-to-client gates remain pending independent QA and specifically
authorized live checks. Source tests do not make the repository operational or
register an API. Root alone may review/promote these source candidates. STOP.

## Narrow R1: FSREAD-01 sticky close uncertainty

The original source delivery remains BLOCK evidence. R1 changes only the local
close_once closure: any BaseException from either closed getter or driver
finish is recorded as sticky uncertainty for that exact connection. The
repository quarantines, withholds release and returns no private source. A
later closed=True cannot erase an earlier exception, trigger another close or
release, or permit another acquisition. Timeout/read-only COMMIT uncertainty
still retains its 504 classification, and cancellation remains sanitized.
SQL, Owner scope, schema, API, shared Budget and Pool are unchanged.

Original thirteen method bodies and assertions are preserved. The independent
five methods, including the failing FSREAD-01 case, now pass without changing
their assertions. Three necessary additions cover first/verification getter
errors followed by true, finish RuntimeError/KeyboardInterrupt/SystemExit after
true, and COMMIT unknown combined with a finish-after-true error. These are
twenty-one pure fake methods, not actual DB/driver/RLS/HTTP evidence. Independent
review of this new revision is still required; the old BLOCK is not relabeled.


## Root integration review: 2026-11-18

Independent review accepted the FSREAD-01 fix with a retained test timing caveat.
The original failing finish-after-closed counterexample and the added getter,
BaseException and COMMIT-unknown combinations now reject private results and
quarantine without release. Only the close-once uncertainty logic changed.

The independent full run retained 20 passes and one failure in an unchanged
10 ms late-acquire test with six event-loop yields. Exactly one unchanged
isolated replay passed. This does not establish the initial scheduling cause,
does not constitute a single clean 21-test run, and is not counted as a new
case. The original source BLOCK and all initial logs remain preserved. Source
integration is a limited acceptance of the narrow lifecycle repair; actual
PostgreSQL/DDL/RLS/Owner/API/driver validation remains pending and no endpoint
is enabled by this module. Tests use an explicit isolated NeverConnect stub;
run them in the approved offline test process, not against live services.

Independent receipt SHA256:
`7a7e9f579fe4d0d350bc0bdf358704e8864c759b2b463a4e02a242d49856aa54`.
