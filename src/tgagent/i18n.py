"""The bot's interface in Russian and English, chosen by the language of the person's Telegram app."""

from typing import Literal

Lang = Literal["ru", "en"]
DEFAULT_LANG: Lang = "en"


def lang_of(code: str | None) -> Lang:
    return "ru" if code and code.split("-")[0].lower() == "ru" else "en"


TEXTS: dict[str, dict[Lang, str]] = {
    # Turn outcomes
    "failure": {
        "ru": "⚠️ Не удалось получить ответ. Попробуйте ещё раз чуть позже.",
        "en": "⚠️ Couldn't get an answer. Please try again a bit later.",
    },
    "refusal": {"ru": "Не могу помочь с этим запросом.", "en": "I can't help with that request."},
    "error.bad_attachment": {
        "ru": "⚠️ Не смог прочитать вложение: такой формат файла модель не принимает. "
        "Пришлите PDF, текст или картинку.",
        "en": "⚠️ Couldn't read the attachment: the model doesn't accept this file format. "
        "Send a PDF, text or an image.",
    },
    "error.rate_limited": {
        "ru": "⏳ Модель сейчас упёрлась в лимит запросов (на бесплатных тарифах он маленький). "
        "Попробуйте через {wait}.",
        "en": "⏳ The model hit its request limit (free plans have small ones). Try again in {wait}.",
    },
    "wait.minute": {"ru": "минуту", "en": "a minute"},
    "wait.seconds": {"ru": "{seconds} с", "en": "{seconds} s"},
    "error.quota": {
        "ru": "💳 У провайдера модели закончились деньги или квота. Пополните баланс или выберите "
        "другую модель в /settings.",
        "en": "💳 The model's provider is out of credit or quota. Top up the balance or pick another "
        "model in /settings.",
    },
    "error.region": {
        "ru": "🌍 Провайдер этой модели не работает в регионе сервера бота. "
        "Выберите другую модель в /settings.",
        "en": "🌍 This model's provider doesn't serve the region the bot runs in. Pick another model "
        "in /settings.",
    },
    "error.auth": {
        "ru": "🔑 Провайдер не принял API-ключ. Владельцу бота стоит проверить ключи в .env.",
        "en": "🔑 The provider rejected the API key. The bot's owner should check the keys in .env.",
    },
    "error.too_long": {
        "ru": "📚 Разговор стал слишком длинным для этой модели. Начните новый: /new.",
        "en": "📚 The conversation got too long for this model. Start a new one: /new.",
    },
    "error.unavailable": {
        "ru": "⚠️ Сервис модели сейчас недоступен или перегружен. Попробуйте ещё раз чуть позже.",
        "en": "⚠️ The model's service is down or overloaded right now. Please try again a bit later.",
    },
    "stopped": {"ru": "⏹ Остановлено", "en": "⏹ Stopped"},
    "done": {"ru": "Готово.", "en": "Done."},
    # Streaming
    "thinking": {"ru": "💭 Размышления", "en": "💭 Thoughts"},
    "thinking.placeholder": {"ru": "Думаю…", "en": "Thinking…"},
    "tool.web_search": {"ru": "🔎 Ищу", "en": "🔎 Searching"},
    "tool.web_fetch": {"ru": "🌐 Читаю", "en": "🌐 Reading"},
    "tool.code_execution": {"ru": "🐍 Считаю", "en": "🐍 Computing"},
    "tool.bash_code_execution": {"ru": "🐍 Выполняю код", "en": "🐍 Running code"},
    "tool.text_editor_code_execution": {"ru": "📝 Работаю с файлом", "en": "📝 Working on a file"},
    "tool.code_interpreter": {"ru": "🐍 Выполняю код", "en": "🐍 Running code"},
    "tool.image_generation": {"ru": "🎨 Рисую", "en": "🎨 Drawing"},
    "tool.compaction": {"ru": "🗜 Сжимаю историю", "en": "🗜 Compacting the history"},
    "tool.set_reminder": {"ru": "⏰ Ставлю напоминание", "en": "⏰ Setting a reminder"},
    "tool.list_reminders": {"ru": "⏰ Смотрю напоминания", "en": "⏰ Checking reminders"},
    "tool.cancel_reminder": {"ru": "⏰ Отменяю напоминание", "en": "⏰ Cancelling a reminder"},
    "tool.read_chat_history": {"ru": "📜 Читаю чат", "en": "📜 Reading the chat"},
    "tool.create_poll": {"ru": "📊 Создаю опрос", "en": "📊 Creating a poll"},
    "tool.reply_to_checklist_task": {"ru": "✅ Отчитываюсь по задаче", "en": "✅ Reporting on a task"},
    "guest.title": {"ru": "Ответ", "en": "Answer"},
    "guest.files": {
        "ru": "_Файлы из этого ответа можно получить в личном чате с ботом._",
        "en": "_Files from this answer are available in your private chat with the bot._",
    },
    # Settings
    "effort.low": {"ru": "⚡ Быстро", "en": "⚡ Fast"},
    "effort.medium": {"ru": "⚖️ Обычно", "en": "⚖️ Normal"},
    "effort.high": {"ru": "🧠 Глубоко", "en": "🧠 Deep"},
    "flag.show_thinking": {"ru": "💭 Размышления", "en": "💭 Thoughts"},
    "flag.web": {"ru": "🌐 Веб-поиск", "en": "🌐 Web search"},
    "flag.code": {"ru": "🐍 Код", "en": "🐍 Code"},
    "on": {"ru": "вкл", "en": "on"},
    "off": {"ru": "выкл", "en": "off"},
    "style.troll": {"ru": "😈 Стиль: дерзкий", "en": "😈 Style: bold"},
    "style.normal": {"ru": "🙂 Стиль: обычный", "en": "🙂 Style: normal"},
    "back": {"ru": "‹ Назад", "en": "‹ Back"},
    "badges": {
        "ru": "👁 видит картинки · 🎙 сама слушает голосовые и смотрит YouTube · 🌐 ищет в интернете · "
        "🐍 запускает код · 🎨 рисует картинки · 🆓 бесплатный тариф",
        "en": "👁 sees images · 🎙 listens to voice and watches YouTube itself · 🌐 searches the web · "
        "🐍 runs code · 🎨 draws pictures · 🆓 free plan",
    },
    "models.text": {
        "ru": "🤖 Сейчас: {label}\n\nВыберите модель. В личке смена модели начинает новый разговор, "
        "ответы на старые сообщения продолжаются в их модели.\n\n{badges}",
        "en": "🤖 Now: {label}\n\nPick a model. In private chats a new model starts a new conversation; "
        "replies to older messages continue in their own model.\n\n{badges}",
    },
    "settings.text": {
        "ru": "⚙️ Твои настройки — действуют в личке, в группах и в гостевом режиме\n\n"
        "Модель: {model}\n"
        "Глубина: {effort} — сколько модель размышляет перед ответом.\n"
        "Размышления: показывать ход мыслей (в стриме и свёрнутым блоком в ответе).\n"
        "Веб-поиск и код: инструменты агента — поиск в интернете и Python-песочница.\n"
        "Стиль: обычный или дерзкий (с матом и подъёбами).",
        "en": "⚙️ Your settings — they apply in private chats, groups and guest mode\n\n"
        "Model: {model}\n"
        "Depth: {effort} — how much the model thinks before answering.\n"
        "Thoughts: show the reasoning (while streaming and folded in the answer).\n"
        "Web search and code: the agent's tools — searching the web and a Python sandbox.\n"
        "Style: normal or bold (swearing and banter, in Russian slang).",
    },
    "settings.saved": {"ru": "Сохранено", "en": "Saved"},
    "settings.model_chosen": {
        "ru": "Модель: {label}. Следующее сообщение начнёт новый разговор.",
        "en": "Model: {label}. Your next message starts a new conversation.",
    },
    "settings.stale": {
        "ru": "Сообщение с настройками устарело, вызовите /settings ещё раз.",
        "en": "These settings are out of date, open /settings again.",
    },
    # Commands
    "help": {
        "ru": "Я ИИ-агент. Что умею:\n"
        "• отвечать, рассуждать и считать (Python-песочница, графики и файлы);\n"
        "• искать в интернете и читать ссылки;\n"
        "• понимать фото, PDF и документы, расшифровывать голосовые и кружочки;\n"
        "• ставить напоминания и отложенные задания («через час проверь курс и напиши»);\n"
        "• выполнять задачи из чек-листов Telegram.\n\n"
        "В личке каждый топик — отдельный разговор, /new начинает новый. Ответ на моё старое сообщение "
        "продолжает разговор с того места. Во время ответа можно нажать «Стоп».\n"
        "В группе упомяните меня или ответьте на моё сообщение.\n\n"
        "/settings — модель, глубина размышлений и инструменты, /usage — расходы.",
        "en": "I'm an AI agent. I can:\n"
        "• answer, reason and calculate (a Python sandbox, charts and files);\n"
        "• search the web and read links;\n"
        "• understand photos, PDFs and documents, transcribe voice and video notes;\n"
        '• set reminders and scheduled tasks ("in an hour check the rate and message me");\n'
        "• work through Telegram checklists.\n\n"
        "In private chats every topic is a separate conversation, /new starts a new one. Replying to one of "
        "my older messages continues from there. Press Stop while I'm answering to stop me.\n"
        "In groups, mention me or reply to my message.\n\n"
        "/settings — model, depth of thinking and tools, /usage — spending.",
    },
    "not_allowed": {
        "ru": "Эта команда доступна только тем, кто управляет ботом.",
        "en": "Only the people who run this bot can use this command.",
    },
    "new.topic_name": {"ru": "Новый чат", "en": "New chat"},
    "new.topic_ready": {
        "ru": "Новый чат готов — пишите сюда 👇",
        "en": "Your new chat is ready — write here 👇",
    },
    "new.started": {
        "ru": "🆕 Начат новый разговор, прежний контекст больше не учитывается.",
        "en": "🆕 New conversation started; the earlier context no longer counts.",
    },
    "command.new": {"ru": "Новый разговор", "en": "New conversation"},
    "command.settings": {
        "ru": "Модель, глубина размышлений и инструменты",
        "en": "Model, depth of thinking and tools",
    },
    "command.settings_group": {"ru": "Твои настройки агента", "en": "Your agent settings"},
    "command.usage": {"ru": "Расходы на API", "en": "API spending"},
    "command.usage_group": {"ru": "Расходы этого чата", "en": "This chat's spending"},
    "command.help": {"ru": "Что я умею", "en": "What I can do"},
    # Groups
    "group.hello": {
        "ru": "Привет! Я ИИ-агент. Упомяните меня (@{username}) или ответьте на моё сообщение — "
        "отвечу, поищу в интернете, посчитаю, расшифрую голосовое или поставлю напоминание.\n"
        "Подсказка: ответьте на чужое сообщение с упоминанием, например «@{username} это правда?», — "
        "я учту, о чём речь.",
        "en": "Hi! I'm an AI agent. Mention me (@{username}) or reply to my message and I'll answer, search "
        "the web, calculate, transcribe a voice message or set a reminder.\n"
        "Tip: reply to someone's message with a mention, e.g. \"@{username} is this true?\", and I'll take "
        "it into account.",
    },
    "group.privacy": {
        "ru": "\n\n⚠️ У меня включён privacy mode: я вижу только обращения ко мне, поэтому не смогу "
        "пересказать обсуждение. Владелец может выключить его в @BotFather и добавить меня заново.",
        "en": "\n\n⚠️ My privacy mode is on: I only see messages addressed to me, so I can't sum up the "
        "discussion. The owner can turn it off in @BotFather and add me again.",
    },
    # Usage report
    "usage.title": {"ru": "## Расходы", "en": "## Spending"},
    "usage.title_chat": {"ru": "## Расходы этого чата", "en": "## This chat's spending"},
    "usage.header": {
        "ru": "| Период | Запросы | Токены вход / выход | Из кэша | Поиски | Голос | Стоимость |",
        "en": "| Period | Requests | Tokens in / out | Cached | Searches | Voice | Cost |",
    },
    "usage.today": {"ru": "Сегодня", "en": "Today"},
    "usage.week": {"ru": "7 дней", "en": "7 days"},
    "usage.month": {"ru": "30 дней", "en": "30 days"},
    "usage.minutes": {"ru": "{minutes} мин", "en": "{minutes} min"},
    "usage.top": {"ru": "**Топ за 30 дней:**", "en": "**Top over 30 days:**"},
    "usage.system": {"ru": "система", "en": "system"},
    # Reminders and access
    "reminder": {"ru": "⏰ **Напоминание**", "en": "⏰ **Reminder**"},
    "access_denied": {"ru": "Доступ к боту закрыт.", "en": "This bot is private."},
    # Secretary mode
    "secretary.connected": {
        "ru": "Секретарь подключён. Он отвечает от вашего имени в чатах, выбранных в настройках Telegram "
        "для бизнеса. Что он пишет, задаёт prompts/secretary.md, когда отвечает — prompts/secretary.toml.",
        "en": "Secretary connected. It replies on your behalf in the chats chosen in Telegram Business "
        "settings. prompts/secretary.md sets what it writes, prompts/secretary.toml when it replies.",
    },
    "secretary.no_reply": {
        "ru": "Секретарь подключён без права отвечать на сообщения, поэтому молчит. Разрешите ответы: "
        "Настройки → Telegram для бизнеса → Чат-боты.",
        "en": "The secretary is connected without permission to reply to messages, so it stays silent. "
        "Allow replies in Settings → Telegram Business → Chatbots.",
    },
    "secretary.off": {
        "ru": "Бот подключён, но режим секретаря выключен в prompts/secretary.toml (enabled = false).",
        "en": "The bot is connected, but secretary mode is off in prompts/secretary.toml (enabled = false).",
    },
    "secretary.disconnected": {"ru": "Секретарь отключён.", "en": "Secretary disconnected."},
}


def t(lang: Lang, key: str, **params: object) -> str:
    text = TEXTS[key][lang]
    return text.format(**params) if params else text
