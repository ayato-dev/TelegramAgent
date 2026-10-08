# tgagent — ИИ-агент для Telegram

Закрытый Telegram-агент на **Claude Haiku 5.5**. Работает в личке и группах (как Grok в X), использует
нативные AI-функции Bot API 10.3: стриминг с кнопкой «Стоп», Rich Messages, топики в личке, гостевой режим,
ephemeral-сообщения.

## Возможности

- **Личка.** Каждый топик — отдельный разговор (топик без имени бот назовёт сам), `/new` — новый разговор.
  Ответ на старое сообщение бота продолжает разговор с этого места (ветка). Ответ стримится черновиком
  с блоком «размышлений» и кнопкой «Стоп».
- **Группы.** Бот видит переписку, но отвечает только на @упоминание или ответ на своё сообщение.
  `@bot это правда?` в ответ на чужое сообщение, фото или голосовое — бот берёт его и цепочку ответов
  в контекст. Каждый, кто отвечает боту, продолжает свою ветку. По запросу пересказывает обсуждение.
- **Гостевой режим.** Пользователь из белого списка зовёт бота в любом чате, даже где бота нет.
- **Инструменты агента.** Веб-поиск и чтение ссылок, Python-песочница (расчёты, графики, файлы в чат),
  фото/PDF/документы, напоминания и отложенные задания, опросы, выполнение чек-листов Telegram
  с ответом на каждую задачу.
- **Голос.** Голосовые, кружочки, аудио и видео расшифровывает Groq `whisper-large-v3` (с кэшем).
- **Контекст.** История сжимается на стороне API, как только запрос превышает 100K токенов
  (`COMPACTION_TRIGGER_TOKENS`), — так Haiku 5.5 остаётся на дешёвом тарифе ($0.10 / $0.50 за 1M).
  Prompt caching включён.
- **`/settings`** — глубина размышлений, показ размышлений, веб-поиск, код (в группах — ephemeral-меню).
  **`/usage`** — расходы в $ по периодам и людям.
- **Доступ.** Только Telegram ID из `ALLOWED_USER_IDS`. Группа разрешена, только если бота добавил
  человек из списка, иначе бот сразу выходит. Посторонним бот не отвечает.

## Архитектура

```
Telegram ──long polling──▶ AccessMiddleware ─▶ handlers (private / groups / guest / commands)
                                                   │
                                                   ▼
                       TurnService: ветка разговора → ContentBuilder → AgentRunner
                                                   │                       │
                                       PostgreSQL ◀┘        Claude Messages API (стрим, compaction,
                       (дерево реплик, лог чатов,            серверные + клиентские инструменты)
                        напоминания, расходы)                              │
                                                   ResponseSink ◀──────────┘
                                   (DraftSink / EditSink / GuestSink / PlainSink)
```

| Пакет | Ответственность |
|---|---|
| `agent/` | Замороженный system prompt, инструменты, агентный цикл со стримингом, цены |
| `context/` | Нормализация сообщений Telegram, медиа (Files API, Whisper), сборка user-реплик, история из дерева |
| `services/` | Оркестрация хода, Stop, напоминания, обработчики инструментов, названия топиков, отчёт расходов |
| `telegram/` | Хендлеры, контроль доступа, рендеринг Markdown → Rich Messages (с запасным путём), стримеры |
| `storage/` | SQLAlchemy-модели, репозитории, миграции Alembic |

Разговор хранится деревом узлов: контекст запроса — путь от узла к корню (или к последней точке сжатия).
История только дописывается, а system prompt и инструменты не зависят от запроса — это требование
thinking-блоков Haiku 5.5 и условие хорошего кэширования.

## Настройка бота в @BotFather

1. Создайте бота, сохраните токен.
2. **Bot Settings → Group Privacy → Turn off** — иначе бот не видит переписку в группах
   (после изменения бота нужно добавить в группу заново).
3. В Mini App настроек BotFather включите **Threaded Mode / топики в личных чатах** и **Guest Mode**.

## Локальный запуск

```bash
cp .env.example .env   # заполните токены, ALLOWED_USER_IDS и POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f bot
```

Миграции применяются при старте контейнера бота.

## Разработка

```bash
uv sync
uv run ruff check src tests migrations && uv run ruff format src tests migrations
uv run mypy
docker run -d --name tgagent-testdb -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test -p 55432:5432 postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:55432/test uv run pytest
```

Без `TEST_DATABASE_URL` тесты, которым нужна база, пропускаются. Новая миграция:
`DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`.

## CI/CD

`.github/workflows/ci.yml`: **lint** (ruff, mypy) и **test** (pytest на PostgreSQL 18 + `alembic check`)
на каждый PR и push → **build** (образ в GHCR, кэш buildx) → **deploy** при push в `main`.

Деплой без SSH: на сервере работает **self-hosted runner** GitHub Actions. Он сам забирает задания
по исходящему соединению, входящие порты не нужны. Job кладёт `compose.yaml` и `.env` в `DEPLOY_DIR`,
делает `docker compose pull` и `up --wait`; при неудаче печатает логи.

### Однократная подготовка сервера

1. Установите Docker Engine с плагином compose.
2. Создайте пользователя для runner и каталог деплоя:
   ```bash
   sudo useradd --system --create-home gha-runner
   sudo usermod -aG docker gha-runner
   sudo install -d -o gha-runner -g gha-runner -m 750 /opt/tgagent
   ```
   Членство в группе `docker` равносильно root-доступу, поэтому держите сервер выделенным под бота.
3. **Settings → Actions → Runners → New self-hosted runner** (Linux). Скачайте runner по инструкции
   под пользователем `gha-runner` и зарегистрируйте с меткой `tgagent`:
   ```bash
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels tgagent --unattended
   sudo ./svc.sh install gha-runner && sudo ./svc.sh start
   ```

### Настройки GitHub

- Репозиторий должен быть **приватным**: self-hosted runner в публичном репозитории небезопасен.
- **Settings → Environments → `production`**: ограничьте деплой веткой `main`, по желанию добавьте ревьюеров.
- **Secrets** окружения: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`,
  `POSTGRES_PASSWORD` (только буквы, цифры, `-`, `_`).
- **Variables**: `ALLOWED_USER_IDS` (через запятую). Необязательные: `TIMEZONE`, `DEFAULT_EFFORT`,
  `COMPACTION_TRIGGER_TOKENS`, `WHISPER_MODEL`, `DEPLOY_DIR` (по умолчанию `/opt/tgagent`).

## Эксплуатация

```bash
cd /opt/tgagent
docker compose ps
docker compose logs -f --tail 200 bot
docker compose exec postgres pg_dump -U tgagent tgagent > backup.sql
```

Данные PostgreSQL лежат в volume `tgagent_pgdata` и переживают деплои.
