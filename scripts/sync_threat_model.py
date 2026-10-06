"""Regenerates the attack tables in docs/threat-model.md from copilot/invariants.py so documentation, catalogue and tests cannot drift."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from copilot.invariants import ATTACKS, INVARIANTS  # noqa: E402

p = Path(__file__).resolve().parent.parent / "docs" / "threat-model.md"
text = p.read_text()
head = text[: text.index("## Invariants and the attacks")]
tail = text[text.index("## Threats specific to the design"):]
body = ("## Invariants and the attacks that must try to break them\nEach attack below is a *test that attempts the violation*. `implemented` means a test exists in the repository now; "
        "`planned` names the milestone that adds it. The catalogue lives in `copilot/invariants.py`; `tests/test_invariant_catalog.py` keeps this document, the catalogue and the tests in sync "
        "(regenerate with `python scripts/sync_threat_model.py`).\n")
for i in ("I1", "I2", "I3", "I4"):
    body += f"\n### {i} — {INVARIANTS[i]}\n| Attack | Attempted violation | Milestone | Status |\n|---|---|---|---|\n"
    body += "".join(f"| {a.id} | {a.description} | {a.milestone} | {a.status} |\n" for a in ATTACKS if a.invariant == i)
impl = sum(a.status == "implemented" for a in ATTACKS)
body += f"\n**Totals:** {len(ATTACKS)} attacks; {impl} have executable tests; {len(ATTACKS) - impl} are planned.\n\n"
p.write_text(head + body + tail)
print(len(ATTACKS), impl)
