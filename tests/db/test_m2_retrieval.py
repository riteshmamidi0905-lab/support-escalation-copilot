"""M2 retrieval against real PostgreSQL + pgvector through the real roles, pool and fixed catalogue.

Two kinds of test, kept honest:
  * DETERMINISTIC INFRASTRUCTURE tests (this file): correct SQL, lifecycle/duplicate/conflict/abstention mechanics, citations, schema, security. They replay the
    committed real-model embedding cache (or use the labelled NON-SEMANTIC plumbing embedder) and make NO claim about retrieval quality.
  * The MEASURED benchmark (scripts/run_benchmark.py; replay reproducibility checked in test_m2_benchmark_replay.py).
"""
import json
import re

import psycopg
import pytest
from jsonschema import Draft202012Validator

from copilot import contracts as C
from copilot.db import queries as Q
from copilot.retrieval import chunker, embed, probes
from copilot.retrieval.service import CONFLICT, NO_EVIDENCE, Retriever, ticket_query
from copilot.scope import ScopeError
from tests.support.logcapture import CANARIES, capture_logs

pytestmark = [pytest.mark.db, pytest.mark.invariant]
HAND = C.CONTRACTS_DIR.parent / "data" / "hand-labelled-v1"
SCHEMA = Draft202012Validator(json.loads((C.CONTRACTS_DIR / "evidence.schema.json").read_text()))
STRATS = ("lexical", "vector", "hybrid", "rerank")
INJECTED = {"RBK-0030", "RBK-0032", "RBK-0035", "RBK-0051"}


@pytest.fixture(scope="module")
def rt(env):
    return Retriever(env.pool, embed.CachedEmbedder(), embed.Reranker())


@pytest.fixture(scope="module")
def hand():
    return C.read_jsonl(HAND / "hand_tickets.jsonl")


def q(t):
    return ticket_query(t["subject"], t["body"])


def scope_for(env, account_id):
    t = next(t for t in env.tickets if t["account_id"] == account_id)
    return env.intake.open_case(t["ticket_id"])[1]


# ---- infrastructure ---------------------------------------------------------------------------------------------------------------------------
def test_chunk_table_matches_the_chunker_and_the_documents(env, rt):
    docs = Q.run(env.pool, None, None, "list_runbooks", {})
    expect = {c["chunk_id"]: c for c in chunker.chunk_corpus(docs)}
    got = {c["chunk_id"]: c for d in docs for c in Q.run(env.pool, None, None, "get_doc_chunks", {"doc_id": d["doc_id"]})}
    assert set(got) == set(expect) and env.counts["runbook_chunks"] == len(expect)
    for cid, c in got.items():
        assert (c["text"], c["char_start"], c["char_end"], c["section"]) == (expect[cid]["text"], expect[cid]["char_start"], expect[cid]["char_end"], expect[cid]["section"])


def test_every_strategy_is_independently_selectable_and_returns_schema_valid_evidence(rt, hand):
    for s in STRATS:
        for t in hand:
            res = rt.retrieve(q(t), s)
            assert res["strategy"] == s
            assert not list(SCHEMA.iter_errors(res)), (s, t["ticket_id"], [e.message for e in SCHEMA.iter_errors(res)][:2])
    with pytest.raises(ValueError):
        rt.retrieve("x", "bm25")


def test_citations_resolve_to_the_exact_source_text_for_every_strategy_and_ticket(rt, hand):
    n = 0
    for s in STRATS:
        for t in hand:
            for e in rt.retrieve(q(t), s)["evidence"]:
                assert rt.resolve_citation(e["citation"]) == e["text"]
                n += 1
    assert n > 100
    with pytest.raises(ValueError):
        rt.resolve_citation("RBK-0019@9.9#RBK-0019#c0[0:10]")              # wrong version: the citation does not resolve


def test_superseded_and_draft_documents_never_appear_as_evidence_but_are_reported_as_excluded(rt, hand):
    seen_excluded = 0
    for s in STRATS:
        for t in hand:
            res = rt.retrieve(q(t), s)
            for e in res["evidence"]:
                assert e["status"] == "active"
                assert not (rt.registry.docs[e["doc_id"]]["status"] != "active")
                for d in e["duplicates"]:
                    assert rt.registry.docs[d]["status"] == "active"
            seen_excluded += len(res["excluded"])
            assert all(x["reason"] in ("superseded", "draft") for x in res["excluded"])
    assert seen_excluded > 0, "the benchmark queries do reach obsolete documents; exclusion must be visible, not silent"


def test_resync_lexical_miss_is_preserved_as_a_baseline_not_special_cased(rt):
    lex = [d["doc_id"] for d in rt.ranked("lexical", "resync")]
    assert "RBK-0019" not in lex and "RBK-0026" not in lex, "bare 'resync' must NOT match 're-sync' lexically (no synonym or stem hack)"
    assert "RBK-0019" in [d["doc_id"] for d in rt.ranked("lexical", "re-sync")]
    assert "RBK-0019" in [d["doc_id"] for d in rt.ranked("vector", "resync")][:3]


def test_conflicting_active_documents_yield_conflict_not_a_winner(rt, hand):
    t = next(t for t in hand if t["ticket_id"] == "TCK-8017")
    res = rt.retrieve(q(t), "vector")
    assert res["outcome"] == CONFLICT and not list(SCHEMA.iter_errors(res))
    assert {e["doc_id"] for e in res["evidence"]} == {"RBK-0015", "RBK-0017"}
    assert all(e["authority"]["conflicts_with"] for e in res["evidence"])
    assert res["conflict_sets"] == [["RBK-0015", "RBK-0017"]] and res["abstain_reason"]


def test_near_duplicates_collapse_into_one_item_that_lists_every_copy(rt, hand):
    t = next(t for t in hand if t["ticket_id"] == "TCK-8026")
    ev = rt.retrieve(q(t), "vector")["evidence"]
    items = [e for e in ev if e["doc_id"] in ("RBK-0031", "RBK-0057")]
    assert len(items) == 1 and set(items[0]["duplicates"]) | {items[0]["doc_id"]} == {"RBK-0031", "RBK-0057"}


def test_abstention_mechanics_with_the_plumbing_embedder_and_lexical_zero_hits(env, rt):
    """Mechanics only (the plumbing embedder is NOT semantic): no hits => NO_SUFFICIENT_EVIDENCE; a threshold above every score => NO_SUFFICIENT_EVIDENCE."""
    res = rt.retrieve("qwertyuiopasdfgh zxcvbnm", "lexical")
    assert res["outcome"] == NO_EVIDENCE and res["evidence"] == [] and not list(SCHEMA.iter_errors(res))
    plumb = Retriever(env.pool, embed.PlumbingEmbedder(), thresholds={"vector": 0.9999})
    out = plumb.retrieve("some ordinary ticket text about shipments", "vector")
    assert out["outcome"] == NO_EVIDENCE and out["evidence"] == []
    ok = Retriever(env.pool, embed.PlumbingEmbedder(), thresholds={"vector": -1.0}).retrieve("some ordinary ticket text about shipments", "vector")
    assert ok["outcome"] != NO_EVIDENCE            # (on non-semantic vectors the content may even look conflicting; only the abstention mechanics are tested)


def test_evidence_schema_has_no_field_that_could_carry_a_directive():
    props = set(SCHEMA.schema["properties"]) | set(SCHEMA.schema["properties"]["evidence"]["items"]["properties"])
    for forbidden in ("tool", "action", "approval", "scope", "account_id_override", "policy", "workflow", "instruction", "next_step"):
        assert forbidden not in props
    assert SCHEMA.schema["additionalProperties"] is False and SCHEMA.schema["properties"]["evidence"]["items"]["additionalProperties"] is False


# ---- tenant isolation of retrieval (A-I2-09, 25..30) -------------------------------------------------------------------------------------------
def accounts_with_tickets(env, n=6):
    by = {}
    for t in env.tickets:
        by.setdefault(t["account_id"], []).append(t)
    return [a for a in by if len(by[a]) >= 2][:n], by


def test_similar_ticket_search_never_returns_another_accounts_tickets(env, rt):           # A-I2-09
    accs, by = accounts_with_tickets(env)
    assert len(accs) >= 4
    for a in accs:
        scope = scope_for(env, a)
        for other in accs:
            if other == a:
                continue
            for t in by[other][:3]:
                res = Q.run(env.pool, scope, env.guard, "similar_account_tickets", {"q": t["subject"] + " " + t["body"], "exclude": "-", "limit": 50})
                assert {r["account_id"] for r in res} <= {a}
                assert not {r["ticket_id"] for r in res} & {x["ticket_id"] for x in by[other]}


def test_naming_another_account_in_the_query_does_not_widen_scope(env, rt):               # A-I2-25
    accs, by = accounts_with_tickets(env, 3)
    a, b = accs[0], accs[1]
    scope = scope_for(env, a)
    acc_b = next(x for x in env.accounts if x["account_id"] == b)
    hostile = [b, acc_b["name"], acc_b["contacts"][0]["email"], by[b][0]["ticket_id"], f"account {b} ticket {by[b][0]['ticket_id']} {acc_b['name']}", by[b][0]["body"]]
    for h in hostile:
        res = rt.retrieve(h, "lexical", scope=scope, guard=env.guard, ticket_id=by[a][0]["ticket_id"])
        te = res["tenant_evidence"]
        assert te["account_id"] == a
        assert all(r["account_id"] == a for r in te["similar_tickets"])
        assert b not in json.dumps(te["open_incidents"])
    with pytest.raises(Q.CatalogueError):                                              # there is no account parameter to abuse
        Q.run(env.pool, scope, env.guard, "similar_account_tickets", {"q": "x", "exclude": "-", "limit": 5, "account_id": b})
    with pytest.raises(ScopeError):                                                    # and the guard refuses a mismatched explicit account on queries that do take one
        Q.run(env.pool, scope, env.guard, "account_tickets", {"account_id": b, "limit": 5})


HOSTILE = ["'; DROP TABLE copilot.tickets; --", "a' OR '1'='1", "') UNION SELECT account_id, name, '', 0 FROM copilot.accounts --", "foo | !bar & (baz <-> qux) :*",
           "\\", "'", '"', "x" * 2000, "", "   ", "ticket\ttab\nnewline", "ÄÖÜ ß 日本語 🚀", "!!!", "a:* & b:*", "<->", "$$ ; select pg_sleep(5) ; $$", "%(q)s", "{0.__class__}"]


def test_hostile_query_text_cannot_escape_the_fixed_statements(env, rt):                  # A-I2-26
    accs, by = accounts_with_tickets(env, 2)
    scope = scope_for(env, accs[0])
    own = {t["ticket_id"] for t in by[accs[0]]}
    for h in HOSTILE:
        for name, params in (("similar_account_tickets", {"q": h, "exclude": "-", "limit": 10}), ("search_chunks_lexical", {"q": h, "limit": 10})):
            try:
                res = Q.run(env.pool, scope, env.guard, name, params)
            except (Q.CatalogueError, psycopg.Error):
                continue                                                                # failing closed is acceptable; leaking is not
            if name == "similar_account_tickets":
                assert {r["ticket_id"] for r in res} <= own
            else:
                assert all(r["chunk_id"].startswith("RBK-") for r in res)
    with env.pool.connection() as c:                                                    # the tables are intact
        assert c.execute("SELECT count(*) FROM copilot.runbook_chunks").fetchone()[0] == env.counts["runbook_chunks"]
        c.rollback()
    unscoped = Q.run(env.pool, None, None, "similar_account_tickets", {"q": "carrier feed duplicate events", "exclude": "-", "limit": 20})
    assert unscoped == [], "no scope => the database returns no tenant rows, whatever the query says"


def test_vector_and_identifier_parameter_abuse(env):                                      # A-I2-27
    ok = [0.1] * 384
    bad = [[0.1] * 383, [0.1] * 385, [], [float("nan")] * 384, [float("inf")] * 384, ["0.1"] * 384, [1] * 384, None, "[0.1,0.2]", "0.1); DROP TABLE copilot.tickets; --", {"v": 1}, [0.1] * 383 + [1e9]]
    for v in bad:
        with pytest.raises(Q.CatalogueError):
            Q.run(env.pool, None, None, "search_chunks_vector", {"v": v, "limit": 3})
    assert len(Q.run(env.pool, None, None, "search_chunks_vector", {"v": ok, "limit": 3})) == 3
    for doc_id in ["RBK-0001' OR '1'='1", "../../etc/passwd", "RBK-0001; select 1", "%", "ACC-0001", "TCK-0001", "x" * 3000]:
        try:
            res = Q.run(env.pool, None, None, "get_doc_chunks", {"doc_id": doc_id})
        except Q.CatalogueError:
            continue
        assert res == []
    with env.pool.connection() as c:
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='copilot' AND table_name='runbook_chunks'").fetchall()}
        c.rollback()
    assert not {"account_id", "ticket_id", "case_id", "email"} & cols, "the knowledge table has no tenant column to filter on or leak"


def test_forged_tampered_or_expired_scope_gives_retrieval_no_tenant_evidence(env, rt):    # A-I2-28
    accs, by = accounts_with_tickets(env, 2)
    a, b = accs[0], accs[1]
    good = scope_for(env, a)
    from dataclasses import replace
    for bad in (replace(good, account_id=b), replace(good, sig="0" * 64), replace(good, exp=good.exp - 10_000), replace(good, case_id="CASE-999999")):
        with pytest.raises(ScopeError):
            rt.retrieve("carrier feed duplicates", "lexical", scope=bad, guard=env.guard, ticket_id="-")
    assert rt.retrieve("carrier feed duplicates", "lexical")["tenant_evidence"] is None
    # even if the application guard were bypassed, the database returns nothing for a bad scope
    forged = replace(good, account_id=b)
    with env.pool.connection() as conn, conn.transaction():
        for k, v in forged.settings().items():
            conn.execute("SELECT set_config(%s, %s, true)", (k, v))
        assert conn.execute("SELECT count(*) FROM copilot.tickets WHERE to_tsvector('english', subject || ' ' || body) @@ plainto_tsquery('english', 'carrier')").fetchone()[0] == 0


def test_chunks_and_embeddings_hold_no_tenant_identifiers(env):                           # A-I2-29
    tokens = {x["account_id"] for x in env.accounts} | {x["name"] for x in env.accounts} | {c["email"] for x in env.accounts for c in x["contacts"]} | {t["ticket_id"] for t in env.tickets[:50]}
    docs = Q.run(env.pool, None, None, "list_runbooks", {})
    texts = [c["embed_text"] for c in chunker.chunk_corpus(docs)] + [c["text"] for d in docs for c in Q.run(env.pool, None, None, "get_doc_chunks", {"doc_id": d["doc_id"]})]
    blob = "\n".join(texts)
    assert not [t for t in tokens if t in blob]
    assert not re.search(r"@[a-z0-9.-]+\.(com|net|org)\b|ACC-\d{4}|TCK-\d{4}|CASE-\d{6}", blob)
    cache = embed.CachedEmbedder()
    assert all(re.fullmatch(r"[0-9a-f]{64}", k) for k in cache._d["vectors"]), "the cache stores hashes and numbers, never ticket or customer text"


# ---- untrusted retrieved text: injected runbooks (A-I1-11, A-I2-30, A-I3-06, A-I4-07) ----------------------------------------------------------
def injection_queries():
    return list(probes.INJECTION_QUERIES)


def test_injected_runbooks_are_retrievable_flagged_and_inert(env, rt):                    # A-I1-11, A-I2-30
    accs, by = accounts_with_tickets(env, 2)
    a = accs[0]
    scope = scope_for(env, a)
    before = (scope, json.dumps(scope.settings(), sort_keys=True))
    def counts():
        with env.pool.connection() as conn:
            r = [conn.execute(q_).fetchone()[0] for q_ in ("SELECT count(*) FROM copilot.runbook_docs", "SELECT count(*) FROM copilot.runbook_chunks", "SELECT count(*) FROM copilot.incidents")]
            conn.rollback()
        return r
    retrieved = set()
    for text in injection_queries():
        for s in ("lexical", "vector", "hybrid"):
            res = rt.retrieve(text, s, scope=scope, guard=env.guard, ticket_id=by[a][0]["ticket_id"])
            assert not list(SCHEMA.iter_errors(res))
            for e in res["evidence"]:
                assert e["content_trust"] == "untrusted_data"
                if e["doc_id"] in INJECTED:
                    retrieved.add(e["doc_id"])
                    assert e["flags"] == ["instruction_like_text"]                     # annotated (advisory) but still returned as plain data
            assert res["tenant_evidence"]["account_id"] == a
            others = {x["account_id"] for x in env.accounts} - {a}
            assert not [o for o in others if o in json.dumps(res)], "another account's id appears in the result"
    assert retrieved == INJECTED, f"the adversarial test must actually retrieve all four injected documents; got {retrieved}"
    assert (scope, json.dumps(scope.settings(), sort_keys=True)) == before, "retrieval altered the scope"
    assert counts() == [60, 60, counts()[2]]
    with pytest.raises(psycopg.Error), env.pool.connection() as conn:                  # the application role is read-only: retrieval cannot change workflow state
        conn.execute("INSERT INTO copilot.cases (case_id, account_id, ticket_id) VALUES ('CASE-999998', 'ACC-0001', 'TCK-0001')")


def test_retrieval_output_is_pure_data_with_no_executable_or_directive_content_path(rt):
    res = rt.retrieve("operations notes routing tips carrier integrations", "vector")
    json.dumps(res)                                                                     # serialisable data only: no callables, no objects
    allowed = set(SCHEMA.schema["properties"])
    assert set(res) == allowed
    for e in res["evidence"]:
        assert set(e) == set(SCHEMA.schema["properties"]["evidence"]["items"]["properties"])


def test_canary_secrets_do_not_appear_in_retrieval_output_or_logs(env, rt, monkeypatch):    # A-I4-07
    for k, v in CANARIES.items():
        monkeypatch.setenv(f"COPILOT_CANARY_{k.upper()}", v)
    with capture_logs() as cap:
        outs = [json.dumps(rt.retrieve(t, s)) for t in injection_queries() for s in ("lexical", "vector", "hybrid")]
    assert cap.leaked() == []
    assert not [k for k, v in CANARIES.items() if any(v in o for o in outs)]


# ---- gaps found by the mutation check (scripts/mutation_check_m2.py) --------------------------------------------------------------------------
def test_source_location_matches_the_citation_and_the_source_text(rt, hand):
    for t in hand[:20]:
        for e in rt.retrieve(q(t), "vector")["evidence"]:
            loc = e["source_location"]
            assert e["citation"].endswith(f"[{loc['char_start']}:{loc['char_end']}]")
            assert rt.registry.docs[e["doc_id"]]["body_markdown"][loc["char_start"]:loc["char_end"]] == e["text"]


def test_similar_ticket_search_is_relevance_filtered_and_excludes_the_case_ticket(env):
    accs, by = accounts_with_tickets(env, 1)
    a = accs[0]
    scope = scope_for(env, a)
    assert Q.run(env.pool, scope, env.guard, "similar_account_tickets", {"q": "qwertyuiop zxcvbnm", "exclude": "-", "limit": 20}) == []
    t = by[a][0]
    res = Q.run(env.pool, scope, env.guard, "similar_account_tickets", {"q": t["subject"], "exclude": t["ticket_id"], "limit": 20})
    assert t["ticket_id"] not in {r["ticket_id"] for r in res}
    assert all(r["score"] > 0 for r in res)


def test_retrieval_tenant_evidence_includes_the_case_accounts_open_incidents_and_only_those(env, rt):
    open_by_acc = {}
    for inc in C.read_jsonl(env.dataset / "incidents.jsonl"):
        if inc["status"] != "resolved":
            for a in inc["affected_account_ids"]:
                open_by_acc.setdefault(a, set()).add(inc["incident_id"])
    acc = next(a for a in open_by_acc if any(t["account_id"] == a for t in env.tickets))
    t = next(t for t in env.tickets if t["account_id"] == acc)
    scope = env.intake.open_case(t["ticket_id"])[1]
    te = rt.retrieve("carrier feed duplicate events", "lexical", scope=scope, guard=env.guard, ticket_id=t["ticket_id"])["tenant_evidence"]
    assert {i["incident_id"] for i in te["open_incidents"]} == open_by_acc[acc]
    free = next(a for a in {t["account_id"] for t in env.tickets} if a not in open_by_acc)
    t2 = next(t for t in env.tickets if t["account_id"] == free)
    te2 = rt.retrieve("carrier feed duplicate events", "lexical", scope=env.intake.open_case(t2["ticket_id"])[1], guard=env.guard, ticket_id=t2["ticket_id"])["tenant_evidence"]
    assert te2["open_incidents"] == []
