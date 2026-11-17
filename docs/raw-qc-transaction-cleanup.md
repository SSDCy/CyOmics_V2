# RawQC transaction cleanup

RawQC closes its connection before pool release, including after successful
transactions. These slow tasks do not depend on reusing open connections. A
connection is released at most once and only after its closed state is confirmed.
An open IDLE status alone never makes a connection safe after uncertain teardown.

Cleanup is private to `raw_qc_repository.Transactions`. The shared Budget,
FixedPool, Auth and other repositories are unchanged. Budget abort callbacks do
not throw: a failed socket finish cannot prevent Budget from cancelling its task
and attaching the existing consume/discard callback. A late pool acquisition uses
the same nonthrowing close/release discipline. Callback errors are not logged with
private exception text.

After cancellation/timeout teardown has started, RawQC issues no ROLLBACK or other
SQL on that connection. Before teardown, an already completed failed operation
may still use the existing bounded rollback path. No rollback or cleanup outcome
disproves a previously dispatched COMMIT: writing requests retain submission or
transition unknown. Non-COMMIT caller cancellation, timeout and primary Fault
classification remain intact. Release failures do not replace primary results.

If closing fails, RawQC does not retry closing, release the open connection or
pretend it is healthy. Its active lease and capacity remain withheld. If release
itself fails after confirmed closure, there is also no second release attempt.
These are explicit development availability limitations, not leak-free recovery:
the pool can lose capacity and require operator recovery/restart. An uncooperative
driver that ignores cancellation and cannot be closed may remain pending; later
task errors are consumed, but termination cannot be guaranteed. There is no
automatic reconnect, retry, repeated POST, claim or R execution.

Memory-only regression coverage includes abort failure during claim/read/Owner
read/transition cancellation, held-RPC timeout, COMMIT receipt loss, dirty pool
state, late acquisition, cursor-fetch cancellation and cancellation-resistant late
errors, normal one-close/one-release, and release exceptions. These are not real
PostgreSQL, driver failure-frequency, HTTP, authentication or scientific tests.

Two test expectations changed under explicit review: late failed closure now
withholds the lease instead of requiring release; successful rollback is followed
by closure rather than leaving the connection IDLE for reuse. The original failing
reproduction and original regression logs remain separate evidence. No public
runtime or feature is enabled by this change.
