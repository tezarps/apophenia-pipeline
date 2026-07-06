import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Reused from narava-pipeline GCP project on purpose — keeps billing on the
# same free-trial credit pool. See project memory project_apophenia.md.
GEMINI_IMAGE_API_KEY = os.environ.get("GEMINI_IMAGE_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
NANO_BANANA_MODEL = "gemini-3.1-flash-image"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET_PATH", "youtube_client_secret.json")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"
OUTPUT_DIR = BASE_DIR / "output"
TOPICS_FILE = BASE_DIR / "topics" / "archetype_topics.csv"
TOKEN_FILE = BASE_DIR / "youtube_token.pickle"

# Switched narration voice from ElevenLabs to Kokoro (local, free, no API key)
# 2026-07-06 — ElevenLabs credits ran out. bm_george selected after auditioning
# af_heart / bm_george / am_echo (user decision 2026-07-06).
KOKORO_VOICE = "bm_george"
KOKORO_SPEED = 0.95          # near-normal essay pace
KOKORO_MODEL_PATH = "/Users/admin/kokoro-models/kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = "/Users/admin/kokoro-models/voices-v1.0.bin"

# ElevenLabs still used by agents/music_agent.py's Sound Generation API — not
# retired, only the narration voice moved to Kokoro.

# Homebrew's default `ffmpeg` formula bottle isn't built with libass, so the
# `subtitles` filter (caption burn-in) fails with "Unknown filter" on a stock
# local install — confirmed 2026-06-21. `brew install ffmpeg-full` provides it
# but is keg-only, so it's opted into per-project via .env (FFMPEG_BIN) instead
# of touching the global shell PATH. GitHub Actions' `apt-get install ffmpeg`
# already includes libass, so the plain "ffmpeg"/"ffprobe" default is correct there.
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "audio").mkdir(exist_ok=True)
(OUTPUT_DIR / "video").mkdir(exist_ok=True)
(OUTPUT_DIR / "shorts").mkdir(exist_ok=True)
