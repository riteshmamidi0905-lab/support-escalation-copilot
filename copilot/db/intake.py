"""TRUSTED intake: the only place a scope originates.

The case service (not a model-facing tool) reads the TICKET ROW, takes the account from that row, creates a case bound to it and mints a signed scope.
Ticket text, model output and request parameters never influence which account is chosen: the only input is a ticket id, and the account comes from
the database row it names.
"""
from __future__ import annotations

import psycopg

from copilot.scope import Scope, ScopeError, Signer


class Intake:
    def __init__(self, intake_dsn: str, signer: Signer):
        self.dsn, self.signer = intake_dsn, signer

    def open_case(self, ticket_id: str) -> tuple[str, Scope]:
        with psycopg.connect(self.dsn) as c:
            row = c.execute("SELECT account_id FROM copilot.tickets WHERE ticket_id = %s", (ticket_id,)).fetchone()
            if row is None:
                raise ScopeError("unknown ticket")
            account_id = row[0]
            case_id = c.execute("SELECT 'CASE-' || lpad(nextval('copilot.case_seq')::text, 6, '0')").fetchone()[0]
            c.execute("INSERT INTO copilot.cases (case_id, account_id, ticket_id) VALUES (%s, %s, %s)", (case_id, account_id, ticket_id))
        return case_id, self.signer.mint(account_id, case_id)

    def rescope(self, case_id: str) -> Scope:
        """Mint a fresh scope for an existing case (tokens are short-lived). The account again comes from the case row."""
        with psycopg.connect(self.dsn) as c:
            row = c.execute("SELECT account_id FROM copilot.cases WHERE case_id = %s", (case_id,)).fetchone()
        if row is None:
            raise ScopeError("unknown case")
        return self.signer.mint(row[0], case_id)
