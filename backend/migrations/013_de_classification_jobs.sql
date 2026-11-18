-- CLASSIFICATION-ADMISSION-001 SOURCE ONLY / NOT APPLIED. Immutable intent, not an execution claim.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.classification013_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.differential_results') IS NULL
 OR to_regclass('cyomics_app.derived_classification_versions') IS NULL
 OR to_regclass('cyomics_app.de_classification_jobs') IS NOT NULL
 OR to_regprocedure('cyomics_app.classification_queue_guard()') IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 THEN RAISE EXCEPTION 'classification013 preflight rejected'; END IF;
END;
$preflight$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.de_classification_jobs(
 classification_job_id uuid PRIMARY KEY,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),owner_id uuid NOT NULL,
 request_id uuid NOT NULL,request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 8192),
 request_payload_json text NOT NULL CHECK(octet_length(request_payload_json) BETWEEN 1 AND 16384),
 request_payload_sha256 text NOT NULL CHECK(request_payload_sha256=encode(sha256(convert_to(request_payload_json,'UTF8')),'hex')),
 source_result_id uuid NOT NULL REFERENCES cyomics_app.differential_results(result_id),
 source_manifest_sha256 text NOT NULL CHECK(source_manifest_sha256~'^[0-9a-f]{64}$'),
 source_binding_json text NOT NULL CHECK(octet_length(source_binding_json) BETWEEN 1 AND 16384),
 source_binding_sha256 text NOT NULL CHECK(source_binding_sha256=encode(sha256(convert_to(source_binding_json,'UTF8')),'hex')),
 parameters_bytes bytea NOT NULL CHECK(octet_length(parameters_bytes) BETWEEN 1 AND 4096),
 parameters_sha256 text NOT NULL CHECK(parameters_sha256=encode(sha256(parameters_bytes),'hex')),
 core_json text NOT NULL CHECK(octet_length(core_json) BETWEEN 1 AND 8192),
 status text NOT NULL DEFAULT 'queued' CHECK(status='queued'),created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 UNIQUE(owner_id,project_id,request_id)
);
ALTER TABLE cyomics_app.de_classification_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.de_classification_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY classification_job_owner_select ON cyomics_app.de_classification_jobs FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_results p
  WHERE p.result_id=de_classification_jobs.source_result_id AND p.owner_id=de_classification_jobs.owner_id AND p.project_id=de_classification_jobs.project_id));
CREATE POLICY classification_job_owner_insert ON cyomics_app.de_classification_jobs FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_results p
  WHERE p.result_id=de_classification_jobs.source_result_id AND p.owner_id=de_classification_jobs.owner_id AND p.project_id=de_classification_jobs.project_id));
CREATE FUNCTION cyomics_app.classification_queue_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE p cyomics_app.differential_results;j cyomics_app.differential_jobs;
 b jsonb;k jsonb;r jsonb;params jsonb;i jsonb;expected jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable classification admission'; END IF;
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 OR NEW.status IS DISTINCT FROM 'queued' OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
 THEN RAISE EXCEPTION 'classification admission rejected'; END IF;
 SELECT * INTO p FROM cyomics_app.differential_results WHERE result_id=NEW.source_result_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 SELECT * INTO j FROM cyomics_app.differential_jobs WHERE job_id=p.job_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 IF p.result_id IS NULL OR j.job_id IS NULL OR NOT EXISTS(SELECT 1 FROM cyomics_app.differential_job_runs dr
  WHERE dr.job_id=p.job_id AND dr.attempt_id=p.attempt_id AND dr.status='succeeded' AND dr.result_id=p.result_id
  AND dr.registration_id::text=p.metadata_json::jsonb->>'registration_id')
 THEN RAISE EXCEPTION 'classification saved parent missing'; END IF;
 b:=NEW.source_binding_json::jsonb;k:=NEW.core_json::jsonb;r:=NEW.request_json::jsonb;params:=r->'parameters';i:=NEW.request_payload_json::jsonb;
 expected:=jsonb_build_object('schemaVersion',1,'contract','de_parent_binding_v0.1','owner_id',NEW.owner_id::text,'project_id',NEW.project_id::text,
  'source_result_id',p.result_id::text,'source_job_id',p.job_id::text,'source_attempt_id',p.attempt_id::text,
  'source_registration_id',p.metadata_json::jsonb->>'registration_id','source_request_id',j.request_id::text,
  'source_contrast_id',j.input_snapshot_json::jsonb->'contrast'->>'contrast_id','admission_binding_sha256',j.binding_sha256,
  'input_snapshot_sha256',j.input_snapshot_sha256,'source_metadata_sha256',p.metadata_sha256,'source_receipt_sha256',p.receipt_sha256,
  'source_manifest_sha256',p.manifest_sha256,'source_artifacts',p.manifest_json::jsonb,'source_status',p.metadata_json::jsonb->>'scientific_status',
  'source_warning_count',p.metadata_json::jsonb->'warning_count','source_method_sha256',j.method_sha256,'contrast_direction','numerator_minus_denominator');
 IF b IS DISTINCT FROM expected OR NEW.source_manifest_sha256 IS DISTINCT FROM p.manifest_sha256
 OR p.metadata_sha256 IS DISTINCT FROM encode(sha256(convert_to(p.metadata_json,'UTF8')),'hex')
 OR p.manifest_sha256 IS DISTINCT FROM encode(sha256(convert_to(p.manifest_json,'UTF8')),'hex')
 OR p.receipt_sha256 IS DISTINCT FROM encode(sha256(convert_to(p.receipt_json,'UTF8')),'hex')
 OR p.metadata_json::jsonb->>'receipt_sha256' IS DISTINCT FROM p.receipt_sha256
 OR p.metadata_json::jsonb->>'manifest_sha256' IS DISTINCT FROM p.manifest_sha256
 OR p.metadata_json::jsonb->'artifacts' IS DISTINCT FROM p.manifest_json::jsonb
 THEN RAISE EXCEPTION 'classification parent binding rejected'; END IF;
 IF k IS DISTINCT FROM '{"contract":"de_classification_v0.1","script_pin":{"bytes":10335,"sha256":"65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421"},"validator_pin":{"bytes":12074,"sha256":"2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584"}}'::jsonb
 OR r IS DISTINCT FROM jsonb_build_object('request_id',NEW.request_id::text,'source_result_id',NEW.source_result_id::text,'parameters',params)
 OR params IS DISTINCT FROM jsonb_build_object('schema_version','de_classification_v0.1','input_scale','log2',
  'adjusted_p_max',params->'adjusted_p_max','absolute_log2fc_min',params->'absolute_log2fc_min')
 OR jsonb_typeof(params->'adjusted_p_max') IS DISTINCT FROM 'string' OR jsonb_typeof(params->'absolute_log2fc_min') IS DISTINCT FROM 'string'
 OR (params->>'adjusted_p_max')!~'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
 OR (params->>'absolute_log2fc_min')!~'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
 THEN RAISE EXCEPTION 'classification original parameter tokens required'; END IF;
 IF (params->>'adjusted_p_max')::double precision<=0 OR (params->>'adjusted_p_max')::double precision>1
 OR (params->>'absolute_log2fc_min')::double precision<0
 OR (params->>'absolute_log2fc_min')::double precision='Infinity'::double precision
 OR NEW.parameters_bytes IS DISTINCT FROM convert_to(E'key\tvalue\nschema_version\tde_classification_v0.1\ninput_scale\tlog2\nadjusted_p_max\t'
  ||(params->>'adjusted_p_max')||E'\nabsolute_log2fc_min\t'||(params->>'absolute_log2fc_min')||E'\n','UTF8')
 OR i IS DISTINCT FROM jsonb_build_object('owner_id',NEW.owner_id::text,'project_id',NEW.project_id::text,'request_id',NEW.request_id::text,
  'source_result_id',NEW.source_result_id::text,'source_manifest_sha256',NEW.source_manifest_sha256,'source_binding_sha256',NEW.source_binding_sha256,
  'parameters_sha256',NEW.parameters_sha256,'core',k)
 THEN RAISE EXCEPTION 'classification admission intent rejected'; END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER classification_queue_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.de_classification_jobs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.classification_queue_guard();
REVOKE ALL ON TABLE cyomics_app.de_classification_jobs FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT SELECT ON TABLE cyomics_app.de_classification_jobs TO cyomicsv2_app_runtime;
GRANT INSERT(classification_job_id,project_id,owner_id,request_id,request_json,request_payload_json,request_payload_sha256,
 source_result_id,source_manifest_sha256,source_binding_json,source_binding_sha256,parameters_bytes,parameters_sha256,core_json)
 ON TABLE cyomics_app.de_classification_jobs TO cyomicsv2_app_runtime;
REVOKE ALL ON FUNCTION cyomics_app.classification_queue_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
-- No old-table ACL/role changes; no UPDATE/DELETE/TRUNCATE/worker grants or claim schema.
COMMIT;
