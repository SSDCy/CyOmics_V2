-- PREPROCESS-EXECUTION-001 SOURCE DRAFT ONLY. Root must approve number/apply separately.
-- No migration008 rewrite, parent DML, new role/password, service or automatic registration.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.preprocess_execution_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.preprocessing_jobs') IS NULL
 OR to_regclass('cyomics_app.preprocessing_runtime_registrations') IS NOT NULL
 OR to_regclass('cyomics_app.preprocessing_job_runs') IS NOT NULL
 OR to_regclass('cyomics_app.processed_dataset_versions') IS NOT NULL
 OR to_regclass('cyomics_app.processed_dataset_artifact_bundles') IS NOT NULL
 OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('cyomicsv2_app_runtime','cyomicsv2_raw_qc_worker_runtime','cyomicsv2_raw_qc_executor') AND (rolsuper OR rolbypassrls))
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_raw_qc_executor' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_raw_qc_worker')
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_raw_qc_worker_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR pg_has_role('cyomicsv2_raw_qc_worker_runtime','cyomicsv2_raw_qc_executor','MEMBER')
 OR NOT pg_has_role('cyomicsv2_raw_qc_worker_runtime','cyomicsv2_raw_qc_worker','USAGE')
 THEN RAISE EXCEPTION 'preprocessing execution preflight rejected'; END IF;
END $preflight$;

CREATE TABLE cyomics_app.preprocessing_runtime_registrations(
 registration_id uuid PRIMARY KEY,
 project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 proposal_sha256 text NOT NULL CHECK(proposal_sha256 ~ '^[0-9a-f]{64}$'),
 profile_json text NOT NULL CHECK(octet_length(profile_json) BETWEEN 1 AND 32768),
 profile_sha256 text NOT NULL CHECK(profile_sha256=encode(sha256(convert_to(profile_json,'UTF8')),'hex')),
 attestation_json text NOT NULL CHECK(octet_length(attestation_json) BETWEEN 1 AND 32768),
 attestation_sha256 text NOT NULL CHECK(attestation_sha256=encode(sha256(convert_to(attestation_json,'UTF8')),'hex')),
 startup_scope_sha256 text NOT NULL CHECK(startup_scope_sha256 ~ '^[0-9a-f]{64}$'),
 epoch_id uuid NOT NULL UNIQUE,
 approved_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 expires_at timestamptz NOT NULL CHECK(expires_at>approved_at),
 max_jobs integer NOT NULL CHECK(max_jobs BETWEEN 1 AND 256)
);
CREATE TABLE cyomics_app.preprocessing_job_runs(
 job_id uuid PRIMARY KEY REFERENCES cyomics_app.preprocessing_jobs(job_id),
 attempt_id uuid NOT NULL UNIQUE,
 registration_id uuid NOT NULL REFERENCES cyomics_app.preprocessing_runtime_registrations(registration_id),
 runtime_binding_sha256 text NOT NULL CHECK(runtime_binding_sha256 ~ '^[0-9a-f]{64}$'),
 status text NOT NULL CHECK(status IN ('running','succeeded','failed')),
 revision bigint NOT NULL DEFAULT 2 CHECK(revision>=2),
 observation text NOT NULL DEFAULT 'confirmed' CHECK(observation IN ('confirmed','reconciliation_pending')),
 started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 finished_at timestamptz,
 error_code text CHECK(error_code IN ('input_integrity_failed','execution_unavailable','resource_limit','runner_failed','output_invalid','publish_integrity_failed','empty_not_analyzable','worker_interrupted')),
 receipt_json text CHECK(octet_length(receipt_json) BETWEEN 1 AND 131072),
 receipt_sha256 text CHECK(receipt_sha256=encode(sha256(convert_to(receipt_json,'UTF8')),'hex')),
 result_version_id uuid,
 manifest_sha256 text CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 CHECK((status='running' AND finished_at IS NULL AND result_version_id IS NULL AND error_code IS NULL)
    OR (status='failed' AND finished_at>=started_at AND result_version_id IS NULL AND error_code IS NOT NULL AND observation='confirmed')
    OR (status='succeeded' AND finished_at>=started_at AND result_version_id IS NOT NULL AND error_code IS NULL AND observation='confirmed' AND receipt_json IS NOT NULL AND receipt_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL)),
 CHECK((receipt_json IS NULL)=(receipt_sha256 IS NULL))
);
CREATE TABLE cyomics_app.processed_dataset_versions(
 processed_dataset_version_id uuid PRIMARY KEY,
 job_id uuid NOT NULL UNIQUE REFERENCES cyomics_app.preprocessing_job_runs(job_id),
 project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 parent_dataset_version_id uuid NOT NULL REFERENCES cyomics_app.dataset_versions(dataset_version_id),
 file_id uuid NOT NULL REFERENCES cyomics_app.raw_uploads(file_id),
 created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 metadata_json text NOT NULL CHECK(octet_length(metadata_json) BETWEEN 1 AND 32768),
 metadata_sha256 text NOT NULL CHECK(metadata_sha256=encode(sha256(convert_to(metadata_json,'UTF8')),'hex')),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE cyomics_app.preprocessing_job_runs ADD CONSTRAINT preprocessing_result_version_fk
 FOREIGN KEY(result_version_id) REFERENCES cyomics_app.processed_dataset_versions(processed_dataset_version_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE cyomics_app.processed_dataset_artifact_bundles(
 processed_dataset_version_id uuid PRIMARY KEY REFERENCES cyomics_app.processed_dataset_versions(processed_dataset_version_id),
 manifest_json text NOT NULL CHECK(octet_length(manifest_json) BETWEEN 1 AND 8192),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256=encode(sha256(convert_to(manifest_json,'UTF8')),'hex')),
 filter_log bytea NOT NULL, row_map bytea NOT NULL, summary bytea NOT NULL,
 raw_retained bytea NOT NULL, working_matrix bytea NOT NULL,
 original_blank_mask bytea NOT NULL, original_zero_mask bytea NOT NULL,
 working_na_mask bytea NOT NULL, imputed_mask bytea NOT NULL,
 r_session_info bytea NOT NULL, parameters bytea NOT NULL,
 CHECK(octet_length(filter_log) BETWEEN 1 AND 26214400 AND octet_length(row_map) BETWEEN 1 AND 26214400
 AND octet_length(summary) BETWEEN 1 AND 26214400 AND octet_length(raw_retained) BETWEEN 1 AND 26214400
 AND octet_length(working_matrix) BETWEEN 1 AND 26214400 AND octet_length(original_blank_mask) BETWEEN 1 AND 26214400
 AND octet_length(original_zero_mask) BETWEEN 1 AND 26214400 AND octet_length(working_na_mask) BETWEEN 1 AND 26214400
 AND octet_length(imputed_mask) BETWEEN 1 AND 26214400 AND octet_length(r_session_info) BETWEEN 1 AND 26214400
 AND octet_length(parameters) BETWEEN 1 AND 26214400),
 CHECK(octet_length(filter_log)::bigint+octet_length(row_map)+octet_length(summary)+octet_length(raw_retained)
 +octet_length(working_matrix)+octet_length(original_blank_mask)+octet_length(original_zero_mask)
 +octet_length(working_na_mask)+octet_length(imputed_mask)+octet_length(r_session_info)+octet_length(parameters)<=134217728)
);
ALTER TABLE cyomics_app.preprocessing_runtime_registrations OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.preprocessing_job_runs OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.processed_dataset_versions OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.processed_dataset_artifact_bundles OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.preprocessing_runtime_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.preprocessing_runtime_registrations FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.preprocessing_job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.preprocessing_job_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.processed_dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.processed_dataset_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.processed_dataset_artifact_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.processed_dataset_artifact_bundles FORCE ROW LEVEL SECURITY;
CREATE POLICY preprocess_registry_executor ON cyomics_app.preprocessing_runtime_registrations FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
CREATE POLICY preprocess_admission_executor ON cyomics_app.preprocessing_jobs FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
-- Existing RawQC capsule policies remain byte-exact; add only new-job-bound parents.
CREATE POLICY preprocess_executor_project ON cyomics_app.projects FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs q WHERE q.project_id=projects.project_id AND q.owner_id=projects.owner_id));
CREATE POLICY preprocess_executor_dataset ON cyomics_app.dataset_versions FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs q WHERE q.project_id=dataset_versions.project_id AND q.dataset_version_id=dataset_versions.dataset_version_id AND q.file_id=dataset_versions.file_id));
CREATE POLICY preprocess_executor_raw ON cyomics_app.raw_uploads FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs q WHERE q.project_id=raw_uploads.project_id AND q.file_id=raw_uploads.file_id));
CREATE POLICY preprocess_runs_executor_read ON cyomics_app.preprocessing_job_runs FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
CREATE POLICY preprocess_runs_executor_insert ON cyomics_app.preprocessing_job_runs FOR INSERT TO cyomicsv2_raw_qc_executor WITH CHECK(true);
CREATE POLICY preprocess_runs_executor_update ON cyomics_app.preprocessing_job_runs FOR UPDATE TO cyomicsv2_raw_qc_executor USING(true) WITH CHECK(true);
CREATE POLICY preprocess_versions_executor_read ON cyomics_app.processed_dataset_versions FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
CREATE POLICY preprocess_versions_executor_insert ON cyomics_app.processed_dataset_versions FOR INSERT TO cyomicsv2_raw_qc_executor WITH CHECK(true);
CREATE POLICY preprocess_bundles_executor_read ON cyomics_app.processed_dataset_artifact_bundles FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
CREATE POLICY preprocess_bundles_executor_insert ON cyomics_app.processed_dataset_artifact_bundles FOR INSERT TO cyomicsv2_raw_qc_executor WITH CHECK(true);
CREATE POLICY preprocess_registry_owner_refs ON cyomics_app.preprocessing_runtime_registrations FOR SELECT TO cyomicsv2_app_runtime
 USING(EXISTS(SELECT 1 FROM cyomics_app.preprocessing_job_runs r JOIN cyomics_app.preprocessing_jobs j ON j.job_id=r.job_id
 WHERE r.registration_id=preprocessing_runtime_registrations.registration_id AND j.owner_id=cyomics_app.current_user_id()));
CREATE POLICY preprocess_runs_owner ON cyomics_app.preprocessing_job_runs FOR SELECT TO cyomicsv2_app_runtime
 USING(EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs j WHERE j.job_id=preprocessing_job_runs.job_id AND j.owner_id=cyomics_app.current_user_id()));
CREATE POLICY preprocess_versions_owner ON cyomics_app.processed_dataset_versions FOR SELECT TO cyomicsv2_app_runtime
 USING(created_by=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs j WHERE j.job_id=processed_dataset_versions.job_id AND j.owner_id=processed_dataset_versions.created_by AND j.project_id=processed_dataset_versions.project_id AND j.dataset_version_id=processed_dataset_versions.parent_dataset_version_id AND j.file_id=processed_dataset_versions.file_id));
CREATE POLICY preprocess_bundles_owner ON cyomics_app.processed_dataset_artifact_bundles FOR SELECT TO cyomicsv2_app_runtime
 USING(EXISTS(SELECT 1 FROM cyomics_app.processed_dataset_versions v WHERE v.processed_dataset_version_id=processed_dataset_artifact_bundles.processed_dataset_version_id AND v.created_by=cyomics_app.current_user_id()));

CREATE FUNCTION cyomics_app.preprocessing_registration_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'postgres' OR session_user<>'postgres'
 OR NEW.profile_json::jsonb->>'contract' IS DISTINCT FROM 'preprocessing_runtime_profile_v1'
 OR NEW.approved_at IS DISTINCT FROM statement_timestamp()
 OR NEW.profile_json::jsonb->>'proposal_sha256' IS DISTINCT FROM NEW.proposal_sha256
 OR NEW.attestation_json::jsonb->>'metadata_origin' IS DISTINCT FROM 'actual_A_root_verified'
 OR NEW.attestation_json::jsonb->>'uid' IS DISTINCT FROM '1000'
 OR NEW.attestation_json::jsonb->>'source_manifest_sha256' IS DISTINCT FROM NEW.profile_json::jsonb->>'source_manifest_sha256'
 OR NEW.attestation_json::jsonb->>'startup_scope_sha256' IS DISTINCT FROM NEW.startup_scope_sha256
 THEN RAISE EXCEPTION 'trusted preprocessing registration required'; END IF;
 RETURN NEW;
END $body$;
CREATE TRIGGER preprocessing_registration_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.preprocessing_runtime_registrations
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.preprocessing_registration_guard();
CREATE FUNCTION cyomics_app.preprocessing_immutable_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'cyomicsv2_raw_qc_executor' THEN RAISE EXCEPTION 'immutable processed object'; END IF;
 RETURN NEW;
END $body$;
CREATE TRIGGER processed_version_immutable BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.processed_dataset_versions
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.preprocessing_immutable_guard();
CREATE TRIGGER processed_bundle_immutable BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.processed_dataset_artifact_bundles
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.preprocessing_immutable_guard();

-- Internal capsule helpers never expose a generic Owner or dynamic-SQL parameter.
CREATE FUNCTION cyomics_app.preprocessing_admission_valid(k uuid) RETURNS boolean
LANGUAGE sql SET search_path=pg_catalog AS $body$
 SELECT EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs j
 JOIN cyomics_app.projects p ON p.project_id=j.project_id AND p.owner_id=j.owner_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=j.project_id AND d.dataset_version_id=j.dataset_version_id AND d.file_id=j.file_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=j.project_id AND f.file_id=j.file_id
 WHERE j.job_id=k AND j.status='queued' AND f.status='stored' AND f.format='proteinGroups_txt'
 AND d.representation='raw_backed' AND NOT d.parsed_table_materialized
 AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256
 AND j.source_json::jsonb->>'source_sha256'=f.sha256 AND j.source_json::jsonb->>'source_bytes'=f.bytes::text
 AND j.source_json::jsonb->>'config_sha256'=d.config_sha256 AND j.source_json::jsonb->>'report_sha256'=d.report_sha256
 AND j.execution_json::jsonb->'registered'='false'::jsonb)
$body$;
CREATE FUNCTION cyomics_app.preprocessing_csv_cell(v text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
 SELECT v
$body$;
CREATE FUNCTION cyomics_app.preprocessing_selected_tsv(k uuid) RETURNS bytea
LANGUAGE sql SET search_path=pg_catalog AS $body$
 SELECT convert_to(E'column_ordinal\tname_raw\tquant_family\tsample_label_raw\n'||string_agg(
 (c->>'ordinal')||E'\t'||cyomics_app.preprocessing_csv_cell(c->>'name_raw')||E'\t'
 ||cyomics_app.preprocessing_csv_cell(c->>'quant_family')||E'\t'||cyomics_app.preprocessing_csv_cell(c->>'sample_label_raw')||E'\n','' ORDER BY (c->>'ordinal')::integer),'UTF8')
 FROM cyomics_app.preprocessing_jobs j JOIN cyomics_app.dataset_versions d ON d.dataset_version_id=j.dataset_version_id AND d.project_id=j.project_id,
 LATERAL jsonb_array_elements(d.report_json::jsonb->'sample_columns') AS q(c)
 WHERE j.job_id=k AND c->>'quant_family'=j.quant_family
$body$;
CREATE FUNCTION cyomics_app.preprocessing_run_record(k uuid,a uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $body$
 SELECT jsonb_build_object('admission',to_jsonb(j),'run',to_jsonb(r),'registration',to_jsonb(g))
 FROM cyomics_app.preprocessing_jobs j JOIN cyomics_app.preprocessing_job_runs r ON r.job_id=j.job_id
 JOIN cyomics_app.preprocessing_runtime_registrations g ON g.registration_id=r.registration_id
 WHERE (k IS NULL OR j.job_id=k) AND r.attempt_id=a AND cyomics_app.preprocessing_admission_valid(j.job_id)
$body$;
CREATE FUNCTION cyomics_app.preprocessing_claim(k uuid,a uuid,g uuid,scope_sha text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
DECLARE j cyomics_app.preprocessing_jobs; reg cyomics_app.preprocessing_runtime_registrations; rb text;
BEGIN
 IF a IS NULL OR g IS NULL OR scope_sha IS NULL OR scope_sha !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'preprocessing claim rejected'; END IF;
 PERFORM pg_advisory_xact_lock(47009,1);
 IF EXISTS(SELECT 1 FROM cyomics_app.preprocessing_job_runs WHERE status='running') THEN RETURN NULL; END IF;
 SELECT * INTO reg FROM cyomics_app.preprocessing_runtime_registrations WHERE registration_id=g;
 IF NOT FOUND OR reg.expires_at<=clock_timestamp() OR reg.approved_at>clock_timestamp()
 OR reg.startup_scope_sha256<>scope_sha OR (SELECT count(*) FROM cyomics_app.preprocessing_job_runs WHERE registration_id=g)>=reg.max_jobs
 THEN RAISE EXCEPTION 'preprocessing runtime unavailable'; END IF;
 -- No historical unregistered queue is silently enrolled in a later registration.
 -- Admission is immutable; the global advisory transaction lock serializes claims.
 -- Run job_id PK and attempt_id UNIQUE provide the final duplicate-claim guard.
 SELECT q.* INTO j FROM cyomics_app.preprocessing_jobs q
 WHERE (k IS NULL OR q.job_id=k) AND q.project_id=reg.project_id
 AND q.created_at>=reg.approved_at AND q.created_at<reg.expires_at
 AND encode(sha256(convert_to(q.execution_json,'UTF8')),'hex')=reg.proposal_sha256
 AND NOT EXISTS(SELECT 1 FROM cyomics_app.preprocessing_job_runs old WHERE old.job_id=q.job_id)
 AND cyomics_app.preprocessing_admission_valid(q.job_id)
 ORDER BY q.created_at,q.job_id LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 rb='{"attempt_id":'||to_jsonb(a::text)::text||',"binding_sha256":'||to_jsonb(j.binding_sha256)::text
 ||',"contract":"preprocessing_runtime_binding_v1","job_id":'||to_jsonb(j.job_id::text)::text
 ||',"profile_sha256":'||to_jsonb(reg.profile_sha256)::text||',"proposal_sha256":'||to_jsonb(reg.proposal_sha256)::text
 ||',"registration_id":'||to_jsonb(g::text)::text||'}';
 INSERT INTO cyomics_app.preprocessing_job_runs(job_id,attempt_id,registration_id,runtime_binding_sha256,status)
 VALUES(j.job_id,a,g,encode(sha256(convert_to(rb,'UTF8')),'hex'),'running');
 RETURN cyomics_app.preprocessing_run_record(j.job_id,a);
END $body$;
CREATE FUNCTION cyomics_app.preprocessing_bound_source(k uuid,a uuid)
RETURNS TABLE(record jsonb,dataset_json jsonb,raw_meta jsonb,content bytea)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $body$
 SELECT cyomics_app.preprocessing_run_record(j.job_id,r.attempt_id),to_jsonb(d),
 jsonb_build_object('file_id',f.file_id,'project_id',f.project_id,'format',f.format,'bytes',f.bytes,'sha256',f.sha256,'status',f.status),f.content
 FROM cyomics_app.preprocessing_jobs j JOIN cyomics_app.preprocessing_job_runs r ON r.job_id=j.job_id
 JOIN cyomics_app.dataset_versions d ON d.dataset_version_id=j.dataset_version_id AND d.project_id=j.project_id
 JOIN cyomics_app.raw_uploads f ON f.file_id=j.file_id AND f.project_id=j.project_id
 WHERE j.job_id=k AND r.attempt_id=a AND r.status='running' AND r.observation='confirmed'
 AND cyomics_app.preprocessing_admission_valid(j.job_id)
$body$;
CREATE FUNCTION cyomics_app.preprocessing_finish(k uuid,a uuid,rev bigint,target text,code text,failure_receipt text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
DECLARE r cyomics_app.preprocessing_job_runs; fr jsonb; ff jsonb; changed_job uuid;
BEGIN
 IF k IS NULL OR a IS NULL OR rev IS NULL OR rev<2 OR target IS NULL THEN RAISE EXCEPTION 'preprocessing CAS input rejected'; END IF;
 SELECT * INTO r FROM cyomics_app.preprocessing_job_runs WHERE job_id=k AND attempt_id=a FOR UPDATE;
 IF NOT FOUND OR r.status IS DISTINCT FROM 'running' OR r.revision IS DISTINCT FROM rev OR cyomics_app.preprocessing_admission_valid(k) IS NOT TRUE THEN RETURN NULL; END IF;
 IF target='reconciliation_pending' AND code IS NULL AND failure_receipt IS NULL THEN
  UPDATE cyomics_app.preprocessing_job_runs SET observation='reconciliation_pending',revision=revision+1 WHERE job_id=k AND attempt_id=a AND revision=rev RETURNING job_id INTO changed_job;
  IF NOT FOUND OR changed_job IS DISTINCT FROM k THEN RAISE EXCEPTION 'preprocessing pending CAS unconfirmed'; END IF;
 ELSIF target='failed' AND code IN ('input_integrity_failed','execution_unavailable','resource_limit','runner_failed','output_invalid','publish_integrity_failed','empty_not_analyzable','worker_interrupted') THEN
  IF failure_receipt IS NULL OR octet_length(failure_receipt)>131072 THEN RAISE EXCEPTION 'trusted failure facts required'; END IF;
  fr=failure_receipt::jsonb;ff=fr->'facts';
  IF fr->>'kind' IS DISTINCT FROM 'log2_failure_receipt_v1' OR fr->>'job_id' IS DISTINCT FROM k::text
  OR fr->>'attempt_id' IS DISTINCT FROM a::text OR fr->>'runtime_binding_sha256' IS DISTINCT FROM r.runtime_binding_sha256
  OR fr->>'code' IS DISTINCT FROM code OR jsonb_typeof(ff) IS DISTINCT FROM 'object'
  OR NOT coalesce(ff ?& ARRAY['schemaVersion','job_id','attempt_id','configuration_sha256','popen_attempted','launch_state','process_identity','exit_code','child_reaped','observed_tasks_stopped','owned_fds_closed','timer_disabled','signal_handler_restored','process_evidence_sha256','cleanup_known'],false)
  OR (SELECT count(*) FROM jsonb_object_keys(ff))<>15
  OR NOT coalesce(ff->>'launch_state' IN ('not_started','started'),false)
  OR ff->'schemaVersion' IS DISTINCT FROM '1'::jsonb
  OR ff->>'job_id' IS DISTINCT FROM k::text OR ff->>'attempt_id' IS DISTINCT FROM a::text
  OR NOT coalesce(ff->>'configuration_sha256' ~ '^[0-9a-f]{64}$',false)
  OR ff->'cleanup_known' IS DISTINCT FROM 'true'::jsonb OR ff->'child_reaped' IS DISTINCT FROM 'true'::jsonb
  OR ff->'observed_tasks_stopped' IS DISTINCT FROM 'true'::jsonb OR ff->'owned_fds_closed' IS DISTINCT FROM 'true'::jsonb
  OR ff->'timer_disabled' IS DISTINCT FROM 'true'::jsonb OR ff->'signal_handler_restored' IS DISTINCT FROM 'true'::jsonb
  OR NOT coalesce((
   (ff->>'launch_state'='not_started' AND ff->'popen_attempted'='false'::jsonb
    AND ff->'process_identity'='null'::jsonb AND ff->'exit_code'='null'::jsonb
    AND ff->'process_evidence_sha256'='null'::jsonb)
   OR (ff->>'launch_state'='started' AND ff->'popen_attempted'='true'::jsonb
    AND jsonb_typeof(ff->'process_identity')='object'
    AND (SELECT count(*) FROM jsonb_object_keys(ff->'process_identity'))=4
    AND (ff->'process_identity') ?& ARRAY['pid','starttick','proc_inode','uid']
    AND jsonb_typeof(ff->'process_identity'->'pid')='number'
    AND (ff->'process_identity'->>'pid')::numeric BETWEEN 1 AND 2147483647
    AND trunc((ff->'process_identity'->>'pid')::numeric)=(ff->'process_identity'->>'pid')::numeric
    AND jsonb_typeof(ff->'process_identity'->'starttick')='number'
    AND (ff->'process_identity'->>'starttick')::numeric BETWEEN 1 AND 9223372036854775807
    AND trunc((ff->'process_identity'->>'starttick')::numeric)=(ff->'process_identity'->>'starttick')::numeric
    AND jsonb_typeof(ff->'process_identity'->'proc_inode')='number'
    AND (ff->'process_identity'->>'proc_inode')::numeric BETWEEN 1 AND 9223372036854775807
    AND trunc((ff->'process_identity'->>'proc_inode')::numeric)=(ff->'process_identity'->>'proc_inode')::numeric
    AND ff->'process_identity'->'uid'='1000'::jsonb
    AND jsonb_typeof(ff->'exit_code')='number'
    AND (ff->>'exit_code')::numeric BETWEEN -255 AND 255
    AND trunc((ff->>'exit_code')::numeric)=(ff->>'exit_code')::numeric
    AND coalesce(ff->>'process_evidence_sha256' ~ '^[0-9a-f]{64}$',false))
  ),false)
  THEN RAISE EXCEPTION 'preprocessing failure evidence rejected'; END IF;
  UPDATE cyomics_app.preprocessing_job_runs SET status='failed',finished_at=clock_timestamp(),error_code=code,observation='confirmed',revision=revision+1,
   receipt_json=failure_receipt,receipt_sha256=encode(sha256(convert_to(failure_receipt,'UTF8')),'hex') WHERE job_id=k AND attempt_id=a AND revision=rev RETURNING job_id INTO changed_job;
  IF NOT FOUND OR changed_job IS DISTINCT FROM k THEN RAISE EXCEPTION 'preprocessing failed CAS unconfirmed'; END IF;
 ELSE RAISE EXCEPTION 'preprocessing transition rejected'; END IF;
 RETURN cyomics_app.preprocessing_run_record(k,a);
END $body$;

CREATE FUNCTION cyomics_app.preprocessing_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF current_user<>'cyomicsv2_raw_qc_executor' OR TG_OP='DELETE' THEN RAISE EXCEPTION 'preprocessing run mutation rejected'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'running' OR NEW.revision<>2 OR NEW.observation<>'confirmed' OR NEW.finished_at IS NOT NULL
  OR NEW.error_code IS NOT NULL OR NEW.receipt_json IS NOT NULL OR NEW.result_version_id IS NOT NULL
  THEN RAISE EXCEPTION 'preprocessing initial run rejected'; END IF;
 ELSE
  IF (NEW.job_id,NEW.attempt_id,NEW.registration_id,NEW.runtime_binding_sha256,NEW.started_at)
    IS DISTINCT FROM (OLD.job_id,OLD.attempt_id,OLD.registration_id,OLD.runtime_binding_sha256,OLD.started_at)
  OR OLD.status<>'running' OR OLD.observation<>'confirmed' OR NEW.revision<>OLD.revision+1
  OR (NEW.status='running' AND NEW.observation<>'reconciliation_pending')
  THEN RAISE EXCEPTION 'preprocessing run CAS rejected'; END IF;
 END IF;
 RETURN NEW;
END $body$;
CREATE TRIGGER preprocessing_run_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.preprocessing_job_runs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.preprocessing_run_guard();

-- All11 payloads are parameters in one transaction. No path/ref-only publication.
CREATE FUNCTION cyomics_app.preprocessing_publish(k uuid,a uuid,rev bigint,v uuid,meta text,manifest text,receipt text,
 p_filter bytea,p_rows bytea,p_summary bytea,p_raw bytea,p_work bytea,p_blank bytea,p_zero bytea,p_na bytea,p_imputed bytea,p_session bytea,p_parameters bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
DECLARE r cyomics_app.preprocessing_job_runs; j cyomics_app.preprocessing_jobs; g cyomics_app.preprocessing_runtime_registrations;
 m jsonb; md jsonb; rc jsonb; item jsonb; names text[]; bodies bytea[]; i integer; total bigint:=0; mh text; rh text; expected_runtime jsonb; changed_job uuid;
BEGIN
 IF k IS NULL OR a IS NULL OR rev IS NULL OR rev<2 OR v IS NULL OR meta IS NULL OR manifest IS NULL OR receipt IS NULL
 THEN RAISE EXCEPTION 'preprocessing publication CAS input rejected'; END IF;
 SELECT * INTO r FROM cyomics_app.preprocessing_job_runs WHERE job_id=k AND attempt_id=a FOR UPDATE;
 IF NOT FOUND OR r.status IS DISTINCT FROM 'running' OR r.observation IS DISTINCT FROM 'confirmed' OR r.revision IS DISTINCT FROM rev OR cyomics_app.preprocessing_admission_valid(k) IS NOT TRUE THEN RETURN NULL; END IF;
 SELECT * INTO j FROM cyomics_app.preprocessing_jobs WHERE job_id=k;
 SELECT * INTO g FROM cyomics_app.preprocessing_runtime_registrations WHERE registration_id=r.registration_id;
 IF v IS NULL OR meta IS NULL OR manifest IS NULL OR receipt IS NULL OR octet_length(meta)>32768 OR octet_length(manifest)>8192 OR octet_length(receipt)>131072 THEN RAISE EXCEPTION 'preprocessing publication rejected'; END IF;
 m=manifest::jsonb;md=meta::jsonb;rc=receipt::jsonb;
 mh=encode(sha256(convert_to(manifest,'UTF8')),'hex');rh=encode(sha256(convert_to(receipt,'UTF8')),'hex');
 names=ARRAY['R-session-info.txt','filter-log.tsv','imputed-mask.tsv','original-blank-mask.tsv','original-zero-mask.tsv','parameters.json','raw-retained.tsv','row-map.tsv','summary.tsv','working-NA-mask.tsv','working-matrix.tsv'];
 bodies=ARRAY[p_session,p_filter,p_imputed,p_blank,p_zero,p_parameters,p_raw,p_rows,p_summary,p_na,p_work];
 IF jsonb_typeof(m) IS DISTINCT FROM 'array' OR jsonb_array_length(m)<>11 THEN RAISE EXCEPTION 'preprocessing manifest rejected'; END IF;
 FOR i IN 1..11 LOOP
  item=m->(i-1);
  IF bodies[i] IS NULL OR octet_length(bodies[i]) NOT BETWEEN 1 AND 26214400 OR jsonb_typeof(item)<>'object'
  OR (SELECT count(*) FROM jsonb_object_keys(item))<>4 OR item->>'name' IS DISTINCT FROM names[i]
  OR item->'bytes' IS DISTINCT FROM to_jsonb(octet_length(bodies[i]))
  OR item->>'sha256' IS DISTINCT FROM encode(sha256(bodies[i]),'hex')
  OR item->>'media_type' IS DISTINCT FROM (CASE WHEN names[i]='parameters.json' THEN 'application/json' WHEN names[i]='R-session-info.txt' THEN 'text/plain; charset=utf-8' ELSE 'text/tab-separated-values; charset=utf-8' END)
  THEN RAISE EXCEPTION 'preprocessing artifact rejected'; END IF;
  total=total+octet_length(bodies[i]);
 END LOOP;
 IF total>134217728 THEN RAISE EXCEPTION 'preprocessing artifact total rejected'; END IF;
 expected_runtime=jsonb_build_object('registration_id',g.registration_id,'profile_sha256',g.profile_sha256,
 'runtime_binding_sha256',r.runtime_binding_sha256,'attestation_sha256',g.attestation_sha256,'receipt_sha256',rh);
 IF jsonb_typeof(md)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(md))<>20
 OR md->'schemaVersion' IS DISTINCT FROM '2'::jsonb OR md->>'processed_dataset_version_id' IS DISTINCT FROM v::text
 OR md->>'job_id' IS DISTINCT FROM k::text OR md->>'attempt_id' IS DISTINCT FROM a::text
 OR md->>'project_id' IS DISTINCT FROM j.project_id::text OR md->>'parent_dataset_version_id' IS DISTINCT FROM j.dataset_version_id::text
 OR md->>'file_id' IS DISTINCT FROM j.file_id::text OR md->>'created_by' IS DISTINCT FROM j.owner_id::text
 OR md->'source' IS DISTINCT FROM j.source_json::jsonb OR md->'parameters' IS DISTINCT FROM j.params_json::jsonb
 OR md->>'quant_family' IS DISTINCT FROM j.quant_family OR md->'steps' IS DISTINCT FROM j.params_json::jsonb->'steps'
 OR md->>'parameters_sha256' IS DISTINCT FROM encode(sha256(convert_to(j.params_json,'UTF8')),'hex')
 OR md->>'proposal_sha256' IS DISTINCT FROM g.proposal_sha256 OR md->'analysis_ready' IS DISTINCT FROM 'false'::jsonb
 OR md->'artifacts' IS DISTINCT FROM m OR md->>'manifest_sha256' IS DISTINCT FROM mh OR md->'runtime' IS DISTINCT FROM expected_runtime
 OR rc->>'attempt_id' IS DISTINCT FROM a::text OR NOT coalesce(rc->>'configuration_sha256' ~ '^[0-9a-f]{64}$',false)
 OR rc->>'job_id' IS DISTINCT FROM k::text OR rc->>'family' IS DISTINCT FROM j.quant_family
 OR rc->'source'->>'sha256' IS DISTINCT FROM j.source_json::jsonb->>'source_sha256'
 OR rc->'selected'->>'sha256' IS DISTINCT FROM encode(sha256(cyomics_app.preprocessing_selected_tsv(k)),'hex')
 OR rc->'parameters'->>'sha256' IS DISTINCT FROM md->>'parameters_sha256'
 OR rc->>'manifest_sha256' IS DISTINCT FROM mh OR rc->'exit_code' IS DISTINCT FROM '0'::jsonb
 OR rc->'all_11_outputs_verified' IS DISTINCT FROM 'true'::jsonb OR rc->'close_confirmed' IS DISTINCT FROM 'true'::jsonb
 OR rc->'script' IS DISTINCT FROM j.execution_json::jsonb->'script' OR rc->'validator' IS DISTINCT FROM j.execution_json::jsonb->'validator'
 OR rc->'native' IS DISTINCT FROM j.execution_json::jsonb->'executable'
 OR rc->'flags' IS DISTINCT FROM jsonb_build_array(j.params_json::jsonb->'filter_flags'->'reverse',j.params_json::jsonb->'filter_flags'->'contaminant',j.params_json::jsonb->'filter_flags'->'site_only')
 OR md->'tool' IS DISTINCT FROM jsonb_build_object('program','R','r_version',j.execution_json::jsonb->'r_version',
 'script_sha256',j.execution_json::jsonb->'script'->'sha256','validator_sha256',j.execution_json::jsonb->'validator'->'sha256',
 'runner_sha256',j.execution_json::jsonb->'runner'->'sha256','native_sha256',j.execution_json::jsonb->'executable'->'sha256',
 'process_policy_sha256',j.execution_json::jsonb->'process_policy_sha256')
 OR (md->>'created_at')::timestamptz<r.started_at OR (md->>'created_at')::timestamptz>clock_timestamp()

 THEN RAISE EXCEPTION 'preprocessing result binding rejected'; END IF;
 INSERT INTO cyomics_app.processed_dataset_versions(processed_dataset_version_id,job_id,project_id,parent_dataset_version_id,file_id,created_by,created_at,metadata_json,metadata_sha256,manifest_sha256)
 VALUES(v,k,j.project_id,j.dataset_version_id,j.file_id,j.owner_id,(md->>'created_at')::timestamptz,meta,encode(sha256(convert_to(meta,'UTF8')),'hex'),mh);
 INSERT INTO cyomics_app.processed_dataset_artifact_bundles VALUES(v,manifest,mh,p_filter,p_rows,p_summary,p_raw,p_work,p_blank,p_zero,p_na,p_imputed,p_session,p_parameters);
 UPDATE cyomics_app.preprocessing_job_runs SET status='succeeded',revision=revision+1,observation='confirmed',finished_at=(md->>'created_at')::timestamptz,
 receipt_json=receipt,receipt_sha256=rh,result_version_id=v,manifest_sha256=mh WHERE job_id=k AND attempt_id=a AND revision=rev RETURNING job_id INTO changed_job;
 IF NOT FOUND OR changed_job IS DISTINCT FROM k THEN RAISE EXCEPTION 'preprocessing publication CAS unconfirmed'; END IF;
 RETURN cyomics_app.preprocessing_run_record(k,a);
END $body$;

-- Immutable registration/versions/bundle have no runtime mutation grants.
ALTER FUNCTION cyomics_app.preprocessing_registration_guard() OWNER TO cyomicsv2_app_owner;
ALTER FUNCTION cyomics_app.preprocessing_immutable_guard() OWNER TO cyomicsv2_app_owner;
ALTER FUNCTION cyomics_app.preprocessing_run_guard() OWNER TO cyomicsv2_app_owner;
ALTER FUNCTION cyomics_app.preprocessing_csv_cell(text) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_selected_tsv(uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_admission_valid(uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_run_record(uuid,uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_claim(uuid,uuid,uuid,text) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_bound_source(uuid,uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_finish(uuid,uuid,bigint,text,text,text) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.preprocessing_publish(uuid,uuid,bigint,uuid,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea) OWNER TO cyomicsv2_raw_qc_executor;
REVOKE ALL ON TABLE cyomics_app.preprocessing_runtime_registrations,cyomics_app.preprocessing_job_runs,cyomics_app.processed_dataset_versions,cyomics_app.processed_dataset_artifact_bundles FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker;
REVOKE ALL ON FUNCTION cyomics_app.preprocessing_registration_guard(),cyomics_app.preprocessing_immutable_guard(),cyomics_app.preprocessing_run_guard(),cyomics_app.preprocessing_csv_cell(text),cyomics_app.preprocessing_selected_tsv(uuid),cyomics_app.preprocessing_admission_valid(uuid),
 cyomics_app.preprocessing_run_record(uuid,uuid),cyomics_app.preprocessing_claim(uuid,uuid,uuid,text),cyomics_app.preprocessing_bound_source(uuid,uuid),
 cyomics_app.preprocessing_finish(uuid,uuid,bigint,text,text,text),cyomics_app.preprocessing_publish(uuid,uuid,bigint,uuid,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea)
 FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker;
GRANT SELECT(job_id,attempt_id,registration_id,runtime_binding_sha256,status,revision,observation,started_at,finished_at,error_code,receipt_sha256,result_version_id,manifest_sha256) ON cyomics_app.preprocessing_job_runs TO cyomicsv2_app_runtime;
GRANT SELECT ON cyomics_app.processed_dataset_versions,cyomics_app.processed_dataset_artifact_bundles TO cyomicsv2_app_runtime;
GRANT SELECT(registration_id,profile_sha256,attestation_sha256) ON cyomics_app.preprocessing_runtime_registrations TO cyomicsv2_app_runtime;
GRANT SELECT ON cyomics_app.preprocessing_runtime_registrations,cyomics_app.preprocessing_job_runs,cyomics_app.processed_dataset_versions,cyomics_app.processed_dataset_artifact_bundles TO cyomicsv2_raw_qc_executor;
GRANT INSERT(job_id,attempt_id,registration_id,runtime_binding_sha256,status) ON cyomics_app.preprocessing_job_runs TO cyomicsv2_raw_qc_executor;
GRANT UPDATE(status,revision,observation,finished_at,error_code,receipt_json,receipt_sha256,result_version_id,manifest_sha256) ON cyomics_app.preprocessing_job_runs TO cyomicsv2_raw_qc_executor;
GRANT INSERT ON cyomics_app.processed_dataset_versions,cyomics_app.processed_dataset_artifact_bundles TO cyomicsv2_raw_qc_executor;
-- Exact extension of the existing fixed executor capsule, not broad parent grants.
GRANT SELECT(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,source_json,quant_family,params_json,execution_json,status,created_at) ON cyomics_app.preprocessing_jobs TO cyomicsv2_raw_qc_executor;
GRANT SELECT(parser_contract,config_snapshot,report_json,representation,parsed_table_materialized,created_at) ON cyomics_app.dataset_versions TO cyomicsv2_raw_qc_executor;
GRANT EXECUTE ON FUNCTION cyomics_app.preprocessing_run_record(uuid,uuid),cyomics_app.preprocessing_claim(uuid,uuid,uuid,text),cyomics_app.preprocessing_bound_source(uuid,uuid),
 cyomics_app.preprocessing_finish(uuid,uuid,bigint,text,text,text),cyomics_app.preprocessing_publish(uuid,uuid,bigint,uuid,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea)
 TO cyomicsv2_raw_qc_worker;
COMMIT;
