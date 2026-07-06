import json
import pickle
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Prefer IPv4 but keep IPv6 as a fallback — some networks (including the
# primary dev machine on 2026-06-24) have broken IPv6 routing, so Google's
# APIs resolving IPv6-first caused hangs/failures there. But that flipped on
# this same machine 2026-07-06 (IPv6 worked, IPv4 was unreachable) — a hard
# IPv4-only filter broke connectivity entirely in that direction since it
# discarded IPv6 outright, leaving no fallback for socket.create_connection's
# normal try-each-address-in-order behavior to use. Reordering (not filtering)
# keeps that fallback intact regardless of which family is actually broken on
# a given network.
_orig_getaddrinfo = socket.getaddrinfo
def _prefer_ipv4(host, port, *args, **kwargs):
    results = _orig_getaddrinfo(host, port, *args, **kwargs)
    return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)
socket.getaddrinfo = _prefer_ipv4
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from config import YOUTUBE_CLIENT_SECRET, TOKEN_FILE
from agents.playlist_agent import add_video_to_playlist


SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# Switched 2026-06-23 from 3 PM ET (US) to 4 PM Australian Eastern — target
# audience moved to AU. Same underlying principle as the original ET choice:
# publish 2-3 hours AHEAD of the evening peak (~7-9 PM local) so the video
# already has early watch-time signal by the time the evening surge hits,
# rather than publishing INTO the peak. Revisit once this channel has its
# own YouTube Studio "when your viewers are on YouTube" heatmap data.
PUBLISH_HOUR = 16  # 4 PM AU Eastern

# Switched to daily cadence 2026-07-06 — pipeline now runs fully in the cloud
# (GitHub Actions) daily rather than the old 2x/week Wed/Thu schedule, same
# publish hour every day. Python weekday(): Mon=0..Sun=6.
PUBLISH_WEEKDAYS = {0, 1, 2, 3, 4, 5, 6}  # every day


def _latest_scheduled_utc(yt=None):
    """Latest still-future publishAt among the channel's own videos, read
    live from the YouTube API rather than the local schedule.json cache.

    schedule.json drifts from reality the moment someone edits a publish
    date by hand in YouTube Studio (confirmed 2026-06-22 — Topic #2 was
    manually moved from its automated slot to 28 Jun, but the local cache
    still said 25 Jun). Trusting that stale cache for collision-avoidance
    would let the next automated upload land before/on top of a manually
    moved video. The API call is one extra round-trip per upload, worth it
    to never desync again. Falls back to the local file only if the API
    call itself fails (e.g. transient network error)."""
    try:
        yt = yt or _get_service()
        res = yt.search().list(part="id", forMine=True, type="video", maxResults=50).execute()
        ids = [i["id"]["videoId"] for i in res.get("items", [])]
        if not ids:
            return None
        det = yt.videos().list(part="status", id=",".join(ids)).execute()
        now = datetime.now(timezone.utc)
        future = []
        for v in det["items"]:
            pa = v["status"].get("publishAt")
            if not pa:
                continue
            ts = datetime.strptime(pa, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if ts >= now:
                future.append(ts)
        return max(future) if future else None
    except Exception as e:
        print(f"    Warning: live publishAt check failed ({e}), falling back to local schedule.json")
        return _latest_scheduled_utc_from_cache()


def _latest_scheduled_utc_from_cache():
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


def _next_publish_time(yt=None):
    """Return next Wednesday/Thursday 4 PM Australian Eastern as UTC RFC3339,
    queued after any already-scheduled (future) upload so videos publish on
    cadence without colliding."""
    # AEDT (DST, Oct-Apr) = UTC+11 | AEST (Apr-Oct) = UTC+10. Southern
    # hemisphere DST is inverted vs the old US ET logic.
    month = datetime.now(timezone.utc).month
    au_offset = 11 if month in (10, 11, 12, 1, 2, 3) else 10
    au_zone = timezone(timedelta(hours=au_offset))
    label = "AEDT" if au_offset == 11 else "AEST"

    now_au = datetime.now(au_zone)
    target = now_au.replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
    if now_au >= target:
        target += timedelta(days=1)

    latest = _latest_scheduled_utc(yt)
    if latest is not None:
        latest_au = latest.astimezone(au_zone)
        if target <= latest_au:
            # Bumping past an existing slot must still land on PUBLISH_HOUR —
            # carrying latest_au's own clock time forward (e.g. a leftover
            # hour from before a cadence change) would silently revert any
            # PUBLISH_HOUR update (confirmed bug class, see ET-era comment
            # history for this same fix on the old US targeting).
            target = (latest_au + timedelta(days=1)).replace(
                hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0
            )

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


def _one_off_wib_target(candidates=((7, 0), (7, 30), (8, 0))):
    """One-off override for a single specific publish target, requested
    2026-06-22 for Topic #2 (publish today, 7 AM WIB, falling back to 7:30/8
    AM WIB if the visual-quality rework runs long). Not the recurring
    Sun/Wed cadence — see _next_publish_time() for that."""
    wib = timezone(timedelta(hours=7))
    now_wib = datetime.now(wib)
    target_wib = None
    for h, m in candidates:
        candidate = now_wib.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate > now_wib:
            target_wib = candidate
            break
    if target_wib is None:
        target_wib = now_wib.replace(hour=candidates[-1][0], minute=candidates[-1][1], second=0, microsecond=0)

    target_utc = target_wib.astimezone(timezone.utc)
    utc_str = target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    et_offset = -4 if 3 <= target_utc.month <= 11 else -5
    et_label = "EDT" if et_offset == -4 else "EST"
    target_et = target_utc.astimezone(timezone(timedelta(hours=et_offset)))
    return utc_str, f"{target_wib.strftime('%H:%M')} WIB = {target_et.strftime('%H:%M')} {et_label}"


def delete_video(video_id):
    """Deletes a published YouTube video outright. The Data API has no
    endpoint to replace an uploaded video's file (only metadata via
    videos.update) — replacing one means delete + re-upload as a new video
    object, which loses the old video's ID/comments/view history. Only call
    this with explicit user confirmation; see _replace_topic3.py."""
    yt = _get_service()
    yt.videos().delete(id=video_id).execute()
    print(f"    Deleted: youtube.com/watch?v={video_id}")


def upload_video(video_path, thumbnail_path, metadata, category=None, one_off_wib_target=False):
    yt = _get_service()
    publish_at_utc, publish_at_label = _one_off_wib_target() if one_off_wib_target else _next_publish_time(yt)

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
        chunksize=1 * 1024 * 1024,  # 1MB — small chunks + retry for unstable connections
    )

    request = yt.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    import time as _time
    response = None
    while response is None:
        for attempt in range(5):
            try:
                status, response = request.next_chunk()
                break
            except Exception as chunk_err:
                if attempt == 4:
                    raise
                wait = 2 ** attempt
                print(f"    Upload chunk error ({chunk_err}), retry in {wait}s...")
                _time.sleep(wait)
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

    if category:
        add_video_to_playlist(yt, category, video_id)

    return video_id, publish_at_utc


def post_comment(video_id, text):
    """Posts a top-level comment as the channel itself. Added 2026-06-24 to
    seed low-stakes engagement on sensitive topics that otherwise get zero
    comments despite high retention (per Gemini performance review).

    NOTE: the YouTube Data API v3 has no endpoint to pin a comment — pinning
    is only exposed in YouTube Studio's UI. This posts the comment; pinning
    it on top still needs one manual tap in Studio. Best-effort/non-fatal —
    a comment failure must never affect the main upload, same principle as
    the thumbnail-set step above."""
    try:
        yt = _get_service()
        yt.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": text}},
                }
            },
        ).execute()
        print(f"    Comment posted (pin it manually in Studio): {text!r}")
    except Exception as e:
        print(f"    Warning: comment post failed (video still uploaded fine): {e}")


def upload_short(video_path, metadata, parent_video_id, publish_at_utc):
    """Uploads the companion YouTube Short cut by agents/shorts_agent.py.
    Scheduled at the SAME publishAt as the main video (parent_video_id) —
    the Short's only job is to funnel Shorts-feed traffic to the full video,
    so it should land the moment that video goes live, not on its own
    separate slot. Title/description differ from the main upload: short,
    curiosity-driven, with an explicit link to the parent video."""
    yt = _get_service()
    title = metadata["title"]
    if "#shorts" not in title.lower():
        title = f"{title[:90]} #Shorts"

    description = (
        f"{metadata.get('hook_line', metadata['title'])}\n\n"
        f"Watch the full breakdown: https://youtu.be/{parent_video_id}\n\n"
        "#Shorts #Psychology #SelfAwareness"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": metadata.get("tags", [])[:15],
            "categoryId": "22",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_utc,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=50 * 1024 * 1024)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"    Short upload: {int(status.progress()*100)}%", end="\r")

    short_id = response["id"]
    print(f"    Short uploaded: youtube.com/shorts/{short_id} (links to {parent_video_id})")

    # Filed into its own "Apophenia Shorts" playlist (not the parent video's
    # category playlist) — see agents/playlist_agent.py. Non-fatal: a missing
    # playlist placement must never undo the upload itself.
    add_video_to_playlist(yt, "shorts", short_id)

    return short_id


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
