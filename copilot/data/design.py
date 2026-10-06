"""Design-shape checks for a generated dataset: does it contain the DESIGNED categories and difficulties? (Not: how well anything performs on it.)"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from copilot import contracts as C
from copilot.data import generator as G

UNDOC_KEYWORDS = {"sso-sandbox": ["saml"], "legacy-export": ["fixed-width", "fixed width"], "report-builder": ["pivot"], "offline-mobile": ["offline"],
                  "onprem-connector": ["on-premise", "on-prem"], "data-residency": ["residency", "data residency"]}


def design_report(dataset: Path) -> C.Report:
    rep = C.Report()
    rd = lambda n: C.read_jsonl(dataset / n)         # noqa: E731
    accounts, contracts, tickets, labels = rd("accounts.jsonl"), rd("contracts.jsonl"), rd("tickets.jsonl"), rd("synthetic_labels.jsonl")
    docs, incidents, scen = rd("runbooks.jsonl"), rd("incidents.jsonl"), rd("scenarios.jsonl")
    if len(accounts) != G.N_ACCOUNTS or len(tickets) != G.N_TICKETS or len(labels) != G.N_TICKETS:
        rep.add("account/ticket/label counts differ from the designed shape")
    if {s["scenario_id"] for s in scen} != {f"S{i}" for i in range(1, 17)}:
        rep.add("scenario table must define S1..S16")
    cnt = Counter(lab["scenario_id"] for lab in labels)
    want = Counter()
    for k, n in G.QUOTAS.items():
        want["S6" if k.startswith("S6") else ("S14" if k == "routine" else k)] += n
    if cnt != want:
        rep.add(f"scenario counts {dict(cnt)} differ from designed quotas {dict(want)}")
    # document difficulties
    by_title = Counter(d["title"] for d in docs)
    both_active = [t for t, n in by_title.items() if n >= 2 and sum(1 for d in docs if d["title"] == t and d["status"] == "active" and "(copy)" not in d["title"]) >= 2]
    if len(both_active) < 3:
        rep.add(f"expected at least 3 topics with two ACTIVE conflicting versions, found {len(both_active)}")
    chains = [d for d in docs if d["supersedes"]]
    if len(chains) != 3:
        rep.add(f"expected 3 supersession chains, found {len(chains)}")
    if sum(1 for d in docs if d["title"].endswith("(copy)")) != 5:
        rep.add("expected 5 near-duplicate documents")
    if sum(1 for d in docs if d["adversarial"]) != len(G.INJECTIONS_DOC):
        rep.add("adversarial document count differs from design")
    # no tenant data in global knowledge
    ident = [a["account_id"] for a in accounts] + [a["name"] for a in accounts] + [c["email"] for a in accounts for c in a["contacts"]]
    ident += [re.sub(r"[^a-z0-9]+", "", a["name"].lower().replace("(fictional)", "")) for a in accounts]
    for d in docs:
        low = d["body_markdown"].lower() + " " + d["title"].lower()
        for x in ident:
            if x.lower() in low:
                rep.add(f"runbook {d['doc_id']} contains tenant identifier {x!r}")
    # undocumented topics must really be undocumented
    corpus = " ".join((d["title"] + " " + d["body_markdown"]).lower() for d in docs)
    for k, kws in UNDOC_KEYWORDS.items():
        for kw in kws:
            if kw in corpus:
                rep.add(f"undocumented topic {k!r} is accidentally documented (keyword {kw!r})")
    # label/data consistency (design intent, not performance)
    tk = {t["ticket_id"]: t for t in tickets}
    cn = {c["account_id"]: c for c in contracts}
    open_inc = {i["component"]: i for i in incidents if i["status"] == "open"}
    for lab in labels:
        t = tk[lab["ticket_id"]]
        if lab["root_cause_id"] == "open-incident-match":
            inc = open_inc.get(G.AREA_COMPONENT[t["product_area"]])
            if not inc or t["account_id"] not in inc["affected_account_ids"]:
                rep.add(f"{t['ticket_id']}: marked open-incident-match but account is not affected by an open incident of that component")
        if lab["scenario_id"] in ("S1", "S5"):
            m = re.search(r"(\d+)%", t["body"])
            lim = cn[t["account_id"]]["credit_policy"]["max_agent_requestable_pct"]
            if not m or (lab["scenario_id"] == "S5") != (int(m.group(1)) <= lim and lim > 0):
                rep.add(f"{t['ticket_id']}: credit percentage inconsistent with scenario {lab['scenario_id']}")
        if lab["scenario_id"] == "S15" and not re.search(r"synthetic", t["body"]):
            rep.add(f"{t['ticket_id']}: secret in ticket is not unmistakably synthetic")
    return rep
