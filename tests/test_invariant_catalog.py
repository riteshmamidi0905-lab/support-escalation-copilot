import re
from pathlib import Path

from copilot.invariants import ATTACKS, INVARIANTS, attacks_for

ROOT = Path(__file__).resolve().parent.parent
THREAT = (ROOT / "docs" / "threat-model.md").read_text()


def test_four_invariants_each_with_many_attacks():
    assert set(INVARIANTS) == {"I1", "I2", "I3", "I4"}
    for i in INVARIANTS:
        assert len(attacks_for(i)) >= 5, f"{i} has too few planned attacks"


def test_attack_ids_unique_and_well_formed():
    ids = [a.id for a in ATTACKS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"A-I[1-4]-\d\d", i) for i in ids)
    assert all(a.status in ("planned", "implemented") and re.fullmatch(r"M[0-6]", a.milestone) for a in ATTACKS)


def test_every_attack_is_in_the_threat_model():
    missing = [a.id for a in ATTACKS if a.id not in THREAT]
    assert not missing, f"threat-model.md does not mention {missing}"


def test_status_is_honest_implemented_attacks_have_a_test():
    """An attack may only be marked 'implemented' if a test names it. Prevents the catalogue from claiming coverage that does not exist."""
    corpus = "\n".join(p.read_text() for p in (ROOT / "tests").rglob("test_*.py"))
    for a in ATTACKS:
        if a.status == "implemented":
            assert a.id in corpus, f"{a.id} is marked implemented but no test references it"


def test_attack_ids_referenced_by_tests_exist():
    corpus = "\n".join(p.read_text() for p in (ROOT / "tests").rglob("test_*.py"))
    known = {a.id for a in ATTACKS}
    assert set(re.findall(r"A-I[1-4]-\d\d", corpus)) <= known
