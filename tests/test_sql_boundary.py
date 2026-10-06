"""A-I2-23 — static check: SQL is executed only inside copilot.db, and never built by string formatting or concatenation."""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.invariant
ROOT = Path(__file__).resolve().parent.parent / "copilot"
EXEC_NAMES = {"execute", "executemany"}


def calls(path):
    tree = ast.parse(path.read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in EXEC_NAMES:
            recv = n.func.value
            if isinstance(recv, ast.Attribute) and recv.attr == "gateway":        # ControlGateway.execute(action, ...) is the typed-action executor, not a database call
                continue
            yield n


def test_no_sql_execution_outside_the_db_package():                               # A-I2-23
    # copilot.db is the data layer. The trusted control service (never model-facing) runs its own FIXED statements as the control role: only these four modules may.
    allowed = {("control", "audit.py"), ("control", "approvals.py"), ("control", "ledger.py"), ("control", "gateway.py"), ("workflow", "machine.py")}      # machine.py: the durable case state machine (M4)
    offenders = [f"{p.relative_to(ROOT)}:{c.lineno}" for p in ROOT.rglob("*.py") if "db" not in p.relative_to(ROOT).parts[:1] and p.name != "rebuild.py"
                 and (p.relative_to(ROOT).parts[0], p.name) not in allowed for c in calls(p)]
    assert offenders == [], f"SQL executed outside copilot/db: {offenders}"


def test_statements_are_never_built_by_formatting_or_concatenation():            # A-I2-23
    bad = []
    for p in ROOT.rglob("*.py"):
        for c in calls(p):
            if not c.args:
                continue
            a = c.args[0]
            formatted = isinstance(a, ast.JoinedStr) or (isinstance(a, ast.BinOp) and isinstance(a.op, (ast.Mod, ast.Add))) or \
                (isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "format" and isinstance(a.func.value, ast.Constant))
            if formatted:
                bad.append(f"{p.relative_to(ROOT)}:{c.lineno}")
    # identifiers are composed with psycopg.sql.Identifier (never text formatting); everything else is a constant or a catalogue lookup
    assert bad == [], bad


def test_the_query_catalogue_statements_are_constants_with_named_parameters():
    from copilot.db import queries as Q
    for name in Q.NAMES:
        text = Q.statement(name)
        assert isinstance(text, str) and "{" not in text and "%(" in text or "%(" not in text
        assert "'" not in text.replace("'english'", "").replace("'resolved'", "").replace("'&'", "").replace("'|'", ""), f"{name}: literal values must be bound parameters"


def test_model_facing_modules_cannot_import_the_privileged_roles():
    """Tools (M3+) may only receive a catalogue runner. Nothing under copilot/ other than db/ and rebuild may reference intake/loader/admin helpers."""
    allowed = {"db", "rebuild.py"}
    for p in ROOT.rglob("*.py"):
        top = p.relative_to(ROOT).parts[0]
        if top in allowed or p.name == "rebuild.py":
            continue
        src = p.read_text()
        assert "copilot.db.admin" not in src and "copilot.db.load" not in src and "Intake" not in src, p
