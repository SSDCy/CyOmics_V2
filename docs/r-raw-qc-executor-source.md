# Raw QC executor source package

This package contains reviewed executor source only. It does not register a
runtime, add a route, launch a worker, connect to a database, probe a runtime or
run R on import. The package initializer contains only a docstring. Importing
the package is not a runtime availability check.

Explicit executor-module imports and execution require a dedicated Linux/POSIX
single-thread worker with the reviewed procfs/pidfd/resource support. Native
module imports can fail on Windows or unsupported systems. Such failures must
remain failures; there is no available-looking fallback or substitute executor.

`Config` and `AuthorizedInput` are trusted server-side objects, not HTTP DTOs.
Paths, input handles, native executable/directory pins and the fixed environment
must be supplied by a separately reviewed server registration and authorization
flow. This package does not establish Owner access or permit client-controlled
paths, commands, environment or resource overrides. No package-level instance or
automatic registration is provided.

## Preserved science and development policy

The R script is exactly 4949 bytes with SHA256
`2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64`.
Python handles transport, monitoring and structural output checks; the scientific
calculations remain in the approved R script. No scientific estimator changed.

The unchanged fourteen-field `raw_qc_limits_v3` envelope has SHA256
`fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0`.
The fixed development policy has SHA256
`2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede`.
The policy identity is distinct from the `process_policy.py` source-file hash.

NPROC 512 and the 480-task admission check concern the shared real UID. The 8-task
job threshold is observed monitoring, not a kernel per-job process ceiling.
CPU 45 seconds and AS 2GiB are per-process limits, not aggregate job hard quotas.
The cumulative output quota is monitored and may transiently overshoot. The 50ms
monitor interval and 2-second cleanup budget are subject to scheduling and blocking
kernel/filesystem operations rather than real-time deadline guarantees.

Only observed identity-bound processes may be signalled through verified pidfds.
There is no numeric PID/PGID/name/whole-UID kill fallback. Short-lived,
unobserved, reparented or escaped helpers may be missed; namespace visibility is
limited. Monitoring does not guarantee descendant containment or complete orphan
cleanup. Unknown observed ownership or cleanup fails publication and quarantines
the runner instance. There is no background monitor, cgroup/namespace setup or
global temporary-file sweep. Implicit R temporary-file behavior remains outside
the proven package boundary.

## Source identity and future registration

Package tests pin all three Python implementation files, the inert initializer
and the scientific script. The implementation files are `raw_qc_runner.py`,
`process_policy.py` and `output_contract.py`. Only the runner's two imports changed
to relative imports; its other AST is unchanged. The other three delivered files
are byte-identical to the approved flat-layout source.

A future approved `execution.runner` identity must cover the complete executable
source bundle, including `process_policy.py` and `output_contract.py`, and retain
the script's separate exact pin. A lone runner-file hash does not identify the
monitoring policy implementation. Initialization code should also remain pinned.
The package manifest is source-build metadata, not an execution registration or
an alias compatible with the existing BE R3/v2 wire. This change neither edits
that wire nor enables v3 registration.

## Validation boundary

Portable tests check exact source pins, import-only AST parity, the two relative
imports, an inert initializer, and static v3/policy identities. The explicit
POSIX module-import test is skipped on unsupported hosts; it does not execute R
or create a runner instance.

Earlier review covered 33 flat-layout source/stub tests, 6 independent tests and
two actual tiny-family runs with seven outputs checked. Those are prior flat
evidence, not execution of this new package. Four golden research-data runs have
not been performed. Package unit/import verification on the actual POSIX worker
and independent review remain separate gates; this packaging task does not
claim worker, HTTP, database, publication, scientific-stage or release readiness.

Run the portable package suite from a repository checkout with:

```text
python -B -m unittest discover -s backend/tests -p test_r_raw_qc_package.py -v
```

The same command on the dedicated POSIX environment exercises the explicit
package-relative import check. It does not rerun the earlier flat executor tests
or the tiny R runs. No private plans, runtime diagnostics or inputs are shipped.

Root 后续独立验证：A 的固定 UID/namespace、既有 Python 环境在离线源码快照中实跑七项公开测试及四项独立边界测试，11/11 通过、无跳过，源码前后摘要一致，I/O/进程守卫无触发。没有运行包化 R、修改资源限额或注册服务；先前 flat 执行器的 tiny 兼容证据不能代替此包的科学全链验证。
