# RAW-QC-SINGLE-LAUNCHER-SOURCE-001 — private one-attempt controller, default off

This candidate supplies the launcher/explicit repository adapter plus its minimal fixed ordinary-process startup source, fake tests and document. No existing public app/HTTP/repository/worker/SQL/Git/state/A service is changed. No R, SSH, DB, real process, credential, Auth session or real research data is used by preparation. The ordinary once entry is the original reviewed public implementation; this wrapper does not reimplement worker/R, ACK or a queue loop.

## Root's clarified semantics supersede the older contract proposal

Root explicitly approved ordinary global-queue semantics. There is no target-job selector/fence, monkeypatch or new SQL. A queued POST notification job and the job actually returned by ordinary worker claim may differ. snapshot() reports notification_job_id separately from worker_outcome.actual_job_id and always post_job_success_inferredFalse. Child success never marks the POST job successful; the browser's original real Owner GET and result/artifact validation alone determine its own job state. The earlier sealed DI package's LAUNCHER-CONTRACT target-fence proposal is historical and is superseded for this controlled private slice; it is not a new implementation requirement.

Before activation Root must freshly prove queued/running0, old preview produceroff, no competing worker, new API workers1 and this adapter as the unique new enqueue path. Those are outside this module and are not self-certified by a bool or Config hash. This is not global exactly-once processing or a continuous multi-user scheduler.

## Exact internal observation and POST serialization

SingleRawQCLauncher(config=None) is defaultoff and opens no file/socket/process, performs no Auth/SQL or component import. Config() is alsooff. Enabled Config requires four explicit Root-built public pins (startup source, fixed Python executable, backend manifest, activation-review hash); it accepts no path,command,environment,Owner credentials or jobselector. An activation-review hash binds operator provenance in private startup control; this module cannot prove that review/preflight happened merely because a hash is supplied. Root must separately approve/materialize that control.

SingleLaunchRepository(repo,controller) is the explicit server-only DI adapter for RawQCJobs. Every POST still passes its unchanged real Authority before invoking enqueue. The adapter calls controller.submit on the same RawQCRepository; read_request/read_job delegate unchanged to the same repository and Owner/source/RLS checks. No generic getattr, owner header, bypass token, alternate pool or workerSQL surface is added.

The repository must be the original exact class with enqueue_observation_enabledTrue. It consumes mainline's frozen EnqueueObservation(_job_json,created,commit_confirmed,teardown_confirmed), job_public() independent DTO and may_notify_new property. The observation seam must have passed independent QA before activation; author-only33tests are not approval. No202 inference is made.

The controller atomically leaves idle before its firstawait, validates fixed public source pins and creates/fsyncs a persistent O_EXCL web-launch.once file before enqueue_observed. Thus one API loop serializes the whole POST→COMMIT→close/release observation→notify interval. Concurrent same/different POSTs cannot both enter SQL through this controller. Only exact createdTrue+commitTrue+teardownTrue+may_notify_newTrue+validated queued/confirmed DTO schedules one owned monitor. createdFalse replay never starts a child. After notified/observed/replayed, identical subject/project/dataset/request/family can only call read_request, never enqueue again; other newPOSTs are refused. Unknown never permits even that path. No worker status is projected into the response DTO.

This is deliberately one attempted POST per private release: any post-reservation error, cancellation, validation failure, existing latch or close uncertainty conservatively consumes/blocks the slot. It does not unlink even after a definite noninserting failure; a new slice needs Root disposition/new identity. A hostile authenticated request can consume this single private slot, so it is not a public multi-job admission policy. Root-controlled singleproducer/loopback access is essential. Restarting/reconstructing the controller does not authorize another attempt: the same durable latch is never removed/reset/reused.

## Fixed process, software and roots

The single command is the existing fixed venv Python with -I -B, the exact fixed private-release raw_qc_live_once_start.py path and --once. cwd is that private backend. Environment is exactly PATH/LANG/LC_ALL; stdinDEVNULL and private stdout/stderr pipes. No parent PG* or Auth token/password is passed. The ordinary worker alone lazily reads its fixed runtime secret via WorkerPool; this launcher/startup does not read/export it.

Before reservation and again before spawn, _sources checks actual Linux UID1000 and fixed namespaces, the approved startup SHA, fixed interpreter's resolved regular bytes/SHA and the private approved-backend-manifest SHA. It verifies at most256public source files/4MiB, singlelink/non-symlink/safe-owner/mode paths and stable before/after metadata. Mandatory ordinary worker/R core files must be included. Empty approved public source files are allowed. These are disk consistency checks, not a memory attestation or protection against concurrent trusted UID1000 mutation. Root controls must bind the entire approved release and forbid changing it while active.

The fixed startup checks exactly --once, actualMainThread/UID/namespaces/cwd and bytecode-disabled mode, constructs original RuntimeConfig/WorkerRoots/dispatch.Config from fixed fresh raw-qc-live-001 control/inputs/staging/published/receipts, and calls original raw_qc_once_entry.main. Its native5historical device/inode/owner/mode pins are unchanged. It accepts no client config/env/native path. Default startup main() isoff with no config imports/IO. The ordinary factory revalidates package2072/c088/v3/native/resource identity; API's full8execution snapshot must match during Root assembly.

All five new roots must be exclusively prepared0700 and correctly bound to the API's long-lived Reader published root. They have not been created or inspected by this task. Reusing oldconfirmed synthetic checkpoint roots would only recover the oldattempt; do not do so. No migration, password provisioning, runtime registration or service switch occurs here.

## Finite supervision and sticky uncertainty

The controller owns one app-lifetime monitor task and retains all spawned/acquisition/read/wait tasks. Spawn observation is bounded3s, child observation85s and one owned termination wait4s. StreamReader limit4096 and explicit application caps8192stdout/4096stderr bound retained captures; these are not kernel hard pipe/memory caps. No raw capture or exception text is persisted: only fixed status/UUID and length/SHA/exit/capture_complete diagnostics. Partial/unknown capture remains unknown rather than discarding all stage diagnostics.

For a live child, pidfd acquisition is bound to exact argv/cwd/UID/namespaces/starttick before/after open. Only that verified pidfd may receive one SIGTERM; never numericPID/processgroup/wholeUID kills. No SIGKILL/escalation/retry is added. An ignored signal, blocked kernel operation or failed handle acquisition can outlive the finite wait; the controller then remainsunknown with durable intent retained. It never claims a hard kernel lifetime bound or that process/R descendants exited merely because a deadline expired. Actual cleanup/Root disposition is required before another activation. The ordinary child owns its R cleanup; parent exit alone is not descendant proof.

Late acquisition callbacks consume errors and attempt one cleanup of a returned handle but never unlock unknown. Monitor cancellation during acquisition arms the same late cleanup path. If the loop is already closed, the coroutine is closed and completed task exception consumed; OS disposition remainsunknown, not falselyclosed. close() before the monitor starts prevents a process; activeclose retains uncertainty and does not cancel-and-gather an unbounded handshake. It must be called by the app lifespan while its loop is still available; abrupt loop/process loss is not a successful shutdown. No daemon thread, monitor-for-new-jobs, timerloop or automatic rebuild exists.

A normal process receipt is checked with the real child exit, strict schema/booleans/UUIDs, noACK/next/retry/OwnerE2E claim, exact confirmed/idle reasons, loopclosure/no pending resources and fixed stdio. Refused/unknown/cancelled/partial/malformed outcomes stopunknown. Observed failed or terminal-recovery/idle outcomes never spawn again. Confirmed child business information remains separate if subsequent FD/transportclose isunknown. CPython asyncio subprocess transport private fields are explicitly used only after knownreturncode to avoid close()'s live-child numeric kill path; _finished,closing and pipe fileclosure must be confirmed. Any close exception is sticky even if a later getter saysclosed. This POSIX/CPython resource behavior remains a real-runtime validation gate; fake tests do not prove it.

## Remaining integration and verification gates

This candidate is a functioning source controller/adapter, not an activated web pipeline. Root must integrate, in a separately reviewed assembly delta: RawQCRepository(...,enqueue_observation_enabled=True) on the original same appPool; one controller retained in app.state; adapter supplied to both RawQCJobs and artifact reads; await controller.close() during lifespan before loop teardown; startup pins/manifest/config and correct private-graceful-shutdown budget. Existing samepool DI helper currently returns an unwrapped repository with observation defaultFalse; it must not silently become live without this explicit integration.

Sites' client flags and BFF twoflags/fixed native request injection must be active only in the approved private release; actual Auth,Owner isolation,job GET,sevenartifact transport and manual table display remain real E2E gates. No automatic public activation/continuous queue claim/ACK is provided. The real Owner browser run requires Root's next specific approval after independent source QA.

Author fake tests cover defaultoff,serial pre-SQL admission,new/replay/commit/close/cancel,same-key read-only recovery,different global worker job,lateacquisition,closefailure,timeout oneowned signal and cap/receipt fences. They run no actual process/DB/R or POSIX file/pidfd primitives. The initial harness rejected CPython3.14 Windows _fallback_socketpair selfpipe creation, producing13setup errors; original attempt001 logs are retained. Only the test harness was corrected to recognize that exact stdlib function; attempt002 ran16unique methods with26stdlib selfpipe events and zero business-network/actual-execution guardhits. No product assertion was relaxed and counts are not doubled. Independent QA remains required. STOP.


## Integration review record

Independent source review passed 16 author model checks plus 3 added model
checks. These include fake asyncio creation/cancellation and filesystem/handle
models, not real POSIX, Python subprocess transport, database or R execution.
The launcher and fixed startup products are copied byte for byte.

The public tests use repository-relative paths. The startup default-off check
remains; its historical native-directory audit comparison stays in the sealed
review evidence, rather than depend on a private work folder. All 16 portable
checks passed after placement; these are repeated checks, not new cases.

The activation-review digest is operator provenance, not a physical approval
verification by this module. Actual activation still needs Root's reviewed
release and public manifests, fresh empty ordinary queue, prior producer off,
one API worker, same-pool observed enqueue and app-lifetime cleanup. No roots,
processes, services or automatic computation were activated by this merge.
