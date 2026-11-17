-- RAW-QC-BE-01 / 007 PROPOSAL ONLY. NOT APPLIED, NOT SAFE TO AUTO-EXECUTE.
-- No changes to 006 or old rows/constraints. Parent composite consistency uses
-- new-table FK singles + insert/transition checked joins, not new parent UNIQUEs.
-- New worker NOLOGIN role is a privilege template, NOT a provisioned credential.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $guard$
BEGIN
  IF current_database()<>'cyomicsv2_business_dev'
     OR current_user<>'postgres' OR session_user<>'postgres'
     OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
     OR current_setting('cyomics.raw_qc007_reviewed',true) IS DISTINCT FROM 'yes'
     OR current_setting('server_version_num')::integer/10000<>17
     OR to_regclass('cyomics_app.sample_design_versions') IS NULL
     OR to_regclass('cyomics_app.raw_qc_jobs') IS NOT NULL
     OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('cyomicsv2_raw_qc_executor','cyomicsv2_raw_qc_worker'))
     OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
     OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
        WHERE c.oid IN ('cyomics_app.projects'::regclass,'cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
          AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner'))
  THEN RAISE EXCEPTION 'raw QC 007 proposal preflight rejected'; END IF;
END $guard$;

CREATE ROLE cyomicsv2_raw_qc_executor NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE cyomicsv2_raw_qc_worker NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
-- No memberships/passwords/LOGIN changes, no auth-schema access.
CREATE TABLE cyomics_app.raw_qc_jobs(
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
  params_json text NOT NULL CHECK(params_json='{}'),
  execution_json text NOT NULL CHECK(octet_length(execution_json) BETWEEN 1 AND 4096),
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
  revision bigint NOT NULL DEFAULT 1 CHECK(revision BETWEEN 1 AND 9007199254740991),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  started_at timestamptz,
  finished_at timestamptz,
  attempt_id uuid,
  observation text NOT NULL DEFAULT 'confirmed' CHECK(observation IN ('confirmed','reconciliation_pending')),
  error_code text,
  result_json text CHECK(octet_length(result_json) BETWEEN 1 AND 32768),
  result_sha256 text CHECK(result_sha256 ~ '^[0-9a-f]{64}$'),
  UNIQUE(project_id,request_id),
  UNIQUE(attempt_id),
  CHECK(started_at IS NULL OR started_at>=created_at),
  CHECK(finished_at IS NULL OR finished_at>=COALESCE(started_at,created_at)),
  CHECK((status='queued' AND started_at IS NULL AND finished_at IS NULL AND attempt_id IS NULL)
     OR (status='running' AND started_at IS NOT NULL AND finished_at IS NULL AND attempt_id IS NOT NULL)
     OR (status='succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND attempt_id IS NOT NULL)
     OR (status='failed' AND finished_at IS NOT NULL AND ((started_at IS NULL)=(attempt_id IS NULL)))),
  CHECK(status='running' OR observation='confirmed'),
  CHECK((status='failed' AND error_code IS NOT NULL AND error_code IN ('input_integrity_failed','execution_unavailable','runner_failed','resource_limit','output_invalid','worker_interrupted','publish_integrity_failed'))
     OR (status<>'failed' AND error_code IS NULL)),
  CHECK((status='succeeded' AND result_json IS NOT NULL AND result_sha256 IS NOT NULL
     AND result_sha256=encode(sha256(convert_to(result_json,'UTF8')),'hex'))
     OR (status<>'succeeded' AND result_json IS NULL AND result_sha256 IS NULL))
);
ALTER TABLE cyomics_app.raw_qc_jobs OWNER TO cyomicsv2_app_owner;
CREATE INDEX raw_qc_queued_idx ON cyomics_app.raw_qc_jobs(created_at,job_id) WHERE status='queued';
ALTER TABLE cyomics_app.raw_qc_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.raw_qc_jobs FORCE ROW LEVEL SECURITY;

-- Runtime RLS rechecks current Owner + the actual version/file relationship.
CREATE POLICY raw_qc_owner_select ON cyomics_app.raw_qc_jobs FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
   SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
   JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
   WHERE p.project_id=raw_qc_jobs.project_id AND p.owner_id=raw_qc_jobs.owner_id
     AND d.dataset_version_id=raw_qc_jobs.dataset_version_id AND f.file_id=raw_qc_jobs.file_id));
CREATE POLICY raw_qc_owner_insert ON cyomics_app.raw_qc_jobs FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
   SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
   JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
   WHERE p.project_id=raw_qc_jobs.project_id AND p.owner_id=raw_qc_jobs.owner_id
     AND d.dataset_version_id=raw_qc_jobs.dataset_version_id AND f.file_id=raw_qc_jobs.file_id));
-- Internal NOLOGIN definer can see queue across Owners, never inherited by callers.
CREATE POLICY raw_qc_executor_select ON cyomics_app.raw_qc_jobs FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
CREATE POLICY raw_qc_executor_update ON cyomics_app.raw_qc_jobs FOR UPDATE TO cyomicsv2_raw_qc_executor USING(true) WITH CHECK(true);
-- Parent additions are SELECT-only and job-bound. There is no worker table grant.
CREATE POLICY raw_qc_executor_project ON cyomics_app.projects FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.raw_qc_jobs j WHERE j.project_id=projects.project_id AND j.owner_id=projects.owner_id));
CREATE POLICY raw_qc_executor_dataset ON cyomics_app.dataset_versions FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.raw_qc_jobs j WHERE j.project_id=dataset_versions.project_id
   AND j.dataset_version_id=dataset_versions.dataset_version_id AND j.file_id=dataset_versions.file_id));
CREATE POLICY raw_qc_executor_raw ON cyomics_app.raw_uploads FOR SELECT TO cyomicsv2_raw_qc_executor
 USING(EXISTS(SELECT 1 FROM cyomics_app.raw_qc_jobs j WHERE j.project_id=raw_uploads.project_id AND j.file_id=raw_uploads.file_id));

CREATE FUNCTION cyomics_app.raw_qc_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE v_source jsonb; v_execution jsonb; v_dataset record; v_request_text text; v_binding_text text; v_field_key text;
BEGIN
 IF TG_OP='UPDATE' THEN
   IF ROW(NEW.job_id,NEW.project_id,NEW.dataset_version_id,NEW.file_id,NEW.owner_id,NEW.request_id,
          NEW.request_sha256,NEW.binding_sha256,NEW.source_json,NEW.quant_family,NEW.params_json,NEW.execution_json,NEW.created_at)
      IS DISTINCT FROM
      ROW(OLD.job_id,OLD.project_id,OLD.dataset_version_id,OLD.file_id,OLD.owner_id,OLD.request_id,
          OLD.request_sha256,OLD.binding_sha256,OLD.source_json,OLD.quant_family,OLD.params_json,OLD.execution_json,OLD.created_at)
      OR NEW.revision<>OLD.revision+1 OR OLD.status IN ('succeeded','failed')
      OR NOT ((OLD.status='queued' AND NEW.status='running' AND OLD.attempt_id IS NULL AND NEW.attempt_id IS NOT NULL)
           OR (OLD.status='running' AND NEW.status IN ('running','succeeded','failed') AND NEW.attempt_id=OLD.attempt_id AND NEW.started_at=OLD.started_at))
      OR current_user<>'cyomicsv2_raw_qc_executor'
   THEN RAISE EXCEPTION 'raw QC immutable or transition rejected'; END IF;
   RETURN NEW;
 END IF;
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
    OR NEW.status<>'queued' OR NEW.revision<>1 OR NEW.attempt_id IS NOT NULL
 THEN RAISE EXCEPTION 'raw QC admission rejected'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.project_id::text,47004));
 IF (SELECT count(*) FROM cyomics_app.raw_qc_jobs j
     WHERE j.project_id=NEW.project_id AND j.status IN ('queued','running'))>=2
 THEN RAISE EXCEPTION 'raw QC capacity'; END IF;
 SELECT d0.*, f.bytes AS raw_bytes,f.sha256 AS raw_sha,f.status AS raw_status,f.format AS raw_format
 INTO v_dataset FROM cyomics_app.projects p JOIN cyomics_app.dataset_versions d0 ON d0.project_id=p.project_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d0.file_id
 WHERE p.project_id=NEW.project_id AND p.owner_id=NEW.owner_id AND d0.dataset_version_id=NEW.dataset_version_id AND f.file_id=NEW.file_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'raw QC source missing'; END IF;
 v_source:=NEW.source_json::jsonb; v_execution:=NEW.execution_json::jsonb;
 IF (jsonb_typeof(v_source)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(v_source))<>11
    OR EXISTS(SELECT 1 FROM jsonb_each(v_source) AS source_fields(key,value) WHERE source_fields.key NOT IN ('source_bytes','row_count','column_count') AND jsonb_typeof(source_fields.value)<>'string')
    OR v_source->>'project_id'<>NEW.project_id::text OR v_source->>'dataset_version_id'<>NEW.dataset_version_id::text
    OR v_source->>'file_id'<>NEW.file_id::text
    OR NOT CASE WHEN jsonb_typeof(v_source->'source_bytes')='number' THEN
       (v_source->>'source_bytes')::numeric BETWEEN 1 AND 26214400
       AND trunc((v_source->>'source_bytes')::numeric)=(v_source->>'source_bytes')::numeric
       AND (v_source->>'source_bytes')::numeric=v_dataset.source_bytes ELSE false END
    OR v_source->>'source_sha256'<>v_dataset.source_sha256 OR v_source->>'parser_contract'<>v_dataset.parser_contract
    OR v_source->>'config_sha256'<>v_dataset.config_sha256 OR v_source->>'report_sha256'<>v_dataset.report_sha256
    OR NOT CASE WHEN jsonb_typeof(v_source->'row_count')='number' THEN
       (v_source->>'row_count')::numeric BETWEEN 1 AND 10000
       AND trunc((v_source->>'row_count')::numeric)=(v_source->>'row_count')::numeric
       AND (v_source->>'row_count')::numeric=(v_dataset.report_json::jsonb->'report'->>'row_count')::numeric ELSE false END
    OR NOT CASE WHEN jsonb_typeof(v_source->'column_count')='number' THEN
       (v_source->>'column_count')::numeric BETWEEN 2 AND 512
       AND trunc((v_source->>'column_count')::numeric)=(v_source->>'column_count')::numeric
       AND (v_source->>'column_count')::numeric=(v_dataset.report_json::jsonb->'report'->>'column_count')::numeric ELSE false END
    OR v_source->>'selected_columns_sha256' IS DISTINCT FROM (SELECT encode(sha256(convert_to(
      '['||string_agg('{"name_raw":'||(column_rows.c->'name_raw')::text||',"ordinal":'||(column_rows.c->'ordinal')::text
       ||',"quant_family":'||(column_rows.c->'quant_family')::text||',"sample_label_raw":'||(column_rows.c->'sample_label_raw')::text||'}',
       ',' ORDER BY (column_rows.c->>'ordinal')::integer)||']','UTF8')),'hex')
       FROM jsonb_array_elements(v_dataset.report_json::jsonb->'sample_columns') AS column_rows(c) WHERE column_rows.c->>'quant_family'=NEW.quant_family)
    OR v_dataset.raw_bytes<>v_dataset.source_bytes OR v_dataset.raw_sha<>v_dataset.source_sha256
    OR v_dataset.raw_status<>'stored' OR v_dataset.raw_format<>'proteinGroups_txt'
    OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_dataset.report_json::jsonb->'sample_columns') AS column_rows(c) WHERE column_rows.c->>'quant_family'=NEW.quant_family)
    OR jsonb_typeof(v_execution)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(v_execution))<>8
    OR v_execution->>'entry_kind'<>'native_R'
    OR v_execution->'script'<>jsonb_build_object('bytes',4949,'sha256','2072a20e9dca53ab9ff03ef5a0a5d6aa9556dcee8004b5f28ce2fa4345624d64')
    OR v_execution->>'r_version'<>'R version 4.6.1' OR v_execution->>'limits_profile'<>'raw_qc_limits_v3'
    OR v_execution->>'limits_sha256'<>'fe1e05ac949ed027969d4ae4475e0e66bbc44cdc1d9310486d33aa334f40aad0'
    OR EXISTS(SELECT 1 FROM unnest(ARRAY['script','runner','output_validator','executable']) AS pins(pin_key)
       WHERE jsonb_typeof(v_execution->pins.pin_key)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(v_execution->pins.pin_key))<>2
         OR NOT ((v_execution->pins.pin_key) ?& ARRAY['bytes','sha256'])
         OR v_execution->pins.pin_key->>'sha256' IS NULL OR v_execution->pins.pin_key->>'bytes' IS NULL
         OR jsonb_typeof(v_execution->pins.pin_key->'sha256')<>'string'
         OR v_execution->pins.pin_key->>'sha256' !~ '^[0-9a-f]{64}$'
         OR NOT CASE WHEN jsonb_typeof(v_execution->pins.pin_key->'bytes')='number' THEN
            (v_execution->pins.pin_key->>'bytes')::numeric BETWEEN 1 AND 268435456
            AND trunc((v_execution->pins.pin_key->>'bytes')::numeric)=(v_execution->pins.pin_key->>'bytes')::numeric ELSE false END)
    OR NOT(v_execution ?& ARRAY['script','runner','output_validator','executable','r_version','entry_kind','limits_profile','limits_sha256'])) IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'raw QC source binding rejected'; END IF;
 -- Normalize accepted integral JSON numbers (1.0 -> 1) BEFORE canonical binding.
 -- CASE type gates above reject bool/string/null and fractions without unsafe casts.
 FOREACH v_field_key IN ARRAY ARRAY['source_bytes','row_count','column_count'] LOOP
   v_source:=jsonb_set(v_source,ARRAY[v_field_key],to_jsonb(((v_source->>v_field_key)::numeric)::bigint));
 END LOOP;
 FOREACH v_field_key IN ARRAY ARRAY['script','runner','output_validator','executable'] LOOP
   v_execution:=jsonb_set(v_execution,ARRAY[v_field_key,'bytes'],to_jsonb(((v_execution->v_field_key->>'bytes')::numeric)::bigint));
 END LOOP;
 NEW.source_json:=(SELECT '{'||string_agg(to_jsonb(source_fields.key)::text||':'||source_fields.value::text,',' ORDER BY source_fields.key COLLATE "C")||'}' FROM jsonb_each(v_source) AS source_fields(key,value));
 NEW.execution_json:=(SELECT '{'||string_agg(to_jsonb(execution_fields.key)::text||':'||
   CASE WHEN jsonb_typeof(execution_fields.value)='object' THEN '{"bytes":'||(execution_fields.value->'bytes')::text||',"sha256":'||(execution_fields.value->'sha256')::text||'}'
   ELSE execution_fields.value::text END,',' ORDER BY execution_fields.key COLLATE "C")||'}' FROM jsonb_each(v_execution) AS execution_fields(key,value));
 -- Python additionally verifies full decoded import provenance before COMMIT.
 v_request_text:='{"dataset_version_id":"'||NEW.dataset_version_id||'","params":{},"project_id":"'||NEW.project_id
      ||'","quant_family":"'||NEW.quant_family||'","schemaVersion":1}';
 v_binding_text:='{"contract":"raw_qc_job_v0.1","execution":'||NEW.execution_json||',"params":{},"quant_family":"'
      ||NEW.quant_family||'","request_sha256":"'||NEW.request_sha256||'","source":'||NEW.source_json||'}';
 IF NEW.request_sha256<>encode(sha256(convert_to(v_request_text,'UTF8')),'hex')
    OR NEW.binding_sha256<>encode(sha256(convert_to(v_binding_text,'UTF8')),'hex')
 THEN RAISE EXCEPTION 'raw QC digest rejected'; END IF;
 RETURN NEW;
END $body$;
ALTER FUNCTION cyomics_app.raw_qc_guard() OWNER TO cyomicsv2_app_owner;
REVOKE ALL ON FUNCTION cyomics_app.raw_qc_guard() FROM PUBLIC;
CREATE TRIGGER raw_qc_guard BEFORE INSERT OR UPDATE ON cyomics_app.raw_qc_jobs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.raw_qc_guard();

-- All definer entry points have fixed search_path, typed args, no dynamic SQL,
-- no Owner arg, and current source-owner checks. Functions are not owned by superuser.
CREATE FUNCTION cyomics_app.raw_qc_read_attempt(k uuid,a uuid) RETURNS SETOF cyomics_app.raw_qc_jobs
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $body$
 SELECT j.* FROM cyomics_app.raw_qc_jobs j
 JOIN cyomics_app.projects p ON p.project_id=j.project_id AND p.owner_id=j.owner_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=j.project_id AND d.dataset_version_id=j.dataset_version_id AND d.file_id=j.file_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=j.project_id AND f.file_id=j.file_id
 WHERE (k IS NULL OR j.job_id=k) AND j.attempt_id=a AND f.status='stored' AND f.format='proteinGroups_txt'
   AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256
   AND j.source_json::jsonb->>'source_sha256'=f.sha256
   AND j.source_json::jsonb->>'source_bytes'=f.bytes::text
   AND j.source_json::jsonb->>'report_sha256'=d.report_sha256
   AND j.source_json::jsonb->>'config_sha256'=d.config_sha256
$body$;
CREATE FUNCTION cyomics_app.raw_qc_claim(p_attempt_id uuid) RETURNS SETOF cyomics_app.raw_qc_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
DECLARE v_job_id uuid;
BEGIN
 IF p_attempt_id IS NULL THEN RAISE EXCEPTION 'attempt required'; END IF;
 PERFORM pg_advisory_xact_lock(47004,1); -- single controlled worker, stale running blocks new claim
 IF EXISTS(SELECT 1 FROM cyomics_app.raw_qc_jobs WHERE status='running') THEN RETURN; END IF;
 SELECT j.job_id INTO v_job_id FROM cyomics_app.raw_qc_jobs j
 JOIN cyomics_app.projects p ON p.project_id=j.project_id AND p.owner_id=j.owner_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=j.project_id AND d.dataset_version_id=j.dataset_version_id AND d.file_id=j.file_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=j.project_id AND f.file_id=j.file_id
 WHERE j.status='queued' AND f.status='stored' AND f.format='proteinGroups_txt'
   AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256
   AND j.source_json::jsonb->>'source_sha256'=f.sha256
   AND j.source_json::jsonb->>'source_bytes'=f.bytes::text
   AND j.source_json::jsonb->>'report_sha256'=d.report_sha256
   AND j.source_json::jsonb->>'config_sha256'=d.config_sha256
 ORDER BY j.created_at,j.job_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
 IF v_job_id IS NULL THEN RETURN; END IF;
 RETURN QUERY UPDATE cyomics_app.raw_qc_jobs AS job_update SET status='running',attempt_id=p_attempt_id,
     started_at=clock_timestamp(),revision=job_update.revision+1 WHERE job_update.job_id=v_job_id AND job_update.status='queued' RETURNING job_update.*;
END $body$;
CREATE FUNCTION cyomics_app.raw_qc_transition(p_job_id uuid,p_attempt_id uuid,p_revision bigint,p_target text,p_result_text text,p_error_code text)
 RETURNS SETOF cyomics_app.raw_qc_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
DECLARE v_job cyomics_app.raw_qc_jobs; v_result jsonb; v_artifacts_text text;
BEGIN
 SELECT bound_job.* INTO v_job FROM cyomics_app.raw_qc_read_attempt(p_job_id,p_attempt_id) AS bound_job;
 IF NOT FOUND OR v_job.status<>'running' OR v_job.revision<>p_revision THEN RETURN; END IF;
 IF p_target NOT IN ('succeeded','failed','reconciliation_pending') OR p_target IS NULL
    OR (p_target='failed' AND (p_error_code IS NULL OR p_error_code NOT IN ('input_integrity_failed','execution_unavailable','runner_failed','resource_limit','output_invalid','worker_interrupted','publish_integrity_failed')))
    OR (p_target<>'failed' AND p_error_code IS NOT NULL) OR ((p_target='succeeded')<>(p_result_text IS NOT NULL))
 THEN RAISE EXCEPTION 'raw QC transition rejected'; END IF;
 IF p_target='succeeded' THEN
   IF octet_length(p_result_text)>32768 THEN RAISE EXCEPTION 'result too large'; END IF;
   v_result:=p_result_text::jsonb;
   IF (jsonb_typeof(v_result)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(v_result))<>7
      OR NOT CASE WHEN jsonb_typeof(v_result->'schemaVersion')='number' THEN
          (v_result->>'schemaVersion')::numeric=1 ELSE false END
      OR jsonb_typeof(v_result->'job_id')<>'string' OR v_result->>'job_id'<>p_job_id::text
      OR jsonb_typeof(v_result->'binding_sha256')<>'string' OR jsonb_typeof(v_result->'manifest_sha256')<>'string'
      OR v_result->>'binding_sha256'<>v_job.binding_sha256 OR v_result->'execution'<>v_job.execution_json::jsonb
      OR jsonb_typeof(v_result->'artifacts')<>'array' OR jsonb_array_length(v_result->'artifacts')<>7
      OR NOT(v_result ?& ARRAY['schemaVersion','job_id','binding_sha256','execution','artifacts','manifest_sha256','verified_at'])
      OR NOT CASE WHEN jsonb_typeof(v_result->'verified_at')='string'
        AND v_result->>'verified_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
        THEN to_char((v_result->>'verified_at')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')=v_result->>'verified_at'
          AND (v_result->>'verified_at')::timestamptz>=v_job.started_at
          AND (v_result->>'verified_at')::timestamptz<=clock_timestamp() ELSE false END
      -- Strictly reject unsorted arrays; manifest stays sorted-by-name canonical.
      OR v_result->'artifacts' IS DISTINCT FROM (SELECT jsonb_agg(artifact_rows.c ORDER BY (artifact_rows.c->>'name') COLLATE "C")
          FROM jsonb_array_elements(v_result->'artifacts') AS artifact_rows(c))
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_result->'artifacts') AS artifact_rows(c)
        WHERE jsonb_typeof(artifact_rows.c)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(artifact_rows.c))<>4
          OR NOT(artifact_rows.c ?& ARRAY['name','bytes','sha256','media_type'])
          OR artifact_rows.c->>'name' IS NULL OR artifact_rows.c->>'bytes' IS NULL OR artifact_rows.c->>'sha256' IS NULL OR artifact_rows.c->>'media_type' IS NULL
          OR jsonb_typeof(artifact_rows.c->'name')<>'string' OR jsonb_typeof(artifact_rows.c->'sha256')<>'string' OR jsonb_typeof(artifact_rows.c->'media_type')<>'string'
          OR artifact_rows.c->>'name' NOT IN ('raw-matrix.tsv','numeric-matrix.tsv','blank-mask.tsv','zero-mask.tsv','sample-qc.tsv','summary.tsv','R-session-info.txt')
          OR NOT CASE WHEN jsonb_typeof(artifact_rows.c->'bytes')='number' THEN
             (artifact_rows.c->>'bytes')::numeric BETWEEN 1 AND 67108864
             AND trunc((artifact_rows.c->>'bytes')::numeric)=(artifact_rows.c->>'bytes')::numeric ELSE false END
          OR artifact_rows.c->>'sha256' !~ '^[0-9a-f]{64}$'
          OR artifact_rows.c->>'media_type'<>CASE WHEN artifact_rows.c->>'name'='R-session-info.txt' THEN 'text/plain; charset=utf-8' ELSE 'text/tab-separated-values; charset=utf-8' END)
      OR (SELECT count(DISTINCT artifact_rows.c->>'name') FROM jsonb_array_elements(v_result->'artifacts') AS artifact_rows(c))<>7
      OR (SELECT sum((artifact_rows.c->>'bytes')::numeric) FROM jsonb_array_elements(v_result->'artifacts') AS artifact_rows(c))>268435456
      OR v_result->>'manifest_sha256' IS DISTINCT FROM (SELECT encode(sha256(convert_to('['||string_agg(
           '{"bytes":'||(((artifact_rows.c->>'bytes')::numeric)::bigint)::text||',"media_type":'||(artifact_rows.c->'media_type')::text||',"name":'||(artifact_rows.c->'name')::text
           ||',"sha256":'||(artifact_rows.c->'sha256')::text||'}',',' ORDER BY (artifact_rows.c->>'name') COLLATE "C")||']','UTF8')),'hex')
           FROM jsonb_array_elements(v_result->'artifacts') AS artifact_rows(c))) IS DISTINCT FROM FALSE
   THEN RAISE EXCEPTION 'result binding rejected'; END IF;
   -- Canonicalize integral numbers after validation; preserve supplied sorted order.
   v_artifacts_text:=(SELECT '['||string_agg('{"bytes":'||(((artifact_rows.c->>'bytes')::numeric)::bigint)::text||
       ',"media_type":'||(artifact_rows.c->'media_type')::text||',"name":'||(artifact_rows.c->'name')::text||
       ',"sha256":'||(artifact_rows.c->'sha256')::text||'}',',' ORDER BY artifact_rows.ord)||']'
       FROM jsonb_array_elements(v_result->'artifacts') WITH ORDINALITY AS artifact_rows(c,ord));
   p_result_text:='{"artifacts":'||v_artifacts_text||',"binding_sha256":'||(v_result->'binding_sha256')::text||
      ',"execution":'||v_job.execution_json||',"job_id":'||(v_result->'job_id')::text||
      ',"manifest_sha256":'||(v_result->'manifest_sha256')::text||',"schemaVersion":1,"verified_at":'||(v_result->'verified_at')::text||'}';
   -- Actual file bytes/protocol/hashes and publication/fsync evidence remain future adapter requirements.
 END IF;
 RETURN QUERY UPDATE cyomics_app.raw_qc_jobs AS job_update
 SET status=CASE WHEN p_target='reconciliation_pending' THEN 'running' ELSE p_target END,
     revision=job_update.revision+1, observation=CASE WHEN p_target='reconciliation_pending' THEN 'reconciliation_pending' ELSE 'confirmed' END,
     finished_at=CASE WHEN p_target='reconciliation_pending' THEN NULL ELSE clock_timestamp() END,
     error_code=p_error_code,result_json=p_result_text,result_sha256=CASE WHEN p_result_text IS NULL THEN NULL ELSE encode(sha256(convert_to(p_result_text,'UTF8')),'hex') END
 WHERE job_update.job_id=p_job_id AND job_update.attempt_id=p_attempt_id AND job_update.status='running' AND job_update.revision=p_revision RETURNING job_update.*;
END $body$;
-- Bound input is the ONLY raw-content worker entry point; no arbitrary file/Owner key.
CREATE FUNCTION cyomics_app.raw_qc_bound_source(k uuid,a uuid)
 RETURNS TABLE(source_json text,execution_json text,content bytea)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $body$
 SELECT j.source_json,j.execution_json,f.content FROM cyomics_app.raw_qc_read_attempt(k,a) j
 JOIN cyomics_app.raw_uploads f ON f.project_id=j.project_id AND f.file_id=j.file_id
 WHERE j.status='running' AND f.bytes BETWEEN 1 AND 26214400
   AND octet_length(f.content)=f.bytes AND encode(sha256(f.content),'hex')=f.sha256
$body$;

ALTER FUNCTION cyomics_app.raw_qc_read_attempt(uuid,uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.raw_qc_claim(uuid) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.raw_qc_transition(uuid,uuid,bigint,text,text,text) OWNER TO cyomicsv2_raw_qc_executor;
ALTER FUNCTION cyomics_app.raw_qc_bound_source(uuid,uuid) OWNER TO cyomicsv2_raw_qc_executor;
REVOKE ALL ON FUNCTION cyomics_app.raw_qc_read_attempt(uuid,uuid),cyomics_app.raw_qc_claim(uuid),
 cyomics_app.raw_qc_transition(uuid,uuid,bigint,text,text,text),cyomics_app.raw_qc_bound_source(uuid,uuid)
 FROM PUBLIC,cyomicsv2_app_runtime;
REVOKE ALL ON TABLE cyomics_app.raw_qc_jobs FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker,cyomicsv2_raw_qc_executor;
GRANT SELECT ON cyomics_app.raw_qc_jobs TO cyomicsv2_app_runtime,cyomicsv2_raw_qc_executor;
GRANT INSERT(job_id,project_id,dataset_version_id,file_id,owner_id,request_id,request_sha256,binding_sha256,
 source_json,quant_family,params_json,execution_json) ON cyomics_app.raw_qc_jobs TO cyomicsv2_app_runtime;
GRANT UPDATE(status,revision,started_at,finished_at,attempt_id,observation,error_code,result_json,result_sha256)
 ON cyomics_app.raw_qc_jobs TO cyomicsv2_raw_qc_executor;
GRANT USAGE ON SCHEMA cyomics_app TO cyomicsv2_raw_qc_worker,cyomicsv2_raw_qc_executor;
GRANT SELECT(project_id,owner_id) ON cyomics_app.projects TO cyomicsv2_raw_qc_executor;
GRANT SELECT(project_id,dataset_version_id,file_id,source_bytes,source_sha256,report_sha256,config_sha256)
 ON cyomics_app.dataset_versions TO cyomicsv2_raw_qc_executor;
GRANT SELECT(project_id,file_id,bytes,sha256,status,format,content) ON cyomics_app.raw_uploads TO cyomicsv2_raw_qc_executor;
GRANT EXECUTE ON FUNCTION cyomics_app.raw_qc_read_attempt(uuid,uuid),cyomics_app.raw_qc_claim(uuid),
 cyomics_app.raw_qc_transition(uuid,uuid,bigint,text,text,text),cyomics_app.raw_qc_bound_source(uuid,uuid)
 TO cyomicsv2_raw_qc_worker;
-- No public app hooks, no LOGIN/credential, no auth schema, no worker table grant,
-- no generic UPDATE function, no reset/retry/cancel function and no retention deletes.
COMMIT;

