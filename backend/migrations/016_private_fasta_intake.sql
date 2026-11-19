-- S1-FASTA-PRIVATE-PERSISTENCE-CORE-001: DRAFT ONLY, NEVER APPLIED.
-- Atomic immutable raw source + source-backed full-index proof.
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
     OR to_regclass('cyomics_app.fasta_sources') IS NOT NULL
     OR to_regclass('cyomics_app.fasta_index_versions') IS NOT NULL
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
CREATE TABLE cyomics_app.fasta_sources (
  source_id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES cyomics_app.projects(project_id),
  request_id uuid NOT NULL,
  filename text NOT NULL CHECK (octet_length(filename) BETWEEN 1 AND 255
    AND lower(filename) ~ '[.](fa|faa|fasta)$' AND filename !~ '[[:cntrl:]/\\]'),
  format text NOT NULL CHECK (format='protein_fasta'),
  bytes integer NOT NULL CHECK (bytes BETWEEN 1 AND 26214400),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL CHECK (status='stored'),
  created_at timestamptz NOT NULL,
  content bytea NOT NULL,
  UNIQUE (project_id,request_id),
  UNIQUE (source_id,project_id,bytes,sha256),
  CHECK (bytes=octet_length(content)),
  CHECK (sha256=encode(pg_catalog.sha256(content),'hex'))
);
CREATE TABLE cyomics_app.fasta_index_versions (
  index_version_id uuid PRIMARY KEY,
  project_id uuid NOT NULL,
  source_id uuid NOT NULL UNIQUE,
  source_bytes integer NOT NULL,
  source_sha256 text NOT NULL,
  config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  index_manifest_sha256 text NOT NULL CHECK (index_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  candidate_json text NOT NULL CHECK (octet_length(candidate_json) BETWEEN 1 AND 16384),
  created_at timestamptz NOT NULL,
  FOREIGN KEY (source_id,project_id,source_bytes,source_sha256)
    REFERENCES cyomics_app.fasta_sources(source_id,project_id,bytes,sha256),
  UNIQUE (source_id,config_sha256),
  CHECK ((candidate_json::jsonb @> '{"representation":"fasta_source_backed_index","index_materialized":false,"source_complete":true,"status":"indexed","index_contract":"fasta_ascii_identity_index_v0.1"}'::jsonb
    AND candidate_json::jsonb->>'index_version_id'=index_version_id::text
    AND candidate_json::jsonb->>'source_id'=source_id::text
    AND candidate_json::jsonb->>'project_id'=project_id::text
    AND candidate_json::jsonb->>'source_bytes'=source_bytes::text
    AND candidate_json::jsonb->>'source_sha256'=source_sha256
    AND candidate_json::jsonb->>'config_sha256'=config_sha256
    AND candidate_json::jsonb->>'index_manifest_sha256'=index_manifest_sha256
    AND config_sha256=encode(pg_catalog.sha256(convert_to(candidate_json::jsonb->>'config_json','UTF8')),'hex')
    AND candidate_json::jsonb->>'summary_sha256'=encode(pg_catalog.sha256(convert_to(candidate_json::jsonb->>'summary_json','UTF8')),'hex')) IS TRUE)
);
ALTER TABLE cyomics_app.fasta_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.fasta_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY fasta_sources_owner_select ON cyomics_app.fasta_sources FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_sources.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY fasta_sources_owner_insert ON cyomics_app.fasta_sources FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_sources.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.fasta_sources FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.fasta_sources TO cyomicsv2_app_runtime;
ALTER TABLE cyomics_app.fasta_index_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.fasta_index_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY fasta_index_versions_owner_select ON cyomics_app.fasta_index_versions FOR SELECT
  TO cyomicsv2_app_runtime USING (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_index_versions.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
CREATE POLICY fasta_index_versions_owner_insert ON cyomics_app.fasta_index_versions FOR INSERT
  TO cyomicsv2_app_runtime WITH CHECK (EXISTS (SELECT 1 FROM cyomics_app.projects p
    WHERE p.project_id=fasta_index_versions.project_id AND p.owner_id=(SELECT cyomics_app.current_user_id())));
REVOKE ALL PRIVILEGES ON TABLE cyomics_app.fasta_index_versions FROM PUBLIC,cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.fasta_index_versions TO cyomicsv2_app_runtime;
GRANT INSERT (source_id,project_id,request_id,filename,format,bytes,sha256,status,created_at,content)
  ON TABLE cyomics_app.fasta_sources TO cyomicsv2_app_runtime;
GRANT INSERT (index_version_id,project_id,source_id,source_bytes,source_sha256,config_sha256,index_manifest_sha256,candidate_json,created_at)
  ON TABLE cyomics_app.fasta_index_versions TO cyomicsv2_app_runtime;
-- No UPDATE/DELETE/TRUNCATE, existing table ALTER, authentication or role changes.
COMMIT;
