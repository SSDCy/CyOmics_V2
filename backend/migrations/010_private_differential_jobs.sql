-- DIFFERENTIAL-JOB-ADMISSION-001: SOURCE ONLY, NOT APPLIED.
--010 draft number requires Root reconciliation and a separate runtime decision.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
DO $guard$
BEGIN
 IF current_database()<>'cyomicsv2_business_dev' OR current_user<>'postgres' OR session_user<>'postgres'
 OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
 OR current_setting('cyomics.differential010_reviewed',true) IS DISTINCT FROM 'yes'
 OR current_setting('server_version_num')::integer/10000<>17
 OR to_regclass('cyomics_app.differential_jobs') IS NOT NULL
 OR to_regprocedure('cyomics_app.differential_queue_guard()') IS NOT NULL
 OR to_regclass('cyomics_app.processed_dataset_versions') IS NULL
 OR to_regclass('cyomics_app.sample_design_versions') IS NULL
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner' AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
 OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
 WHERE c.oid IN('cyomics_app.projects'::regclass,'cyomics_app.dataset_versions'::regclass,'cyomics_app.raw_uploads'::regclass,
 'cyomics_app.processed_dataset_versions'::regclass,'cyomics_app.sample_design_versions'::regclass)
 AND(NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner'))
 THEN RAISE EXCEPTION 'differential admission preflight rejected'; END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.differential_jobs(
 job_id uuid PRIMARY KEY,project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),owner_id uuid NOT NULL,
 request_id uuid NOT NULL,request_json text NOT NULL CHECK(octet_length(request_json) BETWEEN 1 AND 8192),
 request_sha256 text NOT NULL CHECK(request_sha256~'^[0-9a-f]{64}$'),binding_sha256 text NOT NULL CHECK(binding_sha256~'^[0-9a-f]{64}$'),
 processed_dataset_version_id uuid NOT NULL REFERENCES cyomics_app.processed_dataset_versions(processed_dataset_version_id),
 parent_dataset_version_id uuid NOT NULL REFERENCES cyomics_app.dataset_versions(dataset_version_id),
 file_id uuid NOT NULL REFERENCES cyomics_app.raw_uploads(file_id),
 sample_design_version_id uuid NOT NULL REFERENCES cyomics_app.sample_design_versions(sample_design_version_id),sample_design_request_id uuid NOT NULL,
 quant_family text NOT NULL CHECK(quant_family IN('Intensity','LFQ intensity')),
 method_json text NOT NULL CHECK(octet_length(method_json) BETWEEN 1 AND 8192),method_sha256 text NOT NULL,
 input_snapshot_json text NOT NULL CHECK(octet_length(input_snapshot_json) BETWEEN 1 AND 4194304),input_snapshot_sha256 text NOT NULL,
 input_manifest_json text NOT NULL CHECK(octet_length(input_manifest_json) BETWEEN 1 AND 4096),input_manifest_sha256 text NOT NULL,
 matrix_bytes bytea NOT NULL CHECK(octet_length(matrix_bytes) BETWEEN 1 AND 26214400),
 design_bytes bytea NOT NULL CHECK(octet_length(design_bytes) BETWEEN 1 AND 131072),
 preset_bytes bytea NOT NULL CHECK(octet_length(preset_bytes) BETWEEN 1 AND 4096),
 status text NOT NULL DEFAULT 'queued' CHECK(status='queued'),revision integer NOT NULL DEFAULT 1 CHECK(revision=1),
 created_at timestamptz NOT NULL DEFAULT statement_timestamp(),UNIQUE(owner_id,project_id,request_id),
 CHECK(method_sha256=encode(sha256(convert_to(method_json,'UTF8')),'hex')),
 CHECK(input_snapshot_sha256=encode(sha256(convert_to(input_snapshot_json,'UTF8')),'hex')),
 CHECK(input_manifest_sha256=encode(sha256(convert_to(input_manifest_json,'UTF8')),'hex')),
 CHECK(method_json='{"R_version":"4.6.1","contract":"limma_core_v0.1","contrast_direction":"numerator_minus_denominator","imputation":"none","input_binder":{"bytes":13163,"sha256":"3a67e6c41b9ded6407562fe4b92a7ee6a163d751f80f3309ec44d8e8f0576fee"},"input_scale":"log2","library_files_sha256":"7ab763eb038759748400b1ff055f2d4ea2733fd81a4ded99de2169670f76ff56","limma_version":"3.68.5","native":{"bytes":14488,"sha256":"9c572a708d444e0ec91c59935bf9b5c3fd426a75fe2b7101b2a65f4783155283"},"normalization":"none","output_contract":{"bytes":10301,"sha256":"3534ef4ddf70bf1830abed33ccf05ad8273941343edf225f44006872d44a4047"},"publication_registered":false,"replicate_model":"independent_biological","runner":{"bytes":19031,"sha256":"14a330bd1c1c84879ed8f7ad90bb48debc72449e5475402b18db6b0411a6d7d5"},"runtime_registered":false,"script":{"bytes":12103,"sha256":"9807467ba2ae54efd5ef77e5c15dbe0525d205f4d2988ba24995ea932024c908"},"statmod_version":"1.5.2","supervisor":{"bytes":30207,"sha256":"6f7839b4a867cea8fd9aef63b684ac8c9dbf63dbc7a95bb289080e8aa8074a3e"}}'),
 CHECK(((input_manifest_json::jsonb->'matrix'->>'bytes')::integer=octet_length(matrix_bytes)
 AND input_manifest_json::jsonb->'matrix'->>'sha256'=encode(sha256(matrix_bytes),'hex')
 AND(input_manifest_json::jsonb->'design'->>'bytes')::integer=octet_length(design_bytes)
 AND input_manifest_json::jsonb->'design'->>'sha256'=encode(sha256(design_bytes),'hex')
 AND(input_manifest_json::jsonb->'preset'->>'bytes')::integer=octet_length(preset_bytes)
 AND input_manifest_json::jsonb->'preset'->>'sha256'=encode(sha256(preset_bytes),'hex')) IS TRUE)
);
ALTER TABLE cyomics_app.differential_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.differential_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY differential_owner_select ON cyomics_app.differential_jobs FOR SELECT TO cyomicsv2_app_runtime USING(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
 SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.processed_dataset_versions v ON v.project_id=p.project_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id AND d.dataset_version_id=v.parent_dataset_version_id AND d.file_id=v.file_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
 JOIN cyomics_app.sample_design_versions sd ON sd.project_id=p.project_id AND sd.dataset_version_id=d.dataset_version_id
 WHERE p.project_id=differential_jobs.project_id AND p.owner_id=differential_jobs.owner_id
 AND v.processed_dataset_version_id=differential_jobs.processed_dataset_version_id AND v.created_by=differential_jobs.owner_id
 AND d.dataset_version_id=differential_jobs.parent_dataset_version_id AND f.file_id=differential_jobs.file_id
 AND f.format='proteinGroups_txt' AND f.status='stored' AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256
 AND sd.sample_design_version_id=differential_jobs.sample_design_version_id AND sd.request_id=differential_jobs.sample_design_request_id
 AND sd.quant_family=differential_jobs.quant_family));
CREATE POLICY differential_owner_insert ON cyomics_app.differential_jobs FOR INSERT TO cyomicsv2_app_runtime WITH CHECK(owner_id=(SELECT cyomics_app.current_user_id()) AND EXISTS(
 SELECT 1 FROM cyomics_app.projects p JOIN cyomics_app.processed_dataset_versions v ON v.project_id=p.project_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id AND d.dataset_version_id=v.parent_dataset_version_id AND d.file_id=v.file_id
 JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id AND f.file_id=d.file_id
 JOIN cyomics_app.sample_design_versions sd ON sd.project_id=p.project_id AND sd.dataset_version_id=d.dataset_version_id
 WHERE p.project_id=differential_jobs.project_id AND p.owner_id=differential_jobs.owner_id
 AND v.processed_dataset_version_id=differential_jobs.processed_dataset_version_id AND v.created_by=differential_jobs.owner_id
 AND d.dataset_version_id=differential_jobs.parent_dataset_version_id AND f.file_id=differential_jobs.file_id
 AND f.format='proteinGroups_txt' AND f.status='stored' AND f.bytes=d.source_bytes AND f.sha256=d.source_sha256
 AND sd.sample_design_version_id=differential_jobs.sample_design_version_id AND sd.request_id=differential_jobs.sample_design_request_id
 AND sd.quant_family=differential_jobs.quant_family));
CREATE FUNCTION cyomics_app.differential_queue_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE p record;r jsonb;s jsonb;sample jsonb;original jsonb;request_text text;binding_text text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable differential admission'; END IF;
 IF current_user<>'cyomicsv2_app_runtime' OR NEW.owner_id IS DISTINCT FROM cyomics_app.current_user_id()
 OR NEW.status<>'queued' OR NEW.revision<>1 THEN RAISE EXCEPTION 'differential admission rejected'; END IF;
 SELECT v.metadata_json,d.report_json,d.report_sha256,sd.design_json,sd.design_sha256
 INTO p FROM cyomics_app.projects pr
 JOIN cyomics_app.processed_dataset_versions v ON v.project_id=pr.project_id
 JOIN cyomics_app.dataset_versions d ON d.project_id=pr.project_id AND d.dataset_version_id=v.parent_dataset_version_id AND d.file_id=v.file_id
 JOIN cyomics_app.sample_design_versions sd ON sd.project_id=pr.project_id AND sd.dataset_version_id=d.dataset_version_id
 WHERE pr.project_id=NEW.project_id AND pr.owner_id=NEW.owner_id
 AND v.processed_dataset_version_id=NEW.processed_dataset_version_id AND v.created_by=NEW.owner_id
 AND d.dataset_version_id=NEW.parent_dataset_version_id AND d.file_id=NEW.file_id
 AND sd.sample_design_version_id=NEW.sample_design_version_id AND sd.request_id=NEW.sample_design_request_id AND sd.quant_family=NEW.quant_family;
 IF NOT FOUND THEN RAISE EXCEPTION 'differential exact source missing'; END IF;
 r:=NEW.request_json::jsonb;s:=NEW.input_snapshot_json::jsonb;
 IF(jsonb_typeof(r)<>'object' OR(SELECT count(*) FROM jsonb_object_keys(r))<>6
 OR NOT(r ?& ARRAY['request_id','processed_dataset_version_id','sample_design_version_id','sample_design_request_id','contrast','replicate_model'])
 OR r->>'request_id'<>NEW.request_id::text OR r->>'processed_dataset_version_id'<>NEW.processed_dataset_version_id::text
 OR r->>'sample_design_version_id'<>NEW.sample_design_version_id::text OR r->>'sample_design_request_id'<>NEW.sample_design_request_id::text
 OR r->>'replicate_model'<>'independent_biological'
 OR r->'contrast'->>'dataset_version_id'<>NEW.parent_dataset_version_id::text
 OR r->'contrast'->>'sample_design_version_id'<>NEW.sample_design_version_id::text
 OR r->'contrast'->>'quant_family'<>NEW.quant_family OR jsonb_array_length(r->'contrast'->'contrasts')<>1
 OR s->>'source_version_json'<>p.metadata_json OR p.metadata_json::jsonb->>'quant_family'<>NEW.quant_family
 OR p.metadata_json::jsonb->'source'->>'report_sha256'<>p.report_sha256
 OR s->'source_analysis_ready'<>'false'::jsonb OR s->'design_rank'<>'2'::jsonb OR s->'contrast_weights'<>'[1,-1]'::jsonb
 OR s->'sample_source_binding'->>'sample_design_version_id'<>NEW.sample_design_version_id::text
 OR s->'sample_source_binding'->>'design_sha256'<>p.design_sha256
 OR s->'contrast'<>r->'contrast'->'contrasts'->0
 OR jsonb_array_length(s->'samples') NOT BETWEEN 4 AND 64
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p.design_json::jsonb->'rows') x WHERE x->'confirmed' IS DISTINCT FROM 'true'::jsonb)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(s->'samples') x WHERE x->'design_row'->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR x->'design_row'->>'replicate_type'<>'biological' OR x->>'group_key' NOT IN('g1','g2'))
 OR(SELECT count(*) FROM jsonb_array_elements(s->'samples') x WHERE x->>'group_key'='g1')<2
 OR(SELECT count(*) FROM jsonb_array_elements(s->'samples') x WHERE x->>'group_key'='g2')<2
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(s->'samples') x GROUP BY x->'design_row'->>'group',x->'design_row'->>'replicate' HAVING count(*)>1)) IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'differential input binding rejected'; END IF;
 IF (jsonb_array_length(s->'masks')<>4 OR EXISTS(
 SELECT 1 FROM jsonb_array_elements(s->'masks') m WHERE m->>'name' NOT IN('original-blank-mask.tsv','original-zero-mask.tsv','working-NA-mask.tsv','imputed-mask.tsv')
 OR (m->>'source_sha256') IS DISTINCT FROM (m->'original_bytes'->>'sha256')
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p.metadata_json::jsonb->'artifacts') a
 WHERE a->>'name'=m->>'name' AND jsonb_build_object('bytes',a->'bytes','sha256',a->'sha256')=m->'original_bytes'))
 OR (SELECT count(DISTINCT m->>'name') FROM jsonb_array_elements(s->'masks') m)<>4
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p.metadata_json::jsonb->'artifacts') a
 WHERE a->>'name'='row-map.tsv' AND jsonb_build_object('bytes',a->'bytes','sha256',a->'sha256')=s->'upstream_row_map_tsv')) IS DISTINCT FROM FALSE
 THEN RAISE EXCEPTION 'differential mask lineage rejected'; END IF;
 FOR sample IN SELECT value FROM jsonb_array_elements(s->'samples') LOOP
  SELECT value INTO original FROM jsonb_array_elements(p.design_json::jsonb->'rows') x(value)
  WHERE value->'column_ordinal'=sample->'design_row'->'column_ordinal';
  IF (original IS NULL OR original<>sample->'design_row'
  OR sample->'source_column'->'ordinal'<>original->'column_ordinal'
  OR sample->'source_column'->>'name_raw'<>original->>'name_raw'
  OR sample->'source_column'->>'quant_family'<>NEW.quant_family
  OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p.report_json::jsonb->'sample_columns') x WHERE x=sample->'source_column')
  OR original->>'group'<>r->'contrast'->'contrasts'->0->>(CASE WHEN sample->>'group_key'='g1' THEN 'numerator_group' ELSE 'denominator_group' END))
  IS DISTINCT FROM FALSE THEN RAISE EXCEPTION 'differential sample lineage rejected'; END IF;
 END LOOP;
 request_text:='{"owner_id":"'||NEW.owner_id||'","project_id":"'||NEW.project_id||'","request":'||NEW.request_json||'}';
 binding_text:='{"input_manifest_sha256":"'||NEW.input_manifest_sha256||'","input_snapshot_sha256":"'||NEW.input_snapshot_sha256
 ||'","method_sha256":"'||NEW.method_sha256||'","request_sha256":"'||NEW.request_sha256||'"}';
 IF NEW.request_sha256<>encode(sha256(convert_to(request_text,'UTF8')),'hex')
 OR NEW.binding_sha256<>encode(sha256(convert_to(binding_text,'UTF8')),'hex') THEN RAISE EXCEPTION 'differential digest rejected'; END IF;
 RETURN NEW;
END;
$body$;
REVOKE ALL ON FUNCTION cyomics_app.differential_queue_guard() FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
CREATE TRIGGER differential_queue_guard BEFORE INSERT OR UPDATE OR DELETE ON cyomics_app.differential_jobs
 FOR EACH ROW EXECUTE FUNCTION cyomics_app.differential_queue_guard();
REVOKE ALL ON TABLE cyomics_app.differential_jobs FROM PUBLIC,cyomicsv2_app_runtime,cyomicsv2_raw_qc_worker_runtime;
GRANT SELECT ON TABLE cyomics_app.differential_jobs TO cyomicsv2_app_runtime;
GRANT INSERT(job_id,project_id,owner_id,request_id,request_json,request_sha256,binding_sha256,processed_dataset_version_id,
 parent_dataset_version_id,file_id,sample_design_version_id,sample_design_request_id,quant_family,method_json,method_sha256,
 input_snapshot_json,input_snapshot_sha256,input_manifest_json,input_manifest_sha256,matrix_bytes,design_bytes,preset_bytes)
 ON TABLE cyomics_app.differential_jobs TO cyomicsv2_app_runtime;
-- No new roles/worker grants, no old table changes, no UPDATE/DELETE/TRUNCATE or runtime registration.
COMMIT;
