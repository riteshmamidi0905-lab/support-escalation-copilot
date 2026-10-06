"""Administrative operations run with an ADMIN connection (superuser or equivalent) — never at application run time:
create a database, run migrations, bootstrap role passwords and the scope signing key."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_LOCK_KEY = 7243906
ROLES = ("copilot_app", "copilot_loader", "copilot_intake")


def role_dsn(admin_dsn: str, role: str, password: str, dbname: str | None = None) -> str:
    info = conninfo_to_dict(admin_dsn)
    info.update(user=role, password=password)
    info.pop("passfile", None)
    if dbname:
        info["dbname"] = dbname
    return make_conninfo(**info)


def with_dbname(admin_dsn: str, dbname: str) -> str:
    info = conninfo_to_dict(admin_dsn)
    info["dbname"] = dbname
    return make_conninfo(**info)


def create_database(admin_dsn: str, dbname: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", dbname):
        raise ValueError("unsafe database name")
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    return with_dbname(admin_dsn, dbname)


def drop_database(admin_dsn: str, dbname: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


def migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", p.name))


def migrate(db_admin_dsn: str) -> list[str]:
    """Forward-only, each file in its own transaction, recorded in public.schema_migrations, serialised by an advisory lock. Returns versions applied."""
    applied: list[str] = []
    with psycopg.connect(db_admin_dsn, autocommit=True) as c:
        c.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            c.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {r[0] for r in c.execute("SELECT version FROM public.schema_migrations").fetchall()}
            for f in migration_files():
                if f.name in done:
                    continue
                with c.transaction():
                    c.execute(f.read_text())
                    c.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (f.name,))
                applied.append(f.name)
        finally:
            c.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied


def bootstrap(db_admin_dsn: str, passwords: dict, scope_secret: str, key_id: str = "k1") -> None:
    """Give the LOGIN roles their passwords and install the scope signing key. All values come from the environment/caller; nothing is stored in SQL files."""
    if len(scope_secret.encode()) < 32:
        raise ValueError("scope secret must be at least 32 bytes")
    missing = [r for r in ROLES if not passwords.get(r)]
    if missing:
        raise ValueError(f"missing passwords for {missing}")
    with psycopg.connect(db_admin_dsn, autocommit=True) as c:
        for role in ROLES:
            c.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(passwords[role])))
        c.execute("INSERT INTO copilot_private.scope_keys (key_id, secret, active) VALUES (%s, %s, true) ON CONFLICT (key_id) DO UPDATE SET secret = EXCLUDED.secret, active = true",
                  (key_id, scope_secret))


def table_names(db_admin_dsn: str, schema: str = "copilot") -> Iterable[str]:
    with psycopg.connect(db_admin_dsn) as c:
        return [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY 1", (schema,)).fetchall()]


def audit_roles(db_admin_dsn: str) -> list[str]:
    """Return a list of problems with the application roles' privileges (empty = healthy). Run at startup and in CI: a role that can bypass RLS, own
    objects, create things or belong to a powerful role would silently void tenant isolation."""
    probs: list[str] = []
    with psycopg.connect(db_admin_dsn) as c:
        for role in ROLES:
            r = c.execute("SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if r is None:
                probs.append(f"{role}: missing")
                continue
            for flag, name in zip(r[:5], ("SUPERUSER", "BYPASSRLS", "CREATEDB", "CREATEROLE", "REPLICATION"), strict=True):
                if flag:
                    probs.append(f"{role}: has {name}")
            members = c.execute("SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid JOIN pg_roles u ON u.oid=m.member WHERE u.rolname=%s", (role,)).fetchall()
            if members:
                probs.append(f"{role}: is a member of {[m[0] for m in members]}")
            owned = c.execute("SELECT count(*) FROM pg_class c JOIN pg_roles o ON o.oid=c.relowner WHERE o.rolname=%s", (role,)).fetchone()[0]
            if owned:
                probs.append(f"{role}: owns {owned} relation(s)")
            if c.execute("SELECT has_schema_privilege(%s, 'copilot', 'CREATE') OR has_schema_privilege(%s, 'public', 'CREATE')", (role, role)).fetchone()[0]:
                probs.append(f"{role}: can CREATE in a schema")
        # tenant tables must have RLS enabled AND forced
        for name, en, force in c.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='copilot' AND relname = ANY(%s)",
                                         (["accounts", "account_contacts", "contracts", "integrations", "tickets", "ticket_history", "incident_accounts", "cases"],)).fetchall():
            if not (en and force):
                probs.append(f"copilot.{name}: RLS enabled={en} forced={force}")
        # the app role may execute exactly one function
        fn = [r[0] for r in c.execute("SELECT n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('copilot','copilot_private') AND has_function_privilege('copilot_app', p.oid, 'EXECUTE')").fetchall()]
        if fn != ["copilot_private.scope_account"]:
            probs.append(f"copilot_app can execute {fn}; expected only copilot_private.scope_account")
    probs += audit_privileges(db_admin_dsn)
    return probs


_PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_APP_READ = {"accounts", "account_contacts", "contracts", "integrations", "tickets", "ticket_history", "incident_accounts", "cases", "incidents", "deployments", "release_notes", "runbook_docs"}
_LOADER = {"accounts", "account_contacts", "contracts", "integrations", "tickets", "ticket_history", "incident_accounts", "incidents", "deployments", "release_notes", "runbook_docs"}
EXPECTED_PRIVILEGES = {                     # role -> {table: exact set of table privileges}; everything not listed must be empty
    "copilot_app": {t: {"SELECT"} for t in _APP_READ},
    "copilot_loader": {**{t: {"INSERT", "TRUNCATE"} for t in _LOADER}, "cases": {"TRUNCATE"}},
    "copilot_intake": {"tickets": {"SELECT"}, "cases": {"SELECT", "INSERT"}},
}


def audit_privileges(db_admin_dsn: str) -> list[str]:
    """Exact-match audit of table privileges: any EXTRA grant (e.g. UPDATE for the app role) is reported, as is a missing one."""
    probs: list[str] = []
    with psycopg.connect(db_admin_dsn) as c:
        tables = [(r[0], r[1]) for r in c.execute("SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('copilot','copilot_private') ORDER BY 1,2").fetchall()]
        for role, want in EXPECTED_PRIVILEGES.items():
            for schema, table in tables:
                have = {p for p in _PRIVS if c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"{schema}.{table}", p)).fetchone()[0]}
                expected = want.get(table, set()) if schema == "copilot" else set()
                if have != expected:
                    probs.append(f"{role} on {schema}.{table}: has {sorted(have)}, expected {sorted(expected)}")
    return probs
