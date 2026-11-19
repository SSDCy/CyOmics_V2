-- S1-AUTO-DIFF-SERVICE-SOURCE-001 SOURCE ONLY. NOT APPLIED.
-- Root approves one bounded service scope. Ordinary runtime only EXECUTEs the
-- fixed exact-request issuer; existing table INSERT/UPDATE grants remain absent.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.differential015_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.differential_runtime_registrations') IS NULL
 OR to_regclass('cyomics_app.differential_service_scopes') IS NOT NULL
 OR to_regprocedure('cyomics_app.issue_differential_request_registration(uuid,uuid,text,text)') IS NOT NULL
 THEN RAISE EXCEPTION 'differential015 preflight rejected'; END IF;
END;
$preflight$;
CREATE TABLE cyomics_app.differential_service_scopes(
 scope_id uuid PRIMARY KEY,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
 scope_sha256 text NOT NULL CHECK(scope_sha256~'^[0-9a-f]{64}$'),
 source_manifest_sha256 text NOT NULL CHECK(source_manifest_sha256~'^[0-9a-f]{64}$'),
 environment_sha256 text NOT NULL CHECK(environment_sha256~'^[0-9a-f]{64}$'),
 method_json text NOT NULL CHECK(octet_length(method_json) BETWEEN 1 AND 8192),
 method_sha256 text NOT NULL CHECK(method_sha256=encode(sha256(convert_to(method_json,'UTF8')),'hex')),
 allowed_selection_json text NOT NULL CHECK(octet_length(allowed_selection_json) BETWEEN 1 AND 8192),
 attestation_json text NOT NULL CHECK(octet_length(attestation_json) BETWEEN 1 AND 32768),
 approved_at timestamptz NOT NULL DEFAULT statement_timestamp(),expires_at timestamptz NOT NULL,
 max_registrations integer NOT NULL CHECK(max_registrations BETWEEN 1 AND 100),
 CHECK(expires_at>approved_at AND expires_at<=approved_at+interval '12 hours')
);
ALTER TABLE cyomics_app.differential_service_scopes OWNER TO cyomicsv2_app_owner;
ALTER TABLE cyomics_app.differential_service_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_service_scopes FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE cyomics_app.differential_service_scopes FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
CREATE FUNCTION cyomics_app.differential_service_scope_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'postgres' OR session_user<>'postgres'
 OR NEW.approved_at IS DISTINCT FROM statement_timestamp()
 OR NEW.method_sha256<>'1f1f74bb816c11dfd7a0bfce0cc4e06dd13fd249500c48eac69569093f5eee1f'
 OR NEW.attestation_json::jsonb->>'metadata_origin' IS DISTINCT FROM 'actual_A_root_verified'
 OR NEW.attestation_json::jsonb->'uid' IS DISTINCT FROM '1000'::jsonb
 OR NEW.attestation_json::jsonb->>'startup_scope_sha256' IS DISTINCT FROM NEW.scope_sha256
 OR NEW.attestation_json::jsonb->>'source_manifest_sha256' IS DISTINCT FROM NEW.source_manifest_sha256
 OR jsonb_typeof(NEW.allowed_selection_json::jsonb) IS DISTINCT FROM 'object'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.allowed_selection_json::jsonb) k)
    IS DISTINCT FROM ARRAY['contrast','processed_dataset_version_id','replicate_model','sample_design_request_id','sample_design_version_id']
 THEN RAISE EXCEPTION 'explicit Root service scope required'; END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER differential_service_scope_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.differential_service_scopes
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.differential_service_scope_guard();
REVOKE ALL ON FUNCTION cyomics_app.differential_service_scope_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;

CREATE OR REPLACE FUNCTION cyomics_app.differential_registry_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'postgres'
 OR (session_user<>'postgres' AND (session_user='cyomicsv2_app_runtime'
   AND NEW.request_id IS NOT NULL
   AND NEW.profile_json::jsonb->>'registration_origin'='service_exact_request_v1'
   AND NEW.profile_json::jsonb->>'claim_policy'='exact_request'
   AND NEW.profile_json::jsonb->>'expected_request_sha256'~'^[0-9a-f]{64}$'
   AND EXISTS(SELECT 1 FROM cyomics_app.differential_service_scopes s
     WHERE s.scope_id::text=NEW.profile_json::jsonb->>'service_scope_id'
       AND s.project_id=NEW.project_id AND s.scope_sha256=NEW.scope_sha256
       AND s.method_json=NEW.method_json AND s.expires_at>=NEW.expires_at
       AND s.approved_at<clock_timestamp() AND s.expires_at>clock_timestamp())) IS NOT TRUE)
 OR NEW.approved_at IS DISTINCT FROM statement_timestamp()
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

CREATE OR REPLACE FUNCTION cyomics_app.differential_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE j cyomics_app.differential_jobs;g cyomics_app.differential_runtime_registrations;
BEGIN
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 OR TG_OP='DELETE' THEN RAISE EXCEPTION 'differential run rejected'; END IF;
 SELECT * INTO j FROM cyomics_app.differential_jobs WHERE job_id=NEW.job_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 SELECT * INTO g FROM cyomics_app.differential_runtime_registrations WHERE registration_id=NEW.registration_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 -- The immutable Root scope was checked by the privileged issuer/registry
 -- trigger. Runtime has no scope-table SELECT grant; retain that boundary.
 -- Its immutable exact-request registration carries the scope and expiry.
 IF g.profile_json::jsonb->>'registration_origin'='service_exact_request_v1'
 AND g.profile_json::jsonb->>'expected_request_sha256' IS DISTINCT FROM j.request_sha256
 THEN RAISE EXCEPTION 'service registration request binding rejected'; END IF;
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

CREATE UNIQUE INDEX differential_service_request_once ON cyomics_app.differential_runtime_registrations(owner_id,project_id,request_id)
 WHERE profile_json::jsonb->>'registration_origin'='service_exact_request_v1';

CREATE FUNCTION cyomics_app.issue_differential_request_registration(p_scope_id uuid,p_project_id uuid,p_request_json text,p_source_manifest_sha256 text)
RETURNS TABLE(disposition text,registration_id uuid,epoch_id uuid,scope_sha256 text,owner_id uuid,project_id uuid,
 request_id uuid,request_sha256 text,expires_at timestamptz,approved_at timestamptz,observed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $issue$
DECLARE s cyomics_app.differential_service_scopes;g cyomics_app.differential_runtime_registrations;
 actor uuid;req jsonb;c jsonb;one jsonb;canonical_request text;request_digest text;
 rid uuid;ep uuid;expires timestamptz;profile text;count_used bigint;eligible boolean;
BEGIN
 -- Definer bypasses RLS: explicit actor/source joins below are mandatory.
 IF current_user<>'postgres' OR session_user<>'cyomicsv2_app_runtime' THEN RAISE EXCEPTION 'runtime session required'; END IF;
 actor:=cyomics_app.current_user_id();
 IF actor IS NULL OR octet_length(p_request_json) NOT BETWEEN 1 AND 8192
 THEN RAISE EXCEPTION 'invalid differential service request'; END IF;
 req:=p_request_json::jsonb;c:=req->'contrast';one:=c->'contrasts'->0;
 IF jsonb_typeof(req) IS DISTINCT FROM 'object'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(req) k) IS DISTINCT FROM
    ARRAY['contrast','processed_dataset_version_id','replicate_model','request_id','sample_design_request_id','sample_design_version_id']
 OR jsonb_typeof(c) IS DISTINCT FROM 'object'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(c) k) IS DISTINCT FROM
    ARRAY['contrasts','dataset_version_id','quant_family','sample_design_version_id','schemaVersion']
 OR jsonb_typeof(c->'contrasts') IS DISTINCT FROM 'array' OR jsonb_array_length(c->'contrasts')<>1
 OR jsonb_typeof(one) IS DISTINCT FROM 'object'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(one) k) IS DISTINCT FROM
    ARRAY['contrast_id','denominator_group','numerator_group']
 OR c->'schemaVersion' IS DISTINCT FROM '1'::jsonb
 OR req->>'replicate_model' IS DISTINCT FROM 'independent_biological'
 OR c->>'quant_family' IS DISTINCT FROM 'LFQ intensity'
 OR one->>'numerator_group' IS NULL OR one->>'denominator_group' IS NULL
 OR one->>'numerator_group'=one->>'denominator_group'
 THEN RAISE EXCEPTION 'invalid differential service selection'; END IF;
 -- Reconstruct the exact existing Python canonical request (all scalar fields
 -- here are strings except the fixed schemaVersion1). No generic JSON helper,
 -- arbitrary profile/owner/method/path/TTL/approval argument exists.
 canonical_request:=format('{"contrast":{"contrasts":[{"contrast_id":%s,"denominator_group":%s,"numerator_group":%s}],"dataset_version_id":%s,"quant_family":%s,"sample_design_version_id":%s,"schemaVersion":1},"processed_dataset_version_id":%s,"replicate_model":%s,"request_id":%s,"sample_design_request_id":%s,"sample_design_version_id":%s}',
   one->'contrast_id',one->'denominator_group',one->'numerator_group',c->'dataset_version_id',c->'quant_family',c->'sample_design_version_id',
   req->'processed_dataset_version_id',req->'replicate_model',req->'request_id',req->'sample_design_request_id',req->'sample_design_version_id');
 IF canonical_request IS DISTINCT FROM p_request_json THEN RAISE EXCEPTION 'canonical differential request required'; END IF;
 rid:=(req->>'request_id')::uuid;
 IF rid::text IS DISTINCT FROM req->>'request_id' OR (one->>'contrast_id')::uuid::text IS DISTINCT FROM one->>'contrast_id'
 THEN RAISE EXCEPTION 'canonical request identity required'; END IF;
 -- Lock scope first for bounded, atomic lifetime quota, then the existing Owner
 -- request namespace. Issued rows count forever, including enqueue failure.
 SELECT x.* INTO s FROM cyomics_app.differential_service_scopes x WHERE x.scope_id=p_scope_id FOR UPDATE;
 SELECT EXISTS(SELECT 1 FROM cyomics_app.projects p
   JOIN cyomics_app.processed_dataset_versions v ON v.project_id=p.project_id AND v.created_by=p.owner_id
   JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id AND d.dataset_version_id=v.parent_dataset_version_id AND d.file_id=v.file_id
   JOIN cyomics_app.raw_uploads u ON u.project_id=p.project_id AND u.file_id=d.file_id
   JOIN cyomics_app.sample_design_versions sd ON sd.project_id=p.project_id AND sd.dataset_version_id=d.dataset_version_id
   WHERE p.project_id=p_project_id AND p.owner_id=actor
     AND v.processed_dataset_version_id=(req->>'processed_dataset_version_id')::uuid
     AND d.dataset_version_id=(c->>'dataset_version_id')::uuid
     AND sd.sample_design_version_id=(req->>'sample_design_version_id')::uuid
     AND sd.sample_design_version_id=(c->>'sample_design_version_id')::uuid
     AND sd.request_id=(req->>'sample_design_request_id')::uuid AND sd.quant_family=c->>'quant_family'
     AND v.metadata_json::jsonb->>'quant_family'=sd.quant_family
     AND v.metadata_json::jsonb->'parameters'->>'output_scale'='log2'
     AND sd.design_json::jsonb->>'dataset_version_id'=d.dataset_version_id::text
     AND sd.design_json::jsonb->>'quant_family'=sd.quant_family
     AND (SELECT count(*) FROM jsonb_array_elements(sd.design_json::jsonb->'rows') x
       WHERE x->>'group'=one->>'numerator_group' AND x->'confirmed'='true'::jsonb AND x->>'replicate_type'='biological')>=2
     AND (SELECT count(*) FROM jsonb_array_elements(sd.design_json::jsonb->'rows') x
       WHERE x->>'group'=one->>'denominator_group' AND x->'confirmed'='true'::jsonb AND x->>'replicate_type'='biological')>=2
     AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(sd.design_json::jsonb->'rows') x WHERE x->'confirmed' IS DISTINCT FROM 'true'::jsonb)
 ) INTO eligible;
 IF NOT eligible OR s.scope_id IS NULL OR s.project_id<>p_project_id
 OR (s.allowed_selection_json::jsonb #- '{contrast,contrasts,0,contrast_id}')
    IS DISTINCT FROM ((req-'request_id') #- '{contrast,contrasts,0,contrast_id}')
 OR s.source_manifest_sha256 IS DISTINCT FROM p_source_manifest_sha256
 OR s.method_sha256<>'1f1f74bb816c11dfd7a0bfce0cc4e06dd13fd249500c48eac69569093f5eee1f' OR s.approved_at>=clock_timestamp()
 OR s.expires_at-clock_timestamp()<=interval '432 seconds'
 THEN RETURN QUERY SELECT 'unavailable'::text,NULL::uuid,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid,NULL::uuid,NULL::text,NULL::timestamptz,NULL::timestamptz,clock_timestamp();RETURN; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text||':'||p_project_id::text||':'||rid::text,47015));
 request_digest:=encode(sha256(convert_to(format('{"owner_id":"%s","project_id":"%s","request":%s}',actor,p_project_id,p_request_json),'UTF8')),'hex');
 SELECT x.* INTO g FROM cyomics_app.differential_runtime_registrations x WHERE x.owner_id=actor AND x.project_id=p_project_id AND x.request_id=rid LIMIT 1;
 IF g.registration_id IS NOT NULL THEN
  RETURN QUERY SELECT CASE WHEN g.profile_json::jsonb->>'expected_request_sha256' IS NOT NULL AND g.profile_json::jsonb->>'expected_request_sha256'<>request_digest THEN 'conflict' ELSE 'consumed' END,
   g.registration_id,g.epoch_id,g.scope_sha256,g.owner_id,g.project_id,g.request_id,request_digest,g.expires_at,g.approved_at,clock_timestamp();RETURN;
 END IF;
 -- Never backfill an old queued admission or issue a ticket after its creation.
 IF EXISTS(SELECT 1 FROM cyomics_app.differential_jobs j WHERE j.owner_id=actor AND j.project_id=p_project_id AND j.request_id=rid)
 THEN RETURN QUERY SELECT 'consumed'::text,NULL::uuid,NULL::uuid,NULL::text,actor,p_project_id,rid,request_digest,NULL::timestamptz,NULL::timestamptz,clock_timestamp();RETURN; END IF;
 SELECT count(*) INTO count_used FROM cyomics_app.differential_runtime_registrations x
 WHERE x.profile_json::jsonb->>'registration_origin'='service_exact_request_v1' AND x.profile_json::jsonb->>'service_scope_id'=s.scope_id::text;
 IF count_used>=s.max_registrations
 THEN RETURN QUERY SELECT 'unavailable'::text,NULL::uuid,NULL::uuid,NULL::text,NULL::uuid,NULL::uuid,NULL::uuid,NULL::text,NULL::timestamptz,NULL::timestamptz,clock_timestamp();RETURN; END IF;
 ep:=gen_random_uuid();expires:=least(s.expires_at,statement_timestamp()+interval '15 minutes');
 profile:=format('{"claim_policy":"exact_request","contract":"differential_runtime_profile_v0.1","environment_sha256":"%s","epoch_id":"%s","expected_request_sha256":"%s","library_files_sha256":"%s","method_sha256":"%s","registration_origin":"service_exact_request_v1","service_scope_id":"%s","source_manifest_sha256":"%s"}',s.environment_sha256,ep,request_digest,s.method_json::jsonb->>'library_files_sha256',s.method_sha256,s.scope_id,s.source_manifest_sha256);
 INSERT INTO cyomics_app.differential_runtime_registrations(registration_id,owner_id,project_id,request_id,scope_sha256,epoch_id,method_json,profile_json,profile_sha256,attestation_json,attestation_sha256,expires_at,max_jobs)
 VALUES(gen_random_uuid(),actor,p_project_id,rid,s.scope_sha256,ep,s.method_json,profile,encode(sha256(convert_to(profile,'UTF8')),'hex'),s.attestation_json,encode(sha256(convert_to(s.attestation_json,'UTF8')),'hex'),expires,1) RETURNING * INTO g;
 RETURN QUERY SELECT 'issued'::text,g.registration_id,g.epoch_id,g.scope_sha256,g.owner_id,g.project_id,g.request_id,request_digest,g.expires_at,g.approved_at,clock_timestamp();
END;
$issue$;
ALTER FUNCTION cyomics_app.issue_differential_request_registration(uuid,uuid,text,text) OWNER TO postgres;
REVOKE ALL ON FUNCTION cyomics_app.issue_differential_request_registration(uuid,uuid,text,text) FROM PUBLIC,cyomicsv2_raw_qc_worker_runtime;
GRANT EXECUTE ON FUNCTION cyomics_app.issue_differential_request_registration(uuid,uuid,text,text) TO cyomicsv2_app_runtime;
-- No ordinary registry table write, role membership, credential or worker changes.
COMMIT;
