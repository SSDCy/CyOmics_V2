# Ordinary raw-QC single-process entry — source candidate

`raw_qc_once_entry` adds a process entry for the existing ordinary chain; it
does not implement that chain again. Source only, default unconfigured. There
is no audit fixture, admin SQL, first-job flag patch, alternative Pool/runner,
registration override, HTTP route, controller, polling, daemon, automatic retry,
ACK or next job. This file does not enable the current preview/API flags.

## Exact interfaces and activation

`run_configured_once(config=None) -> EntryOutcome` is a synchronous foreground
process operation. `main(argv=None, *, config=None) -> int` writes one fixed JSON
receipt and returns the observed exit classification, except uncertain teardown
uses the self-exit described below. `argv=None` reads only the process argument
list; accepted arguments are empty or exactly `--once`. No path, Owner, job,
attempt, action, endpoint, environment, password or arbitrary import argument is
accepted. A configured main requires explicit `--once`.

With config=None, no dispatcher/factory/POSIX module, event loop, credential,
connection or child is created. `python -B -m cyomics_projects.raw_qc_once_entry`
therefore prints off/entry_unconfigured and exits2. `--once` with no trusted
object still stays off. There is **no** file/environment activation fallback.
Invalid arguments are not echoed. JSON/HTTP objects are not accepted as Config.

Activation uses the original exact `raw_qc_dispatch.Config`, assembled by a
separately reviewed server startup module. This candidate intentionally chooses
trusted startup objects rather than adding another filesystem registration or
secret loader. It is not permanently disabled: a valid object on Linux/POSIX,
real/effective UID1000, `-B`, one MainThread and no running event loop reaches
ordinary `dispatch_once(config, action='run_once')` exactly once.

## How Root supplies the ordinary configuration

Root must approve/pin the entire deployed backend release (including this entry,
dispatcher, repository, Pool, loader/exporter, checkpoint and artifact helpers),
fixed A namespace/UID/startup and existing private roots before execution. The
four-Python-source runner manifest alone does not pin the whole backend or
attest imported memory. Do not patch an old sealed audit/release to enable this.
Root's **fixed** startup module constructs the following public metadata objects:

```python
from pathlib import Path
from cyomics_projects.raw_qc_runtime import Config as RuntimeConfig
from cyomics_projects.raw_qc_checkpoint import WorkerRoots
from cyomics_projects.raw_qc_dispatch import Config as DispatchConfig
from cyomics_projects.raw_qc_once_entry import main

# ROOT and APPROVED_NATIVE_DIRECTORY_PINS are literal, independently approved
# server constants in this startup module, not CLI/environment/current capture.
roots = WorkerRoots(control=ROOT/'control', inputs=ROOT/'inputs',
    staging=ROOT/'staging', published=ROOT/'published', receipts=ROOT/'receipts')
runtime = RuntimeConfig(input_root=roots.inputs, staging_root=roots.staging,
    publish_root=roots.published, expected_uid=1000, source_owner_uid=1000,
    native_directory_pins=APPROVED_NATIVE_DIRECTORY_PINS)
config = DispatchConfig(runtime=runtime, roots=roots, expected_uid=1000)
raise SystemExit(main(config=config))  # fixed startup.py --once
```

This is a constructor interface, not a ready-to-run approval or a root creator.
ROOT must name the newly approved project-specific private worker root; all five
sibling directories must already exist, be distinct/non-nested and700. The
approved native tuple contains five existing `NativeDirectoryPin(env_key,path,
device,inode,owner,mode)` objects, with Path values. Reuse the previously reviewed
R_HOME/R_SHARE_DIR/R_INCLUDE_DIR/R_DOC_DIR/LD_LIBRARY_PATH metadata; never capture
current values and silently call them approved. No new arbitrary package,
executable, env, limits or registration can be supplied through this entry.

The original factory verifies the fixed sibling source bundle, original2072
script, native ELF/R version and unchanged v3 resource profile, then produces a
normal RawQCRunner plus ExecutionRegistration. The original dispatcher checks
the exact pair/root correspondence and assembles WorkerPool, worker repository,
WorkerBoundSource, BoundRaw, InputExporter, RawQCArtifacts and WorkerOnce.
WorkerPool's existing server-only secret remains lazy and local to A. This
entry never reads/copies/prints/hashes the password document and has no admin or
app-pool fallback. Config data is never printed or included in a receipt.

## One action and lifecycle

An enabled attempt consumes an irreversible module-local once latch before
signal/loop setup. A second call cannot claim again, including after idle or
unknown; there is no public reset. An unknown latch cannot be washed by a later
config=None call. This is not a durable cross-process lock: the existing worker
flock/checkpoint still governs attempts, and an operator must not respawn this
process to bypass unknown disposition. Queue selection is ordinary claim, not
an imagined job-ID selector. First UI integration must establish an appropriate
controlled queue; this source entry grants neither Owner authority nor safe
permission to claim some other imagined job.

SIGINT/SIGTERM are temporarily handled on the enabled MainThread, with original
handlers restored exactly once. First interruption raises a private BaseException
so ordinary cleanup can run; further signals do not repeatedly interrupt it.
Cancellation/KeyboardInterrupt/restore errors are sanitized and non-success.

The new stdlib loop directly runs the one dispatch task. There is no wait_for
wrapper adding a competing task and no asyncio.run automatic shutdown gather.
Any pending task after return is unknown even if one cancellation/scheduling
turn consumes it. That turn is not a hard wall deadline against arbitrary
blocking Python/C code. No unbounded cancellation gather or executor shutdown
wait is attempted. New async generators/executor resources conservatively mean
unknown (the entry deliberately checks the reviewed stdlib loop's private
`_asyncgens/_default_executor` fields; another event-loop implementation needs
review). The loop is closed once. Any close exception is sticky even if
is_closed subsequently says true. Pending references remain private until exit;
there is no background monitor or task name/context logging.

`run_configured_once` is a terminal process API, not an embeddable ASGI helper.
On unknown it returns non-success for inspection and forbids reuse. Use main in
the ordinary process: after flushing its fixed receipt, uncertain teardown calls
`os._exit(1)` **for this process only**, avoiding interpreter joins/finalization
of cancellation-ignoring work. It never signals numeric R/PG PIDs or claims all
descendants exited. Abrupt exit can leave remote SQL, child R or partial private
state unresolved. A separate reviewed outer deadline/disposition procedure must
establish what remains before any new process/attempt; no cleanup or retry is
automatically performed here. A blocked synchronous native call still requires
that outer deadline; this entry does not invent a new hard process budget.

## Result/exit semantics

Only original confirmed/succeeded with confirmed dispatcher teardown, no
interruption, no pending/extra async resource or loop error, and successful loop
close produces confirmed_succeeded/exit0. Dispatcher business fields are kept
separate from entry teardown. A confirmed job is not erased if process teardown
is unknown, but it cannot cause exit0 by itself.

Confirmed/failed gives exit4. Recovery reason terminal_readback_confirmed does
not distinguish a succeeded from failed historical job in DispatchOutcome, so
it yields confirmed_terminal/exit4 rather than invented success. Empty queue is
idle/exit3. Off/invalid setup is nonzero; unconfirmed/reconciliation is nonzero;
cancel/unknown cleanup exits1. Receipt contains only fixed enums, canonical IDs,
booleans and exit code. No config, path, exception/cancellation text, handles,
source/credential data or opaque extra result keys are serialized. ACK_calls,
next_jobs and automatic_retry are always0/0/false; Owner_or_API_E2E is false.

This entry removes dependency on the synthetic first-job audit CLI. It does not
make a queued browser task trigger a process: that private preview release,
real Owner POST/read/artifact assembly and single-job trigger still need a
separately reviewed wiring task. Repeated run_once on a confirmed checkpoint
recovers the same attempt; no ACK or next job is inferred.

## Validation boundary

Tests use original Config/DispatchOutcome signatures, the actual dispatcher body
composed through its existing private test mocks, and mocked native/DB components.
One bounded **local Python test child**, with only fake dispatch, demonstrates
unknown self-exit with a cancellation-ignoring task; it is not an R/DB child or
POSIX runtime validation. Windows asyncio may use an internal loopback self-pipe.
No genuine driver, credentials, A, SQL, R, service or research input is used.
Default source import/entry has no POSIX dependency probes. Root must obtain
independent source QA, approved startup/release pins, wire/role verification and
actual single-job evidence before enabling this on A. STOP.


## Integration review record, 2026-11-18

Independent source checks passed: 11 original checks and 3 added checks. They
include one bounded local Python child with fake dispatch; they are not native
R, PostgreSQL, Owner/API, browser or production tests. The default remains off.
The candidate module and test are copied byte for byte.

A separate synthetic first-job audit on A completed one ordinary worker claim,
native R computation, seven published artifacts and confirmed terminal readback.
Independent readback verified the six scientific tables and observed native
process disposition. That audit used the earlier ordinary WorkerOnce composition,
not this new process-entry module, and does not prove this entry has run on A.

Private preview assembly, a bounded post-enqueue process trigger, actual Owner
authorization and real-file page acceptance remain separate integration gates.
No service or preview was activated by this source merge.
