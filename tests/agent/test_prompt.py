from tgagent.agent.models import CATALOG
from tgagent.agent.prompt import STYLE_PROMPTS, system_prompt
from tgagent.agent.tools import AgentOptions

HAIKU = CATALOG["anthropic:claude-haiku-5-5"]
GEMINI = CATALOG["gemini:gemini-3.1-flash-lite"]
OSS = CATALOG["groq:openai/gpt-oss-120b"]
LUNA = CATALOG["openai:gpt-6-luna"]


def test_prompt_is_english_and_keeps_fact_checking_rules() -> None:
    prompt = system_prompt(AgentOptions(), HAIKU)

    assert prompt.startswith("You are an AI agent living in Telegram")
    assert "Reuters" in prompt
    assert "✅" in prompt and "❓" in prompt
    assert "language of the person's message" in prompt


def test_media_notes_follow_the_model() -> None:
    gemini, oss, haiku = (system_prompt(AgentOptions(), spec) for spec in (GEMINI, OSS, HAIKU))

    assert "listen" in gemini and "YouTube" in gemini
    assert "transcribed" in oss and "cannot see images" in oss
    assert "transcribed" in haiku and "cannot see images" not in haiku


def test_drawing_is_mentioned_only_for_models_that_draw() -> None:
    assert "Image generation" in system_prompt(AgentOptions(), LUNA)
    assert "Image generation" not in system_prompt(AgentOptions(), HAIKU)


def test_prompt_is_stable_per_model_and_style() -> None:
    normal = system_prompt(AgentOptions(), HAIKU)
    troll = system_prompt(AgentOptions(style="troll"), HAIKU)

    assert system_prompt(AgentOptions(effort="high", web=False), HAIKU) == normal
    assert troll == normal + "\n" + STYLE_PROMPTS["troll"]


def test_posts_are_explained_like_grok_and_questions_answered_as_asked() -> None:
    prompt = system_prompt(AgentOptions(), HAIKU)

    assert "arrive together in one turn" in prompt
    assert "answer exactly that question" in prompt
    assert "the way Grok does on X" in prompt
    assert "short fact-check" in prompt
