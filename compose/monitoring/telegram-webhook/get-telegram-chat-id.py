#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request


def load_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


env_path = os.environ.get("TELEGRAM_ENV_FILE", "/run/secrets/telegram.env")
token = os.environ.get("TELEGRAM_BOT_TOKEN") or load_env_file(env_path).get("TELEGRAM_BOT_TOKEN", "")
token = token.strip()

if not token:
    print("TELEGRAM_BOT_TOKEN is missing. Set it in the runtime secret file or environment.", file=sys.stderr)
    sys.exit(2)

url = f"https://api.telegram.org/bot{token}/getUpdates"
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    print(f"Telegram getUpdates failed with HTTP {exc.code}. Token was not printed.", file=sys.stderr)
    sys.exit(1)

seen = set()
for update in payload.get("result", []):
    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat_id in seen:
        continue
    seen.add(chat_id)
    chat_type = chat.get("type", "unknown")
    title = chat.get("title") or chat.get("username") or chat.get("first_name") or "(no title)"
    print(f"chat_id={chat_id} type={chat_type} label={title}")

if not seen:
    print("No chat IDs found. Send a message to the bot or add it to the group, then run again.")
