"""Load a VALIDATED synthetic dataset into the database as the least-privileged loader role.
Answer keys never enter the application database: synthetic labels, scenarios and the 'adversarial' test flag stay in the dataset files."""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from copilot import contracts as C

DATA_TABLES = ["accounts", "account_contacts", "contracts", "integrations", "incidents", "incident_accounts", "deployments", "release_notes",
               "runbook_docs", "tickets", "ticket_history", "cases"]


def load_dataset(loader_dsn: str, dataset: Path) -> dict:
    C.validate_dataset(dataset).raise_if_failed()        # never load anything that does not satisfy the contracts
    rd = lambda n: C.read_jsonl(dataset / n)           # noqa: E731
    accounts, contracts, integrations = rd("accounts.jsonl"), rd("contracts.jsonl"), rd("integrations.jsonl")
    incidents, deployments, tickets = rd("incidents.jsonl"), rd("deployments.jsonl"), rd("tickets.jsonl")
    runbooks, releases = rd("runbooks.jsonl"), rd("release_notes.jsonl")
    counts = {}
    with psycopg.connect(loader_dsn) as c, c.transaction():
        c.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(sql.Identifier("copilot", t) for t in DATA_TABLES)))
        cur = c.cursor()
        cur.executemany("INSERT INTO copilot.accounts VALUES (%s,%s,%s,%s,%s,%s)", [(a["account_id"], a["name"], a["tier"], a["region"], a["onboarded_on"], a["status"]) for a in accounts])
        cur.executemany("INSERT INTO copilot.account_contacts VALUES (%s,%s,%s)", [(a["account_id"], x["name"], x["email"]) for a in accounts for x in a["contacts"]])
        cur.executemany("INSERT INTO copilot.contracts VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", [(k["contract_id"], k["account_id"], k["tier"], k["sla_response_minutes"], k["sla_resolution_hours"],
                        k["credit_policy"]["max_agent_requestable_pct"], k["credit_policy"]["max_manager_approvable_pct"], k["effective_from"], k["effective_to"]) for k in contracts])
        cur.executemany("INSERT INTO copilot.integrations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", [(i["integration_id"], i["account_id"], i["kind"], i["provider"], i["sync_interval_minutes"],
                        i["endpoint_host"], i["status"], i["last_sync_at"], i["last_resync_at"]) for i in integrations])
        cur.executemany("INSERT INTO copilot.incidents VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [(i["incident_id"], i["title"], i["component"], i["severity"], i["status"], i["started_at"], i["resolved_at"], i["summary"]) for i in incidents])
        cur.executemany("INSERT INTO copilot.incident_accounts VALUES (%s,%s)", [(i["incident_id"], a) for i in incidents for a in i["affected_account_ids"]])
        cur.executemany("INSERT INTO copilot.deployments VALUES (%s,%s,%s,%s,%s)", [(d["deployment_id"], d["component"], d["version"], d["deployed_at"], d["change_summary"]) for d in deployments])
        cur.executemany("INSERT INTO copilot.release_notes VALUES (%s,%s,%s,%s,%s)", [(r["release_id"], r["component"], r["version"], r["released_at"], r["notes"]) for r in releases])
        # runbooks: parents must exist before children that supersede them
        by_id = {r["doc_id"]: r for r in runbooks}
        ordered, seen = [], set()

        def visit(r):
            if r["doc_id"] in seen:
                return
            if r["supersedes"]:
                visit(by_id[r["supersedes"]])
            seen.add(r["doc_id"])
            ordered.append(r)
        for r in runbooks:
            visit(r)
        cur.executemany("INSERT INTO copilot.runbook_docs (doc_id,title,product_area,version,status,effective_from,supersedes,owner,source_path,body_markdown) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(r["doc_id"], r["title"], r["product_area"], r["version"], r["status"], r["effective_from"], r["supersedes"], r["owner"], r["source_path"], r["body_markdown"]) for r in ordered])
        cur.executemany("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", [(t["ticket_id"], t["account_id"], t["created_at"], t["product_area"], t["severity"], t["subject"], t["body"],
                        t["reporter"]["name"], t["reporter"]["email"], t["channel"]) for t in tickets])
        cur.executemany("INSERT INTO copilot.ticket_history VALUES (%s,%s,%s,%s,%s,%s)", [(t["ticket_id"], t["account_id"], n, h["at"], h["author"], h["text"]) for t in tickets for n, h in enumerate(t["history"], 1)])
    for name, rows in dict(accounts=accounts, contracts=contracts, integrations=integrations, incidents=incidents, deployments=deployments, release_notes=releases, runbook_docs=runbooks, tickets=tickets).items():
        counts[name] = len(rows)
    counts["ticket_history"] = sum(len(t["history"]) for t in tickets)
    counts["incident_accounts"] = sum(len(i["affected_account_ids"]) for i in incidents)
    return counts
