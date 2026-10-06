"""M0 environment spike: prove the database assumptions the architecture depends on BEFORE building on them.

These are not the M1 isolation tests. They establish the facts (and the pitfalls) that ADR-0004 relies on:
  * PostgreSQL full-text search and pgvector work in the target image;
  * row-level security isolates tenants for a real non-owner login role, defaults to deny when no scope is set;
  * the pitfalls that would silently break isolation (owner bypass, session-level scope on pooled connections, caller-set scope) are real, so the design
    must mitigate them — each is demonstrated, not assumed.
"""
import psycopg
import pytest

from tests.db.conftest import connect_as

pytestmark = pytest.mark.db
PWD = "spike-only-password"            # synthetic test credential for a throw-away role


@pytest.fixture()
def tenant_table(admin):
    admin.execute("DROP TABLE IF EXISTS spike_tickets CASCADE")
    admin.execute("DROP ROLE IF EXISTS spike_app")
    admin.execute(f"CREATE ROLE spike_app LOGIN PASSWORD '{PWD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
    admin.execute("CREATE TABLE spike_tickets (id serial PRIMARY KEY, account_id text NOT NULL, body text NOT NULL)")
    admin.execute("INSERT INTO spike_tickets (account_id, body) VALUES ('ACC-0001','secret of A'), ('ACC-0001','more of A'), ('ACC-0002','secret of B')")
    admin.execute("ALTER TABLE spike_tickets ENABLE ROW LEVEL SECURITY")
    admin.execute("ALTER TABLE spike_tickets FORCE ROW LEVEL SECURITY")
    admin.execute("CREATE POLICY tenant ON spike_tickets FOR SELECT TO spike_app USING (account_id = current_setting('app.account_id', true))")
    admin.execute("GRANT SELECT ON spike_tickets TO spike_app")
    yield
    admin.execute("DROP TABLE IF EXISTS spike_tickets CASCADE")
    admin.execute("DROP ROLE IF EXISTS spike_app")


def bodies(cur):
    return sorted(r[0] for r in cur.execute("SELECT body FROM spike_tickets").fetchall())


def test_server_version_and_fulltext_search(admin):
    major = admin.execute("SHOW server_version_num").fetchone()[0]
    assert int(major) >= 150000
    admin.execute("DROP TABLE IF EXISTS spike_docs")
    admin.execute("CREATE TABLE spike_docs (id int, body text, tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED)")
    admin.execute("CREATE INDEX ON spike_docs USING gin (tsv)")
    admin.execute("INSERT INTO spike_docs VALUES (1,'re-sync the carrier feed after approval'), (2,'invoice rounding differences on credit notes')")
    def ids(q):
        return [r[0] for r in admin.execute("SELECT id FROM spike_docs WHERE tsv @@ websearch_to_tsquery('english', %s)", (q,)).fetchall()]
    assert ids("carrier re-sync") == [1]
    # FINDING (docs/risks.md R-3): the same concept spelled 'resync' does NOT match 're-sync'. Lexical retrieval needs query/document normalisation
    # (hyphen/compound handling, synonyms) — this is exactly the kind of gap the lexical/vector/hybrid comparison in M2 must measure, not assume away.
    assert ids("carrier resync") == []
    admin.execute("DROP TABLE spike_docs")


@pytest.mark.pgvector
def test_pgvector_available_and_distance_queries_work(admin, has_pgvector):
    if not has_pgvector:
        pytest.skip("pgvector extension is not installed in this database")
    admin.execute("DROP TABLE IF EXISTS spike_vec")
    admin.execute("CREATE TABLE spike_vec (id int, e vector(3))")
    admin.execute("INSERT INTO spike_vec VALUES (1,'[1,0,0]'), (2,'[0,1,0]'), (3,'[0.9,0.1,0]')")
    nearest = admin.execute("SELECT id FROM spike_vec ORDER BY e <=> '[1,0,0]' LIMIT 2").fetchall()
    assert [r[0] for r in nearest] == [1, 3]
    admin.execute("CREATE INDEX ON spike_vec USING hnsw (e vector_cosine_ops)")
    ver = admin.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
    print("pgvector version:", ver)
    admin.execute("DROP TABLE spike_vec")


@pytest.mark.invariant
def test_rls_default_deny_and_scoped_visibility(tenant_table):            # A-I2-01
    with connect_as("spike_app", PWD) as c:
        assert bodies(c) == [], "no scope set must return zero rows"
        c.execute("SELECT set_config('app.account_id', 'ACC-0001', true)")
        assert bodies(c) == ["more of A", "secret of A"]
        c.execute("SELECT set_config('app.account_id', 'ACC-0002', true)")
        assert bodies(c) == ["secret of B"]


@pytest.mark.invariant
def test_scope_is_transaction_local_so_pooled_connections_do_not_leak(tenant_table):    # A-I2-03
    with connect_as("spike_app", PWD) as c:
        c.execute("SELECT set_config('app.account_id', 'ACC-0001', true)")      # is_local=true  -> reset at commit
        assert len(bodies(c)) == 2
        c.commit()
        assert bodies(c) == [], "transaction-local scope must not survive into the next transaction of the same pooled connection"


@pytest.mark.invariant
def test_session_level_scope_DOES_leak_which_is_why_it_is_forbidden(tenant_table):     # A-I2-03 (negative demonstration)
    with connect_as("spike_app", PWD) as c:
        c.execute("SELECT set_config('app.account_id', 'ACC-0001', false)")     # is_local=false -> session level
        c.commit()
        assert len(bodies(c)) == 2, "this is the leak: the next user of the connection would still see account A"


@pytest.mark.invariant
def test_owner_without_force_bypasses_rls_and_force_closes_it(admin):                   # A-I2-04
    admin.execute("DROP TABLE IF EXISTS spike_owner CASCADE")
    admin.execute("DROP ROLE IF EXISTS spike_owner_role")
    admin.execute(f"CREATE ROLE spike_owner_role LOGIN PASSWORD '{PWD}' NOSUPERUSER NOBYPASSRLS")
    admin.execute("GRANT CREATE ON SCHEMA public TO spike_owner_role")
    try:
        with connect_as("spike_owner_role", PWD) as o:
            o.execute("CREATE TABLE spike_owner (account_id text, body text)")
            o.execute("INSERT INTO spike_owner VALUES ('ACC-0001','a'), ('ACC-0002','b')")
            o.execute("ALTER TABLE spike_owner ENABLE ROW LEVEL SECURITY")
            o.execute("CREATE POLICY tenant ON spike_owner USING (account_id = current_setting('app.account_id', true))")
            o.commit()
            assert len(o.execute("SELECT * FROM spike_owner").fetchall()) == 2, "owner sees everything without FORCE (the pitfall)"
            o.execute("ALTER TABLE spike_owner FORCE ROW LEVEL SECURITY")
            o.commit()
            assert o.execute("SELECT * FROM spike_owner").fetchall() == [], "FORCE applies the policy to the owner too"
    finally:
        admin.execute("DROP TABLE IF EXISTS spike_owner CASCADE")
        admin.execute("REVOKE CREATE ON SCHEMA public FROM spike_owner_role")
        admin.execute("DROP ROLE IF EXISTS spike_owner_role")


@pytest.mark.invariant
def test_app_role_that_can_run_arbitrary_sql_can_choose_its_own_scope(tenant_table):    # A-I2-05
    """What RLS-by-setting does NOT stop: anyone able to execute SQL as the app role can set the scope to another account.
    Consequence recorded in ADR-0004 / threat T-I2-5: no model- or user-authored SQL, ever; queries come from a fixed catalogue; and M1 evaluates
    a signed-scope hardening (scope proven by an HMAC only the case service can mint)."""
    with connect_as("spike_app", PWD) as c:
        c.execute("SELECT set_config('app.account_id', 'ACC-0002', true)")
        assert bodies(c) == ["secret of B"]


def test_app_role_is_least_privileged(tenant_table):
    with connect_as("spike_app", PWD) as c:
        c.execute("SELECT set_config('app.account_id', 'ACC-0001', true)")
        for stmt in ("INSERT INTO spike_tickets (account_id, body) VALUES ('ACC-0001','x')", "UPDATE spike_tickets SET body='x'", "DELETE FROM spike_tickets",
                     "CREATE TABLE spike_evil (a int)", "DROP TABLE spike_tickets"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()
            c.execute("SELECT set_config('app.account_id', 'ACC-0001', true)")
        assert len(bodies(c)) == 2
