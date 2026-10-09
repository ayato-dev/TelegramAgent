from datetime import time
from pathlib import Path

import pytest

from tgagent.services.secretary_config import SecretaryConfig, load_secretary_config

REPO_CONFIG = Path(__file__).resolve().parents[2] / "prompts" / "secretary.toml"


def test_a_missing_file_means_defaults(tmp_path: Path) -> None:
    assert load_secretary_config(tmp_path / "secretary.toml") == SecretaryConfig()


def test_the_shipped_file_holds_the_defaults() -> None:
    assert load_secretary_config(REPO_CONFIG) == SecretaryConfig()


def test_settings_left_out_keep_their_defaults(tmp_path: Path) -> None:
    path = tmp_path / "secretary.toml"
    path.write_text('max_replies = 0\nreply_when_online = true\nmodel = "groq:openai/gpt-oss-120b"\n')

    config = load_secretary_config(path)

    assert (config.max_replies, config.reply_when_online) == (0, True)
    assert config.model == "groq:openai/gpt-oss-120b"
    assert config.online_minutes == SecretaryConfig().online_minutes


@pytest.mark.parametrize(
    "text",
    [
        "max_replys = 3\n",
        "online_minutes = -1\n",
        'hours = "9-23"\n',
        'hours = "09:00-25:00"\n',
        "enabled = \n",
    ],
)
def test_mistakes_stop_the_bot_at_startup(tmp_path: Path, text: str) -> None:
    path = tmp_path / "secretary.toml"
    path.write_text(text)

    with pytest.raises(ValueError, match=r"secretary\.toml"):
        load_secretary_config(path)


def test_any_time_without_hours() -> None:
    assert SecretaryConfig().answers_at(time(3, 0))


def test_hours_within_one_day() -> None:
    config = SecretaryConfig(hours="09:00-23:00")

    assert config.answers_at(time(9, 0)) and config.answers_at(time(22, 59))
    assert not config.answers_at(time(8, 59)) and not config.answers_at(time(23, 0))


def test_hours_across_midnight() -> None:
    config = SecretaryConfig(hours="22:00-08:00")

    assert config.answers_at(time(23, 30)) and config.answers_at(time(7, 0))
    assert not config.answers_at(time(12, 0))
