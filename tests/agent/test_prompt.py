from pathlib import Path

from tgagent.agent.models import CATALOG
from tgagent.agent.prompt import (
    SECRETARY_PROMPT,
    STYLE_PROMPTS,
    SYSTEM_PROMPT,
    read_prompt,
    secretary_prompt,
    system_prompt,
)
from tgagent.agent.tools import AgentOptions

HAIKU = CATALOG["anthropic:claude-haiku-5-5"]
GEMINI = CATALOG["gemini:gemini-3.1-flash-lite"]
OSS = CATALOG["groq:openai/gpt-oss-120b"]
LUNA = CATALOG["openai:gpt-6-luna"]
REPO_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


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
    assert "Short fact-check" in prompt


def test_a_file_with_only_its_comment_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "system.md").write_text("<!--\nWrite your prompt here.\n-->\n", encoding="utf-8")

    assert read_prompt(tmp_path / "system.md") is None
    assert read_prompt(tmp_path / "missing.md") is None
    assert system_prompt(AgentOptions(), HAIKU, prompts=tmp_path).startswith(SYSTEM_PROMPT)


def test_own_main_prompt_replaces_the_built_in_one(tmp_path: Path) -> None:
    (tmp_path / "system.md").write_text("<!-- notes -->\nYou are Jarvis.\n", encoding="utf-8")
    (tmp_path / "fact-check.md").write_text("# Checking claims\nBe careful.", encoding="utf-8")

    prompt = system_prompt(AgentOptions(), OSS, prompts=tmp_path)

    assert prompt.startswith("You are Jarvis.\n\n# Checking claims\nBe careful.")
    assert SYSTEM_PROMPT not in prompt
    assert "transcribed" in prompt


def test_the_repository_ships_working_fact_checking_rules() -> None:
    rules = read_prompt(REPO_PROMPTS / "fact-check.md")

    assert rules is not None and "Reuters" in rules


def test_secretary_uses_the_built_in_prompt_by_default(tmp_path: Path) -> None:
    (tmp_path / "secretary.md").write_text("<!-- write the prompt below -->\n", encoding="utf-8")

    assert secretary_prompt() == SECRETARY_PROMPT
    assert secretary_prompt(tmp_path) == SECRETARY_PROMPT


def test_secretary_md_with_text_replaces_the_built_in_prompt(tmp_path: Path) -> None:
    (tmp_path / "secretary.md").write_text("<!-- note -->\nAnswer only in English.\n", encoding="utf-8")

    assert secretary_prompt(tmp_path) == "Answer only in English."
