# Default-off local log2 runner — source candidate

This adds one synchronous dedicated-process adapter, not an execution profile,
worker registration, Owner endpoint, processed-version writer or publication.
The R source remains 11,996 bytes / SHA-256
`08aec68d6ee74bd09b7cc70100d10e147c527986a3d42323fe5541a3b608e787`.
The strict validator remains 14,877 bytes /
`ba5fa3ed521fe7f97810ecdab06e1936e9dec8c9ebac45f8d2da3e3d77989d9e`.
Neither dependency is edited here. The adapter does not implement statistics or
change base::log2, filtering, masks, parameters or scientific tolerances.

## Trusted configuration and call

`Log2MatrixRunner()` and `Log2MatrixRunner(Config())` are disabled. Calling run on
them fails before loading the POSIX executor, probing resources or doing I/O.
There is no environment-variable, JSON, URL, shell-command or HTTP activation.

A future trusted worker constructs the frozen server-only `Config` with
enabled=True, a canonical job UUID, fixed source/selected/script/staging Paths,
source and selected byte-length/SHA `Pin`s, a parameters `Pin`, family, an exact
three-bool tuple and the existing five `raw_qc_runner.DirectoryPin`s captured by
the separately approved server preparation. Constructor type checks are not
Owner authorization. Actual Authority, source/version lookup and startup pins
must be enforced by that future integration; clients must never construct this
Config or supply its paths/native pins.

`flags` is explicitly `(reverse, contaminant, site_only)`, not JSON key order.
The adapter reconstructs the existing canonical parameters with
`log2_matrix_validator.parameters(flags)` and requires the expected bytes/SHA.
That parameters object has exactly 12 top-level keys. The only variable values
are the three named strict booleans, including mixed combinations. No HTTP
request schema is interpreted here; the separately reviewed request envelope has
request_id/quant_family/parameters and does not convey execution authority.

`run(cancelled=trusted_sync_bool_callback)` requires Linux, Python -B, real/effective
UID 1000, one main thread, default SIGCHLD disposition and no existing real timer.
It is unsuitable for ASGI, a multithreaded API process or an event-loop callback.
The native executable is the original fixed `/usr/lib/R/bin/exec/R` with the
original 14,488-byte SHA pin, root-owned mode 0755. No engine/path override exists.
Source/selected files must be UID 1000, ordinary single-link private files with no
group/other permissions. Script/validator code must be ordinary pinned files,
owned by UID 0 or 1000, without group/other write permission. Staging must already
be UID 1000 private directory; ancestry must be nonsymlink and not group/other
writable. Paths, source contents and artifact bodies are excluded from repr.

The fixed CLI matches the already executed three-child synthetic unit's family
invocations (this adapter itself starts only one child):

```
R --vanilla --slave --file=<private script snapshot> --args
  <input snapshot> <selected snapshot> <family>
  <reverse true|false> <contaminant true|false> <site_only true|false>
  to_NA positive_only none none <new output directory>
```

There is no extra parse, reverse, retry or second-family launch in one run.
Original numeric zeros and blanks become working NA via the unchanged R core;
positive values alone use official base::log2. Legal log2 zero/negative outputs
remain legal, duplicate IDs/all-NA retained rows remain present, and no groups are
guessed. The wrapper does not manufacture an R-side source SHA.

## Process lifecycle, bounded bytes and result

The deferred runtime reuses `r_raw_qc.raw_qc_runner`'s exact `Limits`, `ENV`,
`NATIVE_ENV`, native-directory validator, safe absolute/single-file opens and
identity helper. Process admission, observations, finish, pidfd-only stop and
helper-descriptor closure are performed by its existing
`process_policy.admission/OwnedTasks`; there is no new descendant controller.
The native limits remain CPU 45 s, parent wall budget 60 s, address space 2 GiB,
FSIZE 64 MiB, stdout/stderr 64 KiB each, cleanup 2 s, NOFILE 256, NPROC 512,
CORE 0, observed job tasks 8 and UID admission threshold 480. Environment remains
the original clean thread-limited native R map plus TZ=UTC. Limits values are
recorded, but no new registered profile ID or v3 registration is asserted.

Before launch, source/descriptor, script, Python validator and native bytes are
fully read with caps and pins. Only private 0400 source/descriptor/script
snapshots are written, with exclusive create and fsync, into a new fixed
`<job UUID>.partial` directory. An existing job directory is refused, never
removed, overwritten or retried. Current mode/identity and bounded exact output
allowlist are checked throughout execution. Files are O_NOFOLLOW regular
single-link reads; sizes, EOF, before/after fstat and current path identity must
agree. No unrelated research directory is scanned. Artifact scandir enumeration
stops at the fixed job/11-output bounds; the inherited process policy separately
performs its documented public real-UID /proc metadata census.

The inherited resource FSIZE limit remains 64 MiB, while this contract's
stricter validator/read caps are 25 MiB per artifact and 128 MiB total; input is
25 MiB and selected descriptor 128 KiB. Allocated output blocks are monitored as
well as logical lengths. Monitoring is sampled, not a hard aggregate filesystem
quota. Potential overshoot and blocking syscalls are not claimed impossible.

A complete result requires exit 0, exact LOG2_MATRIX_COMPLETE newline stdout,
empty stderr, observed process cleanup without uncertainty, all pipe/selector/
observer/pidfd closes confirmed, and then all 11 files fully read and passed to
the unchanged strict validator. Original input, selected descriptor, script,
validator/native bytes, native directories, snapshots, output identities and
staging-root inode/owner/mode are rechecked before returning. Directory mtime,
ctime and nlink legitimately change when a child directory is created, so they
are not mistaken for staging-root replacement.

`RunResult.bundle` owns detached immutable validated byte artifacts and actual
SHA manifest. `RunResult.receipt` returns a fresh JSON object with source,
descriptor, parameters, native/script/validator pins and process evidence. The
validator bundle's tool_execution_attested/Owner/publication/analysis flags stay
false; the separate runner receipt describes this local invocation, not an
authenticated Owner result or a persisted processed version. No rename, DB
commit, API response, output durability/publishing or success checkpoint occurs.

Exit 3 plus exact LOG2_MATRIX_EMPTY newline and only the five expected audit
files can produce only the failure `empty_not_analyzable`, after the validator
independently confirms the original source has no retained rows. It never
returns a complete bundle or treats those five tables as validated successful
scientific artifacts. The strict validator is not relaxed for empty output.

Any active-run input/integrity/timeout/cancel/unknown/close failure permanently
quarantines this instance. It returns no partial result, never retries and never
deletes diagnostics. An unbound child identity has no numeric PID fallback.
Failure messages use fixed codes and never print R stderr, user measurements,
private paths or original exceptions. Process-policy containment remains its
documented development-only observation: no all-descendant guarantee, no hard
per-job PID cgroup, CPU/AS limits are per process and sampled UID metadata can
miss short-lived/unobserved activity. A same-UID writable development tree is not
production immutability or complete TOCTOU immunity. Python loaded-code trust
still relies on approved startup; reading a source file's pin is not a proof
that an arbitrary mutable Python module object was never replaced.

## Evidence and remaining gates

Sixteen local tests pass, zero skipped. Tests exercise the real adapter state
machine with fake signals/selector/child/OwnedTasks and synthetic actual-unit
artifact bytes. No native child, POSIX primitive, R/SSH/DB/network or user file is
executed/read by the tests. The guard counts for network/process/outside-read/
filesystem-write are zero. Cases cover default-off, strict config, mixed named
flags, exact 11 arguments and inherited resources, complete both-family
synthetic byte validation, short read/SHA/metadata/cap/close failure, extra or
missing artifacts, cancel/timeout, bad exit/marker/stderr, observation uncertainty,
empty filtering, all close positions and final-gate failure.

The first attempt retained two failed assertions because FakeWatch.stop omitted
the poll/reap that real OwnedTasks.stop performs, leaving the fake child's exit
unknown. The adapter correctly refused success with cleanup_unconfirmed. The
original test source/log/pins are retained; only the fake stop witness was fixed.
Attempt 002 passed. A final source hardening added explicit regular/single-link,
private-mode and root-owned native checks; attempt 003 is the final 16/16 PASS.
These runs are not cumulative test counts.

The adapter author previously wrote the precursor flags R core and validator;
this source delivery is not independent certification of their science or of
the inherited process policy. Root's separate prior scientific/validator reviews
remain prerequisites. Independent review of this new adapter and one separately
approved actual-A native unit are still required. There is no current runtime,
Owner API, processed version or publication activation. **STOP.**
