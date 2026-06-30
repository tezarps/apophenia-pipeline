"""Supabase-backed replacement for topic_agent.py's CSV queue + local-disk
storage for audio/script/thumbnail. Raw video is intentionally NOT persisted
here — it's rendered, uploaded to YouTube, then discarded (a single ~789MB
episode would burn most of Supabase Storage's free 1GB tier on its own).

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (service_role key, not
anon — this runs server-side in CI/local scripts, not in a browser, and needs
write access). Falls back to raising a clear error if those aren't set yet,
rather than silently doing nothing.
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

AUDIO_BUCKET = "apophenia-audio"
SCRIPTS_BUCKET = "apophenia-scripts"
THUMBNAILS_BUCKET = "apophenia-thumbnails"
IMAGES_BUCKET = "apophenia-images"
SHORTS_BUCKET = "apophenia-shorts"

_client = None


def _require_client() -> Client:
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env — "
            "complete Supabase project setup first (see supabase_setup/schema.sql)."
        )
    _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ---- Topic queue (mirrors agents/topic_agent.py's interface) ----

def get_next_topic():
    db = _require_client()
    # Retry failed topics before advancing to new pending ones — order by id
    # ensures the lowest-numbered failed/pending topic is always attempted first.
    res = db.table("topics").select("*").in_("status", ["pending", "failed"]).order("id").limit(1).execute()
    return res.data[0] if res.data else None


def mark_topic_done(topic_id, video_id=""):
    db = _require_client()
    db.table("topics").update({"status": "published", "video_id": str(video_id)}).eq("id", topic_id).execute()


def mark_topic_failed(topic_id, reason=""):
    db = _require_client()
    db.table("topics").update({"status": "failed", "notes": str(reason)[:200]}).eq("id", topic_id).execute()


def get_topic(topic_id):
    db = _require_client()
    res = db.table("topics").select("*").eq("id", topic_id).limit(1).execute()
    return res.data[0] if res.data else None


def add_topic(category, topic, angle):
    db = _require_client()
    res = db.table("topics").insert({
        "category": category.lower(), "topic": topic, "angle": angle, "status": "pending",
    }).execute()
    return res.data[0] if res.data else None


def reset_topic(topic_id):
    db = _require_client()
    db.table("topics").update({"status": "pending", "notes": ""}).eq("id", topic_id).execute()


def delete_topic(topic_id):
    db = _require_client()
    db.table("topics").delete().eq("id", topic_id).execute()


def list_topics(status=None, limit=200):
    """Replaces the dead CSV-backed topic list the webapp dashboard used
    before Apophenia moved its queue into Supabase (see supabase_io.py
    module docstring) — confirmed 2026-06-23 the webapp was still pointed
    at a leftover Narava-mythology CSV file that this pipeline no longer
    reads or writes."""
    db = _require_client()
    q = db.table("topics").select("*").order("id")
    if status:
        q = q.eq("status", status)
    res = q.limit(limit).execute()
    return res.data or []


# ---- Pipeline run log (for the dashboard) ----

def run_start(topic_id, angle):
    db = _require_client()
    res = db.table("pipeline_runs").insert({
        "topic_id": topic_id, "angle": angle, "status": "running",
    }).execute()
    return res.data[0]["id"] if res.data else None


def run_update_agent(run_id, current_agent):
    if run_id is None:
        return
    db = _require_client()
    db.table("pipeline_runs").update({"current_agent": current_agent}).eq("id", run_id).execute()


def run_done(run_id):
    if run_id is None:
        return
    db = _require_client()
    db.table("pipeline_runs").update({"status": "done", "finished_at": "now()"}).eq("id", run_id).execute()


def run_failed(run_id, error):
    if run_id is None:
        return
    db = _require_client()
    db.table("pipeline_runs").update({
        "status": "failed", "error": str(error)[:500], "finished_at": "now()",
    }).eq("id", run_id).execute()


def sweep_stale_runs(stale_minutes=20):
    """A killed/crashed process (kill -9, power loss) never reaches the
    except block that calls run_failed(), leaving its pipeline_runs row stuck
    at status='running' forever — the dashboard's realtime subscription has
    nothing new to show, so it just displays a permanently "running" run.
    Confirmed 2026-06-22 (Topic #4's run after it was force-killed). Called at
    the top of every scheduler.py run() so the dashboard self-heals on the
    very next run, even after a SIGKILL that no signal handler could catch."""
    db = _require_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = db.table("pipeline_runs").select("id").eq("status", "running").lt("started_at", cutoff).execute()
    for row in stale.data or []:
        db.table("pipeline_runs").update({
            "status": "failed",
            "error": "Stale — process stopped responding (killed or crashed without clean shutdown)",
            "finished_at": "now()",
        }).eq("id", row["id"]).execute()


# ---- Storage: audio / script / thumbnail (NOT raw video) ----

def _upload(bucket, remote_path, local_path):
    db = _require_client()
    data = Path(local_path).read_bytes()
    db.storage.from_(bucket).upload(remote_path, data, {"upsert": "true"})


def _download(bucket, remote_path, local_path):
    db = _require_client()
    data = db.storage.from_(bucket).download(remote_path)
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    Path(local_path).write_bytes(data)
    return local_path


# Free tier hard-caps individual file uploads at 50MB (confirmed live on the
# Narava org, 2026-06-20 — not just the 1GB total quota, a separate per-file
# limit that Pro removes for $25/mo). A full ~78min episode is ~113MB, so audio
# is split into <50MB chunks on upload and rejoined on download. MP3 is a
# frame-based format with no global header, so naive byte-level concatenation
# of sequential chunks reconstructs a valid, playable file.
AUDIO_CHUNK_BYTES = 40 * 1024 * 1024  # 40MB — comfortable margin under the 50MB cap


def upload_audio(topic_id, local_path):
    db = _require_client()
    data = Path(local_path).read_bytes()
    parts = [data[i:i + AUDIO_CHUNK_BYTES] for i in range(0, len(data), AUDIO_CHUNK_BYTES)] or [b""]
    for i, part in enumerate(parts):
        db.storage.from_(AUDIO_BUCKET).upload(f"{topic_id}_part{i:03d}.mp3", part, {"upsert": "true"})
    return len(parts)


def download_audio(topic_id, local_path):
    db = _require_client()
    chunks = []
    i = 0
    while True:
        try:
            chunks.append(db.storage.from_(AUDIO_BUCKET).download(f"{topic_id}_part{i:03d}.mp3"))
        except Exception:
            break
        i += 1
    if not chunks:
        raise FileNotFoundError(f"No audio parts found in Storage for topic {topic_id}")
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    Path(local_path).write_bytes(b"".join(chunks))
    return local_path


def upload_script(topic_id, local_path):
    _upload(SCRIPTS_BUCKET, f"{topic_id}.txt", local_path)


def download_script(topic_id, local_path):
    return _download(SCRIPTS_BUCKET, f"{topic_id}.txt", local_path)


# ---- Shorts: rendered .mp4 + a small JSON sidecar (title/links), so the
# webapp dashboard can list and serve them for manual TikTok download
# without needing a new Supabase table. A Short is well under the 50MB
# per-file cap (~16-20MB at 50-58s) so unlike audio it's uploaded whole,
# no chunking needed. ----

def upload_short(topic_id, local_video_path, meta: dict, key_suffix=""):
    """key_suffix added 2026-06-24 for the turning-point Short variant
    (e.g. "_turn") so it doesn't collide with the original hook Short's
    {topic_id}.mp4/.json keys — dashboard/api/shorts.js groups by everything
    before the last dot, so the suffix surfaces as its own row there."""
    import json
    db = _require_client()
    key = f"{topic_id}{key_suffix}"
    _upload(SHORTS_BUCKET, f"{key}.mp4", local_video_path)
    db.storage.from_(SHORTS_BUCKET).upload(
        f"{key}.json",
        json.dumps(meta, ensure_ascii=False).encode("utf-8"),
        {"upsert": "true", "content-type": "application/json"},
    )


def list_shorts(signed_url_expires_in=3600):
    """Returns [{topic_id, title, short_youtube_id, parent_video_id,
    publish_at_utc, download_url}, ...] newest first, for the webapp's
    download page. download_url is a time-limited signed URL (bucket is
    private — service-role key only) rather than a permanent public link,
    since these are unlisted-until-published assets."""
    import json
    db = _require_client()
    entries = db.storage.from_(SHORTS_BUCKET).list()
    by_topic = {}
    for e in entries or []:
        name = e["name"]
        if "." not in name:
            continue
        topic_id, ext = name.rsplit(".", 1)
        by_topic.setdefault(topic_id, {})[ext] = name

    out = []
    for topic_id, files in by_topic.items():
        if "mp4" not in files:
            continue
        meta = {}
        if "json" in files:
            try:
                meta = json.loads(db.storage.from_(SHORTS_BUCKET).download(files["json"]).decode("utf-8"))
            except Exception:
                pass
        signed = db.storage.from_(SHORTS_BUCKET).create_signed_url(files["mp4"], signed_url_expires_in)
        download_url = signed.get("signedURL") or signed.get("signedUrl")
        out.append({"topic_id": topic_id, "download_url": download_url, **meta})

    out.sort(key=lambda r: int(r["topic_id"]), reverse=True)
    return out


def upload_timings(topic_id, local_path):
    _upload(AUDIO_BUCKET, f"{topic_id}_timings.json", local_path)


def download_timings(topic_id, local_path):
    return _download(AUDIO_BUCKET, f"{topic_id}_timings.json", local_path)


def upload_thumbnail(category, slug, variant, local_path):
    """Keyed by category/slug (matches get_manual_thumbnails() in assembly_agent.py,
    which looks up thumbnails/{category}/{slug}/A.ext — NOT by topic_id, so this
    stays consistent with the local-disk convention everything else already uses."""
    ext = Path(local_path).suffix
    remote_path = f"{category.lower()}/{slug.lower()}/{variant}{ext}"
    _upload(THUMBNAILS_BUCKET, remote_path, local_path)


def download_thumbnails(category, slug, local_dir):
    """Download whatever A/B thumbnail variants exist for category/slug into
    local_dir, recreating the structure get_manual_thumbnails() expects."""
    db = _require_client()
    remote_prefix = f"{category.lower()}/{slug.lower()}"
    entries = db.storage.from_(THUMBNAILS_BUCKET).list(remote_prefix)
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        data = db.storage.from_(THUMBNAILS_BUCKET).download(f"{remote_prefix}/{entry['name']}")
        (local_dir / entry["name"]).write_bytes(data)
    return local_dir


def download_thumbnail_variant_bytes(category, slug, variant):
    """Fetch a single A or B thumbnail's raw bytes — used by ab_test_check.py,
    which needs to hand the image straight to YouTube's thumbnails().set()
    without caring about the file extension on disk."""
    db = _require_client()
    remote_prefix = f"{category.lower()}/{slug.lower()}"
    entries = db.storage.from_(THUMBNAILS_BUCKET).list(remote_prefix)
    match = next((e for e in entries if e["name"].startswith(variant)), None)
    if not match:
        raise FileNotFoundError(f"No thumbnail variant {variant} for {category}/{slug}")
    return db.storage.from_(THUMBNAILS_BUCKET).download(f"{remote_prefix}/{match['name']}")


# ---- Images: synced into the SAME local folder structure assembly_agent.py
# already expects (images/{category}/{slug}/NN.ext) — this keeps assembly_agent.py
# untouched; only the CI job needs to pull images down before running create_video(). ----

def upload_topic_images(category, slug, local_dir):
    """Upload every image in local_dir (e.g. images/comparative/great_flood/) under
    a 'category/slug/filename' path, mirroring the local folder layout."""
    db = _require_client()
    local_dir = Path(local_dir)
    for img_path in sorted(local_dir.glob("*")):
        if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        remote_path = f"{category.lower()}/{slug.lower()}/{img_path.name}"
        data = img_path.read_bytes()
        db.storage.from_(IMAGES_BUCKET).upload(remote_path, data, {"upsert": "true"})


def download_topic_images(category, slug, local_dir):
    """Download all images for category/slug into local_dir, recreating the same
    folder structure _get_images() in assembly_agent.py already expects."""
    db = _require_client()
    remote_prefix = f"{category.lower()}/{slug.lower()}"
    entries = db.storage.from_(IMAGES_BUCKET).list(remote_prefix)
    if not entries:
        raise FileNotFoundError(
            f"No images in Supabase Storage for {category}/{slug} — generate them via "
            f"Google Flow and run supabase_setup/sync_images_up.py first."
        )
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        remote_path = f"{remote_prefix}/{entry['name']}"
        data = db.storage.from_(IMAGES_BUCKET).download(remote_path)
        (local_dir / entry["name"]).write_bytes(data)
    return local_dir


# ---- Thumbnail A/B self-test (sequential rotation — see ab_test_check.py) ----

def create_thumbnail_test(topic_id, video_id):
    db = _require_client()
    db.table("thumbnail_tests").insert({"topic_id": topic_id, "video_id": video_id}).execute()


def get_running_thumbnail_tests():
    """Tests still in phase A (waiting to flip to B) or phase B (waiting to
    resolve) — anything not yet resolved."""
    db = _require_client()
    res = db.table("thumbnail_tests").select("*").eq("resolved", False).execute()
    return res.data


def flip_to_variant_b(test_id, ctr_a):
    db = _require_client()
    db.table("thumbnail_tests").update({
        "active_variant": "B", "switched_at": "now()", "ctr_a": ctr_a,
    }).eq("id", test_id).execute()


def resolve_thumbnail_test(test_id, ctr_b, winner):
    db = _require_client()
    db.table("thumbnail_tests").update({
        "resolved": True, "ctr_b": ctr_b, "winner": winner,
    }).eq("id", test_id).execute()
