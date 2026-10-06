"""The frozen runtime must be exactly the verified commit. A moved branch, a different fork or a local edit must fail the build."""
import importlib.metadata as md
import json

import pytest

PINNED = "231b1866f88ac31339fbb2b87ffee88d75d5a573"


def _direct_url():
    dist = md.distribution("ai-agent-from-scratch")
    raw = dist.read_text("direct_url.json")
    return json.loads(raw) if raw else None


def test_runtime_is_installed_from_the_pinned_commit():
    du = _direct_url()
    assert du is not None, "runtime was not installed from a VCS URL (no direct_url.json)"
    assert du["vcs_info"]["vcs"] == "git"
    assert du["vcs_info"]["commit_id"] == PINNED
    assert du["url"].endswith("riteshmamidi0905-lab/ai-agent-from-scratch.git")


def test_pyproject_pins_the_same_commit():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert f"@{PINNED}" in text
    assert "ai-agent-from-scratch @ git+https://github.com/riteshmamidi0905-lab/ai-agent-from-scratch.git@" in text


def test_runtime_capabilities_we_rely_on_exist():
    from agent.evals import run_suite  # noqa: F401
    from agent.loop import Agent  # noqa: F401
    from agent.model import ModelProvider, OpenAICompatProvider, ScriptedModel  # noqa: F401
    from agent.reliability import Budget, EscalationRequired  # noqa: F401
    from agent.security import Policy, redact  # noqa: F401
    from agent.trace import Tracer  # noqa: F401
    assert "approver" in Policy.__dataclass_fields__


def test_runtime_smoke_run_is_deterministic():
    from agent.loop import Agent
    from agent.model import RuleModel
    from agent.tools import default_registry
    st = Agent(RuleModel(), default_registry(), sleep=lambda s: None).run("compute 6 x 7")
    assert st.status == "completed" and "42" in st.final_answer


@pytest.mark.xfail(reason="DOCUMENTED GAP (see docs/risks.md R-1): the runtime's approver hook is a bool callback, so role/expiry/diff checks must wrap it", strict=True)
def test_runtime_approver_hook_carries_role_and_diff():
    import inspect

    from agent.security import Policy
    sig = inspect.signature(Policy.check)
    assert "role" in sig.parameters
