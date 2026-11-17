-- UPLOAD-CAPACITY-001 / S1-001 / G-002(part) / D-11 / API-003,004.
-- SOURCE REVIEW ONLY. NOT EXECUTED BY THIS TASK. Do not rerun 003 or 004.
-- Exactly two named CHECK replacements; no row, SHA, RLS, grant or role changes.
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
     OR (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
       WHERE c.oid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
         AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity
         AND r.rolname='cyomicsv2_app_owner') <> 2 THEN
    RAISE EXCEPTION 'Independent business DB capacity preflight failed';
  END IF;
END;
$guard$;
SET LOCAL ROLE cyomicsv2_app_owner;
LOCK TABLE cyomics_app.raw_uploads,cyomics_app.dataset_versions IN ACCESS EXCLUSIVE MODE;
DO $change$
DECLARE
  remaining_before jsonb;
  remaining_after jsonb;
  policies_before jsonb;
  policies_after jsonb;
  tables_before jsonb;
  tables_after jsonb;
BEGIN
  -- Exact PG17 catalog definitions; drift, absent names, already-upgraded state
  -- or unvalidated/inherted checks aborts before either ALTER. No fuzzy matching.
  IF (SELECT count(*) FROM pg_constraint c JOIN pg_attribute a
       ON a.attrelid=c.conrelid AND c.conkey=ARRAY[a.attnum]::smallint[]
       WHERE c.contype='c' AND c.convalidated AND c.conislocal
         AND c.coninhcount=0 AND NOT c.connoinherit AND NOT a.attisdropped
         AND ((c.conrelid='cyomics_app.raw_uploads'::regclass
           AND c.conname='raw_uploads_bytes_check' AND a.attname='bytes'
           AND pg_get_constraintdef(c.oid,false)='CHECK (((bytes >= 1) AND (bytes <= 10485760)))')
         OR (c.conrelid='cyomics_app.dataset_versions'::regclass
           AND c.conname='dataset_versions_source_bytes_check' AND a.attname='source_bytes'
           AND pg_get_constraintdef(c.oid,false)='CHECK (((source_bytes >= 1) AND (source_bytes <= 10485760)))'))) <> 2 THEN
    RAISE EXCEPTION 'Capacity old named CHECK definitions differ; no changes allowed';
  END IF;
  SELECT jsonb_agg(jsonb_build_object('table',c.conrelid::text,'name',c.conname,
      'definition',pg_get_constraintdef(c.oid,false),'validated',c.convalidated) ORDER BY c.conrelid,c.conname)
    INTO remaining_before FROM pg_constraint c
    WHERE c.conrelid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
      AND NOT ((c.conrelid='cyomics_app.raw_uploads'::regclass AND c.conname='raw_uploads_bytes_check')
        OR (c.conrelid='cyomics_app.dataset_versions'::regclass AND c.conname='dataset_versions_source_bytes_check'));
  SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) INTO policies_before FROM pg_policy p
    WHERE p.polrelid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass);
  SELECT jsonb_agg(jsonb_build_object('table',c.oid::text,'owner',c.relowner,
      'acl',c.relacl,'rls',c.relrowsecurity,'force',c.relforcerowsecurity) ORDER BY c.oid)
    INTO tables_before FROM pg_class c
    WHERE c.oid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass);

  ALTER TABLE cyomics_app.raw_uploads DROP CONSTRAINT raw_uploads_bytes_check,
    ADD CONSTRAINT raw_uploads_bytes_check CHECK (bytes BETWEEN 1 AND 26214400);
  ALTER TABLE cyomics_app.dataset_versions DROP CONSTRAINT dataset_versions_source_bytes_check,
    ADD CONSTRAINT dataset_versions_source_bytes_check CHECK (source_bytes BETWEEN 1 AND 26214400);

  SELECT jsonb_agg(jsonb_build_object('table',c.conrelid::text,'name',c.conname,
      'definition',pg_get_constraintdef(c.oid,false),'validated',c.convalidated) ORDER BY c.conrelid,c.conname)
    INTO remaining_after FROM pg_constraint c
    WHERE c.conrelid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass)
      AND NOT ((c.conrelid='cyomics_app.raw_uploads'::regclass AND c.conname='raw_uploads_bytes_check')
        OR (c.conrelid='cyomics_app.dataset_versions'::regclass AND c.conname='dataset_versions_source_bytes_check'));
  SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) INTO policies_after FROM pg_policy p
    WHERE p.polrelid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass);
  SELECT jsonb_agg(jsonb_build_object('table',c.oid::text,'owner',c.relowner,
      'acl',c.relacl,'rls',c.relrowsecurity,'force',c.relforcerowsecurity) ORDER BY c.oid)
    INTO tables_after FROM pg_class c
    WHERE c.oid IN ('cyomics_app.raw_uploads'::regclass,'cyomics_app.dataset_versions'::regclass);
  IF remaining_after IS DISTINCT FROM remaining_before
     OR policies_after IS DISTINCT FROM policies_before
     OR tables_after IS DISTINCT FROM tables_before
     OR (SELECT count(*) FROM pg_constraint c WHERE c.convalidated
       AND ((c.conrelid='cyomics_app.raw_uploads'::regclass AND c.conname='raw_uploads_bytes_check'
         AND pg_get_constraintdef(c.oid,false)='CHECK (((bytes >= 1) AND (bytes <= 26214400)))')
       OR (c.conrelid='cyomics_app.dataset_versions'::regclass AND c.conname='dataset_versions_source_bytes_check'
         AND pg_get_constraintdef(c.oid,false)='CHECK (((source_bytes >= 1) AND (source_bytes <= 26214400)))'))) <> 2 THEN
    RAISE EXCEPTION 'Capacity metadata postcondition failed; transaction must roll back';
  END IF;
END;
$change$;
COMMIT;
