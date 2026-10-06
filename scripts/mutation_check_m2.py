"""Mutation check for the M2 defences: break each one on purpose and confirm the test-suite notices. Restores every file afterwards. Run under scripts/with_local_pg.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTATIONS = [
    ("lifecycle: superseded/draft documents are not excluded", "copilot/retrieval/governance.py", "    return None if doc[\"status\"] == \"active\" else doc[\"status\"]", "    return None"),
    ("conflicts: registry never reports a conflict", "copilot/retrieval/governance.py", "            if len(by_numbers) > 1:", "            if False:"),
    ("near-duplicates: never collapsed", "copilot/retrieval/governance.py", "                if len(ids) > 1:", "                if False:"),
    ("injection flag: advisory annotation removed", "copilot/retrieval/governance.py", "    return bool(INSTRUCTION_LIKE.search(text))", "    return False"),
    ("trust marker: evidence no longer marked untrusted", "copilot/retrieval/service.py", '"content_trust": "untrusted_data"', '"content_trust": "trusted"'),
    ("citation: range off by one", "copilot/retrieval/service.py", '"source_location": {"char_start": chunk["char_start"], "char_end": chunk["char_end"]},', '"source_location": {"char_start": chunk["char_start"], "char_end": chunk["char_end"] + 1},'),
    ("tenant evidence: similar tickets ignore the case (leaks via exclude override to all rows)", "copilot/db/queries.py", "AND t.ticket_id <> %(exclude)s", "OR t.ticket_id <> %(exclude)s"),
    ("tenant evidence: scope dropped from the incident lookup", "copilot/retrieval/service.py", 'Q.run(self.pool, scope, guard, "open_incidents_for_account", {})', 'Q.run(self.pool, None, None, "open_incidents_for_account", {})'),
    ("resync baseline: lexical search quietly rewrites resync to re-sync", "copilot/db/queries.py", "plainto_tsquery('english', %(q)s)::text, '&', '|')::tsquery AS tq) q \"\n                              \"WHERE c.tsv", "plainto_tsquery('english', replace(%(q)s, 'resync', 're-sync'))::text, '&', '|')::tsquery AS tq) q \"\n                              \"WHERE c.tsv"),
]
survived = []
for name, rel, old, new in MUTATIONS:
    p = ROOT / rel
    src = p.read_text()
    assert old in src, f"mutation target not found for: {name}"
    p.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "tests/test_retrieval_units.py", "tests/db/test_m2_retrieval.py"], cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    finally:
        p.write_text(src)
    status = "KILLED" if r.returncode != 0 else "SURVIVED"
    print(f"{status:9} {name}")
    if r.returncode == 0:
        survived.append(name)
sys.exit(1 if survived else 0)
