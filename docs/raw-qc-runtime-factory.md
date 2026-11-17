# Explicit raw-QC runtime factory

`cyomics_projects.raw_qc_runtime.create_runtime()` returns `None`, disabled,
without filesystem probes or executor imports. Importing the factory only defines
source; it does not create a global runtime. No API, worker, SQL or registration
activation is added. Unsupported hosts fail explicitly when configuration is given.

Only trusted single-thread startup code may construct this module's exact `Config`
object with existing absolute, distinct, non-overlapping 0700 inputs/staging/published
roots, explicit expected worker/source UIDs, and the five independently approved
`NativeDirectoryPin` values. Missing roots or pins fail; no present-day metadata is
automatically captured as approval. There is no JSON/HTTP configuration loader,
arbitrary import path, executable override, environment override or wire pin override.
The caller must authorize each input separately; creating a factory pair is not
Owner proof, service activation or production isolation.

`verify_current(config)` checks the fixed adjacent `r_raw_qc` package only: all four
Python files, the approved script and no extra entries. It verifies regular,
single-link, source-owner 0444/0644 files and a source-owner 0700/0755 directory,
rejects symlinks throughout paths, and verifies file identity before/after bounded
hashing. Launch Python with `-B`; caches and other package entries are rejected.
The native executable is fixed at `/usr/lib/R/bin/exec/R`, root-owned, 0755,
single-link, 14488 bytes with SHA256
`9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283`.
The five native environment directories must match the supplied approved metadata,
remain root-owned and lack group/other write permission. Stage/publish must share
a filesystem. No R invocation, procfs admission census or scientific work occurs.

The complete four-Python canonical manifest uses schemaVersion 1 / python_sources,
477 UTF-8 bytes and SHA256
`c08809cbddedaacfab382e3ab85b0196645c5a2b44218708aadfd5308303ce3c`.
It includes process_policy, output_contract and the inert initializer, rather than
only raw_qc_runner. The separate script is 4949 bytes /
`2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64`;
output_validator is 8474 bytes /
`6966be18a920ad4f7c6bba0251e84966602a5294ea2c37689904846282e68a02`.
The unchanged raw_qc_limits_v3 fourteen-field profile hash is
`fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0`.
The pair's ExecutionRegistration has the existing eight keys, native_R entry,
R version 4.6.1 and those pins. It requires the separately reviewed v3 contract.

After complete disk verification the factory explicitly imports the fixed runner,
checks initializer/runner/helper module locations and relative helper names,
fixed environment/native metadata and limits identity, then verifies disk again.
It returns an ordinary RawQCRunner and ExecutionRegistration tuple. There is no
callback or subclass. Imported-memory attestation is not provided: cached modules,
privileged source writes, ancestor replacement and check/use races remain startup
trust boundaries. Trusted startup must independently pin the entire backend release,
including this factory and contract. A source owner may still modify its own files;
these checks do not create immutable deployment or process isolation.

The unchanged executor's process policy remains development monitoring: shared-UID
NPROC/admission, observed task thresholds, per-process CPU/AS, sampled aggregate
output, namespace visibility and identity-bound pidfds do not guarantee kernel
per-job containment, unseen descendant cleanup or real-time deadlines. This source
does not claim production safety or completion of four golden research-data runs.

Run synthetic tests from a checkout with the reviewed v3 contract:

```text
python -B -m unittest discover -s backend/tests -p test_raw_qc_runtime.py -v
```

These tests patch filesystem/native metadata and construct a synthetic runner.
They are not actual R or native-factory validation. Actual Linux factory construction
requires a separate explicit QA command, approved directory pins and owned temporary
roots; it still must not invoke run(), R, input processing, services or DB.
