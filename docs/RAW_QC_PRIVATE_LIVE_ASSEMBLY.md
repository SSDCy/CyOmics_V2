# RAW-QC-LIVE-ASSEMBLY-PREP-001 — narrow API binding, default off

This candidate changes only app.py, adds raw_qc_api_assembly.py and its focused test/doc. No public source/Git/state/A configuration is modified. No service, SSH, database, R, Owner session or real input is accessed. Sites owns frontend app.js flags; this candidate does not edit frontend. The actual R4 FIRST_JOB_CONFIRMED_STOP receipt is historical evidence of one synthetic ordinary worker/claim/R/seven-file/CAS chain, not a real Owner/page test. Earlier gap-plan references to failed R2 are superseded for that one synthetic run, not silently rewritten.

## Implemented small source interface

`create_app(..., raw_qc_api_config=None)` retains the old explicit repo/reader DI unchanged. Supplying a valid trusted `RawQCAPIConfig(execution_json,publish_root,expected_uid)` additionally creates RawQCRepository and one long-lived RawQCArtifactReader with the factory's final original FixedPool. Pure config validation runs before clients/pools and rejects mixing this mode with explicit raw_qc_repository/raw_qc_artifact_reader. The unchanged production_gate still runs in its original branch before FixedPool selection; binding occurs afterward, before Authority construction. The resulting RawQCJobs and RawQCArtifactHTTP receive the same real Authority and same repository. Neither R runtime/runner nor a WorkerPool is created in ASGI.

Default configNone performs no component import/read/probe in the new helper; app public defaults and settings AUTH_POLICY_VERIFIED/RUNTIME_START_APPROVED remainFalse. This config does not grant startup approval, validate a real Owner, change source gates or authorize an alternate transport. New private control entry must use the already reviewed explicit startup-approval mechanism, bind fresh control/source hashes and confirm real Auth policy before ordinary production startup. Calling production_gate while its flags remainFalse raises; this candidate does not bypass that through exec, settings patch or a new HTTP/env flag. Existing test DI is not real activation evidence.

The full8key execution canonical JSON is frozen to the R4 synthetic factory identity: script2072a20e/4949B,runner c08809cb/477B,output-validator6966be18/8474B,native9c572a70/14488B,R4.6.1,native_R,v3/fe1e05ac. Every byte must match, including runner/executable/validator, because the generic execution contract alone checks only their shape. The new private slice's fixed proposed published path is `/home/user01/Cy_Omics_v2.0/runtime/raw-qc-live-001/published`; path aliases/old production/UID changes are refused. This path has not been created, inspected or approved for execution. Reader later performs its existing descriptor/UID/hash checks; constructor validation is not filesystem proof.

New worker roots must be an exclusive fresh control/inputs/staging/published/receipts set under `/home/user01/Cy_Omics_v2.0/runtime/raw-qc-live-001`, each0700 and same-filesystem staging/published. Do not point the web run at an already-confirmed synthetic checkpoint: it would recover the same prior attempt, not consume a new job. Root must review/create/check this new identity separately. Worker startup uses original runtime.Config/WorkerRoots and checks factory registration.snapshot against the exact API execution canonical JSON, including fresh software/native/source pins. API binding alone does not verify these roots or run R.

The single Reader and its HTTP handler remain attached for the application lifetime. No per-request Reader recreation or quarantine reset is added. Same app pool is used for Owner/RLS enqueue/read and existing project/import operations; the worker's distinct fixed WorkerPool/lazy credential loading remains exclusively in its dedicated ordinary MainThread process. No token/password/source bytes are handed to the worker by this candidate.

## Current exact source evidence

app.py create_app:181-216 owns original production gate/pool/Authority/mount. Repository FixedPool:22-74 lazily acquires fixed app-role connections; RawQCRepository:135-235 performs per-request Owner/source binding and returns public job DTO. ExecutionRegistration.snapshot at contract:189 validates the snapshot; runtime.verify_current:130-172 checks original source/native/root pins without running R. dispatch_once:70-164 constructs ordinary WorkerPool/source/exporter/runner/worker only in a dedicated loop. RawQCArtifactReader:160-205 is a long-lived serialized reader that quarantines cleanup uncertainty. Current browser app.js:68 does not yet pass the two QC flags; server.mjs:66-87 also defaults both flags/request dependencies off. This document refers to read source; it is not a running-listener attestation.

## Not implemented here: automatic one-time web launch

Only this API binding and Sites flags would create queued jobs, not start computation. Root explicitly approved a controlled subprocess launcher as the next narrow component: ASGI may start a fixed dedicated Python MainThread once process; it must never run dispatch/R directly in the ASGI loop or use the app pool for worker computation. No launcher or HTTP/repository transaction change is implemented in this candidate. The separate LAUNCHER-CONTRACT.md gives its exact required seam and negative cases, for another developer to implement independently. Automatic web execution remainsfalse until that seam, launcher and private activation are actually reviewed/run.

The current repository enqueue only returns the same public DTO for INSERT and idempotent prior branches. A normal return confirms transaction COMMIT/status, but cleanup's nonthrowing release_closed does not provide an exact teardown observation. HTTP202 covers both new and replay queued/running jobs. Therefore202/public DTO alone cannot prove new creation or confirmed teardown and must not trigger a fresh process. No pretend created flag is inferred here.

## Ordered private slice gates

1. Independently source-review this narrow constructor/app delta and the separate ordinary once entry. Public defaults stayoff. Check same pool/Authority and all8expected execution values; no runtime actions in constructor tests.
2. Implement/review exact new-created/commit/teardown observation and the one-time launcher contract. Only a successful new insertion with confirmed cleanup may hand off once. Unknown/busy/late/close outcomes lock; no ACK/next/retry/reset.
3. Freeze an isolated release/control: current backend/frontend source pins, true only private client flags, BFF true only private flags and exact native http.request transports, typed API config and full8snapshot, reviewed root/native pins and one fixed worker startup command. Root fresh startup approval is separate; no old release edits or service switch occur here.
4. Verify new roots/checkpoint absent/queue initiallyempty and no competing producer/worker, actual UID/namespaces/DB/role/runtime gates. Ordinary global claim is not job-id selection: first single-job slice needs a reviewed exclusive producer admission and exact post-claim job/source/execution fence before R. If this cannot be enforced, STOP rather than claim a targeted execution. Root's preflight alone is not an atomic lock against arbitrary other producers.
5. Actual real Owner onePOST→confirmed enqueue→one independent ordinary process→one claim/R/CAS→Owner jobGET→all7artifact transport hashes→manual sample-qc view, plus crossOwner denial/refresh recovery. Only then may automatic single-job web execution be reported. No continuous scheduler is authorized by this slice.

Focused tests execute the candidate create_app function AST with fake constructors to verify gate order/pool/Authority/long-lived object identity. They do not exercise real FastAPI lifecycle, Auth/DB/FS or HTTP. Real integration/regression/private activation remains a separate Root/QA gate. STOP after source handoff.


## Integration review record

Independent source review passed nine original audit checks plus five added
checks. The public test file retains the eight functional constructor/gate
checks with assertions unchanged and repository-relative source paths.
The remaining inverse-source/actual-fixture audit check stays in the sealed
review evidence; it is not replaced with a fake fixture or skipped test.
All eight portable checks passed after placement. This is not eight new cases.

The original application lifespan close ordering remains unchanged: an
Authority.close failure propagates and may prevent the later pool.close call.
This source-only binding does not prove complete runtime teardown. The new
published root has not been created or activated by this merge. Enqueue
observation, process launching and real Owner/API/R acceptance remain pending.
