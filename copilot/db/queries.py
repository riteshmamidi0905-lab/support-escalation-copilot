"""The FIXED query catalogue. The only SQL the model-facing application role ever runs.

Every statement is a constant string with bind parameters. There is no API to run other SQL: unknown names are rejected, raw SQL is rejected,
parameters are type-checked and bound (never formatted into text). Combined with signed scope + forced RLS, SQL authorship by a model or a user
cannot become a scope bypass.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from .session import scoped

_Q: dict[str, tuple] = {
    # name: (sql, ((param_name, type), ...))
    "get_account": ("SELECT account_id, name, tier, region, status FROM copilot.accounts WHERE account_id = %(account_id)s", (("account_id", str),)),
    "list_contracts": ("SELECT contract_id, tier, sla_response_minutes, sla_resolution_hours, max_agent_requestable_pct, max_manager_approvable_pct FROM copilot.contracts WHERE account_id = %(account_id)s ORDER BY effective_from DESC", (("account_id", str),)),
    "list_integrations": ("SELECT integration_id, kind, provider, status, last_sync_at, last_resync_at FROM copilot.integrations WHERE account_id = %(account_id)s ORDER BY integration_id", (("account_id", str),)),
    "get_ticket": ("SELECT ticket_id, account_id, created_at, product_area, severity, subject, body FROM copilot.tickets WHERE ticket_id = %(ticket_id)s", (("ticket_id", str),)),
    "ticket_history": ("SELECT seq, at, author, text FROM copilot.ticket_history WHERE ticket_id = %(ticket_id)s ORDER BY seq", (("ticket_id", str),)),
    "account_tickets": ("SELECT ticket_id, created_at, product_area, severity, subject FROM copilot.tickets WHERE account_id = %(account_id)s ORDER BY created_at DESC LIMIT %(limit)s", (("account_id", str), ("limit", int))),
    "open_incidents_for_account": ("SELECT i.incident_id, i.title, i.component, i.severity, i.started_at FROM copilot.incidents i JOIN copilot.incident_accounts ia ON ia.incident_id = i.incident_id WHERE i.status <> 'resolved' ORDER BY i.started_at DESC", ()),
    "recent_deployments": ("SELECT deployment_id, component, version, deployed_at, change_summary FROM copilot.deployments WHERE component = %(component)s ORDER BY deployed_at DESC LIMIT %(limit)s", (("component", str), ("limit", int))),
    "get_runbook": ("SELECT doc_id, title, product_area, version, status, effective_from, supersedes, owner, source_path, body_markdown FROM copilot.runbook_docs WHERE doc_id = %(doc_id)s", (("doc_id", str),)),
    # Infrastructure check only (M1): plain default full-text search, NO query normalisation, so the R-3 baseline weakness stays observable until M2 measures it.
    "fts_runbooks_baseline": ("SELECT doc_id, title, status, ts_rank(tsv, websearch_to_tsquery('english', %(q)s)) AS rank FROM copilot.runbook_docs WHERE tsv @@ websearch_to_tsquery('english', %(q)s) ORDER BY rank DESC, doc_id LIMIT %(limit)s", (("q", str), ("limit", int))),
    # ---- M2 retrieval. Runbooks are global knowledge; none of these reads tenant data except similar_account_tickets, which runs under RLS. ----
    "list_runbooks": ("SELECT doc_id, title, product_area, version, status, effective_from, supersedes, owner, source_path, body_markdown FROM copilot.runbook_docs ORDER BY doc_id", ()),
    "get_doc_chunks": ("SELECT chunk_id, doc_id, ordinal, section, char_start, char_end, text FROM copilot.runbook_chunks WHERE doc_id = %(doc_id)s ORDER BY ordinal", (("doc_id", str),)),
    # Lexical: OR of the query's lexemes (plainto_tsquery gives AND, which an ordinary ticket never satisfies). No synonyms, no normalisation: re-sync != resync stays visible.
    "search_chunks_lexical": ("SELECT c.chunk_id, c.doc_id, ts_rank(c.tsv, q.tq) AS score FROM copilot.runbook_chunks c, (SELECT replace(plainto_tsquery('english', %(q)s)::text, '&', '|')::tsquery AS tq) q "
                              "WHERE c.tsv @@ q.tq ORDER BY score DESC, c.chunk_id LIMIT %(limit)s", (("q", str), ("limit", int))),
    "search_chunks_vector": ("SELECT chunk_id, doc_id, 1 - (embedding OPERATOR(public.<=>) %(v)s::public.vector) AS score FROM copilot.runbook_chunks ORDER BY embedding OPERATOR(public.<=>) %(v)s::public.vector, chunk_id LIMIT %(limit)s", (("v", "vec"), ("limit", int))),
    # Tenant-scoped: similar tickets of the CASE'S OWN account. RLS decides which tickets exist for the session; there is no account parameter to abuse.
    "similar_account_tickets": ("SELECT t.ticket_id, t.account_id, t.subject, ts_rank(to_tsvector('english', t.subject || chr(32) || t.body), q.tq) AS score FROM copilot.tickets t, "
                                "(SELECT replace(plainto_tsquery('english', %(q)s)::text, '&', '|')::tsquery AS tq) q WHERE to_tsvector('english', t.subject || chr(32) || t.body) @@ q.tq "
                                "AND t.ticket_id <> %(exclude)s ORDER BY score DESC, t.ticket_id LIMIT %(limit)s", (("q", str), ("exclude", str), ("limit", int))),
}
CATALOGUE: Mapping[str, tuple] = MappingProxyType(_Q)
NAMES = tuple(_Q)
MAX_TEXT = 2000


class CatalogueError(Exception):
    pass


def statement(name: str) -> str:
    if name not in _Q:
        raise CatalogueError(f"unknown query {name!r}; the catalogue is fixed")
    return _Q[name][0]


def _check_params(name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    spec = dict(_Q[name][1])
    if set(params) != set(spec):
        raise CatalogueError(f"{name}: parameters must be exactly {sorted(spec)}")
    out: dict[str, Any] = {}
    for k, typ in spec.items():
        v = params[k]
        if typ is int:
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 500:
                raise CatalogueError(f"{name}.{k}: must be an integer 1..500")
        elif typ == "vec":
            if not isinstance(v, (list, tuple)) or len(v) != 384 or not all(isinstance(x, float) and x == x and abs(x) < 1e3 for x in v):
                raise CatalogueError(f"{name}.{k}: must be a list of 384 finite floats")
            v = "[" + ",".join(repr(x) for x in v) + "]"
        elif not isinstance(v, str) or len(v) > MAX_TEXT:
            raise CatalogueError(f"{name}.{k}: must be a string of at most {MAX_TEXT} characters")
        out[k] = v
    return out


def run(pool_or_conn, scope, guard, name: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Run one catalogue query under `scope`. Raises CatalogueError for anything not in the catalogue; ScopeError for any scope problem."""
    if not isinstance(name, str):
        raise CatalogueError("query name must be a string")
    sql_text = statement(name)
    bound = _check_params(name, params)
    with scoped(pool_or_conn, scope, guard, requested_account=bound.get("account_id")) as cur:
        cur.execute(sql_text, bound)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def all_statements() -> Sequence[str]:
    return [v[0] for v in _Q.values()]
