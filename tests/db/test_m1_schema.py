"""M1 schema, roles, grants and loaded data — facts about the database as built by migrations + bootstrap + loader."""
import hashlib
import hmac as pyhmac

import psycopg
import pytest

from copilot.db import admin as A
from copilot.db.queries import NAMES

pytestmark = pytest.mark.db
TENANT = ["accounts", "account_contacts", "contracts", "integrations", "tickets", "ticket_history", "incident_accounts", "cases"]


def q(env, sql, *a):
    with psycopg.connect(env.admin_dsn) as c:
        return c.execute(sql, a).fetchall()


def test_migrations_are_idempotent_and_recorded(env):
    assert A.migrate(env.admin_dsn) == []
    done = [r[0] for r in q(env, "SELECT version FROM public.schema_migrations ORDER BY version")]
    assert done == [p.name for p in A.migration_files()] and len(done) == 6          # 001-005 (M1) + 006 runbook_chunks (M2)


def test_role_audit_is_clean(env):
    assert A.audit_roles(env.admin_dsn) == []


def test_application_roles_have_no_dangerous_attributes(env):                    # A-I2-21
    for role in A.ROLES:
        sup, byp, cdb, crole, repl = q(env, "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname=%s", role)[0]
        assert not any([sup, byp, cdb, crole, repl]), role
    assert q(env, "SELECT count(*) FROM pg_class c JOIN pg_roles o ON o.oid=c.relowner WHERE o.rolname = ANY(%s)", list(A.ROLES))[0][0] == 0


def test_every_tenant_table_has_rls_enabled_and_forced_and_is_owned_by_the_owner_role(env):    # A-I2-04
    for t in TENANT:
        en, force, owner = q(env, "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) FROM pg_class WHERE oid = %s::regclass", f"copilot.{t}")[0]
        assert (en, force, owner) == (True, True, "copilot_owner"), t


def test_app_role_may_execute_exactly_one_function_and_read_no_secrets(env):          # A-I2-17
    fn = [r[0] for r in q(env, "SELECT n.nspname||'.'||p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('copilot','copilot_private') AND has_function_privilege('copilot_app', p.oid, 'EXECUTE')")]
    assert fn == ["copilot_private.scope_account"]
    with psycopg.connect(env.app_dsn) as c:
        for stmt in ("SELECT * FROM copilot_private.scope_keys", "SELECT copilot_private.hmac_sha256('k'::bytea, 'm'::bytea)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()


def test_answer_keys_are_not_in_the_application_database(env):
    cols = {r[0] for r in q(env, "SELECT column_name FROM information_schema.columns WHERE table_schema IN ('copilot','copilot_private')")}
    tables = {r[0] for r in q(env, "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('copilot','copilot_private')")}
    assert not any("label" in t or "scenario" in t for t in tables), tables
    assert not ({"adversarial", "scenario_id", "root_cause_id", "expected_outcome", "expected_runbook_ids"} & cols)


def test_loaded_counts_match_the_dataset(env):
    assert env.counts["tickets"] == 300 and env.counts["accounts"] == 40
    for t, n in (("accounts", 40), ("tickets", 300), ("runbook_docs", env.counts["runbook_docs"]), ("incident_accounts", env.counts["incident_accounts"])):
        assert q(env, f"SELECT count(*) FROM copilot.{t}")[0][0] == n        # noqa: S608


def test_composite_foreign_keys_forbid_cross_account_children(env):
    a, b = env.tickets[0], next(t for t in env.tickets if t["account_id"] != env.tickets[0]["account_id"])
    with psycopg.connect(env.loader_dsn) as c:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            c.execute("INSERT INTO copilot.ticket_history VALUES (%s,%s,99,now(),'x','attach B ticket to A')", (b["ticket_id"], a["account_id"]))


def test_incident_table_does_not_reveal_who_was_affected(env):                 # A-I2-11
    cols = {r[0] for r in q(env, "SELECT column_name FROM information_schema.columns WHERE table_schema='copilot' AND table_name='incidents'")}
    assert not any("account" in c or "affected" in c for c in cols)


def test_hmac_matches_python_for_rfc_vectors_and_random_inputs(env):
    def sql_hmac(key: bytes, msg: bytes) -> str:
        return q(env, "SELECT encode(copilot_private.hmac_sha256(%s::bytea, %s::bytea), 'hex')", key, msg)[0][0]
    # RFC 4231 test case 2 and the long-key case 6
    assert sql_hmac(b"Jefe", b"what do ya want for nothing?") == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
    assert sql_hmac(b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First") == "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"
    import os
    for n in (1, 31, 32, 63, 64, 65, 200):
        k, m = os.urandom(n), os.urandom(n * 2 + 1)
        assert sql_hmac(k, m) == pyhmac.new(k, m, hashlib.sha256).hexdigest()


def test_fts_and_pgvector_infrastructure_function(env):
    rows = q(env, "SELECT doc_id FROM copilot.runbook_docs WHERE tsv @@ websearch_to_tsquery('english', 'carrier feed duplicate events')")
    assert rows
    assert q(env, "SELECT count(*) FROM pg_extension WHERE extname='vector'")[0][0] == 1
    with psycopg.connect(env.admin_dsn) as c:
        c.execute("CREATE TEMP TABLE v (e vector(3))")
        c.execute("INSERT INTO v VALUES ('[1,0,0]'), ('[0,1,0]')")
        assert c.execute("SELECT e::text FROM v ORDER BY e <=> '[1,0,0]' LIMIT 1").fetchone()[0] == '[1,0,0]'


def test_R3_baseline_weakness_is_still_observable(env):
    """R-3 is an OBSERVED BASELINE, deliberately not fixed in M1. Tickets in the dataset spell it 'resync' while runbooks say 're-sync'.
    If this test starts failing, someone changed retrieval/normalisation — that belongs to M2's measured comparison, not here."""
    def hits(qs):
        return {r[0] for r in q(env, "SELECT doc_id FROM copilot.runbook_docs WHERE tsv @@ websearch_to_tsquery('english', %s)", qs)}
    assert hits("re-sync integration approval")           # the hyphenated spelling finds the runbook
    assert not (hits("resync") & hits("re-sync"))          # the unhyphenated spelling does not reach the same documents
    assert any("resync" in t["body"].lower() for t in env.tickets), "dataset should contain the unhyphenated spelling in tickets"


def test_catalogue_size_is_fixed(env):
    # the catalogue is closed: adding a query means editing this list on purpose (M1: 10 queries; M2 added the five retrieval ones)
    assert set(NAMES) == {"get_account", "list_contracts", "list_integrations", "get_ticket", "ticket_history", "account_tickets", "open_incidents_for_account", "recent_deployments",
                          "get_runbook", "fts_runbooks_baseline", "list_runbooks", "get_doc_chunks", "search_chunks_lexical", "search_chunks_vector", "similar_account_tickets"}
