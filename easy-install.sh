#!/usr/bin/env bash
# Installs tgagent with Docker in one go: asks a few questions, writes .env and starts the bot.
#   curl -fsSL https://raw.githubusercontent.com/ayato-dev/TelegramAgent/main/easy-install.sh | bash
# Re-run it to change the settings; your data stays in the Docker volume.
set -euo pipefail

REPO_RAW="${TGAGENT_REPO_RAW:-https://raw.githubusercontent.com/ayato-dev/TelegramAgent/main}"
IMAGE="${TGAGENT_IMAGE:-ghcr.io/ayato-dev/telegramagent:latest}"
DIR="${TGAGENT_DIR:-$HOME/tgagent}"

# Answers come from the terminal even when the script itself arrives through a pipe.
if { : </dev/tty; } 2>/dev/null; then
  exec 3</dev/tty
else
  exec 3<&0
fi

LANGUAGE=en
say() { if [ "$LANGUAGE" = ru ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
ask() { # ask VAR "english prompt" "русский вопрос"
  local prompt
  if [ "$LANGUAGE" = ru ]; then prompt=$3; else prompt=$2; fi
  printf '%s ' "$prompt"
  IFS= read -r "$1" <&3 || true
}
fail() { say "$1" "$2" >&2; exit 1; }

printf 'Language / Язык: 1) English  2) Русский [1]: '
IFS= read -r choice <&3 || true
[ "$choice" = 2 ] && LANGUAGE=ru

say "== tgagent: your own AI agent in Telegram ==" "== tgagent: свой ИИ-агент в Telegram =="

if ! command -v docker >/dev/null 2>&1; then
  [ "$(uname -s)" = Linux ] || fail "Install Docker Desktop first: https://docs.docker.com/get-docker/" \
    "Сначала поставь Docker Desktop: https://docs.docker.com/get-docker/"
  ask install "Docker is not installed. Install it now with the official script? [Y/n]" \
    "Docker не установлен. Поставить официальным скриптом? [Y/n]"
  case "$install" in [nN]*) fail "Docker is required." "Без Docker не получится." ;; esac
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required." "Нужен Docker Compose v2."

mkdir -p "$DIR"
cd "$DIR"

reuse=n
if [ -f .env ]; then
  ask reuse "Found settings in $DIR/.env. Keep them? [Y/n]" "Нашёл настройки в $DIR/.env. Оставить их? [Y/n]"
  case "$reuse" in [nN]*) reuse=n ;; *) reuse=y ;; esac
fi

if [ "$reuse" = n ]; then
  say "" ""
  say "1. Create a bot with @BotFather (/newbot) and paste its token." \
    "1. Создай бота в @BotFather (/newbot) и вставь его токен."
  while :; do
    ask token "Bot token:" "Токен бота:"
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && break
    say "That doesn't look like a bot token." "Это не похоже на токен бота."
  done

  say "2. Who may use the bot: Telegram user IDs, comma-separated (yours: ask @userinfobot)." \
    "2. Кому можно пользоваться ботом: Telegram ID через запятую (свой узнай у @userinfobot)."
  while :; do
    ask users "User IDs:" "ID пользователей:"
    users=${users// /}
    [[ "$users" =~ ^[0-9]+(,[0-9]+)*$ ]] && break
    say "Only digits and commas, please." "Только цифры и запятые."
  done

  say "3. AI providers: paste the keys you have, press Enter to skip. One is enough." \
    "3. Нейросети: вставь ключи, которые есть, Enter — пропустить. Хватит одного."
  say "   Free option: Groq (https://console.groq.com/keys) — gpt-oss-120b and voice messages." \
    "   Бесплатно: Groq (https://console.groq.com/keys) — gpt-oss-120b и голосовые."
  ask anthropic "Anthropic (Claude):" "Anthropic (Claude):"
  ask openai "OpenAI:" "OpenAI:"
  ask gemini "Google Gemini:" "Google Gemini:"
  ask deepseek "DeepSeek:" "DeepSeek:"
  ask groq "Groq:" "Groq:"
  [ -n "$anthropic$openai$gemini$deepseek$groq" ] || fail "At least one key is needed." "Нужен хотя бы один ключ."

  say "4. Default model as provider:model, Enter = the cheapest one you have a key for." \
    "4. Модель по умолчанию как провайдер:модель, Enter — самая дешёвая из доступных."
  say "   e.g. anthropic:claude-haiku-5-5, openai:gpt-6-luna, gemini:gemini-3.1-flash-lite, groq:openai/gpt-oss-120b" \
    "   например anthropic:claude-haiku-5-5, openai:gpt-6-luna, gemini:gemini-3.1-flash-lite, groq:openai/gpt-oss-120b"
  ask model "Default model:" "Модель по умолчанию:"

  umask 077
  {
    echo "TELEGRAM_BOT_TOKEN=$token"
    echo "ALLOWED_USER_IDS=$users"
    [ -n "$anthropic" ] && echo "ANTHROPIC_API_KEY=$anthropic"
    [ -n "$openai" ] && echo "OPENAI_API_KEY=$openai"
    [ -n "$gemini" ] && echo "GEMINI_API_KEY=$gemini"
    [ -n "$deepseek" ] && echo "DEEPSEEK_API_KEY=$deepseek"
    [ -n "$groq" ] && echo "GROQ_API_KEY=$groq"
    [ -n "$model" ] && echo "DEFAULT_MODEL=$model"
    echo "TIMEZONE=${TZ:-Europe/Moscow}"
  } >.env
  say "Saved $DIR/.env (only you can read it)." "Сохранил $DIR/.env (читать его можешь только ты)."
fi

# The SQLite setup: one container, the database on a Docker volume. Uses the published image.
curl -fsSL "$REPO_RAW/compose.sqlite.yaml" |
  sed -e '/^    build: \.$/d' -e "s|\${BOT_IMAGE:-[^}]*}|$IMAGE|" >compose.yaml
docker compose pull
docker compose up -d

say "" ""
say "Done! Message your bot in Telegram." "Готово! Напиши своему боту в Telegram."
say "Logs:    cd $DIR && docker compose logs -f" "Логи:      cd $DIR && docker compose logs -f"
say "Update:  cd $DIR && docker compose pull && docker compose up -d" \
  "Обновить: cd $DIR && docker compose pull && docker compose up -d"
say "Settings: re-run this script or edit $DIR/.env (all options: $REPO_RAW/.env.example)" \
  "Настройки: запусти скрипт ещё раз или поправь $DIR/.env (все опции: $REPO_RAW/.env.example)"
