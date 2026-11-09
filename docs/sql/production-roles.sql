-- Production provisioning of the database roles, run by the platform team (NOT by migrations or
-- the app). Mirrors backend/migrations/versions/0002_governance.py: the migration creates the
-- runtime roles only if they are missing (local dev); in production they already exist, have no
-- passwords in the repo, and their credentials live in the secret manager.
--
-- Run as a cluster admin (e.g. the managed-Postgres admin), once per environment.
-- Placeholders: :'migrator_password', :'api_password', :'worker_password' are passed by psql -v
-- from the secret manager; prefer IAM/Entra token auth over passwords where the provider
-- supports it (Azure Database for PostgreSQL: Entra auth; RDS: IAM auth).

\set ON_ERROR_STOP on

BEGIN;

-- 1. Schema owner / migrator. Runs alembic in CI/CD only. Never serves traffic.
--    Owns the tables (so owner-bypass of RLS is confined to the migration job).
CREATE ROLE app_migrator LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 2;

-- 2. Runtime roles. Same attributes the governance tests assert from pg_roles
--    (tests/security/test_db_governance.py): no superuser, no BYPASSRLS, no role/db creation,
--    not members of any other role, not table owners.
CREATE ROLE app_api LOGIN PASSWORD :'api_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 50;
CREATE ROLE app_worker LOGIN PASSWORD :'worker_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 10;

-- Defense in depth against runaway queries and idle-in-transaction connections holding locks.
ALTER ROLE app_api    SET statement_timeout = '5s';
ALTER ROLE app_api    SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE app_worker SET statement_timeout = '30s';
ALTER ROLE app_worker SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE app_api    SET search_path = public;
ALTER ROLE app_worker SET search_path = public;

COMMIT;

-- 3. Database + schema. Run connected to the cluster's maintenance DB.
CREATE DATABASE app OWNER app_migrator;
REVOKE ALL ON DATABASE app FROM PUBLIC;
GRANT CONNECT ON DATABASE app TO app_api, app_worker;

\connect app

-- No one but the owner may create objects in public (PG15+ default; explicit for older).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO app_migrator;
GRANT USAGE ON SCHEMA public TO app_api, app_worker;

-- 4. Table-level grants, RLS and policies are applied by migration 0002 (run as app_migrator),
--    so they are versioned and reviewed with the schema. The migration's role-creation block is
--    a no-op here because the roles already exist.
--
-- 5. After every deploy, CI runs the catalog checks in tests/security/test_db_governance.py
--    against a fresh migration. To check a live environment, run the same assertions read-only:
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
FROM pg_roles WHERE rolname IN ('app_api', 'app_worker');

SELECT c.relname, c.relrowsecurity,
       has_table_privilege('app_api', c.oid, 'DELETE')    AS api_delete,
       has_table_privilege('app_worker', c.oid, 'DELETE') AS worker_delete,
       has_table_privilege('app_api', c.oid, 'TRUNCATE')  AS api_truncate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY 1;

-- 6. Credential rotation: create the new password in the secret manager, then
--    ALTER ROLE app_api PASSWORD :'new_api_password'; roll the API pods; revoke the old secret.
