# tgagent

[Русская версия](README.ru.md)

A self-hosted AI agent for Telegram. It answers in private chats, groups and guest mode, searches the web,
runs Python, reads photos, PDFs and voice messages, sets reminders, creates polls and works through
Telegram checklists.

Supported providers: **Anthropic (Claude), OpenAI, Google Gemini, DeepSeek and Groq**. One API key is
enough; with several keys each user can choose a model in `/settings`.

Access is limited to the Telegram user IDs listed in the configuration. The interface is available in
Russian and English and follows the language of the user's Telegram app.

## Quick start

Requirements: a Linux or macOS machine with Docker (the installer can install Docker on Linux), and

1. a bot token from [@BotFather](https://t.me/BotFather) (`/newbot`);
2. your Telegram user ID, available from [@userinfobot](https://t.me/userinfobot);
3. at least one provider API key. [Groq](https://console.groq.com/keys) offers a free plan.

```bash
curl -fsSL https://raw.githubusercontent.com/ayato-dev/TelegramAgent/main/easy-install.sh | bash
```

The script asks for the values above, writes `~/tgagent/.env` and starts the published image (amd64 and
arm64). Data is stored in a SQLite file on a Docker volume.

Then configure the bot in @BotFather → Bot Settings:

- **Group Privacy → Turn off**, so the bot can read group messages. If the bot is already in a group,
  remove it and add it again after the change.
- In the BotFather settings mini app, enable **Threaded Mode** (topics in private chats) and
  **Guest Mode**.

## Features

- **Private chats.** Each topic is a separate conversation; unnamed topics are titled automatically.
  `/new` starts a new conversation. Replying to an earlier bot message continues the conversation from
  that point. Answers are streamed as drafts with a Stop button and optional reasoning.
- **Groups.** The bot reads the chat and answers only when mentioned or replied to. A mention in reply to
  another message, photo or voice note includes that message and its reply chain in the context. Each
  person replying to the bot continues their own branch. The bot can summarise the recent discussion.
  While it works, the chat shows the typing status; the answer is posted as one message.
- **Guest mode.** The bot can be called in any chat, including chats it is not a member of, and answers
  once.
- **Secretary mode.** Connected to your account through Telegram Business, the bot answers people in your
  private chats on your behalf, by rules set in a config file. See [Secretary mode](#secretary-mode).
- **Grouped messages.** Messages sent by one person in quick succession (a comment and the posts
  forwarded with it, an album) are handled as one request.
- **Forwarded posts.** A post sent without a question is explained: what it is about, the background it
  omits, and a short fact-check. A post sent with a question gets an answer to that question.
- **Tools.** Web search and page reading, Python execution (results and files are sent to the chat),
  reminders and scheduled tasks, polls, Telegram checklists. OpenAI models can generate images.
- **Voice and video.** Voice messages, video notes, audio and video are transcribed with Groq Whisper.
  Gemini models receive the audio and video directly and also process YouTube links.
- **Long conversations.** When the context exceeds the configured size, the history is summarised:
  by the API for Claude, by the bot for other providers.
- **`/settings`** — model, reasoning depth, showing reasoning, web search, code execution, response style.
  Settings are stored per user and apply in private chats, groups and guest mode.
- **`/usage`** — API spending by period and by user.
- **Access control.** Only users listed in `ALLOWED_USER_IDS` can use the bot. A group is allowed only if
  one of these users added the bot; otherwise the bot leaves.

## Models

Prices per 1M tokens (input / output), checked on 2026-10-08. The key column is the value for
`DEFAULT_MODEL`.

| Model | Key | Images | Native audio | Web search | Code | Image generation | Price |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| Claude Haiku 5.5 | `anthropic:claude-haiku-5-5` | ✅ | | ✅ | ✅ | | $0.10 / $0.50 (up to 100K context) |
| Claude Sonnet 5.5 | `anthropic:claude-sonnet-5-5` | ✅ | | ✅ | ✅ | | $2 / $10 |
| Claude Opus 5.5 | `anthropic:claude-opus-5-5` | ✅ | | ✅ | ✅ | | $4 / $20 |
| GPT-6 Luna | `openai:gpt-6-luna` | ✅ | | ✅ | ✅ | ✅ | $0.10 / $0.50 |
| GPT-6.1 Sol | `openai:gpt-6.1-sol` | ✅ | | ✅ | ✅ | ✅ | $2 / $10 |
| GPT-6 Astra | `openai:gpt-6-astra` | ✅ | | ✅ | ✅ | ✅ | $10 / $50 |
| Gemini 3.1 Flash-Lite | `gemini:gemini-3.1-flash-lite` | ✅ | ✅ | paid plan | ✅ | | free plan / $0.25 / $1.50 |
| Gemini 3.8 Flash | `gemini:gemini-3.8-flash` | ✅ | ✅ | paid plan | ✅ | | free plan / $0.75 / $3.75 |
| Gemini 3.1 Pro | `gemini:gemini-3.1-pro-preview` | ✅ | ✅ | paid plan | ✅ | | $2 / $12 (up to 200K) |
| DeepSeek Flash | `deepseek:deepseek-flash` | ✅ | | | | | $0.30 / $1.20; off-peak $0.15 / $0.60 |
| DeepSeek V4 Pro | `deepseek:deepseek-v4-pro` | | | | | | $1.32 / $3.96; off-peak 50% off |
| gpt-oss-120b (Groq) | `groq:openai/gpt-oss-120b` | | | ✅ | ✅ | | free plan / $0.15 / $0.60 |
| gpt-oss-20b (Groq) | `groq:openai/gpt-oss-20b` | | | ✅ | ✅ | | free plan / $0.075 / $0.30 |

Notes:

- **Voice transcription** for models without native audio requires a Groq key (Whisper). Without it the
  model is told that the voice message could not be transcribed.
- **Gemini free plan:** no Google Search grounding, low rate limits, and Google may use the data for
  training. URL reading is available. Set `GEMINI_PAID_TIER=true` on a paid plan.
- **Groq free plan:** 30 requests per minute, 1,000 per day, 8K tokens per minute, 200K tokens per day.
  On this plan the bot summarises gpt-oss conversations from 5K tokens and limits answer length. A web
  search that reads several pages can use up to 40K tokens. Set `GROQ_PAID_TIER=true` on a paid plan.
- **DeepSeek** charges 50% less outside peak hours (peak: weekdays 01:00–04:00 and 06:00–10:00 UTC).
  DeepSeek models have no built-in search or code execution; only Flash accepts images.
- **OpenAI** image generation (gpt-image-2) costs about $0.04 per image; a code interpreter container costs
  $0.03 per session of up to 20 minutes. Web search on OpenAI and Claude costs $0.01 per query plus tokens.
- **Long context pricing:** Claude Haiku 5.5 is 5× more expensive above 100K tokens, OpenAI doubles input
  pricing above 272K, Gemini Pro above 200K. The default summarisation threshold is 100K.
- **Regions:** OpenAI, Anthropic, Google and Groq are unavailable in some countries, including Russia.
  Requests from such locations fail with a region error, which the bot reports to the user.

## Reducing costs

Example configurations:

| Setup | Keys | Cost | Limitations |
|---|---|---|---|
| Free | Groq | $0 | Groq free-plan limits; no image input |
| Free with audio and YouTube | Gemini, Groq; `DEFAULT_MODEL=gemini:gemini-3.1-flash-lite` | $0 | Gemini free-plan limits; search through gpt-oss only |
| Low cost, full feature set | Anthropic (Haiku 5.5) or OpenAI (Luna), plus Groq for Whisper | cents per day of regular use | — |
| Low cost off-peak | DeepSeek | $0.15 / $0.60 off-peak | no search or code |

Settings that affect cost:

- Reasoning depth: the "Fast" level reduces reasoning tokens, which are billed as output.
- Web search: each search is billed and adds page content to the input.
- `COMPACTION_TRIGGER_TOKENS`: a higher threshold increases the cost of every request in long
  conversations and can move requests into long-context pricing.
- New topics or `/new` keep unrelated history out of requests.
- Prompt caching is used automatically. Cached input is 50× cheaper on DeepSeek, 10× on Claude and
  OpenAI, 2× on Groq.
- `/usage` shows spending by user.

## Hosting

The bot uses about 300 MB of RAM. In the default polling mode it needs outbound internet access only and
no public address. The server must be in a country the chosen providers serve.

| Option | Cost | Notes |
|---|---|---|
| Oracle Cloud Always Free (ARM) | free | 2 OCPU and 12 GB on arm64; a card is required at sign-up; capacity in popular regions is often unavailable; idle instances may be reclaimed. |
| Google Cloud e2-micro | free | 1 GB RAM in us-west1, us-central1 or us-east1; sufficient with SQLite. |
| VPS | €4–6 per month | Hetzner, OVH, Contabo and similar providers. |
| Local machine or Raspberry Pi | free | Runs while the machine is on. |
| Google Cloud Run | free tier | Requires webhook mode and an external database. |
| Render, Railway, Fly | free–$5 | Requires webhook mode; free services sleep and have no persistent disk, use Neon Postgres. |

Some datacenter IP ranges receive HTTP 403 from Groq (Cloudflare). If that happens, use another provider
or host.

## Installation

**Docker with SQLite (single container):**

```bash
git clone https://github.com/ayato-dev/TelegramAgent.git && cd TelegramAgent
cp .env.example .env          # set the token, ALLOWED_USER_IDS and at least one API key
docker compose -f compose.sqlite.yaml up -d
docker compose -f compose.sqlite.yaml logs -f
```

**Docker with PostgreSQL** (`compose.yaml` runs PostgreSQL 18 next to the bot):

```bash
cp .env.example .env          # also set POSTGRES_PASSWORD (letters, digits, "-" and "_")
docker compose up -d --build
```

Database migrations run on startup. The startup log line `models: ...` lists the available models.

**Without Docker:** Python 3.13 and [uv](https://docs.astral.sh/uv/): `uv sync`, then
`uv run alembic upgrade head && uv run python -m tgagent`.

### Webhook mode and serverless platforms

Polling is the default. For platforms that provide an HTTPS endpoint and stop idle instances, use webhook
mode:

- `WEBHOOK_URL=https://your-host` — Telegram sends updates to `<url>/webhook`.
- `PORT` — listening port (default 8080; most platforms set it).
- `WEBHOOK_SECRET` — optional; derived from the bot token when unset. Set it explicitly when using `/cron`.
- `WEBHOOK_INLINE=true` — process each update within its HTTP request. Required where CPU is throttled
  after the response is sent (Cloud Run with request-based billing).

The server also exposes `GET /health` and `/cron`. Call `/cron` every 1–5 minutes with the header
`X-Cron-Secret: <WEBHOOK_SECRET>` (or `?secret=<WEBHOOK_SECRET>`) to send due reminders and remove old
group logs on platforms where nothing runs between requests.

Serverless instances have no persistent disk; use PostgreSQL. [Neon](https://neon.tech) has a free plan:
`DATABASE_URL=postgresql+asyncpg://user:password@host/dbname?ssl=require`. Neon suspends after 5 idle
minutes; set `REMINDER_POLL_SECONDS=600` so that reminder checks do not keep it running.

**Google Cloud Run.** Cloud Run does not pull images from ghcr.io, so copy the image to Artifact Registry
first (Cloud Shell includes `gcloud` and Docker):

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
  --uri https://SERVICE-URL/cron --http-method POST --headers X-Cron-Secret=YOUR_WEBHOOK_SECRET
```

The service URL is shown after the first deployment; set it in `WEBHOOK_URL` and deploy again. Store API
keys in Secret Manager (`--set-secrets`) rather than plain environment variables.

## Settings by provider

| Setting | Claude | OpenAI | Gemini | DeepSeek | Groq gpt-oss |
|---|---|---|---|---|---|
| Reasoning depth (fast / normal / deep) | effort low / medium / high | reasoning low / medium / high | thinking low / default / high | low / high / max | low / medium / high |
| Show reasoning | thinking summary | reasoning summary | thought summary | raw reasoning | raw reasoning |
| Web search | web search and page reading | web search | Google Search (paid plan) and URL context | — | browser search |
| Code execution | sandbox, files sent to the chat | code interpreter, files sent to the chat | code execution, charts sent to the chat | — | Python, charts sent to the chat |
| Context summarisation | by the API | by the bot | by the bot | by the bot | by the bot (from 5K on the free plan) |

The model button in `/settings` lists the models of all configured providers, with icons for their
capabilities. In private chats, changing the model starts a new conversation; replies to earlier messages
continue in the model of that conversation. Models not listed above can be set as `provider:model`; they
get the provider's default capabilities and are counted at $0 in `/usage`.

Documents a model cannot read natively are converted to text: PDF text is extracted (including
signed PDFs), text files are inserted as is. Other binary files are passed to the code sandbox with Claude
only.

## Secretary mode

The bot can answer people in your private chats on your behalf through Telegram Business (a Telegram
Premium feature).

1. In Telegram, open Settings → Telegram Business → Chatbots, enter the bot's username, choose the chats it
   may access and allow it to reply to messages.
2. The bot confirms the connection in its chat with you. Only accounts listed in `ALLOWED_USER_IDS` can
   connect it; connections from other accounts are ignored.

The secretary makes a single request to the model per reply, without web search, code execution or
reminders: the people it answers are not on the whitelist. The model reads the chat's latest messages,
including yours and its own earlier replies. Voice and video notes are transcribed when `GROQ_API_KEY` is
set. Spending is counted as yours in `/usage`.

`prompts/secretary.md` sets what the secretary writes; while the file contains only its comment, a
built-in prompt is used. `prompts/secretary.toml` sets when it replies:

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` turns secretary mode off without disconnecting the bot |
| `online_minutes` | `10` | You count as online for this many minutes after your last message in a connected chat or to the bot |
| `reply_when_online` | `false` | Reply while you are online. With `false` the bot waits until you have been away for `online_minutes` and stays silent if you answer first |
| `delay_seconds` | `5` | Pause after the person's last message, so several messages in a row get one reply |
| `max_replies`, `limit_hours` | `3`, `24` | At most `max_replies` replies per chat within `limit_hours`. Your own message in the chat resets the count. `0` removes the limit |
| `hours` | `""` | Reply only within these local hours, e.g. `"09:00-23:00"` or `"22:00-08:00"`. Empty: any time |
| `voice` | `true` | Transcribe voice and video notes |
| `history` | `30` | How many of the chat's latest messages the model reads |
| `model` | `""` | Model for the secretary, e.g. `anthropic:claude-haiku-5-5`. Empty: the model chosen in `/settings` |

Telegram does not tell bots whether a user is online, so the bot judges by the last message of yours it has
seen. A pending reply needs a running process: if the bot restarts while waiting, it replies after the
person's next message. On hosts that stop the CPU between requests (`WEBHOOK_INLINE`), waiting does not
work.

## Prompts

The `prompts/` folder contains prompt files that can be edited without changing code:

- `fact-check.md` — rules for explaining forwarded posts and checking claims. It is appended to the system
  prompt.
- `system.md` — a template. While it contains only its comment, the built-in prompt is used. Text written
  below the comment replaces the built-in system prompt.
- `secretary.md` — a template for the secretary's prompt. While it contains only its comment, the
  built-in prompt is used.
- `secretary.toml` — secretary mode settings, see [Secretary mode](#secretary-mode).

These files are read at startup. With Docker, rebuild the image or mount the folder:
`-v ./prompts:/app/prompts:ro`. `PROMPTS_DIR` sets a different location.

## Configuration

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs allowed to use the bot |
| `ACCESS_DENIED_TEXT` | Reply to other users: unset — a short refusal in their language; empty — no reply |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY` | Provider API keys; at least one is required |
| `DEFAULT_MODEL` | `provider:model`; empty — the cheapest model of the first configured provider |
| `GROQ_PAID_TIER`, `GEMINI_PAID_TIER` | `true` on paid plans |
| `COMPACTION_TRIGGER_TOKENS` | Context size that triggers summarisation (minimum 50000, default 100000) |
| `MAX_OUTPUT_TOKENS` | Maximum answer length (default 16000) |
| `DEFAULT_EFFORT` | Default reasoning depth: low, medium or high |
| `WEB_SEARCH_MAX_USES` | Maximum Claude web searches per answer (default 5) |
| `WHISPER_MODEL` | `whisper-large-v3` or `whisper-large-v3-turbo` |
| `DATABASE_URL` | Default `sqlite+aiosqlite:///data/tgagent.db`; PostgreSQL: `postgresql+asyncpg://...` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database settings for `compose.yaml` |
| `WEBHOOK_URL`, `WEBHOOK_SECRET`, `PORT`, `WEBHOOK_INLINE` | Webhook mode, see above |
| `REMINDER_POLL_SECONDS` | Interval between reminder checks (default 60) |
| `PROMPTS_DIR` | Folder with prompt files (default `prompts`) |
| `TIMEZONE` | Time zone for reminders (default Europe/Moscow) |
| `CHAT_LOG_RETENTION_DAYS` | Retention of group message logs and the secretary's chats (default 30) |
| `LOG_LEVEL` | INFO or DEBUG |

## Architecture

```
Telegram ──polling or webhook──▶ AccessMiddleware ─▶ handlers (private / groups / guest / commands)
                                                        │
                                                        ▼
            TurnService: conversation branch → summarisation → model runner of the conversation
                               │                                │
          SQLite or PostgreSQL ◀┘       Claude / OpenAI / Gemini / DeepSeek / Groq
     (message tree, group log,                 (streaming, tools, media)
      reminders, usage)                                   │
                                          ResponseSink ◀──┘
                     (drafts in private chats, typing and one reply in groups, guest, reminders)
```

| Package | Contents |
|---|---|
| `agent/` | model catalog and pricing, prompts, tools, provider runners (`providers/`), error handling |
| `context/` | Telegram message rendering, per-provider media encoding, PDF handling, history tree |
| `services/` | turn processing, summarisation, stop handling, reminders, tool handlers, topic titles, usage, secretary mode |
| `telegram/` | handlers, access control, Markdown rendering, streaming, webhook server |
| `storage/` | SQLAlchemy models, repositories, Alembic migrations |
| `i18n.py` | interface texts in Russian and English |

A conversation is stored as a tree of nodes; the request context is the path from a message to the root
or to the latest summary. Each conversation is bound to the model it started with, and its history is
stored in that provider's native format, so reasoning items, signatures and tool calls are returned to the
provider unchanged.

## Development

```bash
uv sync
uv run ruff check src tests migrations && uv run ruff format src tests migrations
uv run mypy
uv run pytest                 # database tests use a temporary SQLite file
docker run -d --name tgagent-testdb -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test -p 55432:5432 postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:55432/test uv run pytest
```

Provider runners are tested with fakes and with the official SDKs over a mocked HTTP transport. New
migration: `DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`.

## CI/CD

`.github/workflows/ci.yml` runs linting (ruff, mypy) and tests on PostgreSQL and SQLite for every pull
request and push, builds a multi-arch image and pushes it to GHCR from `main`, and optionally deploys.

Deployment uses a self-hosted GitHub Actions runner on the server, which connects to GitHub over outbound
HTTPS; no SSH or inbound ports are required. The job copies `compose.yaml` and `.env` to `DEPLOY_DIR`,
runs `docker compose pull` and `up --wait`, and prints the logs on failure.

Server setup (once):

1. Install Docker Engine with the compose plugin.
2. Create the runner user and the deployment directory:
   ```bash
   sudo useradd --system --create-home gha-runner
   sudo usermod -aG docker gha-runner
   sudo install -d -o gha-runner -g gha-runner -m 750 /opt/tgagent
   ```
   Membership in the `docker` group is equivalent to root access; use a dedicated server.
3. **Settings → Actions → Runners → New self-hosted runner** (Linux). Install the runner as `gha-runner`
   and register it with the `tgagent` label:
   ```bash
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels tgagent --unattended
   sudo ./svc.sh install gha-runner && sudo ./svc.sh start
   ```

Repository settings:

- For public repositories: Settings → Actions → General → "Fork pull request workflows" → "Require
  approval for all outside collaborators", so that pull requests cannot run code on the runner. The
  deployment job runs only on pushes to `main`.
- **Settings → Environments → `production`**: restrict deployments to `main`; reviewers are optional.
- **Secrets:** `TELEGRAM_BOT_TOKEN`, `POSTGRES_PASSWORD` and the provider keys in use.
- **Variables:** `DEPLOY_ENABLED=true` (deployment is skipped otherwise), `ALLOWED_USER_IDS`. Optional:
  `DEFAULT_MODEL`, `GROQ_PAID_TIER`, `GEMINI_PAID_TIER`, `ACCESS_DENIED_TEXT`, `TIMEZONE`, `DEFAULT_EFFORT`,
  `COMPACTION_TRIGGER_TOKENS`, `WHISPER_MODEL`, `DEPLOY_DIR` (default `/opt/tgagent`).

## Maintenance

```bash
docker compose ps
docker compose logs -f --tail 200 bot
docker compose pull && docker compose up -d                              # update
docker compose exec postgres pg_dump -U tgagent tgagent > backup.sql     # PostgreSQL backup
```

With SQLite, back up the `tgagent_botdata` volume (file `tgagent.db`).

## License

[MIT](LICENSE)
