-- DE-CLASSIFICATION-PERSIST-001 SOURCE ONLY / NOT APPLIED. One new table only.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $preflight$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.de_classification012_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.differential_results') IS NULL
 OR to_regclass('cyomics_app.derived_classification_versions') IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 THEN RAISE EXCEPTION 'classification012 preflight rejected'; END IF;
END;
$preflight$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.derived_classification_versions(
 derived_version_id uuid PRIMARY KEY,owner_id uuid NOT NULL,
 project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),request_id uuid NOT NULL,
 request_payload_json text NOT NULL CHECK(octet_length(request_payload_json) BETWEEN 1 AND 16384),
 request_payload_sha256 text NOT NULL CHECK(request_payload_sha256=encode(sha256(convert_to(request_payload_json,'UTF8')),'hex')),
 publication_json text NOT NULL CHECK(octet_length(publication_json) BETWEEN 1 AND 8192),
 publication_sha256 text NOT NULL CHECK(publication_sha256=encode(sha256(convert_to(publication_json,'UTF8')),'hex')),
 source_result_id uuid NOT NULL REFERENCES cyomics_app.differential_results(result_id),
 source_manifest_sha256 text NOT NULL CHECK(source_manifest_sha256~'^[0-9a-f]{64}$'),
 source_binding_json text NOT NULL CHECK(octet_length(source_binding_json) BETWEEN 1 AND 16384),
 source_binding_sha256 text NOT NULL CHECK(source_binding_sha256=encode(sha256(convert_to(source_binding_json,'UTF8')),'hex')),
 parameters_bytes bytea NOT NULL CHECK(octet_length(parameters_bytes) BETWEEN 1 AND 4096),
 parameters_sha256 text NOT NULL CHECK(parameters_sha256=encode(sha256(parameters_bytes),'hex')),
 core_json text NOT NULL CHECK(octet_length(core_json) BETWEEN 1 AND 8192),
 metadata_json text NOT NULL CHECK(octet_length(metadata_json) BETWEEN 1 AND 32768),
 metadata_sha256 text NOT NULL CHECK(metadata_sha256=encode(sha256(convert_to(metadata_json,'UTF8')),'hex')),
 manifest_json text NOT NULL CHECK(octet_length(manifest_json) BETWEEN 1 AND 8192),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256=encode(sha256(convert_to(manifest_json,'UTF8')),'hex')),
 native_receipt_json text NOT NULL CHECK(octet_length(native_receipt_json) BETWEEN 1 AND 262144),
 native_receipt_sha256 text NOT NULL CHECK(native_receipt_sha256=encode(sha256(convert_to(native_receipt_json,'UTF8')),'hex')),
 configuration_sha256 text NOT NULL CHECK(configuration_sha256~'^[0-9a-f]{64}$'),
 status text NOT NULL CHECK(status IN('CLASSIFIED','BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED')),
 members_evaluated boolean NOT NULL,member_set_id uuid UNIQUE,created_at timestamptz NOT NULL,
 classification_audit bytea NOT NULL,classification_parameters bytea NOT NULL,classification bytea NOT NULL,
 selected_de bytea NOT NULL,source_method_audit bytea NOT NULL,source_test_set bytea NOT NULL,
 UNIQUE(owner_id,project_id,request_id),
 CHECK((status='CLASSIFIED' AND members_evaluated AND member_set_id IS NOT NULL)
  OR(status IN('BLOCKED_SOURCE_NO_TESTABLE_ROWS','BLOCKED_SOURCE_REVIEW_REQUIRED') AND NOT members_evaluated AND member_set_id IS NULL)),
 CHECK(octet_length(classification_audit) BETWEEN 1 AND 27262976 AND octet_length(classification_parameters) BETWEEN 1 AND 27262976
  AND octet_length(classification) BETWEEN 1 AND 27262976 AND octet_length(selected_de) BETWEEN 1 AND 27262976
  AND octet_length(source_method_audit) BETWEEN 1 AND 27262976 AND octet_length(source_test_set) BETWEEN 1 AND 27262976
  AND octet_length(classification_audit)::bigint+octet_length(classification_parameters)+octet_length(classification)
   +octet_length(selected_de)+octet_length(source_method_audit)+octet_length(source_test_set)<=134217728)
);
ALTER TABLE cyomics_app.derived_classification_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.derived_classification_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY classification_version_owner_select ON cyomics_app.derived_classification_versions FOR SELECT TO cyomicsv2_app_runtime
 USING(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_results p
  WHERE p.result_id=derived_classification_versions.source_result_id AND p.owner_id=derived_classification_versions.owner_id AND p.project_id=derived_classification_versions.project_id));
CREATE POLICY classification_version_owner_insert ON cyomics_app.derived_classification_versions FOR INSERT TO cyomicsv2_app_runtime
 WITH CHECK(owner_id=cyomics_app.current_user_id() AND EXISTS(SELECT 1 FROM cyomics_app.differential_results p
  WHERE p.result_id=derived_classification_versions.source_result_id AND p.owner_id=derived_classification_versions.owner_id AND p.project_id=derived_classification_versions.project_id));

CREATE FUNCTION cyomics_app.derived_classification_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE p cyomics_app.differential_results;j cyomics_app.differential_jobs;
 m jsonb;b jsonb;n jsonb;a jsonb;i jsonb;u jsonb;k jsonb;counts jsonb;params jsonb;diagnostic jsonb;
 v jsonb;bytes bytea;name text;names text[];key text;token text;
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 THEN RAISE EXCEPTION 'immutable derived classification'; END IF;
 SELECT * INTO p FROM cyomics_app.differential_results WHERE result_id=NEW.source_result_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 SELECT * INTO j FROM cyomics_app.differential_jobs WHERE job_id=p.job_id AND owner_id=NEW.owner_id AND project_id=NEW.project_id;
 m:=NEW.metadata_json::jsonb;b:=NEW.source_binding_json::jsonb;n:=NEW.native_receipt_json::jsonb;
 a:=NEW.manifest_json::jsonb;i:=NEW.request_payload_json::jsonb;u:=NEW.publication_json::jsonb;k:=NEW.core_json::jsonb;counts:=m->'counts';
 IF p.result_id IS NULL OR j.job_id IS NULL OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
 OR NEW.source_manifest_sha256 IS DISTINCT FROM p.manifest_sha256
 OR b->>'contract' IS DISTINCT FROM 'de_parent_binding_v0.1' OR b->'schemaVersion' IS DISTINCT FROM '1'::jsonb
 OR b->>'owner_id' IS DISTINCT FROM NEW.owner_id::text OR b->>'project_id' IS DISTINCT FROM NEW.project_id::text
 OR b->>'source_result_id' IS DISTINCT FROM NEW.source_result_id::text
 OR b->>'source_job_id' IS DISTINCT FROM p.job_id::text OR b->>'source_attempt_id' IS DISTINCT FROM p.attempt_id::text
 OR b->>'source_registration_id' IS DISTINCT FROM p.metadata_json::jsonb->>'registration_id'
 OR b->>'source_request_id' IS DISTINCT FROM j.request_id::text
 OR b->>'source_contrast_id' IS DISTINCT FROM j.input_snapshot_json::jsonb->'contrast'->>'contrast_id'
 OR b->>'admission_binding_sha256' IS DISTINCT FROM j.binding_sha256
 OR b->>'input_snapshot_sha256' IS DISTINCT FROM j.input_snapshot_sha256
 OR b->>'source_metadata_sha256' IS DISTINCT FROM p.metadata_sha256
 OR b->>'source_receipt_sha256' IS DISTINCT FROM p.receipt_sha256
 OR b->>'source_manifest_sha256' IS DISTINCT FROM p.manifest_sha256 OR b->'source_artifacts' IS DISTINCT FROM p.manifest_json::jsonb
 OR b->>'source_status' IS DISTINCT FROM p.metadata_json::jsonb->>'scientific_status'
 OR b->'source_warning_count' IS DISTINCT FROM p.metadata_json::jsonb->'warning_count'
 OR b->>'source_method_sha256' IS DISTINCT FROM j.method_sha256
 OR b->>'contrast_direction' IS DISTINCT FROM 'numerator_minus_denominator'
 THEN RAISE EXCEPTION 'classification parent binding rejected'; END IF;
 IF m->>'contract' IS DISTINCT FROM 'de_classification_version_v0.1' OR m->'schemaVersion' IS DISTINCT FROM '1'::jsonb
 OR m->>'derived_version_id' IS DISTINCT FROM NEW.derived_version_id::text OR m->>'request_id' IS DISTINCT FROM NEW.request_id::text
 OR m->>'project_id' IS DISTINCT FROM NEW.project_id::text OR m->>'created_by' IS DISTINCT FROM NEW.owner_id::text
 OR m->>'source_result_id' IS DISTINCT FROM NEW.source_result_id::text
 OR m->>'source_manifest_sha256' IS DISTINCT FROM NEW.source_manifest_sha256 OR m->>'source_binding_sha256' IS DISTINCT FROM NEW.source_binding_sha256
 OR m->'source_binding' IS DISTINCT FROM b
 OR m->>'parameters_sha256' IS DISTINCT FROM NEW.parameters_sha256 OR m->'core' IS DISTINCT FROM k
 OR m->>'status' IS DISTINCT FROM NEW.status OR m->'DE_members_evaluated' IS DISTINCT FROM to_jsonb(NEW.members_evaluated)
 OR m->'source_analysis_ready' IS DISTINCT FROM 'false'::jsonb
 OR(m->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
 OR m->'artifacts' IS DISTINCT FROM a OR m->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
 OR k->>'contract' IS DISTINCT FROM 'de_classification_v0.1'
 OR k->'script_pin' IS DISTINCT FROM '{"bytes":10335,"sha256":"65e24d287a1546dd7a9fa0480f2a899d4d91eb4b11cda7a4802256fb90f3c421"}'::jsonb
 OR k->'validator_pin' IS DISTINCT FROM '{"bytes":12074,"sha256":"2d37d97fcbbe6bb5c323c887b8c168e68c19945904dd3fe5bc9b247174a5d584"}'::jsonb
 OR NEW.status IS DISTINCT FROM (CASE b->>'source_status' WHEN 'COMPUTED_CANDIDATE' THEN 'CLASSIFIED'
  WHEN 'NO_TESTABLE_ROWS' THEN 'BLOCKED_SOURCE_NO_TESTABLE_ROWS' WHEN 'REVIEW_REQUIRED_LIMMA_WARNING' THEN 'BLOCKED_SOURCE_REVIEW_REQUIRED' ELSE '' END)
 OR(NEW.members_evaluated AND b->'source_warning_count' IS DISTINCT FROM '0'::jsonb)
 THEN RAISE EXCEPTION 'classification metadata rejected'; END IF;
 IF i IS DISTINCT FROM jsonb_build_object('owner_id',NEW.owner_id::text,'project_id',NEW.project_id::text,'request_id',NEW.request_id::text,
  'source_result_id',NEW.source_result_id::text,'source_manifest_sha256',NEW.source_manifest_sha256,'source_binding_sha256',NEW.source_binding_sha256,
  'parameters_sha256',NEW.parameters_sha256,'core',k)
 OR u IS DISTINCT FROM jsonb_build_object('request_payload_sha256',NEW.request_payload_sha256,'manifest_sha256',NEW.manifest_sha256,
  'native_receipt_sha256',NEW.native_receipt_sha256,'configuration_sha256',NEW.configuration_sha256)
 OR n->>'contract' IS DISTINCT FROM 'de_classification_receipt_v0.1' OR n->'schemaVersion' IS DISTINCT FROM '1'::jsonb
 OR n->'source_binding' IS DISTINCT FROM b OR n->>'source_binding_sha256' IS DISTINCT FROM NEW.source_binding_sha256
 OR n->'parameters' IS DISTINCT FROM jsonb_build_object('bytes',octet_length(NEW.parameters_bytes),'sha256',NEW.parameters_sha256)
 OR n->'script' IS DISTINCT FROM k->'script_pin' OR n->'validator' IS DISTINCT FROM k->'validator_pin'
 OR n->>'configuration_sha256' IS DISTINCT FROM NEW.configuration_sha256 OR n->>'manifest_sha256' IS DISTINCT FROM NEW.manifest_sha256
 OR n->>'classification_status' IS DISTINCT FROM NEW.status OR n->'DE_members_evaluated' IS DISTINCT FROM to_jsonb(NEW.members_evaluated)
 OR n->'exit_code' IS DISTINCT FROM to_jsonb((CASE NEW.status WHEN 'CLASSIFIED' THEN 0 WHEN 'BLOCKED_SOURCE_NO_TESTABLE_ROWS' THEN 3 ELSE 4 END))
 OR n->'all_six_outputs_verified' IS DISTINCT FROM 'true'::jsonb OR n->'close_confirmed' IS DISTINCT FROM 'true'::jsonb
 OR n->'published' IS DISTINCT FROM 'false'::jsonb OR n->'statistics_recomputed' IS DISTINCT FROM 'false'::jsonb
 OR n->'resource_supervisor_reused' IS DISTINCT FROM 'true'::jsonb
 OR n->'contract_per_file_bytes' IS DISTINCT FROM '27262976'::jsonb OR n->'contract_total_bytes' IS DISTINCT FROM '134217728'::jsonb
 OR n->'process_control'->'owned_tasks'->'cleanup'->'complete_for_observed_identities' IS DISTINCT FROM 'true'::jsonb
 OR n->'process_control'->'owned_tasks'->'uncertainty' IS DISTINCT FROM '[]'::jsonb
 OR n->>'job_id' IS NULL OR n->>'attempt_id' IS NULL OR n->>'epoch_id' IS NULL
 OR(n->>'job_id')::uuid IS NOT DISTINCT FROM p.job_id OR(n->>'attempt_id')::uuid IS NOT DISTINCT FROM p.attempt_id
 OR n->>'environment_sha256' IS NULL OR n->>'process_policy_sha256' IS NULL
 THEN RAISE EXCEPTION 'classification intent/publication/receipt rejected'; END IF;
 -- Exact original copies: all source families remain intact, no new background.
 IF NEW.classification_parameters IS DISTINCT FROM NEW.parameters_bytes
 OR NEW.source_method_audit IS DISTINCT FROM p.method_audit OR NEW.source_test_set IS DISTINCT FROM p.test_set
 THEN RAISE EXCEPTION 'classification copy rejected'; END IF;
 SELECT array_agg(x.value->>'name' ORDER BY x.ordinality) INTO names FROM jsonb_array_elements(a) WITH ORDINALITY x(value,ordinality);
 IF names IS DISTINCT FROM ARRAY['classification-audit.tsv','classification-parameters.tsv','classification.tsv','selected-DE.tsv','source-method-audit.tsv','source-test-set.tsv']
 THEN RAISE EXCEPTION 'classification exact six required'; END IF;
 FOR v IN SELECT value FROM jsonb_array_elements(a) LOOP
  name:=v->>'name';bytes:=CASE name WHEN 'classification-audit.tsv' THEN NEW.classification_audit
   WHEN 'classification-parameters.tsv' THEN NEW.classification_parameters WHEN 'classification.tsv' THEN NEW.classification
   WHEN 'selected-DE.tsv' THEN NEW.selected_de WHEN 'source-method-audit.tsv' THEN NEW.source_method_audit WHEN 'source-test-set.tsv' THEN NEW.source_test_set END;
  IF(v->>'bytes')::integer IS DISTINCT FROM octet_length(bytes) OR v->>'sha256' IS DISTINCT FROM encode(sha256(bytes),'hex')
  THEN RAISE EXCEPTION 'classification artifact pin rejected'; END IF;
 END LOOP;
 SELECT array_agg(split_part(line,E'\t',1) ORDER BY ordinality),jsonb_object_agg(split_part(line,E'\t',1),split_part(line,E'\t',2))
 INTO names,params FROM string_to_table(convert_from(NEW.parameters_bytes,'UTF8'),E'\n') WITH ORDINALITY x(line,ordinality) WHERE ordinality>1 AND line<>'';
 IF names IS DISTINCT FROM ARRAY['schema_version','input_scale','adjusted_p_max','absolute_log2fc_min']
 OR params->>'schema_version' IS DISTINCT FROM 'de_classification_v0.1' OR params->>'input_scale' IS DISTINCT FROM 'log2'
 OR m->'parameters' IS DISTINCT FROM params
 OR(params->>'adjusted_p_max')!~'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
 OR(params->>'absolute_log2fc_min')!~'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
 OR(params->>'adjusted_p_max')::double precision<=0 OR(params->>'adjusted_p_max')::double precision>1
 OR(params->>'absolute_log2fc_min')::double precision<0
 THEN RAISE EXCEPTION 'classification explicit parameter tokens required'; END IF;
 SELECT jsonb_object_agg(split_part(line,E'\t',1),split_part(line,E'\t',2)) INTO diagnostic
 FROM string_to_table(convert_from(NEW.classification_audit,'UTF8'),E'\n') WITH ORDINALITY x(line,ordinality) WHERE ordinality>1 AND line<>'';
 IF diagnostic->>'schema_version' IS DISTINCT FROM 'de_classification_v0.1'
 OR diagnostic->>'classification_status' IS DISTINCT FROM NEW.status
 OR diagnostic->>'DE_members_evaluated' IS DISTINCT FROM (CASE WHEN NEW.members_evaluated THEN 'TRUE' ELSE 'FALSE' END)
 OR diagnostic->>'source_contrast_id' IS DISTINCT FROM b->>'source_contrast_id'
 OR diagnostic->>'adjusted_p_max' IS DISTINCT FROM params->>'adjusted_p_max'
 OR diagnostic->>'absolute_log2fc_min' IS DISTINCT FROM params->>'absolute_log2fc_min'
 THEN RAISE EXCEPTION 'classification audit rejected'; END IF;
 FOREACH key IN ARRAY ARRAY['Up','Down','NS','not_testable','not_evaluated','selected_DE_rows'] LOOP
  token:=diagnostic->>key;
  IF NOT NEW.members_evaluated AND key=ANY(ARRAY['Up','Down','NS','selected_DE_rows']) THEN
   IF counts->key IS DISTINCT FROM 'null'::jsonb OR token IS DISTINCT FROM 'NA' THEN RAISE EXCEPTION 'blocked membership rejected'; END IF;
  ELSE
   IF token IS NULL OR token!~'^(0|[1-9][0-9]*)$' OR token::integer>10000
    OR counts->key IS DISTINCT FROM to_jsonb(token::integer) THEN RAISE EXCEPTION 'classification count rejected'; END IF;
  END IF;
 END LOOP;
 IF NEW.members_evaluated THEN
  IF m->'member_set' IS DISTINCT FROM jsonb_build_object('member_set_id',NEW.member_set_id::text,'derived_version_id',NEW.derived_version_id::text,
    'selected_artifact_sha256',encode(sha256(NEW.selected_de),'hex'),'member_count',(counts->>'selected_DE_rows')::integer)
  OR(counts->>'selected_DE_rows')::integer IS DISTINCT FROM (counts->>'Up')::integer+(counts->>'Down')::integer
  OR counts->'not_evaluated' IS DISTINCT FROM '0'::jsonb
  OR(counts->>'selected_DE_rows')::integer IS DISTINCT FROM cardinality(string_to_array(convert_from(NEW.selected_de,'UTF8'),E'\n'))-2
  THEN RAISE EXCEPTION 'classification member descriptor rejected'; END IF;
 ELSE
  IF m->'member_set' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'blocked has no usable member set'; END IF;
 END IF;
 RETURN NEW;
END;
$body$;
CREATE TRIGGER derived_classification_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.derived_classification_versions
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.derived_classification_guard();
REVOKE ALL ON TABLE cyomics_app.derived_classification_versions FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT SELECT,INSERT ON TABLE cyomics_app.derived_classification_versions TO cyomicsv2_app_runtime;
REVOKE ALL ON FUNCTION cyomics_app.derived_classification_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
-- New object privileges only; no old-table ACL/policy or role definition change.
COMMIT;
