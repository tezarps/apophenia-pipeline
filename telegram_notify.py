"""Best-effort Telegram push for pipeline status — never raises, never blocks
the pipeline if Telegram is slow/down or the bot isn't configured yet."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# This bot/chat is now shared across all three channel pipelines (revenge-
# pipeline, apophenia-pipeline, narava-pipeline), one Telegram chat for
# everything instead of three separate bots (user decision 2026-07-08).
# The prefix is the only thing that tells them apart in that shared chat.
CHANNEL_PREFIX = "[Apophenia]"


def notify(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": f"{CHANNEL_PREFIX} {text}"},
            timeout=10,
        )
    except Exception:
        pass
