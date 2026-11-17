# Raw QC artifact ASGI handler — source only

`RawQCArtifactHTTP(authority, repository=None, reader=None)` is an unmounted ASGI
callable. It adds no app registration, SQL, BFF, frontend, worker, storage path,
service or runtime activation. Keep one server-owned handler and one long-lived
`RawQCArtifactReader` instance for the process lifetime. Never construct a reader
per request or automatically replace a quarantined instance. Missing Authority,
repository or reader returns a best-effort fixed 503 before receive/Auth/DB/file
or thread work. Construction creates only in-memory state and an admission lock.

## Request and authorization

Only exact GET `/api/v1/raw-qc-jobs/{canonical-job-uuid}/artifacts/{fixed-name}`
with exact single query `project_id={canonical-project-uuid}` is accepted. The
seven names come from the existing result contract. Raw/decoded path disagreement,
root_path, encoded aliases, duplicate/additional query keys and other methods are
refused. There is no caller-supplied path, Owner, root, manifest, filename alias,
URL or prevalidated subject input.

The handler reuses jobs HTTP header validation: fixed Host 127.0.0.1:57430,
optional Origin exactly http://127.0.0.1:4206, same-origin fetch policy, no cookies
or forged Owner/context headers. Authorization is passed to the injected existing
Authority.verify for every admitted request; there is no identity cache or local
signature substitute. The returned subject must be canonical. The existing
supplemental JWT-expiry check further shortens the original GET Budget. Actual
server composition must supply the real approved Authority, not the synthetic
test authority. This module does not create an Authority/client or read its key.

Range/Content-Range/Accept-Ranges, all If-* conditions, WebDAV If, compression,
redirect/proxy hints and nonce/context headers are rejected. Missing or literal
identity encoding is permitted; responses never set Content-Encoding. GET has no
body: the first ASGI request event must be final and empty. Content-Length, if
present, must be the single canonical `0`; Transfer-Encoding is refused. Existing
remaining-ms/absolute-deadline hints are validated through import_budget, with a
total service deadline no longer than nine seconds and no longer than JWT expiry.

Only `repository.read_job(subject, project_id, job_id, budget)` supplies authority
for artifact access. The DTO is canonical deep-cloned and passed through existing
raw_qc_http.response_value. It must be succeeded/confirmed with the exact valid
result and request/source/execution binding. Nonterminal valid jobs return 409
raw_qc_result_not_ready. A foreign/missing job repository rejection reaches no
reader or thread. Default-off and busy admission reach no Authority or repository.

## Full-byte response contract

The synchronous reader runs in one explicit controlled daemon thread, outside
the event loop. Its public VerifiedArtifact type is required. Name, manifest
media_type, exact integer length, bytes length and SHA256 are rechecked in that
same thread; the HTTP layer never accepts a path or an unverified stream. The
maximum is 67,108,864 bytes. Entire body/hash verification and actual thread
termination precede any 200 headers. There is no cache, Range, conditional,
compression, redirect or chunk-streaming implementation.

A successful response has exactly the fixed binary semantics:

- Content-Type: application/octet-stream, even for the manifest's TSV/text MIME.
- Content-Length: canonical decimal exact body length, 1 through 67,108,864.
- Content-Disposition: attachment; filename="{fixed-name}".
- X-Cyomics-File-Sha256: lowercase 64-character exact manifest/body SHA256.
- Cache-Control: no-store; X-Content-Type-Options: nosniff;
  Referrer-Policy: no-referrer; Content-Encoding absent.

Manifest MIME is still checked against the reader result; octet-stream is only
the transport MIME. One complete body message is sent. A response-start attempt
is never followed by another error start. HTTP errors use existing safe codes:
raw_qc_unavailable, raw_qc_persisted_invalid, raw_qc_artifact_integrity_failed,
raw_qc_timeout, raw_qc_result_not_ready and raw_qc_artifact_not_found, plus the
existing request/auth/route errors. Physical absence is not distinguished from
the reader's other unavailable/integrity failures and maps to 503 integrity;
internal descriptor/cleanup codes, original exceptions, paths and credentials
are not emitted. Unsupported fixed names use artifact_not_found.

## Single lease and uncertainty

One nonblocking admission lock is held from the complete admitted request through
Auth/Owner read, reader thread/hash and final send/cleanup. A handler binds to its
first event loop and cannot be reused on a different loop. There is no queue or
controller. A private Budget view delegates to the existing Budget and records
every child task dispatched through its run method, including nested Authority,
repository and send tasks; it does not change shared Budget/Auth/Pool code. This
prevents an outer task's completion from hiding an inner cancellation-resistant
operation. Trusted dependencies must use the passed Budget and may not spawn
unreported resource-owning background work.

On disconnect, cancellation or timeout, the reader's threading.Event stop flag
is set and all tracked pending tasks receive cancellation. Reader check callbacks
honor that stop flag and the same absolute deadline. Late ignored Auth/repository
cancellation cannot start a new reader thread because the Budget view checks the
stop flag before each later operation. Cleanup waits cooperatively for at most
50 ms within the original remaining budget. The nine-second limit includes Auth,
repository, reading/hash and sending; no new error-send or cleanup budget is added.

Normal success and fully sent known errors release only after all tracked tasks
are done and any reader thread is actually terminated with confirmed cleanup.
Reader cleanup-unconfirmed, an unexpected/untyped reader failure or return type,
any failed/aborted send, or any still-running task/thread permanently latches this
handler's admission. Late completion callbacks consume exceptions but never
return capacity. The reader is not recreated and quarantine is never reset.
The caller/operator must stop and resolve the process/resource disposition;
there is no public reset API or automatic restart/retry. This deliberately
conservative policy can sacrifice availability even on a benign transport error.

Python cannot forcibly stop a thread or a dependency that ignores cancellation,
nor retract bytes already accepted by ASGI send. An uncooperative send may finish
late; an uncooperative reader may continue until its own return. At most this one
admission remains occupied and no further acquisition/thread is permitted. One
unknown request can retain its bounded body/task references for the handler's
lifetime. This is not a hard process-memory, OS-thread-kill or delivery guarantee.
Immediate off/busy/rejection responses use the existing non-yielding best-effort
public-error helper; they do not create waiting send tasks or new budgets.

## Verification boundary

Twenty-eight synthetic ASGI tests passed on existing Windows Python 3.14 with
real Starlette request/response handling, actual controlled Python threads and
the existing synthetic Authority/psycopg NeverConnect test seams. Fake readers
return the actual public frozen VerifiedArtifact type and typed reader failures;
the tests do not invoke the POSIX filesystem reader, real Auth, DB, network,
scientific input, R, BLAST, BFF, browser or production service.

Coverage includes all seven binary response names, default-off zero dependency
work, Owner rejection, strict paths/headers/body/JWT deadlines, malformed result
and 64MiB+1 refusal before any success headers, full hash/length/MIME checks,
busy and normal release, hanging read/disconnect/cancel, permanent close-unknown,
cancellation-resistant sends with late private errors consumed, nested Auth budget
children, late repository completion not starting a reader, asynchronous-reader
protocol refusal and no second response start after send failure.

The first timing fixture could expire before reaching its intended held read;
its logs are preserved and its deadline was raised to 500 ms while retaining
the held-thread precondition. A subsequent test exposed rechecking an already
failed watcher while sending its 400 error; that interrupted the error body and
unnecessarily quarantined admission. The code now checks that watcher only for
successful binary sending; the regression asserts a complete 400 and release.
Both old logs remain separate. Independent QA and actual Linux ASGI/Reader
integration remain pending. Passing this source suite is not runtime activation
or authenticated Owner end-to-end/scientific acceptance.

## Limited R1: rejected Future disposal and narrowed deadlines

Independent review of the original candidate found AH01/AH02 while confirming
safe fixed errors and withheld leases: a completed rejected Future from an
already closed foreign loop could keep its exception unconsumed; and the outer
wait captured the earlier bridge timeout before JWT validation shortened the
shared deadline. Neither finding demonstrated a 200 response, data disclosure,
Owner bypass or admission reopening. The original candidate and failing logs
remain unchanged in their original directories.

R1 consumes an already-done rejected Future directly without scheduling into its
closed owner loop. A pending rejected Future is never cross-loop cancelled or
awaited. The permanently quarantined lease retains its one object; if its owner
loop can accept a thread-safe callback, that callback consumes a later exception.
Cleanup and later busy refusals also consume an already-done retained object,
without releasing the lease. A closed/non-running owner loop may be unable to
run callbacks: bounded retention prevents abandoning that private-error object
while the long-lived handler remains alive, and is not completion or cleanup
proof. Do not drop/recreate the handler or discard unresolved objects as a
recovery shortcut. Operator disposition must establish completion and consume
any done error before disposal. There is no poller, monitor, new thread, strong
cross-loop cancellation or automatic capacity return.

Wrong-return-type disposal precedes the post-reader deadline check, so a bad
completed Future returned after a timed-out read is still consumed. It never
becomes an artifact or starts another read. Cancellation-resistant operations
and their unknown leases retain the original fail-closed behavior.

After JWT narrowing, the body-ready wait, single Owner repository read and
single reader signal wait each use the same tracked BudgetView.run. Every one
is dispatched with the then-current shared deadline; none is retried and the
outer FIRST_COMPLETED wait is not restarted. Thus a held reader/ready/repository
stage can finish its request near a validated 120ms expiry even when the earlier
bridge allowed 900ms, while a live cancellation-resistant child/thread still
withholds capacity. Auth, the shared Budget, Reader, SQL, worker and response
binary contract are unchanged.

R1 keeps all 28 original test-method ASTs and all eight original independent
method ASTs/assertions, including the unchanged 0.55-second deadline threshold.
Five additional methods cover pending own/closed-loop Future error disposition,
a wrong completed Future arriving after timeout, and narrowed ready/repository
waits. These are 33 product tests plus eight replayed independent tests, all
synthetic; no runtime, real Owner/DB/Reader filesystem or scientific acceptance
is claimed. The original independent reviewer must still rerun the frozen R1.
