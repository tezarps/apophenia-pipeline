#!/usr/bin/env python3
"""Run once to authenticate YouTube. Saves token to youtube_token.pickle."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.upload_agent import _get_service

if __name__ == "__main__":
    print("Opening browser for YouTube OAuth...")
    svc = _get_service()
    me = svc.channels().list(part="snippet", mine=True).execute()
    name = me["items"][0]["snippet"]["title"]
    print(f"\n✓ Authenticated as: {name}")
    print("Token saved to youtube_token.pickle — pipeline is ready.")
