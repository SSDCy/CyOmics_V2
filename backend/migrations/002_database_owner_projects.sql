-- PROJECT-DB-DRAFT-001: REVIEW DRAFT ONLY. Never executed in this task.
-- Future NEW INDEPENDENT businessdev socket; never Auth cluster/port 57422.
-- Connection: cyomicsv2_business_dev, session user postgres.
-- Bootstrap postgres performs catalog assertions and SET ROLE only; no user rows.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '1s';
SET LOCAL search_path = pg_catalog;
DO $preflight$
BEGIN
  IF current_database() <> 'cyomicsv2_business_dev'
     OR current_user <> 'postgres' OR session_user <> 'postgres'
     OR current_setting('server_version_num')::integer / 10000 <> 17
     OR current_setting('cyomics.cluster_kind', true) IS DISTINCT FROM 'businessdev'
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname = current_user AND rolsuper) THEN
    RAISE EXCEPTION 'Unexpected migration connection';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database d
                  JOIN pg_catalog.pg_roles r ON r.oid = d.datdba
                  WHERE d.datname = current_database()
                    AND r.rolname = 'cyomicsv2_app_owner')
     OR NOT pg_catalog.pg_has_role(current_user, 'cyomicsv2_app_owner', 'SET') THEN
    RAISE EXCEPTION 'Unexpected database owner or migration membership';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                  WHERE rolname = 'cyomicsv2_app_owner' AND NOT rolcanlogin
                    AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
                    AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls)
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                  WHERE rolname = 'cyomicsv2_app_runtime' AND rolcanlogin
                    AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
                    AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                  JOIN pg_catalog.pg_roles r ON r.oid = m.member
                  WHERE r.rolname IN ('cyomicsv2_app_owner', 'cyomicsv2_app_runtime')) THEN
    RAISE EXCEPTION 'Unexpected role attributes or parent memberships';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = 'cyomics_app')
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
                 WHERE nspname !~ '^pg_' AND nspname NOT IN ('public', 'information_schema'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c
                 JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public')
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                 JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public')
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname <> 'plpgsql') THEN
    RAISE EXCEPTION 'New database is not pristine; no changes applied';
  END IF;
  IF NOT pg_catalog.has_database_privilege('cyomicsv2_app_runtime', current_database(), 'CONNECT')
     OR pg_catalog.has_database_privilege('cyomicsv2_app_runtime', current_database(), 'CREATE')
     OR pg_catalog.has_database_privilege('cyomicsv2_app_runtime', current_database(), 'TEMPORARY') THEN
    RAISE EXCEPTION 'Unexpected runtime database privileges';
  END IF;
END;
$preflight$;

SET LOCAL ROLE cyomicsv2_app_owner;
-- This public schema is in the NEW business database, never postgres/auth DB.
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC, cyomicsv2_app_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE cyomicsv2_app_owner
  REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE cyomicsv2_app_owner
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE cyomicsv2_app_owner
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE cyomicsv2_app_owner
  REVOKE USAGE ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE cyomicsv2_app_owner
  REVOKE ALL PRIVILEGES ON SCHEMAS FROM PUBLIC;
-- No defaults granting runtime access to future tables/functions.
CREATE SCHEMA cyomics_app AUTHORIZATION cyomicsv2_app_owner;
REVOKE ALL PRIVILEGES ON SCHEMA cyomics_app FROM PUBLIC;

CREATE FUNCTION cyomics_app.current_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $identity$
DECLARE subject text;
BEGIN
  subject := pg_catalog.current_setting('app.user_id', true);
  IF subject IS NULL OR subject = '' OR subject !~
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
    RETURN NULL;
  END IF;
  RETURN subject::uuid;
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END;
$identity$;
REVOKE ALL PRIVILEGES ON FUNCTION cyomics_app.current_user_id() FROM PUBLIC;

CREATE TABLE cyomics_app.workspaces (
  workspace_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  owner_id uuid NOT NULL UNIQUE,
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility = 'private'),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  CONSTRAINT workspaces_id_owner_unique UNIQUE (workspace_id, owner_id)
);
CREATE TABLE cyomics_app.projects (
  project_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  owner_id uuid NOT NULL,
  created_by uuid NOT NULL,
  workspace_id uuid NOT NULL,
  name text NOT NULL CHECK (pg_catalog.char_length(name) BETWEEN 1 AND 80
    AND name = pg_catalog.btrim(name) AND name !~ '[[:cntrl:]]'),
  description text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(description) <= 1000),
  species text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(species) <= 200
    AND species = pg_catalog.btrim(species) AND species !~ '[[:cntrl:]]'),
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility = 'private'),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT pg_catalog.statement_timestamp(),
  CONSTRAINT projects_creator_is_owner CHECK (created_by = owner_id),
  CONSTRAINT projects_workspace_owner_fk FOREIGN KEY (workspace_id, owner_id)
    REFERENCES cyomics_app.workspaces (workspace_id, owner_id)
);
CREATE INDEX projects_owner_created_id_idx ON cyomics_app.projects
  (owner_id, created_at DESC, project_id DESC);

ALTER TABLE cyomics_app.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyomics_app.projects FORCE ROW LEVEL SECURITY;
CREATE POLICY workspaces_owner_select ON cyomics_app.workspaces
  FOR SELECT TO cyomicsv2_app_runtime
  USING (owner_id = (SELECT cyomics_app.current_user_id()));
CREATE POLICY workspaces_owner_insert ON cyomics_app.workspaces
  FOR INSERT TO cyomicsv2_app_runtime
  WITH CHECK (owner_id = (SELECT cyomics_app.current_user_id()));
CREATE POLICY projects_owner_select ON cyomics_app.projects
  FOR SELECT TO cyomicsv2_app_runtime
  USING (owner_id = (SELECT cyomics_app.current_user_id()));
CREATE POLICY projects_owner_insert ON cyomics_app.projects
  FOR INSERT TO cyomicsv2_app_runtime
  WITH CHECK (owner_id = (SELECT cyomics_app.current_user_id())
    AND created_by = (SELECT cyomics_app.current_user_id()));

REVOKE ALL PRIVILEGES ON TABLE cyomics_app.workspaces, cyomics_app.projects FROM PUBLIC;
GRANT USAGE ON SCHEMA cyomics_app TO cyomicsv2_app_runtime;
GRANT EXECUTE ON FUNCTION cyomics_app.current_user_id() TO cyomicsv2_app_runtime;
GRANT SELECT ON TABLE cyomics_app.workspaces, cyomics_app.projects TO cyomicsv2_app_runtime;
GRANT INSERT (owner_id) ON TABLE cyomics_app.workspaces TO cyomicsv2_app_runtime;
GRANT INSERT (owner_id, created_by, workspace_id, name, description, species)
  ON TABLE cyomics_app.projects TO cyomicsv2_app_runtime;
-- No runtime UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/DDL or role membership.
-- No sequences, security-definer functions, auth-table FKs, or business-row reads.
COMMIT;
