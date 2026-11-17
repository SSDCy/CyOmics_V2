-- TXT-IMPORT-PERSIST-001: SOURCE REVIEW ONLY. NOT EXECUTED BY THIS TASK.
-- One immutable row atomically publishes DatasetVersion + ImportReport, no parsed cell table.
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
     OR to_regclass('cyomics_app.dataset_versions') IS NOT NULL
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
       WHERE c.oid IN ('cyomics_app.projects'::regclass,'cyomics_app.raw_uploads'::regclass)
         AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner')) THEN
    RAISE EXCEPTION 'Independent business DB import migration preflight failed';
  END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.dataset_versions (
  dataset_version_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
  file_id uuid NOT NULL REFERENCES cyomics_app.raw_uploads(file_id),
  parser_contract text NOT NULL CHECK (parser_contract='protein_groups_txt_v1'),
  config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  config_snapshot text NOT NULL CHECK (octet_length(config_snapshot) BETWEEN 1 AND 4096),
  source_bytes integer NOT NULL CHECK (source_bytes BETWEEN 1 AND 10485760),
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  representation text NOT NULL CHECK (representation='raw_backed'),
  parsed_table_materialized boolean NOT NULL CHECK (parsed_table_materialized=false),
  report_json text NOT NULL CHECK (octet_length(report_json) BETWEEN 1 AND 1048576),
  report_sha256 text NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  UNIQUE (file_id,parser_contract,config_sha256),
  CHECK (config_sha256=encode(pg_catalog.sha256(convert_to(config_snapshot,'UTF8')),'hex')),
  CHECK (report_sha256=encode(pg_catalog.sha256(convert_to(report_json,'UTF8')),'hex')),
  CHECK ((config_snapshot::jsonb @> '{"schemaVersion":1,"publisher_contract":"raw_backed_dataset_import_v1","parser_contract":"protein_groups_txt_v1","report_schema_version":1,"adapter_version":"raw_bytea_v1","representation":"raw_backed","parsed_table_materialized":false}'::jsonb
    AND jsonb_typeof(config_snapshot::jsonb->'parser_release')='object'
    AND jsonb_typeof(config_snapshot::jsonb->'limits')='object') IS TRUE),
  CHECK ((report_json::jsonb @> '{"schemaVersion":1,"representation":"raw_backed","parsed_table_materialized":false,"report":{"status":"parsed","persisted":true,"source_complete":true,"parser_contract_version":"protein_groups_txt_v1","association_status":"not_requested","association_counts":null,"scientific_analysis_status":"not_started","issues":[]}}'::jsonb
    AND report_json::jsonb->'report'->>'dataset_version_id'=dataset_version_id::text
    AND report_json::jsonb->'report'->'source'->>'project_id'=project_id::text
    AND report_json::jsonb->'report'->'source'->>'raw_file_id'=file_id::text
    AND report_json::jsonb->'report'->'source'->>'expected_raw_sha256'=source_sha256
    AND report_json::jsonb->'report'->>'observed_raw_sha256'=source_sha256
    AND report_json::jsonb->'report'->>'observed_bytes'=source_bytes::text
    AND jsonb_typeof(report_json::jsonb->'columns')='array'
    AND jsonb_typeof(report_json::jsonb->'sample_columns')='array'
    AND jsonb_typeof(report_json::jsonb->'row_source_spans')='array') IS TRUE)
);
ALTER TABLE cyomics_app.dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.dataset_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY dataset_versions_owner_select ON cyomics_app.dataset_versions FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id
    WHERE p.project_id=dataset_versions.project_id AND f.file_id=dataset_versions.file_id
      AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY dataset_versions_owner_insert ON cyomics_app.dataset_versions FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (EXISTS (SELECT 1 FROM cyomics_app.projects p
    JOIN cyomics_app.raw_uploads f ON f.project_id=p.project_id
    WHERE p.project_id=dataset_versions.project_id AND f.file_id=dataset_versions.file_id
      AND f.bytes=dataset_versions.source_bytes AND f.sha256=dataset_versions.source_sha256
      AND f.status='stored' AND f.format='proteinGroups_txt'
      AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.dataset_versions FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.dataset_versions TO cyomicsv2_app_runtime;
GRANT INSERT (dataset_version_id,project_id,file_id,parser_contract,config_sha256,config_snapshot,
  source_bytes,source_sha256,representation,parsed_table_materialized,report_json,report_sha256)
  ON TABLE cyomics_app.dataset_versions TO cyomicsv2_app_runtime;
-- No UPDATE/DELETE/TRUNCATE, new role, default privilege, SECURITY DEFINER or running DDL.
COMMIT;
