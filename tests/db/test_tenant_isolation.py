"""Invariant I2 — attempts to break tenant isolation, against real PostgreSQL, through the real roles, pool and catalogue.
Every test here tries to SEE or ACT ON another account's data, or to alter/forge scope. Zero cross-tenant exposure is absolute, not statistical."""
import time

import psycopg
import pytest

from copilot.db import queries as Q
from copilot.db.session import scoped
from copilot.scope import ScopeError, Signer

pytestmark = [pytest.mark.db, pytest.mark.invariant]


def case_for(env, ticket):
    return env.intake.open_case(ticket["ticket_id"])


def two_accounts(env):
    a = env.tickets[0]
    b = next(t for t in env.tickets if t["account_id"] != a["account_id"])
    return a, b


def rows(env, scope, sql, params=()):
    with scoped(env.pool, scope, env.guard) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def raw_rows(env, settings, sql, params=()):
    """What a hostile caller who can run SQL as the app role could do: set ANY session settings, bypassing every Python check."""
    with env.pool.connection() as conn, conn.transaction():
        for k, v in settings.items():
            conn.execute("SELECT set_config(%s, %s, true)", (k, v))
        return conn.execute(sql, params).fetchall()


# ---- correct access ----------------------------------------------------------------------------------------------------------------
def test_every_account_sees_exactly_its_own_data_and_nothing_else(env):
    by_acc = {}
    for t in env.tickets:
        by_acc.setdefault(t["account_id"], set()).add(t["ticket_id"])
    for acc, tids in by_acc.items():
        _, scope = case_for(env, next(t for t in env.tickets if t["account_id"] == acc))
        assert {r[0] for r in rows(env, scope, "SELECT ticket_id FROM copilot.tickets")} == tids, acc
        assert [r[0] for r in rows(env, scope, "SELECT account_id FROM copilot.accounts")] == [acc]
        assert {r[0] for r in rows(env, scope, "SELECT DISTINCT account_id FROM copilot.integrations")} <= {acc}
        assert {r[0] for r in rows(env, scope, "SELECT DISTINCT account_id FROM copilot.contracts")} == {acc}


# ---- wrong account ---------------------------------------------------------------------------------------------------------------------
def test_scopeguard_refuses_a_request_for_another_account_before_any_sql(env):                       # A-I2-02
    a, b = two_accounts(env)
    _, scope = case_for(env, a)
    with pytest.raises(ScopeError):
        Q.run(env.pool, scope, env.guard, "get_account", {"account_id": b["account_id"]})
    assert Q.run(env.pool, scope, env.guard, "get_account", {"account_id": a["account_id"]})[0]["account_id"] == a["account_id"]


def test_database_independently_returns_nothing_when_the_guard_is_bypassed(env):                      # A-I2-02 layer 3
    a, b = two_accounts(env)
    _, scope = case_for(env, a)
    assert rows(env, scope, "SELECT * FROM copilot.accounts WHERE account_id = %s", (b["account_id"],)) == []
    assert rows(env, scope, "SELECT * FROM copilot.contracts WHERE account_id = %s", (b["account_id"],)) == []
    assert rows(env, scope, "SELECT * FROM copilot.integrations WHERE account_id <> %s", (a["account_id"],)) == []


# ---- direct object references ------------------------------------------------------------------------------------------------------
def test_direct_references_to_another_accounts_objects_return_nothing(env):                           # A-I2-19
    a, b = two_accounts(env)
    _, scope = case_for(env, a)
    assert Q.run(env.pool, scope, env.guard, "get_ticket", {"ticket_id": b["ticket_id"]}) == []
    assert Q.run(env.pool, scope, env.guard, "ticket_history", {"ticket_id": b["ticket_id"]}) == []
    assert rows(env, scope, "SELECT t.ticket_id FROM copilot.tickets t JOIN copilot.contracts c ON true WHERE t.ticket_id = %s", (b["ticket_id"],)) == []
    assert rows(env, scope, "SELECT * FROM copilot.cases WHERE ticket_id = %s", (b["ticket_id"],)) == []
    case_b, _ = case_for(env, b)
    assert rows(env, scope, "SELECT * FROM copilot.cases WHERE case_id = %s", (case_b,)) == []


# ---- forged / tampered / expired / mismatched scope -------------------------------------------------------------------------------
def test_forged_account_with_someone_elses_signature_fails_closed(env):                               # A-I2-13
    a, b = two_accounts(env)
    _, sa = case_for(env, a)
    forged = dict(sa.settings(), **{"app.scope_account": b["account_id"]})
    assert raw_rows(env, forged, "SELECT * FROM copilot.tickets") == []


def test_account_set_with_no_signature_or_garbage_signature_fails_closed(env):                      # A-I2-05 / A-I2-13
    a, b = two_accounts(env)
    _, sa = case_for(env, a)
    assert raw_rows(env, {"app.account_id": b["account_id"]}, "SELECT * FROM copilot.tickets") == []
    assert raw_rows(env, {"app.scope_account": b["account_id"]}, "SELECT * FROM copilot.tickets") == []
    for bad in ("", "0" * 64, "zz", sa.sig.upper(), sa.sig[:-1], sa.sig + "0", " " + sa.sig, "'; DROP TABLE copilot.tickets;--"):
        assert raw_rows(env, dict(sa.settings(), **{"app.scope_sig": bad}), "SELECT * FROM copilot.tickets") == [], bad


@pytest.mark.parametrize("field,value", [("app.scope_exp", "99999999999"), ("app.scope_case", "CASE-999999"), ("app.scope_key", "k2"), ("app.scope_exp", "not-a-number"), ("app.scope_exp", "")])
def test_tampering_with_any_signed_field_fails_closed(env, field, value):                              # A-I2-14
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    assert len(raw_rows(env, sa.settings(), "SELECT * FROM copilot.tickets")) > 0            # control: the untampered scope works
    assert raw_rows(env, dict(sa.settings(), **{field: value}), "SELECT * FROM copilot.tickets") == []


def test_expired_scope_fails_closed_in_database_and_in_application(env):                          # A-I2-15
    a, _ = two_accounts(env)
    case_id, _ = case_for(env, a)
    past = Signer(env.scope_secret, clock=lambda: time.time() - 3600)
    old = past.mint(a["account_id"], case_id, ttl_s=60)
    assert raw_rows(env, old.settings(), "SELECT * FROM copilot.tickets") == []
    with pytest.raises(ScopeError):
        env.guard.check(old)
    fresh = env.signer.mint(a["account_id"], case_id, ttl_s=60)
    assert len(raw_rows(env, fresh.settings(), "SELECT * FROM copilot.tickets")) > 0


def test_signature_made_with_the_wrong_secret_fails_closed(env):                                     # A-I2-13
    a, _ = two_accounts(env)
    case_id, _ = case_for(env, a)
    attacker = Signer("x" * 40)
    assert raw_rows(env, attacker.mint(a["account_id"], case_id).settings(), "SELECT * FROM copilot.tickets") == []


def test_case_and_account_that_do_not_belong_together_fail_closed_even_if_signed(env):               # A-I2-16
    a, b = two_accounts(env)
    case_a, _ = case_for(env, a)
    # a buggy or malicious SIGNER mints a perfectly valid signature for (account B, case of A)
    mismatched = env.signer.mint(b["account_id"], case_a)
    env.guard.check(mismatched)                                                   # the signature itself is valid ...
    assert raw_rows(env, mismatched.settings(), "SELECT * FROM copilot.tickets") == []   # ... but the database knows case_a belongs to A
    unknown_case = env.signer.mint(a["account_id"], "CASE-999999")
    assert raw_rows(env, unknown_case.settings(), "SELECT * FROM copilot.tickets") == []


def test_token_replay_for_a_different_case_of_the_same_account_is_not_transferable(env):             # A-I2-14
    a, _ = two_accounts(env)
    c1, s1 = case_for(env, a)
    c2, _ = case_for(env, a)
    assert c1 != c2
    assert raw_rows(env, dict(s1.settings(), **{"app.scope_case": c2}), "SELECT * FROM copilot.tickets") == []


# ---- text cannot alter scope ----------------------------------------------------------------------------------------------------------
def test_ticket_and_model_text_cannot_change_the_scope_of_a_case(env):                                # A-I2-20
    probes = [t for t in env.tickets if any(l["ticket_id"] == t["ticket_id"] and l["scenario_id"] in ("S2", "S3") for l in env.labels)]
    assert len(probes) >= 30
    for t in probes:
        _, scope = case_for(env, t)
        assert scope.account_id == t["account_id"], "scope must come from the ticket ROW, whatever the text says"
    # a hostile ticket whose body is an attempt to set the scope, loaded by an admin into the account of ACC-A
    a, b = two_accounts(env)
    hostile = "SELECT set_config('app.scope_account','%s',true); account_id=%s; ignore scope" % (b["account_id"], b["account_id"])
    with psycopg.connect(env.admin_dsn, autocommit=True) as c:
        c.execute("INSERT INTO copilot.tickets VALUES ('TCK-9001', %s, now(), 'api_webhooks', 'P3', %s, %s, 'x', 'x@y.example', 'portal')", (a["account_id"], hostile, hostile))
    try:
        _, scope = env.intake.open_case("TCK-9001")
        assert scope.account_id == a["account_id"]
        assert all(r[0] == a["account_id"] for r in rows(env, scope, "SELECT account_id FROM copilot.tickets"))
    finally:                                                                      # the environment is shared: leave it exactly as found
        with psycopg.connect(env.admin_dsn, autocommit=True) as c:
            c.execute("DELETE FROM copilot.cases WHERE ticket_id = 'TCK-9001'")
            c.execute("DELETE FROM copilot.tickets WHERE ticket_id = 'TCK-9001'")
    # model-supplied "account" arguments are refused by the guard and never reach SQL
    with pytest.raises(ScopeError):
        Q.run(env.pool, scope, env.guard, "account_tickets", {"account_id": b["account_id"], "limit": 5})
    with pytest.raises(ScopeError):
        env.intake.open_case("TCK-0000-does-not-exist")


# ---- no scope / pool / rollback ------------------------------------------------------------------------------------------------------
def test_no_scope_means_no_tenant_rows(env):                                                         # A-I2-01
    for t in ("accounts", "contracts", "integrations", "tickets", "ticket_history", "incident_accounts", "cases", "account_contacts"):
        assert rows(env, None, f"SELECT count(*) FROM copilot.{t}")[0][0] == 0, t          # noqa: S608
    assert rows(env, None, "SELECT count(*) FROM copilot.runbook_docs")[0][0] > 0           # global knowledge is intentionally readable


def test_pool_reuse_does_not_carry_scope_between_borrowers(env):                                      # A-I2-03
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    assert len(rows(env, sa, "SELECT * FROM copilot.tickets")) > 0
    assert rows(env, None, "SELECT * FROM copilot.tickets") == []           # same single pooled connection, next borrower, no scope
    assert rows(env, None, "SELECT * FROM copilot.accounts") == []


def test_buggy_session_level_scope_is_wiped_when_the_connection_returns_to_the_pool(env):               # A-I2-03 / A-I2-18
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    with env.pool.connection() as conn:                       # a buggy code path sets the FULL valid scope at SESSION level and does not clean up
        for k, v in sa.settings().items():
            conn.execute("SELECT set_config(%s, %s, false)", (k, v))
        assert len(conn.execute("SELECT * FROM copilot.tickets").fetchall()) > 0
        conn.commit()
    assert rows(env, None, "SELECT * FROM copilot.tickets") == [], "DISCARD ALL on return must have removed it"


def test_without_the_pool_reset_a_session_level_scope_WOULD_leak(env):                                 # negative demonstration: why the reset exists
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    with psycopg.connect(env.app_dsn) as raw:                 # a raw connection with no reset hook
        for k, v in sa.settings().items():
            raw.execute("SELECT set_config(%s, %s, false)", (k, v))
        raw.commit()
        assert len(raw.execute("SELECT * FROM copilot.tickets").fetchall()) > 0


def test_exception_inside_a_scoped_block_does_not_preserve_scope(env):                                # A-I2-18
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    with pytest.raises(RuntimeError):
        with scoped(env.pool, sa, env.guard) as cur:
            assert cur.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] > 0
            raise RuntimeError("tool crashed")
    assert rows(env, None, "SELECT * FROM copilot.tickets") == []


def test_sql_error_and_timeout_paths_do_not_preserve_scope(env):                                     # A-I2-18
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    with pytest.raises(psycopg.errors.UndefinedTable):
        with scoped(env.pool, sa, env.guard) as cur:
            cur.execute("SELECT * FROM copilot.no_such_table")
    assert rows(env, None, "SELECT * FROM copilot.tickets") == []
    with pytest.raises(psycopg.errors.QueryCanceled):
        with scoped(env.pool, sa, env.guard) as cur:
            cur.execute("SELECT pg_sleep(11)")                                           # role statement_timeout = 10s
    assert rows(env, None, "SELECT * FROM copilot.tickets") == []


def test_scoped_refuses_to_run_inside_an_open_outer_transaction(env):
    with psycopg.connect(env.app_dsn) as conn:
        conn.execute("SELECT 1")                                                         # opens a transaction
        a, _ = two_accounts(env)
        _, sa = case_for(env, a)
        with pytest.raises(RuntimeError):
            with scoped(conn, sa, env.guard):
                pass


# ---- the application role cannot escape ----------------------------------------------------------------------------------------------
def test_application_role_cannot_write_alter_or_create_anything(env):                                 # A-I2-24
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    stmts = ["INSERT INTO copilot.tickets SELECT * FROM copilot.tickets LIMIT 0", "UPDATE copilot.tickets SET body = 'x'", "DELETE FROM copilot.tickets", "TRUNCATE copilot.tickets",
             "CREATE TABLE copilot.evil (a int)", "CREATE TABLE public.evil (a int)", "CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS 'select 1'", "ALTER TABLE copilot.tickets DISABLE ROW LEVEL SECURITY",
             "DROP POLICY tenant_read ON copilot.tickets", "ALTER ROLE copilot_app BYPASSRLS", "SET ROLE copilot_owner", "SET SESSION AUTHORIZATION copilot_owner", "GRANT SELECT ON copilot.tickets TO PUBLIC",
             "COPY copilot.tickets TO '/tmp/x'", "CREATE EXTENSION pgcrypto", "SELECT pg_read_file('/etc/passwd')"]
    for stmt in stmts:
        with env.pool.connection() as conn:
            with pytest.raises(psycopg.Error):
                with conn.transaction():
                    for k, v in sa.settings().items():
                        conn.execute("SELECT set_config(%s, %s, true)", (k, v))
                    conn.execute(stmt)
    with scoped(env.pool, sa, env.guard) as cur:
        assert cur.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] > 0     # nothing above changed anything


def test_application_role_cannot_forge_or_discover_the_signing_key(env):                              # A-I2-17
    a, b = two_accounts(env)
    with psycopg.connect(env.app_dsn) as c:
        for stmt in ("SELECT secret FROM copilot_private.scope_keys", "SELECT copilot_private.hmac_sha256('a'::bytea,'b'::bytea)", "SELECT prosrc FROM pg_proc WHERE proname='scope_account'"):
            try:
                out = c.execute(stmt).fetchall()
            except psycopg.errors.InsufficientPrivilege:
                c.rollback()
                continue
            assert env.scope_secret not in str(out), "the signing secret must not be reachable"
            c.rollback()
    assert env.scope_secret not in str(rows(env, None, "SELECT prosrc FROM pg_proc WHERE proname = 'scope_account'"))


# ---- owner / BYPASSRLS behaviour explicitly ------------------------------------------------------------------------------------------
def test_bypassrls_and_superuser_DO_see_everything_which_is_why_the_role_audit_exists(env):         # A-I2-21
    with psycopg.connect(env.admin_dsn, autocommit=True) as c:
        total = c.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0]
        assert total == len(env.tickets)          # superuser/admin bypasses RLS: expected, and the reason application code never connects as it
        c.execute("DROP ROLE IF EXISTS leaky_probe")
        c.execute("CREATE ROLE leaky_probe LOGIN PASSWORD 'probe-only' BYPASSRLS")
        c.execute("GRANT USAGE ON SCHEMA copilot TO leaky_probe")
        c.execute("GRANT SELECT ON copilot.tickets TO leaky_probe")
    try:
        from tests.db.conftest import connect_as
        with connect_as("leaky_probe", "probe-only") as p:
            p.execute("SELECT 1")
    except psycopg.OperationalError:
        pass                                      # connect_as targets the server-level DSN db; the point is made by the catalogue check below
    finally:
        with psycopg.connect(env.admin_dsn, autocommit=True) as c:
            c.execute("REVOKE ALL ON copilot.tickets FROM leaky_probe")
            c.execute("REVOKE USAGE ON SCHEMA copilot FROM leaky_probe")
            c.execute("DROP ROLE leaky_probe")


def test_role_audit_detects_a_role_that_could_bypass_rls(env):                                         # A-I2-21 positive control
    with psycopg.connect(env.admin_dsn, autocommit=True) as c:
        c.execute("ALTER ROLE copilot_app BYPASSRLS")
    try:
        from copilot.db import admin as A
        probs = A.audit_roles(env.admin_dsn)
        assert any("copilot_app" in p and "BYPASSRLS" in p for p in probs), probs
        with psycopg.connect(env.app_dsn) as c:       # and the leak is real, not hypothetical
            assert c.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] == len(env.tickets)
    finally:
        with psycopg.connect(env.admin_dsn, autocommit=True) as c:
            c.execute("ALTER ROLE copilot_app NOBYPASSRLS")
    from copilot.db import admin as A
    assert A.audit_roles(env.admin_dsn) == []
    with psycopg.connect(env.app_dsn) as c:
        assert c.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] == 0


def test_forced_rls_applies_to_the_table_owner(env):                                                   # A-I2-04
    with psycopg.connect(env.admin_dsn, autocommit=True) as c:
        c.execute("SET ROLE copilot_owner")
        assert c.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] == 0, "FORCE ROW LEVEL SECURITY must bind the owner too"
        c.execute("RESET ROLE")


def test_intake_and_loader_roles_are_confined(env):
    with psycopg.connect(env.intake_dsn) as c:
        assert c.execute("SELECT count(*) FROM copilot.tickets").fetchone()[0] == len(env.tickets)   # the trusted intake may look up which account a ticket belongs to
        for stmt in ("SELECT * FROM copilot.contracts", "SELECT * FROM copilot.integrations", "UPDATE copilot.tickets SET body='x'", "SELECT * FROM copilot_private.scope_keys"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()
        a, b = two_accounts(env)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):                                       # cannot create a case binding ticket B to account A
            c.execute("INSERT INTO copilot.cases (case_id, account_id, ticket_id) VALUES ('CASE-777777', %s, %s)", (a["account_id"], b["ticket_id"]))
        c.rollback()
    with psycopg.connect(env.loader_dsn) as c:
        for stmt in ("SELECT * FROM copilot.tickets", "UPDATE copilot.tickets SET body='x'", "DELETE FROM copilot.tickets"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()


# ---- incident / document leakage -----------------------------------------------------------------------------------------------------
def test_incident_queries_never_reveal_other_accounts(env):                                            # A-I2-11
    by_acc = {}
    for t in env.tickets:
        by_acc.setdefault(t["account_id"], t)
    import json
    inc = [json.loads(l) for l in (env.dataset / "incidents.jsonl").read_text().splitlines()]
    for acc, t in list(by_acc.items())[:15]:
        _, scope = case_for(env, t)
        mine = {i["incident_id"] for i in inc if acc in i["affected_account_ids"] and i["status"] != "resolved"}
        assert {r["incident_id"] for r in Q.run(env.pool, scope, env.guard, "open_incidents_for_account", {})} == mine
        assert {r[0] for r in rows(env, scope, "SELECT DISTINCT account_id FROM copilot.incident_accounts")} <= {acc}


def test_global_runbook_corpus_contains_no_tenant_identifiers(env):                                    # A-I2-12
    ids = [a["account_id"] for a in env.accounts] + [a["name"] for a in env.accounts] + [c["email"] for a in env.accounts for c in a["contacts"]]
    corpus = " ".join(r[0] + " " + r[1] for r in rows(env, None, "SELECT title, body_markdown FROM copilot.runbook_docs")).lower()
    assert not [x for x in ids if x.lower() in corpus]


# ---- fixed query catalogue ------------------------------------------------------------------------------------------------------------
def test_catalogue_rejects_arbitrary_sql_unknown_names_and_bad_parameters(env):                       # A-I2-22
    a, _ = two_accounts(env)
    _, sa = case_for(env, a)
    for bad_name in ("SELECT * FROM copilot.tickets", "get_account; DROP TABLE copilot.tickets", "nope", "", None, 5):
        with pytest.raises(Q.CatalogueError):
            Q.run(env.pool, sa, env.guard, bad_name, {})
    with pytest.raises(Q.CatalogueError):
        Q.run(env.pool, sa, env.guard, "get_ticket", {"ticket_id": a["ticket_id"], "extra": "x"})
    with pytest.raises(Q.CatalogueError):
        Q.run(env.pool, sa, env.guard, "get_ticket", {})
    with pytest.raises(Q.CatalogueError):
        Q.run(env.pool, sa, env.guard, "account_tickets", {"account_id": a["account_id"], "limit": "5; DROP TABLE x"})
    with pytest.raises(Q.CatalogueError):
        Q.run(env.pool, sa, env.guard, "account_tickets", {"account_id": a["account_id"], "limit": 10 ** 6})


def test_sql_injection_payloads_in_parameters_are_inert_values(env):                                    # A-I2-22
    a, b = two_accounts(env)
    _, sa = case_for(env, a)
    for payload in ("' OR '1'='1", "x'; SELECT set_config('app.scope_account','%s',true);--" % b["account_id"], "%s", "\\'", b["ticket_id"] + "' --", "1 UNION SELECT * FROM copilot.tickets"):
        assert Q.run(env.pool, sa, env.guard, "get_ticket", {"ticket_id": payload}) == []
        assert Q.run(env.pool, sa, env.guard, "fts_runbooks_baseline", {"q": payload, "limit": 3}) is not None
    assert {r["ticket_id"] for r in Q.run(env.pool, sa, env.guard, "account_tickets", {"account_id": a["account_id"], "limit": 500})} <= {t["ticket_id"] for t in env.tickets if t["account_id"] == a["account_id"]}


def test_privilege_audit_is_exact_and_detects_an_excess_grant(env):                                     # A-I2-24 (positive control found by mutation testing)
    from copilot.db import admin as A
    assert A.audit_privileges(env.admin_dsn) == []
    with psycopg.connect(env.admin_dsn, autocommit=True) as c:
        c.execute("GRANT UPDATE ON copilot.tickets TO copilot_app")
    try:
        probs = A.audit_privileges(env.admin_dsn)
        assert any("copilot_app on copilot.tickets" in p and "UPDATE" in p for p in probs), probs
    finally:
        with psycopg.connect(env.admin_dsn, autocommit=True) as c:
            c.execute("REVOKE UPDATE ON copilot.tickets FROM copilot_app")
    assert A.audit_privileges(env.admin_dsn) == []
