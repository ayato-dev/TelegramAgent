# tgagent — свой ИИ-агент в Telegram

[English version](README.md)

Это не очередной бот «спроси нейросеть». Агент живёт в Telegram и реально делает дела: сам ищет в
интернете, гоняет Python, читает фотки, PDF и голосовые, ставит напоминания, делает опросы, закрывает
чек-листы. В личке у него отдельные разговоры по топикам (как чаты в ChatGPT), в группах он ведёт себя
как Grok в X: молчит, пока не позовёшь.

Мозги подключаешь какие хочешь: **Claude, OpenAI, Gemini, DeepSeek или Groq** (там бесплатный
gpt-oss-120b). Хватит одного ключа, можно все пять, а модель каждый переключает прямо в `/settings`.

Бот закрытый: пользуются только те, чьи Telegram ID ты вписал. Говорит по-русски или по-английски —
смотря какой язык у человека в Telegram.

## Быстрый старт

Нужен сервер или комп с Docker (или Linux, куда скрипт сам поставит Docker), и:

1. Токен бота от [@BotFather](https://t.me/BotFather) (`/newbot`).
2. Твой Telegram ID — подскажет [@userinfobot](https://t.me/userinfobot).
3. Хотя бы один ключ нейросети. Бесплатный вариант — [Groq](https://console.groq.com/keys).

И дальше одна команда:

```bash
curl -fsSL https://raw.githubusercontent.com/ayato-dev/TelegramAgent/main/easy-install.sh | bash
```

Скрипт задаст пару вопросов, запишет `~/tgagent/.env` и запустит бота из готового образа (amd64 и
arm64). Данные лежат в файле SQLite на Docker-томе, отдельная база не нужна.

Потом донастрой бота в @BotFather (Bot Settings):

- **Group Privacy → Turn off**, иначе бот не видит переписку в группах (если бот уже был в группе —
  удали и добавь заново).
- В мини-приложении настроек BotFather включи **Threaded Mode** (топики в личке) и **Guest Mode**.

## Что умеет

- **Личка.** Каждый топик — свой разговор, топик без названия бот назовёт сам. `/new` — начать заново.
  Ответил на старое сообщение бота — разговор продолжится с того места. Ответ печатается на лету,
  с ходом мыслей и кнопкой «Стоп».
- **Группы.** Бот видит переписку, но отвечает только на @упоминание или реплай на своё сообщение.
  Кинул `@bot это правда?` в ответ на чужое сообщение, фото или голосовое — бот возьмёт его и всю
  цепочку ответов в контекст. Каждый, кто отвечает боту, ведёт свою ветку. Может пересказать, о чём тут
  спорили. Переслал пост с комментарием — бот поймёт их как один запрос. Пока бот думает, висит
  «печатает…», потом приходит один готовый ответ.
- **Гостевой режим.** Зовёшь бота в любом чате, даже где его нет, и он отвечает один раз.
- **Инструменты.** Поиск и чтение ссылок, Python (расчёты, графики, файлы прилетают в чат),
  напоминания и отложенные задания («через час проверь курс и напиши»), опросы, чек-листы Telegram
  с отчётом по каждой задаче. У OpenAI ещё и рисование картинок.
- **Посты.** Переслал новость без вопроса — бот разберёт её как Grok: о чём это, что пост недоговаривает
  (кто эти люди, что было до этого), и в конце короткий фактчек. Если вопрос есть — отвечает ровно на него.
- **Голос.** Голосовые, кружочки, аудио и видео расшифровывает Groq Whisper (на бесплатном тарифе
  бесплатно). Gemini слушает и смотрит их сам, а ещё понимает ссылки на YouTube.
- **Длинные разговоры.** Когда контекст разрастается, история сжимается в выжимку, и разговор идёт
  дальше. У Claude это делает сам API, у остальных — бот.
- **`/settings`** — модель, глубина размышлений, показ мыслей, поиск, код, стиль (обычный или дерзкий).
  Настройки у каждого свои и действуют везде: в личке, в группах и в гостевом режиме.
  **`/usage`** — сколько потрачено в $ по дням и людям.
- **Доступ.** Только ID из `ALLOWED_USER_IDS`. Группа работает, только если бота добавил кто-то из
  списка, иначе бот сразу выходит.

## Модели: что умеют и сколько стоят

Цены за 1M токенов, вход / выход, проверены 8 октября 2026. Ключ модели — то, что пишешь в
`DEFAULT_MODEL`.

| Модель | Ключ | 👁 фото | 🎙 голос сама | 🌐 поиск | 🐍 код | 🎨 рисует | Цена |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| Claude Haiku 5.5 | `anthropic:claude-haiku-5-5` | ✅ | | ✅ | ✅ | | $0.10 / $0.50 (до 100K контекста) |
| Claude Sonnet 5.5 | `anthropic:claude-sonnet-5-5` | ✅ | | ✅ | ✅ | | $2 / $10 |
| Claude Opus 5.5 | `anthropic:claude-opus-5-5` | ✅ | | ✅ | ✅ | | $4 / $20 |
| GPT-6 Luna | `openai:gpt-6-luna` | ✅ | | ✅ | ✅ | ✅ | $0.10 / $0.50 |
| GPT-6.1 Sol | `openai:gpt-6.1-sol` | ✅ | | ✅ | ✅ | ✅ | $2 / $10 |
| GPT-6 Astra | `openai:gpt-6-astra` | ✅ | | ✅ | ✅ | ✅ | $10 / $50 |
| Gemini 3.1 Flash-Lite | `gemini:gemini-3.1-flash-lite` | ✅ | ✅ | платно | ✅ | | бесплатный тариф / $0.25 / $1.50 |
| Gemini 3.8 Flash | `gemini:gemini-3.8-flash` | ✅ | ✅ | платно | ✅ | | бесплатный тариф / $0.75 / $3.75 |
| Gemini 3.1 Pro | `gemini:gemini-3.1-pro-preview` | ✅ | ✅ | платно | ✅ | | $2 / $12 (до 200K) |
| DeepSeek Flash | `deepseek:deepseek-flash` | ✅ | | | | | $0.30 / $1.20, вне пика $0.15 / $0.60 |
| DeepSeek V4 Pro | `deepseek:deepseek-v4-pro` | | | | | | $1.32 / $3.96, вне пика вдвое дешевле |
| gpt-oss-120b (Groq) | `groq:openai/gpt-oss-120b` | | | ✅ | ✅ | | бесплатно с лимитами / $0.15 / $0.60 |
| gpt-oss-20b (Groq) | `groq:openai/gpt-oss-20b` | | | ✅ | ✅ | | бесплатно с лимитами / $0.075 / $0.30 |

Что важно знать:

- **Голос.** Модели без 🎙 получают голосовые уже текстом через Whisper, нужен ключ Groq. Без него бот
  скажет модели, что голосовое расшифровать нечем.
- **Gemini без денег** работает, но без Google-поиска (он только на платном тарифе), лимиты маленькие,
  а Google учится на твоих данных. Ссылки Gemini читает и бесплатно. Платный тариф включается
  `GEMINI_PAID_TIER=true`.
- **Groq бесплатно** — это 30 запросов в минуту, 1000 в день, 8K токенов в минуту и 200K в день. Бот под
  это подстраивается: сжимает разговор уже на 5K токенов и пишет ответы покороче. Один веб-поиск с
  чтением страниц может съесть до 40K токенов, то есть всего несколько поисков в день. На платном тарифе
  ставь `GROQ_PAID_TIER=true`.
- **DeepSeek** вдвое дешевле вне пика. Пик — будни 01:00–04:00 и 06:00–10:00 UTC (04:00–07:00 и
  09:00–13:00 по Москве). Поиска и кода у DeepSeek нет, фото видит только Flash.
- **OpenAI** рисует картинки (gpt-image-2, примерно $0.04 за штуку) и запускает код в контейнере ($0.03
  за сессию до 20 минут). Поиск у OpenAI и Claude — $0.01 за запрос плюс токены.
- **Длинный контекст дороже.** Haiku 5.5 после 100K токенов стоит в 5 раз дороже, OpenAI после 272K —
  вдвое за вход, Gemini Pro после 200K — тоже. Поэтому сжатие стоит на 100K.
- OpenAI, Anthropic, Google и Groq работают не во всех странах (Россия, например, заблокирована). Если
  сервер стоит не там, запросы падают с ошибкой региона — бот так и напишет.

## Как сэкономить

Готовые варианты:

- **Совсем бесплатно.** Только `GROQ_API_KEY`: gpt-oss-120b с поиском и Python плюс бесплатный Whisper,
  хостинг на Oracle Always Free или GCP e2-micro (ниже). Для личного бота на пару человек хватит, но на
  лимиты будешь натыкаться.
- **Бесплатно и с голосом/YouTube.** Ключи Gemini и Groq, `DEFAULT_MODEL=gemini:gemini-3.1-flash-lite`.
  Gemini сам слушает голосовые и смотрит ролики, а gpt-oss выручит, когда понадобится поиск.
- **Всё и почти даром.** Claude Haiku 5.5 или GPT-6 Luna по $0.10 / $0.50. У Haiku лучше агентская часть
  и сжатие на стороне API, у Luna есть рисование. Плюс ключ Groq ради бесплатного Whisper. Обычный день
  активного общения выходит в центы.
- **Дёшево и умно ночью.** DeepSeek Flash вне пика: $0.15 / $0.60, но без поиска.

Ещё советы:

- Ставь «⚡ Быстро» в `/settings`, если не нужны сложные рассуждения: модель меньше думает, а мысли
  оплачиваются как обычный ответ.
- Выключай поиск, если он не нужен: каждый поиск — это деньги и пачка токенов со страниц.
- Не задирай `COMPACTION_TRIGGER_TOKENS`. Чем длиннее контекст, тем дороже каждое сообщение, а у части
  моделей после порога дорожает вообще всё.
- Новая тема — новый топик или `/new`: старый контекст не тащится в каждый запрос.
- Повторяющееся начало запроса (промпт, старая история) кэшируется само, и читать его из кэша дешевле:
  у DeepSeek в 50 раз, у Claude и OpenAI в 10, у Groq вдвое.
- Следи за `/usage`: там видно, кто и на чём тратит.

## Где хостить

Боту нужно совсем немного: около 300 МБ памяти. По умолчанию он сам опрашивает Telegram (polling),
поэтому публичный адрес не нужен — подойдёт хоть твой комп или Raspberry Pi. Главное, чтобы не в
стране, которую блокируют провайдеры нейросетей.

| Вариант | Цена | Что важно |
|---|---|---|
| Oracle Cloud Always Free (ARM) | бесплатно | 2 ядра + 12 ГБ на arm64, при регистрации нужна карта, в популярных регионах часто «out of capacity». Простаивающие ВМ могут отобрать; перевод на pay-as-you-go (всё ещё $0), говорят, это снимает. |
| Google Cloud e2-micro | бесплатно | 1 ГБ памяти в us-west1 / us-central1 / us-east1. Боту с SQLite хватает. |
| Любой маленький VPS | €4–6 в месяц | Hetzner, OVH, Contabo и им подобные. Самый беспроблемный вариант. |
| Свой комп или Raspberry Pi | бесплатно | Работает, пока включён. |
| Google Cloud Run (webhook) | бесплатный лимит | Засыпает без запросов; нужен webhook-режим и внешняя база (ниже). |
| Render / Railway / Fly (webhook) | бесплатно–$5 | Бесплатные сервисы засыпают и теряют диск, так что вместе с ними — Neon Postgres. |

Groq стоит за Cloudflare, и некоторые IP дата-центров получают 403 — если так, попробуй другой хостинг.

## Другие способы установки

**Вручную, с SQLite (один контейнер):**

```bash
git clone https://github.com/ayato-dev/TelegramAgent.git && cd TelegramAgent
cp .env.example .env          # токен, ALLOWED_USER_IDS и хотя бы один ключ
docker compose -f compose.sqlite.yaml up -d
docker compose -f compose.sqlite.yaml logs -f
```

**С PostgreSQL** (`compose.yaml` поднимает Postgres 18 рядом с ботом):

```bash
cp .env.example .env          # плюс POSTGRES_PASSWORD (только буквы, цифры, - и _)
docker compose up -d --build
```

Миграции применяются сами при старте. Строка `models: ...` в логе показывает, какие модели бот видит.

**Без Docker:** Python 3.13 и [uv](https://docs.astral.sh/uv/): `uv sync`, потом
`uv run alembic upgrade head && uv run python -m tgagent`.

### Webhook и serverless

По умолчанию бот сам опрашивает Telegram, публичный адрес не нужен. На платформах, которые дают
HTTPS-адрес и усыпляют сервис без запросов, включи webhook-режим:

- `WEBHOOK_URL=https://твой-адрес` — Telegram будет слать обновления на `<url>/webhook`.
- `PORT` — порт (по умолчанию 8080, платформы обычно задают его сами).
- `WEBHOOK_SECRET` — по желанию; если не задан, выводится из токена бота. Задай явно, если будешь
  дёргать `/cron`.
- `WEBHOOK_INLINE=true` — обрабатывать каждое обновление внутри HTTP-запроса. Нужно там, где CPU
  отключается после ответа (Cloud Run с оплатой за запросы).

Сервер ещё отвечает на `GET /health` и `/cron`. Дёргай `/cron` регулярно (раз в 1–5 минут) с заголовком
`X-Cron-Secret: <WEBHOOK_SECRET>` или `?secret=<WEBHOOK_SECRET>`: он отправляет наступившие напоминания и
чистит старый лог групп на платформах, где между запросами ничего не работает.

У serverless-контейнеров диск временный, поэтому там нужен Postgres. У [Neon](https://neon.tech) есть
бесплатный тариф: `DATABASE_URL=postgresql+asyncpg://user:password@host/dbname?ssl=require`. Neon засыпает
через 5 минут простоя, а проверка напоминаний будила бы его, так что ставь `REMINDER_POLL_SECONDS=600`.

**Google Cloud Run коротко** (в Cloud Shell `gcloud` и Docker уже есть). Cloud Run не тянет образы с
ghcr.io, поэтому сначала копируем образ в Artifact Registry:

```bash
gcloud artifacts repositories create tgagent --repository-format=docker --location=us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
docker pull ghcr.io/ayato-dev/telegramagent:latest
docker tag ghcr.io/ayato-dev/telegramagent:latest us-central1-docker.pkg.dev/PROJECT/tgagent/bot
docker push us-central1-docker.pkg.dev/PROJECT/tgagent/bot
gcloud run deploy tgagent --image us-central1-docker.pkg.dev/PROJECT/tgagent/bot \
  --region us-central1 --allow-unauthenticated --max-instances 1 --timeout 600 \
  --set-env-vars WEBHOOK_INLINE=true,REMINDER_POLL_SECONDS=600,WEBHOOK_URL=https://SERVICE-URL \
  --set-env-vars TELEGRAM_BOT_TOKEN=...,ALLOWED_USER_IDS=...,GROQ_API_KEY=...,DATABASE_URL=...,WEBHOOK_SECRET=...
gcloud scheduler jobs create http tgagent-cron --location us-central1 --schedule "*/5 * * * *" \
  --uri https://SERVICE-URL/cron --http-method POST --headers X-Cron-Secret=ТВОЙ_WEBHOOK_SECRET
```

Адрес сервиса Cloud Run покажет после первого деплоя — впиши его в `WEBHOOK_URL` и задеплой ещё раз.
Ключи лучше держать в Secret Manager (`--set-secrets`), а не в открытых переменных.

## Что значат настройки у разных провайдеров

| Настройка | Claude | OpenAI | Gemini | DeepSeek | Groq gpt-oss |
|---|---|---|---|---|---|
| ⚡/⚖️/🧠 глубина | effort low / medium / high | reasoning low / medium / high | thinking low / по умолчанию / high | low / high / max | low / medium / high |
| 💭 размышления | краткие мысли Claude | сводка рассуждений | сводка мыслей | сырые рассуждения | сырые рассуждения |
| 🌐 поиск | web search + чтение страниц | web search | Google Search (платно) + чтение ссылок | нет | browser search |
| 🐍 код | песочница, файлы в чат | code interpreter, файлы в чат | code execution, графики в чат | нет | Python, графики в чат |
| сжатие | на стороне API | ботом | ботом | ботом | ботом (на бесплатном — с 5K) |

Кнопка «🤖 Модель» в `/settings` открывает список всех моделей с ключами, значки показывают, кто что
умеет. В личке смена модели начинает новый разговор, а реплаи на старые сообщения продолжаются в той
модели, с которой разговор начинался. Можно указать и модель не из таблицы (`openai:gpt-7-preview`) — она
получит типовые возможности своего провайдера, а в `/usage` её цена будет $0.

Документы, которые модель не умеет читать сама, бот превращает в текст: из PDF достаёт текст (подписанные
ЭЦП тоже), текстовые файлы вставляет как есть. Таблицы и прочие бинарные файлы в песочницу пока умеет
отдавать только Claude.

## Все настройки

| Переменная | Зачем |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `ALLOWED_USER_IDS` | Telegram ID через запятую, кому можно |
| `ACCESS_DENIED_TEXT` | ответ чужим: не задано — короткий отказ на их языке, пусто — молчать |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY` | ключи провайдеров, хватит одного |
| `DEFAULT_MODEL` | `провайдер:модель`; пусто — самая дешёвая модель первого провайдера с ключом |
| `GROQ_PAID_TIER`, `GEMINI_PAID_TIER` | `true` на платных тарифах |
| `COMPACTION_TRIGGER_TOKENS` | с какого размера контекста сжимать (минимум 50000, по умолчанию 100000) |
| `MAX_OUTPUT_TOKENS` | потолок длины ответа (16000) |
| `DEFAULT_EFFORT` | глубина по умолчанию: low / medium / high |
| `WEB_SEARCH_MAX_USES` | сколько поисков Claude может сделать за ответ (5) |
| `WHISPER_MODEL` | `whisper-large-v3` или подешевле `whisper-large-v3-turbo` |
| `DATABASE_URL` | по умолчанию `sqlite+aiosqlite:///data/tgagent.db`; Postgres: `postgresql+asyncpg://...` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | база в `compose.yaml` |
| `WEBHOOK_URL`, `WEBHOOK_SECRET`, `PORT`, `WEBHOOK_INLINE` | webhook-режим, см. выше |
| `REMINDER_POLL_SECONDS` | как часто проверять напоминания между пробуждениями (60) |
| `TIMEZONE` | часовой пояс для напоминаний (Europe/Moscow) |
| `CHAT_LOG_RETENTION_DAYS` | сколько дней хранить лог групп (30) |
| `LOG_LEVEL` | INFO / DEBUG |

## Как устроено

```
Telegram ──polling или webhook──▶ AccessMiddleware ─▶ хендлеры (личка / группы / гость / команды)
                                                         │
                                                         ▼
             TurnService: ветка разговора → сжатие → раннер модели разговора
                                │                          │
           SQLite или Postgres ◀┘      Claude / OpenAI / Gemini / DeepSeek / Groq
       (дерево реплик, лог групп,          (стрим, инструменты, медиа)
        напоминания, расходы)                         │
                                       ResponseSink ◀─┘
                  (черновик в личке, «печатает» + один ответ в группах, гость, напоминания)
```

| Пакет | Что внутри |
|---|---|
| `agent/` | каталог моделей и цены, промпт, инструменты, раннеры провайдеров (`providers/`), ошибки |
| `context/` | сообщения Telegram → текст, медиа под каждого провайдера, PDF, дерево истории |
| `services/` | ход разговора, сжатие, «Стоп», напоминания, инструменты, названия топиков, расходы |
| `telegram/` | хендлеры, доступ, Markdown → Rich Messages, стриминг, webhook-сервер |
| `storage/` | модели SQLAlchemy, репозитории, миграции Alembic |
| `i18n.py` | тексты интерфейса на русском и английском |

Разговор — это дерево: контекст запроса — путь от сообщения до корня (или до последней выжимки). Каждый
разговор привязан к модели, на которой начался, а история хранится в родном формате этой модели —
поэтому мысли, подписи и вызовы инструментов возвращаются провайдеру ровно такими, какими он их прислал.

## Разработка

```bash
uv sync
uv run ruff check src tests migrations && uv run ruff format src tests migrations
uv run mypy
uv run pytest                 # тесты с базой идут на временном файле SQLite
docker run -d --name tgagent-testdb -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test -p 55432:5432 postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:55432/test uv run pytest
```

Раннеры провайдеров проверяются и на фейках, и на настоящих SDK через подменённый HTTP. Новая миграция:
`DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`.

## CI/CD

`.github/workflows/ci.yml`: **lint** (ruff, mypy) и **tests** на PostgreSQL и SQLite на каждый PR и push →
**build** (мультиархитектурный образ в GHCR) → **deploy** при push в `main`, если включишь.

Деплой без SSH: на сервере крутится **self-hosted runner** GitHub Actions, он сам ходит за заданиями,
входящие порты не нужны. Job кладёт `compose.yaml` и `.env` в `DEPLOY_DIR`, делает `docker compose pull`
и `up --wait`, а если что-то упало — печатает логи.

Подготовить сервер (один раз):

1. Поставь Docker Engine с плагином compose.
2. Заведи пользователя для runner и папку деплоя:
   ```bash
   sudo useradd --system --create-home gha-runner
   sudo usermod -aG docker gha-runner
   sudo install -d -o gha-runner -g gha-runner -m 750 /opt/tgagent
   ```
   Группа `docker` — это фактически root, так что сервер лучше держать только под бота.
3. **Settings → Actions → Runners → New self-hosted runner** (Linux). Скачай runner по инструкции под
   `gha-runner` и зарегистрируй с меткой `tgagent`:
   ```bash
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels tgagent --unattended
   sudo ./svc.sh install gha-runner && sudo ./svc.sh start
   ```

Настройки GitHub:

- Репозиторий **публичный**? Settings → Actions → General → «Fork pull request workflows» →
  «Require approval for all outside collaborators». Иначе чужой PR может поменять workflow и запустить код
  на твоём runner. Сам деплой запускается только на push в `main`.
- **Settings → Environments → `production`**: разреши деплой только из `main`, при желании добавь ревьюеров.
- **Secrets**: `TELEGRAM_BOT_TOKEN`, `POSTGRES_PASSWORD` и ключи нужных провайдеров.
- **Variables**: `DEPLOY_ENABLED=true` (без неё деплой пропускается), `ALLOWED_USER_IDS`. По желанию:
  `DEFAULT_MODEL`, `GROQ_PAID_TIER`, `GEMINI_PAID_TIER`, `ACCESS_DENIED_TEXT`, `TIMEZONE`, `DEFAULT_EFFORT`,
  `COMPACTION_TRIGGER_TOKENS`, `WHISPER_MODEL`, `DEPLOY_DIR` (по умолчанию `/opt/tgagent`).

## Обслуживание

```bash
docker compose ps
docker compose logs -f --tail 200 bot
docker compose pull && docker compose up -d             # обновить
docker compose exec postgres pg_dump -U tgagent tgagent > backup.sql   # бэкап Postgres
```

С SQLite бэкапь Docker-том `tgagent_botdata` (в нём файл `tgagent.db`).

## Лицензия

[MIT](LICENSE)
