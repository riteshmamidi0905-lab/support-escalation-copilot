"""Schemas for everything the model may produce (Diagnosis, ProposedActions, DraftReply). They contain ONLY conclusions, evidence references and a concise rationale:
there is no field for chain-of-thought, and none for scope, tenant, approval, role, expiry, workflow state or sufficiency. `additionalProperties:false` everywhere, so a model
that tries to supply them produces INVALID output, which takes the repair/failure path and is never executed. (Subset understood by the frozen runtime's validator.)"""
from __future__ import annotations

VERDICTS = ["applies", "partially_applies", "does_not_apply", "stale", "contradicted", "contains_instructions"]
DISPOSITIONS = ["proceed", "refuse", "abstain", "clarify", "escalate"]

DIAGNOSIS = {
    "type": "object", "additionalProperties": False,
    "required": ["hypotheses", "applicability", "missing_evidence", "uncertainty", "disposition", "rationale"],
    "properties": {
        "hypotheses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["statement", "supporting", "contradicting", "confidence"], "properties": {
            "statement": {"type": "string"}, "supporting": {"type": "array", "items": {"type": "string"}}, "contradicting": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}},
        "applicability": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["evidence", "verdict", "note"], "properties": {
            "evidence": {"type": "string"}, "verdict": {"type": "string", "enum": VERDICTS}, "note": {"type": "string"}}}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
        "disposition": {"type": "string", "enum": DISPOSITIONS},
        "rationale": {"type": "string"},
    },
}

PROPOSED_ACTIONS = {
    "type": "object", "additionalProperties": False, "required": ["actions", "rationale"],
    "properties": {
        "actions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["action_type", "params", "cited_evidence", "rationale"], "properties": {
            "action_type": {"type": "string"}, "params": {"type": "object"}, "cited_evidence": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string"}}}},
        "rationale": {"type": "string"},
    },
}

DRAFT_REPLY = {
    "type": "object", "additionalProperties": False, "required": ["draft", "cited_evidence", "limitations"],
    "properties": {"draft": {"type": "string"}, "cited_evidence": {"type": "array", "items": {"type": "string"}}, "limitations": {"type": "array", "items": {"type": "string"}}},
}
MAX_TEXT = {"statement": 400, "note": 300, "rationale": 400, "draft": 3000, "limitation": 200, "missing": 200}
