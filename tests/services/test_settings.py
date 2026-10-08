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


def test_style_saved_and_toggled() -> None:
    assert options_from({"style": "troll"}, default_effort="medium").style == "troll"
    assert options_from({"style": "evil"}, default_effort="medium").style == "normal"
    assert toggle(AgentOptions(), "style") == {"style": "troll"}
    assert toggle(AgentOptions(style="troll"), "style") == {"style": "normal"}


MODELS = ("anthropic:claude-haiku-5-5", "groq:openai/gpt-oss-120b")


def test_saved_model_must_still_be_available() -> None:
    assert options_from({"model": MODELS[1]}, default_effort="medium", models=MODELS).model == MODELS[1]
    assert options_from({"model": "openai:gpt-6-luna"}, default_effort="medium", models=MODELS).model is None


def test_model_is_picked_by_its_position() -> None:
    assert toggle(AgentOptions(), "model:1", MODELS) == {"model": MODELS[1]}
    assert toggle(AgentOptions(), "model:7", MODELS) == {}
    assert toggle(AgentOptions(), "model:x", MODELS) == {}
