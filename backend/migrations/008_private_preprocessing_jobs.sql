-- PREPROCESS-QUEUE-SOURCE-001: DRAFT ONLY / NOT APPLIED / NO AUTO EXECUTION.
-- 008 is a draft number; Root must reconcile the separate FASTA draft first.
-- Queued-only admission/read; NO worker role, transitions, result or publication.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev'
    OR current_user<>'postgres' OR session_user<>'postgres'
    OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
    OR current_setting('cyomics.preprocess008_reviewed',true) IS DISTINCT FROM 'yes'
    OR current_setting('server_version_num')::integer/10000<>17
    OR to_regclass('cyomics_app.raw_qc_jobs') IS NULL
    OR to_regclass('cyomics_app.preprocessing_jobs') IS NOT NULL
    OR to_regprocedure('cyomics_app.preprocessing_queue_guard()') IS NOT NULL
    OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner'
        AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
    OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime'
        AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
    OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
        WHERE c.oid IN ('cyomics_app.projects'::regclass,'cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
        AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner'))
 THEN RAISE EXCEPTION 'preprocessing draft preflight rejected'; END IF;
END $preflight$;

CREATE TABLE cyomics_app.preprocessing_jobs(
 job_id uuid PRIMARY KEY,
 project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 dataset_version_id uuid NOT NULL REFERENCES cyomics_app.dataset_versions(dataset_version_id),
 file_id uuid NOT NULL REFERENCES cyomics_app.raw_uploads(file_id),
 owner_id uuid NOT NULL,
 request_id uuid NOT NULL,
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 binding_sha256 text NOT NULL CHECK(binding_sha256 ~ '^[0-9a-f]{64}$'),
 source_json text NOT NULL CHECK(octet_length(source_json) BETWEEN 1 AND 4096),
 quant_family text NOT NULL CHECK(quant_family IN ('Intensity','LFQ intensity')),
 params_json text NOT NULL CHECK(octet_length(params_json) BETWEEN 1 AND 4096),
 execution_json text NOT NULL CHECK(octet_length(execution_json) BETWEEN 1 AND 4096),
 status text NOT NULL DEFAULT 'queued' CHECK(status='queued'),
 created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 UNIQUE(project_id,request_id)
);
ALTER TABLE cyomics_app.preprocessing_jobs OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.preprocessing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.preprocessing_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY preprocessing_owner_select ON cyomics_app.preprocessing_jobs FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
  SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
  JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
  WHERE p.project_id=preprocessing_jobs.project_id AND p.owner_id=preprocessing_jobs.owner_id
   AND d.dataset_version_id=preprocessing_jobs.dataset_version_id AND f.file_id=preprocessing_jobs.file_id
   AND f.format='proteinGroups_txt' AND f.status='stored'
   AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256));
CREATE POLICY preprocessing_owner_insert ON cyomics_app.preprocessing_jobs FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
  SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
  JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
  WHERE p.project_id=preprocessing_jobs.project_id AND p.owner_id=preprocessing_jobs.owner_id
   AND d.dataset_version_id=preprocessing_jobs.dataset_version_id AND f.file_id=preprocessing_jobs.file_id
   AND f.format='proteinGroups_txt' AND f.status='stored'
   AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256));

CREATE FUNCTION cyomics_app.preprocessing_queue_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE d record; p jsonb; e jsonb; expected_params text; expected_source text;
 expected_execution text; selected_sha text; request_text text; binding_text text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable preprocessing queue'; END IF;
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
    OR NEW.status<>'queued' THEN RAISE EXCEPTION 'preprocessing admission rejected'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.project_id::text,47008));
 SELECT d0.*,f.bytes AS raw_bytes,f.sha256 AS raw_sha,f.status AS raw_status,f.format AS raw_format
 INTO d FROM cyomics_app.projects pr JOIN cyomics_app.dataset_versions d0 ON d0.project_id=pr.project_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=pr.project_id AND f.file_id=d0.file_id
 WHERE pr.project_id=NEW.project_id AND pr.owner_id=NEW.owner_id
  AND d0.dataset_version_id=NEW.dataset_version_id AND f.file_id=NEW.file_id;
 IF NOT FOUND OR d.raw_format<>'proteinGroups_txt' OR d.raw_status<>'stored'
    OR d.raw_bytes<>d.source_bytes OR d.raw_sha<>d.source_sha256
    OR d.parser_contract<>'protein_groups_txt_v1' OR d.representation<>'raw_backed'
    OR d.parsed_table_materialized IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'preprocessing source missing'; END IF;
 IF NOT CASE WHEN jsonb_typeof(d.report_json::jsonb->'report'->'row_count')='number'
                  AND jsonb_typeof(d.report_json::jsonb->'report'->'column_count')='number' THEN
   (d.report_json::jsonb->'report'->>'row_count')::numeric BETWEEN 1 AND 10000
   AND trunc((d.report_json::jsonb->'report'->>'row_count')::numeric)=(d.report_json::jsonb->'report'->>'row_count')::numeric
   AND (d.report_json::jsonb->'report'->>'column_count')::numeric BETWEEN 2 AND 512
   AND trunc((d.report_json::jsonb->'report'->>'column_count')::numeric)=(d.report_json::jsonb->'report'->>'column_count')::numeric
   AND (d.report_json::jsonb->'report'->>'row_count')::numeric*(d.report_json::jsonb->'report'->>'column_count')::numeric<=2000000
   ELSE false END
 THEN RAISE EXCEPTION 'preprocessing source dimensions rejected'; END IF;
 SELECT encode(sha256(convert_to('['||string_agg(
  '{"name_raw":'||(q.c->'name_raw')::text||',"ordinal":'||(q.c->'ordinal')::text||',"quant_family":'||(q.c->'quant_family')::text
  ||',"sample_label_raw":'||(q.c->'sample_label_raw')::text||'}',',' ORDER BY (q.c->>'ordinal')::integer)||']','UTF8')),'hex')
 INTO selected_sha FROM jsonb_array_elements(d.report_json::jsonb->'sample_columns') AS q(c)
 WHERE q.c->>'quant_family'=NEW.quant_family;
 IF selected_sha IS NULL THEN RAISE EXCEPTION 'preprocessing family absent'; END IF;
 expected_source:='{"column_count":'||(d.report_json::jsonb->'report'->>'column_count')
  ||',"config_sha256":"'||d.config_sha256||'","dataset_version_id":"'||NEW.dataset_version_id
  ||'","file_id":"'||NEW.file_id||'","parser_contract":"protein_groups_txt_v1","project_id":"'||NEW.project_id
  ||'","report_sha256":"'||d.report_sha256||'","row_count":'||(d.report_json::jsonb->'report'->>'row_count')
  ||',"selected_columns_sha256":"'||selected_sha||'","source_bytes":'||d.source_bytes||',"source_sha256":"'||d.source_sha256||'"}';
 IF NEW.source_json IS DISTINCT FROM expected_source THEN RAISE EXCEPTION 'preprocessing source binding rejected'; END IF;
 p:=NEW.params_json::jsonb;e:=NEW.execution_json::jsonb;
 IF (jsonb_typeof(p)<>'object' OR jsonb_typeof(p->'filter_flags')<>'object'
    OR (SELECT count(*) FROM jsonb_object_keys(p->'filter_flags'))<>3
    OR NOT ((p->'filter_flags') ?& ARRAY['reverse','contaminant','site_only'])
    OR EXISTS(SELECT 1 FROM jsonb_each(p->'filter_flags') AS b(k,v) WHERE jsonb_typeof(b.v)<>'boolean')) IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'preprocessing flags rejected'; END IF;
 expected_params:='{"blank_policy":"to_NA","filter_flags":{"contaminant":'||(p->'filter_flags'->'contaminant')::text
  ||',"reverse":'||(p->'filter_flags'->'reverse')::text||',"site_only":'||(p->'filter_flags'->'site_only')::text
  ||'},"imputation":"none","input_scale":"raw_nonnegative","log2":"positive_only","normalization":"none","output_scale":"log2","preset":"positive_log2_zero_na_no_impute_v1","pseudocount":0,"seed":null,"steps":["flags_filter","zero_and_blank_to_NA","log2_positive","normalization_none","imputation_none"],"zero_policy":"to_NA"}';
 IF NEW.params_json IS DISTINCT FROM expected_params THEN RAISE EXCEPTION 'preprocessing parameters rejected'; END IF;
 IF EXISTS(SELECT 1 FROM (VALUES('reverse','Reverse'),('contaminant','Potential contaminant'),('site_only','Only identified by site')) AS flag(k,n)
   WHERE p->'filter_flags'->flag.k='true'::jsonb AND NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(d.report_json::jsonb->'columns') AS col(c) WHERE col.c->>'name_raw'=flag.n))
 THEN RAISE EXCEPTION 'preprocessing enabled flag absent'; END IF;
 IF (jsonb_typeof(e)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(e))<>10
    OR NOT(e ?& ARRAY['contract','entry_kind','executable','limits','process_policy_sha256','r_version','registered','runner','script','validator'])
    OR e->>'contract'<>'log2_local_source_proposal_v0.1' OR e->>'entry_kind'<>'native_R'
    OR e->>'r_version'<>'R version 4.6.1' OR e->'registered'<>'false'::jsonb
    OR e->>'process_policy_sha256'<>'2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede'
    OR e->'limits'<>'{"address_space_bytes":2147483648,"cleanup_seconds":2,"core_bytes":0,"cpu_seconds":45,"job_tasks":8,"monitor_seconds":0.05,"open_files":256,"output_bytes":268435456,"per_file_bytes":67108864,"stderr_bytes":65536,"stdout_bytes":65536,"uid_admission_tasks":480,"uid_nproc_limit":512,"wall_seconds":60}'::jsonb
    OR e->'script'<>'{"bytes":11996,"sha256":"08aec68d6ee74bd09b7cc70100d10e147c527986a3d42323fe5541a3b608e787"}'::jsonb OR e->'validator'<>'{"bytes":14877,"sha256":"ba5fa3ed521fe7f97810ecdab06e1936e9dec8c9ebac45f8d2da3e3d77989d9e"}'::jsonb
    OR e->'executable'<>'{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"}'::jsonb
    OR jsonb_typeof(e->'runner')<>'object' OR (SELECT count(*) FROM jsonb_object_keys(e->'runner'))<>2
    OR NOT((e->'runner') ?& ARRAY['bytes','sha256']) OR jsonb_typeof(e->'runner'->'sha256')<>'string'
    OR e->'runner'->>'sha256' !~ '^[0-9a-f]{64}$'
    OR NOT CASE WHEN jsonb_typeof(e->'runner'->'bytes')='number' THEN
      (e->'runner'->>'bytes')::numeric BETWEEN 1 AND 262144
      AND trunc((e->'runner'->>'bytes')::numeric)=(e->'runner'->>'bytes')::numeric ELSE false END) IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'preprocessing source proposal rejected'; END IF;
 expected_execution:='{"contract":"log2_local_source_proposal_v0.1","entry_kind":"native_R","executable":{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"},"limits":{"address_space_bytes":2147483648,"cleanup_seconds":2,"core_bytes":0,"cpu_seconds":45,"job_tasks":8,"monitor_seconds":0.05,"open_files":256,"output_bytes":268435456,"per_file_bytes":67108864,"stderr_bytes":65536,"stdout_bytes":65536,"uid_admission_tasks":480,"uid_nproc_limit":512,"wall_seconds":60},"process_policy_sha256":"2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede","r_version":"R version 4.6.1","registered":false,"runner":{"bytes":'
  ||((e->'runner'->>'bytes')::numeric)::bigint||',"sha256":'||(e->'runner'->'sha256')::text||'},"script":{"bytes":11996,"sha256":"08aec68d6ee74bd09b7cc70100d10e147c527986a3d42323fe5541a3b608e787"},"validator":{"bytes":14877,"sha256":"ba5fa3ed521fe7f97810ecdab06e1936e9dec8c9ebac45f8d2da3e3d77989d9e"}}';
 IF NEW.execution_json IS DISTINCT FROM expected_execution THEN RAISE EXCEPTION 'preprocessing noncanonical proposal'; END IF;
 request_text:='{"dataset_version_id":"'||NEW.dataset_version_id||'","project_id":"'||NEW.project_id
  ||'","request":{"parameters":'||NEW.params_json||',"quant_family":'||to_jsonb(NEW.quant_family)::text
  ||',"request_id":"'||NEW.request_id||'"}}';
 binding_text:='{"contract":"preprocessing_queued_v0.1","execution":'||NEW.execution_json
  ||',"parameters":'||NEW.params_json||',"quant_family":'||to_jsonb(NEW.quant_family)::text
  ||',"request_sha256":"'||NEW.request_sha256||'","source":'||NEW.source_json||'}';
 IF NEW.request_sha256<>encode(sha256(convert_to(request_text,'UTF8')),'hex')
    OR NEW.binding_sha256<>encode(sha256(convert_to(binding_text,'UTF8')),'hex')
 THEN RAISE EXCEPTION 'preprocessing digest rejected'; END IF;
 RETURN NEW;
END $body$;
ALTER FUNCTION cyomics_app.preprocessing_queue_guard() OWNER TO cyomicsv2_app_owner;
REVOKE ALL ON FUNCTION cyomics_app.preprocessing_queue_guard() FROM PUBLIC,cyomicsv2_app_runtime;
CREATE TRIGGER preprocessing_queue_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.preprocessing_jobs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.preprocessing_queue_guard();
REVOKE ALL ON TABLE cyomics_app.preprocessing_jobs FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,
 source_json,quant_family,params_json,execution_json,status,created_at) ON cyomics_app.preprocessing_jobs TO cyomicsv2_app_runtime;
GRANT INSERT(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,
 source_json,quant_family,params_json,execution_json) ON cyomics_app.preprocessing_jobs TO cyomicsv2_app_runtime;
-- No roles/passwords/LOGIN/grants to workers, no UPDATE/DELETE/TRUNCATE privilege,
-- no parent changes, no SECDEF, no queue capacity policy, no result/download API.
COMMIT;
