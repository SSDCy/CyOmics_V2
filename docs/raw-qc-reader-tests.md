# Portable reader tests — source only

test_raw_qc_reader.py combines34 original reader cases and6 independent pure
attacks. All40 test-method ASTs remain exact. Changes are only the bootstrap,
standard OS TemporaryDirectory(), removal of the managed ancestor UID adapter,
and SimpleNamespace bindings to the same public r/c/make_job fixture for the
unchanged six methods. No old support modules, v2 shim or server audit path is
included in this public candidate. Reader product9904B/22194c3d... is unchanged.

Run from the repository root using its current public backend:

```text
python -B -m unittest discover -s backend/tests -p test_raw_qc_reader.py -v
```

Non-Linux/non-POSIX raises module SkipTest before product/POSIX imports. That is
not40 passing cases. Linux tests require an unprivileged UID and real private
filesystem semantics; no mapped-root ownership adaptation is applied. Root can
set TMPDIR to a new owned0700 test root before Python starts. No checkout-local
test-runs directory is created. Tests create/clean only their own synthetic temp
files, including exact64MiB boundary and intentionally injected descriptor faults.

Six pure cases use fake numeric descriptors/control functions;34 originals use
real POSIX file IO with synthetic DTO and seven fake output files. These are not
scientific R outputs or user data. No DB, Auth, HTTP, worker/lease/runtime or R
connection/launch is involved. The helper requires separate real caller Owner
authorization. Only selected bytes are verified; other six contents are not
certified. Cleanup uncertainty quarantines the same long-lived instance: no
per-request replacement, automatic reset or resource-cleanup claim is introduced.

Prior Root actual-A002 passed original34+independent6 against its frozen audit
layout after a two-line entry import/root fix. A001 ran0 tests due to import-path
failure. Neither event is a result for this new public-path port. Windows local
checks compile and explicitly skip the module; Root's separately pinned actual
public dependency overlay subsequently passed all40 cases with zero skips in
0.779s. Its47 staged source files retained exact pins, synthetic temp root was
empty and network/bind/launch/private-read/outside-write counters were zero.
This separate public-layout result permits the source/test port merge; HTTP,
Owner, lease, deployment and scientific-content acceptance remain separate.
No service, credential or runtime registration was changed by these tests.
