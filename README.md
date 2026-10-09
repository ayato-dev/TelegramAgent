# tgagent — your own AI agent in Telegram

[Русская версия](README.ru.md)

This isn't another "ask the chatbot" bot. The agent lives in Telegram and actually does stuff: searches
the web, runs Python, reads photos, PDFs and voice messages, sets reminders, makes polls and works through
checklists. In private chats every topic is its own conversation (like chats in ChatGPT). In groups it
behaves like Grok on X: quiet until you call it.

Plug in whatever brain you like: **Claude, OpenAI, Gemini, DeepSeek or Groq** (Groq has gpt-oss-120b for
free). One key is enough, all five work too, and anyone can switch models right in `/settings`.

The bot is private: only the Telegram IDs you list can use it. It speaks Russian or English, depending on
the person's Telegram app.

## Quick start

You need a server or computer with Docker (or a Linux box where the script can install it) and:

1. A bot token from [@BotFather](https://t.me/BotFather) (`/newbot`).
2. Your Telegram ID — [@userinfobot](https://t.me/userinfobot) will tell you.
3. At least one AI key. Free option: [Groq](https://console.groq.com/keys).

Then:

```bash
curl -fsSL https://raw.githubusercontent.com/ayato-dev/TelegramAgent/main/easy-install.sh | bash
```

The script asks a few questions, writes `~/tgagent/.env` and starts the bot from the ready-made image
(amd64 and arm64). Data lives in a SQLite file on a Docker volume — no database server needed.

Finish the setup in @BotFather (Bot Settings):

- **Group Privacy → Turn off**, otherwise the bot can't see group chats (if it's already in a group,
  remove it and add it again).
- In the BotFather settings mini app turn on **Threaded Mode** (topics in private chats) and
  **Guest Mode**.

## What it can do

- **Private chats.** Every topic is a separate conversation; a topic without a name gets one from the
  bot. `/new` starts over. Reply to an old bot message and the conversation continues from there.
  Answers stream live with the bot's thinking and a Stop button.
- **Groups.** The bot reads along but only answers when you @mention it or reply to it. Send
  `@bot is this true?` as a reply to someone's message, photo or voice note and it takes that message and
  the whole reply chain into account. Everyone replying to the bot gets their own thread. It can also sum
  up what the chat was arguing about. Forward a post with a comment and it treats both as one request.
- **Guest mode.** Call the bot in any chat, even one it isn't in, and it answers once.
- **Tools.** Web search and reading links, Python (math, charts, files sent back to the chat), reminders
  and scheduled tasks ("in an hour check the rate and message me"), polls, Telegram checklists with a
  report on each task. OpenAI models can also draw pictures.
- **Posts.** Forward a news post without a question and you get it explained the way Grok does it: what
  it's about, the background the post leaves out, then a short fact-check.
- **Voice.** Voice messages, video notes, audio and video are transcribed by Groq Whisper (free on
  Groq's free plan). Gemini listens to and watches them itself, and it understands YouTube links too.
- **Long conversations.** When the context gets big, the history is compressed into a summary and the
  conversation goes on. Claude does it on the API side, for the others the bot does it.
- **`/settings`** — model, depth of thinking, showing thoughts, search, code, style (normal or bold).
  Settings belong to the person and apply everywhere: private chat, groups and guest mode.
  **`/usage`** — spending in $ by day and by person.
- **Access.** Only the IDs in `ALLOWED_USER_IDS`. A group works only if someone from that list added
  the bot, otherwise the bot leaves right away.

## Models: what they can do and what they cost

Prices per 1M tokens, input / output, checked on October 8, 2026. The key is what goes into
`DEFAULT_MODEL`.

| Model | Key | 👁 images | 🎙 hears voice | 🌐 search | 🐍 code | 🎨 draws | Price |
|---|---|:-:|:-:|:-:|:-:|:-:|---|
| Claude Haiku 5.5 | `anthropic:claude-haiku-5-5` | ✅ | | ✅ | ✅ | | $0.10 / $0.50 (up to 100K context) |
| Claude Sonnet 5.5 | `anthropic:claude-sonnet-5-5` | ✅ | | ✅ | ✅ | | $2 / $10 |
| Claude Opus 5.5 | `anthropic:claude-opus-5-5` | ✅ | | ✅ | ✅ | | $4 / $20 |
| GPT-6 Luna | `openai:gpt-6-luna` | ✅ | | ✅ | ✅ | ✅ | $0.10 / $0.50 |
| GPT-6.1 Sol | `openai:gpt-6.1-sol` | ✅ | | ✅ | ✅ | ✅ | $2 / $10 |
| GPT-6 Astra | `openai:gpt-6-astra` | ✅ | | ✅ | ✅ | ✅ | $10 / $50 |
| Gemini 3.1 Flash-Lite | `gemini:gemini-3.1-flash-lite` | ✅ | ✅ | paid | ✅ | | free plan / $0.25 / $1.50 |
| Gemini 3.8 Flash | `gemini:gemini-3.8-flash` | ✅ | ✅ | paid | ✅ | | free plan / $0.75 / $3.75 |
| Gemini 3.1 Pro | `gemini:gemini-3.1-pro-preview` | ✅ | ✅ | paid | ✅ | | $2 / $12 (up to 200K) |
| DeepSeek Flash | `deepseek:deepseek-flash` | ✅ | | | | | $0.30 / $1.20, off-peak $0.15 / $0.60 |
| DeepSeek V4 Pro | `deepseek:deepseek-v4-pro` | | | | | | $1.32 / $3.96, half price off-peak |
| gpt-oss-120b (Groq) | `groq:openai/gpt-oss-120b` | | | ✅ | ✅ | | free with limits / $0.15 / $0.60 |
| gpt-oss-20b (Groq) | `groq:openai/gpt-oss-20b` | | | ✅ | ✅ | | free with limits / $0.075 / $0.30 |

Good to know:

- **Voice.** Models without 🎙 get voice messages as text via Whisper, which needs a Groq key. Without one
  the bot tells the model there's nothing to transcribe with.
- **Gemini for free** works, but without Google Search (that's paid only), with small limits, and
  Google trains on your data. Reading links works for free. Turn on the paid plan with
  `GEMINI_PAID_TIER=true`.
- **Groq for free** means 30 requests a minute, 1,000 a day, 8K tokens a minute and 200K a day. The bot
  adapts: it compresses gpt-oss conversations already at 5K tokens and keeps answers shorter. A web
  search that reads pages can eat up to 40K tokens, so you get only a few searches a day. On a paid plan
  set `GROQ_PAID_TIER=true`.
- **DeepSeek** is half price off-peak. Peak hours are weekdays 01:00–04:00 and 06:00–10:00 UTC. DeepSeek
  has no search and no code; only Flash sees images.
- **OpenAI** draws pictures (gpt-image-2, about $0.04 each) and runs code in a container ($0.03 per
  session of up to 20 minutes). Search on OpenAI and Claude is $0.01 per query plus tokens.
- **Long context costs more.** Haiku 5.5 is 5× pricier past 100K tokens, OpenAI doubles input past 272K,
  Gemini Pro doubles past 200K. That's why compression kicks in at 100K by default.
- OpenAI, Anthropic, Google and Groq don't work from every country (Russia, for example, is blocked).
  If the server is in the wrong place, requests fail with a region error and the bot says so.

## Saving money

Ready-made combos:

- **Completely free.** Only `GROQ_API_KEY`: gpt-oss-120b with search and Python plus free Whisper,
  hosted on Oracle Always Free or GCP e2-micro (see below). Fine for a personal bot for a couple of
  people, but you'll hit the limits now and then.
- **Free with voice and YouTube.** Gemini and Groq keys, `DEFAULT_MODEL=gemini:gemini-3.1-flash-lite`.
  Gemini listens to voice and watches videos itself; gpt-oss helps out when you need a search.
- **Everything for pennies.** Claude Haiku 5.5 or GPT-6 Luna at $0.10 / $0.50. Haiku has the stronger
  agent side and server-side compression, Luna can draw. Add a Groq key for free Whisper. A busy day of
  chatting costs cents.
- **Cheap and smart at night.** DeepSeek Flash off-peak: $0.15 / $0.60, but no search.

More tips:

- Pick "⚡ Fast" in `/settings` when you don't need deep reasoning: the model thinks less, and thinking
  is billed like any other output.
- Turn search off when you don't need it: every search costs money plus a pile of tokens from the pages.
- Don't raise `COMPACTION_TRIGGER_TOKENS`. The longer the context, the more every message costs, and for
  some models everything gets pricier past the threshold.
- New topic → new topic or `/new`: the old context doesn't get dragged into every request.
- The repeated start of each request (prompt, older history) is cached automatically and is cheaper to
  read: 50× on DeepSeek, 10× on Claude and OpenAI, 2× on Groq.
- Keep an eye on `/usage`: it shows who spends what.

## Where to host it

The bot needs very little: about 300 MB of RAM. Polling (the default) works from anywhere with internet
access, no public address needed — your PC or a Raspberry Pi will do. Just keep it outside the countries
the AI providers block.

| Option | Cost | Notes |
|---|---|---|
| Oracle Cloud Always Free (ARM) | free | 2 CPUs + 12 GB on arm64, card required for sign-up, popular regions often "out of capacity". Idle VMs can be reclaimed; upgrading to pay-as-you-go (still $0) reportedly avoids that. |
| Google Cloud e2-micro | free | 1 GB RAM in us-west1 / us-central1 / us-east1. Enough for the bot with SQLite. |
| Any small VPS | €4–6 a month | Hetzner, OVH, Contabo and the like. The most hassle-free option. |
| Your PC or Raspberry Pi | free | Works while it's on. |
| Google Cloud Run (webhook) | free tier | Scales to zero; needs webhook mode and an external database (see below). |
| Render / Railway / Fly (webhook) | free–$5 | Free web services sleep and have no persistent disk, so pair them with Neon Postgres. |

Groq sits behind Cloudflare and some datacenter IPs get a 403 — if that happens, try another host.

## Other ways to install

**By hand, with SQLite (one container):**

```bash
git clone https://github.com/ayato-dev/TelegramAgent.git && cd TelegramAgent
cp .env.example .env          # fill in the token, ALLOWED_USER_IDS and at least one key
docker compose -f compose.sqlite.yaml up -d
docker compose -f compose.sqlite.yaml logs -f
```

**With PostgreSQL** (`compose.yaml` runs Postgres 18 next to the bot):

```bash
cp .env.example .env          # also set POSTGRES_PASSWORD (letters, digits, - and _ only)
docker compose up -d --build
```

Migrations run automatically on start. The log line `models: ...` shows which models the bot sees.

**Without Docker:** Python 3.13 and [uv](https://docs.astral.sh/uv/): `uv sync`, then
`uv run alembic upgrade head && uv run python -m tgagent`.

### Webhook mode and serverless

By default the bot polls Telegram, which needs no public address. On platforms that give you an HTTPS
address and sleep between requests, switch to webhook mode:

- `WEBHOOK_URL=https://your-address` — Telegram will post updates to `<url>/webhook`.
- `PORT` — the port to listen on (8080 by default; most platforms set it for you).
- `WEBHOOK_SECRET` — optional; derived from the bot token if unset. Set it explicitly if you use `/cron`.
- `WEBHOOK_INLINE=true` — handle each update inside its HTTP request. Needed where the CPU stops after
  the response is sent (Cloud Run with request-based billing).

The server also answers `GET /health` and `/cron`. Call `/cron` regularly (every 1–5 minutes) with the
header `X-Cron-Secret: <WEBHOOK_SECRET>` or `?secret=<WEBHOOK_SECRET>`: it fires due reminders and cleans
up old group logs on platforms where nothing runs between requests.

Serverless instances lose their disk, so use Postgres there. [Neon](https://neon.tech) has a free plan:
`DATABASE_URL=postgresql+asyncpg://user:password@host/dbname?ssl=require`. Neon sleeps after 5 idle
minutes, and the reminder check would keep waking it, so set `REMINDER_POLL_SECONDS=600`.

**Google Cloud Run in short** (Cloud Shell has `gcloud` and Docker ready). Cloud Run doesn't pull from
ghcr.io, so copy the image into Artifact Registry first:

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

Cloud Run tells you the service address after the first deploy; put it into `WEBHOOK_URL` and deploy
again. Better keep the keys in Secret Manager (`--set-secrets`) than in plain env vars.

## What the settings mean for each provider

| Setting | Claude | OpenAI | Gemini | DeepSeek | Groq gpt-oss |
|---|---|---|---|---|---|
| ⚡/⚖️/🧠 depth | effort low / medium / high | reasoning low / medium / high | thinking low / default / high | low / high / max | low / medium / high |
| 💭 thoughts | Claude's thought summary | reasoning summary | thought summary | raw reasoning | raw reasoning |
| 🌐 search | web search + page reading | web search | Google Search (paid) + reading links | none | browser search |
| 🐍 code | sandbox, files to the chat | code interpreter, files to the chat | code execution, charts to the chat | none | Python, charts to the chat |
| compression | on the API side | by the bot | by the bot | by the bot | by the bot (from 5K on the free plan) |

The **🤖 Model** button in `/settings` lists every model you have a key for, with badges for what it can
do. In private chats a new model starts a new conversation; replies to older messages continue in the
model the conversation started with. You can also set a model that isn't in the table
(`openai:gpt-7-preview`): it gets its provider's usual abilities, and `/usage` counts it as $0.

Documents a model can't read itself become text: the bot pulls the text out of PDFs (e-signed ones too)
and pastes text files as they are. Spreadsheets and other binary files go to the code sandbox only with
Claude for now.

## All settings

| Variable | What it's for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from @BotFather |
| `ALLOWED_USER_IDS` | comma-separated Telegram IDs that may use the bot |
| `ACCESS_DENIED_TEXT` | reply to strangers: unset = a short refusal in their language, empty = silence |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY` | provider keys, one is enough |
| `DEFAULT_MODEL` | `provider:model`; empty = the cheapest model of the first provider with a key |
| `GROQ_PAID_TIER`, `GEMINI_PAID_TIER` | `true` on paid plans |
| `COMPACTION_TRIGGER_TOKENS` | context size that triggers compression (at least 50000, default 100000) |
| `MAX_OUTPUT_TOKENS` | answer length cap (16000) |
| `DEFAULT_EFFORT` | default depth: low / medium / high |
| `WEB_SEARCH_MAX_USES` | how many searches Claude may run per answer (5) |
| `WHISPER_MODEL` | `whisper-large-v3` or the cheaper `whisper-large-v3-turbo` |
| `DATABASE_URL` | default `sqlite+aiosqlite:///data/tgagent.db`; Postgres: `postgresql+asyncpg://...` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | the database in `compose.yaml` |
| `WEBHOOK_URL`, `WEBHOOK_SECRET`, `PORT`, `WEBHOOK_INLINE` | webhook mode, see above |
| `REMINDER_POLL_SECONDS` | how often reminders are checked between wake-ups (60) |
| `TIMEZONE` | time zone for reminders (Europe/Moscow) |
| `CHAT_LOG_RETENTION_DAYS` | how long group logs are kept (30) |
| `LOG_LEVEL` | INFO / DEBUG |

## How it works

```
Telegram ──polling or webhook──▶ AccessMiddleware ─▶ handlers (private / groups / guest / commands)
                                                        │
                                                        ▼
            TurnService: conversation branch → compression → the conversation's model runner
                               │                                │
          SQLite or Postgres ◀─┘        Claude / OpenAI / Gemini / DeepSeek / Groq
     (message tree, group log,                 (streaming, tools, media)
      reminders, spending)                                │
                                          ResponseSink ◀──┘
                     (drafts in private chats, typing + one reply in groups, guest, reminders)
```

| Package | What's inside |
|---|---|
| `agent/` | model catalog and prices, prompt, tools, provider runners (`providers/`), errors |
| `context/` | Telegram messages → text, media for each provider, PDFs, the history tree |
| `services/` | a conversation turn, compression, Stop, reminders, tools, topic titles, spending |
| `telegram/` | handlers, access, Markdown → Rich Messages, streaming, webhook server |
| `storage/` | SQLAlchemy models, repositories, Alembic migrations |
| `i18n.py` | interface texts in Russian and English |

A conversation is a tree: a request's context is the path from a message up to the root (or the latest
summary). Every conversation is pinned to the model it started with and its history is kept in that
model's own format, so thoughts, signatures and tool calls go back to the provider exactly as it sent them.

## Development

```bash
uv sync
uv run ruff check src tests migrations && uv run ruff format src tests migrations
uv run mypy
uv run pytest                 # database tests run on a throwaway SQLite file
docker run -d --name tgagent-testdb -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test -p 55432:5432 postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:55432/test uv run pytest
```

Provider runners are tested on fakes and on the real SDKs over a mocked HTTP transport. A new migration:
`DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`.

## CI/CD

`.github/workflows/ci.yml`: **lint** (ruff, mypy) and **tests** on PostgreSQL and SQLite on every PR and
push → **build** (multi-arch image to GHCR) → **deploy** on push to `main`, if you turn it on.

Deployment has no SSH: a **self-hosted GitHub Actions runner** on your server picks up jobs over an
outgoing connection, no inbound ports. The job drops `compose.yaml` and `.env` into `DEPLOY_DIR`, runs
`docker compose pull` and `up --wait`, and prints the logs if something fails.

One-time server setup:

1. Install Docker Engine with the compose plugin.
2. Create a user for the runner and the deploy folder:
   ```bash
   sudo useradd --system --create-home gha-runner
   sudo usermod -aG docker gha-runner
   sudo install -d -o gha-runner -g gha-runner -m 750 /opt/tgagent
   ```
   Being in the `docker` group is effectively root, so keep the server for the bot only.
3. **Settings → Actions → Runners → New self-hosted runner** (Linux). Download the runner as
   `gha-runner` and register it with the `tgagent` label:
   ```bash
   ./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels tgagent --unattended
   sudo ./svc.sh install gha-runner && sudo ./svc.sh start
   ```

GitHub settings:

- Public repo? Settings → Actions → General → "Fork pull request workflows" → "Require approval for all
  outside collaborators". Otherwise someone's PR could change the workflow and run code on your runner.
  The deploy itself only runs on pushes to `main`.
- **Settings → Environments → `production`**: allow deployments from `main` only, add reviewers if you like.
- **Secrets**: `TELEGRAM_BOT_TOKEN`, `POSTGRES_PASSWORD` and the provider keys you use.
- **Variables**: `DEPLOY_ENABLED=true` (without it the deploy is skipped), `ALLOWED_USER_IDS`. Optional:
  `DEFAULT_MODEL`, `GROQ_PAID_TIER`, `GEMINI_PAID_TIER`, `ACCESS_DENIED_TEXT`, `TIMEZONE`, `DEFAULT_EFFORT`,
  `COMPACTION_TRIGGER_TOKENS`, `WHISPER_MODEL`, `DEPLOY_DIR` (default `/opt/tgagent`).

## Maintenance

```bash
docker compose ps
docker compose logs -f --tail 200 bot
docker compose pull && docker compose up -d             # update
docker compose exec postgres pg_dump -U tgagent tgagent > backup.sql   # Postgres backup
```

With SQLite, back up the `tgagent_botdata` volume (the `tgagent.db` file in it).

## License

[MIT](LICENSE)
