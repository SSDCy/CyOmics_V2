-- DIFFERENTIAL-WORKER-ONCE-001 SOURCE ONLY / NOT APPLIED / default no registration.
-- No010 mutation, old ACL/policy/roles/credentials change or automatic execution.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.differential011_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.differential_jobs') IS NULL
 OR to_regclass('cyomics_app.differential_runtime_registrations') IS NOT NULL
 OR to_regclass('cyomics_app.differential_job_runs') IS NOT NULL
 OR to_regclass('cyomics_app.differential_results') IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 THEN RAISE EXCEPTION 'differential011 preflight rejected'; END IF;
END;
$preflight$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.differential_runtime_registrations(
 registration_id uuid PRIMARY KEY,owner_id uuid NOT NULL,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 request_id uuid,scope_sha256 text NOT NULL CHECK(scope_sha256~'^[0-9a-f]{64}$'),epoch_id uuid NOT NULL UNIQUE,
 method_json text NOT NULL CHECK(octet_length(method_json) BETWEEN 1 AND 8192),
 profile_json text NOT NULL CHECK(octet_length(profile_json) BETWEEN 1 AND 32768),
 profile_sha256 text NOT NULL CHECK(profile_sha256=encode(sha256(convert_to(profile_json,'UTF8')),'hex')),
 attestation_json text NOT NULL CHECK(octet_length(attestation_json) BETWEEN 1 AND 32768),
 attestation_sha256 text NOT NULL CHECK(attestation_sha256=encode(sha256(convert_to(attestation_json,'UTF8')),'hex')),
 approved_at timestamptz NOT NULL DEFAULT statement_timestamp(),expires_at timestamptz NOT NULL,
 max_jobs integer NOT NULL CHECK(max_jobs=1),CHECK(expires_at>approved_at AND expires_at<=approved_at+interval '15 minutes')
);
CREATE TABLE cyomics_app.differential_job_runs(
 job_id uuid PRIMARY KEY REFERENCES cyomics_app.differential_jobs(job_id),attempt_id uuid NOT NULL UNIQUE,
 registration_id uuid NOT NULL UNIQUE REFERENCES cyomics_app.differential_runtime_registrations(registration_id),
 owner_id uuid NOT NULL,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),scope_sha256 text NOT NULL,
 status text NOT NULL DEFAULT 'running' CHECK(status IN('running','succeeded','failed')),
 observation text NOT NULL DEFAULT 'confirmed' CHECK(observation IN('confirmed','reconciliation_pending')),
 revision integer NOT NULL DEFAULT 2 CHECK(revision IN(2,3)),started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 finished_at timestamptz,result_id uuid,error_code text CHECK(error_code IN('input_integrity_failed','runner_failed')),
 CHECK((status='running' AND revision=2 AND finished_at IS NULL AND result_id IS NULL AND error_code IS NULL)
 OR(status='succeeded' AND revision=3 AND observation='confirmed' AND finished_at IS NOT NULL AND finished_at>=started_at AND result_id IS NOT NULL AND error_code IS NULL)
 OR(status='failed' AND revision=3 AND observation='confirmed' AND finished_at IS NOT NULL AND finished_at>=started_at AND result_id IS NULL AND error_code IS NOT NULL))
);
CREATE TABLE cyomics_app.differential_results(
 result_id uuid PRIMARY KEY,job_id uuid NOT NULL UNIQUE REFERENCES cyomics_app.differential_job_runs(job_id),
 attempt_id uuid NOT NULL,owner_id uuid NOT NULL,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 metadata_json text NOT NULL CHECK(octet_length(metadata_json) BETWEEN 1 AND 32768),
 metadata_sha256 text NOT NULL CHECK(metadata_sha256=encode(sha256(convert_to(metadata_json,'UTF8')),'hex')),
 manifest_json text NOT NULL CHECK(octet_length(manifest_json) BETWEEN 1 AND 8192),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256=encode(sha256(convert_to(manifest_json,'UTF8')),'hex')),
 receipt_json text NOT NULL CHECK(octet_length(receipt_json) BETWEEN 1 AND 131072),
 receipt_sha256 text NOT NULL CHECK(receipt_sha256=encode(sha256(convert_to(receipt_json,'UTF8')),'hex')),
 created_at timestamptz NOT NULL,r_session bytea NOT NULL,method_audit bytea NOT NULL,results bytea NOT NULL,sample_audit bytea NOT NULL,test_set bytea NOT NULL,
 CHECK(octet_length(r_session) BETWEEN 1 AND 26214400 AND octet_length(method_audit) BETWEEN 1 AND 26214400
 AND octet_length(results) BETWEEN 1 AND 26214400 AND octet_length(sample_audit) BETWEEN 1 AND 26214400 AND octet_length(test_set) BETWEEN 1 AND 26214400
 AND octet_length(r_session)::bigint+octet_length(method_audit)+octet_length(results)+octet_length(sample_audit)+octet_length(test_set)<=134217728)
);
ALTER TABLE cyomics_app.differential_job_runs ADD CONSTRAINT differential_result_fk
 FOREIGN KEY(result_id) REFERENCES cyomics_app.differential_results(result_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cyomics_app.differential_runtime_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_runtime_registrations FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_job_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_results FORCE ROW LEVEL SECURITY;
CREATE POLICY differential_registry_owner ON cyomics_app.differential_runtime_registrations FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.projects p WHERE p.project_id=differential_runtime_registrations.project_id AND p.owner_id=differential_runtime_registrations.owner_id));
CREATE POLICY differential_run_owner_select ON cyomics_app.differential_job_runs FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_jobs j WHERE j.job_id=differential_job_runs.job_id AND j.project_id=differential_job_runs.project_id AND j.owner_id=differential_job_runs.owner_id));
CREATE POLICY differential_run_owner_insert ON cyomics_app.differential_job_runs FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_jobs j WHERE j.job_id=differential_job_runs.job_id AND j.project_id=differential_job_runs.project_id AND j.owner_id=differential_job_runs.owner_id));
CREATE POLICY differential_run_owner_update ON cyomics_app.differential_job_runs FOR UPDATE TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id()) WITH CHECK(owner_id=cyomics_app.current_user_id());
CREATE POLICY differential_result_owner_select ON cyomics_app.differential_results FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_job_runs r WHERE r.job_id=differential_results.job_id AND r.owner_id=differential_results.owner_id AND r.project_id=differential_results.project_id));
CREATE POLICY differential_result_owner_insert ON cyomics_app.differential_results FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_job_runs r WHERE r.job_id=differential_results.job_id AND r.owner_id=differential_results.owner_id AND r.project_id=differential_results.project_id));

CREATE FUNCTION cyomics_app.differential_registry_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'postgres' OR session_user<>'postgres' OR NEW.approved_at IS DISTINCT FROM statement_timestamp()
 OR NOT EXISTS(SELECT 1 FROM cyomics_app.projects p WHERE p.project_id=NEW.project_id AND p.owner_id=NEW.owner_id)
 OR NEW.profile_json::jsonb->>'contract' IS DISTINCT FROM 'differential_runtime_profile_v0.1'
 OR NOT((NEW.request_id IS NOT NULL AND (NOT(NEW.profile_json::jsonb ? 'claim_policy')
    OR NEW.profile_json::jsonb->'claim_policy' IS NOT DISTINCT FROM '"exact_request"'::jsonb))
   OR(NEW.request_id IS NULL AND NEW.profile_json::jsonb->'claim_policy' IS NOT DISTINCT FROM '"one_new_job_in_scope"'::jsonb))
 OR NEW.profile_json::jsonb->>'method_sha256' IS DISTINCT FROM encode(sha256(convert_to(NEW.method_json,'UTF8')),'hex')
 OR NEW.profile_json::jsonb->>'epoch_id' IS DISTINCT FROM NEW.epoch_id::text
 OR NEW.profile_json::jsonb->>'library_files_sha256' IS DISTINCT FROM NEW.method_json::jsonb->>'library_files_sha256'
 OR NEW.profile_json::jsonb->>'source_manifest_sha256' IS NULL OR NEW.profile_json::jsonb->>'environment_sha256' IS NULL
 OR NEW.attestation_json::jsonb->>'metadata_origin' IS DISTINCT FROM 'actual_A_root_verified'
 OR NEW.attestation_json::jsonb->'uid' IS DISTINCT FROM '1000'::jsonb
 OR NEW.attestation_json::jsonb->>'startup_scope_sha256' IS DISTINCT FROM NEW.scope_sha256
 OR NEW.attestation_json::jsonb->>'source_manifest_sha256' IS DISTINCT FROM NEW.profile_json::jsonb->>'source_manifest_sha256'
 THEN RAISE EXCEPTION 'explicit trusted differential registration required'; END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER differential_registry_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.differential_runtime_registrations
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.differential_registry_guard();

CREATE FUNCTION cyomics_app.differential_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE j cyomics_app.differential_jobs;g cyomics_app.differential_runtime_registrations;
BEGIN
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 OR TG_OP='DELETE' THEN RAISE EXCEPTION 'differential run rejected'; END IF;
 SELECT * INTO j FROM cyomics_app.differential_jobs WHERE job_id=NEW.job_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 SELECT * INTO g FROM cyomics_app.differential_runtime_registrations WHERE registration_id=NEW.registration_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 IF j.job_id IS NULL OR g.registration_id IS NULL OR NEW.scope_sha256 IS DISTINCT FROM g.scope_sha256
 OR NOT((g.request_id IS NOT NULL AND g.request_id IS NOT DISTINCT FROM j.request_id
    AND(NOT(g.profile_json::jsonb ? 'claim_policy') OR g.profile_json::jsonb->'claim_policy' IS NOT DISTINCT FROM '"exact_request"'::jsonb))
   OR(g.request_id IS NULL AND g.profile_json::jsonb->'claim_policy' IS NOT DISTINCT FROM '"one_new_job_in_scope"'::jsonb))
 OR g.method_json IS DISTINCT FROM j.method_json
 OR j.created_at<=g.approved_at OR g.expires_at<=clock_timestamp() OR g.approved_at>clock_timestamp() OR g.max_jobs<>1
 THEN RAISE EXCEPTION 'new registered differential admission required'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'running' OR NEW.revision<>2 OR NEW.observation<>'confirmed' OR NEW.started_at IS DISTINCT FROM statement_timestamp()
  THEN RAISE EXCEPTION 'initial run required'; END IF;
 ELSE
  IF(NEW.job_id,NEW.attempt_id,NEW.registration_id,NEW.owner_id,NEW.project_id,NEW.scope_sha256,NEW.started_at)
   IS DISTINCT FROM(OLD.job_id,OLD.attempt_id,OLD.registration_id,OLD.owner_id,OLD.project_id,OLD.scope_sha256,OLD.started_at)
  OR OLD.status<>'running' OR OLD.revision<>2 OR OLD.observation<>'confirmed' OR NEW.revision<>3
  OR NEW.status NOT IN('succeeded','failed') OR NEW.observation<>'confirmed'
  THEN RAISE EXCEPTION 'single exact terminal CAS required'; END IF;
  IF NEW.status='succeeded' AND NOT EXISTS(SELECT 1 FROM cyomics_app.differential_results d WHERE d.result_id=NEW.result_id AND d.job_id=NEW.job_id AND d.attempt_id=NEW.attempt_id AND d.owner_id=NEW.owner_id AND d.project_id=NEW.project_id)
  THEN RAISE EXCEPTION 'atomic five-output result required'; END IF;
 END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER differential_run_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.differential_job_runs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.differential_run_guard();

CREATE FUNCTION cyomics_app.differential_result_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE r cyomics_app.differential_job_runs;j cyomics_app.differential_jobs;g cyomics_app.differential_runtime_registrations;m jsonb;a jsonb;c jsonb;v jsonb;b bytea;n text;names text[];
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 THEN RAISE EXCEPTION 'immutable differential result'; END IF;
 SELECT * INTO r FROM cyomics_app.differential_job_runs WHERE job_id=NEW.job_id AND attempt_id=NEW.attempt_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id AND status='running' AND observation='confirmed' AND revision=2;
 SELECT * INTO j FROM cyomics_app.differential_jobs WHERE job_id=NEW.job_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 SELECT * INTO g FROM cyomics_app.differential_runtime_registrations WHERE registration_id=r.registration_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 m:=NEW.metadata_json::jsonb;a:=NEW.manifest_json::jsonb;c:=NEW.receipt_json::jsonb;
 IF r.job_id IS NULL OR j.job_id IS NULL OR g.registration_id IS NULL OR g.expires_at<=clock_timestamp()
 OR m->>'contract' IS DISTINCT FROM 'differential_result_v0.1' OR m->'schemaVersion' IS DISTINCT FROM '1'::jsonb
 OR m->>'job_id' IS DISTINCT FROM NEW.job_id::text OR m->>'result_id' IS DISTINCT FROM NEW.result_id::text
 OR m->>'attempt_id' IS DISTINCT FROM NEW.attempt_id::text OR m->>'registration_id' IS DISTINCT FROM r.registration_id::text
 OR m->>'created_by' IS DISTINCT FROM NEW.owner_id::text OR m->>'project_id' IS DISTINCT FROM NEW.project_id::text
 OR(m->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR NEW.created_at<r.started_at OR NEW.created_at>clock_timestamp()
 OR m->>'request_id' IS DISTINCT FROM j.request_id::text OR m->>'admission_binding_sha256' IS DISTINCT FROM j.binding_sha256
 OR m->'selection' IS DISTINCT FROM (j.request_json::jsonb-'request_id')
 OR m->'method' IS DISTINCT FROM j.method_json::jsonb OR m->'input_manifest' IS DISTINCT FROM j.input_manifest_json::jsonb
 OR m->'source_analysis_ready' IS DISTINCT FROM 'false'::jsonb OR m->'stored' IS DISTINCT FROM 'true'::jsonb
 OR m->'artifacts' IS DISTINCT FROM a OR m->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
 OR m->>'receipt_sha256' IS DISTINCT FROM NEW.receipt_sha256
 OR NEW.receipt_json::jsonb->>'job_id' IS DISTINCT FROM NEW.job_id::text
 OR NEW.receipt_json::jsonb->>'attempt_id' IS DISTINCT FROM NEW.attempt_id::text
 OR NEW.receipt_json::jsonb->'close_confirmed' IS DISTINCT FROM 'true'::jsonb
 OR NEW.receipt_json::jsonb->'all_five_outputs_verified' IS DISTINCT FROM 'true'::jsonb
 OR NEW.receipt_json::jsonb->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
 OR c->>'epoch_id' IS DISTINCT FROM g.epoch_id::text
 OR c->>'configuration_sha256' IS DISTINCT FROM encode(sha256(convert_to(
  format('{"attempt_id":"%s","epoch_id":"%s","inputs":%s,"job_id":"%s"}',NEW.attempt_id,g.epoch_id,j.input_manifest_json,NEW.job_id),'UTF8')),'hex')
 OR c->'inputs' IS DISTINCT FROM j.input_manifest_json::jsonb
 OR c->'script' IS DISTINCT FROM j.method_json::jsonb->'script'
 OR c->'native' IS DISTINCT FROM j.method_json::jsonb->'native'
 OR c->>'library_manifest_sha256' IS DISTINCT FROM j.method_json::jsonb->>'library_files_sha256'
 OR c->'published' IS DISTINCT FROM 'false'::jsonb
 OR c->'source_analysis_ready' IS DISTINCT FROM 'false'::jsonb
 OR c->'process_control'->'owned_tasks'->'cleanup'->'complete_for_observed_identities' IS DISTINCT FROM 'true'::jsonb
 OR c->'process_control'->'owned_tasks'->'uncertainty' IS DISTINCT FROM '[]'::jsonb
 THEN RAISE EXCEPTION 'differential result binding rejected'; END IF;
 IF m->>'exit_code' IS NULL OR m->>'exit_code' NOT IN('0','3','4')
 OR m->>'scientific_status' IS DISTINCT FROM (CASE m->>'exit_code' WHEN '0' THEN 'COMPUTED_CANDIDATE' WHEN '3' THEN 'NO_TESTABLE_ROWS' WHEN '4' THEN 'REVIEW_REQUIRED_LIMMA_WARNING' END)
 OR m->'exit_code' IS DISTINCT FROM NEW.receipt_json::jsonb->'exit_code'
 OR m->>'scientific_status' IS DISTINCT FROM c->>'status'
 OR jsonb_typeof(m->'exit_code') IS DISTINCT FROM 'number'
 OR jsonb_typeof(m->'warning_count') IS DISTINCT FROM 'number'
 OR(m->>'warning_count')!~'^(0|[1-9][0-9]*)$'
 OR m->'warning_count' IS DISTINCT FROM c->'warning_count'
 OR m->'review_required' IS DISTINCT FROM to_jsonb((m->>'warning_count')::bigint>0 OR m->>'exit_code'='4')
 OR m->'review_required' IS DISTINCT FROM c->'review_required'
 THEN RAISE EXCEPTION 'differential exit semantics rejected'; END IF;
 SELECT array_agg(x.value->>'name' ORDER BY x.ordinality) INTO names FROM jsonb_array_elements(a) WITH ORDINALITY x(value,ordinality);
 IF names IS DISTINCT FROM ARRAY['R-session.txt','method-audit.tsv','results.tsv','sample-audit.tsv','test-set.tsv'] THEN RAISE EXCEPTION 'exact five outputs required'; END IF;
 FOR v IN SELECT value FROM jsonb_array_elements(a) LOOP
  n:=v->>'name';b:=CASE n WHEN 'R-session.txt' THEN NEW.r_session WHEN 'method-audit.tsv' THEN NEW.method_audit WHEN 'results.tsv' THEN NEW.results WHEN 'sample-audit.tsv' THEN NEW.sample_audit WHEN 'test-set.tsv' THEN NEW.test_set END;
  IF(v->>'bytes')::integer IS DISTINCT FROM octet_length(b) OR v->>'sha256' IS DISTINCT FROM encode(sha256(b),'hex')
  THEN RAISE EXCEPTION 'differential artifact pin rejected'; END IF;
 END LOOP;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER differential_result_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.differential_results
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.differential_result_guard();
REVOKE ALL ON TABLE cyomics_app.differential_runtime_registrations,cyomics_app.differential_job_runs,cyomics_app.differential_results FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT SELECT ON TABLE cyomics_app.differential_runtime_registrations,cyomics_app.differential_job_runs,cyomics_app.differential_results TO cyomicsv2_app_runtime;
GRANT INSERT(job_id,attempt_id,registration_id,owner_id,project_id,scope_sha256) ON cyomics_app.differential_job_runs TO cyomicsv2_app_runtime;
GRANT UPDATE(status,observation,revision,finished_at,result_id,error_code) ON cyomics_app.differential_job_runs TO cyomicsv2_app_runtime;
GRANT INSERT ON TABLE cyomics_app.differential_results TO cyomicsv2_app_runtime;
REVOKE ALL ON FUNCTION cyomics_app.differential_registry_guard(),cyomics_app.differential_run_guard(),cyomics_app.differential_result_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
-- No ordinary-app registry INSERT/UPDATE, worker role grant,010 mutation, roles,
-- old-table policy/ACL change, SECURITY DEFINER, installation or execution.
COMMIT;
