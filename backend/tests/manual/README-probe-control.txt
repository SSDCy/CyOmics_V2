Directed A-only Root probe-control suite

From an existing reviewed A checkout:

    /path/to/reviewed/venv/bin/python -I -B backend/tests/manual/probe_control_directed_a.py

This manual suite requires the existing public Root helpers at
/home/user01/Cy_Omics_v2.0/audit/RAW-QC-LIVE-CONTROL-001/preview_plan_ops_draft.py
with SHA75ad437aa43504ceefdc94525bdce34b5f6834ab15d61575fc0741ec4d83694f.
It reads product modules from this checkout, not an old fixed source clone or
an audit candidate. It uses temporary synthetic approvals/records and checks
that production gate/loader identities are unchanged. It never calls bootstrap,
real registration, R, Auth, DB, a listener or the legacy start/save helpers.

It is deliberately named outside automatic test discovery: this is a Linux
Root-control integration suite, not a portable Windows/CI unit test. Do not
install dependencies, copy secrets or use a platform shim to make it run.
Original independent actual-A16 evidence is retained outside Git; old case and
assertion bodies are unchanged by this packaging.
