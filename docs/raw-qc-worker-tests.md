# Portable worker lifecycle tests — source only

The three suites contain35 original worker cases,8 independent lifecycle-edge
cases and1 precise roots-regression case. Each of these44 test-method ASTs is
unchanged from the reviewed R1 suites. Only bootstrap, OS temporary-directory
selection, suite inheritance/helper references and script entry were adapted.
All imports use this checkout's backend and sibling test directory. There is no
copied support backend, private audit dependency or hardcoded server path.

Run exactly44 cases from the checkout's backend/tests directory:

```text
python -B -m unittest test_raw_qc_worker_once test_raw_qc_worker_lifecycle_edges test_raw_qc_worker_roots -v
```

Each suite is also independently discoverable. The two extra classes inherit
unittest.TestCase, copying only setUp/worker/once from the primary suite. They
do not inherit or rediscover the original35 test methods. TemporaryDirectory()
uses the host's standard temporary location (including an operator-supplied
TMPDIR on POSIX); no test-runs directory is created in the checkout. Root's
separate actual-A invocation can set TMPDIR to a new owned private audit root.
The tests themselves do not prepare server directories or change environment.

On Windows/non-POSIX all three modules raise unittest.SkipTest before importing
worker/executor modules that require fcntl/resource. This is an explicit platform
skip, never an executor stub or successful POSIX test result. These filesystem
tests target the reviewed unprivileged Linux/POSIX environment (procfs, fork,
fsync and flock); they do not claim general Unix portability or root suitability.

Public v3 source is used directly. The existing v2-registration rejection case
remains a negative test; there is no v2 compatibility shim or monkeypatch that
accepts a v2 execution contract. No public product code is changed by this port.

Repository/source loader/executor are synthetic fakes. The fake executor writes
obvious lifecycle placeholders; these are not scientific R outputs. Actual
private filesystem IO/fsync/flock occurs only when run on supported POSIX, and
fixtures use only small synthetic bytes. No DB driver connection, R subprocess,
Owner/Auth/HTTP, real research file, worker daemon or runtime registration is
required or established. This suite does not prove function-only DB permissions,
actual R correctness, scientific-stage completion or production containment.

The pinned R1 worker fix verifies private roots again in _ready before proceeding
to execution. Its precise regression observes successful synthetic input export,
blocks execution on staging permission drift, and verifies recovery only reads
the unresolved attempt after restoring roots; recovery does not resume R.
Checkpoint and input-export products remain the same reviewed bytes. Lifecycle
tests retain crash/unknown-commit/no-retry and historical-receipt protections.

The earlier R1 review recorded44 real POSIX test passes against the original
audit bootstrap. Those are prior evidence. This port was only compiled and
platform-skip checked on Windows; Root must independently run this exact public
test layout on POSIX before recording its44-case result. Portable test presence,
module skips or exit0 do not activate a worker or prove this port's POSIX result.
