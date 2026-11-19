-- CLASSIFICATION-ONCE-CLAIM-001 SOURCE ONLY / NOT APPLIED. No native/R activation.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.classification014_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.de_classification_jobs') IS NULL
 OR to_regclass('cyomics_app.derived_classification_versions') IS NULL
 OR to_regclass('cyomics_app.de_classification_job_runs') IS NOT NULL
 OR to_regprocedure('cyomics_app.classification_once_guard()') IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 THEN RAISE EXCEPTION 'classification014 preflight rejected'; END IF;
END;
$preflight$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.de_classification_job_runs(
 classification_job_id uuid PRIMARY KEY REFERENCES cyomics_app.de_classification_jobs(classification_job_id),
 attempt_id uuid NOT NULL UNIQUE,owner_id uuid NOT NULL,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),request_id uuid NOT NULL,
 epoch_id uuid NOT NULL,scope_sha256 text NOT NULL CHECK(scope_sha256~'^[0-9a-f]{64}$'),
 request_payload_sha256 text NOT NULL CHECK(request_payload_sha256~'^[0-9a-f]{64}$'),
 configuration_sha256 text NOT NULL CHECK(configuration_sha256~'^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'claimed' CHECK(status IN('claimed','finished')),
 observation text NOT NULL DEFAULT 'confirmed' CHECK(observation='confirmed'),
 claimed_at timestamptz NOT NULL DEFAULT statement_timestamp(),finished_at timestamptz,
 derived_version_id uuid REFERENCES cyomics_app.derived_classification_versions(derived_version_id),
 error_code text CHECK(error_code IN('input_integrity_failed','runner_failed')),
 failure_facts_json text CHECK(octet_length(failure_facts_json) BETWEEN 1 AND 32768),failure_facts_sha256 text,
 CHECK((status='claimed' AND finished_at IS NULL AND derived_version_id IS NULL AND error_code IS NULL AND failure_facts_json IS NULL AND failure_facts_sha256 IS NULL)
 OR(status='finished' AND finished_at IS NOT NULL AND finished_at>=claimed_at AND
  ((derived_version_id IS NOT NULL AND error_code IS NULL AND failure_facts_json IS NULL AND failure_facts_sha256 IS NULL)
  OR(derived_version_id IS NULL AND error_code IS NOT NULL AND failure_facts_json IS NOT NULL AND failure_facts_sha256 IS NOT NULL
   AND failure_facts_sha256=encode(sha256(convert_to(failure_facts_json,'UTF8')),'hex')))))
);
ALTER TABLE cyomics_app.de_classification_job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.de_classification_job_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY classification_run_owner_select ON cyomics_app.de_classification_job_runs FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.de_classification_jobs j
  WHERE j.classification_job_id=de_classification_job_runs.classification_job_id AND j.owner_id=de_classification_job_runs.owner_id
  AND j.project_id=de_classification_job_runs.project_id AND j.request_id=de_classification_job_runs.request_id));
CREATE POLICY classification_run_owner_insert ON cyomics_app.de_classification_job_runs FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.de_classification_jobs j
  WHERE j.classification_job_id=de_classification_job_runs.classification_job_id AND j.owner_id=de_classification_job_runs.owner_id
  AND j.project_id=de_classification_job_runs.project_id AND j.request_id=de_classification_job_runs.request_id));
CREATE POLICY classification_run_owner_update ON cyomics_app.de_classification_job_runs FOR UPDATE TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.de_classification_jobs j
  WHERE j.classification_job_id=de_classification_job_runs.classification_job_id AND j.owner_id=de_classification_job_runs.owner_id
  AND j.project_id=de_classification_job_runs.project_id AND j.request_id=de_classification_job_runs.request_id))
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.de_classification_jobs j
  WHERE j.classification_job_id=de_classification_job_runs.classification_job_id AND j.owner_id=de_classification_job_runs.owner_id
  AND j.project_id=de_classification_job_runs.project_id AND j.request_id=de_classification_job_runs.request_id));
CREATE FUNCTION cyomics_app.classification_once_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE j cyomics_app.de_classification_jobs;v cyomics_app.derived_classification_versions;n jsonb;f jsonb;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable classification attempt'; END IF;
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 THEN RAISE EXCEPTION 'classification attempt Owner rejected'; END IF;
 SELECT * INTO j FROM cyomics_app.de_classification_jobs WHERE classification_job_id=NEW.classification_job_id
  AND owner_id=NEW.owner_id AND project_id=NEW.project_id AND request_id=NEW.request_id;
 IF j.classification_job_id IS NULL OR NEW.request_payload_sha256 IS DISTINCT FROM j.request_payload_sha256
 OR NEW.observation IS DISTINCT FROM 'confirmed'
 THEN RAISE EXCEPTION 'classification attempt admission rejected'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status IS DISTINCT FROM 'claimed' OR NEW.finished_at IS NOT NULL OR NEW.derived_version_id IS NOT NULL
  OR NEW.error_code IS NOT NULL OR NEW.failure_facts_json IS NOT NULL OR NEW.failure_facts_sha256 IS NOT NULL
  OR NEW.claimed_at<transaction_timestamp() OR NEW.claimed_at>clock_timestamp()
  OR EXISTS(SELECT 1 FROM cyomics_app.derived_classification_versions d WHERE d.owner_id=NEW.owner_id AND d.project_id=NEW.project_id AND d.request_id=NEW.request_id)
  THEN RAISE EXCEPTION 'classification attempt already has result'; END IF;
  RETURN NEW;
 END IF;
 IF OLD.status IS DISTINCT FROM 'claimed' OR NEW.status IS DISTINCT FROM 'finished'
 OR ROW(NEW.classification_job_id,NEW.attempt_id,NEW.owner_id,NEW.project_id,NEW.request_id,NEW.epoch_id,NEW.scope_sha256,
  NEW.request_payload_sha256,NEW.configuration_sha256,NEW.observation,NEW.claimed_at)
 IS DISTINCT FROM ROW(OLD.classification_job_id,OLD.attempt_id,OLD.owner_id,OLD.project_id,OLD.request_id,OLD.epoch_id,OLD.scope_sha256,
  OLD.request_payload_sha256,OLD.configuration_sha256,OLD.observation,OLD.claimed_at)
 OR NEW.finished_at IS NULL OR NEW.finished_at<OLD.claimed_at OR NEW.finished_at>clock_timestamp()
 THEN RAISE EXCEPTION 'immutable classification attempt transition'; END IF;
 IF NEW.derived_version_id IS NOT NULL THEN
  SELECT * INTO v FROM cyomics_app.derived_classification_versions WHERE derived_version_id=NEW.derived_version_id
   AND owner_id=NEW.owner_id AND project_id=NEW.project_id AND request_id=NEW.request_id;
  n:=v.native_receipt_json::jsonb;
  IF v.derived_version_id IS NULL OR NEW.error_code IS NOT NULL OR NEW.failure_facts_json IS NOT NULL OR NEW.failure_facts_sha256 IS NOT NULL
  OR v.request_payload_sha256 IS DISTINCT FROM j.request_payload_sha256 OR v.request_payload_json IS DISTINCT FROM j.request_payload_json
  OR v.source_binding_json IS DISTINCT FROM j.source_binding_json OR v.parameters_bytes IS DISTINCT FROM j.parameters_bytes OR v.core_json IS DISTINCT FROM j.core_json
  OR v.configuration_sha256 IS DISTINCT FROM NEW.configuration_sha256
  OR n->>'job_id' IS DISTINCT FROM NEW.classification_job_id::text OR n->>'attempt_id' IS DISTINCT FROM NEW.attempt_id::text
  OR n->>'epoch_id' IS DISTINCT FROM NEW.epoch_id::text OR n->>'configuration_sha256' IS DISTINCT FROM NEW.configuration_sha256
  OR n->'source_binding' IS DISTINCT FROM j.source_binding_json::jsonb OR n->>'source_binding_sha256' IS DISTINCT FROM j.source_binding_sha256
  OR n->'parameters' IS DISTINCT FROM jsonb_build_object('bytes',octet_length(j.parameters_bytes),'sha256',j.parameters_sha256)
  OR n->'script' IS DISTINCT FROM j.core_json::jsonb->'script_pin' OR n->'validator' IS DISTINCT FROM j.core_json::jsonb->'validator_pin'
  OR n->'close_confirmed' IS DISTINCT FROM 'true'::jsonb OR n->'all_six_outputs_verified' IS DISTINCT FROM 'true'::jsonb
  OR n->'classification_status' IS DISTINCT FROM v.metadata_json::jsonb->'status'
  OR v.native_receipt_sha256 IS DISTINCT FROM encode(sha256(convert_to(v.native_receipt_json,'UTF8')),'hex')
  OR n->>'manifest_sha256' IS DISTINCT FROM v.manifest_sha256
  OR n->'DE_members_evaluated' IS DISTINCT FROM v.metadata_json::jsonb->'DE_members_evaluated'
  OR n->'process_control'->'owned_tasks'->'cleanup'->'complete_for_observed_identities' IS DISTINCT FROM 'true'::jsonb
  OR n->'process_control'->'owned_tasks'->'uncertainty' IS DISTINCT FROM '[]'::jsonb
  THEN RAISE EXCEPTION 'classification persisted result proof rejected'; END IF;
 ELSE
  f:=NEW.failure_facts_json::jsonb;
  IF NEW.error_code IS NULL OR NEW.failure_facts_json IS NULL OR NEW.failure_facts_sha256 IS NULL
  OR EXISTS(SELECT 1 FROM cyomics_app.derived_classification_versions d WHERE d.owner_id=NEW.owner_id AND d.project_id=NEW.project_id AND d.request_id=NEW.request_id)
  OR f->'schemaVersion' IS DISTINCT FROM '1'::jsonb OR f->'cleanup_known' IS DISTINCT FROM 'true'::jsonb
  OR f->>'job_id' IS DISTINCT FROM NEW.classification_job_id::text OR f->>'attempt_id' IS DISTINCT FROM NEW.attempt_id::text
  OR f->>'configuration_sha256' IS DISTINCT FROM NEW.configuration_sha256
  OR f->'child_reaped' IS DISTINCT FROM 'true'::jsonb OR f->'observed_tasks_stopped' IS DISTINCT FROM 'true'::jsonb
  OR f->'owned_fds_closed' IS DISTINCT FROM 'true'::jsonb OR f->'timer_disabled' IS DISTINCT FROM 'true'::jsonb
  OR f->'signal_handler_restored' IS DISTINCT FROM 'true'::jsonb
  THEN RAISE EXCEPTION 'classification known failure proof required'; END IF;
 END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER classification_once_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.de_classification_job_runs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.classification_once_guard();
REVOKE ALL ON TABLE cyomics_app.de_classification_job_runs FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT SELECT ON TABLE cyomics_app.de_classification_job_runs TO cyomicsv2_app_runtime;
GRANT INSERT(classification_job_id,attempt_id,owner_id,project_id,request_id,epoch_id,scope_sha256,request_payload_sha256,configuration_sha256)
 ON TABLE cyomics_app.de_classification_job_runs TO cyomicsv2_app_runtime;
GRANT UPDATE(status,finished_at,derived_version_id,error_code,failure_facts_json,failure_facts_sha256)
 ON TABLE cyomics_app.de_classification_job_runs TO cyomicsv2_app_runtime;
REVOKE ALL ON FUNCTION cyomics_app.classification_once_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
-- No old object/role/credential grants, no runtime registration, no worker loop.
COMMIT;
