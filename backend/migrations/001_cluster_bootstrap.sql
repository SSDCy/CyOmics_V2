-- PROJECT-DB-DRAFT-001: REVIEW DRAFT ONLY. NEVER EXECUTED.
-- FUTURE INDEPENDENT cyomicsv2_businessdev cluster ONLY; never Auth port 57422.
-- New cluster marker and superuser requirements intentionally reject Auth DB.
-- Future socket connection: OS postgres, database/user postgres; NO TCP admin.
-- No passwords. Credential provisioning requires its own approved task.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '1s';
SET LOCAL search_path = pg_catalog;
DO $preflight$
BEGIN
  IF current_database() <> 'postgres' OR current_user <> 'postgres'
     OR session_user <> 'postgres'
     OR current_setting('cyomics.cluster_kind', true) IS DISTINCT FROM 'businessdev'
     OR current_setting('server_version_num')::integer / 10000 <> 17
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname = current_user AND rolsuper) THEN
    RAISE EXCEPTION 'Wrong cluster or bootstrap identity; no changes allowed';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_database
              WHERE datname NOT IN ('postgres', 'template0', 'template1'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname !~ '^pg_' AND rolname <> 'postgres') THEN
    RAISE EXCEPTION 'Cluster is not a pristine independent business cluster';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_database
              WHERE datname = 'cyomicsv2_business_dev')
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN
                 ('cyomicsv2_app_owner', 'cyomicsv2_app_runtime')) THEN
    RAISE EXCEPTION 'Target names already exist; stop, do not retry blindly';
  END IF;
END;
$preflight$;
-- These ACLs belong ONLY to the NEW independent cluster, never existing Auth.
REVOKE ALL PRIVILEGES ON DATABASE postgres, template0, template1 FROM PUBLIC;
CREATE ROLE cyomicsv2_app_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE cyomicsv2_app_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
-- Omitted PASSWORD remains NULL. No parent-role memberships are granted.
-- Independent bootstrap postgres is superuser and can SET ROLE without grants.
COMMIT;

-- CREATE DATABASE CANNOT be in a transaction. Do not use psql -1.
-- A later failure may leave committed roles or a closed new DB. Stop for review;
-- no automatic DROP/retry/cleanup. Connections remain closed until ACL commit.
CREATE DATABASE cyomicsv2_business_dev WITH OWNER = cyomicsv2_app_owner
  TEMPLATE = template0 ENCODING = 'UTF8' ALLOW_CONNECTIONS = false;

BEGIN;
SET LOCAL statement_timeout = '2s';
SET LOCAL lock_timeout = '1s';
SET LOCAL search_path = pg_catalog;
DO $verify_new_database$
BEGIN
  IF current_database() <> 'postgres' OR current_user <> 'postgres'
     OR current_setting('cyomics.cluster_kind', true) IS DISTINCT FROM 'businessdev'
     OR NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r
         ON r.oid = d.datdba
       WHERE d.datname = 'cyomicsv2_business_dev'
         AND r.rolname = 'cyomicsv2_app_owner' AND NOT d.datallowconn
     ) THEN
    RAISE EXCEPTION 'Unexpected independent database state; stop for review';
  END IF;
END;
$verify_new_database$;
ALTER ROLE cyomicsv2_app_runtime IN DATABASE cyomicsv2_business_dev
  SET search_path = pg_catalog, cyomics_app;
ALTER ROLE cyomicsv2_app_runtime IN DATABASE cyomicsv2_business_dev
  SET statement_timeout = '2s';
ALTER ROLE cyomicsv2_app_runtime IN DATABASE cyomicsv2_business_dev
  SET lock_timeout = '1s';
SET LOCAL ROLE cyomicsv2_app_owner;
REVOKE ALL PRIVILEGES ON DATABASE cyomicsv2_business_dev FROM PUBLIC;
GRANT CONNECT ON DATABASE cyomicsv2_business_dev TO cyomicsv2_app_runtime;
ALTER DATABASE cyomicsv2_business_dev ALLOW_CONNECTIONS true;
COMMIT;
-- Stop. Next file requires a NEW independent socket connection to business DB.
