# Dedicated worker pool — source only, disabled by default

`WorkerPool(config=None)` defines an inert disabled pool. Constructor/close do not
read files, import psycopg, connect, warm up or start tasks/threads. getconn refuses
when unconfigured. Python source loading itself is outside runtime zero-I/O claims.
Only trusted startup may supply this module's exact `Config(expected_uid=...)`,
an explicit positive real/effective UID. There is no HTTP/JSON/environment loader
or endpoint/password override. No global pool is constructed or registered.

The sole endpoint is host/hostaddr172.19.0.2, port5432,
dbnamecyomicsv2_business_dev, usercyomicsv2_raw_qc_worker_runtime. The lazy existing
psycopg AsyncConnection.connect call fixes autocommit=True, connect_timeout=2,
require_auth=scram-sha-256, sslmode=disable and gssencmode=disable. Any PG* environment
name rejects before credential access/driver import; its value is never inspected.
No DNS-dependent host choice, dependency installation or alternative endpoint exists.

Only getconn reads the fixed server file
`/home/user01/Cy_Omics_v2.0/runtime/raw-qc-worker.json`. No credential file is created
or provisioned by this change. The fixed descriptor walk uses O_DIRECTORY /
O_NOFOLLOW for every ancestor and O_NOFOLLOW / O_NONBLOCK for the file. Ancestors
must be root/current configured UID-owned and lack group/other write permission;
immediate runtime directory must be0700/ownUID. File must be regular, single-link,
0600/ownUID,1..4096 bytes. Bounded read and descriptor/path identity rechecks reject
replacement/change. JSON must be UTF8, duplicate-free and exactly schemaVersion1,
purpose raw_qc_worker_runtime, password matching64 ASCII [A-Za-z0-9_-] characters.
Every opened descriptor is tracked immediately. A close attempt consumes its
ownership before the syscall; an error has unknown close status, so that numeric
FD is never blindly retried. Other tracked descriptors each receive one cleanup
attempt, including the child if closing its predecessor fails. Any teardown
uncertainty raises a fixed private signal to poison/withhold the pool's slot even
before a driver call, preventing repeated getconn from repeating possible leaks.
Ordinary metadata/schema rejection returns pre-driver capacity only when all
owned descriptors have confirmed successful close attempts. These are synthetic
failure-policy guarantees, not evidence of real secret/actual-A FD leakage.

No credential/document is logged, returned as DTO or retained in pool fields.
Read buffers are overwritten; Python/driver immutable string zeroization is not
guaranteed. Fixed sanitized errors expose neither driver text nor credential data.
Source-owner/privileged edits and filesystem stall/TOCTOU boundaries still exist.

Capacity is exactly one across acquisition/active lease. No idle list, warmup,
retry, background thread/task, cleanup loop or health check exists. One call waits
on a semaphore then directly awaits the driver. A successfully returned handle
is bound as active before cancellation/close checks. Only that exact handle may
be released; foreign and duplicate releases do not return capacity. release/close
attempt synchronous pgconn.finish and consume every teardown BaseException.
Capacity returns only after closed is literally True. Successful transactions or
source reads must continue closing before release; an accidentally open release
is closed here rather than stored for idle reuse. No active socket is returned
for another request.

Unknown close/finish failures withhold the slot and fail closed without raw
exception logging. Driver failure/cancellation before returning a usable handle
also poisons the pool conservatively: absence of a handle is not proof of socket
closure. It does not retry or fabricate cleanup. Operator-reviewed recovery must
establish any unknown connection's disposition before constructing a replacement
pool; creating replacements automatically could violate the single-connection
bound. An already waiting caller requires its existing external Budget/cancellation
to stop waiting when a poisoned slot is withheld. getconn itself does not start
a timer task. New acquisitions reject once poisoned/closing.

If cancellation is ignored by the driver until it returns late, getconn binds the
handle, detects task.cancelling(), closes/withholds it and preserves cancellation
when cleanup succeeds. Closing during acquisition similarly rejects the late
handle and only frees confirmed closure. A waiter cancelled before acquisition
does not free someone else's lease. close is synchronous and idempotent; a
previous borrower must not reuse a socket that shutdown closed. One event loop
and one thread own this object; thread/cross-loop use is unsupported. The driver
owns sockets never returned to Python; no stronger unknown-socket cleanup claim
is made. Pool creation/configuration is not Owner, function-grant or Auth proof.

Migration007's worker role is only a NOLOGIN permission template. Actual LOGIN,
password provisioning, secret materialization, connection grants and live
function-only permissions have NOT been established or tested here. This source
does not alter FixedPool, global settings, Budget, Auth, SQL, services or any
runtime activation. Raw-QC transactions and loader keep their separate reviewed
close-before-release responsibilities. Actual integration remains a later gate.

Run the portable suite with existing Python:

```text
python -B -m unittest discover -s backend/tests -p test_raw_qc_worker_pool.py -v
```

All credentials are obvious repeated synthetic bytes built in memory. Tests mock
file metadata/descriptor reads and the driver, including fixed connect kwargs,
single-capacity interleavings, cancellation, late return and failed finish.
No real secret, socket, DB role/login, PostgreSQL, service, R or research input is
used. Passing these tests is not actual worker integration or production approval.
