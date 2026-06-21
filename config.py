import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Reused from narava-pipeline GCP project on purpose — keeps billing on the
# same free-trial credit pool. See project memory project_apophenia.md.
GEMINI_IMAGE_API_KEY = os.environ.get("GEMINI_IMAGE_API_KEY", "")
NANO_BANANA_MODEL = "gemini-3.1-flash-image"

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET_PATH", "youtube_client_secret.json")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"
OUTPUT_DIR = BASE_DIR / "output"
TOPICS_FILE = BASE_DIR / "topics" / "archetype_topics.csv"
TOKEN_FILE = BASE_DIR / "youtube_token.pickle"

ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_VOICE_ID = "sIivXWc5MTlPIP3kJXhg"  # picked from ElevenLabs voice library, 2026-06-21 — untested, audition before first real render
ELEVENLABS_VOICE_SETTINGS = {
    "stability": 0.22,    # lower = more emotional range/intonation variation following sentence content,
                          # at some cost to take-to-take consistency — worth it for essay delivery
    "similarity_boost": 0.8,
    "style": 0.65,        # lean further into the voice's natural expressive style (raised from 0.5,
                          # 2026-06-21 — first audition sounded too flat/even for context-driven delivery)
    "speed": 0.95,        # near-normal pace, this is a talking-head essay not sleep narration
    "use_speaker_boost": True,
}

# Same lossy-format constraint as Narava — uncompressed PCM needs ElevenLabs Pro
# tier ($99/mo), Creator tier only supports lossy. See project memory project_apophenia.md.
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_192"

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
