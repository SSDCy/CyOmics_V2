# Raw QC private-page wiring

The private project page opts in to the existing raw QC task and artifact
transports. Both transport constructors remain disabled by default. The page
uses the existing saved raw-file/dataset context and explicit family selection;
it does not add an uploader, compute statistics or automatically submit a job.

The source change is exactly two options at the existing app initialization.
Author verification passed 144 synthetic frontend checks, including 18 new
page-composition checks. Root independently verified the full candidate manifest,
the exact two-option delta, unchanged baseline dependencies and replayed those
18 checks. This is source/DOM validation, not browser layout or live E2E proof.

The backend still requires reviewed same-pool repository/reader configuration,
the approved R execution snapshot and a bounded dedicated worker process trigger.
Until the new private runtime is assembled and verified, a source merge does not
mean the current preview can calculate. Seven validated manifest entries do not
mean all seven artifact bodies have been downloaded; the explicit sample-table
preview verifies the selected artifact's actual bytes and hash.

No service, authentication session or production deployment was changed by this
source merge. Real Owner upload/queue/R/result and cross-Owner acceptance remain
separate checks.
