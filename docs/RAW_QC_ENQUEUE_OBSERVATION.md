# Server-only enqueue observation — default off

This candidate adds no launcher, route, callback, worker, loop, R computation,
DB provisioning or service activation. The current HTTP `enqueue` call still
returns exactly the existing public job DTO. HTTP202 still includes replays;
neither that status nor a legacy DTO is evidence for a new launch.

Trusted startup may explicitly construct:

```python
repo = RawQCRepository(existing_app_pool, approved_registration,
    enqueue_observation_enabled=True)
observation = await repo.enqueue_observed(authority_subject, project_id,
    dataset_version_id, original_body_bytes, existing_budget)
```

The opt-in is a strict server constructor bool, defaultFalse. No HTTP/JSON/env
field enables it. With the default it refuses before any connection acquisition.
This is not a new authorization system: the subject must still come from the
existing Authority. The ordinary Owner/RLS/source checks and parameterized SQL
are reused, not bypassed. No change to public job keys or wire status mapping.

`EnqueueObservation` is frozen with slots. Its fields are `_job_json:str`
(canonical original DTO, excluded from repr), `created:bool`,
`commit_confirmed:bool`, `teardown_confirmed:bool`. `job_public()` returns a fresh
strictly parsed ordinary DTO on every call. `may_notify_new` is true only when
all three bool fields areTrue. The created marker is local to that request's
actual INSERT branch; an advisory-lock replay returnsFalse, including concurrent
same-request submissions. It is never inferred from timestamps, revision or202.

The observed method awaits the existing transaction completely, including its
finally cleanup, before producing any observation. COMMIT must have returned,
the connection must have reachedIDLE, close must have returned without exception
and the reviewed pool's release must have returned without exception. A normal
release return is interpreted only under that fixed pool contract, not as proof
for arbitrary substituted pools. The candidate does not inspect pool internals
or undo a release which may have partially completed before an exception.

Close exceptions stay uncertain even if `closed` subsequently becomesTrue.
There is one close attempt and no release of a connection whose close threw.
Release exceptions are recorded once without retry. Commit uncertainty, observed
cancellation, or cleanup uncertainty permanently locks this repository instance's
observation interface. `enqueue_observation_locked` exposes only that bool; no
reset exists. Further observed calls refuse before acquiring another connection.
Close/late-acquisition callbacks consume errors, do not log private text, and can
only tighten the latch. Nothing automatically reconstructs a fresh instance to
wash it. Unknown results require independent operator disposition before any
future launcher/controller replacement.

When an observed write has an acknowledged commit but uncertain close/release,
it raises fixed `504/raw_qc_submission_unknown`, returns no observation and locks
the interface. This conservative submission disposition does not assert the
row was rolled back: the committed job can still exist. Existing dispatched
COMMIT Fault priority and CancelledError propagation remain unchanged. Known
precommit Owner/conflict failures with clean cleanup retain their original Fault.

Root explicitly approved compatibility with the existing legacy policy:
`enqueue` and WorkerRepo do not gain a new teardown guarantee. In particular,
their existing release-error-after-known-success behavior stays nonthrowing,
and the unchanged regression method for that policy is retained. The new local
disposition records uncertainty instead of upgrading that legacy return into
a confirmed trigger receipt. Shared FixedPool/Budget/Auth and worker APIs are
unchanged. Existing `Transactions` close tracking is tightened so a thrown close
cannot be washed by a later true closed property or return the lease to a pool.

## Future launcher boundary

There is no notification in this slice. A separately reviewed trusted caller
may consider a new-job notification only after a normal observed return and an
exact `EnqueueObservation`/all-fields check, never for replay or exception. The
observation is point-in-time evidence, not a revocable permission, one-use token,
process reservation or cross-request atomic launch latch. Calling its property
twice cannot authorize two processes. A later launcher must implement durable
single-use admission, handle the repository's sticky unknown state, bind the
fixed startup/roots/release/registration and honor the ordinary global-queue
semantics. Root has approved no expected-job selector/fence in this slice:
the notified job ID and the job actually claimed by the ordinary worker must
remain separate receipts, with each page relying on its own Owner GET. Initial
activation requires Root's fresh empty-queue observation, old producers off and
one new API process. Concurrent different requests can still enqueue different jobs;
this source does not impose a global queue capacity ofone or producer lock.

No Popen, callbacks, HTTP response changes, R in ASGI, app-pool-as-worker, retry,
ACK, automatic next job or daemon is added. A future callback must run after this
method's complete normal return, not inside SQL/COMMIT/finally or from a timeout
callback. Correct scientific outputs and the previous synthetic first-job PASS
do not prove this interface has ever been used by a real Owner/API/page flow.

Tests are SQL-shaped in-memory fakes plus unchanged repository/cleanup
regressions. They do not prove PostgreSQL/RLS/concurrency, psycopg wire or actual
process behavior. No dependencies are installed and no credentials are read.

Final local run: original15 + original8 + new10 =33 methods PASS,0skip. The
earlier32-method run is retained separately and is not added to33. Test support
contains the existing never-connect psycopg stub and exact public test fixtures;
it is not product code or a runtime shim. The private runner records its exact
source support pins. Its Windows loopback socketpair events are stdlib event-loop
self-pipes, not DB/HTTP network calls; external network/launch/write/secret guard
counts arezero. Re-run in a fresh private overlay using the recorded command;
do not install the fixture stub into a deployed backend.


## Integration review record

Independent source verification passed 36 fake-transaction checks: 15 prior
repository checks, 8 prior cleanup checks, 10 observation checks and 3 added
interleaving/failure checks. Worker repository class AST and SQL literal sets
remain unchanged. These are not live PostgreSQL/RLS/Owner API or process tests.

The default remains disabled. The immutable observation is point-in-time
evidence and is not an independently revocable or single-use launch permission.
A controller still needs request admission, its current uncertainty latch,
bounded process handling and ordinary queue semantics before enabling web
computation. No runtime or service was activated by this merge.
