import string

import pytest

from tgagent.i18n import TEXTS, lang_of, t


@pytest.mark.parametrize(
    ("code", "lang"), [("ru", "ru"), ("ru-RU", "ru"), ("RU", "ru"), ("en", "en"), ("uk", "en"), (None, "en")]
)
def test_language_follows_the_telegram_app(code: str | None, lang: str) -> None:
    assert lang_of(code) == lang


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", sorted(TEXTS))
def test_every_text_has_both_languages_with_the_same_placeholders(key: str) -> None:
    ru, en = TEXTS[key]["ru"], TEXTS[key]["en"]

    assert ru.strip() and en.strip()
    assert placeholders(ru) == placeholders(en)


def test_texts_are_filled_in() -> None:
    assert t("en", "settings.model_chosen", label="GPT-6 Luna").startswith("Model: GPT-6 Luna.")
    assert t("ru", "wait.seconds", seconds=20) == "20 с"
