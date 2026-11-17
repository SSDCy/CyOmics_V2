# Explicit confirmed acknowledgement — source only

`await worker.acknowledge_confirmed(expected_attempt_id=..., expected_job_id=...)`
is an explicit trusted-server action, not an API/Owner capability. Missing
dependencies return off; invalid canonical IDs refuse before journal IO. It adds
no SQL, Auth, pool, runtime registration, worker loop or automatic next dispatch.

Only a durable confirmed checkpoint with matching IDs and running job snapshot
is eligible. ACK uses the same nonblocking exclusive Journal lock, requiring its
existing worker.lock (no O_CREAT). The lock covers load, read_attempt await,
terminal/private receipt validation, roots/lock rechecks, idle save/fsync/readback
and context teardown. The existing recovery logic shares _verified_terminal;
ordinary run_once/recovery semantics remain unchanged. ACK additionally requires
complete canonical equality between DB history and stored confirmed_job, including
finished_at/result/error, and a valid exact private receipt schema. Both succeeded
and failed history can be acknowledged. No history, receipt, output or exported
input is changed or removed. There is no claim, transition, R or artifact rebuild.

Success advances checkpoint sequence once to exactly idle and returns
AckOutcome(status='acknowledged', reason=..., attempt_id=..., job_id=...). Other
fixed statuses are off/refused/busy/unknown, with no exception text. From starting
idle save through readback/context exit, every exception is unknown even if idle
is already visible. Preserve checkpoint.next/old confirmed/new idle and stop the
controller; never retry ACK, delete partial state or automatically next/claim/R.
Visible idle alone cannot attribute an unknown ACK to the old IDs. Separate
read-only reconciliation must inspect historical receipt/DB/sequence. Repeated
ACK against idle refuses, rather than pretending idempotent success.

ACK does not call _ready, replace/repair a runner or pool, clear quarantine,
reopen connections or authorize future jobs. A quarantined executor flag remains
set even if its already confirmed history is acknowledged; subsequent explicit
run_once still uses its original readiness gate. Closed/read-failing repository
connections refuse before idle write. A different family/job requires a separate
explicit trusted-server dispatch after acknowledged and readiness review.

Only Journal.locked's optional keyword require_existing was added, default False
preserving ordinary startup. ACK passes True. No journal format, original44 test
method, original result history or private receipt name/pin is changed. This
source slice does not apply007, provision LOGIN, use real accounts or loosen
Owner restrictions. POSIX atomicity/descriptor/fsync and same-user/privileged
filesystem trust boundaries remain those of the existing journal.

New ACK tests use fake repository/executor plus private real POSIX temp files.
They do not run R/DB/Auth/HTTP or inspect real user data. Non-POSIX raises SkipTest
before fcntl/resource-dependent imports. Windows compile/skip is not POSIX pass.
The separate Root entry runs unchanged35+8+1 originals plus new19 ACK cases in a
new audit with owned TMPDIR and exact source manifest; independent entry/source
QA and actual POSIX execution remain pending at delivery. No runtime is enabled.
