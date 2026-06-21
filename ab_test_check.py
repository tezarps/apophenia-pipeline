#!/usr/bin/env python3
"""Daily cron: drives our own thumbnail A/B rotation since YouTube has no
public API for its native "Test & Compare" (confirmed — Data API only
exposes thumbnails().set() for ONE active thumbnail, no variant/experiment
endpoint). Sequential, not simultaneous: publish with A, run it for
PHASE_DAYS, switch to B, run that for PHASE_DAYS too, compare CTR, keep
whichever won (already active if B won; swapped back if A won).
"""
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import supabase_io as sb
from agents.upload_agent import get_credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram_notify import notify

PHASE_DAYS = 3


def _slug(topic_name):
    return topic_name.lower().replace(" ", "_")


def _ctr(analytics, video_id, start, end):
    """Returns impressions click-through rate (%) for a video over a date
    range, or None if YouTube doesn't have data for it yet (too new / too
    little traffic — impressionsClickThroughRate has a minimum data threshold)."""
    try:
        resp = analytics.reports().query(
            ids="channel==MINE",
            startDate=start.strftime("%Y-%m-%d"),
            endDate=end.strftime("%Y-%m-%d"),
            metrics="impressions,impressionsClickThroughRate",
            filters=f"video=={video_id}",
        ).execute()
    except Exception as e:
        print(f"    Analytics query failed for {video_id}: {e}")
        return None
    rows = resp.get("rows")
    if not rows:
        return None
    return rows[0][1]


def _set_thumbnail(yt, video_id, image_bytes):
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_bytes)
        tmp_path = f.name
    try:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(tmp_path)).execute()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run():
    tests = sb.get_running_thumbnail_tests()
    print(f"Thumbnail A/B check — {len(tests)} test(s) in progress")
    if not tests:
        return

    creds = get_credentials()
    yt = build("youtube", "v3", credentials=creds)
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    now = datetime.now(timezone.utc)

    for test in tests:
        topic = sb.get_topic(test["topic_id"])
        if not topic:
            continue
        slug = _slug(topic["topic"])
        category = topic["category"]
        video_id = test["video_id"]

        try:
            if test["active_variant"] == "A" and not test["switched_at"]:
                started = datetime.fromisoformat(test["started_at"].replace("Z", "+00:00"))
                if (now - started).days < PHASE_DAYS:
                    continue
                ctr_a = _ctr(analytics, video_id, started, now)
                if ctr_a is None:
                    print(f"    {topic['topic']}: no analytics data yet for phase A, retrying tomorrow")
                    continue
                thumb_b = sb.download_thumbnail_variant_bytes(category, slug, "B")
                _set_thumbnail(yt, video_id, thumb_b)
                sb.flip_to_variant_b(test["id"], ctr_a)
                print(f"    {topic['topic']}: switched A -> B (CTR A = {ctr_a}%)")
                notify(f"🔄 Narava A/B — {topic['topic']}\nSwitched to thumbnail B (CTR A: {ctr_a}%)")

            elif test["active_variant"] == "B" and test["switched_at"]:
                switched = datetime.fromisoformat(test["switched_at"].replace("Z", "+00:00"))
                if (now - switched).days < PHASE_DAYS:
                    continue
                ctr_b = _ctr(analytics, video_id, switched, now)
                if ctr_b is None:
                    print(f"    {topic['topic']}: no analytics data yet for phase B, retrying tomorrow")
                    continue
                ctr_a = test["ctr_a"] or 0
                winner = "A" if ctr_a > ctr_b else "B"
                if winner == "A":
                    thumb_a = sb.download_thumbnail_variant_bytes(category, slug, "A")
                    _set_thumbnail(yt, video_id, thumb_a)
                sb.resolve_thumbnail_test(test["id"], ctr_b, winner)
                print(f"    {topic['topic']}: resolved — winner {winner} (A={ctr_a}% B={ctr_b}%)")
                notify(
                    f"🏆 Narava A/B resolved — {topic['topic']}\n"
                    f"Winner: thumbnail {winner} (A: {ctr_a}% · B: {ctr_b}%)\n"
                    f"youtube.com/watch?v={video_id}"
                )
        except Exception as e:
            print(f"    {topic['topic']}: error — {e}")
            traceback.print_exc()


if __name__ == "__main__":
    run()
