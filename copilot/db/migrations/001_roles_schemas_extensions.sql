-- 001: extensions, roles, schemas. No passwords or secrets here: bootstrap() sets them from the environment.
CREATE EXTENSION IF NOT EXISTS vector;          -- infrastructure only in M1 (embeddings tables arrive in M2)

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_owner')  THEN CREATE ROLE copilot_owner  NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_app')    THEN CREATE ROLE copilot_app    NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_loader') THEN CREATE ROLE copilot_loader NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_intake') THEN CREATE ROLE copilot_intake NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS copilot;            -- application data
CREATE SCHEMA IF NOT EXISTS copilot_private;    -- scope keys + verification functions: the app role can EXECUTE one function here and read nothing
