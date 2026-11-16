-- PRIVATE-TXT-IMPORT-001: SOURCE REVIEW ONLY. NEVER EXECUTED BY THIS TASK.
-- Temporary development bytea delta; private object storage/API003/API004 pending.
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
     OR to_regclass('cyomics_app.projects') IS NULL
     OR to_regclass('cyomics_app.raw_uploads') IS NOT NULL
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
     OR NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
       WHERE c.oid='cyomics_app.projects'::regclass AND c.relrowsecurity
         AND c.relforcerowsecurity AND r.rolname='cyomicsv2_app_owner') THEN
    RAISE EXCEPTION 'Independent business DB migration preflight failed';
  END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
CREATE TABLE cyomics_app.raw_uploads (
  file_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
  request_id uuid NOT NULL,
  filename text NOT NULL CHECK (octet_length(filename) BETWEEN 1 AND 255
    AND lower(right(filename,4))='.txt' AND filename !~ '[[:cntrl:]/\\]'),
  format text NOT NULL CHECK (format='proteinGroups_txt'),
  bytes integer NOT NULL CHECK (bytes BETWEEN 1 AND 10485760),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'stored' CHECK (status='stored'),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  content bytea NOT NULL,
  UNIQUE (project_id,request_id),
  CHECK (bytes=octet_length(content)),
  CHECK (sha256=encode(pg_catalog.sha256(content),'hex'))
);
CREATE INDEX raw_uploads_project_created_id_idx ON cyomics_app.raw_uploads
  (project_id,created_at DESC,file_id DESC);
ALTER TABLE cyomics_app.raw_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.raw_uploads FORCE ROW LEVEL SECURITY;
CREATE POLICY raw_uploads_owner_select ON cyomics_app.raw_uploads FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=raw_uploads.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY raw_uploads_owner_insert ON cyomics_app.raw_uploads FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=raw_uploads.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.raw_uploads FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.raw_uploads TO cyomicsv2_app_runtime;
GRANT INSERT (project_id,request_id,filename,format,bytes,sha256,content)
  ON TABLE cyomics_app.raw_uploads TO cyomicsv2_app_runtime;
-- No runtime UPDATE/DELETE/TRUNCATE/DDL, new roles, credentials or extensions.
COMMIT;
