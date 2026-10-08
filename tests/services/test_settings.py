from tgagent.agent.tools import AgentOptions
from tgagent.services.settings import options_from, toggle


def test_defaults_when_nothing_saved() -> None:
    assert options_from({}, default_effort="medium") == AgentOptions()


def test_saved_values_override_defaults() -> None:
    saved = {"effort": "high", "show_thinking": True, "web": False, "code": False}

    assert options_from(saved, default_effort="low") == AgentOptions("high", True, False, False)


def test_garbage_values_are_ignored() -> None:
    assert options_from({"effort": "ultra", "web": "yes"}, default_effort="low") == AgentOptions(effort="low")


def test_toggle_returns_patch() -> None:
    options = AgentOptions()

    assert toggle(options, "web") == {"web": False}
    assert toggle(options, "show_thinking") == {"show_thinking": True}
    assert toggle(options, "effort:high") == {"effort": "high"}
    assert toggle(options, "effort:bogus") == {}
    assert toggle(options, "unknown") == {}
