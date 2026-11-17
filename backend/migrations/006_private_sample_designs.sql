-- SAMPLE-DESIGN-PERSIST-001: SOURCE ONLY. NOT EXECUTED.
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
     OR to_regclass('cyomics_app.dataset_versions') IS NULL
     OR to_regclass('cyomics_app.sample_design_versions') IS NOT NULL
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
       WHERE c.oid IN ('cyomics_app.projects'::regclass,'cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
         AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity OR r.rolname<>'cyomicsv2_app_owner')) THEN
    RAISE EXCEPTION 'Independent business DB import migration preflight failed';
  END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.sample_design_versions (
  sample_design_version_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
  dataset_version_id uuid NOT NULL REFERENCES cyomics_app.dataset_versions(dataset_version_id),
  quant_family text NOT NULL CHECK (quant_family IN ('Intensity','LFQ intensity')),
  parent_version_id uuid,
  revision integer NOT NULL CHECK (revision BETWEEN 1 AND 100),
  request_id uuid NOT NULL,
  request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  design_json text NOT NULL CHECK (octet_length(design_json) BETWEEN 1 AND 131072),
  design_sha256 text NOT NULL CHECK (design_sha256 ~ '^[0-9a-f]{64}$'),
  model_contract text NOT NULL CHECK (model_contract='sample_design_review_v0.2'),
  validator_sha256 text NOT NULL CHECK (validator_sha256='c19d4f55de08154b4cafca216835b86a210b1919c16e85eaf99226750eb007ed'),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  UNIQUE (project_id,dataset_version_id,quant_family,sample_design_version_id),
  UNIQUE (dataset_version_id,quant_family,revision),
  UNIQUE (dataset_version_id,quant_family,request_id),
  FOREIGN KEY (project_id,dataset_version_id,quant_family,parent_version_id)
    REFERENCES cyomics_app.sample_design_versions(project_id,dataset_version_id,quant_family,sample_design_version_id),
  CHECK ((revision=1) = (parent_version_id IS NULL)),
  CHECK (parent_version_id IS DISTINCT FROM sample_design_version_id),
  CHECK (design_sha256=encode(pg_catalog.sha256(convert_to(design_json,'UTF8')),'hex')),
  CHECK ((design_json::jsonb @> '{"schemaVersion":1}'::jsonb
    AND design_json::jsonb->>'dataset_version_id'=dataset_version_id::text
    AND design_json::jsonb->>'quant_family'=quant_family
    AND jsonb_typeof(design_json::jsonb->'rows')='array') IS TRUE)
);
ALTER TABLE cyomics_app.sample_design_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.sample_design_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY sample_design_owner_select ON cyomics_app.sample_design_versions FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
    WHERE p.project_id=sample_design_versions.project_id
      AND d.dataset_version_id=sample_design_versions.dataset_version_id
      AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY sample_design_owner_insert ON cyomics_app.sample_design_versions FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (EXISTS (SELECT 1 FROM cyomics_app.projects p
    JOIN cyomics_app.dataset_versions d ON d.project_id=p.project_id
    WHERE p.project_id=sample_design_versions.project_id
      AND d.dataset_version_id=sample_design_versions.dataset_version_id
      AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.sample_design_versions FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.sample_design_versions TO cyomicsv2_app_runtime;
GRANT INSERT (sample_design_version_id,project_id,dataset_version_id,quant_family,parent_version_id,
  revision,request_id,request_sha256,design_json,design_sha256,model_contract,validator_sha256)
  ON TABLE cyomics_app.sample_design_versions TO cyomicsv2_app_runtime;
-- No existing-table changes, UPDATE/DELETE/TRUNCATE, roles, triggers, SECURITY DEFINER.
COMMIT;
