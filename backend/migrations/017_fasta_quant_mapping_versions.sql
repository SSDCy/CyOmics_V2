-- S1-FASTA-QUANT-MAPPING-HTTP-001: DRAFT ONLY. NOT APPLIED OR REPLAYED.
-- Requires separately reviewed DDL/ACL/RLS/behavior QA and explicit apply approval.
-- Parent changes ONLY the three composite unique keys required by same-project FKs.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '1s';
SET LOCAL search_path = pg_catalog;
DO $guard$
BEGIN
  IF current_database() <> 'cyomicsv2_business_dev'
     OR current_user <> 'postgres' OR session_user <> 'postgres'
     OR current_setting('cyomics.cluster_kind',true) IS DISTINCT FROM 'businessdev'
     OR current_setting('server_version_num')::integer / 10000 <> 17
     OR to_regclass('cyomics_app.raw_uploads') IS NULL
     OR to_regclass('cyomics_app.fasta_sources') IS NULL
     OR to_regclass('cyomics_app.fasta_index_versions') IS NULL
     OR to_regclass('cyomics_app.dataset_versions') IS NULL
     OR to_regclass('cyomics_app.fasta_quant_mapping_versions') IS NOT NULL
     OR to_regprocedure('pg_catalog.sha256(bytea)') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
       WHERE d.datname=current_database() AND r.rolname='cyomicsv2_app_owner')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_owner'
       AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
       AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls)
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cyomicsv2_app_runtime'
       AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb
       AND NOT rolcreaterole AND NOT rolinherit AND NOT rolreplication)
     OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
       WHERE r.rolname IN ('cyomicsv2_app_owner','cyomicsv2_app_runtime'))
     OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
       WHERE c.oid IN ('cyomics_app.projects'::regclass,'cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass,'cyomics_app.fasta_sources'::regclass,'cyomics_app.fasta_index_versions'::regclass)
         AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner')) THEN
    RAISE EXCEPTION 'Independent business DB import migration preflight failed';
  END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
ALTER TABLE cyomics_app.dataset_versions ADD CONSTRAINT mapping_dataset_project_key UNIQUE (project_id,dataset_version_id);
ALTER TABLE cyomics_app.fasta_sources ADD CONSTRAINT mapping_fasta_source_project_key UNIQUE (project_id,source_id);
ALTER TABLE cyomics_app.fasta_index_versions ADD CONSTRAINT mapping_fasta_index_project_key UNIQUE (project_id,source_id,index_version_id);
CREATE TABLE cyomics_app.fasta_quant_mapping_versions (
  mapping_version_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
  created_by uuid NOT NULL,
  dataset_version_id uuid NOT NULL,
  fasta_source_id uuid NOT NULL,
  fasta_index_version_id uuid NOT NULL,
  algorithm text NOT NULL CHECK (algorithm='protein_groups_exact_fasta_members_v1'),
  binding_sha256 text NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
  metadata_json text NOT NULL CHECK (octet_length(metadata_json) BETWEEN 1 AND 65536),
  metadata_sha256 text NOT NULL CHECK (metadata_sha256 ~ '^[0-9a-f]{64}$'),
  result_json bytea NOT NULL,
  result_bytes integer NOT NULL CHECK (result_bytes BETWEEN 1 AND 33554432),
  result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL,
  UNIQUE (project_id,dataset_version_id,fasta_source_id,fasta_index_version_id,algorithm),
  UNIQUE (project_id,binding_sha256),
  FOREIGN KEY (project_id,dataset_version_id) REFERENCES cyomics_app.dataset_versions(project_id,dataset_version_id),
  FOREIGN KEY (project_id,fasta_source_id) REFERENCES cyomics_app.fasta_sources(project_id,source_id),
  FOREIGN KEY (project_id,fasta_source_id,fasta_index_version_id) REFERENCES cyomics_app.fasta_index_versions(project_id,source_id,index_version_id),
  CHECK (result_bytes=octet_length(result_json)),
  CHECK (result_sha256=encode(pg_catalog.sha256(result_json),'hex')),
  CHECK (metadata_sha256=encode(pg_catalog.sha256(convert_to(metadata_json,'UTF8')),'hex')),
  CHECK ((convert_from(result_json,'UTF8')::jsonb @> '{"schemaVersion":1,"annotation_ready":false,"owner_authorized":false,"published":false}'::jsonb
    AND convert_from(result_json,'UTF8')::jsonb->>'mapping_version_id'=mapping_version_id::text
    AND convert_from(result_json,'UTF8')::jsonb->>'algorithm'=algorithm
    AND convert_from(result_json,'UTF8')::jsonb->>'binding_sha256'=binding_sha256
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,algorithm}'=algorithm
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,quantification,project_id}'=project_id::text
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,quantification,dataset_version_id}'=dataset_version_id::text
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,fasta,project_id}'=project_id::text
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,fasta,source_id}'=fasta_source_id::text
    AND convert_from(result_json,'UTF8')::jsonb#>>'{inputs,fasta,index_version_id}'=fasta_index_version_id::text
    AND jsonb_typeof(convert_from(result_json,'UTF8')::jsonb->'rows')='array'
    AND jsonb_array_length(convert_from(result_json,'UTF8')::jsonb->'rows')::text=convert_from(result_json,'UTF8')::jsonb#>>'{summary,row_count}') IS TRUE),
  CHECK ((metadata_json::jsonb @> '{"schemaVersion":1,"contract":"fasta_quant_mapping_saved_v0.1","status":"stored","result":{"name":"mapping.json","media_type":"application/json"}}'::jsonb
    AND metadata_json::jsonb->>'mapping_version_id'=mapping_version_id::text
    AND metadata_json::jsonb->>'project_id'=project_id::text
    AND metadata_json::jsonb->>'created_by'=created_by::text
    AND metadata_json::jsonb->>'algorithm'=algorithm
    AND metadata_json::jsonb->>'binding_sha256'=binding_sha256
    AND metadata_json::jsonb->'inputs'=convert_from(result_json,'UTF8')::jsonb->'inputs'
    AND metadata_json::jsonb->'summary'=convert_from(result_json,'UTF8')::jsonb->'summary'
    AND metadata_json::jsonb#>>'{result,bytes}'=result_bytes::text
    AND metadata_json::jsonb#>>'{result,sha256}'=result_sha256) IS TRUE)
);
ALTER TABLE cyomics_app.fasta_quant_mapping_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.fasta_quant_mapping_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY fasta_quant_mapping_owner_select ON cyomics_app.fasta_quant_mapping_versions FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_quant_mapping_versions.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY fasta_quant_mapping_owner_insert ON cyomics_app.fasta_quant_mapping_versions FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (created_by=(SELECT cyomics_app.current_user_id()) AND EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_quant_mapping_versions.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.fasta_quant_mapping_versions FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.fasta_quant_mapping_versions TO cyomicsv2_app_runtime;
GRANT INSERT (mapping_version_id,project_id,created_by,dataset_version_id,fasta_source_id,fasta_index_version_id,algorithm,binding_sha256,metadata_json,metadata_sha256,result_json,result_bytes,result_sha256,created_at)
  ON TABLE cyomics_app.fasta_quant_mapping_versions TO cyomicsv2_app_runtime;
-- No runtime UPDATE/DELETE/TRUNCATE. No existing data edits or role changes.
COMMIT;
