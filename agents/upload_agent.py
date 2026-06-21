import json
import pickle
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from config import YOUTUBE_CLIENT_SECRET, TOKEN_FILE

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# Optimal publish time: 8 PM US Eastern (same prime-time reasoning as Narava).
PUBLISH_HOUR = 20  # 8 PM

# 2x/week cadence. Tuesday + Friday is a starting point, not a locked decision —
# user said "durasi fleksibel, hari fleksibel" (see project memory
# project_apophenia.md), adjust once there's real watch-time data to optimize
# against. Python weekday(): Mon=0..Sun=6.
PUBLISH_WEEKDAYS = {1, 4}  # Tuesday, Friday


def _latest_scheduled_utc():
    """Latest still-future publish_at_utc already queued in schedule.json, or None."""
    schedule_file = TOKEN_FILE.parent / "schedule.json"
    if not schedule_file.exists():
        return None
    try:
        entries = json.loads(schedule_file.read_text())
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    future = []
    for e in entries:
        try:
            ts = datetime.strptime(e["publish_at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= now:
            future.append(ts)
    return max(future) if future else None


def _next_publish_time():
    """Return next Tuesday/Friday 8 PM US Eastern as UTC RFC3339, queued after
    any already-scheduled (future) upload so videos publish on cadence without
    colliding."""
    month = datetime.now(timezone.utc).month
    et_offset = -4 if 3 <= month <= 11 else -5
    et_zone = timezone(timedelta(hours=et_offset))
    label = "EDT" if et_offset == -4 else "EST"

    now_et = datetime.now(et_zone)
    target = now_et.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)

    latest = _latest_scheduled_utc()
    if latest is not None:
        latest_et = latest.astimezone(et_zone)
        if target <= latest_et:
            target = latest_et + timedelta(days=1)

    while target.weekday() not in PUBLISH_WEEKDAYS:
        target += timedelta(days=1)

    utc_str = target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    wib = target.astimezone(timezone(timedelta(hours=7)))
    return utc_str, f"{target.strftime('%H:%M')} {label} = {wib.strftime('%H:%M')} WIB"


def get_credentials():
    """Public so other scripts (e.g. ab_test_check.py) can build their own
    service — e.g. youtubeAnalytics v2 — off the same token without reaching
    into a private function's internals."""
    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return creds


def _get_service():
    return build("youtube", "v3", credentials=get_credentials())


def upload_video(video_path, thumbnail_path, metadata, category=None):
    yt = _get_service()
    publish_at_utc, publish_at_label = _next_publish_time()

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": "22",  # People & Blogs — closer fit than Narava's Entertainment(24) for essay content
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_utc,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=50 * 1024 * 1024,
    )

    request = yt.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"    Upload: {pct}%", end="\r")

    video_id = response["id"]
    print(f"    Uploaded: youtube.com/watch?v={video_id}")
    print(f"    Scheduled: {publish_at_label}")

    # Save the schedule slot BEFORE the thumbnail step — same ordering rule as
    # Narava: a thumbnail failure must never leave this publish slot untracked.
    _save_schedule(video_id, metadata, publish_at_utc, publish_at_label)

    if thumbnail_path and Path(thumbnail_path).exists():
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path)),
            ).execute()
            print("    Thumbnail set (variant A)")
        except Exception as e:
            # Non-fatal — confirmed 2026-06-21: custom thumbnails need the
            # channel to be phone-verified at youtube.com/verify, a manual
            # one-time step. The video itself already uploaded successfully
            # above; losing the custom thumbnail must never fail the whole
            # run or risk a duplicate re-upload on retry.
            print(f"    Warning: thumbnail set failed (video still uploaded fine): {e}")

    return video_id


def _save_schedule(video_id, metadata, publish_at_utc, publish_at_label):
    schedule_file = TOKEN_FILE.parent / "schedule.json"
    entries = []
    if schedule_file.exists():
        try:
            entries = json.loads(schedule_file.read_text())
        except Exception:
            pass
    entries.insert(0, {
        "video_id": video_id,
        "title": metadata.get("title", ""),
        "publish_at_utc": publish_at_utc,
        "publish_at_label": publish_at_label,
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "scheduled",
    })
    entries = entries[:60]
    schedule_file.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
