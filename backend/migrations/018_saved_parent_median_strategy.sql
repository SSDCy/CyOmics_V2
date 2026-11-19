-- S1-MEDIAN-PROCESSED-STRATEGY-001:018 DRAFT ONLY, NEVER APPLIED.
-- Exact new strategy admission plus one claimed-job parent capsule. No new columns/queue.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $guard$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.median018_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regprocedure('cyomics_app.preprocessing_median_parent_bundle(uuid,uuid)') IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM pg_proc f JOIN pg_roles r ON r.oid=f.proowner
   WHERE f.oid=to_regprocedure('cyomics_app.preprocessing_queue_guard()') AND r.rolname='cyomicsv2_app_owner'
   AND encode(sha256(convert_to(f.prosrc,'UTF8')),'hex') IN ('41d48dbb79b9488f7d8f739e3fa2283c9619b2d65f8b4aa00263ea110fa24f7e','ebce3b825b1b15509346a73e8dda9b70f7355f111d07c6cbe9f86b4ff28ae99d'))
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_raw_qc_executor' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR EXISTS(SELECT 1 FROM pg_class t JOIN pg_roles r ON r.oid=t.relowner
   WHERE t.oid IN ('cyomics_app.preprocessing_jobs'::regclass,'cyomics_app.preprocessing_job_runs'::regclass,
     'cyomics_app.processed_dataset_versions'::regclass,'cyomics_app.processed_dataset_artifact_bundles'::regclass)
   AND (NOT t.relrowsecurity OR NOT t.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner'))
 THEN RAISE EXCEPTION 'median018 preflight rejected'; END IF;
END $guard$;
CREATE OR REPLACE FUNCTION cyomics_app.preprocessing_queue_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE d record; p jsonb; e jsonb; expected_params text; expected_source text;
 expected_execution text; none_params text; selected_sha text; request_text text; binding_text text;
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
 IF NOT (CASE WHEN jsonb_typeof(d.report_json::jsonb->'report'->'row_count')='number'
                  AND jsonb_typeof(d.report_json::jsonb->'report'->'column_count')='number' THEN
   (d.report_json::jsonb->'report'->>'row_count')::numeric BETWEEN 1 AND 10000
   AND trunc((d.report_json::jsonb->'report'->>'row_count')::numeric)=(d.report_json::jsonb->'report'->>'row_count')::numeric
   AND (d.report_json::jsonb->'report'->>'column_count')::numeric BETWEEN 2 AND 512
   AND trunc((d.report_json::jsonb->'report'->>'column_count')::numeric)=(d.report_json::jsonb->'report'->>'column_count')::numeric
   AND (d.report_json::jsonb->'report'->>'row_count')::numeric*(d.report_json::jsonb->'report'->>'column_count')::numeric<=2000000
   ELSE false END)
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
 none_params:=expected_params;
 IF p->>'normalization'='median' THEN
   IF NOT coalesce(jsonb_typeof(p->'normalization_parent')='object'
      AND (SELECT count(*) FROM jsonb_object_keys(p->'normalization_parent'))=2
      AND (p->'normalization_parent'->>'processed_dataset_version_id') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND (p->'normalization_parent'->>'manifest_sha256') ~ '^[0-9a-f]{64}$',false)
   THEN RAISE EXCEPTION 'median parent binding rejected'; END IF;
   IF NOT EXISTS(SELECT 1 FROM cyomics_app.processed_dataset_versions v
       JOIN cyomics_app.processed_dataset_artifact_bundles b USING(processed_dataset_version_id)
       JOIN cyomics_app.preprocessing_job_runs r ON r.job_id=v.job_id AND r.result_version_id=v.processed_dataset_version_id
       WHERE v.processed_dataset_version_id=(p->'normalization_parent'->>'processed_dataset_version_id')::uuid
       AND v.project_id=NEW.project_id AND v.parent_dataset_version_id=NEW.dataset_version_id
       AND v.file_id=NEW.file_id AND v.created_by=NEW.owner_id
       AND v.manifest_sha256=p->'normalization_parent'->>'manifest_sha256'
       AND r.status='succeeded' AND r.observation='confirmed'
       AND v.metadata_sha256=encode(sha256(convert_to(v.metadata_json,'UTF8')),'hex')
       AND v.metadata_json::jsonb->'source'=NEW.source_json::jsonb
       AND v.metadata_json::jsonb->'parameters'=none_params::jsonb
       AND v.metadata_json::jsonb->>'quant_family'=NEW.quant_family
       AND v.metadata_json::jsonb->>'manifest_sha256'=v.manifest_sha256
       AND b.manifest_sha256=v.manifest_sha256
       AND b.manifest_sha256=encode(sha256(convert_to(b.manifest_json,'UTF8')),'hex')
       AND b.manifest_json::jsonb=v.metadata_json::jsonb->'artifacts')
   THEN RAISE EXCEPTION 'median saved None parent rejected'; END IF;
   expected_params:=replace(replace(replace(expected_params,
       '"normalization":"none"','"normalization":"median","normalization_parent":{"manifest_sha256":"'||(p->'normalization_parent'->>'manifest_sha256')||'","processed_dataset_version_id":"'||(p->'normalization_parent'->>'processed_dataset_version_id')||'"}'),
       '"preset":"positive_log2_zero_na_no_impute_v1"','"preset":"positive_log2_median_zero_na_no_impute_v1"'),
       '"normalization_none"','"normalization_median"');
 END IF;
 IF NEW.params_json IS DISTINCT FROM expected_params THEN RAISE EXCEPTION 'preprocessing parameters rejected'; END IF;
 IF EXISTS(SELECT 1 FROM (VALUES('reverse','Reverse'),('contaminant','Potential contaminant'),('site_only','Only identified by site')) AS flag(k,n)
   WHERE p->'filter_flags'->flag.k='true'::jsonb AND NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(d.report_json::jsonb->'columns') AS col(c) WHERE col.c->>'name_raw'=flag.n))
 THEN RAISE EXCEPTION 'preprocessing enabled flag absent'; END IF;
 IF p->>'normalization'='median' THEN
  expected_execution:='{"contract":"median_saved_parent_source_proposal_v1","entry_kind":"native_R","executable":{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"},"limits":{"address_space_bytes":2147483648,"cleanup_seconds":2,"core_bytes":0,"cpu_seconds":45,"job_tasks":8,"monitor_seconds":0.05,"open_files":256,"output_bytes":268435456,"per_file_bytes":67108864,"stderr_bytes":65536,"stdout_bytes":65536,"uid_admission_tasks":480,"uid_nproc_limit":512,"wall_seconds":60},"process_policy_sha256":"2ab8f62a3e1973711c2bd63940161a72d57d4daa641e22967b12c28ed5d5fede","r_version":"R version 4.6.1","registered":false,"runner":{"bytes":14378,"sha256":"498698b9bce3f30685e45833e76a8902f0e31c90c9220886797406f3116aadbb"},"script":{"bytes":3981,"sha256":"bf6a486859dd350c9b3d1937c123eae0d4d233118af13c3e196f30d340433f92"},"validator":{"bytes":5639,"sha256":"8a414077569be9ea1da281f688826b993dc2b1165a1f45207d7f69088897fbff"}}';
  IF NEW.execution_json IS DISTINCT FROM expected_execution THEN RAISE EXCEPTION 'median exact execution rejected'; END IF;
 ELSE
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
 END IF;
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

CREATE FUNCTION cyomics_app.preprocessing_median_parent_bundle(k uuid,a uuid)
RETURNS TABLE(metadata_json text,manifest_json text,r_session_info bytea,filter_log bytea,
 imputed_mask bytea,original_blank_mask bytea,original_zero_mask bytea,parameters bytea,
 raw_retained bytea,row_map bytea,summary bytea,working_na_mask bytea,working_matrix bytea)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $body$
 SELECT v.metadata_json,b.manifest_json,b.r_session_info,b.filter_log,b.imputed_mask,
 b.original_blank_mask,b.original_zero_mask,b.parameters,b.raw_retained,b.row_map,b.summary,b.working_na_mask,b.working_matrix
 FROM cyomics_app.preprocessing_jobs j JOIN cyomics_app.preprocessing_job_runs r ON r.job_id=j.job_id
 JOIN cyomics_app.preprocessing_runtime_registrations g ON g.registration_id=r.registration_id
 JOIN cyomics_app.processed_dataset_versions v
   ON v.processed_dataset_version_id=(j.params_json::jsonb->'normalization_parent'->>'processed_dataset_version_id')::uuid
 JOIN cyomics_app.processed_dataset_artifact_bundles b USING(processed_dataset_version_id)
 JOIN cyomics_app.preprocessing_job_runs parent_run ON parent_run.job_id=v.job_id AND parent_run.result_version_id=v.processed_dataset_version_id
 WHERE j.job_id=k AND r.attempt_id=a AND r.status='running' AND r.observation='confirmed'
 AND g.project_id=j.project_id AND g.expires_at>clock_timestamp()
 AND cyomics_app.preprocessing_admission_valid(j.job_id)
 AND j.params_json::jsonb->>'normalization'='median'
 AND v.project_id=j.project_id AND v.created_by=j.owner_id
 AND v.parent_dataset_version_id=j.dataset_version_id AND v.file_id=j.file_id
 AND parent_run.status='succeeded' AND parent_run.observation='confirmed'
 AND v.manifest_sha256=j.params_json::jsonb->'normalization_parent'->>'manifest_sha256'
 AND v.metadata_sha256=encode(sha256(convert_to(v.metadata_json,'UTF8')),'hex')
 AND v.metadata_json::jsonb->'source'=j.source_json::jsonb
 AND v.metadata_json::jsonb->>'quant_family'=j.quant_family
 AND v.metadata_json::jsonb->'parameters'=(j.params_json::jsonb-'normalization_parent')||
     jsonb_build_object('normalization','none','preset','positive_log2_zero_na_no_impute_v1',
       'steps',jsonb_build_array('flags_filter','zero_and_blank_to_NA','log2_positive','normalization_none','imputation_none'))
 AND b.manifest_sha256=v.manifest_sha256
 AND b.manifest_sha256=encode(sha256(convert_to(b.manifest_json,'UTF8')),'hex')
 AND b.manifest_json::jsonb=v.metadata_json::jsonb->'artifacts'
$body$;
ALTER FUNCTION cyomics_app.preprocessing_median_parent_bundle(uuid,uuid) OWNER TO cyomicsv2_raw_qc_executor;
REVOKE ALL ON FUNCTION cyomics_app.preprocessing_median_parent_bundle(uuid,uuid) FROM PUBLIC,cyomicsv2_app_runtime;
GRANT EXECUTE ON FUNCTION cyomics_app.preprocessing_median_parent_bundle(uuid,uuid) TO cyomicsv2_raw_qc_worker;
-- No table privilege, role membership, service, default profile or startup approval change.
COMMIT;
