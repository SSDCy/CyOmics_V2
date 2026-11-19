-- S1-MEDIAN-AUTO-DISPATCH-SOURCE-001 /021: DRAFT ONLY, DO NOT APPLY.
-- 020 is in flight. Actual pg_get_functiondef hashes MUST be supplied/reviewed by Root.
-- NULL preconditions deliberately prevent this draft from performing any DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='2s'; SET LOCAL lock_timeout='1s'; SET LOCAL search_path=pg_catalog;
DO $preflight$
DECLARE expected_020_queue_sha text:=NULL; expected_020_capsule_sha text:=NULL; expected_020_claim_sha text:=NULL;
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('server_version_num')::int/10000<>17
 OR expected_020_queue_sha IS NULL OR expected_020_capsule_sha IS NULL OR expected_020_claim_sha IS NULL
 OR expected_020_queue_sha IS DISTINCT FROM encode(sha256(convert_to(pg_get_functiondef('cyomics_app.preprocessing_queue_guard()'::regprocedure),'UTF8')),'hex')
 OR expected_020_capsule_sha IS DISTINCT FROM encode(sha256(convert_to(pg_get_functiondef('cyomics_app.preprocessing_run_record(uuid,uuid)'::regprocedure),'UTF8')),'hex')
 OR expected_020_claim_sha IS DISTINCT FROM encode(sha256(convert_to(pg_get_functiondef('cyomics_app.preprocessing_claim(uuid,uuid,uuid,text)'::regprocedure),'UTF8')),'hex')
 OR to_regclass('cyomics_app.median_service_scopes') IS NOT NULL
 OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN('cyomicsv2_app_runtime','cyomicsv2_raw_qc_worker_runtime','cyomicsv2_raw_qc_executor') AND (rolsuper OR rolbypassrls))
 THEN RAISE EXCEPTION '021 requires reviewed actual020 prerequisites and independent QA'; END IF;
END $preflight$;
CREATE TABLE cyomics_app.median_service_scopes(
 scope_id uuid PRIMARY KEY,scope_sha256 text NOT NULL CHECK(scope_sha256~'^[0-9a-f]{64}$'),
 policy_json text NOT NULL CHECK(octet_length(policy_json) BETWEEN 1 AND 32768),
 attestation_json text NOT NULL CHECK(octet_length(attestation_json) BETWEEN 1 AND 32768),
 approved_at timestamptz NOT NULL DEFAULT statement_timestamp(),expires_at timestamptz NOT NULL,
 CHECK(scope_sha256=encode(sha256(convert_to(policy_json,'UTF8')),'hex')),
 CHECK(expires_at>approved_at AND expires_at<=approved_at+interval '12 hours'),
 CHECK((policy_json::jsonb @> '{"schemaVersion":1,"contract":"root_median_service_scope_v1","max_registrations":4}'::jsonb
 AND policy_json::jsonb->>'scope_id'=scope_id::text
 AND policy_json::jsonb->>'project_id'='47278eaf-c156-4b66-b60a-287ca4b23805'
 AND policy_json::jsonb->>'dataset_version_id'='28bc9473-173f-5c48-bc61-7edc7c40f584'
 AND policy_json::jsonb->>'parent_version_id'='2fc0a8e6-ef3d-4afe-a15c-f27043b00227'
 AND encode(sha256(convert_to(policy_json::jsonb->>'proposal_json','UTF8')),'hex')='7dc8c4dc4dbd1b2e0e1c972aafc8e7b8511caed43020c1deee145ebe4ee3538a') IS TRUE)
);
CREATE TABLE cyomics_app.median_service_requests(
 registration_id uuid PRIMARY KEY REFERENCES cyomics_app.preprocessing_runtime_registrations(registration_id) DEFERRABLE INITIALLY DEFERRED,
 scope_id uuid NOT NULL REFERENCES cyomics_app.median_service_scopes(scope_id),epoch_id uuid NOT NULL UNIQUE,
 owner_id uuid NOT NULL,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),dataset_version_id uuid NOT NULL,
 request_id uuid NOT NULL,request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 4096),
 request_sha256 text NOT NULL CHECK(request_sha256~'^[0-9a-f]{64}$'),binding_sha256 text NOT NULL CHECK(binding_sha256~'^[0-9a-f]{64}$'),
 planned_job_id uuid NOT NULL UNIQUE REFERENCES cyomics_app.preprocessing_jobs(job_id) DEFERRABLE INITIALLY DEFERRED,
 slot integer NOT NULL CHECK(slot BETWEEN 0 AND 3),attestation_json text NOT NULL CHECK(octet_length(attestation_json) BETWEEN 1 AND 32768),
 approved_at timestamptz NOT NULL DEFAULT statement_timestamp(),expires_at timestamptz NOT NULL,
 UNIQUE(project_id,request_id),UNIQUE(scope_id,slot)
);
ALTER TABLE cyomics_app.median_service_scopes OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.median_service_requests OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.median_service_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.median_service_scopes FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.median_service_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.median_service_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY median_service_scope_owner ON cyomics_app.median_service_scopes FOR SELECT TO cyomicsv2_app_runtime
 USING(policy_json::jsonb->>'owner_id'=cyomics_app.current_user_id()::text);
CREATE POLICY median_service_request_owner ON cyomics_app.median_service_requests FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.projects p WHERE p.project_id=median_service_requests.project_id AND p.owner_id=median_service_requests.owner_id));
REVOKE ALL ON cyomics_app.median_service_scopes,cyomics_app.median_service_requests FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
-- No direct runtime reads/DML are needed; the Owner function below is the only ticket read.
CREATE FUNCTION cyomics_app.median_service_immutable_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'postgres'
 OR (TG_TABLE_NAME='median_service_scopes' AND (session_user<>'postgres' OR NEW.approved_at IS DISTINCT FROM statement_timestamp()
   OR NEW.attestation_json::jsonb->>'metadata_origin' IS DISTINCT FROM 'actual_A_root_verified'
   OR NEW.attestation_json::jsonb->>'uid' IS DISTINCT FROM '1000'))
 OR (TG_TABLE_NAME='median_service_requests' AND session_user<>'cyomicsv2_app_runtime')
 THEN RAISE EXCEPTION 'immutable Root Median authority required'; END IF;
 RETURN NEW;
END $body$;
CREATE TRIGGER median_service_scope_immutable BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.median_service_scopes
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.median_service_immutable_guard();
CREATE TRIGGER median_service_request_immutable BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.median_service_requests
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.median_service_immutable_guard();
CREATE FUNCTION cyomics_app.median_service_admission_constraint() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $body$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cyomics_app.preprocessing_jobs j JOIN cyomics_app.median_service_scopes s ON s.scope_id=NEW.scope_id
   WHERE j.job_id=NEW.planned_job_id AND j.owner_id=NEW.owner_id AND j.project_id=NEW.project_id AND j.dataset_version_id=NEW.dataset_version_id
   AND j.request_id=NEW.request_id AND j.request_sha256=NEW.request_sha256 AND j.binding_sha256=NEW.binding_sha256
   AND j.created_at>=NEW.approved_at AND j.created_at<NEW.expires_at AND j.quant_family='LFQ intensity'
   AND j.params_json=s.policy_json::jsonb->>'parameters_json' AND j.source_json=s.policy_json::jsonb->>'source_json'
   AND j.execution_json=s.policy_json::jsonb->>'proposal_json')
 THEN RAISE EXCEPTION 'exact Median admission required in same transaction'; END IF;RETURN NEW;
END $body$;
ALTER FUNCTION cyomics_app.median_service_admission_constraint() OWNER TO postgres;
CREATE CONSTRAINT TRIGGER median_service_admission_exact AFTER INSERT ON cyomics_app.median_service_requests
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cyomics_app.median_service_admission_constraint();
CREATE FUNCTION cyomics_app.median_service_flat_canonical(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
 SELECT '{'||string_agg(to_json(key)::text||':'||val::text,',' ORDER BY key COLLATE "C")||'}' FROM jsonb_each(value) AS e(key,val)
$body$;
CREATE OR REPLACE FUNCTION cyomics_app.preprocessing_registration_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP='INSERT' AND current_user='postgres' AND session_user='cyomicsv2_app_runtime' AND EXISTS (
   SELECT 1 FROM cyomics_app.median_service_requests b JOIN cyomics_app.median_service_scopes s ON s.scope_id=b.scope_id
   WHERE b.registration_id=NEW.registration_id AND b.epoch_id=NEW.epoch_id AND b.project_id=NEW.project_id
     AND b.owner_id=cyomics_app.current_user_id() AND NEW.max_jobs=1 AND NEW.approved_at=statement_timestamp()
     AND NEW.startup_scope_sha256=s.scope_sha256 AND NEW.profile_json=s.policy_json::jsonb->>'profile_json'
     AND NEW.profile_sha256=encode(sha256(convert_to(NEW.profile_json,'UTF8')),'hex')
     AND NEW.proposal_sha256=encode(sha256(convert_to(s.policy_json::jsonb->>'proposal_json','UTF8')),'hex')
     AND NEW.attestation_json=b.attestation_json AND NEW.attestation_sha256=encode(sha256(convert_to(b.attestation_json,'UTF8')),'hex')
     AND NEW.expires_at=b.expires_at AND NEW.expires_at<=s.expires_at AND NEW.expires_at<=NEW.approved_at+interval '15 minutes'
 ) THEN RETURN NEW; END IF;
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
CREATE FUNCTION cyomics_app.issue_median_request_registration(p_scope_id uuid,p_project_id uuid,p_dataset_version_id uuid,
 p_request_json text,p_source_manifest_sha256 text,p_planned_job_id uuid)
RETURNS TABLE(disposition text,registration_id uuid,epoch_id uuid,scope_sha256 text,owner_id uuid,project_id uuid,dataset_version_id uuid,
 request_id uuid,request_sha256 text,binding_sha256 text,planned_job_id uuid,slot integer,approved_at timestamptz,expires_at timestamptz,observed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $issue$
DECLARE s cyomics_app.median_service_scopes;pol jsonb;req jsonb;src jsonb;actor uuid;rid uuid;gid uuid;ep uuid;
 expected text;rh text;bh text;att text;used integer;expiry timestamptz;pp jsonb;
BEGIN
 IF current_user<>'postgres' OR session_user<>'cyomicsv2_app_runtime' OR p_planned_job_id IS NULL
 OR octet_length(p_request_json) NOT BETWEEN 1 AND 4096 THEN RAISE EXCEPTION 'runtime Median request required'; END IF;
 actor:=cyomics_app.current_user_id();req:=p_request_json::jsonb;
 IF actor IS NULL OR jsonb_typeof(req)<>'object' OR req->>'request_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 THEN RAISE EXCEPTION 'invalid Median request'; END IF;
 rid:=(req->>'request_id')::uuid;
 SELECT * INTO s FROM cyomics_app.median_service_scopes t WHERE t.scope_id=p_scope_id FOR UPDATE;
 IF NOT FOUND OR s.approved_at>clock_timestamp() OR s.expires_at-clock_timestamp()<=interval '432 seconds'
 THEN RAISE EXCEPTION 'Median scope unavailable'; END IF;
 pol:=s.policy_json::jsonb;src:=(pol->>'source_json')::jsonb;
 IF pol->>'owner_id' IS DISTINCT FROM actor::text OR pol->>'project_id' IS DISTINCT FROM p_project_id::text
 OR pol->>'dataset_version_id' IS DISTINCT FROM p_dataset_version_id::text
 OR (pol->>'profile_json')::jsonb->>'source_manifest_sha256' IS DISTINCT FROM p_source_manifest_sha256
 OR NOT EXISTS(SELECT 1 FROM cyomics_app.projects x JOIN cyomics_app.dataset_versions d ON d.project_id=x.project_id
   JOIN cyomics_app.raw_uploads f ON f.project_id=d.project_id AND f.file_id=d.file_id
   JOIN cyomics_app.processed_dataset_versions v ON v.project_id=x.project_id AND v.created_by=x.owner_id
   WHERE x.project_id=p_project_id AND x.owner_id=actor AND d.dataset_version_id=p_dataset_version_id
   AND d.file_id::text=src->>'file_id' AND d.source_bytes::text=src->>'source_bytes' AND d.source_sha256=src->>'source_sha256'
   AND d.config_sha256=src->>'config_sha256' AND d.report_sha256=src->>'report_sha256' AND f.sha256=d.source_sha256
   AND f.bytes=d.source_bytes AND f.status='stored' AND f.format='proteinGroups_txt'
   AND v.processed_dataset_version_id::text=pol->>'parent_version_id' AND v.manifest_sha256=pol->>'parent_manifest_sha256')
 THEN RAISE EXCEPTION 'Median Owner or source rejected'; END IF;
 expected:='{"parameters":'||(pol->>'parameters_json')||',"quant_family":"LFQ intensity","request_id":'||to_json(rid::text)::text||'}';
 IF p_request_json IS DISTINCT FROM expected THEN RAISE EXCEPTION 'exact canonical Median request required'; END IF;
 IF EXISTS(SELECT 1 FROM cyomics_app.median_service_requests b WHERE b.project_id=p_project_id AND b.request_id=rid)
 THEN RAISE EXCEPTION 'Median request already issued; lookup only'; END IF;
 SELECT count(*)::int INTO used FROM cyomics_app.median_service_requests b WHERE b.scope_id=p_scope_id;
 IF used>=4 THEN RAISE EXCEPTION 'Median scope quota consumed'; END IF;
 gid:=gen_random_uuid();ep:=gen_random_uuid();expiry:=least(s.expires_at,statement_timestamp()+interval '15 minutes',to_timestamp((pol->>'expires_at_ms')::numeric/1000));
 IF expiry-clock_timestamp()<=interval '432 seconds' THEN RAISE EXCEPTION 'Median scope expires soon'; END IF;
 rh:=encode(sha256(convert_to('{"dataset_version_id":'||to_json(p_dataset_version_id::text)::text||',"project_id":'||to_json(p_project_id::text)::text||',"request":'||expected||'}','UTF8')),'hex');
 bh:=encode(sha256(convert_to('{"contract":"preprocessing_queued_v0.1","execution":'||(pol->>'proposal_json')||',"parameters":'||(pol->>'parameters_json')||',"quant_family":"LFQ intensity","request_sha256":'||to_json(rh)::text||',"source":'||(pol->>'source_json')||'}','UTF8')),'hex');
 att:=cyomics_app.median_service_flat_canonical(jsonb_build_object('metadata_origin','root_service_scope_derived',
  'service_scope_id',s.scope_id::text,'startup_scope_sha256',s.scope_sha256,'root_attestation_sha256',encode(sha256(convert_to(s.attestation_json,'UTF8')),'hex'),
  'source_manifest_sha256',p_source_manifest_sha256,'owner_id',actor::text,'project_id',p_project_id::text,'dataset_version_id',p_dataset_version_id::text,
  'request_id',rid::text,'request_sha256',rh,'binding_sha256',bh,'planned_job_id',p_planned_job_id::text,'registration_id',gid::text,'epoch_id',ep::text,'slot',used));
 INSERT INTO cyomics_app.median_service_requests(registration_id,scope_id,epoch_id,owner_id,project_id,dataset_version_id,request_id,request_json,request_sha256,binding_sha256,planned_job_id,slot,attestation_json,expires_at)
 VALUES(gid,p_scope_id,ep,actor,p_project_id,p_dataset_version_id,rid,expected,rh,bh,p_planned_job_id,used,att,expiry);
 INSERT INTO cyomics_app.preprocessing_runtime_registrations(registration_id,project_id,proposal_sha256,profile_json,profile_sha256,attestation_json,attestation_sha256,startup_scope_sha256,epoch_id,expires_at,max_jobs)
 VALUES(gid,p_project_id,encode(sha256(convert_to(pol->>'proposal_json','UTF8')),'hex'),pol->>'profile_json',encode(sha256(convert_to(pol->>'profile_json','UTF8')),'hex'),att,encode(sha256(convert_to(att,'UTF8')),'hex'),s.scope_sha256,ep,expiry,1);
 RETURN QUERY SELECT 'issued'::text,gid,ep,s.scope_sha256,actor,p_project_id,p_dataset_version_id,rid,rh,bh,p_planned_job_id,used,statement_timestamp(),expiry,clock_timestamp();
END $issue$;
ALTER FUNCTION cyomics_app.issue_median_request_registration(uuid,uuid,uuid,text,text,uuid) OWNER TO postgres;
CREATE FUNCTION cyomics_app.read_median_service_ticket(g uuid,k uuid,h text)
RETURNS TABLE(expires_at timestamptz,attestation_json text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $read$
BEGIN
 IF current_user<>'postgres' OR session_user<>'cyomicsv2_app_runtime' THEN RAISE EXCEPTION 'runtime Owner required'; END IF;
 RETURN QUERY SELECT r.expires_at,b.attestation_json FROM cyomics_app.median_service_requests b
 JOIN cyomics_app.median_service_scopes s ON s.scope_id=b.scope_id
 JOIN cyomics_app.preprocessing_runtime_registrations r ON r.registration_id=b.registration_id
 JOIN cyomics_app.preprocessing_jobs j ON j.job_id=b.planned_job_id
 JOIN cyomics_app.projects pr ON pr.project_id=j.project_id AND pr.owner_id=j.owner_id
 WHERE b.registration_id=g AND b.planned_job_id=k AND s.scope_sha256=h AND b.owner_id=cyomics_app.current_user_id()
 AND b.owner_id=j.owner_id AND b.request_sha256=j.request_sha256 AND b.binding_sha256=j.binding_sha256
 AND r.startup_scope_sha256=h AND r.expires_at>clock_timestamp() AND s.expires_at>clock_timestamp()
 AND r.attestation_json=b.attestation_json AND j.created_at>=r.approved_at AND r.max_jobs=1;
END $read$;
ALTER FUNCTION cyomics_app.read_median_service_ticket(uuid,uuid,text) OWNER TO postgres;
CREATE OR REPLACE FUNCTION cyomics_app.preprocessing_claim(k uuid,a uuid,g uuid,scope_sha text) RETURNS jsonb
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
 -- Service registrations may claim only their exact bound job, never a queue scan.
 IF EXISTS(SELECT 1 FROM cyomics_app.median_service_requests b WHERE b.registration_id=g)
 AND (k IS NULL OR NOT EXISTS(SELECT 1 FROM cyomics_app.median_service_requests b
   JOIN cyomics_app.preprocessing_jobs q ON q.job_id=b.planned_job_id
   WHERE b.registration_id=g AND b.planned_job_id=k AND b.owner_id=q.owner_id AND b.project_id=q.project_id
     AND b.dataset_version_id=q.dataset_version_id AND b.request_id=q.request_id
     AND b.request_sha256=q.request_sha256 AND b.binding_sha256=q.binding_sha256))
 THEN RAISE EXCEPTION 'exact Median service job required'; END IF;
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
ALTER FUNCTION cyomics_app.preprocessing_registration_guard() OWNER TO cyomicsv2_app_owner;
ALTER FUNCTION cyomics_app.preprocessing_claim(uuid,uuid,uuid,text) OWNER TO cyomicsv2_raw_qc_executor;
-- Original executor must see only these immutable linkage rows for exact service claims.
CREATE POLICY median_service_request_executor ON cyomics_app.median_service_requests FOR SELECT TO cyomicsv2_raw_qc_executor USING(true);
GRANT SELECT ON cyomics_app.median_service_requests TO cyomicsv2_raw_qc_executor;
REVOKE ALL ON FUNCTION cyomics_app.issue_median_request_registration(uuid,uuid,uuid,text,text,uuid),cyomics_app.read_median_service_ticket(uuid,uuid,text),
 cyomics_app.median_service_immutable_guard(),cyomics_app.median_service_admission_constraint(),cyomics_app.median_service_flat_canonical(jsonb)
 FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT EXECUTE ON FUNCTION cyomics_app.issue_median_request_registration(uuid,uuid,uuid,text,text,uuid),cyomics_app.read_median_service_ticket(uuid,uuid,text) TO cyomicsv2_app_runtime;
-- Existing worker claim grants/009/018/019/020 algorithms and publication ACL remain unchanged.
COMMIT;
