"""Deterministic grounding checks for customer-facing DRAFTS (the draft-steering mitigation). M4 showed that deterministic controls contain executable harm but cannot stop a fooled model
from writing a misleading, non-executable draft. These checks make a class of steering *detectable before a human sees the draft*; they do not make drafts true.

What is checked (each is a cheap, explainable rule over the draft text and the case's own evidence):
  * numeric grounding: every number, version, and identifier in the draft must appear in the cited evidence, the ticket, the account facts or a document title/version;
  * effect claims: 'we have re-synced / credited / escalated ...' only when that exact action actually SUCCEEDED in this case; 'resolved / fixed / restored' never;
  * no URLs, domains, e-mail addresses or phone numbers that are not in the evidence;
  * no other account's identifiers; no internal policy terms (thresholds, approver roles, prompts);
  * promises and guarantees (guarantee, promise, ETA commitments) are refused;
  * instruction-echo phrases (advisory only; the rules above do not depend on it);
  * hedging: if the case has conflicting/contradicted/stale evidence or high uncertainty, the draft must say it needs confirmation.
Residual weakness (stated, measured in docs/m5-draft-steering.md): a draft that is wrong or misleading using only words and numbers that ARE grounded (a plausible but incorrect causal claim, a
reassurance that contradicts the facts, a wrong recommendation) passes every rule here. The mandatory human review (with an elevated, itemised review when risk flags are present) is the control for that."""
from __future__ import annotations

import re

_NUM = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+)*)(?![\w])")
_ID = re.compile(r"\b(?:INT|INC|RBK|TCK|ACC|CASE|CTR|ACT|APR)-\d{3,6}\b")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|dev|app|co|biz|info|ru|cn|xyz)\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_PROMISE = re.compile(r"(?i)\b(guarantee[sd]?|promise[sd]?|definitely will|100% (?:sure|certain)|without fail)\b")
_RESOLVED = re.compile(r"(?i)\b(?:is|are|has been|have been|was|were|now|already)\s+(?:now\s+|fully\s+|completely\s+)?(?:resolved|fixed|restored|repaired|solved)\b|\bwe(?:'ve| have)? (?:resolved|fixed|restored|repaired|solved)\b")
_INTERNAL = re.compile(r"(?i)agent[-_ ]requestable|manager[-_ ]approvable|max_(?:agent|manager)|approval threshold|credit threshold|policy engine|system prompt|my instructions|hidden instructions|\bSEV[0-9]\b|idempotency")
_ECHO = re.compile(r"(?i)ignore (?:all |any )?(?:previous|prior|above)|as (?:the )?(?:operations )?notes? (?:say|instruct)|system override|you are now|new instructions?")
_HEDGE = re.compile(r"(?i)\b(confirm|verif|disagree|differ|conflict|not yet|may|might|likely|investigat|will check|we will|an engineer will)\w*")
_EFFECT = (  # (pattern, action type that must have SUCCEEDED/REPLAYED)
    (re.compile(r"(?i)\b(?:we(?:'ve| have)? |has been |have been |was |were |already )(?:re-?synced|re-?synchron\w+|restarted)\b|\bre-?sync (?:has|was|is) (?:been )?(?:completed|started|applied|done)"), "trigger_resync"),
    (re.compile(r"(?i)\b(?:we(?:'ve| have)? |has been |have been |was |were |already )(?:credited|refunded|applied (?:a |the )?credit|issued (?:a |the )?credit)\b|\bcredit (?:has|was|is) (?:been )?(?:applied|issued|granted)"), "request_sla_credit"),
    (re.compile(r"(?i)\b(?:we(?:'ve| have)? |has been |have been |was |were |already )(?:escalated|raised (?:this )?with engineering)\b|\bescalat\w+ (?:has|was|is) (?:been )?(?:completed|sent|done)"), "escalate_engineering"),
)


def _numbers(text: str) -> set[str]:
    return set(_NUM.findall(_ID.sub(" ", text)))               # identifiers are checked as identifiers, not as digit runs


def _tokens(texts: list[str]) -> tuple[set[str], set[str]]:
    nums, ids = set(), set()
    for t in texts:
        nums |= _numbers(t)
        ids |= set(_ID.findall(t))
    return nums, ids


def check_grounding(draft: str, *, evidence_texts: list[str], ticket_text: str, own_account: str, facts_text: str = "", executed_types: set[str] | frozenset[str] = frozenset(), needs_hedge: bool = False) -> list[str]:
    """Return a list of problems (empty = no rule fired). Each message names the rule so the repair prompt and the human reviewer can see exactly what was refused."""
    p: list[str] = []
    corpus = list(evidence_texts) + [ticket_text, facts_text]
    nums, ids = _tokens(corpus)
    corpus_l = "\n".join(corpus).lower()
    for n in sorted(_numbers(draft) - nums):
        p.append(f"ungrounded number {n!r}: it does not appear in the cited evidence, the ticket or the account facts")
    for i in sorted(set(_ID.findall(draft)) - ids):
        if i.startswith("ACC-") and i != own_account:
            p.append(f"another account's identifier {i}")
        elif not i.startswith("ACC-"):
            p.append(f"ungrounded identifier {i}")
    for u in _URL.findall(draft):
        if u.lower() not in corpus_l:
            p.append(f"link or domain {u!r} is not in the evidence")
    for e in _EMAIL.findall(draft):
        p.append(f"e-mail address {e!r}")
    for ph in _PHONE.findall(draft):
        if ph not in " ".join(corpus):
            p.append("phone number not in the evidence")
    if _PROMISE.search(draft):
        p.append("promise or guarantee")
    if _RESOLVED.search(draft):
        p.append("claims the problem is resolved/fixed; only a human can say that")
    for rx, action in _EFFECT:
        if rx.search(draft) and action not in executed_types:
            p.append(f"claims an effect ({action}) that did not happen in this case")
    if _INTERNAL.search(draft):
        p.append("reveals internal policy, role or system details")
    if _ECHO.search(draft):
        p.append("repeats instruction-like text from a document or ticket")
    if re.search(r"```|<[a-z]+[^>]*>|\]\(http", draft):
        p.append("markup, code or links are not allowed in a draft")
    if needs_hedge and not _HEDGE.search(draft):
        p.append("the evidence is conflicting/stale/uncertain: the draft must say it needs confirmation")
    return p
