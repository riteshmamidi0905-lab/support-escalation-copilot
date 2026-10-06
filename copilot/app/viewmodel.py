"""Turns the durable case file (+ approval records, artifacts, audit) into what the operator sees. Pure presentation logic: it decides NOTHING (policy, sufficiency and approvals were decided earlier by
deterministic code); it only explains. Plain-language state explanations replace generic errors; hidden reasoning does not exist in the data, so it cannot be shown."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

REASONS = {
    "CASE_NOT_FOUND_IN_SCOPE": "The case could not be resolved inside its tenant scope.", "CASE_MISMATCH": "The action named a different case than the one being worked.", "CASE_NOT_OPEN": "The case is not open.",
    "INTEGRATION_NOT_IN_ACCOUNT": "That integration does not exist for this customer account (or belongs to another account).", "RESYNC_COOLDOWN_ACTIVE": "This integration was re-synced within the last 48 hours; a repeat is not allowed.",
    "OPEN_INCIDENT_BLOCKS_RESYNC": "A platform incident for the carrier gateway is open; the runbook says escalate, do not re-sync.", "ESCALATE_TO_ENGINEERING": "Route to engineering.",
    "CONFLICTING_EVIDENCE_NEEDS_HUMAN": "The evidence conflicts; a human must resolve it before anything is requested.", "EVIDENCE_INSUFFICIENT": "Required facts are missing, so the system abstains.",
    "APPROVAL_REQUIRED_PRODUCTION_ACTION": "A production action: requires approval by the on-call SRE.", "APPROVAL_REQUIRED_CREDIT_WITHIN_POLICY": "A credit within the account's policy: requires approval by a support manager.",
    "APPROVAL_REQUIRED_ENGINEERING_ESCALATION": "An engineering escalation: requires confirmation by a Tier-2 engineer.", "CREDIT_ABOVE_AGENT_THRESHOLD": "The credit is above what support may request. Not requested; flag for finance.",
    "NO_SLA_BREACH_EVIDENCED_IN_TICKET_HISTORY": "No SLA breach is recorded by the system SLA monitor in the ticket history.", "INCIDENT_NOT_OPEN_FOR_ACCOUNT": "The cited incident is not open for this account.",
    "INCIDENT_LINKED_AND_VERIFIED_OPEN": "The linked incident was verified open for this account.", "UNVERIFIED_STATE_ACTIONS_DISABLED": "Live status could not be verified; actions are disabled until a human re-checks.",
    "MODEL_UNAVAILABLE": "The language-model provider was unavailable.", "MODEL_TIMEOUT": "The language-model call timed out.", "MODEL_OUTPUT_INVALID": "The model's output was invalid after repair and was discarded.",
    "MODEL_BUDGET_EXCEEDED": "The model token budget was exhausted.", "RETRIEVAL_UNAVAILABLE": "Evidence retrieval failed completely.", "TICKETING_UNAVAILABLE": "The ticketing system could not be reached.",
    "PROPOSAL_STORED_INTERNALLY_NO_EXTERNAL_EFFECT": "Stored as an internal artifact; nothing is sent.", "SECRET_IN_PARAMS": "A parameter contained a credential-like value and was refused.",
    "FORBIDDEN_ACTION": "The model asked for something that can never be done (e.g. e-mail the customer, run SQL, approve its own action).", "UNKNOWN_ACTION_TYPE": "The model invented an action type that does not exist.",
    "SCHEMA_INVALID": "The proposed action did not match its schema (unexpected or missing fields).", "NO_VALID_EVIDENCE_CITED": "The proposal cited no valid evidence.", "PROPOSAL_STILL_INVALID_AFTER_REPAIR": "Some proposals stayed invalid after one repair round and were dropped.",
    "BREAKER_OPEN": "The circuit breaker for this dependency is open: calls fail fast while it recovers.", "REJECTED": "The dependency permanently rejected the request.", "NOT_FOUND": "The dependency has no record of that item.",
    "TIMEOUT_RETRIES_EXHAUSTED": "The dependency kept timing out; retries are exhausted.", "OUTCOME_UNKNOWN_NEEDS_HUMAN_RECONCILIATION": "The customer system did not answer and cannot confirm; a human must reconcile.", "TRANSIENT_RETRIES_EXHAUSTED": "The customer system kept failing; retries are exhausted.",
    "REJECTED_BY_CUSTOMER_SYSTEM": "The customer system permanently rejected the request.", "APPROVAL_REQUIRED": "No approval exists for this action.", "ACTION_CHANGED": "The action differs from the one that was approved: a new approval is required.",
}
SEVERITY_ORDER = ["ok", "info", "warn", "danger"]


def why(code: str) -> str:
    """Plain-language reason for a machine code. 'api:CODE' (a dependency failure) is split so both halves read naturally; the result always ends in a full stop."""
    if code in REASONS:
        text = REASONS[code]
    elif re.fullmatch(r"[a-z_]+:[A-Z_]+", code or ""):
        api, c = code.split(":", 1)
        text = f"{api.replace('_', ' ')}: " + REASONS.get(c, c.replace("_", " ").lower())
    else:
        text = (code or "").replace("_", " ").capitalize()
    return text if text[-1:] in ".!?" else text + "."


def banner(row: dict[str, Any], f: dict[str, Any]) -> dict[str, str]:
    st, out, disp = row["state"], row.get("outcome") or f.get("outcome"), row.get("disposition") or f.get("disposition")
    deg = f.get("degraded")
    if st == "FAILED":
        fail = f.get("failure") or {}
        return {"tone": "danger", "label": "FAILED", "text": f"The case could not proceed at {fail.get('stage', '?')}: {why(fail.get('code', 'UNKNOWN'))} No action was taken."}
    if st == "REVIEW":
        pend = [a for a in f.get("approvals", []) if a["status"] == "awaiting_approval"]
        return {"tone": "info", "label": "WAITING FOR A HUMAN", "text": f"{len(pend)} proposed action(s) need approval before anything is executed. Nothing has happened on the customer's systems."}
    if st in ("INTAKE", "SCOPE", "RETRIEVE", "VERIFY", "DIAGNOSE", "PLAN", "EXECUTE", "DRAFT", "NEW"):
        return {"tone": "info", "label": f"IN PROGRESS ({st})", "text": "The workflow is running or waiting to be resumed by the recovery worker."}
    if disp == "OUTCOME_UNCERTAIN":
        return {"tone": "danger", "label": "UNCERTAIN OUTCOME", "text": "The customer system did not confirm the write. The system did NOT retry (a retry could apply the change twice). A human must reconcile it."}
    if disp in ("EXECUTED_RECONCILED",):
        return {"tone": "ok", "label": "RECONCILED", "text": "A human reconciled the uncertain write with the customer system's record."}
    if st == "HANDED_OFF" and out == "DEGRADED":
        return {"tone": "warn", "label": "DEGRADED", "text": "The case could not be fully automated: " + why((deg or {}).get("reason") or (f.get("verification", {}).get("unverified_reasons") or ["a dependency was unavailable"])[0]) + " Actions are disabled; a human takes over."}
    if st == "HANDED_OFF" and disp == "DENIED":
        return {"tone": "warn", "label": "HANDED OFF: DENIED", "text": "The approver denied the action. Nothing was executed; the case stays open for a human."}
    if st == "HANDED_OFF" and disp == "EXPIRED":
        return {"tone": "warn", "label": "HANDED OFF: APPROVAL TIMED OUT", "text": "Nobody decided in time; a timeout counts as a denial. Nothing was executed; the case stays open."}
    if st == "HANDED_OFF" and disp == "EXECUTION_FAILED":
        return {"tone": "danger", "label": "HANDED OFF: EXECUTION FAILED", "text": "The approved action failed on the customer system. The failure is recorded; a human takes over."}
    if st == "HANDED_OFF":
        return {"tone": "warn", "label": "HANDED OFF", "text": "A human takes over this case."}
    if st == "REFUSED":
        return {"tone": "warn", "label": "REFUSED", "text": "The request is outside policy. Nothing was executed" + ("; flag for finance." if any("FLAG_FOR_FINANCE" in c for c in (f.get("reason") or {}).get("constraints", [])) else ".")}
    if st == "ABSTAINED":
        return {"tone": "warn", "label": "ABSTAINED" if out == "INSUFFICIENT_EVIDENCE" else "NEEDS CLARIFICATION", "text": "No applicable evidence was found, so the system does not guess." if out == "INSUFFICIENT_EVIDENCE" else "The request is too vague to act on; a clarifying question is drafted."}
    if st == "ESCALATED":
        return {"tone": "info", "label": "ESCALATED", "text": "Routed to engineering."}
    if st == "CLOSED":
        flagged = f.get("retrieval", {}).get("outcome") == "CONFLICTING_AUTHORITATIVE_EVIDENCE"
        done = [e for e in f.get("executions", []) if e["status"] in ("SUCCEEDED", "REPLAYED")]
        tail = (f" {len(done)} approved action(s) were carried out on the customer's system, once each." if done else " Nothing was executed.") + (" A draft reply is waiting for human review; it is never sent by this system." if (f.get("draft_reply") or {}).get("text") else "")
        return {"tone": "warn" if flagged else "ok", "label": "CLOSED: CONFLICTING EVIDENCE FLAGGED" if flagged else "CLOSED", "text": ("Active documents disagree; the draft discloses it and a human confirms." if flagged else "Completed.") + tail}
    return {"tone": "info", "label": st, "text": ""}


def fmt_ts(ts: Any) -> str:
    if isinstance(ts, datetime):
        return (ts.astimezone(UTC) if ts.tzinfo else ts).strftime("%Y-%m-%d %H:%M:%S UTC")
    if isinstance(ts, str):
        return ts.replace("T", " ").replace("Z", " UTC")[:23]
    return ""


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def build(row: dict[str, Any], f: dict[str, Any], *, ident, approvals: list[Any], transitions: list[dict[str, Any]], audit: list[dict[str, Any]], artifacts: list[dict[str, Any]], registry, now: datetime) -> dict[str, Any]:
    cf = f.get("case_file") or {}
    diag = f.get("diagnosis") or {}
    verdicts = {a["evidence"]: a for a in diag.get("applicability", [])}
    ev = []
    for e in (f.get("retrieval") or {}).get("evidence", []):
        doc = registry.docs.get(e["doc_id"], {})
        ev.append({**e, "verdict": verdicts.get(e["handle"], {}).get("verdict"), "note": verdicts.get(e["handle"], {}).get("note"), "owner": doc.get("owner"), "source_path": doc.get("source_path"),
                   "effective_from": str(doc.get("effective_from", "")), "supersedes": doc.get("supersedes"), "text_short": e["text"][:380]})
    cited_by_ref = {f"{e['doc_id']}@{e['version']}": e for e in ev}
    actions = []
    ex_by = {e["action_id"]: e for e in f.get("executions", []) if e["status"] != "PENDING"}
    appr_by_id = {a.approval_id: a for a in approvals}
    for p in (f.get("plan") or {}).get("actions", []):
        dec = p.get("decision") or {}
        rec = appr_by_id.get(p.get("approval_id") or "")
        entry = next((a for a in f.get("approvals", []) if a["approval_id"] == p.get("approval_id")), None)
        actions.append({"action_id": p["action_id"], "type": p["type"], "params": p["raw"]["params"], "status": ex_by[p["action_id"]]["status"] if p["action_id"] in ex_by else rec.status if rec and rec.status in ("approved", "denied", "expired") else p["status"], "decision": dec.get("decision"), "sufficiency": dec.get("sufficiency"),
                        "reasons": [{"code": r, "text": why(r)} for r in p.get("reasons", [])], "limitations": dec.get("limitations", []), "missing_facts": dec.get("missing_facts", []), "role": p["raw"]["required_role"],
                        "rationale": p["model"]["rationale"], "cited": [{"ref": r, "ev": cited_by_ref.get(r)} for r in p["model"].get("evidence_refs", [])], "invocation_id": p["model"].get("invocation_id"),
                        "amended_from": p.get("amended_from"), "amended_by": p.get("amended_by"), "history": p.get("history", []), "approval": _approval_view(rec, entry, ident, p, now), "raw_json": pretty(p["raw"])})
    superseded = []
    for a in f.get("approvals", []):
        if a["status"] == "superseded":
            rec = appr_by_id.get(a["approval_id"])
            superseded.append({"approval_id": a["approval_id"], "superseded_by": a.get("superseded_by"), "reason": a.get("decision_reason"), "action_json": pretty(json.loads(rec.action_canonical)) if rec else ""})
    d = f.get("draft_reply")
    note_art = next((x for x in artifacts if x["kind"] == "internal_note"), None)
    draft_art = next((x for x in artifacts if x["kind"] == "draft_reply"), None)
    steps = ["INTAKE", "SCOPE", "RETRIEVE", "VERIFY", "DIAGNOSE", "PLAN", "REVIEW", "EXECUTE", "DRAFT"]
    visited = {t["to"]: t for t in transitions}
    tl = []
    for s in steps:
        t = visited.get(s)
        tl.append({"state": s, "at": fmt_ts(t["ts"]) if t else "", "done": bool(t) and row["state"] != s, "current": row["state"] == s, "skipped": not t})
    if row["state"] in ("CLOSED", "REFUSED", "ABSTAINED", "ESCALATED", "HANDED_OFF", "FAILED"):
        t = visited.get(row["state"])
        tl.append({"state": row["state"], "at": fmt_ts(t["ts"]) if t else "", "done": False, "current": True, "skipped": False})
    return {
        "row": row, "banner": banner(row, f), "timeline": tl, "ticket": f.get("ticket") or {}, "scope": f.get("scope") or {}, "evidence": ev,
        "retrieval": f.get("retrieval") or {}, "excluded": (f.get("retrieval") or {}).get("excluded", []), "hypotheses": diag.get("hypotheses", []), "applicability": diag.get("applicability", []),
        "missing": diag.get("missing_evidence", []), "uncertainty": diag.get("uncertainty"), "disposition_model": diag.get("disposition"), "rationale": diag.get("rationale"),
        "contradictions": cf.get("contradictions", []), "verification": f.get("verification") or {}, "actions": actions, "superseded": superseded, "rejected": (f.get("plan") or {}).get("rejected", []),
        "executions": [{**e, "why": [why(r) for r in e.get("reasons", [])]} for e in f.get("executions", [])], "draft": d, "draft_text_stored": draft_art["body"] if draft_art else None,
        "note": note_art["body"] if note_art else None, "reason": cf.get("reason"), "limitations": cf.get("limitations", []), "degraded": f.get("degraded"),
        "audit": [{"seq": a["seq"], "type": a["type"], "ts": fmt_ts(a["ts"]), "actor": f"{a['actor']['kind']}:{a['actor']['id']}", "hash": a["hash"][:12], "payload": a["payload"], "corr": a["correlation"]} for a in audit],
        "outcome": row.get("outcome") or f.get("outcome"), "disposition": row.get("disposition") or f.get("disposition"),
        "draft_stale": bool(d and d.get("text") and (row.get("disposition") or f.get("disposition")) == "EXECUTED_RECONCILED"),
    }


def _approval_view(rec, entry, ident, plan_entry, now: datetime) -> dict[str, Any] | None:
    if rec is None:
        return None
    remaining = int((rec.expires_at - now).total_seconds()) if rec.status == "pending" else None
    can = bool(ident and rec.status == "pending" and remaining is not None and remaining > 0 and ident.role == rec.required_role and ident.covers(rec.account_id) and ident.id != rec.requester_id
               and ident.id != plan_entry.get("amended_by"))
    return {"approval_id": rec.approval_id, "status": rec.status, "role": rec.required_role, "requester": rec.requester_id, "created": fmt_ts(rec.created_at), "expires": fmt_ts(rec.expires_at), "remaining_s": remaining,
            "approver": rec.approver_id, "decided": fmt_ts(rec.decided_at) if rec.decided_at else None, "reason": rec.decision_reason, "hash": rec.action_hash, "canonical": pretty(json.loads(rec.action_canonical)),
            "shown": pretty(rec.evidence), "can_decide": can, "viewer_role": getattr(ident, "role", None)}
